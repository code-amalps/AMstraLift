"""Unit and integration tests for Phase 2A Governance Policies (Angular LTS & Python Runtime)."""

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from amstralift.adapters.angular import AngularAdapter
from amstralift.governance.angular_lts import (
    AngularLTSConfig,
    AngularLTSGovernance,
    PolicyError,
)
from amstralift.governance.python_runtime import (
    PythonRuntimeConfig,
    PythonRuntimeGovernance,
    PythonRuntimeRelease,
)

# ==========================================
# 1. Angular LTS Policy Tests (Section 5)
# ==========================================


def test_angular_lts_load_and_evaluate_fresh(tmp_path: Path):
    policy_file = tmp_path / "angular-lts-policy.yaml"
    today = date(2026, 9, 27)
    future_date = today + timedelta(days=30)
    past_date = today - timedelta(days=30)

    policy_content = {
        "current_lts_majors": [18, 19],
        "last_verified": past_date.isoformat(),
        "next_review_by": future_date.isoformat(),
        "verified_by": "Frontend Platform Lead",
    }
    policy_file.write_text(yaml.dump(policy_content), encoding="utf-8")

    config = AngularLTSGovernance.load_config(policy_file)
    assert config.current_lts_majors == [18, 19]
    assert config.verified_by == "Frontend Platform Lead"

    decision = AngularLTSGovernance.evaluate(config, reference_date=today)
    assert not decision.is_stale
    assert not decision.use_latest_fallback
    assert decision.target_major == 19
    assert decision.escalate_to is None
    assert "Frontend Platform Lead" in decision.reason


def test_angular_lts_evaluate_stale_policy(tmp_path: Path):
    today = date(2026, 9, 27)
    past_review_by = today - timedelta(days=5)

    config = AngularLTSConfig(
        current_lts_majors=[17],
        last_verified=today - timedelta(days=60),
        next_review_by=past_review_by,
        verified_by="Frontend Platform Lead",
    )

    decision = AngularLTSGovernance.evaluate(config, reference_date=today)
    assert decision.is_stale
    assert decision.use_latest_fallback
    assert decision.target_major is None
    assert decision.escalate_to == "Frontend Platform Lead"
    assert "passed" in decision.reason
    assert "Falling back to latest stable only" in decision.reason


def test_angular_lts_missing_config():
    decision = AngularLTSGovernance.evaluate(None)
    assert decision.is_stale
    assert decision.use_latest_fallback
    assert decision.target_major is None


def test_angular_lts_invalid_yaml(tmp_path: Path):
    invalid_file = tmp_path / "bad-policy.yaml"
    invalid_file.write_text("not_a_valid: [yaml", encoding="utf-8")

    with pytest.raises(PolicyError, match="Failed to parse"):
        AngularLTSGovernance.load_config(invalid_file)

    missing_file = tmp_path / "missing.yaml"
    with pytest.raises(PolicyError, match="not found"):
        AngularLTSGovernance.load_config(missing_file)


def test_angular_adapter_with_lts_policy(tmp_path: Path):
    """Verify AngularAdapter respects LTS major when LTS policy is provided."""
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        '{"dependencies": {"@angular/core": "^17.0.0", "tslib": "^2.6.0"}}',
        encoding="utf-8",
    )

    lts_config = AngularLTSConfig(
        current_lts_majors=[18],
        last_verified=date(2026, 8, 1),
        next_review_by=date(2026, 12, 1),
        verified_by="Frontend Platform Lead",
    )

    adapter = AngularAdapter(lts_config=lts_config)

    def mock_fetch(pkg: str, target_major: int | None = None):
        if target_major == 18:
            return "18.2.13"
        return "19.0.0"

    with patch.object(adapter, "fetch_latest_version", side_effect=mock_fetch):
        candidates = adapter.discover_candidates(tmp_path)

    angular_candidate = next(c for c in candidates if c.package_name == "@angular/core")
    # Must target major 18 from LTS policy, not 19.0.0
    assert angular_candidate.to_version == "^18.2.13"
    assert "Targeting active LTS major 18" in angular_candidate.rationale


# ==========================================
# 2. Python Runtime Default Tests (Section 6)
# ==========================================


@pytest.fixture
def sample_python_runtime_config() -> PythonRuntimeConfig:
    today = date(2026, 9, 27)
    return PythonRuntimeConfig(
        approved_base_images=[
            "docker.internal.org/python:3.11-slim",
            "docker.internal.org/python:3.12-slim",
        ],
        known_releases=[
            PythonRuntimeRelease(minor="3.11", release_date=today - timedelta(days=500)),
            PythonRuntimeRelease(minor="3.12", release_date=today - timedelta(days=200)),  # > 90d, in images
            PythonRuntimeRelease(minor="3.13", release_date=today - timedelta(days=30)),  # < 90d, not in images
        ],
        minimum_stability_days=90,
        last_verified=today - timedelta(days=30),
        next_review_by=today + timedelta(days=60),
        verified_by="Security Architecture Team",
        owner="Platform & Security Team",
    )


def test_python_runtime_repo_override_precedence(sample_python_runtime_config):
    """Precedence rule: repo declaration always wins."""
    decision = PythonRuntimeGovernance.evaluate(
        config=sample_python_runtime_config,
        repo_requires_python=">=3.10,<3.13",
    )
    assert decision.action == "USE_REPO_OVERRIDE"
    assert decision.resolved_runtime == ">=3.10,<3.13"
    assert not decision.flagged_for_manual_triage
    assert "Precedence rule applies" in decision.reason


def test_python_runtime_apply_default(sample_python_runtime_config):
    """When repo has no requirement, selects newest minor stable >= 90d and in approved base images."""
    decision = PythonRuntimeGovernance.evaluate(
        config=sample_python_runtime_config,
        repo_requires_python=None,
    )
    assert decision.action == "APPLY_DEFAULT"
    assert decision.resolved_runtime == ">=3.12"
    assert not decision.flagged_for_manual_triage
    assert "Selected governed default Python 3.12" in decision.reason


def test_python_runtime_no_match_safe_fallback():
    """Section 6: When no version satisfies both conditions simultaneously, make NO change and flag manual triage."""
    today = date(2026, 9, 27)
    config = PythonRuntimeConfig(
        approved_base_images=["docker.internal.org/python:3.13-slim"],  # Only 3.13 approved in image
        known_releases=[
            # 3.12 is stable for 200d but NOT in approved base image
            PythonRuntimeRelease(minor="3.12", release_date=today - timedelta(days=200)),
            # 3.13 is in approved base image but ONLY 20d old (< 90d required)
            PythonRuntimeRelease(minor="3.13", release_date=today - timedelta(days=20)),
        ],
        minimum_stability_days=90,
        last_verified=today - timedelta(days=30),
        next_review_by=today + timedelta(days=60),
        verified_by="Security Architecture Team",
    )

    current_runtime = "3.10"
    decision = PythonRuntimeGovernance.evaluate(
        config=config,
        repo_requires_python=None,
        current_runtime=current_runtime,
        reference_date=today,
    )

    # Invariant: NO automatic change, left untouched, flagged for manual triage
    assert decision.action == "NO_CHANGE_FLAG_MANUAL_TRIAGE"
    assert decision.resolved_runtime == "3.10"
    assert decision.flagged_for_manual_triage
    assert "Pipeline makes NO automatic runtime change" in decision.reason
    assert "manual platform-team triage" in decision.reason


def test_python_runtime_stale_policy_flags_manual_triage(sample_python_runtime_config):
    """When policy date expires, fails safely to manual triage rather than using stale policy."""
    today = date(2026, 9, 27)
    past_review = today - timedelta(days=1)
    sample_python_runtime_config.next_review_by = past_review

    decision = PythonRuntimeGovernance.evaluate(
        config=sample_python_runtime_config,
        repo_requires_python=None,
        current_runtime="3.11",
        reference_date=today,
    )

    assert decision.action == "NO_CHANGE_FLAG_MANUAL_TRIAGE"
    assert decision.is_stale
    assert decision.flagged_for_manual_triage
    assert decision.resolved_runtime == "3.11"
    assert "Stale policy cannot be applied silently" in decision.reason
