"""Unit and integration tests for safe remediation modes, verification gates, and uncertainty detection."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from amstralift.core.models import GateResult, GateStatus
from amstralift.governance.vulnerabilities import VulnerabilitySeverity
from amstralift.security.models import (
    AuditReport,
    RemediationPlan,
    VulnerabilityFinding,
)
from amstralift.security.plan_generator import RemediationPlanGenerator
from amstralift.security.remediation_engine import RemediationEngine
from amstralift.security.verification_engine import VerificationEngine


def test_remediation_plan_generator_direct_and_transitive():
    """Verify plan generator classifies direct vs transitive and chooses appropriate mechanisms."""
    report = AuditReport(
        repo_path="/test",
        ecosystem="npm",
        scanned_packages_count=2,
        findings=[
            VulnerabilityFinding(
                cve_id="CVE-2021-1111",
                package_name="express",
                ecosystem="npm",
                current_version="4.16.0",
                severity=VulnerabilitySeverity.HIGH,
                fixed_version="4.17.1",
                all_fixed_versions=["4.17.1", "5.0.0"],
                is_direct=True,
                introduced_by=[],
                summary="High severity CVE in express",
            ),
            VulnerabilityFinding(
                cve_id="CVE-2021-2222",
                package_name="qs",
                ecosystem="npm",
                current_version="6.5.0",
                severity=VulnerabilitySeverity.CRITICAL,
                fixed_version="6.5.3",
                all_fixed_versions=["6.5.3"],
                is_direct=False,
                introduced_by=["express"],
                summary="Critical prototype pollution in qs",
            ),
        ],
    )

    plan = RemediationPlanGenerator.generate_plan(report)
    assert len(plan.items) == 2

    express_item = next(item for item in plan.items if item.package_name == "express")
    assert express_item.is_direct
    assert express_item.remediation_mechanism == "package.json dependencies"
    assert express_item.target_version == "4.17.1"
    assert not express_item.is_major_bump

    qs_item = next(item for item in plan.items if item.package_name == "qs")
    assert not qs_item.is_direct
    assert qs_item.remediation_mechanism == "npm overrides"
    assert qs_item.target_version == "6.5.3"
    assert "express" in qs_item.introduced_by


def test_npm_overrides_application(tmp_path: Path):
    """Verify RemediationEngine injects 'overrides' in package.json for transitive fixes."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps({
            "name": "sample",
            "dependencies": {"express": "4.16.0"},
        }),
        encoding="utf-8",
    )

    report = AuditReport(
        repo_path=str(tmp_path),
        ecosystem="npm",
        findings=[
            VulnerabilityFinding(
                cve_id="CVE-2022-9999",
                package_name="body-parser",
                ecosystem="npm",
                current_version="1.18.0",
                severity=VulnerabilitySeverity.HIGH,
                fixed_version="1.19.0",
                is_direct=False,
                introduced_by=["express"],
            )
        ],
    )

    plan = RemediationPlanGenerator.generate_plan(report)
    with patch("shutil.which", return_value=None):  # don't run npm CLI in unit test
        modified = RemediationEngine.apply_npm(tmp_path, plan)

    assert "package.json" in modified
    updated_data = json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "overrides" in updated_data
    assert updated_data["overrides"]["body-parser"] == "1.19.0"


def test_dotnet_transitive_pin_application(tmp_path: Path):
    """Verify RemediationEngine adds transitive PackageReference pin to .csproj."""
    csproj = tmp_path / "TestProject.csproj"
    csproj.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Azure.Identity" Version="1.10.0" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    report = AuditReport(
        repo_path=str(tmp_path),
        ecosystem="dotnet",
        findings=[
            VulnerabilityFinding(
                cve_id="CVE-2024-38178",
                package_name="System.Text.Json",
                ecosystem="NuGet",
                current_version="8.0.0",
                severity=VulnerabilitySeverity.HIGH,
                fixed_version="8.0.4",
                is_direct=False,
                introduced_by=["Azure.Identity"],
            )
        ],
    )

    plan = RemediationPlanGenerator.generate_plan(report)
    with patch("shutil.which", return_value=None):
        modified = RemediationEngine.apply_dotnet(tmp_path, plan)

    assert len(modified) > 0
    updated_xml = csproj.read_text(encoding="utf-8")
    assert 'Include="System.Text.Json" Version="8.0.4"' in updated_xml
    assert "AMstraLift Security Fix" in updated_xml


def test_gate_3_uncertainty_detection():
    """Verify Gate 3 distinguishes VERIFIED_SAFE (>0 tests) from UNVERIFIED_NO_TESTS (0 tests)."""
    # 1. 0 tests executed -> UNVERIFIED_NO_TESTS
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(return_value=0, returncode=0, stdout="Passed: 0, Failed: 0, Total: 0", stderr="")
        g3, count, is_skipped = VerificationEngine._run_gate_3_tests(Path("/tmp"), "dotnet")
        assert g3.status == GateStatus.REQUIRED_PASSED
        assert count == 0
        assert not is_skipped

    # 2. 15 tests executed and passed -> VERIFIED_SAFE
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(return_value=0, returncode=0, stdout="Passed: 15, Failed: 0, Total: 15", stderr="")
        g3, count, is_skipped = VerificationEngine._run_gate_3_tests(Path("/tmp"), "dotnet")
        assert g3.status == GateStatus.REQUIRED_PASSED
        assert count == 15
        assert not is_skipped

    # 3. Tests skipped or missing script -> VERIFICATION_INCOMPLETE
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(return_value=0, returncode=0, stdout="npm ERR! missing script: test", stderr="missing script: test")
        g3, count, is_skipped = VerificationEngine._run_gate_3_tests(Path("/tmp"), "angular")
        assert is_skipped


def test_verification_matrix_situations():
    """Verify the explicit 6-situation behavioral matrix in VerificationEngine."""
    from amstralift.security.models import VerificationStatus

    dummy_plan = RemediationPlan()

    # Situation 1: Build fails -> Remediation fails (BUILD_FAILED)
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_FAILED, exit_code=1, stderr="TypeScript error TS2304")):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert not eligible
        assert status == VerificationStatus.BUILD_FAILED
        assert "Gate 2 Build failed" in warn

    # Situation 2: Tests fail -> Remediation fails (TESTS_FAILED)
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_3_tests", return_value=(GateResult(name="test", command="npm test", status=GateStatus.REQUIRED_FAILED, exit_code=1, stderr="AssertionError: expected true to be false"), 0, False)):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert not eligible
        assert status == VerificationStatus.TESTS_FAILED
        assert "Gate 3 Tests failed" in warn

    # Situation 3: No relevant tests exist (0 tests run) -> UNVERIFIED_NO_TESTS (Eligible under review policy)
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_3_tests", return_value=(GateResult(name="test", command="npm test", status=GateStatus.REQUIRED_PASSED, exit_code=0), 0, False)), \
         patch.object(VerificationEngine, "_run_gate_4_rescan", return_value=(None, True, "All resolved")):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert eligible  # Eligible for manual review PR proposal
        assert status == VerificationStatus.UNVERIFIED_NO_TESTS
        assert "UNVERIFIED (NO AUTOMATED TESTS)" in warn

    # Situation 4: Tests skipped or cannot run -> VERIFICATION_INCOMPLETE
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_3_tests", return_value=(GateResult(name="test", command="npm test", status=GateStatus.REQUIRED_SKIPPED, exit_code=0), 0, True)), \
         patch.object(VerificationEngine, "_run_gate_4_rescan", return_value=(None, True, "All resolved")):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert eligible
        assert status == VerificationStatus.VERIFICATION_INCOMPLETE
        assert "VERIFICATION INCOMPLETE" in warn

    # Situation 5: Vulnerability rescan fails -> RESCAN_FAILED (Do not claim resolved)
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_3_tests", return_value=(GateResult(name="test", command="npm test", status=GateStatus.REQUIRED_PASSED, exit_code=0), 10, False)), \
         patch.object(VerificationEngine, "_run_gate_4_rescan", return_value=(None, False, "Vulnerability CVE-2024-1234 still present")):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert not eligible
        assert status == VerificationStatus.RESCAN_FAILED
        assert "Gate 4 Vulnerability Rescan failed" in warn
        assert "Do not claim vulnerability is resolved" in warn

    # Situation 6: Build & required tests pass (>0 tests) -> VERIFIED_SAFE
    with patch.object(VerificationEngine, "_run_gate_1_resolution", return_value=GateResult(name="res", command="npm", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_2_build", return_value=GateResult(name="build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)), \
         patch.object(VerificationEngine, "_run_gate_3_tests", return_value=(GateResult(name="test", command="npm test", status=GateStatus.REQUIRED_PASSED, exit_code=0), 10, False)), \
         patch.object(VerificationEngine, "_run_gate_4_rescan", return_value=(None, True, "All resolved")):
        eligible, status, summary, rescan, warn = VerificationEngine.execute_gates(Path("/tmp"), "npm", dummy_plan)
        assert eligible
        assert status == VerificationStatus.VERIFIED_SAFE
        assert warn is None
