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

from amstralift.adapters.angular import extract_major_version, get_max_existing_major
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
        incremental: bool = False,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.incremental = incremental

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

    def fetch_latest_version(self, package_name: str, target_major: int | None = None) -> str | None:
        """Fetch latest stable GA version tag from npm registry (never pre-release).

        If target_major is provided, returns latest stable version matching that major.
        """
        url = f"{self.registry_url}/{package_name}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    if target_major is not None:
                        matching = []
                        for version in data.get("versions", {}):
                            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
                            if match and int(match.group(1)) == target_major:
                                matching.append((tuple(map(int, match.groups())), version))
                        if matching:
                            return max(matching, key=lambda item: item[0])[1]
                        return None

                    latest = data.get("dist-tags", {}).get("latest")
                    if latest and not any(pre in latest.lower() for pre in ("-rc", "-canary", "-next", "-beta", "-alpha", "-dev", "-preview")):
                        return latest

                    stable_matching = []
                    for version in data.get("versions", {}):
                        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
                        if match:
                            stable_matching.append((tuple(map(int, match.groups())), version))
                    if stable_matching:
                        return max(stable_matching, key=lambda item: item[0])[1]
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

        # Determine current React major and latest GA React major
        react_core_ver = direct_deps.get("react") or dev_deps.get("react") or direct_deps.get("react-dom")
        cur_react_major = extract_major_version(react_core_ver) if react_core_ver else None

        target_react_major = None
        if cur_react_major is not None:
            latest_react = self.fetch_latest_version("react")
            latest_ga_major = extract_major_version(latest_react) if latest_react else None
            if self.incremental:
                if latest_ga_major is not None and cur_react_major >= latest_ga_major:
                    target_react_major = cur_react_major
                elif latest_ga_major is not None:
                    target_react_major = min(cur_react_major + 1, latest_ga_major)
                else:
                    target_react_major = cur_react_major + 1
            else:
                target_react_major = latest_ga_major

        def get_react_target_major(pkg_name: str) -> int | None:
            if target_react_major is None:
                return None
            if pkg_name in ("react", "react-dom", "react-is", "react-test-renderer") or pkg_name.startswith("@types/react"):
                return target_react_major
            return None

        for pkg, cur_ver in direct_deps.items():
            if pkg in ("react", "react-dom") or pkg.startswith("react-") or pkg.startswith("@reduxjs/"):
                clean_cur = cur_ver.lstrip("^~>=<")
                major_constraint = get_react_target_major(pkg)
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)
                if not latest and major_constraint is not None and not (pkg in ("react", "react-dom") or pkg.startswith("@types/react")):
                    latest = self.fetch_latest_version(pkg, target_major=None)
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
                major_constraint = get_react_target_major(pkg)
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)
                if not latest and major_constraint is not None and not (pkg in ("react", "react-dom") or pkg.startswith("@types/react")):
                    latest = self.fetch_latest_version(pkg, target_major=None)
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

        # Clean up problematic native binary overrides if present
        if "overrides" in data and isinstance(data["overrides"], dict):
            for bad_key in list(data["overrides"].keys()):
                if bad_key == "esbuild" or bad_key.startswith(("@esbuild/", "@swc/", "@rollup/")):
                    del data["overrides"][bad_key]
            for parent_k, parent_v in list(data["overrides"].items()):
                if isinstance(parent_v, dict):
                    for bad_k in list(parent_v.keys()):
                        if bad_k == "esbuild" or bad_k.startswith(("@esbuild/", "@swc/", "@rollup/")):
                            del parent_v[bad_k]
                    if not parent_v:
                        del data["overrides"][parent_k]
            if not data["overrides"]:
                del data["overrides"]

        for change in changes:
            if change.change_type == "transitive":
                # Native binary packages (like esbuild) have strict platform-binary equality checks
                # and are managed by the framework/build toolchain; overriding them breaks install scripts.
                if change.package_name == "esbuild" or change.package_name.startswith(("@esbuild/", "@swc/", "@rollup/")):
                    continue

                clean_target = change.to_version if change.to_version.startswith(("^", "~")) else f"^{change.to_version}"
                target_m = extract_major_version(clean_target)
                direct_pkgs = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                max_m = get_max_existing_major(repo_path, change.package_name, direct_pkgs)

                # Never allow an older major patch to globally downgrade a higher major used elsewhere in the project
                parent_scope = change.parent_package or (change.introduced_by[0] if change.introduced_by else None)
                if max_m is not None and target_m is not None and target_m < max_m:
                    if parent_scope:
                        data.setdefault("overrides", {}).setdefault(parent_scope, {})[change.package_name] = clean_target
                elif change.package_name in direct_pkgs and parent_scope:
                    data.setdefault("overrides", {}).setdefault(parent_scope, {})[change.package_name] = clean_target
                else:
                    data.setdefault("overrides", {})[change.package_name] = clean_target
            elif "dependencies" in data and change.package_name in data["dependencies"]:
                data["dependencies"][change.package_name] = change.to_version
            elif "devDependencies" in data and change.package_name in data["devDependencies"]:
                data["devDependencies"][change.package_name] = change.to_version

        pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        # Update package-lock.json accurately using npm_lockfile
        from amstralift.adapters.npm_lockfile import update_npm_lockfile

        gate_env = get_node_execution_env()
        update_npm_lockfile(repo_path, changes, env=gate_env)

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


