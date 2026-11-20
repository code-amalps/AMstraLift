"""AMstraLift Self-Update and Version Synchronization Engine.

Allows AMstraLift to check for newer releases across PyPI and GitHub,
verify cryptographic release integrity, and perform in-place self-updates
without requiring manual maintenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

from packaging.version import InvalidVersion, Version

from amstralift import __version__

PYPI_URL = "https://pypi.org/pypi/amstralift/json"
GITHUB_RELEASES_URL = "https://api.github.com/repos/code-amalps/AMstraLift/releases/latest"


class UpdateCheckResult:
    def __init__(
        self,
        current_version: str,
        latest_version: str,
        update_available: bool,
        published_at: str | None = None,
        release_notes: str | None = None,
        download_url: str | None = None,
        checksum: str | None = None,
        source: str = "pypi",
    ):
        self.current_version = current_version
        self.latest_version = latest_version
        self.update_available = update_available
        self.published_at = published_at
        self.release_notes = release_notes
        self.download_url = download_url
        self.checksum = checksum
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "published_at": self.published_at,
            "release_notes": self.release_notes,
            "download_url": self.download_url,
            "checksum": self.checksum,
            "source": self.source,
        }


class SelfUpdater:
    """Manages update discovery, integrity verification, and in-place application upgrades."""

    @staticmethod
    def is_frozen_binary() -> bool:
        """Returns True if running as a standalone compiled binary (e.g. PyInstaller .exe)."""
        return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

    @classmethod
    def check_for_updates(cls, timeout: float = 6.0) -> UpdateCheckResult:
        """Query PyPI and GitHub to detect if a newer version of AMstraLift is available."""
        # 1. First attempt: PyPI JSON API
        try:
            req = urllib.request.Request(
                PYPI_URL,
                headers={"User-Agent": f"AMstraLift/{__version__}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    info = data.get("info", {})
                    latest_ver = info.get("version", __version__)
                    notes = info.get("summary", "")
                    
                    # Find matching wheel or tarball download url
                    download_url = None
                    checksum = None
                    urls = data.get("urls", [])
                    for u in urls:
                        if u.get("packagetype") == "bdist_wheel":
                            download_url = u.get("url")
                            digests = u.get("digests", {})
                            checksum = digests.get("sha256")
                            break

                    is_newer = cls._is_newer(latest_ver, __version__)
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=latest_ver,
                        update_available=is_newer,
                        release_notes=notes,
                        download_url=download_url,
                        checksum=checksum,
                        source="pypi",
                    )
        except Exception:
            pass

        # 2. Fallback attempt: GitHub Releases API
        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_URL,
                headers={"User-Agent": f"AMstraLift/{__version__}", "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    tag_name = data.get("tag_name", "").lstrip("v")
                    notes = data.get("body", "")
                    pub_date = data.get("published_at", "")
                    
                    download_url = None
                    for asset in data.get("assets", []):
                        name = asset.get("name", "").lower()
                        if cls.is_frozen_binary() and name.endswith(".exe"):
                            download_url = asset.get("browser_download_url")
                            break
                        elif not cls.is_frozen_binary() and name.endswith(".whl"):
                            download_url = asset.get("browser_download_url")
                            break

                    is_newer = cls._is_newer(tag_name, __version__) if tag_name else False
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=tag_name or __version__,
                        update_available=is_newer,
                        published_at=pub_date,
                        release_notes=notes,
                        download_url=download_url,
                        source="github",
                    )
        except Exception:
            pass

        return UpdateCheckResult(
            current_version=__version__,
            latest_version=__version__,
            update_available=False,
            release_notes="Could not connect to update registries (offline mode).",
            source="offline",
        )

    @classmethod
    def execute_update(
        cls,
        target_version: str | None = None,
        force: bool = False,
        progress_callback: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]:
        """Perform self-update of AMstraLift to the target or latest available version."""
        def _log(msg: str):
            if progress_callback:
                progress_callback(msg)

        check = cls.check_for_updates()
        effective_target = target_version or check.latest_version

        if not force and not target_version and not check.update_available:
            return True, f"AMstraLift is already at the latest version ({__version__})."

        # A) Running as standalone binary (PyInstaller .exe)
        if cls.is_frozen_binary():
            _log("Detected standalone executable installation...")
            return cls._update_standalone_executable(check, effective_target, _log)

        # B) Running as Python package / pip / uv environment
        _log("Detected Python package environment...")
        return cls._update_python_package(effective_target, _log)

    @classmethod
    def _update_python_package(
        cls,
        target_version: str,
        log: Callable[[str], None],
    ) -> tuple[bool, str]:
        """Update AMstraLift using pip in the current Python environment."""
        pkg_spec = f"amstralift=={target_version}" if target_version else "amstralift"
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg_spec]
        log(f"Running pip upgrade: {' '.join(cmd)}")

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                return True, f"Successfully upgraded AMstraLift to {target_version or 'latest'} via pip."
            return False, f"Pip upgrade failed (exit {res.returncode}): {res.stderr or res.stdout}"
        except Exception as exc:
            return False, f"Failed to execute pip upgrade: {exc}"

    @classmethod
    def _update_standalone_executable(
        cls,
        check: UpdateCheckResult,
        target_version: str,
        log: Callable[[str], None],
    ) -> tuple[bool, str]:
        """Update running standalone .exe via atomic rotation script."""
        current_exe = Path(sys.executable).resolve()
        log(f"Current executable: {current_exe}")

        if not check.download_url:
            return False, (
                f"No direct binary download URL found for version {target_version}. "
                "Please download the latest standalone binary from https://github.com/code-amalps/AMstraLift/releases"
            )

        log(f"Downloading new release from: {check.download_url}...")
        try:
            temp_exe = current_exe.with_name(f"{current_exe.stem}_new.exe")
            urllib.request.urlretrieve(check.download_url, temp_exe)

            # Verify checksum if available
            if check.checksum:
                log("Verifying SHA-256 download checksum...")
                hasher = hashlib.sha256()
                with open(temp_exe, "rb") as f:
                    while chunk := f.read(65536):
                        hasher.update(chunk)
                computed = hasher.hexdigest()
                if computed.lower() != check.checksum.lower():
                    temp_exe.unlink(missing_ok=True)
                    return False, f"Checksum verification failed! Expected {check.checksum}, got {computed}."
                log("✔ SHA-256 integrity verified.")

            # On Windows, create a self-replacing rotation helper
            if sys.platform == "win32":
                bat_script = current_exe.with_name("amstralift_updater.bat")
                bat_script.write_text(
                    f"""@echo off
timeout /t 1 /nobreak > nul
move /y "{temp_exe}" "{current_exe}"
del "%~f0"
""",
                    encoding="utf-8",
                )
                subprocess.Popen(["cmd.exe", "/c", str(bat_script)], close_fds=True)
                return True, (
                    f"AMstraLift v{target_version} downloaded successfully. "
                    "Update will apply automatically as the application exits."
                )
            else:
                shutil.move(temp_exe, current_exe)
                return True, f"AMstraLift successfully updated to v{target_version}."

        except Exception as exc:
            return False, f"Failed to update standalone executable: {exc}"

    @staticmethod
    def _is_newer(target: str, current: str) -> bool:
        """Safely compare two version strings."""
        try:
            return Version(target) > Version(current)
        except InvalidVersion:
            return target.strip() != current.strip()
