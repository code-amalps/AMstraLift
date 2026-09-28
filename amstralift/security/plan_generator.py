"""Generates actionable, risk-analyzed remediation plans from vulnerability audit findings."""

from typing import Any
from pathlib import Path

from amstralift.core.models import DependencyChange, DependencyTier
from amstralift.governance.vulnerabilities import VulnerabilitySeverity
from amstralift.security.compatibility_selector import CompatibilityAwareVersionSelector
from amstralift.security.models import (
    AuditReport,
    RemediationPlan,
    RemediationPlanItem,
    VulnerabilityFinding,
)


class RemediationPlanGenerator:
    """Produces structured remediation plans and dependency upgrade proposals."""

    @classmethod
    def generate_plan(
        cls,
        report: AuditReport,
        project_context: dict[str, Any] | None = None,
    ) -> RemediationPlan:
        """Create a detailed remediation plan with compatibility and risk analysis."""
        ctx = project_context or {}
        items: list[RemediationPlanItem] = []
        unresolved: list[VulnerabilityFinding] = []
        advisories: list[str] = []

        # Group findings by package name
        grouped: dict[str, list[VulnerabilityFinding]] = {}
        for f in report.findings:
            if f.is_exempted:
                continue
            grouped.setdefault(f.package_name, []).append(f)

        for pkg, findings in grouped.items():
            current_ver = findings[0].current_version
            is_direct = any(f.is_direct for f in findings)
            introduced_by = findings[0].introduced_by
            all_cves = sorted({f.cve_id for f in findings})

            # Collect all fixed versions across all CVEs for this package
            all_fixed: set[str] = set()
            for f in findings:
                if f.fixed_version:
                    all_fixed.add(f.fixed_version)
                all_fixed.update(f.all_fixed_versions)

            if not all_fixed:
                unresolved.extend(findings)
                advisories.append(
                    f"No upstream patch available for {pkg} ({current_ver}) affecting {', '.join(all_cves)}."
                )
                continue

            selection = CompatibilityAwareVersionSelector.select_version(
                package_name=pkg,
                current_version=current_ver,
                fixed_versions=list(all_fixed),
                ecosystem=report.ecosystem,
                project_context=ctx,
            )

            if not selection.is_compatible or not selection.target_version:
                unresolved.extend(findings)
                if selection.advisory:
                    advisories.append(selection.advisory)
                continue

            # Determine remediation mechanism and expected file changes
            expected_files = []
            if report.ecosystem in ("angular", "react", "npm"):
                if is_direct:
                    mech = "package.json dependencies"
                    expected_files = ["package.json", "package-lock.json"]
                else:
                    mech = "npm overrides"
                    expected_files = ["package.json", "package-lock.json"]
            elif report.ecosystem in ("dotnet", "nuget"):
                if is_direct:
                    mech = ".csproj / Directory.Packages.props"
                    expected_files = ["*.csproj"]
                else:
                    mech = ".csproj transitive PackageReference pin"
                    expected_files = ["*.csproj"]
            else:  # python
                if is_direct:
                    mech = "requirements.txt / pyproject.toml"
                    expected_files = ["requirements.txt", "pyproject.toml"]
                else:
                    mech = "constraints.txt / lockfile pin"
                    expected_files = ["constraints.txt", "poetry.lock"]

            # Preserve version prefix if current version had one
            target_v = selection.target_version
            prefix = "^" if current_ver.startswith("^") else ("~" if current_ver.startswith("~") else "")
            if prefix and not target_v.startswith(("^", "~")):
                target_v = f"{prefix}{target_v}"

            items.append(
                RemediationPlanItem(
                    cve_id=", ".join(all_cves),
                    package_name=pkg,
                    current_version=current_ver,
                    target_version=target_v,
                    is_direct=is_direct,
                    introduced_by=introduced_by,
                    is_major_bump=selection.is_major_bump,
                    compatibility_notes=selection.rationale,
                    breaking_change_risks=selection.breaking_risks,
                    expected_file_changes=expected_files,
                    remediation_mechanism=mech,
                )
            )

        return RemediationPlan(
            items=items,
            unresolved_findings=unresolved,
            advisories=advisories,
        )

    @classmethod
    def plan_to_dependency_changes(cls, plan: RemediationPlan) -> list[DependencyChange]:
        """Convert a remediation plan into Stage A DependencyChange proposals."""
        changes: list[DependencyChange] = []
        for item in plan.items:
            tier = (
                DependencyTier.TIER_3_CRITICAL
                if item.is_major_bump
                else DependencyTier.TIER_1_SAFE
            )
            changes.append(
                DependencyChange(
                    package_name=item.package_name,
                    from_version=item.current_version,
                    to_version=item.target_version,
                    change_type="direct" if item.is_direct else "transitive",
                    tier=tier,
                    rationale=f"Security remediation for {item.cve_id} via {item.remediation_mechanism}. {item.compatibility_notes}",
                )
            )
        return changes
