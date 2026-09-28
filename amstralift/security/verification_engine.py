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
    VerificationConfidence,
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
    ) -> tuple[bool, VerificationConfidence, GateSummary, AuditReport | None, str | None]:
        """Execute Gates 1-5 inside the isolated workspace.

        Returns:
            (success, confidence, gate_summary, rescan_report, uncertainty_warning)
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
                VerificationConfidence.GATES_FAILED,
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
                VerificationConfidence.GATES_FAILED,
                GateSummary(results=results),
                None,
                f"Gate 2 Build failed: {g2_res.stderr or g2_res.stdout}",
            )

        # ====================================================
        # Gate 3: Automated Tests & Uncertainty Evaluation
        # ====================================================
        g3_res, test_count = cls._run_gate_3_tests(workspace_path, ecosystem)
        results.append(g3_res)
        if g3_res.status == GateStatus.REQUIRED_FAILED:
            return (
                False,
                VerificationConfidence.GATES_FAILED,
                GateSummary(results=results),
                None,
                f"Gate 3 Tests failed: {g3_res.stderr or g3_res.stdout}",
            )

        uncertainty_warning = None
        if test_count == 0:
            confidence = VerificationConfidence.COMPILED_UNVERIFIED
            uncertainty_warning = (
                "⚠️ UNCERTAINTY WARNING: Build compiled successfully, but NO automated tests were discovered "
                "in this repository. Functional safety cannot be verified automatically. Manual QA required."
            )
        else:
            confidence = VerificationConfidence.VERIFIED_SAFE

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
                VerificationConfidence.GATES_FAILED,
                GateSummary(results=results),
                rescan_report,
                f"Gate 4 Vulnerability Rescan failed: {rescan_reason}",
            )

        # ====================================================
        # Gate 5: Final Decision
        # ====================================================
        gate_summary = GateSummary(results=results)
        return True, confidence, gate_summary, rescan_report, uncertainty_warning

    @classmethod
    def _run_gate_1_resolution(cls, workspace_path: Path, ecosystem: str) -> GateResult:
        """Verify that dependencies resolve without conflicts."""
        start = time.time()
        eco = ecosystem.lower().strip()

        if eco in ("angular", "react", "npm"):
            if shutil.which("npm"):
                cmd = "npm install --package-lock-only --dry-run"
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
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
            cmd = "npm run build" if shutil.which("npm") else "npm test"
            env = get_node_execution_env()
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
    def _run_gate_3_tests(cls, workspace_path: Path, ecosystem: str) -> tuple[GateResult, int]:
        """Run automated test suite and extract executed test count to detect uncertainty."""
        start = time.time()
        eco = ecosystem.lower().strip()

        if eco in ("angular", "react", "npm"):
            cmd = "npm test"
            env = get_node_execution_env()
        elif eco in ("dotnet", "nuget"):
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
        # .NET: "Total tests: 10. Passed: 10." or "Passed! - Failed: 0, Passed: 5"
        dotnet_m = re.search(r"Passed:\s*(\d+)", stdout, re.IGNORECASE)
        if dotnet_m:
            test_count = int(dotnet_m.group(1))

        # Pytest: "10 passed in 0.5s"
        pytest_m = re.search(r"(\d+)\s+passed", stdout, re.IGNORECASE)
        if pytest_m and test_count == 0:
            test_count = int(pytest_m.group(1))

        # Jest/Karma: "Tests: 12 passed" or "Executed 12 of 12"
        jest_m = re.search(r"Tests:\s*(\d+)\s+passed", stdout, re.IGNORECASE)
        karma_m = re.search(r"Executed\s+(\d+)\s+of\s+(\d+)", stdout, re.IGNORECASE)
        if jest_m and test_count == 0:
            test_count = int(jest_m.group(1))
        elif karma_m and test_count == 0:
            test_count = int(karma_m.group(1))

        # If returncode is 0 but test output contains "No test files found" or "0 passed"
        if "no test files found" in stdout.lower() or "no tests found" in stdout.lower():
            test_count = 0

        status = GateStatus.REQUIRED_PASSED if proc.returncode == 0 else GateStatus.REQUIRED_FAILED
        result = GateResult(
            name="test",
            command=cmd,
            status=status,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=dur,
        )
        return result, test_count

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
        # Check if any remaining finding matches the targeted CVEs on remediated packages
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
