"""Runtime EOL Escalation and Missed-SLA Governance (Section 11).

- Acknowledgment SLA: 2 business days.
- Remediation SLA: 30 calendar days.
- Missed acknowledgment auto-escalates to designated secondary.
- Missed remediation converts into a formal documented exception
  with joint sign-off, compensating controls, and explicit expiry.
"""

from datetime import date, timedelta
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class EOLSlaStatus(str, Enum):
    PENDING_ACK = "PENDING_ACK"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OVERDUE_ACK = "OVERDUE_ACK"
    IN_REMEDIATION = "IN_REMEDIATION"
    OVERDUE_REMEDIATION = "OVERDUE_REMEDIATION"
    FORMAL_EXCEPTION = "FORMAL_EXCEPTION"
    RESOLVED = "RESOLVED"


class EOLFormalException(BaseModel):
    """Formal exception when 30-day remediation SLA cannot be met."""

    exception_id: str = Field(default_factory=lambda: f"eol-exc-{uuid4().hex[:8]}")
    security_approver: str
    engineering_approver: str
    compensating_controls: str
    approved_date: date
    expiry_date: date
    next_review_date: date

    def is_valid(self, reference_date: date | None = None) -> bool:
        today = reference_date or date.today()
        return today <= self.expiry_date and today <= self.next_review_date


class EOLEscalationRecord(BaseModel):
    """Tracking record for an EOL runtime finding across its lifecycle."""

    record_id: str = Field(default_factory=lambda: f"eol-{uuid4().hex[:8]}")
    runtime_name: str  # e.g. "Angular 14", "Python 3.8", ".NET 6"
    repo_name: str
    first_detected_date: date
    acknowledged_date: date | None = None
    acknowledged_by: str | None = None
    secondary_escalation_target: str  # Engineering Manager or Security Lead
    status: EOLSlaStatus = EOLSlaStatus.PENDING_ACK
    formal_exception: EOLFormalException | None = None
    resolved_date: date | None = None


def add_business_days(start_date: date, num_days: int) -> date:
    """Add N business days (Monday-Friday) to a date."""
    current = start_date
    added = 0
    while added < num_days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 0-4 are Mon-Fri
            added += 1
    return current


class EOLEscalationManager:
    """Evaluates SLAs and manages conversions into formal EOL exceptions."""

    def __init__(self, records: list[EOLEscalationRecord] | None = None):
        self.records: dict[str, EOLEscalationRecord] = {r.record_id: r for r in (records or [])}

    def record_finding(
        self,
        runtime_name: str,
        repo_name: str,
        secondary_escalation_target: str,
        detected_date: date | None = None,
    ) -> EOLEscalationRecord:
        """Register a new EOL runtime detection."""
        rec = EOLEscalationRecord(
            runtime_name=runtime_name,
            repo_name=repo_name,
            first_detected_date=detected_date or date.today(),
            secondary_escalation_target=secondary_escalation_target,
            status=EOLSlaStatus.PENDING_ACK,
        )
        self.records[rec.record_id] = rec
        return rec

    def acknowledge_finding(self, record_id: str, acknowledged_by: str, ack_date: date | None = None) -> None:
        """Acknowledge finding by the primary repo owner."""
        if record_id not in self.records:
            raise KeyError(f"Record {record_id} not found")
        rec = self.records[record_id]
        rec.acknowledged_date = ack_date or date.today()
        rec.acknowledged_by = acknowledged_by
        rec.status = EOLSlaStatus.IN_REMEDIATION

    def evaluate_sla(
        self,
        record_id: str,
        reference_date: date | None = None,
    ) -> tuple[EOLSlaStatus, str]:
        """Evaluate SLAs for an EOL finding and transition state accordingly."""
        rec = self.records[record_id]
        today = reference_date or date.today()

        if rec.status == EOLSlaStatus.RESOLVED:
            return rec.status, "Finding has been resolved."

        if rec.status == EOLSlaStatus.FORMAL_EXCEPTION:
            if rec.formal_exception and rec.formal_exception.is_valid(today):
                return rec.status, f"Covered by active formal exception through {rec.formal_exception.expiry_date}."
            rec.status = EOLSlaStatus.OVERDUE_REMEDIATION
            return rec.status, "Formal exception expired. Renewed joint approval required."

        # 1. Acknowledgment SLA: 2 business days
        if not rec.acknowledged_date:
            ack_deadline = add_business_days(rec.first_detected_date, 2)
            if today > ack_deadline:
                rec.status = EOLSlaStatus.OVERDUE_ACK
                return (
                    rec.status,
                    f"Acknowledgment SLA missed (deadline {ack_deadline}). Escalated to {rec.secondary_escalation_target}.",
                )
            return rec.status, f"Pending acknowledgment by {ack_deadline}."

        # 2. Remediation SLA: 30 calendar days
        remediation_deadline = rec.first_detected_date + timedelta(days=30)
        if today > remediation_deadline:
            rec.status = EOLSlaStatus.OVERDUE_REMEDIATION
            return (
                rec.status,
                f"30-day remediation SLA missed (deadline {remediation_deadline}). Must convert to formal exception.",
            )

        return rec.status, f"In remediation. Remediation deadline is {remediation_deadline}."

    def grant_formal_exception(
        self,
        record_id: str,
        security_approver: str,
        engineering_approver: str,
        compensating_controls: str,
        duration_days: int = 60,
        approved_date: date | None = None,
    ) -> EOLFormalException:
        """Convert a blocked or overdue EOL finding into a formal documented exception."""
        if not compensating_controls or len(compensating_controls.strip()) < 10:
            raise ValueError("Compensating controls are mandatory for an EOL exception.")
        if not security_approver or not engineering_approver:
            raise ValueError("Joint named sign-off from both Security and Engineering is required.")

        start = approved_date or date.today()
        expiry = start + timedelta(days=duration_days)
        review_date = start + timedelta(days=min(30, duration_days))

        exception = EOLFormalException(
            security_approver=security_approver,
            engineering_approver=engineering_approver,
            compensating_controls=compensating_controls.strip(),
            approved_date=start,
            expiry_date=expiry,
            next_review_date=review_date,
        )

        rec = self.records[record_id]
        rec.formal_exception = exception
        rec.status = EOLSlaStatus.FORMAL_EXCEPTION
        return exception
