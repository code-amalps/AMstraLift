"""Vulnerability Lifecycle Management and Time-Boxed Exceptions (Section 10).

Manages detection, triage, remediation, and time-boxed exceptions.
Exceptions require joint named approvers (Security + Engineering),
mandatory compensating controls, and configurable severity-based expiry.
Deadlines and policies are organization-configurable, not hardcoded.
"""

from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field


class VulnerabilitySeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RemediationStatus(str, Enum):
    """Lifecycle tracking of a discovered vulnerability under organizational SLA governance.

    NOTE: An SLA deadline determines governance urgency, prioritization, and escalation.
    It does NOT determine whether a package upgrade is technically safe.
    Technical safety is determined strictly by dependency compatibility, build validation, and test gates.
    """

    IDENTIFIED = "IDENTIFIED"
    IN_REMEDIATION = "IN_REMEDIATION"
    EXEMPTED = "EXEMPTED"
    SLA_BREACHED = "SLA_BREACHED"
    RESOLVED = "RESOLVED"


class ExceptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RESOLVED = "RESOLVED"
    REVOKED = "REVOKED"


class VulnerabilityPolicyConfig(BaseModel):
    """Organization-configurable security and vulnerability remediation policy.

    Separates policy definition from governance enforcement.
    """

    sla_days: dict[VulnerabilitySeverity, int] = Field(
        default_factory=lambda: {
            VulnerabilitySeverity.CRITICAL: 30,
            VulnerabilitySeverity.HIGH: 60,
            VulnerabilitySeverity.MEDIUM: 90,
            VulnerabilitySeverity.LOW: 90,
        }
    )
    auto_remediation_enabled: bool = True
    auto_remediate_severities: list[VulnerabilitySeverity] = Field(
        default_factory=lambda: [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH]
    )
    allow_major_version_upgrades: bool = False
    allow_unverified_upgrades_for_review: bool = True
    require_joint_signoff: bool = True
    mandatory_compensating_controls: bool = True
    max_exception_extension_days: int = 30
    require_zero_test_uncertainty_flag: bool = True

    def get_sla_days(self, severity: VulnerabilitySeverity) -> int:
        """Get the configured SLA days for a given severity from organization policy."""
        return self.sla_days.get(severity, 30)

    def calculate_deadline(self, severity: VulnerabilitySeverity, detected_at: date) -> date:
        """Calculate the SLA deadline date for a finding based on configured organization policy."""
        days = self.get_sla_days(severity)
        return detected_at + timedelta(days=days)

    def evaluate_sla_status(
        self,
        severity: VulnerabilitySeverity,
        detected_at: date,
        reference_date: date | None = None,
        is_exempted: bool = False,
    ) -> tuple[RemediationStatus, int]:
        """Evaluate SLA compliance status and remaining/overdue days."""
        if is_exempted:
            return RemediationStatus.EXEMPTED, 0
        ref = reference_date or date.today()
        deadline = self.calculate_deadline(severity, detected_at)
        delta_days = (deadline - ref).days
        if delta_days >= 0:
            return RemediationStatus.IDENTIFIED, delta_days
        return RemediationStatus.SLA_BREACHED, abs(delta_days)


class VulnerabilityException(BaseModel):
    """Schema for a time-boxed CVE exception with full audit tracking."""

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
    audit_history: list[str] = Field(default_factory=list)

    def is_active(self, reference_date: date | None = None) -> bool:
        today = reference_date or date.today()
        if self.status != ExceptionStatus.ACTIVE:
            return False
        return today <= self.expiry_date


class VulnerabilityManager:
    """Manages creation, evaluation, persistence, and resolution of vulnerability exceptions."""

    def __init__(
        self,
        exceptions: list[VulnerabilityException] | None = None,
        policy: VulnerabilityPolicyConfig | None = None,
    ):
        self.exceptions: dict[str, VulnerabilityException] = {e.exception_id: e for e in (exceptions or [])}
        self.policy: VulnerabilityPolicyConfig = policy or VulnerabilityPolicyConfig()

    @classmethod
    def load_policy(cls, policy_path: Path | None = None) -> VulnerabilityPolicyConfig:
        """Load organizational vulnerability policy from YAML or return default."""
        if not policy_path or not policy_path.exists():
            return VulnerabilityPolicyConfig()

        try:
            content = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
            raw_sla = content.get("vulnerability_sla_days", content.get("sla_days", {}))
            sla_dict = {}
            for k, v in raw_sla.items():
                try:
                    sla_dict[VulnerabilitySeverity(k.upper())] = int(v)
                except (ValueError, KeyError):
                    continue

            auto_sevs = []
            for s in content.get("auto_remediate_severities", ["CRITICAL", "HIGH"]):
                try:
                    auto_sevs.append(VulnerabilitySeverity(s.upper()))
                except ValueError:
                    continue

            return VulnerabilityPolicyConfig(
                sla_days=sla_dict or VulnerabilityPolicyConfig().sla_days,
                auto_remediation_enabled=content.get("auto_remediation_enabled", True),
                auto_remediate_severities=auto_sevs or [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH],
                allow_major_version_upgrades=content.get("allow_major_version_upgrades", False),
                require_joint_signoff=content.get("require_joint_signoff", True),
                mandatory_compensating_controls=content.get("mandatory_compensating_controls", True),
                max_exception_extension_days=content.get("max_exception_extension_days", 30),
            )
        except Exception:
            return VulnerabilityPolicyConfig()

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
        """Create a formal documented vulnerability exception governed by policy."""
        if self.policy.mandatory_compensating_controls:
            if not compensating_controls or len(compensating_controls.strip()) < 10:
                raise ValueError("Compensating controls are mandatory and must provide clear mitigation rationale.")
        if self.policy.require_joint_signoff:
            if not security_approver or not engineering_approver:
                raise ValueError("Joint named sign-off from both Security and Engineering is required.")

        start_date = created_at or date.today()
        # Use policy configured SLA if duration_days not explicitly provided
        days = duration_days if duration_days is not None else self.policy.get_sla_days(severity)
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
            audit_history=[f"Created by {security_approver} & {engineering_approver} on {start_date.isoformat()}"],
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
                exc.cve_id.upper() in cve_id.upper()
                or cve_id.upper() in exc.cve_id.upper()
            ) and (
                exc.package_name.lower() == package_name.lower()
                and exc.vulnerable_version == version
            ):
                if exc.is_active(today):
                    return exc
                elif exc.status == ExceptionStatus.ACTIVE and today > exc.expiry_date:
                    exc.status = ExceptionStatus.EXPIRED
                    exc.audit_history.append(f"Expired on {today.isoformat()}")
        return None

    def resolve_exception(self, exception_id: str, resolution_notes: str) -> None:
        """Mark an exception as resolved (e.g. patched in production)."""
        if exception_id in self.exceptions:
            self.exceptions[exception_id].status = ExceptionStatus.RESOLVED
            self.exceptions[exception_id].resolution_notes = resolution_notes
            self.exceptions[exception_id].audit_history.append(f"Resolved: {resolution_notes}")

    def is_auto_remediation_allowed(
        self,
        severity: VulnerabilitySeverity,
        is_major_upgrade: bool = False,
    ) -> tuple[bool, str]:
        """Verify whether automated remediation is permitted under governance policy."""
        if not self.policy.auto_remediation_enabled:
            return False, "Automated remediation is globally disabled by organizational policy."

        if is_major_upgrade and not self.policy.allow_major_version_upgrades:
            return (
                False,
                "Major version upgrades require explicit human developer review and cannot be applied automatically.",
            )

        if severity not in self.policy.auto_remediate_severities:
            return (
                False,
                f"Severity {severity.value} is not in the approved auto-remediation policy list ({[s.value for s in self.policy.auto_remediate_severities]}).",
            )

        return True, "Automated remediation is permitted by policy."

    def export_exceptions_yaml(self, path: Path) -> None:
        """Persist exceptions to YAML."""
        data = [e.model_dump(mode="json") for e in self.exceptions.values()]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def load_exceptions_yaml(self, path: Path) -> None:
        """Load exceptions from YAML."""
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for item in data:
            try:
                exc = VulnerabilityException.model_validate(item)
                self.exceptions[exc.exception_id] = exc
            except Exception:
                continue
