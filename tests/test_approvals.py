"""Unit tests for Human Approval Workflow & Governance Engine (P23).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from amstralift.approvals import (
    ApprovalEvaluator,
    ApprovalQueue,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalTrigger,
    ApprovalTriggerType,
    HumanDecision,
    RiskTier,
)
from amstralift.cli import app
from amstralift.planning.models import (
    ExecutionPhase,
    ModernizationPlan,
    PhaseType,
    PlannedTransformation,
    TransformationCategory,
    TransformationRisk,
)

runner = CliRunner()


@pytest.fixture
def sample_plan() -> ModernizationPlan:
    """Create a minimal modernization plan for testing."""
    return ModernizationPlan(
        plan_id="test-plan-001",
        repo_path="/path/to/mock",
        ecosystem="angular",
        current_version="15.0.0",
        target_version="18.0.0",
        phases=[
            ExecutionPhase(
                phase_index=0,
                name="AST Modernization",
                phase_type=PhaseType.TEMPLATE_MIGRATION,
                transformations=[
                    PlannedTransformation(
                        id="angular:control-flow",
                        name="Migrate control flow",
                        category=TransformationCategory.AST,
                        risk=TransformationRisk.LOW,
                    )
                ],
            )
        ],
    )


# ==============================================================================
# Model & Signature Tests
# ==============================================================================


def test_human_decision_signature_verification():
    """Verify cryptographic sealing and tampering detection of approval decisions."""
    decision = HumanDecision(
        decision=ApprovalStatus.APPROVED,
        reviewer_name="Alex Vance",
        reviewer_email="alex@enterprise.corp",
        rationale="Reviewed and verified AST migrations in isolated staging environment.",
    )
    secret = "corporate-audit-hmac-key"
    sig = decision.generate_signature(secret)
    decision.signature = sig

    assert decision.verify_signature(secret) is True
    # Tamper with rationale
    decision.rationale = "Tampered unauthorized text"
    assert decision.verify_signature(secret) is False


def test_approval_request_model():
    """Verify approval request schema and attributes."""
    req = ApprovalRequest(
        plan_id="plan-123",
        repo_name="core-service",
        repo_path="/path/to/core",
        ecosystem="dotnet",
        current_version="6.0",
        target_version="8.0",
        status=ApprovalStatus.PENDING,
        risk_tier=RiskTier.REVIEW,
    )
    assert req.repo_name == "core-service"
    assert req.status == ApprovalStatus.PENDING
    assert req.author == "Amal P S"


# ==============================================================================
# Evaluator Tests
# ==============================================================================


def test_evaluator_auto_approved():
    """Verify SAFE tier with 0 triggers results in AUTO_APPROVED."""
    safe_plan = ModernizationPlan(
        plan_id="safe-plan",
        repo_path="/path/to/safe",
        ecosystem="angular",
        current_version="18.0.0",
        target_version="18.2.0",
        phases=[
            ExecutionPhase(
                phase_index=0,
                name="AST Modernization",
                phase_type=PhaseType.TEMPLATE_MIGRATION,
                transformations=[
                    PlannedTransformation(
                        id="angular:self-closing",
                        name="Self closing tags",
                        category=TransformationCategory.AST,
                        risk=TransformationRisk.LOW,
                    )
                ],
            )
        ],
    )
    req = ApprovalEvaluator.evaluate(safe_plan)
    assert req.risk_tier == RiskTier.SAFE
    assert req.status == ApprovalStatus.AUTO_APPROVED
    assert len(req.triggers) == 0


def test_evaluator_major_jump_trigger(sample_plan: ModernizationPlan):
    """Verify major framework jump >= 2 triggers REVIEW and PENDING status."""
    req = ApprovalEvaluator.evaluate(sample_plan)
    assert req.risk_tier == RiskTier.REVIEW
    assert req.status == ApprovalStatus.PENDING
    assert any(t.trigger_type == ApprovalTriggerType.MAJOR_VERSION_JUMP for t in req.triggers)


def test_evaluator_api_breaking_contract():
    """Verify Gate 6 semantic route removal triggers critical breaking API trigger."""
    plan = ModernizationPlan(
        plan_id="breaking-plan",
        repo_path="/path/to/breaking",
        ecosystem="angular",
        current_version="18.0.0",
        target_version="18.1.0",
        metadata={
            "semantic_diff": {
                "removed_routes": ["/api/v1/legacy-auth"],
                "removed_symbols": ["LegacyAuthModule"],
            }
        },
    )
    req = ApprovalEvaluator.evaluate(plan)
    assert req.risk_tier == RiskTier.REVIEW
    assert any(t.trigger_type == ApprovalTriggerType.API_BREAKING_CHANGE for t in req.triggers)


def test_evaluator_database_migration_trigger(tmp_path: Path):
    """Verify detection of schema migration files triggers database migration review."""
    repo_dir = tmp_path / "repo-db"
    repo_dir.mkdir()
    mig_dir = repo_dir / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_initial_schema.sql").write_text("CREATE TABLE users (id INT);", encoding="utf-8")

    plan = ModernizationPlan(
        plan_id="db-plan",
        repo_path=str(repo_dir),
        ecosystem="python",
        current_version="3.11",
        target_version="3.12",
    )
    req = ApprovalEvaluator.evaluate(plan, repo_path=repo_dir)
    assert any(t.trigger_type == ApprovalTriggerType.DATABASE_MIGRATION for t in req.triggers)


def test_evaluator_policy_blocking_trigger():
    """Verify policy blocking violations classify risk as BLOCK and status as BLOCKED."""
    plan = ModernizationPlan(
        plan_id="blocked-plan",
        repo_path="/path/to/blocked",
        ecosystem="dotnet",
        current_version="6.0",
        target_version="9.0",
    )
    mock_violation = MagicMock()
    mock_violation.message = "Exceeds enterprise maximum major jump threshold (+2)"
    mock_report = MagicMock()
    mock_report.blocking_violations = [mock_violation]
    mock_report.review_violations = []

    req = ApprovalEvaluator.evaluate(plan, policy_report=mock_report)
    assert req.risk_tier == RiskTier.BLOCK
    assert req.status == ApprovalStatus.BLOCKED


# ==============================================================================
# Queue Tests
# ==============================================================================


def test_queue_lifecycle(tmp_path: Path, sample_plan: ModernizationPlan):
    """Verify submit, list, get, approve, and reject operations in ApprovalQueue."""
    req = ApprovalEvaluator.evaluate(sample_plan)
    ApprovalQueue.submit(req, base_dir=tmp_path)

    # List
    all_reqs = ApprovalQueue.list_requests(base_dir=tmp_path)
    assert len(all_reqs) == 1
    assert all_reqs[0].request_id == req.request_id

    # Filter
    pending_reqs = ApprovalQueue.list_requests(status=ApprovalStatus.PENDING, base_dir=tmp_path)
    assert len(pending_reqs) == 1
    approved_reqs = ApprovalQueue.list_requests(status=ApprovalStatus.APPROVED, base_dir=tmp_path)
    assert len(approved_reqs) == 0

    # Approve
    approved = ApprovalQueue.approve(
        request_id=req.request_id,
        reviewer_name="Sam Fisher",
        reviewer_email="sam@corp.com",
        rationale="Approved for deployment in Sprint 44.",
        base_dir=tmp_path,
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decision is not None
    assert approved.decision.verify_signature() is True

    # Reject another request
    req2 = ApprovalRequest(
        plan_id="plan-002",
        repo_name="svc-b",
        repo_path="/path/b",
        status=ApprovalStatus.PENDING,
    )
    ApprovalQueue.submit(req2, base_dir=tmp_path)
    rejected = ApprovalQueue.reject(
        request_id=req2.request_id,
        reviewer_name="Sam Fisher",
        reviewer_email="sam@corp.com",
        rationale="Rejected due to pending API deprecations.",
        base_dir=tmp_path,
    )
    assert rejected.status == ApprovalStatus.REJECTED


# ==============================================================================
# CLI Tests
# ==============================================================================


def test_cli_approve_list(tmp_path: Path):
    """Verify amstralift approve list command."""
    res = runner.invoke(app, ["approve", "list", "--dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Human Approval Queue" in res.stdout


def test_cli_approve_check_and_submit(tmp_path: Path):
    """Verify amstralift approve check --submit command."""
    # Scaffold mock Angular repository
    repo_dir = tmp_path / "mock-angular"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "my-portal",
                "dependencies": {
                    "@angular/core": "15.0.0",
                },
            }
        ),
        encoding="utf-8",
    )

    res = runner.invoke(
        app,
        ["approve", "check", "--repo", str(repo_dir), "--target", "18", "--submit"],
    )
    assert res.exit_code == 0
    assert "Modernization Approval Evaluation" in res.stdout
    assert "PENDING" in res.stdout

    # Verify request was persisted to queue
    requests = ApprovalQueue.list_requests(base_dir=repo_dir)
    assert len(requests) == 1
    req_id = requests[0].request_id

    # Test approve show
    res_show = runner.invoke(app, ["approve", "show", req_id, "--dir", str(repo_dir)])
    assert res_show.exit_code == 0
    assert "Approval Request Details" in res_show.stdout

    # Test approve accept
    res_accept = runner.invoke(
        app,
        [
            "approve",
            "accept",
            req_id,
            "--reviewer",
            "Diana Prince <diana@themyscira.gov>",
            "--rationale",
            "Verified in staging integration test run.",
            "--dir",
            str(repo_dir),
        ],
    )
    assert res_accept.exit_code == 0
    assert "APPROVED successfully" in res_accept.stdout

    # Verify status updated
    updated = ApprovalQueue.get(req_id, base_dir=repo_dir)
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.decision.reviewer_name == "Diana Prince"


def test_cli_approve_reject(tmp_path: Path):
    """Verify amstralift approve reject command."""
    req = ApprovalRequest(
        plan_id="plan-reject",
        repo_name="reject-repo",
        repo_path="/path/reject",
        status=ApprovalStatus.PENDING,
    )
    ApprovalQueue.submit(req, base_dir=tmp_path)

    res = runner.invoke(
        app,
        [
            "approve",
            "reject",
            req.request_id,
            "--reviewer",
            "Bruce Wayne <bruce@wayne.corp>",
            "--rationale",
            "High breaking change density.",
            "--dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0
    assert "REJECTED" in res.stdout
