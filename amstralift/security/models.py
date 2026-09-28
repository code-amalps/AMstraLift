"""Data models for vulnerability audit findings and reports."""

from pydantic import BaseModel, Field

from amstralift.governance.vulnerabilities import VulnerabilitySeverity


class VulnerabilityFinding(BaseModel):
    """Represents an individual vulnerability detected in a project dependency."""

    cve_id: str
    package_name: str
    ecosystem: str  # "npm", "NuGet", "PyPI"
    current_version: str
    severity: VulnerabilitySeverity
    fixed_version: str | None = None
    summary: str = ""
    details: str = ""
    cvss_score: float | None = None

    @property
    def has_fix(self) -> bool:
        return self.fixed_version is not None and len(self.fixed_version.strip()) > 0


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
    def has_critical_or_high(self) -> bool:
        return any(f.severity in (VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH) for f in self.findings)

    @property
    def fixable_findings(self) -> list[VulnerabilityFinding]:
        return [f for f in self.findings if f.has_fix]
