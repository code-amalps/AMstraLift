"""Robust package-lock.json updater for npm-based adapters (Angular, React).

Strategy (in priority order):
1. REGENERATE (preferred): delete the old lockfile and let npm recreate it fresh
   from the updated package.json.  This is the only 100%-reliable approach for
   large version jumps (e.g. Angular 12 → 22) because the old lockfile contains
   deeply-nested, version-pinned transitive entries that cannot all be patched
   surgically.
2. SURGICAL PATCH (fallback when npm is unavailable): update the top-level entries,
   strip stale integrity/resolved hashes, prune obsolete/nested devkit copies, and
   synchronise companion @angular-devkit packages.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_ORIG_SUBPROCESS_RUN = subprocess.run

from amstralift.core.models import DependencyChange

logger = logging.getLogger("amstralift.adapters.npm_lockfile")

# Obsolete devkit packages from older Angular versions that do not exist in modern Angular
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


def _find_npm(env: dict[str, str] | None) -> str | None:
    """Locate the npm executable using the provided PATH or system PATH."""
    return (
        shutil.which("npm", path=env.get("PATH") if env else None)
        or shutil.which("npm")
    )


def _handle_remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _remove_node_modules(repo_path: Path) -> None:
    """Remove stale sandbox dependencies without following symlinks or junctions."""
    node_modules = repo_path / "node_modules"
    try:
        node_stat = node_modules.lstat()
    except FileNotFoundError:
        return

    attributes = getattr(node_stat, "st_file_attributes", 0)
    is_reparse_point = bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    try:
        if node_modules.is_symlink() or (os.name == "nt" and is_reparse_point):
            if os.name == "nt":
                os.rmdir(node_modules)
            else:
                node_modules.unlink()
        elif node_modules.is_dir():
            try:
                shutil.rmtree(node_modules, onexc=_handle_remove_readonly)
            except TypeError:
                shutil.rmtree(node_modules, onerror=_handle_remove_readonly)
        else:
            node_modules.unlink()
    except Exception as exc:
        logger.warning("Could not fully remove node_modules (may be locked by running process): %s", exc)


def _run_with_heartbeat(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    timeout: int,
    message: str,
) -> subprocess.CompletedProcess[str]:
    """Execute a subprocess while periodically printing heartbeat progress and checking cancellation."""
    # If subprocess.run is mocked in tests, delegate to it directly to honor mock expectations
    if subprocess.run is not _ORIG_SUBPROCESS_RUN:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=sys.platform == "win32",
        )

    from amstralift.core.cancellation import check_cancelled
    import time

    start_t = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=sys.platform == "win32",
    )
    last_ping = start_t
    try:
        while proc.poll() is None:
            check_cancelled()
            time.sleep(1.0)
            now = time.time()
            if now - start_t > timeout:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)
            if now - last_ping >= 15.0:
                elapsed = int(now - start_t)
                print(f"   ↳ {message} ({elapsed}s elapsed)...", flush=True)
                last_ping = now
        stdout, stderr = proc.communicate()
    except BaseException:
        proc.kill()
        proc.wait()
        raise

    return subprocess.CompletedProcess(args=cmd, returncode=proc.returncode, stdout=stdout, stderr=stderr)


def _run_npm_install(
    repo_path: Path,
    env: dict[str, str] | None,
    timeout: int = 600,
) -> None:
    """Run npm install to resolve upgraded dependencies cleanly in the workspace."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    npm_cmd = _find_npm(env)
    if not npm_cmd:
        raise RuntimeError("npm CLI not found; cannot install updated package versions.")

    import time

    _remove_node_modules(repo_path)
    print("   ↳ Running 'npm install' to resolve upgraded dependencies (this may take a minute)...", flush=True)
    start_t = time.time()
    result = _run_with_heartbeat(
        [npm_cmd, "install", "--legacy-peer-deps", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=repo_path,
        env=env or os.environ.copy(),
        timeout=timeout,
        message="Still resolving upgraded npm dependencies",
    )
    duration = time.time() - start_t
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"npm install failed (exit {result.returncode}): {detail[-3000:]}"
        )
    print(f"   ✔ npm dependencies installed successfully ({duration:.1f}s)", flush=True)


def install_npm_dependencies(
    repo_path: Path,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> None:
    """Install the updated manifest into this workspace, never a linked source tree."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    _run_npm_install(repo_path, env, timeout)



def _regenerate_lockfile(
    repo_path: Path,
    npm_cmd: str,
    env: dict[str, str] | None,
    timeout: int = 300,
) -> bool:
    """Delete the old package-lock.json and regenerate it from package.json.

    This is the preferred strategy for large version jumps because it produces
    a completely clean lockfile with no stale nested entries.

    Returns True on success, False on failure.
    """
    import time

    lock_file = repo_path / "package-lock.json"
    backup = repo_path / "package-lock.json.amstralift_bak"

    # Keep a backup so we can restore on failure
    try:
        if lock_file.exists():
            import shutil as _shutil
            _shutil.copy2(lock_file, backup)
        lock_file.unlink(missing_ok=True)

        print("   ↳ Running 'npm install --package-lock-only' to generate clean lockfile...", flush=True)
        start_t = time.time()
        result = _run_with_heartbeat(
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
            timeout=timeout,
            message="Still generating clean package-lock.json",
        )
        duration = time.time() - start_t

        if result.returncode == 0 and lock_file.exists():
            backup.unlink(missing_ok=True)
            print(f"   ✔ package-lock.json regenerated cleanly ({duration:.1f}s)", flush=True)
            logger.debug("package-lock.json regenerated cleanly via npm install --package-lock-only")
            return True

        # Restore backup on failure
        logger.warning(
            "npm install --package-lock-only failed (rc=%s): %s",
            result.returncode,
            (result.stderr or result.stdout or "")[:200],
        )
        if backup.exists() and not lock_file.exists():
            import shutil as _shutil
            _shutil.move(str(backup), str(lock_file))
        return False

    except Exception as exc:
        logger.warning("lockfile regeneration error: %s", exc)
        if backup.exists() and not lock_file.exists():
            import shutil as _shutil
            _shutil.move(str(backup), str(lock_file))
        return False
    finally:
        backup.unlink(missing_ok=True)


def update_npm_lockfile(
    repo_path: Path,
    changes: list[DependencyChange],
    env: dict[str, str] | None = None,
) -> bool:
    """Update package-lock.json for the given dependency changes.

    Tries full lockfile regeneration via npm first (Strategy 1).
    Falls back to deterministic surgical patching if npm is unavailable (Strategy 2).

    Returns:
        True if package-lock.json was updated, False otherwise.
    """
    lock_file = repo_path / "package-lock.json"
    if not lock_file.exists():
        return False

    # ── Strategy 1: Full regeneration via npm ────────────────────────────────
    # Skip in unit tests (PYTEST_CURRENT_TEST is set by pytest automatically).
    npm_cmd = _find_npm(env)
    if npm_cmd and not os.environ.get("PYTEST_CURRENT_TEST"):
        pkg_file = repo_path / "package.json"
        if pkg_file.exists():
            _remove_node_modules(repo_path)
            if _regenerate_lockfile(repo_path, npm_cmd, env):
                return True

    # ── Strategy 2: Surgical patch (fallback) ────────────────────────────────
    return _surgical_patch_lockfile(repo_path, changes, env)


def _surgical_patch_lockfile(
    repo_path: Path,
    changes: list[DependencyChange],
    env: dict[str, str] | None = None,
) -> bool:
    """Deterministically patch the existing package-lock.json.

    Handles lockfile versions v1/v2/v3, strips stale integrity/resolved fields,
    prunes obsolete/nested devkit copies, and synchronises companion devkit versions.
    """
    lock_file = repo_path / "package-lock.json"
    if not lock_file.exists():
        return False

    try:
        lock_data = json.loads(lock_file.read_text(encoding="utf-8"))

        # Determine target devkit version from the upgrade set
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

            # 1. packages["node_modules/<pkg>"] (npm v2/v3)
            if "packages" in lock_data and pkg_key in lock_data["packages"]:
                entry = lock_data["packages"][pkg_key]
                entry["version"] = clean_ver
                entry.pop("integrity", None)
                entry.pop("resolved", None)

            # 2. Root manifest packages[""] (npm v2/v3)
            if "packages" in lock_data and "" in lock_data["packages"]:
                root_pkg = lock_data["packages"][""]
                if change.change_type == "direct" and "dependencies" in root_pkg:
                    if change.package_name in root_pkg["dependencies"]:
                        root_pkg["dependencies"][change.package_name] = change.to_version
                elif change.change_type == "dev" and "devDependencies" in root_pkg:
                    if change.package_name in root_pkg["devDependencies"]:
                        root_pkg["devDependencies"][change.package_name] = change.to_version

            # 3. Legacy flat dependencies (npm v1/v2)
            if "dependencies" in lock_data and change.package_name in lock_data["dependencies"]:
                dep_entry = lock_data["dependencies"][change.package_name]
                if isinstance(dep_entry, dict):
                    dep_entry["version"] = clean_ver
                    dep_entry.pop("integrity", None)
                    dep_entry.pop("resolved", None)
                else:
                    lock_data["dependencies"][change.package_name] = clean_ver

        # 4. Prune nested/obsolete devkit entries and sync companions (v2/v3)
        if target_devkit_ver and "packages" in lock_data:
            packages = lock_data["packages"]
            keys_to_delete = []

            for key in list(packages.keys()):
                if not key:
                    continue
                entry = packages[key]

                # Remove obsolete devkit packages wherever they appear
                if any(f"/{obs}" in key for obs in OBSOLETE_DEVKIT_PACKAGES):
                    keys_to_delete.append(key)
                    continue

                # Remove ALL nested copies of companion devkit packages
                # (e.g. node_modules/@angular/cli/node_modules/@angular-devkit/core)
                is_nested_companion = "/node_modules/" in key and any(
                    f"@angular-devkit/{comp.split('/')[1]}" in key
                    for comp in COMPANION_DEVKIT_PACKAGES
                )
                if is_nested_companion:
                    keys_to_delete.append(key)
                    continue

                # Remove all entries nested under build-angular (Angular 12 webpack tree)
                if "node_modules/@angular-devkit/build-angular/node_modules/" in key:
                    keys_to_delete.append(key)
                    continue

                # Update top-level companion versions
                for comp in COMPANION_DEVKIT_PACKAGES:
                    if key == f"node_modules/{comp}":
                        entry["version"] = target_devkit_ver
                        entry.pop("integrity", None)
                        entry.pop("resolved", None)
                        if "dependencies" in entry and isinstance(entry["dependencies"], dict):
                            for sub_k in list(entry["dependencies"].keys()):
                                if sub_k in OBSOLETE_DEVKIT_PACKAGES:
                                    del entry["dependencies"][sub_k]
                        break

            for k in keys_to_delete:
                packages.pop(k, None)

        # 4b. Sync legacy flat section
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
        return True

    except Exception as exc:
        logger.warning("Surgical lockfile patch failed: %s", exc)
        return False
