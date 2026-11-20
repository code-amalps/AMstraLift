"""System diagnostics, health checks, and self-healing engine for AMstraLift.

Performs comprehensive environmental, configuration, and registry health checks,
and provides automated self-repair for corrupted configs, stale sandbox caches,
and misconfigured runtime environments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from amstralift import __version__
from amstralift.core.workspace import run_git


class CheckStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    REPAIRED = "REPAIRED"


class DiagnosticItem:
    def __init__(
        self,
        name: str,
        category: str,
        status: CheckStatus,
        message: str,
        details: str | None = None,
        repairable: bool = False,
        repaired: bool = False,
    ):
        self.name = name
        self.category = category
        self.status = status
        self.message = message
        self.details = details
        self.repairable = repairable
        self.repaired = repaired

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "repairable": self.repairable,
            "repaired": self.repaired,
        }


class DoctorReport:
    def __init__(self, items: list[DiagnosticItem], repairs_applied: list[str] | None = None):
        self.items = items
        self.repairs_applied = repairs_applied or []

    @property
    def has_errors(self) -> bool:
        return any(item.status == CheckStatus.ERROR for item in self.items)

    @property
    def has_warnings(self) -> bool:
        return any(item.status == CheckStatus.WARNING for item in self.items)

    @property
    def is_healthy(self) -> bool:
        return not self.has_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "is_healthy": self.is_healthy,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "items": [item.to_dict() for item in self.items],
            "repairs_applied": self.repairs_applied,
        }


class DoctorEngine:
    """Engine responsible for inspecting system state and executing self-healing repairs."""

    CONFIG_DIR = Path.home() / ".amstralift"
    CONFIG_FILE = CONFIG_DIR / "gui_config.json"

    def __init__(self, config_dir: Path | None = None):
        if config_dir is not None:
            self.config_dir = config_dir
            self.config_file = config_dir / "gui_config.json"
        else:
            self.config_dir = self.CONFIG_DIR
            self.config_file = self.CONFIG_FILE

    def diagnose_and_heal(self, auto_fix: bool = False) -> DoctorReport:
        """Run all diagnostic checks, optionally executing self-healing repairs."""
        items: list[DiagnosticItem] = []
        repairs: list[str] = []

        # 1. Environment & Core Tools
        items.append(self._check_git())
        items.append(self._check_python())
        items.append(self._check_node_npm())
        items.append(self._check_dotnet())
        items.append(self._check_docker())

        # 2. Local AMstraLift Configuration & Storage
        config_item, config_repair = self._check_and_heal_config(auto_fix=auto_fix)
        items.append(config_item)
        if config_repair:
            repairs.append(config_repair)

        cache_item, cache_repair = self._check_and_heal_temp_cache(auto_fix=auto_fix)
        items.append(cache_item)
        if cache_repair:
            repairs.append(cache_repair)

        # 3. Registry Network Connectivity
        items.append(self._check_osv_connectivity())
        items.append(self._check_npm_registry())
        items.append(self._check_pypi_registry())

        return DoctorReport(items=items, repairs_applied=repairs)

    def _check_git(self) -> DiagnosticItem:
        res = run_git(["--version"], cwd=Path.cwd())
        if res.returncode == 0:
            version_str = res.stdout.strip()
            return DiagnosticItem(
                name="Git SCM",
                category="Core Tools",
                status=CheckStatus.OK,
                message=f"Installed ({version_str})",
            )
        return DiagnosticItem(
            name="Git SCM",
            category="Core Tools",
            status=CheckStatus.ERROR,
            message="Git command not found or returned an error.",
            details="AMstraLift requires Git to manage Stage A sandboxes and create atomic patches.",
        )

    def _check_python(self) -> DiagnosticItem:
        v = sys.version_info
        ver_str = f"{v.major}.{v.minor}.{v.micro}"
        if v.major >= 3 and v.minor >= 10:
            return DiagnosticItem(
                name="Python Runtime",
                category="Core Tools",
                status=CheckStatus.OK,
                message=f"Python {ver_str} ({sys.executable})",
            )
        return DiagnosticItem(
            name="Python Runtime",
            category="Core Tools",
            status=CheckStatus.WARNING,
            message=f"Python {ver_str} is below recommended minimum (3.10+).",
        )

    def _check_node_npm(self) -> DiagnosticItem:
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        npm_path = shutil.which(npm_cmd) or shutil.which("npm")
        if not npm_path:
            return DiagnosticItem(
                name="Node.js & npm",
                category="Ecosystem Adapters",
                status=CheckStatus.WARNING,
                message="npm executable not found in PATH.",
                details="Angular and React upgrades require Node.js and npm to execute builds and resolve dependencies.",
            )
        try:
            res = subprocess.run([npm_path, "--version"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                ver = res.stdout.strip()
                return DiagnosticItem(
                    name="Node.js & npm",
                    category="Ecosystem Adapters",
                    status=CheckStatus.OK,
                    message=f"Installed (npm v{ver})",
                )
        except Exception as exc:
            return DiagnosticItem(
                name="Node.js & npm",
                category="Ecosystem Adapters",
                status=CheckStatus.WARNING,
                message=f"npm located but check timed out or failed: {exc}",
            )
        return DiagnosticItem(
            name="Node.js & npm",
            category="Ecosystem Adapters",
            status=CheckStatus.WARNING,
            message="npm returned non-zero exit code.",
        )

    def _check_dotnet(self) -> DiagnosticItem:
        dotnet_path = shutil.which("dotnet")
        if not dotnet_path:
            return DiagnosticItem(
                name=".NET SDK",
                category="Ecosystem Adapters",
                status=CheckStatus.WARNING,
                message=".NET SDK CLI ('dotnet') not found in PATH.",
                details="Required only if upgrading C# and .NET solutions.",
            )
        try:
            res = subprocess.run(["dotnet", "--version"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                ver = res.stdout.strip()
                return DiagnosticItem(
                    name=".NET SDK",
                    category="Ecosystem Adapters",
                    status=CheckStatus.OK,
                    message=f"Installed (.NET SDK v{ver})",
                )
        except Exception:
            pass
        return DiagnosticItem(
            name=".NET SDK",
            category="Ecosystem Adapters",
            status=CheckStatus.WARNING,
            message=".NET CLI detected but failed to query version.",
        )

    def _check_docker(self) -> DiagnosticItem:
        docker_path = shutil.which("docker")
        if not docker_path:
            return DiagnosticItem(
                name="Docker Daemon",
                category="Optional Containerization",
                status=CheckStatus.WARNING,
                message="Docker CLI not found (Host sandbox isolation active).",
                details="Docker is optional. When absent, AMstraLift uses process-isolated Git worktrees.",
            )
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                return DiagnosticItem(
                    name="Docker Daemon",
                    category="Optional Containerization",
                    status=CheckStatus.OK,
                    message="Docker daemon connected and ready for containerized isolation.",
                )
        except Exception:
            pass
        return DiagnosticItem(
            name="Docker Daemon",
            category="Optional Containerization",
            status=CheckStatus.WARNING,
            message="Docker CLI present but daemon is not running.",
            details="Host sandbox isolation will be used instead.",
        )

    def _check_and_heal_config(self, auto_fix: bool = False) -> tuple[DiagnosticItem, str | None]:
        repair_msg: str | None = None
        default_config = {"theme": "dark", "recent_repos": []}

        if not self.config_dir.exists():
            if auto_fix:
                try:
                    self.config_dir.mkdir(parents=True, exist_ok=True)
                    self.config_file.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
                    repair_msg = f"Created missing configuration directory and default config at {self.config_dir}."
                    return DiagnosticItem(
                        name="User Configuration",
                        category="Storage & Config",
                        status=CheckStatus.REPAIRED,
                        message="Created missing user configuration directory and defaults.",
                        repaired=True,
                    ), repair_msg
                except Exception as exc:
                    return DiagnosticItem(
                        name="User Configuration",
                        category="Storage & Config",
                        status=CheckStatus.ERROR,
                        message=f"Failed to create config directory: {exc}",
                    ), None
            else:
                return DiagnosticItem(
                    name="User Configuration",
                    category="Storage & Config",
                    status=CheckStatus.WARNING,
                    message=f"Configuration directory {self.config_dir} does not exist.",
                    repairable=True,
                ), None

        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Configuration root must be a JSON object.")
                return DiagnosticItem(
                    name="User Configuration",
                    category="Storage & Config",
                    status=CheckStatus.OK,
                    message=f"Valid configuration found ({self.config_file})",
                ), None
            except Exception as exc:
                if auto_fix:
                    try:
                        # Backup corrupted file
                        bak_file = self.config_file.with_suffix(".json.corrupted_bak")
                        shutil.copyfile(self.config_file, bak_file)
                        self.config_file.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
                        repair_msg = f"Repaired corrupted config file (backup saved to {bak_file.name})."
                        return DiagnosticItem(
                            name="User Configuration",
                            category="Storage & Config",
                            status=CheckStatus.REPAIRED,
                            message=f"Repaired corrupted config file: {repair_msg}",
                            repaired=True,
                        ), repair_msg
                    except Exception as rep_exc:
                        return DiagnosticItem(
                            name="User Configuration",
                            category="Storage & Config",
                            status=CheckStatus.ERROR,
                            message=f"Failed to repair corrupted config: {rep_exc}",
                        ), None
                return DiagnosticItem(
                    name="User Configuration",
                    category="Storage & Config",
                    status=CheckStatus.ERROR,
                    message=f"Corrupted configuration JSON: {exc}",
                    details="Run with --fix to automatically restore default configuration.",
                    repairable=True,
                ), None
        else:
            if auto_fix:
                try:
                    self.config_file.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
                    repair_msg = f"Created default config at {self.config_file}."
                    return DiagnosticItem(
                        name="User Configuration",
                        category="Storage & Config",
                        status=CheckStatus.REPAIRED,
                        message="Created default user configuration file.",
                        repaired=True,
                    ), repair_msg
                except Exception as exc:
                    return DiagnosticItem(
                        name="User Configuration",
                        category="Storage & Config",
                        status=CheckStatus.ERROR,
                        message=f"Failed to create config file: {exc}",
                    ), None
            return DiagnosticItem(
                name="User Configuration",
                category="Storage & Config",
                status=CheckStatus.WARNING,
                message=f"Config file {self.config_file} missing.",
                repairable=True,
            ), None

    def _check_and_heal_temp_cache(self, auto_fix: bool = False) -> tuple[DiagnosticItem, str | None]:
        temp_dir = Path(tempfile.gettempdir())
        stale_prefixes = ("amstralift_stage_a_", "amstralift_security_sandbox_")
        stale_dirs: list[Path] = []

        try:
            for entry in temp_dir.iterdir():
                if entry.is_dir() and any(entry.name.startswith(p) for p in stale_prefixes):
                    stale_dirs.append(entry)
        except Exception:
            pass

        count = len(stale_dirs)
        if count == 0:
            return DiagnosticItem(
                name="Sandbox Temp Storage",
                category="Storage & Config",
                status=CheckStatus.OK,
                message="Clean (0 orphan sandboxes in system temp)",
            ), None

        if auto_fix:
            cleaned = 0
            for d in stale_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass
            repair_msg = f"Purged {cleaned} orphaned Stage A sandbox directory(ies) from system temp."
            return DiagnosticItem(
                name="Sandbox Temp Storage",
                category="Storage & Config",
                status=CheckStatus.REPAIRED,
                message=f"Cleaned up {cleaned} stale sandbox directories.",
                repaired=True,
            ), repair_msg

        return DiagnosticItem(
            name="Sandbox Temp Storage",
            category="Storage & Config",
            status=CheckStatus.WARNING,
            message=f"{count} orphaned sandbox temporary folder(s) found in {temp_dir}.",
            details="Run with --fix to clean up orphaned sandbox directories.",
            repairable=True,
        ), None

    def _check_osv_connectivity(self) -> DiagnosticItem:
        url = "https://api.osv.dev/v1/querybatch"
        try:
            req = urllib.request.Request(
                url,
                data=b'{"queries": []}',
                headers={"Content-Type": "application/json", "User-Agent": f"AMstraLift/{__version__}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return DiagnosticItem(
                        name="OSV.dev Vulnerability API",
                        category="Network Registries",
                        status=CheckStatus.OK,
                        message="Connected (api.osv.dev online)",
                    )
        except Exception as exc:
            return DiagnosticItem(
                name="OSV.dev Vulnerability API",
                category="Network Registries",
                status=CheckStatus.WARNING,
                message="Unreachable or timed out.",
                details=f"Live CVE security audits require internet access to OSV.dev. Offline scans use local cache. ({exc})",
            )
        return DiagnosticItem(
            name="OSV.dev Vulnerability API",
            category="Network Registries",
            status=CheckStatus.WARNING,
            message="Non-200 response from OSV API.",
        )

    def _check_npm_registry(self) -> DiagnosticItem:
        url = "https://registry.npmjs.org/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"AMstraLift/{__version__}"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    return DiagnosticItem(
                        name="npm Package Registry",
                        category="Network Registries",
                        status=CheckStatus.OK,
                        message="Connected (registry.npmjs.org online)",
                    )
        except Exception:
            return DiagnosticItem(
                name="npm Package Registry",
                category="Network Registries",
                status=CheckStatus.WARNING,
                message="Unreachable or slow connection to registry.npmjs.org.",
            )
        return DiagnosticItem(
            name="npm Package Registry",
            category="Network Registries",
            status=CheckStatus.WARNING,
            message="npm registry returned non-200 status.",
        )

    def _check_pypi_registry(self) -> DiagnosticItem:
        url = "https://pypi.org/pypi/amstralift/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"AMstraLift/{__version__}"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status in (200, 404):  # 404 is fine if package isn't published yet
                    return DiagnosticItem(
                        name="PyPI Package Registry",
                        category="Network Registries",
                        status=CheckStatus.OK,
                        message="Connected (pypi.org online)",
                    )
        except Exception:
            return DiagnosticItem(
                name="PyPI Package Registry",
                category="Network Registries",
                status=CheckStatus.WARNING,
                message="Unreachable or slow connection to pypi.org.",
            )
        return DiagnosticItem(
            name="PyPI Package Registry",
            category="Network Registries",
            status=CheckStatus.WARNING,
            message="PyPI returned non-200 status.",
        )
