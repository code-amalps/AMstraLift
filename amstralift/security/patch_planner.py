"""Surgical patch planner for remediating vulnerability findings."""

from amstralift.core.models import DependencyChange, DependencyTier
from amstralift.governance.vulnerabilities import VulnerabilitySeverity
from amstralift.security.models import AuditReport, VulnerabilityFinding
from amstralift.security.osv_client import _parse_semver_tuple


class VulnerabilityPatchPlanner:
    """Calculates minimal, surgical DependencyChange proposals from audit findings."""

    @classmethod
    def plan_remediation(
        cls,
        report: AuditReport,
        target_package_filter: set[str] | None = None,
    ) -> list[DependencyChange]:
        """Convert audit findings into prioritized surgical dependency upgrades."""
        # 1. Group findings by package name
        grouped: dict[str, list[VulnerabilityFinding]] = {}
        for f in report.findings:
            if not f.has_fix:
                continue
            if target_package_filter and f.package_name not in target_package_filter:
                continue
            grouped.setdefault(f.package_name, []).append(f)

        changes: list[DependencyChange] = []

        for pkg, findings in grouped.items():
            current_ver = findings[0].current_version

            # Find highest fixed version among all CVEs for this package
            fixed_versions = [f.fixed_version for f in findings if f.fixed_version]
            if not fixed_versions:
                continue

            sorted_fixes = sorted(
                fixed_versions,
                key=lambda v: _parse_semver_tuple(v),
            )
            target_fix = sorted_fixes[-1]

            # Determine Tier based on highest severity
            has_crit_or_high = any(
                f.severity in (VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH)
                for f in findings
            )
            tier = DependencyTier.TIER_3_CRITICAL if has_crit_or_high else DependencyTier.TIER_1_SAFE

            # Collect CVE IDs
            cve_ids = ", ".join(sorted({f.cve_id for f in findings}))
            rationale = (
                f"Security remediation for {cve_ids}. "
                f"Surgically bump {pkg} from {current_ver} to {target_fix}."
            )

            # Preserve prefix if present in original
            prefix = "^" if current_ver.startswith("^") else ("~" if current_ver.startswith("~") else "^")
            formatted_target = f"{prefix}{target_fix}" if not target_fix.startswith(("^", "~")) else target_fix

            changes.append(
                DependencyChange(
                    package_name=pkg,
                    from_version=current_ver,
                    to_version=formatted_target,
                    change_type="direct",
                    tier=tier,
                    rationale=rationale,
                )
            )

        return changes
