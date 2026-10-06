"""Tests for the Transformation Evidence, Refusal Engine, and Cryptographic Attestation."""

import json
from pathlib import Path

import pytest

from amstralift.evidence.evidence_engine import EvidenceEngine
from amstralift.evidence.models import (
    EvidenceBundle,
    RefusalCategory,
    RefusalRecord,
    TransformationStatus,
)
from amstralift.evidence.refusal_engine import RefusalEngine
from amstralift.evidence.reporter import render_html_report, render_markdown_report


def test_refusal_record_strict_code_unmodified():
    """Guarantee that RefusalRecord strictly enforces code_modified is False."""
    rec = RefusalRecord(
        adapter="angular",
        rule_id="test-rule",
        file_path="src/app.component.ts",
        category=RefusalCategory.UNSUPPORTED_PATTERN,
        reason="Unsupported dynamic decorator",
    )
    assert rec.code_modified is False
    assert rec.developer_review_required is True


def test_refusal_engine_ecosystem_detectors():
    """Verify deterministic refusal detectors across Angular, React, Python, and .NET."""
    engine = RefusalEngine()

    # 1. Angular custom decorator
    ang_code = "@CustomComponent({ selector: 'my-app' })\nexport class AppComponent {}"
    ang_refusals = engine.inspect_angular_file("src/app.component.ts", ang_code)
    assert len(ang_refusals) == 1
    assert ang_refusals[0].rule_id == "migrate-standalone-decorator"
    assert ang_refusals[0].code_modified is False

    # 2. React custom history abstraction
    react_code = "import { createBrowserHistory } from 'history';\nimport { useHistory } from 'react-router-dom';"
    react_refusals = engine.inspect_react_file("src/nav.tsx", react_code)
    assert len(react_refusals) == 1
    assert react_refusals[0].rule_id == "migrate-custom-history"
    assert react_refusals[0].category == RefusalCategory.UNSUPPORTED_PATTERN

    # 3. Python dynamic Pydantic model
    py_code = "from pydantic import create_model\nDynamicUser = create_model('User', **fields)"
    py_refusals = engine.inspect_python_file("models/user.py", py_code)
    assert len(py_refusals) == 1
    assert py_refusals[0].rule_id == "migrate-pydantic-dynamic-model"
    assert py_refusals[0].category == RefusalCategory.AMBIGUOUS_CONTRACT

    # 4. .NET legacy COM reference
    net_code = "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <ItemGroup>\n    <COMReference Include=\"LegacyAx\" />\n  </ItemGroup>\n</Project>"
    net_refusals = engine.inspect_dotnet_file("App.csproj", net_code)
    assert len(net_refusals) == 1
    assert net_refusals[0].rule_id == "migrate-com-reference"


def test_evidence_engine_overall_status_partial_modernization():
    """Verify that when safe changes pass gates but some changes are refused, status is PARTIALLY_MODERNIZED."""
    ee = EvidenceEngine(
        repo_name="EnterprisePortal",
        repo_path="/repos/portal",
        ecosystem="angular",
        framework_from="Angular 12",
        framework_to="Angular 17",
    )
    ee.set_file_metrics(analyzed=120, modified=15)

    # Record 2 successful transformations
    ee.record_transformation(
        adapter="angular",
        rule_id="migrate-standalone",
        file_path="src/home.component.ts",
        ast_nodes_changed=8,
    )
    ee.record_transformation(
        adapter="angular",
        rule_id="modernize-control-flow",
        file_path="src/home.component.html",
        ast_nodes_changed=12,
    )

    # Record 1 refusal
    ref = ee.refusal_engine.refuse(
        adapter="angular",
        rule_id="migrate-custom-history",
        file_path="src/auth.service.ts",
        category=RefusalCategory.UNSUPPORTED_PATTERN,
        reason="Custom auth routing wrapper cannot be safely analyzed.",
        manual_guidance="Migrate auth guard to functional CanActivateFn manually.",
    )
    ee.record_refusal(ref)

    # Update verification gates (all pass)
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        build_duration_seconds=4.2,
        tests_run=50,
        tests_passed=50,
        pre_cves_count=5,
        post_cves_count=0,
    )

    assert ee.bundle.overall_status == "PARTIALLY_MODERNIZED"
    assert ee.bundle.is_partially_modernized is True
    assert ee.bundle.verification.cves_resolved == 5


def test_evidence_engine_verification_failed_status():
    """Verify that when a gate fails, overall status is VERIFICATION_FAILED."""
    ee = EvidenceEngine(
        repo_name="BrokenApp",
        repo_path="/repos/broken",
        ecosystem="python",
    )
    ee.record_transformation(
        adapter="python",
        rule_id="upgrade-pydantic",
        file_path="models.py",
    )
    ee.update_verification_gates(
        clean_install="passed",
        build="failed",
        tests="skipped",
    )
    assert ee.bundle.overall_status == "VERIFICATION_FAILED"


def test_evidence_bundle_cryptographic_attestation():
    """Verify HMAC-SHA256 signing and tamper detection on EvidenceBundle."""
    ee = EvidenceEngine(
        repo_name="SignedProject",
        repo_path="/repos/signed",
        ecosystem="dotnet",
    )
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
    )
    secret_key = b"super-secret-enterprise-hmac-key-1234"
    attestation = ee.sign_bundle(secret_key)

    assert attestation.signature_algorithm == "HMAC-SHA256"
    assert len(attestation.bundle_signature) == 64
    assert ee.bundle.attestation is not None
    assert ee.bundle.attestation.bundle_signature == attestation.bundle_signature


def test_evidence_bundle_json_export(tmp_path: Path):
    """Verify export to JSON and round-trip deserialization."""
    ee = EvidenceEngine(
        repo_name="JsonTest",
        repo_path="/repos/json",
        ecosystem="react",
    )
    ee.record_transformation("react", "createRoot", "index.tsx", ast_nodes_changed=4)
    ee.update_verification_gates(clean_install="passed", build="passed")

    target_file = tmp_path / "evidence.json"
    ee.save_json(target_file)
    assert target_file.exists()

    raw_data = json.loads(target_file.read_text(encoding="utf-8"))
    loaded = EvidenceBundle.model_validate(raw_data)
    assert loaded.repo_name == "JsonTest"
    assert len(loaded.transformations_applied) == 1
    assert loaded.transformations_applied[0].rule_id == "createRoot"


def test_evidence_reporting_markdown_and_html():
    """Verify human-readable report generation includes both applied and refused sections."""
    ee = EvidenceEngine(
        repo_name="ReportTest",
        repo_path="/repos/report",
        ecosystem="angular",
        framework_from="Angular 12",
        framework_to="Angular 17",
    )
    ee.set_file_metrics(analyzed=500, modified=12)
    ee.record_transformation(
        adapter="angular",
        rule_id="standalone-migration",
        file_path="src/app.component.ts",
        ast_nodes_changed=6,
    )
    ref = ee.refusal_engine.refuse(
        adapter="angular",
        rule_id="migrate-custom-history",
        file_path="src/legacy.ts",
        category=RefusalCategory.UNSUPPORTED_PATTERN,
        reason="Custom routing wrapper cannot be deterministically verified.",
        manual_guidance="Inspect and migrate route manually.",
    )
    ee.record_refusal(ref)
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        pre_cves_count=3,
        post_cves_count=0,
    )
    ee.sign_bundle(b"test-secret-key")

    md = render_markdown_report(ee.bundle)
    assert "PARTIALLY MODERNIZED" in md
    assert "Refused Transformations (Safe Fallback)" in md
    assert "src/legacy.ts" in md
    assert "Custom routing wrapper cannot be deterministically verified" in md
    assert "Cryptographic Attestation Block" in md

    html_out = render_html_report(ee.bundle)
    assert "<!DOCTYPE html>" in html_out
    assert "PARTIALLY MODERNIZED" in html_out
    assert "Refused Transformations" in html_out
    assert "src/legacy.ts" in html_out
    assert "100% untouched" in html_out
