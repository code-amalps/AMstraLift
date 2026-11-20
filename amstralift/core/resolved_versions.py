"""Extracts exact frozen dependency versions from lockfiles and manifests.

Provides a deterministic snapshot of all direct and transitive packages
resolved on the day of the modernization run, ensuring reproducible audits.
"""

from __future__ import annotations

import json
from pathlib import Path

from amstralift.core.workspace import safe_rglob


def extract_npm_resolved_versions(lockfile_path: Path) -> dict[str, str]:
    """Extract package_name -> resolved_version from package-lock.json."""
    if not lockfile_path.exists():
        return {}
    try:
        data = json.loads(lockfile_path.read_text(encoding="utf-8"))
        resolved: dict[str, str] = {}
        # Lockfile v2 / v3 packages map
        packages = data.get("packages", {})
        if packages and isinstance(packages, dict):
            for path_key, meta in packages.items():
                if not isinstance(meta, dict) or not path_key:
                    continue
                ver = meta.get("version")
                if not ver:
                    continue
                parts = path_key.split("node_modules/")
                pkg_name = parts[-1] if parts else path_key
                if pkg_name:
                    resolved[pkg_name] = str(ver)

        # Lockfile v1 dependencies map (or fallback)
        deps = data.get("dependencies", {})
        if deps and isinstance(deps, dict):
            def _walk_v1(d: dict):
                for pkg_name, meta in d.items():
                    if isinstance(meta, dict):
                        if "version" in meta:
                            resolved.setdefault(pkg_name, str(meta["version"]))
                        if "dependencies" in meta and isinstance(meta["dependencies"], dict):
                            _walk_v1(meta["dependencies"])
            _walk_v1(deps)

        return dict(sorted(resolved.items()))
    except Exception:
        return {}


def extract_dotnet_resolved_versions(repo_path: Path) -> dict[str, str]:
    """Extract package_name -> resolved_version from packages.lock.json or *.csproj."""
    resolved: dict[str, str] = {}
    for lock_path in safe_rglob(repo_path, "packages.lock.json"):
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            dependencies = data.get("dependencies", {})
            for tfm_deps in dependencies.values():
                if isinstance(tfm_deps, dict):
                    for pkg, meta in tfm_deps.items():
                        if isinstance(meta, dict) and "resolved" in meta:
                            resolved[pkg] = str(meta["resolved"])
        except Exception:
            continue
    return dict(sorted(resolved.items()))


def extract_python_resolved_versions(repo_path: Path) -> dict[str, str]:
    """Extract package_name -> resolved_version from poetry.lock, Pipfile.lock, or requirements.txt."""
    resolved: dict[str, str] = {}
    # 1. poetry.lock
    poetry_lock = repo_path / "poetry.lock"
    if poetry_lock.exists():
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore
            doc = tomllib.loads(poetry_lock.read_text(encoding="utf-8"))
            for pkg in doc.get("package", []):
                if isinstance(pkg, dict) and "name" in pkg and "version" in pkg:
                    resolved[pkg["name"]] = str(pkg["version"])
        except Exception:
            pass

    # 2. requirements*.txt
    for req_file in safe_rglob(repo_path, "requirements*.txt"):
        try:
            for line in req_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "==" in line:
                    parts = line.split("==")
                    pkg = parts[0].strip()
                    ver = parts[1].split(";")[0].strip()
                    resolved.setdefault(pkg, ver)
        except Exception:
            continue

    return dict(sorted(resolved.items()))


def capture_resolved_versions(repo_path: Path, ecosystem: str) -> dict[str, str]:
    """Capture snapshot of exact resolved dependency versions in the workspace."""
    eco = ecosystem.lower()
    if eco in ("angular", "react", "node", "npm"):
        lock_file = repo_path / "package-lock.json"
        if not lock_file.exists():
            lock_file = repo_path.parent / "package-lock.json"
        return extract_npm_resolved_versions(lock_file)
    elif eco == "dotnet":
        return extract_dotnet_resolved_versions(repo_path)
    elif eco == "python":
        return extract_python_resolved_versions(repo_path)
    return {}
