"""Python ecosystem adapter (Section 2, 6, 8, 9).

Handles Python package discovery via PyPI JSON API, pyproject.toml / requirements.txt
updates, runtime governance evaluation, dependency tiering, and test execution.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger("amstralift.adapters.python")


def get_python_execution_env(repo_path: Path) -> dict[str, str]:
    """Return environment dictionary with local .venv prioritized in PATH if present."""
    env = os.environ.copy()
    scripts_dir = repo_path / (".venv/Scripts" if sys.platform == "win32" else ".venv/bin")
    if scripts_dir.exists():
        env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(repo_path / ".venv")
    return env

from amstralift.adapters.base import BaseAdapter
from amstralift.core.workspace import safe_rglob
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
)
from amstralift.governance.python_runtime import (
    PythonRuntimeConfig,
    PythonRuntimeGovernance,
)

PYPI_BASE = "https://pypi.org/pypi"

# Tiering classifications for Python packages (Section 8)
TIER_3_PATTERNS = [
    re.compile(r".*(auth|security|crypto|payment|stripe|jwt|oauth|ssl).*", re.IGNORECASE),
    re.compile(r"^(cryptography|pyjwt|bcrypt|passlib|auth0-python)$", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(r"^(fastapi|django|flask|sqlalchemy|pydantic|alembic|celery|tornado|aiohttp)$", re.IGNORECASE),
    re.compile(r".*(framework|routing|database|db|orm|queue).*", re.IGNORECASE),
]


def classify_python_tier(package_name: str) -> DependencyTier:
    """Classify Python dependency package into Tier 1, 2, or 3."""
    for pattern in TIER_3_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_3_CRITICAL

    for pattern in TIER_2_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR

    return DependencyTier.TIER_1_SAFE


class PythonAdapter(BaseAdapter):
    """Adapter for Python projects using pyproject.toml or requirements.txt."""

    def __init__(
        self,
        pypi_base: str = PYPI_BASE,
        timeout_seconds: float = 10.0,
        runtime_config: PythonRuntimeConfig | None = None,
    ):
        self.pypi_base = pypi_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.runtime_config = runtime_config

    @property
    def name(self) -> str:
        return "python"

    def detect(self, repo_path: Path) -> bool:
        """Detect Python project via pyproject.toml, requirements.txt, or setup.py."""
        return (
            (repo_path / "pyproject.toml").exists()
            or (repo_path / "requirements.txt").exists()
            or (repo_path / "setup.py").exists()
        )

    def fetch_latest_version(self, package_name: str) -> str | None:
        """Fetch latest stable GA version from PyPI JSON API, rejecting pre-releases."""
        url = f"{self.pypi_base}/{package_name}/json"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    info_ver = data.get("info", {}).get("version")
                    from packaging.version import InvalidVersion, Version
                    if info_ver:
                        try:
                            v_obj = Version(info_ver)
                            if not v_obj.is_prerelease and not v_obj.is_devrelease:
                                return info_ver
                        except InvalidVersion:
                            pass

                    # Info version was pre-release or invalid; find highest stable release
                    releases = data.get("releases", {})
                    stable_versions = []
                    for rel_str in releases:
                        try:
                            v_obj = Version(rel_str)
                            if not v_obj.is_prerelease and not v_obj.is_devrelease:
                                stable_versions.append((v_obj, rel_str))
                        except InvalidVersion:
                            continue
                    if stable_versions:
                        stable_versions.sort(key=lambda item: item[0])
                        return stable_versions[-1][1]
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Discover candidate package upgrades in pyproject.toml and requirements.txt."""
        candidates: list[DependencyChange] = []

        # 1. Inspect pyproject.toml
        pyproject_file = repo_path / "pyproject.toml"
        if pyproject_file.exists():
            text = pyproject_file.read_text(encoding="utf-8")

            # Extract declared requires-python if present
            req_py_match = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', text)
            repo_requires_python = req_py_match.group(1) if req_py_match else None

            # Evaluate runtime governance if configured
            if self.runtime_config:
                decision = PythonRuntimeGovernance.evaluate(
                    config=self.runtime_config,
                    repo_requires_python=repo_requires_python,
                )
                if decision.action == "APPLY_DEFAULT" and decision.resolved_runtime:
                    # Propose runtime update
                    candidates.append(
                        DependencyChange(
                            package_name="python",
                            from_version=repo_requires_python or "unpinned",
                            to_version=decision.resolved_runtime,
                            change_type="direct",
                            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
                            rationale=decision.reason,
                        )
                    )

            # Match dependencies like "package>=1.0.0", "package==1.0.0", "package~=1.0.0"
            dep_matches = re.findall(r'["\']([a-zA-Z0-9_\-]+)\s*([~>=<][=~><0-9\.\*]+)?["\']', text)
            for pkg, ver_spec in dep_matches:
                if pkg.lower() in ("python", "project", "tool", "dependencies"):
                    continue
                latest = self.fetch_latest_version(pkg)
                if latest:
                    clean_cur = ver_spec.lstrip("~>=<").strip() if ver_spec else "unpinned"
                    if latest != clean_cur:
                        tier = classify_python_tier(pkg)
                        candidates.append(
                            DependencyChange(
                                package_name=pkg,
                                from_version=ver_spec or "unpinned",
                                to_version=f">={latest}",
                                change_type="direct",
                                tier=tier,
                                rationale=f"Upgrade Python dependency {pkg} to latest {latest}",
                            )
                        )

        # 2. Inspect requirements.txt if present and no candidates yet
        req_file = repo_path / "requirements.txt"
        if req_file.exists() and not candidates:
            lines = req_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^([a-zA-Z0-9_\-]+)\s*([~>=<]=?\s*[0-9\.\*]+)?", line)
                if match:
                    pkg = match.group(1)
                    ver = match.group(2) or "unpinned"
                    latest = self.fetch_latest_version(pkg)
                    if latest:
                        tier = classify_python_tier(pkg)
                        candidates.append(
                            DependencyChange(
                                package_name=pkg,
                                from_version=ver,
                                to_version=f">={latest}",
                                change_type="direct",
                                tier=tier,
                                rationale=f"Upgrade requirements.txt entry {pkg} to {latest}",
                            )
                        )

        return candidates

    def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
        """Apply dependency updates to pyproject.toml or requirements.txt."""
        pyproject_file = repo_path / "pyproject.toml"
        if pyproject_file.exists():
            content = pyproject_file.read_text(encoding="utf-8")
            for c in changes:
                if c.package_name == "python":
                    if "requires-python" in content:
                        content = re.sub(
                            r'requires-python\s*=\s*["\'][^"\']+["\']',
                            f'requires-python = "{c.to_version}"',
                            content,
                        )
                    else:
                        content = re.sub(
                            r"(\[project\])",
                            f'\\1\nrequires-python = "{c.to_version}"',
                            content,
                        )
                else:
                    pattern = rf'["\']{re.escape(c.package_name)}\s*([~>=<][=~><0-9\.\*]+)?["\']'
                    replacement = f'"{c.package_name}{c.to_version}"'
                    content = re.sub(pattern, replacement, content)
            pyproject_file.write_text(content, encoding="utf-8")

        req_file = repo_path / "requirements.txt"
        if req_file.exists():
            content = req_file.read_text(encoding="utf-8")
            for c in changes:
                if c.package_name != "python":
                    pattern = rf"^{re.escape(c.package_name)}\s*([~>=<]=?\s*[0-9\.\*]+)?"
                    replacement = f"{c.package_name}{c.to_version}"
                    content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            req_file.write_text(content, encoding="utf-8")

        # ── Docker: update FROM / image: tags in Dockerfiles & docker-compose ──
        python_change = next(
            (c for c in changes if c.package_name == "python"),
            None,
        )
        if python_change:
            from amstralift.adapters.docker_updater import DockerfileUpdater
            DockerfileUpdater.update(repo_path, ecosystem="python", target_version=python_change.to_version)

    def run_build_and_tests(self, repo_path: Path, timeout_seconds: float = 300.0) -> GateSummary:
        """Execute build and test gates declared in the Python project."""
        results: list[GateResult] = []

        # Check for pytest or unittest
        test_cmd = None
        if (repo_path / "tests").exists() or any(repo_path.glob("test_*.py")):
            if shutil.which("pytest"):
                test_cmd = "pytest"
            else:
                test_cmd = "python -m unittest discover"

        gates = [
            ("test", test_cmd, True),
            ("lint", "ruff check ." if shutil.which("ruff") else None, False),
        ]

    def run_build_and_tests(
        self,
        repo_path: Path,
        timeout_seconds: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GateSummary:
        """Execute test gate (pytest/unittest) and optional lint gate."""
        results: list[GateResult] = []
        env = get_python_execution_env(repo_path)
        pytest_bin = "pytest"
        if (repo_path / ".venv/Scripts/pytest.exe").exists():
            pytest_bin = str(repo_path / ".venv/Scripts/pytest.exe")
        elif (repo_path / ".venv/bin/pytest").exists():
            pytest_bin = str(repo_path / ".venv/bin/pytest")

        test_cmd = None
        if (repo_path / "tests").exists() or any(repo_path.glob("test_*.py")):
            if shutil.which(pytest_bin) or shutil.which("pytest", path=env.get("PATH")):
                test_cmd = pytest_bin
            else:
                test_cmd = "python -m unittest discover"

        gates = [
            ("test", test_cmd, True),
            ("lint", "ruff check ." if shutil.which("ruff", path=env.get("PATH")) else None, False),
        ]

        for i, (gate_name, cmd, is_required) in enumerate(gates):
            if not cmd:
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout=f"{gate_name} command not detected in environment",
                    )
                )
                continue

            if progress_callback:
                progress_callback(f"↳ [Gate {i+1}/{len(gates)}] Executing {gate_name} ('{cmd}')...")

            start_t = time.time()
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    env=env,
                )
                duration = time.time() - start_t
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    status = GateStatus.REQUIRED_PASSED if is_required else GateStatus.OPTIONAL_PASSED
                    if progress_callback:
                        progress_callback(f"  ✔ Gate '{gate_name}' passed ({duration:.1f}s)")
                else:
                    status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                    if progress_callback:
                        progress_callback(f"  ✖ Gate '{gate_name}' failed with exit code {proc.returncode} ({duration:.1f}s)")

                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=proc.returncode,
                        stdout=stdout,
                        stderr=stderr,
                        duration_seconds=duration,
                    )
                )
            except subprocess.TimeoutExpired:
                duration = time.time() - start_t
                if progress_callback:
                    progress_callback(f"  ⚠ Gate '{gate_name}' timed out after {duration:.0f}s")
                timeout_note = (
                    f"Python {gate_name} timed out after {duration:.0f}s. "
                    "Test result is UNCERTAIN — run with --skip-tests / --allow-failed-gates to proceed."
                )
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=GateStatus.REQUIRED_TIMEOUT,
                        exit_code=124,
                        stdout=timeout_note,
                        duration_seconds=duration,
                    )
                )
            except Exception as e:
                duration = time.time() - start_t
                status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=1,
                        stderr=str(e),
                        duration_seconds=duration,
                    )
                )

        return GateSummary(results=results)

    def get_declared_dependencies(self, repo_path: Path) -> dict[str, str]:
        deps: dict[str, str] = {}
        pyproject_file = repo_path / "pyproject.toml"
        if pyproject_file.exists():
            try:
                text = pyproject_file.read_text(encoding="utf-8")
                matches = re.findall(r'["\']([a-zA-Z0-9_\-]+)\s*([~>=<][=~><0-9\.\*]+)?["\']', text)
                for pkg, ver_spec in matches:
                    if pkg.lower() not in ("python", "project", "tool", "dependencies"):
                        deps[pkg] = ver_spec.lstrip("~>=<").strip() if ver_spec else "1.0.0"
            except Exception:
                pass
        req_file = repo_path / "requirements.txt"
        if req_file.exists():
            try:
                for line in req_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        match = re.match(r"^([a-zA-Z0-9_\-]+)\s*([~>=<][=~><0-9\.\*]+)?", line)
                        if match:
                            pkg = match.group(1)
                            ver = match.group(2) or "1.0.0"
                            deps[pkg] = ver.lstrip("~>=<").strip()
            except Exception:
                pass
        return deps

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply modern Python syntax upgrades and code formatting."""
        if not modernize_flags:
            return []

        applied = []
        normalized = [f.strip().lower() for f in modernize_flags]
        env = get_python_execution_env(repo_path)

        def _run_format(cmd: list[str], success_msg: str, timeout_secs: int = 60) -> str:
            cmd_display = " ".join(cmd)
            try:
                res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout_secs, env=env)
                if res.returncode == 0:
                    return success_msg
                else:
                    return f"{cmd[0]} returned exit code {res.returncode}"
            except subprocess.TimeoutExpired:
                logger.warning("'%s' timed out after %ds; skipping non-critical modernization", cmd_display, timeout_secs)
                return f"Skipped '{cmd_display}' (timed out after {timeout_secs}s; upgrade preserved)"
            except Exception as e:
                logger.warning("'%s' failed: %s; skipping non-critical modernization", cmd_display, e)
                return f"Skipped '{cmd_display}' ({e})"

        # 1. Modern Syntax Upgrades via Ruff / pyupgrade
        if any(f in ("syntax", "upgrade", "ruff", "all") for f in normalized):
            if shutil.which("ruff", path=env.get("PATH")):
                msg = _run_format(
                    ["ruff", "check", "--select", "UP", "--fix", "."],
                    "Applied modern Python syntax upgrades via Ruff (UP rules: union types, built-in generics)",
                    60,
                )
                applied.append(msg)
            elif shutil.which("pyupgrade", path=env.get("PATH")):
                py_files = [str(p) for p in safe_rglob(repo_path, "*.py")]
                if py_files:
                    msg = _run_format(
                        ["pyupgrade", "--py311-plus", "--exit-zero-even-if-changed", *py_files],
                        "Upgraded Python syntax to Python 3.11+ via pyupgrade",
                        60,
                    )
                    applied.append(msg)

        # 2. Modern Code Formatting
        if any(f in ("format", "black", "all") for f in normalized):
            if shutil.which("ruff", path=env.get("PATH")):
                msg = _run_format(["ruff", "format", "."], "Formatted code using modern Ruff formatter", 60)
                applied.append(msg)
            elif shutil.which("black", path=env.get("PATH")):
                msg = _run_format(["black", "."], "Formatted code using Black formatter", 60)
                applied.append(msg)

        return applied


