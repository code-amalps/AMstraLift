"""Multi-repository discovery and dependency analysis engine (P21).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import yaml

from amstralift.core.monorepo import MonorepoScanner
from amstralift.multirepo.models import (
    FleetManifest,
    FleetTopology,
    InterRepoDependency,
    PackageExport,
    PackageImport,
    RepositoryNode,
)


class FleetScanner:
    """Scanner that locates repositories, projects, and inter-repo dependencies."""

    EXCLUDED_DIRS = {
        ".git",
        ".github",
        ".gitlab",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "bin",
        "obj",
        "dist",
        "target",
        "build",
        "out",
        ".angular",
        ".nx",
        ".turbo",
        ".pytest_cache",
        "__pycache__",
        ".vs",
        ".idea",
        ".vscode",
        "coverage",
    }

    @classmethod
    def scan_directory(cls, root_dir: Path, max_depth: int = 3) -> FleetTopology:
        """Scan a directory for repositories, extracting cross-repo dependencies."""
        root_dir = root_dir.resolve()

        # Check for fleet manifest first
        manifest_file = root_dir / "fleet.yaml"
        if not manifest_file.is_file():
            manifest_file = root_dir / "amstralift-fleet.yaml"
        if manifest_file.is_file():
            return cls.load_manifest(manifest_file)

        repo_dirs = cls._locate_repo_dirs(root_dir, max_depth=max_depth)
        repositories: dict[str, RepositoryNode] = {}

        for r_dir in repo_dirs:
            node = cls._inspect_repository(r_dir, root_dir)
            if node:
                repositories[node.repo_id] = node

        # Resolve cross-repository consumer-provider dependencies
        dependencies = cls._resolve_cross_repo_dependencies(repositories)

        return FleetTopology(
            root_path=str(root_dir),
            repositories=repositories,
            dependencies=dependencies,
        )

    @classmethod
    def load_manifest(cls, manifest_path: Path) -> FleetTopology:
        """Load fleet topology from a declarative fleet.yaml manifest."""
        manifest_path = manifest_path.resolve()
        base_dir = manifest_path.parent

        with open(manifest_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        manifest = FleetManifest(**data)
        repositories: dict[str, RepositoryNode] = {}
        explicit_upstreams: list[tuple[str, str]] = []

        for r_spec in manifest.repositories:
            r_path = (base_dir / r_spec.path).resolve()
            if not r_path.is_dir():
                continue

            node = cls._inspect_repository(r_path, base_dir, custom_id=r_spec.id)
            if node:
                if r_spec.tags:
                    node.tags = sorted(list(set(node.tags + r_spec.tags)))
                repositories[node.repo_id] = node

            for up in r_spec.upstream:
                explicit_upstreams.append((r_spec.id, up))

        # Auto-resolve cross-repo dependencies
        dependencies = cls._resolve_cross_repo_dependencies(repositories)

        # Append explicit upstream dependencies from manifest
        existing_pairs = {(d.consumer_repo_id, d.provider_repo_id) for d in dependencies}
        for consumer_id, provider_id in explicit_upstreams:
            if consumer_id in repositories and provider_id in repositories:
                if (consumer_id, provider_id) not in existing_pairs:
                    dependencies.append(
                        InterRepoDependency(
                            consumer_repo_id=consumer_id,
                            provider_repo_id=provider_id,
                            package_name="manifest-link",
                            version_required="*",
                            ecosystem="polyglot",
                            dependency_type="explicit",
                        )
                    )
                    existing_pairs.add((consumer_id, provider_id))

        return FleetTopology(
            root_path=str(base_dir),
            repositories=repositories,
            dependencies=dependencies,
        )

    @classmethod
    def _locate_repo_dirs(cls, root_dir: Path, max_depth: int) -> list[Path]:
        """Locate distinct repository directories under root_dir."""
        repo_dirs: list[Path] = []

        # Check if root_dir itself is a git repo or project container
        if (root_dir / ".git").is_dir():
            # Check if there are sub-repositories inside it
            sub_repos = []
            for entry in root_dir.iterdir():
                if entry.is_dir() and entry.name not in cls.EXCLUDED_DIRS:
                    if (entry / ".git").is_dir() or cls._has_manifest(entry):
                        sub_repos.append(entry)

            if sub_repos:
                return sub_repos
            return [root_dir]

        # Otherwise scan subdirectories up to max_depth
        for entry in root_dir.iterdir():
            if entry.is_dir() and entry.name not in cls.EXCLUDED_DIRS and not entry.name.startswith("."):
                if (entry / ".git").is_dir() or cls._has_manifest(entry):
                    repo_dirs.append(entry)
                elif max_depth > 1:
                    # Look one level deeper (e.g. org/team/repo)
                    try:
                        for sub in entry.iterdir():
                            if sub.is_dir() and sub.name not in cls.EXCLUDED_DIRS and not sub.name.startswith("."):
                                if (sub / ".git").is_dir() or cls._has_manifest(sub):
                                    repo_dirs.append(sub)
                    except (PermissionError, OSError):
                        pass

        return repo_dirs

    @classmethod
    def _has_manifest(cls, directory: Path) -> bool:
        """Check if a directory contains any recognizable project manifest."""
        return (
            (directory / "package.json").is_file()
            or bool(list(directory.glob("*.csproj")))
            or bool(list(directory.glob("*.fsproj")))
            or (directory / "pyproject.toml").is_file()
            or (directory / "requirements.txt").is_file()
            or (directory / "setup.py").is_file()
        )

    @classmethod
    def _inspect_repository(cls, repo_dir: Path, fleet_root: Path, custom_id: Optional[str] = None) -> Optional[RepositoryNode]:
        """Inspect a single repository directory and extract metadata, exports, and imports."""
        repo_id = custom_id or repo_dir.name
        rel_path = "." if repo_dir == fleet_root else str(repo_dir.relative_to(fleet_root)).replace("\\", "/")

        # Detect git branch if available
        branch = cls._detect_git_branch(repo_dir)

        # Discover projects within repository using MonorepoScanner
        topo = MonorepoScanner.discover(repo_dir, max_depth=4)

        exports: list[PackageExport] = []
        imports: list[PackageImport] = []

        # 1. Inspect Node / Angular / React manifests
        for pkg_json_path in repo_dir.glob("**/package.json"):
            if any(part in cls.EXCLUDED_DIRS for part in pkg_json_path.parts):
                continue
            cls._extract_npm_data(pkg_json_path, repo_dir, exports, imports)

        # 2. Inspect .NET manifests
        for csproj_path in list(repo_dir.glob("**/*.csproj")) + list(repo_dir.glob("**/*.fsproj")):
            if any(part in cls.EXCLUDED_DIRS for part in csproj_path.parts):
                continue
            cls._extract_dotnet_data(csproj_path, repo_dir, exports, imports)

        # 3. Inspect Python manifests
        for pyproject_path in repo_dir.glob("**/pyproject.toml"):
            if any(part in cls.EXCLUDED_DIRS for part in pyproject_path.parts):
                continue
            cls._extract_python_pyproject_data(pyproject_path, repo_dir, exports, imports)

        for req_path in repo_dir.glob("**/requirements*.txt"):
            if any(part in cls.EXCLUDED_DIRS for part in req_path.parts):
                continue
            cls._extract_python_requirements_data(req_path, repo_dir, imports)

        # Determine primary ecosystem and framework version
        ecosystems = topo.ecosystems if topo.ecosystems else ["unknown"]
        primary_eco = ecosystems[0] if ecosystems else "unknown"
        fw_version = None
        for p in topo.projects:
            if p.framework_version:
                fw_version = p.framework_version
                break

        # Deduplicate exports and imports
        unique_exports = {f"{e.package_name}:{e.ecosystem}": e for e in exports}
        unique_imports = {f"{i.package_name}:{i.is_dev}:{i.ecosystem}": i for i in imports}

        return RepositoryNode(
            repo_id=repo_id,
            repo_path=str(repo_dir),
            rel_path=rel_path,
            git_branch=branch,
            ecosystems=ecosystems,
            primary_ecosystem=primary_eco,
            framework_version=fw_version,
            exports=list(unique_exports.values()),
            imports=list(unique_imports.values()),
            tags=[],
        )

    @classmethod
    def _detect_git_branch(cls, repo_dir: Path) -> Optional[str]:
        """Read the current git branch from .git/HEAD if present."""
        head_file = repo_dir / ".git" / "HEAD"
        if head_file.is_file():
            try:
                content = head_file.read_text(encoding="utf-8").strip()
                if content.startswith("ref: refs/heads/"):
                    return content[len("ref: refs/heads/") :]
                return content[:8]
            except Exception:
                pass
        return None

    @classmethod
    def _extract_npm_data(
        cls,
        pkg_json_path: Path,
        repo_dir: Path,
        exports: list[PackageExport],
        imports: list[PackageImport],
    ) -> None:
        """Extract npm exports and imports from package.json."""
        try:
            rel = str(pkg_json_path.relative_to(repo_dir)).replace("\\", "/")
            data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            name = data.get("name")
            ver = data.get("version")

            # Determine ecosystem (angular, react, node)
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            eco = "angular" if "@angular/core" in all_deps else ("react" if "react" in all_deps else "node")

            if name:
                exports.append(
                    PackageExport(
                        package_name=str(name),
                        version=str(ver) if ver else None,
                        ecosystem=eco,
                        manifest_path=rel,
                    )
                )

            for pkg, v in data.get("dependencies", {}).items():
                imports.append(
                    PackageImport(
                        package_name=str(pkg),
                        version_spec=str(v),
                        ecosystem=eco,
                        is_dev=False,
                    )
                )

            for pkg, v in data.get("devDependencies", {}).items():
                imports.append(
                    PackageImport(
                        package_name=str(pkg),
                        version_spec=str(v),
                        ecosystem=eco,
                        is_dev=True,
                    )
                )
        except Exception:
            pass

    @classmethod
    def _extract_dotnet_data(
        cls,
        csproj_path: Path,
        repo_dir: Path,
        exports: list[PackageExport],
        imports: list[PackageImport],
    ) -> None:
        """Extract .NET assembly exports and PackageReferences from .csproj."""
        try:
            rel = str(csproj_path.relative_to(repo_dir)).replace("\\", "/")
            tree = ET.parse(csproj_path)
            root = tree.getroot()

            # PackageId or AssemblyName or csproj stem
            package_id = None
            for elem in root.iter("PackageId"):
                if elem.text:
                    package_id = elem.text.strip()
                    break
            if not package_id:
                for elem in root.iter("AssemblyName"):
                    if elem.text:
                        package_id = elem.text.strip()
                        break
            if not package_id:
                package_id = csproj_path.stem

            exports.append(
                PackageExport(
                    package_name=package_id,
                    version=None,
                    ecosystem="dotnet",
                    manifest_path=rel,
                )
            )

            # PackageReferences
            for pr in root.iter("PackageReference"):
                include = pr.get("Include") or pr.get("Update")
                version = pr.get("Version") or "*"
                if include:
                    imports.append(
                        PackageImport(
                            package_name=include.strip(),
                            version_spec=version.strip(),
                            ecosystem="dotnet",
                            is_dev=False,
                        )
                    )

            # ProjectReferences
            for pr in root.iter("ProjectReference"):
                include = pr.get("Include")
                if include:
                    ref_name = Path(include).stem
                    imports.append(
                        PackageImport(
                            package_name=ref_name,
                            version_spec="*",
                            ecosystem="dotnet",
                            is_dev=False,
                        )
                    )
        except Exception:
            pass

    @classmethod
    def _extract_python_pyproject_data(
        cls,
        pyproject_path: Path,
        repo_dir: Path,
        exports: list[PackageExport],
        imports: list[PackageImport],
    ) -> None:
        """Extract Python package name and dependencies from pyproject.toml."""
        try:
            rel = str(pyproject_path.relative_to(repo_dir)).replace("\\", "/")
            text = pyproject_path.read_text(encoding="utf-8")

            # Match project name
            name_match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            if name_match:
                pkg_name = name_match.group(1).strip()
                exports.append(
                    PackageExport(
                        package_name=pkg_name,
                        version=None,
                        ecosystem="python",
                        manifest_path=rel,
                    )
                )

            # Match dependencies
            deps_block_match = re.search(r'dependencies\s*=\s*\[(.*?)\]', text, re.DOTALL)
            if deps_block_match:
                block = deps_block_match.group(1)
                items = re.findall(r'["\']([^"\']+)["\']', block)
                for item in items:
                    parsed_name, parsed_ver = cls._parse_python_dep_spec(item)
                    if parsed_name:
                        imports.append(
                            PackageImport(
                                package_name=parsed_name,
                                version_spec=parsed_ver,
                                ecosystem="python",
                                is_dev=False,
                            )
                        )
        except Exception:
            pass

    @classmethod
    def _extract_python_requirements_data(
        cls,
        req_path: Path,
        repo_dir: Path,
        imports: list[PackageImport],
    ) -> None:
        """Extract dependencies from requirements.txt."""
        try:
            for line in req_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                parsed_name, parsed_ver = cls._parse_python_dep_spec(line)
                if parsed_name:
                    imports.append(
                        PackageImport(
                            package_name=parsed_name,
                            version_spec=parsed_ver,
                            ecosystem="python",
                            is_dev=False,
                        )
                    )
        except Exception:
            pass

    @classmethod
    def _parse_python_dep_spec(cls, dep_string: str) -> tuple[str, str]:
        """Parse Python dependency string e.g. 'requests>=2.25.0' into name and version."""
        match = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*([><=~!].*)?$", dep_string.strip())
        if match:
            name = match.group(1).strip()
            version = (match.group(2) or "*").strip()
            return name, version
        return dep_string.strip(), "*"

    @classmethod
    def _resolve_cross_repo_dependencies(
        cls, repositories: dict[str, RepositoryNode]
    ) -> list[InterRepoDependency]:
        """Cross-reference exports and imports across all repositories to identify inter-repo dependencies."""
        # Index exports: normalized package name -> (repo_id, PackageExport)
        export_index: dict[str, tuple[str, PackageExport]] = {}
        for repo_id, repo in repositories.items():
            for exp in repo.exports:
                norm_name = exp.package_name.lower().strip()
                export_index[norm_name] = (repo_id, exp)

        dependencies: list[InterRepoDependency] = []
        seen_pairs: set[tuple[str, str, str]] = set()

        for consumer_id, consumer_repo in repositories.items():
            for imp in consumer_repo.imports:
                norm_imp_name = imp.package_name.lower().strip()
                if norm_imp_name in export_index:
                    provider_id, exp = export_index[norm_imp_name]
                    if provider_id != consumer_id:
                        key = (consumer_id, provider_id, exp.package_name)
                        if key not in seen_pairs:
                            seen_pairs.add(key)
                            dep_type = "dev" if imp.is_dev else "runtime"
                            dependencies.append(
                                InterRepoDependency(
                                    consumer_repo_id=consumer_id,
                                    provider_repo_id=provider_id,
                                    package_name=exp.package_name,
                                    version_required=imp.version_spec,
                                    ecosystem=imp.ecosystem,
                                    dependency_type=dep_type,
                                )
                            )

        # Sort dependencies deterministically
        dependencies.sort(key=lambda d: (d.provider_repo_id, d.consumer_repo_id, d.package_name))
        return dependencies
