"""Angular ecosystem adapter.

Handles Angular version discovery via npm registry, peer dependency resolution,
dependency tiering, manifest/lockfile updates, and build/test gates.
Enforces Angular LTS governance policy when configured.
"""

import json
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
from amstralift.governance.angular_lts import AngularLTSConfig, AngularLTSGovernance

NPM_REGISTRY_BASE = "https://registry.npmjs.org"

# Tiering classifications for Angular packages (Section 8)
TIER_3_PATTERNS = [
    re.compile(r".*auth.*", re.IGNORECASE),
    re.compile(r".*security.*", re.IGNORECASE),
    re.compile(r".*payment.*", re.IGNORECASE),
    re.compile(r".*stripe.*", re.IGNORECASE),
    re.compile(r".*msal.*", re.IGNORECASE),
    re.compile(r".*crypto.*", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(r"^@angular/(router|forms|common|core)$"),
    re.compile(r"^@ngrx/.*"),
    re.compile(r".*(state|routing|store|interceptor).*", re.IGNORECASE),
]


def classify_angular_tier(package_name: str) -> DependencyTier:
    """Classify dependency package into Tier 1, 2, or 3."""
    for pattern in TIER_3_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_3_CRITICAL

    for pattern in TIER_2_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR

    return DependencyTier.TIER_1_SAFE


class AngularAdapter(BaseAdapter):
    """Adapter for Angular repositories using npm and Angular CLI."""

    def __init__(
        self,
        registry_url: str = NPM_REGISTRY_BASE,
        timeout_seconds: float = 10.0,
        lts_config: AngularLTSConfig | None = None,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.lts_config = lts_config

    @property
    def name(self) -> str:
        return "angular"

    def detect(self, repo_path: Path) -> bool:
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return False

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            return "@angular/core" in deps or (repo_path / "angular.json").exists()
        except Exception:
            return False

    def fetch_latest_version(self, package_name: str, target_major: int | None = None) -> str | None:
        """Fetch version from npm registry.

        If target_major is provided, returns latest version matching that major,
        otherwise returns dist-tags.latest.
        """
        url = f"{self.registry_url}/{package_name}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    if target_major is not None:
                        versions = list(data.get("versions", {}).keys())
                        matching = [v for v in versions if v.startswith(f"{target_major}.")]
                        if matching:
                            return matching[-1]
                    return data.get("dist-tags", {}).get("latest")
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Discover upgradable Angular and related dependencies applying LTS governance."""
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        candidates: list[DependencyChange] = []

        direct_deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})

        # Evaluate Angular LTS policy
        policy_decision = AngularLTSGovernance.evaluate(self.lts_config)
        target_major = policy_decision.target_major if not policy_decision.use_latest_fallback else None

        for pkg, cur_ver in direct_deps.items():
            if pkg.startswith("@angular/") or pkg in ("rxjs", "zone.js", "tslib"):
                clean_cur = cur_ver.lstrip("^~>=<")
                # Angular core packages follow LTS major if policy is active
                major_constraint = target_major if pkg.startswith("@angular/") else None
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)

                if latest and latest != clean_cur:
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="direct",
                            tier=tier,
                            rationale=(f"Upgrade {pkg} to {latest}. LTS Policy: {policy_decision.reason}"),
                        )
                    )

        for pkg, cur_ver in dev_deps.items():
            if pkg.startswith("@angular-devkit/") or pkg in ("typescript", "@angular/cli"):
                clean_cur = cur_ver.lstrip("^~>=<")
                major_constraint = target_major if pkg.startswith("@angular") else None
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)

                if latest and latest != clean_cur:
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="dev",
                            tier=tier,
                            rationale=(f"Upgrade dev tool {pkg} to {latest}. LTS Policy: {policy_decision.reason}"),
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

        # Update package-lock.json if it exists to simulate lockfile update
        lock_file = repo_path / "package-lock.json"
        if lock_file.exists():
            try:
                lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
                for change in changes:
                    if "packages" in lock_data and f"node_modules/{change.package_name}" in lock_data["packages"]:
                        lock_data["packages"][f"node_modules/{change.package_name}"]["version"] = (
                            change.to_version.lstrip("^~")
                        )
                lock_file.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

    def run_build_and_tests(self, repo_path: Path) -> GateSummary:
        """Execute build and test gates declared in package.json."""
        pkg_file = repo_path / "package.json"
        results: list[GateResult] = []

        if not pkg_file.exists():
            return GateSummary(results=results)

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})

        has_npm = shutil.which("npm") is not None

        # Define gates in priority order
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
                or script_cmd.startswith("ng ")
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
                    timeout=300,
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
