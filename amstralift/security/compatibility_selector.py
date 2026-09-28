"""Compatibility-aware version selection engine for secure dependency remediation.

Filters candidate versions against framework runtimes, peer dependencies,
and dependency graph constraints. Rejects incompatible versions and recommends
manual remediation when no safe, compatible version exists.
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
        rejected_candidates: dict[str, str] | None = None,
    ):
        self.target_version = target_version
        self.is_major_bump = is_major_bump
        self.rationale = rationale
        self.breaking_risks = breaking_risks
        self.is_compatible = is_compatible
        self.advisory = advisory
        self.rejected_candidates = rejected_candidates or {}


class CompatibilityAwareVersionSelector:
    """Selects secure versions respecting framework, runtime, peer dependency, and graph constraints."""

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
                advisory=(
                    f"Manual Remediation Recommended: Package {package_name} ({current_version}) has no upstream fix. "
                    "Apply compensating controls or migrate to an alternative supported library."
                ),
            )

        ctx = project_context or {}
        cur_tuple = _parse_semver_tuple(current_version)
        cur_major, cur_minor, cur_patch = cur_tuple[:3]

        # Parse and sort all reported fixed versions
        clean_fixes = list(dict.fromkeys([fv.strip().lstrip("^~>=<v") for fv in fixed_versions if fv.strip()]))
        sorted_fixes = sorted(clean_fixes, key=lambda v: _parse_semver_tuple(v))

        # Filter candidates: must be >= current version (or backport on current major)
        initial_candidates = [
            fv for fv in sorted_fixes
            if _parse_semver_tuple(fv) >= cur_tuple or _parse_semver_tuple(fv)[0] == cur_major
        ]

        if not initial_candidates:
            # All reported versions are lower majors with no backport
            highest = sorted_fixes[-1] if sorted_fixes else current_version
            return VersionSelectionResult(
                target_version=None,
                is_major_bump=True,
                rationale="All reported fixes belong to legacy version branches.",
                breaking_risks=["No compatible patch in current or higher branch"],
                is_compatible=False,
                advisory=(
                    f"Manual Remediation Recommended: All upstream fixes for {package_name} target older major branches. "
                    f"Manual review required to verify if {highest} can be backported."
                ),
            )

        # Evaluate compatibility filters for each candidate
        compatible_candidates: list[tuple[tuple[int, ...], str]] = []
        rejected_reasons: dict[str, str] = {}

        for cand_ver in initial_candidates:
            cand_tuple = _parse_semver_tuple(cand_ver)
            cand_major = cand_tuple[0]

            # 1. Target Framework & Runtime Compatibility Filter (.NET)
            tf = ctx.get("target_framework", "")
            if ecosystem in ("dotnet", "nuget") and tf:
                # If package is a core Microsoft package whose major aligns with .NET SDK
                if package_name.startswith(("Microsoft.Extensions.", "Microsoft.AspNetCore.", "System.Text.Json")):
                    tf_match = re.search(r"net(\d+)\.0", tf)
                    if tf_match:
                        tf_major = int(tf_match.group(1))
                        if cand_major > tf_major:
                            rejected_reasons[cand_ver] = (
                                f"Requires TargetFramework net{cand_major}.0, which exceeds project framework {tf}."
                            )
                            continue

            # 2. Core Framework Desync Filter (Angular & React)
            framework_major = ctx.get("framework_major")
            is_framework_pkg = (
                package_name.startswith(("@angular/", "@angular-devkit/"))
                or package_name in ("react", "react-dom")
            )
            if is_framework_pkg and framework_major is not None:
                if cand_major != framework_major:
                    rejected_reasons[cand_ver] = (
                        f"Major version {cand_major} desyncs from core project framework v{framework_major}."
                    )
                    continue

            # 3. Peer Dependency Constraints Filter
            peer_deps = ctx.get("peer_dependencies", {}).get(cand_ver, {})
            installed_deps = ctx.get("installed_dependencies", {})
            peer_conflict = False
            for peer_pkg, required_range in peer_deps.items():
                if peer_pkg in installed_deps:
                    inst_tuple = _parse_semver_tuple(installed_deps[peer_pkg])
                    req_tuple = _parse_semver_tuple(required_range)
                    # Simple major compatibility check
                    if req_tuple[0] != inst_tuple[0]:
                        rejected_reasons[cand_ver] = (
                            f"peerDependency '{peer_pkg}' '{required_range}' is not satisfied by installed {installed_deps[peer_pkg]}."
                        )
                        peer_conflict = True
                        break
            if peer_conflict:
                continue

            # 4. Project Version Constraint Upper Bound Filter
            version_constraints = ctx.get("version_constraints", {})
            if package_name in version_constraints:
                constraint = version_constraints[package_name]
                # If constraint specifies an upper bound like "< 5.0.0"
                upper_bound_match = re.search(r"<\s*v?(\d+)", constraint)
                if upper_bound_match:
                    max_allowed_major = int(upper_bound_match.group(1))
                    if cand_major >= max_allowed_major:
                        rejected_reasons[cand_ver] = f"Violates project manifest constraint '{constraint}'."
                        continue

            compatible_candidates.append((cand_tuple, cand_ver))

        # If all candidates were rejected by compatibility filters
        if not compatible_candidates:
            details = "; ".join([f"{v}: {reason}" for v, reason in list(rejected_reasons.items())[:3]])
            return VersionSelectionResult(
                target_version=None,
                is_major_bump=False,
                rationale="All available fixed versions conflict with project constraints.",
                breaking_risks=["Compatibility conflict with framework/runtime constraints"],
                is_compatible=False,
                advisory=(
                    f"Manual Remediation Recommended for {package_name} ({current_version}): "
                    f"No candidate satisfied project constraints ({details}). "
                    "Automatic upgrade halted to prevent breaking the application."
                ),
                rejected_candidates=rejected_reasons,
            )

        # Partition compatible candidates into tiers
        tier_1_patch = [c for c in compatible_candidates if c[0][0] == cur_major and c[0][1] == cur_minor]
        tier_2_minor = [c for c in compatible_candidates if c[0][0] == cur_major and c[0][1] > cur_minor]
        tier_3_major = [c for c in compatible_candidates if c[0][0] > cur_major]

        # 1. Prefer lowest secure patch release within same minor (safest)
        if tier_1_patch:
            target = tier_1_patch[0][1]
            return VersionSelectionResult(
                target_version=target,
                is_major_bump=False,
                rationale=f"Selected minimal patch release {target} in same minor ({cur_major}.{cur_minor}.x). Zero breaking API change risk.",
                breaking_risks=[],
                is_compatible=True,
                rejected_candidates=rejected_reasons,
            )

        # 2. Prefer lowest secure minor release within same major (low risk)
        if tier_2_minor:
            target = tier_2_minor[0][1]
            return VersionSelectionResult(
                target_version=target,
                is_major_bump=False,
                rationale=f"Selected minimal minor release {target} in same major ({cur_major}.x). Backward-compatible API addition.",
                breaking_risks=[],
                is_compatible=True,
                rejected_candidates=rejected_reasons,
            )

        # 3. If only higher major exists
        if tier_3_major:
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
                rejected_candidates=rejected_reasons,
            )

        # Fallback to first compatible
        target = compatible_candidates[0][1]
        return VersionSelectionResult(
            target_version=target,
            is_major_bump=compatible_candidates[0][0][0] > cur_major,
            rationale=f"Selected compatible candidate {target}.",
            breaking_risks=[],
            is_compatible=True,
            rejected_candidates=rejected_reasons,
        )
