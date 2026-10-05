"""Dependency graph analyzer for extracting direct and transitive dependencies with ancestor chains."""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from amstralift.core.workspace import safe_rglob
from amstralift.security.models import DiscoveredDependency


class DependencyGraphAnalyzer:
    """Extracts normalized dependency graphs across Angular, React, .NET, and Python."""

    @classmethod
    def analyze(cls, repo_path: Path, ecosystem: str) -> list[DiscoveredDependency]:
        """Discover all direct and transitive dependencies with their introduction chains."""
        eco = ecosystem.lower().strip()
        if eco in ("angular", "react", "npm"):
            return cls.analyze_npm(repo_path, eco)
        elif eco in ("dotnet", "nuget"):
            return cls.analyze_dotnet(repo_path)
        elif eco in ("python", "pypi"):
            return cls.analyze_python(repo_path)
        return []

    @classmethod
    def analyze_npm(cls, repo_path: Path, ecosystem: str = "npm") -> list[DiscoveredDependency]:
        """Extract npm dependencies from package.json and package-lock.json."""
        pkg_json_path = repo_path / "package.json"
        if not pkg_json_path.exists():
            return []

        direct_deps: dict[str, str] = {}
        try:
            pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies"):
                for name, ver in pkg_data.get(section, {}).items():
                    direct_deps[name] = str(ver)
        except Exception:
            pass

        results: dict[tuple[str, str], DiscoveredDependency] = {}

        # 1. Register direct dependencies
        for name, ver in direct_deps.items():
            results[(name, ver)] = DiscoveredDependency(
                package_name=name,
                version=ver,
                is_direct=True,
                introduced_by=[],
                manifest_file="package.json",
                ecosystem=ecosystem,
            )

        # 2. Parse package-lock.json for transitive dependencies and chains
        lock_path = repo_path / "package-lock.json"
        if lock_path.exists():
            try:
                lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
                # v2/v3 lockfile format
                packages = lock_data.get("packages", {})
                if packages:
                    # Map of (pkg_name, ver) -> list of path_keys
                    pkg_instances: dict[tuple[str, str], list[str]] = {}
                    dependents: dict[str, list[str]] = {}

                    for path_key, meta in packages.items():
                        if not path_key:  # root
                            continue
                        # e.g. "node_modules/express" or "node_modules/foo/node_modules/bar"
                        parts = path_key.split("node_modules/")
                        pkg_name = parts[-1]
                        ver = meta.get("version", "")
                        if pkg_name and ver:
                            pkg_instances.setdefault((pkg_name, ver), []).append(path_key)
                            # Track dependencies declared by this package
                            for dep_name in meta.get("dependencies", {}).keys():
                                dependents.setdefault(dep_name, []).append(pkg_name)

                    for (pkg_name, ver), path_keys in pkg_instances.items():
                        is_top_level = any(pk == f"node_modules/{pkg_name}" for pk in path_keys)
                        is_direct = (pkg_name in direct_deps) and is_top_level
                        effective_ver = direct_deps[pkg_name] if is_direct else ver

                        intro_by = []
                        if not is_direct:
                            # 1. Trace immediate enclosing parents from nested path_keys (e.g. node_modules/req/node_modules/uuid -> req)
                            enclosing_parents = []
                            for pk in path_keys:
                                parts = [p.rstrip("/") for p in pk.split("node_modules/") if p.strip("/")]
                                if len(parts) >= 2:
                                    enclosing_parents.append(parts[-2])

                            ancestors: list[str] = []
                            for ep in enclosing_parents:
                                ancestors.append(ep)
                                # trace ep up to direct deps
                                curr = ep
                                visited = {curr}
                                while curr not in direct_deps:
                                    up = [p for p in dependents.get(curr, []) if p not in visited]
                                    if not up:
                                        break
                                    direct_up = next((p for p in up if p in direct_deps), None)
                                    curr = direct_up if direct_up else up[0]
                                    visited.add(curr)
                                    if curr in direct_deps:
                                        ancestors.append(curr)
                                        break

                            raw_parents = dependents.get(pkg_name, [])
                            direct_parents = [p for p in raw_parents if p in direct_deps]
                            fallback = direct_parents if direct_parents else raw_parents[:2]

                            intro_by = list(dict.fromkeys(ancestors + fallback))

                        res_key = (pkg_name, effective_ver)
                        if res_key not in results or not results[res_key].is_direct:
                            results[res_key] = DiscoveredDependency(
                                package_name=pkg_name,
                                version=effective_ver,
                                is_direct=is_direct,
                                introduced_by=intro_by,
                                manifest_file="package-lock.json" if not is_direct else "package.json",
                                ecosystem=ecosystem,
                            )

                # v1 lockfile format fallback
                elif "dependencies" in lock_data:
                    def walk_v1(deps_dict: dict[str, Any], chain: list[str]) -> None:
                        for name, meta in deps_dict.items():
                            ver = meta.get("version", "")
                            is_direct = name in direct_deps and not chain
                            effective_ver = direct_deps[name] if is_direct else ver
                            res_key = (name, effective_ver)
                            if res_key not in results or not results[res_key].is_direct:
                                results[res_key] = DiscoveredDependency(
                                    package_name=name,
                                    version=effective_ver,
                                    is_direct=is_direct,
                                    introduced_by=chain.copy(),
                                    manifest_file="package-lock.json" if not is_direct else "package.json",
                                    ecosystem=ecosystem,
                                )
                            if "dependencies" in meta:
                                walk_v1(meta["dependencies"], chain + [name])

                    walk_v1(lock_data.get("dependencies", {}), [])
            except Exception:
                pass

        return list(results.values())

    @classmethod
    def analyze_dotnet(cls, repo_path: Path) -> list[DiscoveredDependency]:
        """Extract .NET dependencies from .csproj, Directory.Packages.props, and packages.lock.json."""
        results: dict[str, DiscoveredDependency] = {}
        direct_pkgs: set[str] = set()

        # 1. Inspect Directory.Packages.props (Central Package Management)
        for props_path in safe_rglob(repo_path, "Directory.Packages.props"):
            try:
                tree = ET.parse(props_path)
                root = tree.getroot()
                for pv in root.findall(".//PackageVersion"):
                    name = pv.get("Include") or pv.get("Update")
                    ver = pv.get("Version")
                    if name and ver:
                        direct_pkgs.add(name)
                        results[name] = DiscoveredDependency(
                            package_name=name,
                            version=ver,
                            is_direct=True,
                            introduced_by=[],
                            manifest_file=str(props_path.relative_to(repo_path)),
                            ecosystem="dotnet",
                        )
            except Exception:
                pass

        # 2. Inspect all .csproj files
        for csproj_path in safe_rglob(repo_path, "*.csproj"):
            try:
                tree = ET.parse(csproj_path)
                root = tree.getroot()
                tf = root.findtext(".//TargetFramework") or "net"
                for pr in root.findall(".//PackageReference"):
                    name = pr.get("Include") or pr.get("Update")
                    ver = pr.get("Version") or ""
                    if name:
                        direct_pkgs.add(name)
                        # In CPM, version comes from Directory.Packages.props if not specified in csproj
                        if not ver and name in results and results[name].version:
                            ver = results[name].version
                        results[name] = DiscoveredDependency(
                            package_name=name,
                            version=ver,
                            is_direct=True,
                            introduced_by=[],
                            manifest_file=str(csproj_path.relative_to(repo_path)),
                            ecosystem="dotnet",
                            target_framework=tf,
                        )
            except Exception:
                pass

        # 3. Inspect packages.lock.json for transitive dependencies
        for lock_path in safe_rglob(repo_path, "packages.lock.json"):
            try:
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                for tf_name, tf_data in data.get("dependencies", {}).items():
                    for pkg_name, pkg_meta in tf_data.items():
                        ver = pkg_meta.get("resolved", "")
                        dep_type = pkg_meta.get("type", "").lower()
                        is_direct = dep_type == "direct" or pkg_name in direct_pkgs

                        if pkg_name not in results or not results[pkg_name].is_direct:
                            intro_by = []
                            if not is_direct:
                                # Find parents declaring this dependency
                                for parent_name, parent_meta in tf_data.items():
                                    if pkg_name in parent_meta.get("dependencies", {}):
                                        intro_by.append(parent_name)

                            results[pkg_name] = DiscoveredDependency(
                                package_name=pkg_name,
                                version=ver,
                                is_direct=is_direct,
                                introduced_by=intro_by[:2],
                                manifest_file=str(lock_path.relative_to(repo_path)),
                                ecosystem="dotnet",
                                target_framework=tf_name,
                            )
            except Exception:
                pass

        return list(results.values())

    @classmethod
    def analyze_python(cls, repo_path: Path) -> list[DiscoveredDependency]:
        """Extract Python dependencies from pyproject.toml, requirements.txt, and poetry.lock."""
        results: dict[str, DiscoveredDependency] = {}
        direct_pkgs: set[str] = set()

        # 1. pyproject.toml
        pyproject_path = repo_path / "pyproject.toml"
        if pyproject_path.exists():
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore

            try:
                data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
                # PEP 621 dependencies
                deps = data.get("project", {}).get("dependencies", [])
                for d in deps:
                    match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", d.strip())
                    if match:
                        name, ver = match.group(1), match.group(2).strip()
                        direct_pkgs.add(name)
                        results[name] = DiscoveredDependency(
                            package_name=name,
                            version=ver or "*",
                            is_direct=True,
                            introduced_by=[],
                            manifest_file="pyproject.toml",
                            ecosystem="python",
                        )

                # Poetry dependencies
                poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
                for name, ver in poetry_deps.items():
                    if name.lower() != "python":
                        direct_pkgs.add(name)
                        results[name] = DiscoveredDependency(
                            package_name=name,
                            version=str(ver),
                            is_direct=True,
                            introduced_by=[],
                            manifest_file="pyproject.toml",
                            ecosystem="python",
                        )
            except Exception:
                pass

        # 2. requirements.txt
        req_path = repo_path / "requirements.txt"
        if req_path.exists():
            try:
                lines = req_path.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    line = line.strip().split("#")[0]
                    if not line or line.startswith("-"):
                        continue
                    match = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*([~>=<].*)?$", line)
                    if match:
                        name = match.group(1)
                        ver = match.group(2) or "*"
                        direct_pkgs.add(name)
                        if name not in results:
                            results[name] = DiscoveredDependency(
                                package_name=name,
                                version=ver.strip(),
                                is_direct=True,
                                introduced_by=[],
                                manifest_file="requirements.txt",
                                ecosystem="python",
                            )
            except Exception:
                pass

        # 3. poetry.lock for transitive dependencies
        poetry_lock = repo_path / "poetry.lock"
        if poetry_lock.exists():
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore

            try:
                lock_data = tomllib.loads(poetry_lock.read_text(encoding="utf-8"))
                for pkg in lock_data.get("package", []):
                    name = pkg.get("name", "")
                    ver = pkg.get("version", "")
                    if name and ver:
                        is_direct = name in direct_pkgs
                        if name not in results or not results[name].is_direct:
                            results[name] = DiscoveredDependency(
                                package_name=name,
                                version=ver,
                                is_direct=is_direct,
                                introduced_by=[],
                                manifest_file="poetry.lock",
                                ecosystem="python",
                            )
            except Exception:
                pass

        return list(results.values())
