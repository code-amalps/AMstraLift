"""React ecosystem adapter (Section 2, 8, 9).

Handles React version discovery via npm registry, package.json updates,
dependency tiering, and build/test gates.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from amstralift.adapters.base import BaseAdapter, get_node_execution_env
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
)

NPM_REGISTRY_BASE = "https://registry.npmjs.org"

TIER_3_PATTERNS = [
    re.compile(r".*(auth|security|payment|stripe|msal|crypto|jwt).*", re.IGNORECASE),
    re.compile(r"^(@auth0/auth0-react|@azure/msal-react|@stripe/.*)$", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(
        r"^(react|react-dom|react-router|react-router-dom|redux|@reduxjs/toolkit|zustand|mobx|jotai|recoil)$",
        re.IGNORECASE,
    ),
    re.compile(r".*(routing|state|store|navigation).*", re.IGNORECASE),
]


def classify_react_tier(package_name: str) -> DependencyTier:
    """Classify React dependency package into Tier 1, 2, or 3."""
    for pattern in TIER_3_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_3_CRITICAL

    for pattern in TIER_2_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR

    return DependencyTier.TIER_1_SAFE


class ReactAdapter(BaseAdapter):
    """Adapter for React repositories using npm/yarn/pnpm."""

    def __init__(
        self,
        registry_url: str = NPM_REGISTRY_BASE,
        timeout_seconds: float = 10.0,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "react"

    def detect(self, repo_path: Path) -> bool:
        """Detect React project (must not be Angular)."""
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return False

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            # Distinct from Angular
            if "@angular/core" in deps or (repo_path / "angular.json").exists():
                return False
            return "react" in deps or "react-dom" in deps
        except Exception:
            return False

    def fetch_latest_version(self, package_name: str) -> str | None:
        """Fetch latest version tag from npm registry."""
        url = f"{self.registry_url}/{package_name}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("dist-tags", {}).get("latest")
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Discover candidate package upgrades in package.json."""
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        candidates: list[DependencyChange] = []

        direct_deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})

        for pkg, cur_ver in direct_deps.items():
            if pkg in ("react", "react-dom") or pkg.startswith("react-") or pkg.startswith("@reduxjs/"):
                clean_cur = cur_ver.lstrip("^~>=<")
                latest = self.fetch_latest_version(pkg)
                if latest and latest != clean_cur:
                    tier = classify_react_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="direct",
                            tier=tier,
                            rationale=f"Upgrade React dependency {pkg} to latest {latest}",
                        )
                    )

        for pkg, cur_ver in dev_deps.items():
            if pkg.startswith("@types/react") or pkg.startswith("eslint-plugin-react"):
                clean_cur = cur_ver.lstrip("^~>=<")
                latest = self.fetch_latest_version(pkg)
                if latest and latest != clean_cur:
                    tier = classify_react_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="dev",
                            tier=tier,
                            rationale=f"Upgrade React dev tool {pkg} to latest {latest}",
                        )
                    )

        return candidates

    def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
        """Apply dependency updates to package.json and lockfile."""
        pkg_file = repo_path / "package.json"
        data = json.loads(pkg_file.read_text(encoding="utf-8"))

        for change in changes:
            if change.change_type == "direct" and "dependencies" in data:
                if change.package_name in data["dependencies"]:
                    data["dependencies"][change.package_name] = change.to_version
            elif change.change_type == "dev" and "devDependencies" in data:
                if change.package_name in data["devDependencies"]:
                    data["devDependencies"][change.package_name] = change.to_version

        pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        lock_file = repo_path / "package-lock.json"
        if lock_file.exists():
            try:
                lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
                for change in changes:
                    clean_ver = change.to_version.lstrip("^~>=<")
                    # 1. Update packages["node_modules/<package_name>"]
                    if "packages" in lock_data and f"node_modules/{change.package_name}" in lock_data["packages"]:
                        lock_data["packages"][f"node_modules/{change.package_name}"]["version"] = clean_ver
                    # 2. Update root package manifest packages[""]
                    if "packages" in lock_data and "" in lock_data["packages"]:
                        root_pkg = lock_data["packages"][""]
                        if change.change_type == "direct" and "dependencies" in root_pkg:
                            if change.package_name in root_pkg["dependencies"]:
                                root_pkg["dependencies"][change.package_name] = change.to_version
                        elif change.change_type == "dev" and "devDependencies" in root_pkg:
                            if change.package_name in root_pkg["devDependencies"]:
                                root_pkg["devDependencies"][change.package_name] = change.to_version
                    # 3. Update legacy v1 dependencies section if present
                    if "dependencies" in lock_data and change.package_name in lock_data["dependencies"]:
                        lock_data["dependencies"][change.package_name]["version"] = clean_ver
                lock_file.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

        # ── Docker: update FROM / image: tags in Dockerfiles & docker-compose ──
        react_change = next(
            (c for c in changes if c.package_name in ("react", "react-dom")),
            None,
        )
        if react_change:
            target_ver = react_change.to_version.lstrip("^~>=<").split(".")[0]
            from amstralift.adapters.docker_updater import DockerfileUpdater
            DockerfileUpdater.update(repo_path, ecosystem="react", target_version=target_ver)

    def run_build_and_tests(self, repo_path: Path, timeout_seconds: float = 300.0) -> GateSummary:
        """Execute build and test gates declared in package.json."""
        pkg_file = repo_path / "package.json"
        results: list[GateResult] = []

        if not pkg_file.exists():
            return GateSummary(results=results)

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})

        gate_env = get_node_execution_env()
        # Set CI=true so test runners like Jest / react-scripts run in non-interactive single-pass mode
        gate_env["CI"] = "true"
        has_npm = shutil.which("npm", path=gate_env.get("PATH")) is not None

        gates = [
            ("test", scripts.get("test"), True),
            ("build", scripts.get("build"), True),
            ("lint", scripts.get("lint"), False),
        ]

        for gate_name, script_cmd, is_required in gates:
            if not script_cmd:
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout=f"Script '{gate_name}' not defined in package.json",
                    )
                )
                continue

            if not has_npm and (
                script_cmd.startswith("npm ")
                or script_cmd.startswith("npx ")
                or "npm run" in script_cmd
            ):
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout="npm CLI not detected in host environment",
                    )
                )
                continue

            start_t = time.time()
            cmd = f"npm run {gate_name}" if has_npm else script_cmd

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
                    env=gate_env,
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
                    f"Test runner timed out after {duration:.0f}s. "
                    "This usually means the test runner is waiting for interactive input or browser connection. "
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
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return {}
        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            return {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception:
            return {}

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply modern React transforms (e.g. React 17+ JSX Transform, lint auto-fixes)."""
        if not modernize_flags:
            return []

        applied = []
        normalized = [f.strip().lower() for f in modernize_flags]

        # 1. Modern JSX Transform (React 17+)
        if any(f in ("jsx", "jsx-transform", "new-jsx", "all") for f in normalized):
            tsconfig = repo_path / "tsconfig.json"
            if tsconfig.exists():
                try:
                    text = tsconfig.read_text(encoding="utf-8")
                    if '"jsx": "react"' in text:
                        text = text.replace('"jsx": "react"', '"jsx": "react-jsx"')
                        tsconfig.write_text(text, encoding="utf-8")
                        applied.append("Updated tsconfig.json to use modern React 17+ JSX transform ('react-jsx')")
                except Exception:
                    pass

        # 2. Automated Lint Fixes
        if any(f in ("lint", "fix", "all") for f in normalized):
            pkg_file = repo_path / "package.json"
            if pkg_file.exists():
                try:
                    data = json.loads(pkg_file.read_text(encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if "lint" in scripts and shutil.which("npm"):
                        gate_env = get_node_execution_env()
                        res = subprocess.run(["npm", "run", "lint", "--", "--fix"], cwd=repo_path, env=gate_env, capture_output=True, text=True, timeout=120)
                        if res.returncode == 0:
                            applied.append("Applied automated ESLint code fixes via 'npm run lint -- --fix'")
                except Exception:
                    pass

        return applied


