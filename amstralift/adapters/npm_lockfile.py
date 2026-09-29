"""Robust package-lock.json updater for npm-based adapters (Angular, React).

Ensures package-lock.json direct and dev dependencies across all lockfile
versions (v1, v2, v3) are updated accurately and deterministically, and strips
stale integrity/resolved hashes so npm never fails with EINTEGRITY errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from amstralift.core.models import DependencyChange


def update_npm_lockfile(
    repo_path: Path,
    changes: list[DependencyChange],
    env: dict[str, str] | None = None,
) -> bool:
    """Update package-lock.json safely, accurately, and deterministically.

    Updates direct, dev, and root manifest dependencies across all lockfile versions
    (v1, v2, v3), and strips stale integrity/resolved hashes for modified packages
    so npm never fails with EINTEGRITY checksum errors.

    Returns:
        True if package-lock.json was updated, False otherwise.
    """
    lock_file = repo_path / "package-lock.json"
    if not lock_file.exists():
        return False

    try:
        lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
        for change in changes:
            clean_ver = change.to_version.lstrip("^~>=<")
            pkg_key = f"node_modules/{change.package_name}"

            # 1. Update packages["node_modules/<package_name>"] (npm v2/v3)
            if "packages" in lock_data and pkg_key in lock_data["packages"]:
                entry = lock_data["packages"][pkg_key]
                entry["version"] = clean_ver
                # Strip stale integrity & resolved fields so npm re-resolves cleanly without EINTEGRITY
                entry.pop("integrity", None)
                entry.pop("resolved", None)

            # 2. Update root package manifest packages[""] (npm v2/v3)
            if "packages" in lock_data and "" in lock_data["packages"]:
                root_pkg = lock_data["packages"][""]
                if change.change_type == "direct" and "dependencies" in root_pkg:
                    if change.package_name in root_pkg["dependencies"]:
                        root_pkg["dependencies"][change.package_name] = change.to_version
                elif change.change_type == "dev" and "devDependencies" in root_pkg:
                    if change.package_name in root_pkg["devDependencies"]:
                        root_pkg["devDependencies"][change.package_name] = change.to_version

            # 3. Update legacy dependencies section (npm v1/v2)
            if "dependencies" in lock_data and change.package_name in lock_data["dependencies"]:
                dep_entry = lock_data["dependencies"][change.package_name]
                if isinstance(dep_entry, dict):
                    dep_entry["version"] = clean_ver
                    dep_entry.pop("integrity", None)
                    dep_entry.pop("resolved", None)
                else:
                    lock_data["dependencies"][change.package_name] = clean_ver

        lock_file.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False
