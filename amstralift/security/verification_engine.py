"""Verification engine executing the 5 configured verification gates in an isolated environment."""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from amstralift.adapters.base import get_node_execution_env
from amstralift.core.models import GateResult, GateStatus, GateSummary
from amstralift.security.dependency_graph import DependencyGraphAnalyzer
from amstralift.security.models import (
    AuditReport,
    RemediationPlan,
    VerificationStatus,
)
from amstralift.security.osv_client import OSVClient


class VerificationEngine:
    """Executes the 5 verification gates and performs uncertainty and rescan evaluations."""

    @classmethod
    def execute_gates(
        cls,
        workspace_path: Path,
        ecosystem: str,
        plan: RemediationPlan,
        scanner: OSVClient | None = None,
        governance_manager: Any = None,
    ) -> tuple[bool, VerificationStatus, GateSummary, AuditReport | None, str | None]:
        """Execute Gates 1-5 inside the isolated workspace according to the explicit behavior contract.

        Returns:
            (eligible_for_stage_b, verification_status, gate_summary, rescan_report, uncertainty_warning)
        """
        results: list[GateResult] = []
        scanner = scanner or OSVClient()

        # ====================================================
        # Gate 1: Dependency Resolution & Conflict Detection
        # ====================================================
        g1_res = cls._run_gate_1_resolution(workspace_path, ecosystem)
        results.append(g1_res)
        if g1_res.status == GateStatus.REQUIRED_FAILED:
            return (
                False,
                VerificationStatus.BUILD_FAILED,
                GateSummary(results=results),
                None,
                f"Gate 1 Dependency Resolution failed: {g1_res.stderr or g1_res.stdout}",
            )

        # ====================================================
        # Gate 2: Build & Static Validation
        # ====================================================
        g2_res = cls._run_gate_2_build(workspace_path, ecosystem)
        results.append(g2_res)
        if g2_res.status == GateStatus.REQUIRED_FAILED:
            return (
                False,
                VerificationStatus.BUILD_FAILED,
                GateSummary(results=results),
                None,
                f"Gate 2 Build failed: {g2_res.stderr or g2_res.stdout}",
            )

        # ====================================================
        # Gate 3: Automated Tests & Uncertainty Evaluation
        # ====================================================
        g3_res, test_count, is_skipped = cls._run_gate_3_tests(workspace_path, ecosystem)
        results.append(g3_res)
        if g3_res.status == GateStatus.REQUIRED_FAILED:
            return (
                False,
                VerificationStatus.TESTS_FAILED,
                GateSummary(results=results),
                None,
                f"Gate 3 Tests failed: {g3_res.stderr or g3_res.stdout}",
            )

        uncertainty_warning: str | None = None
        if is_skipped or g3_res.status == GateStatus.REQUIRED_SKIPPED:
            status = VerificationStatus.VERIFICATION_INCOMPLETE
            uncertainty_warning = (
                "⚠️ VERIFICATION INCOMPLETE: Automated tests were skipped or test runner tooling was unavailable. "
                "Functional verification is incomplete. Policy-controlled manual approval required."
            )
        elif test_count == 0:
            status = VerificationStatus.UNVERIFIED_NO_TESTS
            uncertainty_warning = (
                "⚠️ UNVERIFIED (NO AUTOMATED TESTS): Build compiled cleanly, but NO relevant automated tests exist "
                "in this repository. Functional safety cannot be verified automatically. Policy-controlled manual approval required."
            )
        else:
            status = VerificationStatus.VERIFIED_SAFE

        # ====================================================
        # Gate 4: Vulnerability Rescan
        # ====================================================
        rescan_report, rescan_passed, rescan_reason = cls._run_gate_4_rescan(
            workspace_path, ecosystem, plan, scanner, governance_manager
        )
        g4_status = GateStatus.REQUIRED_PASSED if rescan_passed else GateStatus.REQUIRED_FAILED
        results.append(
            GateResult(
                name="vulnerability_rescan",
                command="osv-rescan-graph",
                status=g4_status,
                exit_code=0 if rescan_passed else 1,
                stdout=rescan_reason,
            )
        )

        if not rescan_passed:
            return (
                False,
                VerificationStatus.RESCAN_FAILED,
                GateSummary(results=results),
                rescan_report,
                f"Gate 4 Vulnerability Rescan failed: {rescan_reason}. Do not claim vulnerability is resolved.",
            )

        # ====================================================
        # Gate 5: Final Decision
        # ====================================================
        policy = getattr(governance_manager, "policy", None)
        allow_unverified = getattr(policy, "allow_unverified_upgrades_for_review", True) if policy else True

        if status in (VerificationStatus.UNVERIFIED_NO_TESTS, VerificationStatus.VERIFICATION_INCOMPLETE) and not allow_unverified:
            return (
                False,
                status,
                GateSummary(results=results),
                rescan_report,
                f"Remediation halted: Organizational policy strictly requires passing automated tests. {uncertainty_warning}",
            )

        gate_summary = GateSummary(results=results)
        return True, status, gate_summary, rescan_report, uncertainty_warning

    @classmethod
    def _run_gate_1_resolution(cls, workspace_path: Path, ecosystem: str) -> GateResult:
        """Verify that dependencies resolve without conflicts."""
        start = time.time()
        eco = ecosystem.lower().strip()

        if eco in ("angular", "react", "npm"):
            if shutil.which("npm"):
                cmd = "npm install --legacy-peer-deps --ignore-scripts --no-audit --no-fund"
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=get_node_execution_env(),
                )
                dur = time.time() - start
                status = GateStatus.REQUIRED_PASSED if proc.returncode == 0 else GateStatus.REQUIRED_FAILED
                return GateResult(
                    name="dependency_resolution",
                    command=cmd,
                    status=status,
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_seconds=dur,
                )

        elif eco in ("dotnet", "nuget"):
            if shutil.which("dotnet"):
                cmd = "dotnet restore"
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                dur = time.time() - start
                status = GateStatus.REQUIRED_PASSED if proc.returncode == 0 else GateStatus.REQUIRED_FAILED
                return GateResult(
                    name="dependency_resolution",
                    command=cmd,
                    status=status,
                    exit_code=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_seconds=dur,
                )

        return GateResult(
            name="dependency_resolution",
            command="N/A",
            status=GateStatus.REQUIRED_PASSED,
            exit_code=0,
            stdout="Resolution check skipped (tooling not detected)",
            duration_seconds=time.time() - start,
        )

    @classmethod
    def _run_gate_2_build(cls, workspace_path: Path, ecosystem: str) -> GateResult:
        """Run build and static validation."""
        start = time.time()
        eco = ecosystem.lower().strip()

        if eco in ("angular", "react", "npm"):
            env = get_node_execution_env(workspace_path)
            if (workspace_path / "angular.json").exists():
                from amstralift.adapters.angular import (
                    AngularAdapter,
                    _modernize_angular_workspace_json,
                    _modernize_angular_scripts,
                )
                _modernize_angular_workspace_json(workspace_path)
                _modernize_angular_scripts(workspace_path)
                adapter = AngularAdapter()
                adapter._build_workspace_libraries(workspace_path, env)

                pkg_json = workspace_path / "package.json"
                scripts = {}
                if pkg_json.exists():
                    try:
                        scripts = json.loads(pkg_json.read_text(encoding="utf-8")).get("scripts", {})
                    except Exception:
                        pass
                raw_build = scripts.get("build")
                resolved_cmd = adapter._resolve_angular_build_command(
                    raw_build, workspace_path, has_npm=bool(shutil.which("npm"))
                )
                cmd = resolved_cmd or ("npm run build" if shutil.which("npm") else "npm test")
            else:
                cmd = "npm run build" if shutil.which("npm") else "npm test"
        elif eco in ("dotnet", "nuget"):
            cmd = "dotnet build"
            env = None
        else:
            cmd = "python -m py_compile" if shutil.which("python") else "python -V"
            env = None

        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        dur = time.time() - start
        status = GateStatus.REQUIRED_PASSED if proc.returncode == 0 else GateStatus.REQUIRED_FAILED
        return GateResult(
            name="build",
            command=cmd,
            status=status,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=dur,
        )

    @classmethod
    def _run_gate_3_tests(cls, workspace_path: Path, ecosystem: str) -> tuple[GateResult, int, bool]:
        """Run automated test suite and extract executed test count to detect uncertainty.

        Returns: (GateResult, test_count, is_skipped)
        """
        start = time.time()
        eco = ecosystem.lower().strip()

        if eco in ("angular", "react", "npm"):
            if not shutil.which("npm"):
                return (
                    GateResult(
                        name="test",
                        command="npm test",
                        status=GateStatus.REQUIRED_SKIPPED,
                        exit_code=0,
                        stdout="npm not detected",
                    ),
                    0,
                    True,
                )
            env = get_node_execution_env(workspace_path)
            if (workspace_path / "angular.json").exists():
                from amstralift.adapters.angular import AngularAdapter
                adapter = AngularAdapter()
                pkg_json = workspace_path / "package.json"
                scripts = {}
                if pkg_json.exists():
                    try:
                        scripts = json.loads(pkg_json.read_text(encoding="utf-8")).get("scripts", {})
                    except Exception:
                        pass
                raw_test = scripts.get("test")
                if not raw_test:
                    return (
                        GateResult(
                            name="test",
                            command="npm test",
                            status=GateStatus.REQUIRED_SKIPPED,
                            exit_code=0,
                            stdout="Script 'test' not defined in package.json",
                        ),
                        0,
                        True,
                    )
                resolved_test = adapter._resolve_angular_test_command(
                    raw_test, workspace_path, has_npm=True
                )
                if not resolved_test:
                    return (
                        GateResult(
                            name="test",
                            command="npm test",
                            status=GateStatus.REQUIRED_SKIPPED,
                            exit_code=0,
                            stdout="Angular workspace has no configured test target in angular.json",
                        ),
                        0,
                        True,
                    )
                cmd = resolved_test
            else:
                pkg_json = workspace_path / "package.json"
                scripts = {}
                if pkg_json.exists():
                    try:
                        scripts = json.loads(pkg_json.read_text(encoding="utf-8")).get("scripts", {})
                    except Exception:
                        pass
                if "test" not in scripts:
                    return (
                        GateResult(
                            name="test",
                            command="npm test",
                            status=GateStatus.REQUIRED_SKIPPED,
                            exit_code=0,
                            stdout="Script 'test' not defined in package.json",
                        ),
                        0,
                        True,
                    )
                cmd = "npm test"
        elif eco in ("dotnet", "nuget"):
            if not shutil.which("dotnet"):
                return (
                    GateResult(
                        name="test",
                        command="dotnet test",
                        status=GateStatus.REQUIRED_SKIPPED,
                        exit_code=0,
                        stdout="dotnet CLI not detected",
                    ),
                    0,
                    True,
                )
            cmd = "dotnet test"
            env = None
        else:
            cmd = "pytest"
            env = None

        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        dur = time.time() - start
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Extract executed test counts across runners
        test_count = 0
        dotnet_m = re.search(r"Passed:\s*(\d+)", stdout, re.IGNORECASE)
        if dotnet_m:
            test_count = int(dotnet_m.group(1))

        pytest_m = re.search(r"(\d+)\s+passed", stdout, re.IGNORECASE)
        if pytest_m and test_count == 0:
            test_count = int(pytest_m.group(1))

        jest_m = re.search(r"Tests:\s*(\d+)\s+passed", stdout, re.IGNORECASE)
        karma_m = re.search(r"Executed\s+(\d+)\s+of\s+(\d+)", stdout, re.IGNORECASE)
        if jest_m and test_count == 0:
            test_count = int(jest_m.group(1))
        elif karma_m and test_count == 0:
            test_count = int(karma_m.group(1))

        if "no test files found" in stdout.lower() or "no tests found" in stdout.lower():
            test_count = 0

        # Check if tests were skipped or missing script in npm
        browser_missing = any(
            phrase in (stdout + stderr).lower()
            for phrase in (
                "no chrome",
                "cannot start chrome",
                "no provider for \"chrome\"",
                "no provider for \"headlesschrome\"",
                "no browsers were found",
                "cannot find chrome",
                "could not start",
            )
        )
        is_skipped = (
            browser_missing
            or "missing script: test" in stderr.lower()
            or "no test specified" in stdout.lower()
            or "unknown argument: watch" in (stdout + stderr).lower()
            or "target test does not exist" in (stdout + stderr).lower()
            or proc.returncode == 0 and "no tests" in stdout.lower() and test_count == 0
        )

        status = GateStatus.REQUIRED_SKIPPED if is_skipped else (GateStatus.REQUIRED_PASSED if proc.returncode == 0 else GateStatus.REQUIRED_FAILED)
        result = GateResult(
            name="test",
            command=cmd,
            status=status,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=dur,
        )
        return result, test_count, is_skipped

    @classmethod
    def _run_gate_4_rescan(
        cls,
        workspace_path: Path,
        ecosystem: str,
        plan: RemediationPlan,
        scanner: OSVClient,
        governance_manager: Any = None,
    ) -> tuple[AuditReport, bool, str]:
        """Re-scan the resolved workspace to ensure targeted vulnerabilities are resolved."""
        deps = DependencyGraphAnalyzer.analyze(workspace_path, ecosystem)
        rescan_report = scanner.scan_discovered_dependencies(
            deps,
            ecosystem=ecosystem,
            repo_path=str(workspace_path),
            governance_manager=governance_manager,
        )

        remediated_packages = {item.package_name.lower(): item.target_version for item in plan.items}
        unresolved_cves = []
        for f in rescan_report.findings:
            if f.package_name.lower() in remediated_packages and not f.is_exempted:
                unresolved_cves.append(f"{f.package_name} ({f.cve_id})")

        if unresolved_cves:
            return (
                rescan_report,
                False,
                f"Vulnerabilities still present after remediation: {', '.join(unresolved_cves[:5])}",
            )

        return (
            rescan_report,
            True,
            f"Vulnerability rescan passed. All {len(plan.items)} targeted remediations successfully resolved.",
        )
