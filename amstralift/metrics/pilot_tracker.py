"""Pilot Exit Criteria and Governance Scorecard Evaluator (Section 16).

Implements exact thresholds across scheduled weekly pilot windows:
1. Scheduled run completion rate >= 95%
2. PRs passing required CI checks within 24h >= 90%
3. Flagged-incorrect dependency change rate < 2%
4. Duplicate PR count = 0
5. Stale PR count = 0 (>7 days without human reviewer action)
6. Qualitative audit trail sufficiency confirmation
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field


class ScheduledRunRecord(BaseModel):
    """Execution status of a scheduled CI job."""

    run_id: str
    window_id: str
    completed_without_infra_failure: bool
    failure_reason: str | None = None


class PilotPRRecord(BaseModel):
    """Pull request audit record tracked across pilot window."""

    pr_id: str
    window_id: str
    repo_name: str
    target_dependency: str
    created_at: datetime
    ci_status_at_24h: Literal["PASSED", "FAILED", "SKIPPED_UNAVAILABLE", "PENDING"]
    is_flagged_incorrect: bool = False
    is_duplicate: bool = False
    is_merged_or_closed: bool = False
    last_human_action_at: datetime | None = None  # Automated comments do NOT reset this

    def is_stale(self, reference_time: datetime | None = None) -> bool:
        """A PR is stale if open > 7 calendar days without a human reviewer action."""
        if self.is_merged_or_closed:
            return False
        now = reference_time or datetime.now(timezone.utc)
        action_time = self.last_human_action_at or self.created_at
        return (now - action_time) > timedelta(days=7)

    def is_ci_success_within_24h(self) -> bool:
        """Every required check must succeed within 24h. Failed/skipped/pending at deadline is failure."""
        return self.ci_status_at_24h == "PASSED"

    def is_eligible_for_ci_eval(self, reference_time: datetime | None = None) -> bool:
        """Eligible if completed or reached the 24-hour deadline within the window."""
        now = reference_time or datetime.now(timezone.utc)
        if self.ci_status_at_24h in ("PASSED", "FAILED", "SKIPPED_UNAVAILABLE"):
            return True
        # If still pending, only eligible if 24 hours have already passed
        return (now - self.created_at) >= timedelta(hours=24)


class PilotScorecard(BaseModel):
    """Scorecard evaluating pilot performance against Section 16 thresholds."""

    window_id: str
    measurement_time: datetime
    # 1. Completion Rate (>= 95%)
    total_scheduled_runs: int
    completed_runs: int
    completion_rate: float
    completion_rate_passed: bool

    # 2. PRs passing required CI within 24 hours (>= 90%)
    ci_eligible_prs: int
    ci_passed_prs: int
    ci_pass_rate: float
    ci_pass_rate_passed: bool

    # 3. Incorrect dependency change rate (< 2%)
    total_changes_proposed: int
    incorrect_changes_flagged: int
    incorrect_change_rate: float
    incorrect_rate_passed: bool

    # 4. Duplicate PR count (= 0)
    duplicate_pr_count: int
    duplicate_passed: bool

    # 5. Stale PR count (= 0)
    stale_pr_count: int
    stale_passed: bool

    # 6. Audit Trail Confirmation
    audit_trail_confirmed: bool

    # Overall Verdict
    all_criteria_met: bool
    rejection_reasons: list[str] = Field(default_factory=list)


class PilotTracker:
    """Computes and evaluates pilot metrics across windows."""

    @staticmethod
    def evaluate_window(
        window_id: str,
        scheduled_runs: list[ScheduledRunRecord],
        prs: list[PilotPRRecord],
        total_changes_proposed: int,
        audit_confirmed_by_security: bool = True,
        measurement_time: datetime | None = None,
    ) -> PilotScorecard:
        """Compute the official Section 16 scorecard for a pilot window."""
        now = measurement_time or datetime.now(timezone.utc)
        rejection_reasons: list[str] = []

        # 1. Scheduled run completion rate
        total_runs = len(scheduled_runs)
        completed_runs = sum(1 for r in scheduled_runs if r.completed_without_infra_failure)
        completion_rate = (completed_runs / total_runs) if total_runs > 0 else 1.0
        comp_passed = completion_rate >= 0.95
        if not comp_passed:
            rejection_reasons.append(
                f"Completion rate {completion_rate:.1%} is below the 95% threshold ({completed_runs}/{total_runs} runs)."
            )

        # 2. PRs passing required CI within 24h deadline
        eligible_prs = [p for p in prs if p.is_eligible_for_ci_eval(now)]
        ci_denom = len(eligible_prs)
        ci_num = sum(1 for p in eligible_prs if p.is_ci_success_within_24h())
        ci_rate = (ci_num / ci_denom) if ci_denom > 0 else 1.0
        ci_passed = ci_rate >= 0.90
        if not ci_passed:
            rejection_reasons.append(
                f"24-hour CI pass rate {ci_rate:.1%} is below the 90% threshold ({ci_num}/{ci_denom} PRs)."
            )

        # 3. Incorrect dependency change rate
        incorrect_count = sum(1 for p in prs if p.is_flagged_incorrect)
        changes_total = max(total_changes_proposed, len(prs))
        incorrect_rate = (incorrect_count / changes_total) if changes_total > 0 else 0.0
        incorrect_passed = incorrect_rate < 0.02
        if not incorrect_passed:
            rejection_reasons.append(
                f"Incorrect change rate {incorrect_rate:.1%} exceeds the 2% maximum threshold ({incorrect_count}/{changes_total})."
            )

        # 4. Duplicate PR count
        duplicates = sum(1 for p in prs if p.is_duplicate)
        duplicate_passed = duplicates == 0
        if not duplicate_passed:
            rejection_reasons.append(f"Found {duplicates} duplicate PR(s); threshold is strictly 0.")

        # 5. Stale PR count (open > 7d without human reviewer action)
        stale_count = sum(1 for p in prs if p.is_stale(now))
        stale_passed = stale_count == 0
        if not stale_passed:
            rejection_reasons.append(
                f"Found {stale_count} stale PR(s) (>7 days without human action); threshold is strictly 0."
            )

        # 6. Audit trail
        if not audit_confirmed_by_security:
            rejection_reasons.append("Audit trail sufficiency qualitative sign-off is missing.")

        all_passed = (
            comp_passed
            and ci_passed
            and incorrect_passed
            and duplicate_passed
            and stale_passed
            and audit_confirmed_by_security
        )

        return PilotScorecard(
            window_id=window_id,
            measurement_time=now,
            total_scheduled_runs=total_runs,
            completed_runs=completed_runs,
            completion_rate=completion_rate,
            completion_rate_passed=comp_passed,
            ci_eligible_prs=ci_denom,
            ci_passed_prs=ci_num,
            ci_pass_rate=ci_rate,
            ci_pass_rate_passed=ci_passed,
            total_changes_proposed=changes_total,
            incorrect_changes_flagged=incorrect_count,
            incorrect_change_rate=incorrect_rate,
            incorrect_rate_passed=incorrect_passed,
            duplicate_pr_count=duplicates,
            duplicate_passed=duplicate_passed,
            stale_pr_count=stale_count,
            stale_passed=stale_passed,
            audit_trail_confirmed=audit_confirmed_by_security,
            all_criteria_met=all_passed,
            rejection_reasons=rejection_reasons,
        )
