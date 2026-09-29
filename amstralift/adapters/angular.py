"""Angular ecosystem adapter.

Handles Angular version discovery via npm registry, peer dependency resolution,
dependency tiering, manifest/lockfile updates, and build/test gates.
Enforces Angular LTS governance policy when configured.
"""

import json
import re
import shutil
import subprocess
import sys
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


ANGULAR_TS_MATRIX = {
    12: 4,
    13: 4,
    14: 4,
    15: 4,
    16: 5,
    17: 5,
    18: 5,
    19: 5,
    20: 5,
    21: 5,
    22: 5,
}

ANGULAR_TS_RECOMMENDED: dict[int, str] = {
    12: "^4.2.4",
    13: "^4.4.4",
    14: "^4.7.2",
    15: "^4.9.5",
    16: "^5.1.3",
    17: "^5.3.2",
    18: "^5.4.5",
    19: "^5.6.3",
    20: "^5.7.2",
}


def extract_major_version(ver_str: str) -> int | None:
    """Extract integer major version from a version string."""
    clean = ver_str.strip().lstrip("^~>=<")
    match = re.match(r"^(\d+)", clean)
    if match:
        return int(match.group(1))
    return None


class AngularAdapter(BaseAdapter):
    """Adapter for Angular repositories using npm and Angular CLI."""

    def __init__(
        self,
        registry_url: str = NPM_REGISTRY_BASE,
        timeout_seconds: float = 10.0,
        lts_config: AngularLTSConfig | None = None,
        incremental: bool = False,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.lts_config = lts_config
        self.incremental = incremental

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
                        return None
                    return data.get("dist-tags", {}).get("latest")
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Discover upgradable Angular and related dependencies applying LTS or incremental governance."""
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        candidates: list[DependencyChange] = []

        direct_deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})

        # Evaluate Angular LTS policy or Incremental (+1 major)
        target_major = None
        policy_reason = ""
        if self.lts_config:
            policy_decision = AngularLTSGovernance.evaluate(self.lts_config)
            target_major = policy_decision.target_major if not policy_decision.use_latest_fallback else None
            policy_reason = f"LTS Policy: {policy_decision.reason}"

        # Detect current Angular core major from package.json
        core_ver = direct_deps.get("@angular/core") or dev_deps.get("@angular/core") or direct_deps.get("@angular/common")
        cur_angular_major = extract_major_version(core_ver) if core_ver else None

        if target_major is None and self.incremental and cur_angular_major is not None:
            target_major = cur_angular_major + 1
            policy_reason = f"Incremental upgrade (+1 major): v{cur_angular_major} -> v{target_major}"
        elif not policy_reason:
            policy_reason = "Ecosystem Upgrade"

        def get_major_constraint(pkg_name: str) -> int | None:
            if target_major is None:
                return None
            if (
                pkg_name.startswith("@angular/")
                or pkg_name.startswith("@angular-devkit/")
                or pkg_name.startswith("@ngrx/")
                or pkg_name.startswith("@angular-extensions/")
            ):
                return target_major
            if pkg_name == "typescript":
                return ANGULAR_TS_MATRIX.get(target_major, 5)
            if pkg_name == "rxjs":
                return 7 if target_major >= 13 else 6
            if pkg_name == "zone.js":
                return 0
            return None

        tracked_direct_prefixes = ("@angular/", "@ngrx/", "@angular-extensions/")
        for pkg, cur_ver in direct_deps.items():
            if any(pkg.startswith(p) for p in tracked_direct_prefixes) or pkg in ("rxjs", "zone.js", "tslib"):
                clean_cur = cur_ver.lstrip("^~>=<")
                major_constraint = get_major_constraint(pkg)
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
                            rationale=(f"Upgrade {pkg} to {latest}. {policy_reason}"),
                        )
                    )

        for pkg, cur_ver in dev_deps.items():
            if (
                pkg.startswith("@angular-devkit/")
                or pkg.startswith("@angular/")
                or pkg.startswith("@angular-eslint/")
                or pkg in ("typescript", "@angular/cli")
            ):
                clean_cur = cur_ver.lstrip("^~>=<")
                if pkg == "typescript" and target_major and target_major in ANGULAR_TS_RECOMMENDED:
                    rec_ts = ANGULAR_TS_RECOMMENDED[target_major]
                    if clean_cur != rec_ts.lstrip("^~>=<"):
                        tier = classify_angular_tier(pkg)
                        candidates.append(
                            DependencyChange(
                                package_name=pkg,
                                from_version=cur_ver,
                                to_version=rec_ts,
                                change_type="dev",
                                tier=tier,
                                rationale=f"Align TypeScript to {rec_ts} for Angular {target_major} compatibility. {policy_reason}",
                            )
                        )
                    continue

                major_constraint = get_major_constraint(pkg)
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
                            rationale=(f"Upgrade dev tool {pkg} to {latest}. {policy_reason}"),
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

        # Update package-lock.json accurately using npm_lockfile
        from amstralift.adapters.npm_lockfile import update_npm_lockfile

        gate_env = get_node_execution_env()
        update_npm_lockfile(repo_path, changes, env=gate_env)

        # ── Docker: update FROM / image: tags in Dockerfiles & docker-compose ──
        # Determine target Angular major from changes if possible
        angular_core = next(
            (c for c in changes if c.package_name == "@angular/core"),
            None,
        )
        if angular_core:
            target_ver = angular_core.to_version.lstrip("^~>=<").split(".")[0]
            from amstralift.adapters.docker_updater import DockerfileUpdater
            DockerfileUpdater.update(repo_path, ecosystem="angular", target_version=target_ver)

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply modern Angular schematics (control-flow, standalone)."""
        if not modernize_flags:
            return []

        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        core_ver = deps.get("@angular/core", "")
        major = extract_major_version(core_ver) if core_ver else None

        applied = []
        gate_env = get_node_execution_env()
        has_npx = shutil.which("npx", path=gate_env.get("PATH")) is not None

        normalized = [f.strip().lower() for f in modernize_flags]

        # 1. Control-Flow (*ngIf -> @if, *ngFor -> @for)
        if any(f in ("control-flow", "controlflow", "all") for f in normalized):
            if major and major >= 17:
                cf_migrated = False
                if has_npx:
                    cmd = ["npx", "@angular/cli", "generate", "@angular/core:control-flow", "--interactive=false"]
                    try:
                        res = subprocess.run(
                            cmd,
                            cwd=repo_path,
                            env=gate_env,
                            capture_output=True,
                            text=True,
                            timeout=180,
                            shell=sys.platform == "win32",
                        )
                        if res.returncode == 0:
                            applied.append("Migrated templates to modern Angular control flow (@if, @for, @switch)")
                            cf_migrated = True
                    except Exception:
                        pass

                if not cf_migrated:
                    from amstralift.adapters.angular_control_flow import migrate_repository_control_flow

                    stats = migrate_repository_control_flow(repo_path)
                    total_directives = sum(stats.values())
                    if total_directives > 0:
                        applied.append(
                            f"Migrated {len(stats)} template(s) ({total_directives} directives) to modern Angular control flow (@if, @for)"
                        )
                    else:
                        applied.append("Control-flow migration completed (no legacy *ngIf/*ngFor directives found)")
            else:
                applied.append(f"Skipped control-flow migration: requires Angular 17+ (current is v{major or 'unknown'})")

        # 2. Standalone Migration (NgModule -> Standalone components)
        if any(f in ("standalone", "all") for f in normalized):
            if major and major >= 15:
                if has_npx:
                    cmd = [
                        "npx",
                        "@angular/cli",
                        "generate",
                        "@angular/core:standalone",
                        "--mode=convert-to-standalone",
                        "--interactive=false",
                        "--defaults",
                    ]
                    try:
                        res = subprocess.run(
                            cmd,
                            cwd=repo_path,
                            env=gate_env,
                            capture_output=True,
                            text=True,
                            timeout=180,
                            shell=sys.platform == "win32",
                        )
                        if res.returncode == 0:
                            applied.append("Converted components and directives to Angular Standalone architecture")
                        else:
                            raw_err = (res.stderr or res.stdout or "").strip()
                            if "does not support schematics" in raw_err:
                                applied.append(
                                    "Skipped standalone migration: local node_modules has older Angular version. "
                                    "Run 'npm install' on upgraded branch first."
                                )
                            elif "TypeScript" in raw_err:
                                applied.append(f"Standalone migration TypeScript mismatch: {raw_err[:80]}")
                            else:
                                err_hint = raw_err[:80]
                                applied.append(f"Standalone migration returned exit code {res.returncode}: {err_hint}")
                    except Exception as e:
                        applied.append(f"Standalone migration error: {str(e)[:80]}")
                else:
                    applied.append("Skipped standalone migration: npx CLI not found in environment")
            else:
                applied.append(f"Skipped standalone migration: requires Angular 15+ (current is v{major or 'unknown'})")

        return applied

    def run_build_and_tests(self, repo_path: Path, timeout_seconds: float = 300.0) -> GateSummary:
        """Execute build and test gates declared in package.json.

        Handles Angular-specific test runner quirks:
        - ng test (Karma) runs in watch mode by default → inject --watch=false --no-progress
        - Karma requires a browser → inject --browsers=ChromeHeadless in CI/headless environments
        - Jest runs once and exits → no special flags needed
        - Timeout is classified as REQUIRED_TIMEOUT (uncertain), NOT REQUIRED_FAILED
        """
        pkg_file = repo_path / "package.json"
        results: list[GateResult] = []

        if not pkg_file.exists():
            return GateSummary(results=results)

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})

        gate_env = get_node_execution_env()
        existing_opts = gate_env.get("NODE_OPTIONS", "")
        if "--openssl-legacy-provider" not in existing_opts:
            gate_env["NODE_OPTIONS"] = (existing_opts + " --openssl-legacy-provider").strip()

        has_npm = shutil.which("npm", path=gate_env.get("PATH")) is not None

        # ── Resolve test command with watch-mode and headless fixes ──────────
        raw_test_script = scripts.get("test", "")
        resolved_test_cmd = self._resolve_angular_test_command(
            raw_test_script, repo_path, has_npm
        )

        # Define gates in priority order
        gates = [
            ("test", resolved_test_cmd, True),
            ("build", scripts.get("build"), True),
            ("lint", scripts.get("lint"), False),
        ]

        # Remove any stale ngcc lock files before running gates
        stale_lock = repo_path / "node_modules" / "@angular" / "compiler-cli" / "ngcc" / "__ngcc_lock_file__"
        if stale_lock.exists():
            try:
                stale_lock.unlink(missing_ok=True)
            except Exception:
                pass

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
            if gate_name == "test":
                cmd = script_cmd
            else:
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
                    "This usually means 'ng test' is running in watch mode or waiting for a browser. "
                    "AMstraLift injected --watch=false --browsers=ChromeHeadless but the environment may not "
                    "support Chrome. Test result is UNCERTAIN — not confirmed broken. "
                    "Run with --skip-tests / --allow-failed-gates to proceed, or install chromium/google-chrome."
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

    def _resolve_angular_test_command(
        self,
        raw_test_script: str,
        repo_path: Path,
        has_npm: bool,
    ) -> str | None:
        """
        Resolve the test command for Angular, injecting headless/no-watch flags as needed.

        Detection priority:
        1. Jest (runs once, exits cleanly) — no flags needed
        2. Vitest — inject --run flag
        3. ng test / karma — inject --watch=false --no-progress --browsers=ChromeHeadless
        4. Unknown script — fallback to npm run test
        """
        if not raw_test_script:
            return None

        script_lower = raw_test_script.lower()

        # ── Jest: runs once and exits — no special treatment needed ──────────
        if "jest" in script_lower:
            return "npm run test" if has_npm else raw_test_script

        if "vitest" in script_lower:
            if "--run" not in script_lower:
                return "npm test -- --run" if has_npm else f"{raw_test_script} --run"
            return "npm run test" if has_npm else raw_test_script

        is_ng_test = "ng test" in script_lower or "ng t " in script_lower or script_lower.startswith("ng t")
        is_karma = "karma" in script_lower

        if is_ng_test or is_karma:
            # Check all karma configs in the repository (excluding node_modules)
            karma_confs = [
                p for p in repo_path.rglob("karma*.conf*.js")
                if "node_modules" not in p.parts
            ]
            already_headless = False
            for kc in karma_confs:
                try:
                    karma_text = kc.read_text(encoding="utf-8", errors="replace").lower()
                    if "chromeheadless" in karma_text or "chromiumheadless" in karma_text:
                        already_headless = True
                        break
                except Exception:
                    pass

            flags: list[str] = []
            if "--watch=false" not in script_lower and "--no-watch" not in script_lower:
                flags.append("--watch=false")
            if "--no-progress" not in script_lower and "--progress=false" not in script_lower:
                flags.append("--no-progress")
            if not already_headless and "--browsers" not in script_lower:
                flags.append("--browsers=ChromeHeadless")

            flag_str = " ".join(flags)
            if has_npm:
                return f"npm test -- {flag_str}" if flag_str else "npm run test"
            else:
                return f"{raw_test_script} {flag_str}" if flag_str else raw_test_script

        return "npm run test" if has_npm else raw_test_script


    def get_declared_dependencies(self, repo_path: Path) -> dict[str, str]:
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return {}
        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            return {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception:
            return {}

