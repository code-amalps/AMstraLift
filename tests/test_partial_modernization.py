"""End-to-end acceptance tests for Phase P4: Partial Modernization Engine.

Proves that AMstraLift can safely apply a subset of transformations,
verify that safe subset in the sandbox, refuse unsafe transformations
(leaving original code 100% untouched), and deliver a coherent, auditable
PARTIALLY_MODERNIZED delivery artifact.
"""

import json
from pathlib import Path

import pytest

from amstralift.core.crypto import compute_file_sha256, compute_sha256, sign_bundle
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
    UnsignedAdvisoryBundle,
)
from amstralift.core.workspace import get_head_commit, run_git
from amstralift.evidence.evidence_engine import (
    EvidenceEngine,
    verify_bundle_attestation,
)
from amstralift.evidence.models import (
    EvidenceBundle,
    GateVerificationRecord,
    RefusalCategory,
    TransformationDecision,
)
from amstralift.evidence.pipeline import (
    CandidateTransformation,
    TransformationDecisionPipeline,
)
from amstralift.evidence.reporter import render_html_report, render_markdown_report
from amstralift.execution.stage_b import run_stage_b


# ── Test 1: Headline Synthetic Modernization Scenario (10 Candidates: 8 Applied, 2 Refused)


def test_headline_partial_modernization_scenario(tmp_path: Path):
    """Headline P4 Acceptance Test:

    10 transformations:
    - 8 applied
    - 2 refused (unsupported custom history & dynamic Pydantic model)
    - 5 gates pass
    - Outcome: PARTIALLY_MODERNIZED with byte-for-byte refusal preservation
    """
    repo_dir = tmp_path / "synthetic_repo"
    repo_dir.mkdir()

    # Create 10 source files
    initial_hashes: dict[str, str] = {}
    candidates: list[CandidateTransformation] = []

    for i in range(1, 11):
        rel_path = f"src/component_{i}.ts" if i != 7 else "src/models_7.py"
        file_path = repo_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if i == 4:
            # Candidate 4: Unsupported React custom history abstraction -> REFUSE
            content = "import { createBrowserHistory } from 'history';\nimport { useHistory } from 'react-router';\n"
            adapter = "react"
            rule_id = "migrate-custom-history"
            apply_fn = None
        elif i == 7:
            # Candidate 7: Unsupported Python dynamic model synthesis -> REFUSE
            content = "from pydantic import create_model\nDynUser = create_model('User', **fields)\n"
            adapter = "python"
            rule_id = "migrate-pydantic-dynamic-model"
            apply_fn = None
        else:
            # Candidates 1,2,3,5,6,8,9,10: Safe transformations -> APPLY
            content = f"// Component {i} initial version\n@Component({{ selector: 'comp-{i}' }})\nexport class Comp{i} {{}}\n"
            adapter = "angular"
            rule_id = "migrate-standalone"

            def make_apply(c_num: int):
                def _apply(p: Path):
                    old_c = p.read_text(encoding="utf-8")
                    new_c = old_c.replace(f"selector: 'comp-{c_num}'", f"standalone: true, selector: 'comp-{c_num}'")
                    p.write_text(new_c, encoding="utf-8")
                    return 2, f"+ standalone: true in comp-{c_num}"
                return _apply

            apply_fn = make_apply(i)

        file_path.write_text(content, encoding="utf-8")
        initial_hashes[rel_path] = compute_file_sha256(str(file_path))

        candidates.append(
            CandidateTransformation(
                id=f"cand_{i:02d}",
                adapter=adapter,
                rule_id=rule_id,
                file_path=rel_path,
                apply_fn=apply_fn,
            )
        )

    # Execute Decision Pipeline
    ee = EvidenceEngine(
        repo_name="SyntheticMonorepo",
        repo_path=str(repo_dir),
        ecosystem="polyglot",
        framework_from="Legacy Stack",
        framework_to="Modern LTS",
    )
    pipeline = TransformationDecisionPipeline(ee, repo_dir)
    results = pipeline.evaluate_and_execute(candidates)

    # 1. Assert result counts
    assert len(results) == 10
    applied_results = [r for r in results if r.is_applied]
    refused_results = [r for r in results if r.is_refused]
    assert len(applied_results) == 8
    assert len(refused_results) == 2

    # 2. Invariant: Candidate 4 refusal did NOT poison candidates 5 through 10
    assert results[3].is_refused is True
    assert results[4].is_applied is True
    assert results[5].is_applied is True
    assert results[6].is_refused is True
    assert results[7].is_applied is True
    assert results[8].is_applied is True
    assert results[9].is_applied is True

    # 3. Invariant: 8 files were modified on disk
    for r in applied_results:
        p = repo_dir / r.file_path
        current_hash = compute_file_sha256(str(p))
        assert current_hash != initial_hashes[r.file_path]
        assert "standalone: true" in p.read_text(encoding="utf-8")

    # 4. Invariant: 2 refused files are byte-for-byte identical
    for r in refused_results:
        p = repo_dir / r.file_path
        current_hash = compute_file_sha256(str(p))
        assert current_hash == initial_hashes[r.file_path]
        assert r.refusal_record is not None
        assert r.refusal_record.code_modified is False

    # 5. Reconcile 5-Gate sandbox verification
    gate_record = GateVerificationRecord(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        build_duration_seconds=6.1,
        tests_run=84,
        tests_passed=84,
        pre_cves_count=4,
        post_cves_count=0,
    )
    final_status = pipeline.reconcile_modernization_status(gate_record)

    # 6. Outcome is formally PARTIALLY_MODERNIZED
    assert final_status == "PARTIALLY_MODERNIZED"
    assert ee.bundle.overall_status == "PARTIALLY_MODERNIZED"
    assert ee.bundle.is_partially_modernized is True
    assert ee.bundle.files_analyzed == 10
    assert ee.bundle.files_modified == 8

    # 7. Cryptographic attestation verifies cleanly
    secret = b"synthetic-production-secret-key"
    ee.sign_bundle(secret)
    assert verify_bundle_attestation(ee.bundle, secret) is True


# ── Test 2: Cross-Layer Refusal Integrity (Pipeline -> Evidence -> Stage B PR)


def test_cross_layer_refusal_integrity(tmp_path: Path):
    """Verify that a refused transformation maintains canonical identity throughout the entire pipeline:

    Pipeline Result ID -> EvidenceBundle RefusalRecord -> evidence.json -> Markdown Report -> Stage B PR
    """
    repo_dir = tmp_path / "integrity_repo"
    repo_dir.mkdir()

    # Initialize git repo for Stage B
    run_git(["init"], cwd=repo_dir)
    run_git(["config", "user.name", "Attestation Tester"], cwd=repo_dir)
    run_git(["config", "user.email", "tester@amstralift.io"], cwd=repo_dir)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_dir)

    target_file = repo_dir / "src/router.tsx"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    content = "import { createBrowserHistory } from 'history';\nimport { useHistory } from 'react-router';\n"
    target_file.write_text(content, encoding="utf-8")

    run_git(["add", "."], cwd=repo_dir)
    run_git(["commit", "-m", "initial commit"], cwd=repo_dir)
    head_sha = get_head_commit(repo_dir)

    # 1. Pipeline execution
    ee = EvidenceEngine(repo_name="IntegrityApp", repo_path=str(repo_dir), ecosystem="react")
    pipeline = TransformationDecisionPipeline(ee, repo_dir)
    results = pipeline.evaluate_and_execute([
        CandidateTransformation(
            id="cand_custom_history",
            adapter="react",
            rule_id="migrate-custom-history",
            file_path="src/router.tsx",
        )
    ])

    assert len(results) == 1
    res = results[0]
    assert res.is_refused is True
    refusal = res.refusal_record
    assert refusal is not None

    canonical_refusal_id = refusal.id
    canonical_rule_id = refusal.rule_id
    canonical_file_path = refusal.file_path
    canonical_category = refusal.category.value

    # 2. Check EvidenceBundle retention
    assert len(ee.bundle.transformations_refused) == 1
    bundle_refusal = ee.bundle.transformations_refused[0]
    assert bundle_refusal.id == canonical_refusal_id
    assert bundle_refusal.rule_id == canonical_rule_id
    assert bundle_refusal.file_path == canonical_file_path

    # 3. Check JSON export retention
    json_path = tmp_path / "evidence.json"
    ee.save_json(json_path)
    loaded_bundle = EvidenceBundle.model_validate(json.loads(json_path.read_text(encoding="utf-8")))
    assert loaded_bundle.transformations_refused[0].id == canonical_refusal_id

    # 4. Check Markdown Report retention
    md_report = render_markdown_report(ee.bundle)
    assert canonical_rule_id in md_report
    assert canonical_file_path in md_report
    assert canonical_category in md_report

    # 5. Check Stage B PR body retention
    secret = b"stage-b-integrity-secret"
    patch_text = "diff --git a/pkg.json b/pkg.json\n--- a/pkg.json\n+++ b/pkg.json\n"
    unsigned = UnsignedAdvisoryBundle(
        repo_url="https://github.com/org/repo",
        target_branch="master" if "master" in (run_git(["branch", "--show-current"], cwd=repo_dir).stdout or "") else "main",
        base_commit_sha=head_sha,
        head_commit_sha=head_sha,
        patch=patch_text,
        patch_sha256=compute_sha256(patch_text),
        changes=[
            DependencyChange(
                package_name="react",
                from_version="17.0.2",
                to_version="18.2.0",
                tier=DependencyTier.TIER_1_SAFE,
            )
        ],
        gate_summary=GateSummary(
            results=[
                GateResult(name="Build", command="npm run build", status=GateStatus.REQUIRED_PASSED, exit_code=0)
            ]
        ),
        refusals=[
            {
                "rule_id": bundle_refusal.rule_id,
                "file_path": bundle_refusal.file_path,
                "category": bundle_refusal.category.value,
                "reason": bundle_refusal.reason,
            }
        ],
    )
    signed = sign_bundle(unsigned, secret, ttl_seconds=3600)
    proposal = run_stage_b(signed, repo_dir, secret, dry_run=True)

    assert canonical_rule_id in proposal.body
    assert canonical_file_path in proposal.body
    assert canonical_category in proposal.body
    assert "Deliberately Refused Transformations" in proposal.body


# ── Test 3: Transactional Rollback on Transformation Exception


def test_transactional_rollback_on_apply_error(tmp_path: Path):
    """Verify that if an apply function throws an unexpected error, the file is rolled back byte-for-byte and marked REFUSED."""
    repo_dir = tmp_path / "rollback_repo"
    repo_dir.mkdir()

    broken_file = repo_dir / "broken.py"
    original_content = "def calculate():\n    return 42\n"
    broken_file.write_text(original_content, encoding="utf-8")
    original_hash = compute_file_sha256(str(broken_file))

    def failing_apply(p: Path):
        # Partially write corrupt code then raise
        p.write_text("CORRUPT SYNTAX!!!", encoding="utf-8")
        raise SyntaxError("Unexpected AST node parsing failure")

    ee = EvidenceEngine(repo_name="RollbackApp", repo_path=str(repo_dir), ecosystem="python")
    pipeline = TransformationDecisionPipeline(ee, repo_dir)

    results = pipeline.evaluate_and_execute([
        CandidateTransformation(
            id="cand_fail",
            adapter="python",
            rule_id="risky-transform",
            file_path="broken.py",
            apply_fn=failing_apply,
        )
    ])

    # 1. Result was classified as REFUSED
    assert len(results) == 1
    assert results[0].is_refused is True
    assert results[0].refusal_record.category == RefusalCategory.COMPILATION_RISK
    assert "Unexpected AST node parsing failure" in results[0].refusal_record.reason

    # 2. File was restored byte-for-byte
    assert compute_file_sha256(str(broken_file)) == original_hash
    assert broken_file.read_text(encoding="utf-8") == original_content


# ── Test 4: Verification Failure Dominates Partial Status


def test_verification_failure_dominates_status(tmp_path: Path):
    """Verify that a gate failure unconditionally overrides status to VERIFICATION_FAILED even with applied and refused transforms."""
    ee = EvidenceEngine(repo_name="GateFailApp", repo_path=str(tmp_path), ecosystem="angular")
    pipeline = TransformationDecisionPipeline(ee, tmp_path)

    # 1 applied, 1 refused
    ee.record_transformation("angular", "rule1", "f1.ts")
    ref = ee.refusal_engine.refuse("angular", "rule2", "f2.ts", RefusalCategory.UNSUPPORTED_PATTERN, "Unverifiable")
    ee.record_refusal(ref)

    # Build fails
    failing_gates = GateVerificationRecord(
        clean_install="passed",
        build="failed",
        tests="skipped",
        post_rescan="passed",
        manifest_diff="passed",
    )
    final_status = pipeline.reconcile_modernization_status(failing_gates)

    assert final_status == "VERIFICATION_FAILED"
    assert ee.bundle.overall_status == "VERIFICATION_FAILED"
