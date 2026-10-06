"""Monorepo and polyglot repository discovery engine.

Recursively discovers all projects across Angular, React, .NET, and Python
in monorepos, multi-project workspaces, and enterprise repositories.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from amstralift.core.models import DiscoveredProject, MonorepoTopology


class MonorepoScanner:
    """Scanner that locates and classifies all projects in a repository."""

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
        ".next",
        ".nuxt",
    }

    @classmethod
    def discover(cls, repo_path: Path, max_depth: int = 3) -> MonorepoTopology:
        """Scan repository up to max_depth and return detected projects across all ecosystems."""
        repo_path = repo_path.resolve()
        discovered: list[DiscoveredProject] = []

        cls._walk(repo_path, repo_path, discovered, current_depth=0, max_depth=max_depth)

        # De-duplicate projects by absolute directory path
        unique_projects: list[DiscoveredProject] = []
        seen_paths: set[str] = set()
        for p in discovered:
            if p.abs_path not in seen_paths:
                seen_paths.add(p.abs_path)
                unique_projects.append(p)

        ecosystems = sorted(list({p.ecosystem for p in unique_projects}))
        is_monorepo = len(unique_projects) > 1 or (
            len(unique_projects) == 1 and unique_projects[0].rel_path != "."
        )

        return MonorepoTopology(
            is_monorepo=is_monorepo,
            projects=unique_projects,
            ecosystems=ecosystems,
        )

    @classmethod
    def _walk(
        cls,
        root_path: Path,
        current_dir: Path,
        discovered: list[DiscoveredProject],
        current_depth: int,
        max_depth: int,
    ) -> None:
        """Walk subdirectories up to max_depth detecting ecosystem manifests."""
        rel_path = "." if current_dir == root_path else str(current_dir.relative_to(root_path)).replace("\\", "/")

        found_in_dir = False

        # 1. Check for Angular or React (package.json)
        pkg_json = current_dir / "package.json"
        if pkg_json.is_file():
            try:
                pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps = {
                    **pkg_data.get("dependencies", {}),
                    **pkg_data.get("devDependencies", {}),
                }

                if "@angular/core" in deps or (current_dir / "angular.json").is_file():
                    ver = deps.get("@angular/core", "").lstrip("^~>=<")
                    name = pkg_data.get("name") or (current_dir.name if rel_path != "." else "root")
                    discovered.append(
                        DiscoveredProject(
                            name=str(name),
                            rel_path=rel_path,
                            abs_path=str(current_dir),
                            ecosystem="angular",
                            framework_version=ver or None,
                            manifest_file="package.json",
                        )
                    )
                    found_in_dir = True
                elif "react" in deps or "react-dom" in deps:
                    ver = deps.get("react", "").lstrip("^~>=<")
                    name = pkg_data.get("name") or (current_dir.name if rel_path != "." else "root")
                    discovered.append(
                        DiscoveredProject(
                            name=str(name),
                            rel_path=rel_path,
                            abs_path=str(current_dir),
                            ecosystem="react",
                            framework_version=ver or None,
                            manifest_file="package.json",
                        )
                    )
                    found_in_dir = True
            except Exception:
                pass

        # 2. Check for .NET (*.csproj, *.fsproj, *.sln)
        csproj_files = list(current_dir.glob("*.csproj"))
        fsproj_files = list(current_dir.glob("*.fsproj"))
        if csproj_files or fsproj_files:
            main_proj = (csproj_files or fsproj_files)[0]
            target_fw = None
            try:
                tree = ET.parse(main_proj)
                root = tree.getroot()
                for tf in root.iter("TargetFramework"):
                    if tf.text:
                        target_fw = tf.text.strip()
                        break
                if not target_fw:
                    for tfs in root.iter("TargetFrameworks"):
                        if tfs.text:
                            target_fw = tfs.text.split(";")[0].strip()
                            break
            except Exception:
                pass

            discovered.append(
                DiscoveredProject(
                    name=main_proj.stem,
                    rel_path=rel_path,
                    abs_path=str(current_dir),
                    ecosystem="dotnet",
                    framework_version=target_fw,
                    manifest_file=main_proj.name,
                )
            )
            found_in_dir = True

        # 3. Check for Python (pyproject.toml, requirements.txt, setup.py)
        pyproject = current_dir / "pyproject.toml"
        req_txt = current_dir / "requirements.txt"
        setup_py = current_dir / "setup.py"

        if (pyproject.is_file() or req_txt.is_file() or setup_py.is_file()) and not found_in_dir:
            py_ver = None
            manifest = "pyproject.toml" if pyproject.is_file() else ("requirements.txt" if req_txt.is_file() else "setup.py")
            if pyproject.is_file():
                try:
                    text = pyproject.read_text(encoding="utf-8")
                    match = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', text)
                    if match:
                        py_ver = match.group(1).lstrip("^~>=<")
                except Exception:
                    pass

            name = current_dir.name if rel_path != "." else "root"
            discovered.append(
                DiscoveredProject(
                    name=name,
                    rel_path=rel_path,
                    abs_path=str(current_dir),
                    ecosystem="python",
                    framework_version=py_ver,
                    manifest_file=manifest,
                )
            )
            found_in_dir = True

        # Recurse into subdirectories if depth allows
        if current_depth < max_depth:
            try:
                for entry in current_dir.iterdir():
                    if entry.is_dir() and entry.name not in cls.EXCLUDED_DIRS and not entry.name.startswith("."):
                        cls._walk(root_path, entry, discovered, current_depth + 1, max_depth)
            except (PermissionError, OSError):
                pass
