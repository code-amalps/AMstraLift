"""Vulnerability Lifecycle Management and Time-Boxed Exceptions (Section 10).

Manages detection, triage, remediation, and time-boxed exceptions.
Exceptions require joint named approvers (Security + Engineering),
mandatory compensating controls, and severity-based expiry.
- Critical: 30 days
- High: 60 days
- Medium / Low: 90 days
"""

from datetime import date, timedelta
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class VulnerabilitySeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def default_expiry_days(self) -> int:
        match self:
            case VulnerabilitySeverity.CRITICAL:
                return 30
            case VulnerabilitySeverity.HIGH:
                return 60
            case VulnerabilitySeverity.MEDIUM | VulnerabilitySeverity.LOW:
                return 90


class ExceptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RESOLVED = "RESOLVED"
    REVOKED = "REVOKED"


class VulnerabilityException(BaseModel):
    """Schema for a time-boxed CVE exception."""

    exception_id: str = Field(default_factory=lambda: f"cve-exc-{uuid4().hex[:8]}")
    cve_id: str
    package_name: str
    vulnerable_version: str
    severity: VulnerabilitySeverity
    security_approver: str
    engineering_approver: str
    compensating_controls: str  # Mandatory mitigation rationale
    created_at: date = Field(default_factory=date.today)
    expiry_date: date
    status: ExceptionStatus = ExceptionStatus.ACTIVE
    resolution_notes: str | None = None

    def is_active(self, reference_date: date | None = None) -> bool:
        today = reference_date or date.today()
        if self.status != ExceptionStatus.ACTIVE:
            return False
        return today <= self.expiry_date


class VulnerabilityManager:
    """Manages creation, evaluation, and resolution of vulnerability exceptions."""

    def __init__(self, exceptions: list[VulnerabilityException] | None = None):
        self.exceptions: dict[str, VulnerabilityException] = {e.exception_id: e for e in (exceptions or [])}

    def create_exception(
        self,
        cve_id: str,
        package_name: str,
        vulnerable_version: str,
        severity: VulnerabilitySeverity,
        security_approver: str,
        engineering_approver: str,
        compensating_controls: str,
        created_at: date | None = None,
        duration_days: int | None = None,
    ) -> VulnerabilityException:
        """Create a formal documented vulnerability exception."""
        if not compensating_controls or len(compensating_controls.strip()) < 10:
            raise ValueError("Compensating controls are mandatory and must provide clear mitigation rationale.")
        if not security_approver or not engineering_approver:
            raise ValueError("Joint named sign-off from both Security and Engineering is required.")

        start_date = created_at or date.today()
        days = duration_days if duration_days is not None else severity.default_expiry_days
        expiry = start_date + timedelta(days=days)

        exc = VulnerabilityException(
            cve_id=cve_id,
            package_name=package_name,
            vulnerable_version=vulnerable_version,
            severity=severity,
            security_approver=security_approver,
            engineering_approver=engineering_approver,
            compensating_controls=compensating_controls.strip(),
            created_at=start_date,
            expiry_date=expiry,
            status=ExceptionStatus.ACTIVE,
        )
        self.exceptions[exc.exception_id] = exc
        return exc

    def check_coverage(
        self,
        cve_id: str,
        package_name: str,
        version: str,
        reference_date: date | None = None,
    ) -> VulnerabilityException | None:
        """Check if an active, unexpired exception covers this finding."""
        today = reference_date or date.today()
        for exc in self.exceptions.values():
            if (
                exc.cve_id.upper() == cve_id.upper()
                and exc.package_name.lower() == package_name.lower()
                and exc.vulnerable_version == version
            ):
                if exc.is_active(today):
                    return exc
                elif exc.status == ExceptionStatus.ACTIVE and today > exc.expiry_date:
                    exc.status = ExceptionStatus.EXPIRED
        return None

    def resolve_exception(self, exception_id: str, resolution_notes: str) -> None:
        """Mark an exception as resolved (e.g. patched in production)."""
        if exception_id in self.exceptions:
            self.exceptions[exception_id].status = ExceptionStatus.RESOLVED
            self.exceptions[exception_id].resolution_notes = resolution_notes
