"""Unit and lifecycle tests for Phase 2C (Vulnerability & EOL Exceptions, Pilot Metrics)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from amstralift.governance.eol_escalation import (
    EOLEscalationManager,
    EOLSlaStatus,
)
from amstralift.governance.vulnerabilities import (
    ExceptionStatus,
    VulnerabilityManager,
    VulnerabilitySeverity,
)
from amstralift.metrics.pilot_tracker import (
    PilotPRRecord,
    PilotTracker,
    ScheduledRunRecord,
)

# ====================================================
# 1. Vulnerability Exception Lifecycle (Section 10)
# ====================================================


def test_vulnerability_exception_creation_and_expiry():
    mgr = VulnerabilityManager()
    start_date = date(2026, 9, 1)

    # 1. Critical CVE -> 30 days expiry
    crit_exc = mgr.create_exception(
        cve_id="CVE-2026-9999",
        package_name="msal-angular",
        vulnerable_version="3.0.0",
        severity=VulnerabilitySeverity.CRITICAL,
        security_approver="Chief Security Officer",
        engineering_approver="Engineering VP",
        compensating_controls="Network access to auth endpoints restricted to internal IP range via WAF.",
        created_at=start_date,
    )
    assert crit_exc.expiry_date == date(2026, 10, 1)  # 30 days
    assert crit_exc.is_active(reference_date=date(2026, 9, 20))
    assert not crit_exc.is_active(reference_date=date(2026, 10, 2))

    # 2. Check coverage helper
    covered = mgr.check_coverage("CVE-2026-9999", "msal-angular", "3.0.0", reference_date=date(2026, 9, 15))
    assert covered is not None
    assert covered.exception_id == crit_exc.exception_id

    # Expired check
    expired_check = mgr.check_coverage("CVE-2026-9999", "msal-angular", "3.0.0", reference_date=date(2026, 10, 5))
    assert expired_check is None
    assert crit_exc.status == ExceptionStatus.EXPIRED


def test_vulnerability_exception_mandatory_fields_rejection():
    mgr = VulnerabilityManager()
    # Missing compensating controls
    with pytest.raises(ValueError, match="Compensating controls are mandatory"):
        mgr.create_exception(
            cve_id="CVE-2026-1111",
            package_name="lodash",
            vulnerable_version="4.17.20",
            severity=VulnerabilitySeverity.HIGH,
            security_approver="Security Lead",
            engineering_approver="Lead Dev",
            compensating_controls="none",
        )

    # Missing joint approvers
    with pytest.raises(ValueError, match="Joint named sign-off"):
        mgr.create_exception(
            cve_id="CVE-2026-1111",
            package_name="lodash",
            vulnerable_version="4.17.20",
            severity=VulnerabilitySeverity.HIGH,
            security_approver="",
            engineering_approver="Lead Dev",
            compensating_controls="Compensating control details here.",
        )


def test_vulnerability_resolution():
    mgr = VulnerabilityManager()
    exc = mgr.create_exception(
        cve_id="CVE-2026-2222",
        package_name="requests",
        vulnerable_version="2.28.0",
        severity=VulnerabilitySeverity.MEDIUM,
        security_approver="Sec",
        engineering_approver="Eng",
        compensating_controls="Isolated VPC, external calls disallowed.",
    )
    assert exc.status == ExceptionStatus.ACTIVE

    mgr.resolve_exception(exc.exception_id, "Upgraded to 2.32.3 in production release v2.4")
    assert exc.status == ExceptionStatus.RESOLVED
    assert not exc.is_active()


# ====================================================
# 2. Runtime EOL Escalation Lifecycle (Section 11)
# ====================================================


def test_eol_escalation_slas_and_formal_exception():
    mgr = EOLEscalationManager()
    detected = date(2026, 9, 1)  # Tuesday

    rec = mgr.record_finding(
        runtime_name="Python 3.8",
        repo_name="org/legacy-billing",
        secondary_escalation_target="Engineering Director",
        detected_date=detected,
    )
    assert rec.status == EOLSlaStatus.PENDING_ACK

    # Check day 1 (Wed) -> Pending
    status, msg = mgr.evaluate_sla(rec.record_id, reference_date=date(2026, 9, 2))
    assert status == EOLSlaStatus.PENDING_ACK

    # Check day 4 (Friday, > 2 business days) -> OVERDUE_ACK
    status, msg = mgr.evaluate_sla(rec.record_id, reference_date=date(2026, 9, 5))
    assert status == EOLSlaStatus.OVERDUE_ACK
    assert "Engineering Director" in msg

    # Acknowledge on day 5
    mgr.acknowledge_finding(rec.record_id, acknowledged_by="Tech Lead", ack_date=date(2026, 9, 5))
    assert rec.status == EOLSlaStatus.IN_REMEDIATION

    # Check day 35 (> 30 calendar days) -> OVERDUE_REMEDIATION
    status, msg = mgr.evaluate_sla(rec.record_id, reference_date=date(2026, 10, 8))
    assert status == EOLSlaStatus.OVERDUE_REMEDIATION
    assert "Must convert to formal exception" in msg

    # Convert to formal documented exception
    exc = mgr.grant_formal_exception(
        record_id=rec.record_id,
        security_approver="Chief Information Security Officer",
        engineering_approver="VP of Infrastructure",
        compensating_controls="Service isolated behind internal envoy proxy; container image read-only.",
        duration_days=60,
        approved_date=date(2026, 10, 8),
    )
    assert rec.status == EOLSlaStatus.FORMAL_EXCEPTION
    assert exc.is_valid(reference_date=date(2026, 10, 15))


# ====================================================
# 3. Pilot Scorecard & Evaluation (Section 16)
# ====================================================


def test_pilot_tracker_all_criteria_met():
    """Verify scorecard when all Section 16 thresholds are satisfied."""
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    scheduled_runs = [
        ScheduledRunRecord(run_id=f"r-{i}", window_id="w-1", completed_without_infra_failure=True) for i in range(100)
    ]

    prs = [
        PilotPRRecord(
            pr_id=f"pr-{i}",
            window_id="w-1",
            repo_name="org/repo-a",
            target_dependency=f"pkg-{i}",
            created_at=now - timedelta(hours=30),
            ci_status_at_24h="PASSED",
            is_flagged_incorrect=False,
            is_duplicate=False,
            last_human_action_at=now - timedelta(days=2),  # active human engagement
        )
        for i in range(20)
    ]

    scorecard = PilotTracker.evaluate_window(
        window_id="w-1",
        scheduled_runs=scheduled_runs,
        prs=prs,
        total_changes_proposed=20,
        audit_confirmed_by_security=True,
        measurement_time=now,
    )

    assert scorecard.all_criteria_met
    assert scorecard.completion_rate == 1.0
    assert scorecard.ci_pass_rate == 1.0
    assert scorecard.incorrect_change_rate == 0.0
    assert scorecard.duplicate_pr_count == 0
    assert scorecard.stale_pr_count == 0
    assert len(scorecard.rejection_reasons) == 0


def test_pilot_tracker_threshold_failures():
    """Verify that failing any Section 16 threshold correctly flags rejection reasons."""
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

    # 1. Completion rate fails (< 95%)
    scheduled_runs = [
        ScheduledRunRecord(run_id="r-1", window_id="w-2", completed_without_infra_failure=True),
        ScheduledRunRecord(
            run_id="r-2", window_id="w-2", completed_without_infra_failure=False, failure_reason="Runner crash"
        ),
    ]  # 50% completion

    prs = [
        # CI failed within 24h
        PilotPRRecord(
            pr_id="pr-1",
            window_id="w-2",
            repo_name="org/repo-b",
            target_dependency="pkg-1",
            created_at=now - timedelta(hours=30),
            ci_status_at_24h="FAILED",
            is_flagged_incorrect=True,  # Incorrect change
            is_duplicate=True,  # Duplicate PR
        ),
        # Stale PR (> 7 days without human action)
        PilotPRRecord(
            pr_id="pr-2",
            window_id="w-2",
            repo_name="org/repo-b",
            target_dependency="pkg-2",
            created_at=now - timedelta(days=10),
            ci_status_at_24h="PASSED",
            last_human_action_at=now - timedelta(days=9),  # > 7 days ago!
        ),
    ]

    scorecard = PilotTracker.evaluate_window(
        window_id="w-2",
        scheduled_runs=scheduled_runs,
        prs=prs,
        total_changes_proposed=2,
        audit_confirmed_by_security=False,  # Qualitative sign-off missing
        measurement_time=now,
    )

    assert not scorecard.all_criteria_met
    assert not scorecard.completion_rate_passed
    assert not scorecard.ci_pass_rate_passed
    assert not scorecard.incorrect_rate_passed
    assert not scorecard.duplicate_passed
    assert not scorecard.stale_passed
    assert not scorecard.audit_trail_confirmed

    # Verify that specific rejections were recorded
    reasons_text = " ".join(scorecard.rejection_reasons)
    assert "Completion rate" in reasons_text
    assert "24-hour CI pass rate" in reasons_text
    assert "Incorrect change rate" in reasons_text
    assert "duplicate PR" in reasons_text
    assert "stale PR" in reasons_text
    assert "Audit trail sufficiency" in reasons_text
