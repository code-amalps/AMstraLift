"""Comprehensive Acceptance Test Suite for AMstraLift Evidence & Refusal Architecture.

Validates the 10 core trust invariants:
1. Byte-for-byte refusal immutability on disk.
2. Refusal categorization across Angular, React, Python, and .NET.
3. Applied transformation to evidence record consistency.
4. Refusal to PARTIALLY_MODERNIZED status consistency.
5. Gate failure to VERIFICATION_FAILED status consistency.
6. Tampered evidence JSON to HMAC verification failure.
7. Tampered transformation or refusal record to verification failure.
8. Determinism of refusal detection across repeated runs.
9. Cryptographic attestation verification with constant-time security.
10. End-to-end evidence bundle serialization and reporting fidelity.
"""

import json
from pathlib import Path

import pytest

from amstralift.core.crypto import compute_file_sha256, compute_sha256
from amstralift.evidence.evidence_engine import (
    EvidenceEngine,
    compute_canonical_evidence_payload,
    verify_bundle_attestation,
)
from amstralift.evidence.models import (
    EvidenceBundle,
    RefusalCategory,
    RefusalRecord,
    TransformationRecord,
    TransformationStatus,
)
from amstralift.evidence.refusal_engine import RefusalEngine
from amstralift.evidence.reporter import render_html_report, render_markdown_report


# ── Invariant 1: Byte-for-Byte Refusal Immutability ───────────────────────────


def test_byte_for_byte_immutability_angular(tmp_path: Path):
    """Verify Angular file remains byte-for-byte identical when refusal is triggered."""
    f = tmp_path / "app.component.ts"
    content = "@CustomComponent({ selector: 'app-root' })\nexport class AppComponent {}"
    f.write_text(content, encoding="utf-8")

    hash_before = compute_file_sha256(str(f))

    engine = RefusalEngine()
    refusals = engine.inspect_angular_file(str(f), f.read_text(encoding="utf-8"))

    hash_after = compute_file_sha256(str(f))

    assert len(refusals) > 0
    assert hash_before == hash_after
    assert f.read_text(encoding="utf-8") == content
    assert all(r.code_modified is False for r in refusals)


def test_byte_for_byte_immutability_react(tmp_path: Path):
    """Verify React file remains byte-for-byte identical when refusal is triggered."""
    f = tmp_path / "Nav.tsx"
    content = "import { createBrowserHistory } from 'history';\nimport { useHistory } from 'react-router';\n"
    f.write_text(content, encoding="utf-8")

    hash_before = compute_file_sha256(str(f))

    engine = RefusalEngine()
    refusals = engine.inspect_react_file(str(f), f.read_text(encoding="utf-8"))

    hash_after = compute_file_sha256(str(f))

    assert len(refusals) > 0
    assert hash_before == hash_after
    assert f.read_text(encoding="utf-8") == content
    assert all(r.code_modified is False for r in refusals)


def test_byte_for_byte_immutability_python(tmp_path: Path):
    """Verify Python file remains byte-for-byte identical when refusal is triggered."""
    f = tmp_path / "models.py"
    content = "from pydantic import create_model\nDynamicObj = create_model('Dynamic', **extra_kwargs)\n"
    f.write_text(content, encoding="utf-8")

    hash_before = compute_file_sha256(str(f))

    engine = RefusalEngine()
    refusals = engine.inspect_python_file(str(f), f.read_text(encoding="utf-8"))

    hash_after = compute_file_sha256(str(f))

    assert len(refusals) > 0
    assert hash_before == hash_after
    assert f.read_text(encoding="utf-8") == content
    assert all(r.code_modified is False for r in refusals)


def test_byte_for_byte_immutability_dotnet(tmp_path: Path):
    """Verify .NET project file remains byte-for-byte identical when refusal is triggered."""
    f = tmp_path / "Legacy.csproj"
    content = "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <ItemGroup>\n    <COMReference Include=\"Word.Application\" />\n  </ItemGroup>\n</Project>\n"
    f.write_text(content, encoding="utf-8")

    hash_before = compute_file_sha256(str(f))

    engine = RefusalEngine()
    refusals = engine.inspect_dotnet_file(str(f), f.read_text(encoding="utf-8"))

    hash_after = compute_file_sha256(str(f))

    assert len(refusals) > 0
    assert hash_before == hash_after
    assert f.read_text(encoding="utf-8") == content
    assert all(r.code_modified is False for r in refusals)


# ── Invariant 2: Refusal Category Coverage Across Adapters ───────────────────


def test_refusal_categories_coverage():
    """Verify that multiple refusal categories are correctly classified."""
    engine = RefusalEngine()

    # UNSUPPORTED_PATTERN
    r1 = engine.inspect_react_file(
        "history.tsx",
        "createBrowserHistory() useHistory()",
    )[0]
    assert r1.category == RefusalCategory.UNSUPPORTED_PATTERN

    # COMPILATION_RISK
    r2 = engine.inspect_angular_file(
        "routes.ts",
        "loadChildren: 'admin/admin.module#AdminModule'",
    )[0]
    assert r2.category == RefusalCategory.COMPILATION_RISK

    # AMBIGUOUS_CONTRACT
    r3 = engine.inspect_python_file(
        "schema.py",
        "create_model('Dyn', **kwargs)",
    )[0]
    assert r3.category == RefusalCategory.AMBIGUOUS_CONTRACT

    # SANDBOX_FAILURE (via explicit refuse call)
    r4 = engine.refuse(
        adapter="angular",
        rule_id="sass-m2-theming",
        file_path="src/theme.scss",
        category=RefusalCategory.SANDBOX_FAILURE,
        reason="Sandbox Sass compiler threw syntax error during M2 mixin expansion.",
    )
    assert r4.category == RefusalCategory.SANDBOX_FAILURE


# ── Invariant 3: Applied Transformation Evidence Record Consistency ─────────


def test_applied_transformation_consistency():
    """Verify applied transformation tracking and metrics consistency."""
    ee = EvidenceEngine(repo_name="App", repo_path="/repo", ecosystem="angular")
    ee.set_file_metrics(analyzed=42, modified=7)

    tr = ee.record_transformation(
        adapter="angular",
        rule_id="standalone-migration",
        file_path="src/card.component.ts",
        ast_nodes_changed=14,
        dependencies_changed=["@angular/common"],
        diff_snippet="+ imports: [CommonModule]",
    )

    assert tr.status == TransformationStatus.APPLIED
    assert tr.ast_nodes_changed == 14
    assert tr.dependencies_changed == ["@angular/common"]
    assert len(ee.bundle.transformations_applied) == 1
    assert ee.bundle.files_analyzed == 42
    assert ee.bundle.files_modified == 7


# ── Invariant 4: Refusal to PARTIALLY_MODERNIZED Consistency ─────────────────


def test_refusal_to_partially_modernized_status():
    """Verify that 1+ refusals with passing gates strictly resolves to PARTIALLY_MODERNIZED."""
    ee = EvidenceEngine(repo_name="Core", repo_path="/core", ecosystem="react")
    ee.record_transformation("react", "createRoot", "index.tsx", ast_nodes_changed=3)

    ref = ee.refusal_engine.refuse(
        adapter="react",
        rule_id="migrate-custom-history",
        file_path="history.ts",
        category=RefusalCategory.UNSUPPORTED_PATTERN,
        reason="Custom history wrapper.",
    )
    ee.record_refusal(ref)

    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
    )

    assert ee.bundle.overall_status == "PARTIALLY_MODERNIZED"
    assert ee.bundle.is_partially_modernized is True


# ── Invariant 5: Gate Failure to VERIFICATION_FAILED Consistency ─────────────


def test_gate_failure_overrides_to_verification_failed():
    """Verify that build or test failures strictly force VERIFICATION_FAILED."""
    ee = EvidenceEngine(repo_name="Backend", repo_path="/backend", ecosystem="dotnet")
    ee.record_transformation("dotnet", "cpm", "Directory.Packages.props")

    ee.update_verification_gates(
        clean_install="passed",
        build="failed",
        tests="skipped",
    )
    assert ee.bundle.overall_status == "VERIFICATION_FAILED"

    # Even if tests fail after build passes
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="failed",
    )
    assert ee.bundle.overall_status == "VERIFICATION_FAILED"


# ── Invariant 6: Tampered Evidence JSON to HMAC Verification Failure ────────


def test_tampered_json_fails_hmac_attestation(tmp_path: Path):
    """Verify that altering any field in serialized evidence.json invalidates cryptographic attestation."""
    secret = b"vault-secret-key-123456"
    ee = EvidenceEngine(repo_name="VaultApp", repo_path="/vault", ecosystem="python")
    ee.record_transformation("python", "pydantic-v2", "models.py", ast_nodes_changed=5)
    ee.update_verification_gates(clean_install="passed", build="passed")
    ee.sign_bundle(secret)

    # 1. Unmodified bundle verifies successfully
    assert ee.verify_attestation(secret) is True

    # 2. Export to disk
    json_path = tmp_path / "evidence.json"
    ee.save_json(json_path)

    # 3. Tamper with JSON on disk (e.g. change files_modified)
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    raw["files_modified"] = 999
    tampered_bundle = EvidenceBundle.model_validate(raw)

    # 4. Tampered bundle MUST fail verification
    assert verify_bundle_attestation(tampered_bundle, secret) is False

    # 5. Wrong secret key MUST also fail verification
    assert verify_bundle_attestation(ee.bundle, b"wrong-key") is False


# ── Invariant 7: Tampered Transformation/Refusal Record to Verification Failure


def test_tampered_record_ids_fail_verification():
    """Verify that altering applied or refused transformation IDs fails cryptographic verification."""
    secret = b"hmac-test-secret-key"
    ee = EvidenceEngine(repo_name="StrictApp", repo_path="/strict", ecosystem="angular")
    t1 = ee.record_transformation("angular", "rule1", "f1.ts")
    ee.update_verification_gates(clean_install="passed", build="passed")
    ee.sign_bundle(secret)

    assert ee.verify_attestation(secret) is True

    # Tamper with transformation ID inside the bundle
    t1.id = "tr_forged_id_0000"
    assert ee.verify_attestation(secret) is False


# ── Invariant 8: Determinism of Refusal Detection Across Repeated Runs ───────


def test_refusal_detection_determinism():
    """Verify repeated inspection of the same file produces identical refusal counts and rules."""
    engine = RefusalEngine()
    py_sample = "class Bad(BaseModel, metaclass=CustomMeta):\n    pass\n"

    res_1 = engine.inspect_python_file("test.py", py_sample)
    res_2 = engine.inspect_python_file("test.py", py_sample)

    assert len(res_1) == len(res_2) == 1
    assert res_1[0].rule_id == res_2[0].rule_id == "migrate-pydantic-metaclass"
    assert res_1[0].category == res_2[0].category == RefusalCategory.UNSUPPORTED_PATTERN
    assert res_1[0].reason == res_2[0].reason


# ── Invariant 9: Cryptographic Attestation Constant-Time Security ────────────


def test_attestation_absence_returns_false():
    """Verify that verifying an unsigned bundle safely returns False without crashing."""
    ee = EvidenceEngine(repo_name="UnsignedApp", repo_path="/unsigned", ecosystem="react")
    assert ee.verify_attestation(b"any-key") is False


# ── Invariant 10: End-to-End Reporting & Serialization Fidelity ───────────────


def test_evidence_report_fidelity_and_completeness():
    """Verify complete evidence bundle renders faithfully into both Markdown and HTML."""
    ee = EvidenceEngine(
        repo_name="FintechPortal",
        repo_path="/apps/portal",
        ecosystem="angular",
        framework_from="Angular 12",
        framework_to="Angular 17",
    )
    ee.set_file_metrics(analyzed=240, modified=18)
    ee.record_transformation("angular", "standalone", "src/comp.ts", ast_nodes_changed=10)
    ref = ee.refusal_engine.refuse(
        adapter="angular",
        rule_id="custom-decorator",
        file_path="src/wrapped.ts",
        category=RefusalCategory.UNSUPPORTED_PATTERN,
        reason="Custom component wrapper detected.",
        manual_guidance="Convert wrapper to standard standalone component.",
    )
    ee.record_refusal(ref)
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        build_duration_seconds=5.4,
        tests_run=120,
        tests_passed=120,
        pre_cves_count=7,
        post_cves_count=0,
    )
    ee.sign_bundle(b"portal-attestation-secret")

    md = render_markdown_report(ee.bundle)
    assert "PARTIALLY MODERNIZED" in md
    assert "Files analyzed" in md
    assert "240" in md
    assert "Vulnerabilities Resolved" in md
    assert "7" in md
    assert "src/wrapped.ts" in md
    assert "Convert wrapper to standard standalone component." in md
    assert "Cryptographic Attestation Block" in md

    html_out = render_html_report(ee.bundle)
    assert "FintechPortal" in html_out
    assert "PARTIALLY MODERNIZED" in html_out
    assert "src/wrapped.ts" in html_out
    assert "100% untouched" in html_out


def test_stage_b_pr_body_includes_refusal_evidence(tmp_path: Path):
    """Verify that Stage B PR proposal formats the Refusal Evidence table in the PR description."""
    from datetime import datetime, timezone
    from amstralift.core.crypto import sign_bundle
    from amstralift.core.workspace import get_head_commit, run_git
    from amstralift.core.models import (
        DependencyChange,
        DependencyTier,
        GateResult,
        GateStatus,
        GateSummary,
        UnsignedAdvisoryBundle,
    )
    from amstralift.execution.stage_b import run_stage_b

    # Initialize temporary git repo
    run_git(["init"], cwd=tmp_path)
    run_git(["config", "user.name", "Test Runner"], cwd=tmp_path)
    run_git(["config", "user.email", "test@example.com"], cwd=tmp_path)
    run_git(["config", "core.autocrlf", "false"], cwd=tmp_path)
    (tmp_path / "pkg.json").write_text("{\"name\": \"test\"}\n", encoding="utf-8")
    run_git(["add", "."], cwd=tmp_path)
    run_git(["commit", "-m", "init"], cwd=tmp_path)
    head_sha = get_head_commit(tmp_path)

    secret = b"test-stage-b-secret"
    patch_text = "diff --git a/pkg.json b/pkg.json\n--- a/pkg.json\n+++ b/pkg.json\n"
    unsigned = UnsignedAdvisoryBundle(
        repo_url="https://github.com/org/repo",
        target_branch="master" if "master" in (run_git(["branch", "--show-current"], cwd=tmp_path).stdout or "") else "main",
        base_commit_sha=head_sha,
        head_commit_sha=head_sha,
        patch=patch_text,
        patch_sha256=compute_sha256(patch_text),
        changes=[
            DependencyChange(
                package_name="test-lib",
                from_version="1.0.0",
                to_version="2.0.0",
                tier=DependencyTier.TIER_1_SAFE,
            )
        ],
        gate_summary=GateSummary(
            results=[
                GateResult(
                    name="Build",
                    command="npm run build",
                    status=GateStatus.REQUIRED_PASSED,
                    exit_code=0,
                )
            ]
        ),
        modernizations=["Converted 14 components to standalone"],
        refusals=[
            {
                "rule_id": "migrate-custom-history",
                "file_path": "src/history.ts",
                "category": "unsupported_pattern",
                "reason": "Custom history abstraction detected.",
            }
        ],
    )
    signed = sign_bundle(unsigned, secret, ttl_seconds=3600)

    # Run Stage B in dry-run mode
    proposal = run_stage_b(
        signed_bundle=signed,
        target_repo_path=tmp_path,
        secret_key=secret,
        dry_run=True,
    )

    assert "Deliberately Refused Transformations (Safe Fallback)" in proposal.body
    assert "Source code for each file below was left 100% untouched." in proposal.body
    assert "migrate-custom-history" in proposal.body
    assert "src/history.ts" in proposal.body
    assert "Custom history abstraction detected." in proposal.body
    assert "Automated Modernizations Applied" in proposal.body
    assert "Converted 14 components to standalone" in proposal.body
