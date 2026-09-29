"""Robust package-lock.json updater for npm-based adapters (Angular, React).

Ensures package-lock.json direct, dev, companion, and transitive dependencies
across all lockfile versions (v1, v2, v3) are updated accurately and deterministically,
strips stale integrity/resolved hashes, prunes obsolete devkit entries, and
reconciles lockfile consistency using npm when available.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from amstralift.core.models import DependencyChange

logger = logging.getLogger("amstralift.adapters.npm_lockfile")

# Obsolete devkit packages from older Angular versions that do not exist or break modern Angular
OBSOLETE_DEVKIT_PACKAGES = {
    "@angular-devkit/build-optimizer",
    "@angular-devkit/build-webpack",
}

# Companion devkit packages that must track @angular/cli version in lockfiles
COMPANION_DEVKIT_PACKAGES = {
    "@angular-devkit/core",
    "@angular-devkit/schematics",
    "@angular-devkit/architect",
    "@angular-devkit/build-angular",
}


def update_npm_lockfile(
    repo_path: Path,
    changes: list[DependencyChange],
    env: dict[str, str] | None = None,
) -> bool:
    """Update package-lock.json safely, accurately, and deterministically.

    Updates direct, dev, companion, and root manifest dependencies across all lockfile
    versions (v1, v2, v3), strips stale integrity/resolved hashes for modified packages,
    prunes obsolete devkit entries, and reconciles via 'npm install --package-lock-only'
    if the npm CLI is available.

    Returns:
        True if package-lock.json was updated, False otherwise.
    """
    lock_file = repo_path / "package-lock.json"
    if not lock_file.exists():
        return False

    try:
        lock_data = json.loads(lock_file.read_text(encoding="utf-8"))

        # Determine target devkit version if @angular/cli or devkit packages are being upgraded
        cli_or_devkit_change = next(
            (
                c
                for c in changes
                if c.package_name in ("@angular/cli", "@angular-devkit/build-angular")
            ),
            None,
        )
        target_devkit_ver = (
            cli_or_devkit_change.to_version.lstrip("^~>=<")
            if cli_or_devkit_change
            else None
        )

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

        # 4. Synchronize companion @angular-devkit packages and prune obsolete/nested entries
        if target_devkit_ver and "packages" in lock_data:
            packages = lock_data["packages"]
            keys_to_delete = []

            for key in list(packages.keys()):
                if not key:
                    continue

                entry = packages[key]

                # Delete obsolete devkit packages (top-level and nested)
                if any(f"/{obs}" in key for obs in OBSOLETE_DEVKIT_PACKAGES):
                    keys_to_delete.append(key)
                    continue

                # Delete ANY nested copy of a companion devkit package.
                # Pattern: "node_modules/<anything>/node_modules/@angular-devkit/<comp>"
                # This covers:
                #   node_modules/@angular/cli/node_modules/@angular-devkit/core
                #   node_modules/@angular-devkit/build-angular/node_modules/@angular-devkit/core
                #   node_modules/@schematics/angular/node_modules/@angular-devkit/core  etc.
                is_nested_companion = (
                    "/node_modules/" in key
                    and any(f"@angular-devkit/{comp.split('/')[1]}" in key for comp in COMPANION_DEVKIT_PACKAGES)
                )
                if is_nested_companion:
                    keys_to_delete.append(key)
                    continue

                # Also prune old webpack / webassembly etc. nested inside build-angular from Angular 12
                if "node_modules/@angular-devkit/build-angular/node_modules/" in key:
                    keys_to_delete.append(key)
                    continue

                # Synchronize TOP-LEVEL companion devkit packages only
                for comp in COMPANION_DEVKIT_PACKAGES:
                    if key == f"node_modules/{comp}":
                        entry["version"] = target_devkit_ver
                        entry.pop("integrity", None)
                        entry.pop("resolved", None)
                        # Clean obsolete sub-deps
                        if "dependencies" in entry and isinstance(entry["dependencies"], dict):
                            for sub_k in list(entry["dependencies"].keys()):
                                if sub_k in OBSOLETE_DEVKIT_PACKAGES:
                                    del entry["dependencies"][sub_k]
                        break

            for k in keys_to_delete:
                packages.pop(k, None)

        # 4b. Synchronize legacy dependencies section
        if target_devkit_ver and "dependencies" in lock_data:
            deps = lock_data["dependencies"]
            for obs in OBSOLETE_DEVKIT_PACKAGES:
                deps.pop(obs, None)

            for comp in COMPANION_DEVKIT_PACKAGES:
                if comp in deps:
                    comp_entry = deps[comp]
                    if isinstance(comp_entry, dict):
                        comp_entry["version"] = target_devkit_ver
                        comp_entry.pop("integrity", None)
                        comp_entry.pop("resolved", None)
                        comp_entry.pop("requires", None)
                        comp_entry.pop("dependencies", None)
                    else:
                        deps[comp] = target_devkit_ver

        lock_file.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

        # 5. Optional: Reconcile full lockfile using npm if package.json exists and not in unit tests
        pkg_file = repo_path / "package.json"
        if pkg_file.exists() and not os.environ.get("PYTEST_CURRENT_TEST"):
            npm_cmd = shutil.which("npm", path=env.get("PATH") if env else None) or shutil.which("npm")
            if npm_cmd:
                try:
                    subprocess.run(
                        [
                            npm_cmd,
                            "install",
                            "--package-lock-only",
                            "--legacy-peer-deps",
                            "--no-audit",
                            "--no-fund",
                        ],
                        cwd=repo_path,
                        env=env or os.environ.copy(),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        shell=sys.platform == "win32",
                    )
                except Exception as e:
                    logger.debug(f"npm install --package-lock-only reconciliation skipped: {e}")

        return True
    except Exception as e:
        logger.warning(f"Failed to update package-lock.json: {e}")
        return False
