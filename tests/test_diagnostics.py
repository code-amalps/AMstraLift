"""Unit tests for Smart Diagnostics and Draft PR workflow."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amstralift.core.diagnostics import (
    GateDiagnostic,
    diagnose_gate_failure,
    format_diagnostic_panel,
)
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
    SignedAdvisoryBundle,
    UnsignedAdvisoryBundle,
)
from amstralift.execution.stage_b import StageBPublishError, run_stage_b


def test_diagnose_angular_entry_components_error():
    sample_output = """
    > ng build --configuration production
    Compiling @angular/core : es2015 as esm2015
    src/app/app.module.ts(18,5): error TS2339: Property 'entryComponents' does not exist on type 'NgModule'.
    Error: Failed to compile module.
    """
    diag = diagnose_gate_failure(
        gate_name="build",
        command="npm run build",
        stderr=sample_output,
        ecosystem="angular",
    )

    assert diag.gate_name == "build"
    assert diag.failing_file == "src/app/app.module.ts"
    assert diag.line_number == 18
    assert diag.column_number == 5
    assert diag.error_code == "TS2339"
    assert "entryComponents" in diag.error_message
    assert "Ivy" in diag.remediation_tip
    assert diag.docs_url == "https://update.angular.io/?v=12.0-13.0"


def test_diagnose_dotnet_binary_formatter_error():
    sample_output = """
    Microsoft (R) Build Engine version 17.8.3
    Services/DataSerializer.cs(45,18): error CS0618: 'BinaryFormatter' is obsolete: 'BinaryFormatter serialization is dangerous'
    Build FAILED.
    """
    diag = diagnose_gate_failure(
        gate_name="build",
        command="dotnet build",
        stdout=sample_output,
        ecosystem="dotnet",
    )

    assert diag.failing_file == "Services/DataSerializer.cs"
    assert diag.line_number == 45
    assert diag.error_code == "CS0618"
    assert "BinaryFormatter" in diag.remediation_tip
    assert "System.Text.Json" in diag.remediation_tip


def test_diagnose_python_pytest_failure():
    sample_output = """
    ============================= test session starts ==============================
    FAILED tests/test_models.py::test_user_serialization - PydanticDeprecatedSince20: The `dict` method is deprecated; use `model_dump` instead.
    ============================== 1 failed in 0.12s ===============================
    """
    diag = diagnose_gate_failure(
        gate_name="test",
        command="pytest",
        stdout=sample_output,
        ecosystem="python",
    )

    assert diag.failing_file == "tests/test_models.py"
    assert "dict" in diag.error_message or "deprecated" in diag.error_message
    assert "Pydantic" in diag.remediation_tip
    assert "model_dump" in diag.remediation_tip


def test_format_diagnostic_panel():
    diag = GateDiagnostic(
        gate_name="test",
        failing_file="src/index.ts",
        line_number=42,
        error_code="TS1005",
        error_message="';' expected.",
        remediation_tip="Add missing semicolon.",
        docs_url="https://typescriptlang.org",
    )
    panel = format_diagnostic_panel(diag)
    assert panel is not None
    assert "TS1005" in panel.renderable


@pytest.fixture
def dummy_bundle_with_failed_gate():
    from amstralift.core.crypto import compute_sha256, sign_bundle

    patch_text = """diff --git a/package.json b/package.json
index 1111111..2222222 100644
--- a/package.json
+++ b/package.json
@@ -1,3 +1,3 @@
 {
-  "version": "1.0.0"
+  "version": "2.0.0"
 }
"""
    unsigned = UnsignedAdvisoryBundle(
        repo_url="/dummy/repo",
        target_branch="main",
        base_commit_sha="a" * 40,
        head_commit_sha="a" * 40,
        patch=patch_text,
        patch_sha256=compute_sha256(patch_text),
        changes=[
            DependencyChange(
                package_name="@angular/core",
                from_version="12.0.0",
                to_version="13.0.0",
                tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
            )
        ],
        gate_summary=GateSummary(
            results=[
                GateResult(
                    name="test",
                    command="npm test",
                    status=GateStatus.REQUIRED_FAILED,
                    exit_code=1,
                    stderr="src/app/modal.ts(12,4): error TS2339: Property 'entryComponents' does not exist.",
                )
            ]
        ),
    )
    return sign_bundle(unsigned, b"secret" * 8)


def test_stage_b_fails_closed_without_draft_on_fail(tmp_path: Path, dummy_bundle_with_failed_gate: SignedAdvisoryBundle):
    secret_key = b"secret" * 8
    with patch("amstralift.execution.stage_b.is_working_tree_clean", return_value=True), \
         patch("amstralift.execution.stage_b.get_head_commit", return_value="a" * 40):
        with pytest.raises(StageBPublishError) as exc_info:
            run_stage_b(
                signed_bundle=dummy_bundle_with_failed_gate,
                target_repo_path=tmp_path,
                secret_key=secret_key,
                dry_run=False,
                draft_on_fail=False,
            )
        assert exc_info.value.diagnostic is not None
        assert exc_info.value.diagnostic.failing_file == "src/app/modal.ts"
        assert exc_info.value.diagnostic.error_code == "TS2339"


def test_stage_b_draft_on_fail_creates_draft_branch(tmp_path: Path, dummy_bundle_with_failed_gate: SignedAdvisoryBundle):
    secret_key = b"secret" * 8
    with patch("amstralift.execution.stage_b.is_working_tree_clean", return_value=True), \
         patch("amstralift.execution.stage_b.get_head_commit", return_value="a" * 40), \
         patch("amstralift.execution.stage_b.verify_rediff_integrity"), \
         patch("amstralift.execution.stage_b.get_active_branch", return_value="main"), \
         patch("amstralift.execution.stage_b.run_git") as mock_git, \
         patch("amstralift.execution.stage_b.extract_patch_files", return_value=["package.json"]):

        mock_git.return_value = MagicMock(returncode=0, stdout="", stderr="")

        proposal = run_stage_b(
            signed_bundle=dummy_bundle_with_failed_gate,
            target_repo_path=tmp_path,
            secret_key=secret_key,
            dry_run=False,
            draft_on_fail=True,
        )

        assert proposal.publish_status == "DRAFT_COMMITTED"
        assert "draft" in proposal.branch_name
        assert "[DRAFT]" in proposal.title
        assert "DRAFT UPGRADE" in proposal.body
        assert "src/app/modal.ts" in proposal.body
