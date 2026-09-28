"""Data models for vulnerability audit findings, dependency graphs, and remediation plans."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from amstralift.governance.vulnerabilities import VulnerabilitySeverity


class VerificationStatus(str, Enum):
    """Explicit classification of remediation verification outcomes per behavioral contract."""

    VERIFIED_SAFE = "VERIFIED_SAFE"  # Build and required tests pass (>0 tests run) -> Eligible for next configured approval gate
    UNVERIFIED_NO_TESTS = "UNVERIFIED_NO_TESTS"  # Build passed, but no relevant tests exist -> Mark as unverified
    VERIFICATION_INCOMPLETE = "VERIFICATION_INCOMPLETE"  # Tests skipped or cannot run -> Mark verification as incomplete
    BUILD_FAILED = "BUILD_FAILED"  # Build fails -> Remediation fails; do not publish automatically
    TESTS_FAILED = "TESTS_FAILED"  # Tests fail -> Remediation fails; provide diagnostics
    RESCAN_FAILED = "RESCAN_FAILED"  # Vulnerability rescan fails -> Do not claim the vulnerability is resolved

    # Compatibility aliases
    COMPILED_UNVERIFIED = "UNVERIFIED_NO_TESTS"
    GATES_FAILED = "TESTS_FAILED"


# Backward compatibility alias
VerificationConfidence = VerificationStatus


class DiscoveredDependency(BaseModel):
    """Represents a discovered direct or transitive dependency in a repository."""

    package_name: str
    version: str
    is_direct: bool = True
    introduced_by: list[str] = Field(default_factory=list)  # Dependency chain, e.g. ["@angular/material", "tslib"]
    manifest_file: str = ""
    ecosystem: str = ""
    target_framework: str | None = None

    @property
    def display_introduced_by(self) -> str:
        if self.is_direct or not self.introduced_by:
            return "direct"
        return " -> ".join(self.introduced_by)


class VulnerabilityFinding(BaseModel):
    """Represents an individual vulnerability detected in a project dependency."""

    cve_id: str
    package_name: str
    ecosystem: str  # "npm", "NuGet", "PyPI"
    current_version: str
    severity: VulnerabilitySeverity
    fixed_version: str | None = None
    all_fixed_versions: list[str] = Field(default_factory=list)
    summary: str = ""
    details: str = ""
    cvss_score: float | None = None
    is_direct: bool = True
    introduced_by: list[str] = Field(default_factory=list)
    is_confirmed: bool = True
    is_exempted: bool = False
    exemption_id: str | None = None

    @property
    def has_fix(self) -> bool:
        return self.fixed_version is not None and len(self.fixed_version.strip()) > 0

    @property
    def display_introduced_by(self) -> str:
        if self.is_direct or not self.introduced_by:
            return "direct"
        return " -> ".join(self.introduced_by)


class AuditReport(BaseModel):
    """Aggregated vulnerability report for a scanned repository."""

    repo_path: str
    ecosystem: str
    scanned_packages_count: int = 0
    findings: list[VulnerabilityFinding] = Field(default_factory=list)

    @property
    def vulnerable_packages_count(self) -> int:
        return len({f.package_name for f in self.findings})

    @property
    def direct_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if f.is_direct]

    @property
    def transitive_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if not f.is_direct]

    @property
    def exempted_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if f.is_exempted]

    @property
    def actionable_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if not f.is_exempted and f.has_fix]

    @property
    def has_critical_or_high(self) -> bool:
        return any(
            f.severity in (VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH)
            for f in self.findings
            if not f.is_exempted
        )

    @property
    def fixable_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if f.has_fix]


class RemediationPlanItem(BaseModel):
    """Individual action item in an upgrade remediation plan."""

    cve_id: str
    package_name: str
    current_version: str
    target_version: str
    is_direct: bool
    introduced_by: list[str] = Field(default_factory=list)
    is_major_bump: bool = False
    compatibility_notes: str = ""
    breaking_change_risks: list[str] = Field(default_factory=list)
    expected_file_changes: list[str] = Field(default_factory=list)
    remediation_mechanism: str = "direct manifest update"  # e.g., "npm overrides", "Direct PackageReference"


class RemediationPlan(BaseModel):
    """Comprehensive plan of proposed dependency updates before execution."""

    items: list[RemediationPlanItem] = Field(default_factory=list)
    unresolved_findings: list[VulnerabilityFinding] = Field(default_factory=list)
    advisories: list[str] = Field(default_factory=list)

    @property
    def has_major_upgrades(self) -> bool:
        return any(item.is_major_bump for item in self.items)

    @property
    def total_packages_to_remediate(self) -> int:
        return len({item.package_name for item in self.items})


class RemediationResult(BaseModel):
    """Execution output from an audit, preview, or apply remediation workflow."""

    mode: str  # "audit", "preview", "apply"
    report: AuditReport
    plan: RemediationPlan | None = None
    verification_status: VerificationStatus = VerificationStatus.VERIFIED_SAFE
    gate_summary: Any = None
    rescan_report: AuditReport | None = None
    branch_name: str | None = None
    pr_proposal: Any = None
    uncertainty_warning: str | None = None
    error_message: str | None = None
    remediation_successful: bool = False

    @property
    def confidence(self) -> VerificationStatus:
        return self.verification_status
