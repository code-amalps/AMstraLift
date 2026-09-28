"""Compatibility-aware version selection engine for secure dependency remediation.

Chooses the lowest impact secure version compatible with framework and runtime constraints.
Does not blindly choose the lowest version number.
"""

import re
from typing import Any

from amstralift.security.osv_client import _parse_semver_tuple


class VersionSelectionResult:
    """Outcome of compatibility-aware version selection."""

    def __init__(
        self,
        target_version: str | None,
        is_major_bump: bool,
        rationale: str,
        breaking_risks: list[str],
        is_compatible: bool = True,
        advisory: str | None = None,
    ):
        self.target_version = target_version
        self.is_major_bump = is_major_bump
        self.rationale = rationale
        self.breaking_risks = breaking_risks
        self.is_compatible = is_compatible
        self.advisory = advisory


class CompatibilityAwareVersionSelector:
    """Selects secure versions respecting framework, runtime, and dependency graph boundaries."""

    @classmethod
    def select_version(
        cls,
        package_name: str,
        current_version: str,
        fixed_versions: list[str],
        ecosystem: str,
        project_context: dict[str, Any] | None = None,
    ) -> VersionSelectionResult:
        """Select the safest, most compatible secure version."""
        if not fixed_versions:
            return VersionSelectionResult(
                target_version=None,
                is_major_bump=False,
                rationale="No fixed versions reported by vulnerability intelligence.",
                breaking_risks=["No upstream patch available"],
                is_compatible=False,
                advisory=f"Package {package_name} ({current_version}) has no upstream fix. Consider alternative libraries or compensating controls.",
            )

        ctx = project_context or {}
        cur_tuple = _parse_semver_tuple(current_version)
        cur_major, cur_minor, cur_patch = cur_tuple[:3]

        # Parse and filter candidate versions
        candidates: list[tuple[tuple[int, ...], str]] = []
        for fv in fixed_versions:
            clean_fv = fv.strip().lstrip("^~>=<v")
            if not clean_fv:
                continue
            cand_tuple = _parse_semver_tuple(clean_fv)
            # Only consider versions greater than or equal to current
            if cand_tuple >= cur_tuple:
                candidates.append((cand_tuple, clean_fv))

        if not candidates:
            # If all reported fixed versions look numerically lower (e.g. backports to older branches),
            # check if any fixed version exists for the current major branch
            same_major_backports = [
                (_parse_semver_tuple(fv), fv.strip().lstrip("^~>=<v"))
                for fv in fixed_versions
                if _parse_semver_tuple(fv)[0] == cur_major
            ]
            if same_major_backports:
                same_major_backports.sort(key=lambda x: x[0])
                target = same_major_backports[-1][1]
                return VersionSelectionResult(
                    target_version=target,
                    is_major_bump=False,
                    rationale=f"Selected backported security patch {target} for major branch {cur_major}.",
                    breaking_risks=[],
                    is_compatible=True,
                )

            # Fallback to the highest available fixed release
            sorted_all = sorted(
                [(_parse_semver_tuple(fv), fv.strip().lstrip("^~>=<v")) for fv in fixed_versions],
                key=lambda x: x[0],
            )
            highest_fix = sorted_all[-1][1]
            is_maj = sorted_all[-1][0][0] > cur_major
            return VersionSelectionResult(
                target_version=highest_fix,
                is_major_bump=is_maj,
                rationale=f"Selected latest fixed release {highest_fix} as candidate.",
                breaking_risks=["Version discrepancy with current branch; manual verification recommended."] if is_maj else [],
                is_compatible=True,
            )

        # Sort candidates ascending
        candidates.sort(key=lambda x: x[0])

        # Ecosystem and Framework Compatibility Constraints
        framework_major = ctx.get("framework_major")
        # If this is an ecosystem core/framework package (e.g. @angular/* or Microsoft.AspNetCore.*),
        # prevent unsolicited framework major leaps
        is_framework_pkg = (
            package_name.startswith(("@angular/", "@angular-devkit/"))
            or package_name.startswith("Microsoft.AspNetCore.")
            or package_name == "react"
            or package_name == "react-dom"
        )

        # Split into tiers
        # Tier 1: Same major, same minor (patch upgrade) -> safest
        tier_1_patch = [c for c in candidates if c[0][0] == cur_major and c[0][1] == cur_minor]
        # Tier 2: Same major, higher minor -> low risk
        tier_2_minor = [c for c in candidates if c[0][0] == cur_major and c[0][1] > cur_minor]
        # Tier 3: Next major -> potential breaking changes
        tier_3_major = [c for c in candidates if c[0][0] > cur_major]

        # 1. Prefer lowest secure patch release within same minor
        if tier_1_patch:
            target = tier_1_patch[0][1]
            return VersionSelectionResult(
                target_version=target,
                is_major_bump=False,
                rationale=f"Selected minimal patch release {target} in same minor ({cur_major}.{cur_minor}.x). Zero breaking API change risk.",
                breaking_risks=[],
                is_compatible=True,
            )

        # 2. Prefer lowest secure minor release within same major
        if tier_2_minor:
            target = tier_2_minor[0][1]
            return VersionSelectionResult(
                target_version=target,
                is_major_bump=False,
                rationale=f"Selected minimal minor release {target} in same major ({cur_major}.x). Backward-compatible API addition.",
                breaking_risks=[],
                is_compatible=True,
            )

        # 3. If only higher major exists
        if tier_3_major:
            # If it's a framework package and framework_major is set, check if jumping major would desync framework
            if is_framework_pkg and framework_major:
                target_maj = tier_3_major[0][0][0]
                if target_maj != framework_major:
                    return VersionSelectionResult(
                        target_version=None,
                        is_major_bump=True,
                        rationale=f"Upgrading {package_name} to major {target_maj} would desync core framework version {framework_major}.",
                        breaking_risks=[f"Framework version mismatch: project is on v{framework_major}, fix requires v{target_maj}"],
                        is_compatible=False,
                        advisory=f"Package {package_name} fix requires major version {target_maj}, which exceeds project framework {framework_major}. Upgrading framework first or seeking backport is recommended.",
                    )

            target = tier_3_major[0][1]
            target_maj = tier_3_major[0][0][0]
            return VersionSelectionResult(
                target_version=target,
                is_major_bump=True,
                rationale=f"Selected lowest next-major release {target}. No secure release exists in current major {cur_major}.",
                breaking_risks=[
                    f"Major version bump from {cur_major} to {target_maj}.",
                    "Potential breaking API changes require automated tests and developer review.",
                ],
                is_compatible=True,
            )

        # Fallback to closest candidate
        target = candidates[0][1]
        return VersionSelectionResult(
            target_version=target,
            is_major_bump=candidates[0][0][0] > cur_major,
            rationale=f"Selected available candidate {target}.",
            breaking_risks=[],
            is_compatible=True,
        )
