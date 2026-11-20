"""Unit tests for Policy-as-Code (P20).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
from pathlib import Path
from typer.testing import CliRunner

import pytest

from amstralift.cli import app
from amstralift.planning import ModernizationPlan, PhaseType, PlannedTransformation, TransformationStatus
from amstralift.planning.models import ExecutionPhase
from amstralift.policy import (
    EnterprisePolicy,
    GovernancePolicy,
    PolicyAction,
    PolicyEvaluator,
    PolicySeverity,
    SecurityPolicy,
    SemanticPolicy,
    TransformationPolicy,
    load_policy,
)
from amstralift.transforms.models import TransformationRisk


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def base_plan(tmp_path: Path) -> ModernizationPlan:
    phase_0 = ExecutionPhase(
        phase_index=0,
        name="Framework Migration",
        phase_type=PhaseType.FRAMEWORK_MIGRATION,
        description="Core framework migration",
        transformations=[
            PlannedTransformation(
                id="angular:standalone",
                name="Standalone Components",
                status=TransformationStatus.SAFE,
                risk=TransformationRisk.LOW,
                affected_file_patterns=["src/**/*.ts"],
            ),
            PlannedTransformation(
                id="angular:signals",
                name="Signals Reactive Architecture",
                status=TransformationStatus.RISKY,
                risk=TransformationRisk.HIGH,
                affected_file_patterns=["src/**/*.ts"],
            ),
        ],
    )
    return ModernizationPlan(
        repo_path=str(tmp_path),
        ecosystem="angular",
        current_version="17.0.0",
        target_version="v18",
        target_major=18,
        total_transformations=2,
        safe_count=1,
        risky_count=1,
        phases=[phase_0],
    )


def test_policy_yaml_roundtrip(tmp_path: Path):
    policy = EnterprisePolicy(
        name="acme-corp-policy",
        description="Acme enterprise standards",
        governance=GovernancePolicy(allow_major_upgrade=False, max_major_jump=1),
        transformations=TransformationPolicy(disallowed_transformations=["legacy-rule"]),
    )
    yaml_str = policy.to_yaml()
    assert "acme-corp-policy" in yaml_str
    assert "allow_major_upgrade: false" in yaml_str

    loaded = EnterprisePolicy.from_yaml(yaml_str)
    assert loaded.name == "acme-corp-policy"
    assert loaded.governance.allow_major_upgrade is False
    assert loaded.governance.max_major_jump == 1
    assert "legacy-rule" in loaded.transformations.disallowed_transformations

    file_path = tmp_path / "policy.yaml"
    policy.save(file_path)
    from_file = EnterprisePolicy.load(file_path)
    assert from_file.name == "acme-corp-policy"


def test_governance_disallow_major_upgrade(base_plan: ModernizationPlan):
    policy = EnterprisePolicy(
        governance=GovernancePolicy(allow_major_upgrade=False),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.rule_name == "governance.allow_major_upgrade" for v in report.blocking_violations)


def test_governance_max_major_jump_exceeded(base_plan: ModernizationPlan):
    # Base plan is 17 -> 18 (+1 major). If we set current to 15, jump is +3.
    base_plan.current_version = "15.0.0"
    policy = EnterprisePolicy(
        governance=GovernancePolicy(max_major_jump=1),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.rule_name == "governance.max_major_jump" for v in report.blocking_violations)


def test_transformations_disallowed_rule_blocks(base_plan: ModernizationPlan):
    policy = EnterprisePolicy(
        transformations=TransformationPolicy(disallowed_transformations=["angular:signals"]),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.target == "angular:signals" for v in report.blocking_violations)


def test_transformations_high_risk_requires_review(base_plan: ModernizationPlan):
    # angular:signals has HIGH risk
    policy = EnterprisePolicy(
        transformations=TransformationPolicy(allow_high_risk=False),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is True  # Allowed but flagged for review
    assert report.overall_action == PolicyAction.REVIEW
    assert any(v.rule_name == "transformations.allow_high_risk" for v in report.review_violations)


def test_transformations_critical_risk_blocks(base_plan: ModernizationPlan):
    # Add a critical risk transformation
    crit_t = PlannedTransformation(
        id="angular:experimental-compiler",
        name="Experimental Compiler",
        risk=TransformationRisk.CRITICAL,
        status=TransformationStatus.RISKY,
    )
    base_plan.phases[0].transformations.append(crit_t)

    policy = EnterprisePolicy(
        transformations=TransformationPolicy(allow_critical_risk=False),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.rule_name == "transformations.allow_critical_risk" for v in report.blocking_violations)


def test_transformations_required_missing_blocks(base_plan: ModernizationPlan):
    policy = EnterprisePolicy(
        transformations=TransformationPolicy(
            required_transformations=["angular:control-flow"]
        ),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.rule_name == "transformations.required_transformations" for v in report.blocking_violations)


def test_security_cve_policy_enforcement(base_plan: ModernizationPlan):
    base_plan.metadata["security_findings"] = [
        {"id": "GHSA-1234-5678", "severity": "CRITICAL"},
        {"id": "GHSA-9999-0000", "severity": "HIGH"},
    ]
    policy = EnterprisePolicy(
        security=SecurityPolicy(critical_cve=PolicyAction.BLOCK, high_cve=PolicyAction.REVIEW),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.target == "GHSA-1234-5678" for v in report.blocking_violations)
    assert any(v.target == "GHSA-9999-0000" for v in report.review_violations)


def test_semantic_api_removal_blocks(base_plan: ModernizationPlan):
    base_plan.metadata["semantic_findings"] = {
        "removed_symbols": ["UserService", "AuthInterceptor"],
    }
    policy = EnterprisePolicy(
        semantic=SemanticPolicy(public_api_removal=PolicyAction.BLOCK, max_removed_symbols=0),
    )
    report = PolicyEvaluator.evaluate(base_plan, policy)
    assert report.allowed is False
    assert report.overall_action == PolicyAction.BLOCK
    assert any(v.rule_name == "semantic.public_api_removal" for v in report.blocking_violations)


def test_cli_policy_init(runner: CliRunner, tmp_path: Path):
    target_yaml = tmp_path / "policy.yaml"
    res = runner.invoke(app, ["policy", "init", "-o", str(target_yaml)])
    assert res.exit_code == 0
    assert target_yaml.exists()
    assert "Enterprise policy template created" in res.stdout


def test_cli_policy_check_compliant(runner: CliRunner):
    res = runner.invoke(app, ["policy", "check", "--ecosystem", "angular"])
    assert res.exit_code == 0
    assert "COMPLIANT (ALLOW)" in res.stdout
    assert "Zero policy violations detected" in res.stdout


def test_cli_policy_check_blocking_fails(runner: CliRunner, tmp_path: Path):
    # Write a policy that disallows major upgrades
    pol_file = tmp_path / "strict-policy.yaml"
    policy = EnterprisePolicy(
        name="no-major-upgrades",
        governance=GovernancePolicy(allow_major_upgrade=False),
    )
    policy.save(pol_file)

    # Create mock package.json with version 17
    (tmp_path / "package.json").write_text('{"name": "test", "dependencies": {"@angular/core": "^17.0.0"}}', encoding="utf-8")

    res = runner.invoke(app, ["policy", "check", "--repo", str(tmp_path), "--policy", str(pol_file), "--target", "18"])
    assert res.exit_code == 1
    assert "NON-COMPLIANT (BLOCK)" in res.stdout
    assert "governance.allow_major_upgrade" in res.stdout


def test_cli_policy_check_json(runner: CliRunner):
    res = runner.invoke(app, ["policy", "check", "--ecosystem", "angular", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["allowed"] is True
    assert data["overall_action"] == "allow"
    assert len(data["passed_checks"]) > 0
