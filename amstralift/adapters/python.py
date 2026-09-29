"""Python ecosystem adapter (Section 2, 6, 8, 9).

Handles Python package discovery via PyPI JSON API, pyproject.toml / requirements.txt
updates, runtime governance evaluation, dependency tiering, and test execution.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from amstralift.adapters.base import BaseAdapter
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
        """Fetch latest version from PyPI JSON API."""
        url = f"{self.pypi_base}/{package_name}/json"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("info", {}).get("version")
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

        for gate_name, cmd, is_required in gates:
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
                )
                duration = time.time() - start_t
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    status = GateStatus.REQUIRED_PASSED if is_required else GateStatus.OPTIONAL_PASSED
                else:
                    status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED

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

