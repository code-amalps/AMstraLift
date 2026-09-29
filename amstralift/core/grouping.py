"""Intelligent dependency grouping and cohesive batching.

Groups related packages (e.g. @angular/*, Microsoft.AspNetCore.*, react/react-dom)
into atomic, cohesive upgrade batches with human-meaningful titles and branch names,
preventing PR flooding and naming confusion.
"""

import re
from typing import NamedTuple

from amstralift.core.models import DependencyChange, DependencyTier


class PackageFamilyRule(NamedTuple):
    family_id: str
    display_name: str
    pattern: re.Pattern
    ecosystem: str | None = None


# Known framework and cohesive library family rules
FAMILY_RULES: list[PackageFamilyRule] = [
    # ── Angular ──────────────────────────────────────────────────────────────
    PackageFamilyRule(
        family_id="angular-core",
        display_name="Angular Framework Stack",
        pattern=re.compile(r"^@angular/(core|common|compiler|forms|router|platform-|animations|elements)"),
        ecosystem="angular",
    ),
    PackageFamilyRule(
        family_id="angular-cdk-material",
        display_name="Angular Material & CDK",
        pattern=re.compile(r"^@angular/(cdk|material)"),
        ecosystem="angular",
    ),
    PackageFamilyRule(
        family_id="ngrx",
        display_name="NgRx State Management Suite",
        pattern=re.compile(r"^@ngrx/"),
        ecosystem="angular",
    ),
    # ── React ────────────────────────────────────────────────────────────────
    PackageFamilyRule(
        family_id="react-core",
        display_name="React Core Stack",
        pattern=re.compile(r"^(react|react-dom|@types/react|@types/react-dom)$"),
        ecosystem="react",
    ),
    PackageFamilyRule(
        family_id="react-router",
        display_name="React Router Suite",
        pattern=re.compile(r"^(react-router|react-router-dom|@remix-run/router)"),
        ecosystem="react",
    ),
    # ── .NET ─────────────────────────────────────────────────────────────────
    PackageFamilyRule(
        family_id="aspnetcore",
        display_name="ASP.NET Core Stack",
        pattern=re.compile(r"^(Microsoft\.AspNetCore\.|Microsoft\.NET\.TargetFramework)"),
        ecosystem="dotnet",
    ),
    PackageFamilyRule(
        family_id="efcore",
        display_name="Entity Framework Core Suite",
        pattern=re.compile(r"^Microsoft\.EntityFrameworkCore"),
        ecosystem="dotnet",
    ),
    PackageFamilyRule(
        family_id="ms-extensions",
        display_name="Microsoft Extensions Stack",
        pattern=re.compile(r"^Microsoft\.Extensions\."),
        ecosystem="dotnet",
    ),
    # ── Python ───────────────────────────────────────────────────────────────
    PackageFamilyRule(
        family_id="fastapi-stack",
        display_name="FastAPI & Pydantic Stack",
        pattern=re.compile(r"^(fastapi|uvicorn|pydantic|starlette)"),
        ecosystem="python",
    ),
    PackageFamilyRule(
        family_id="pytest-stack",
        display_name="Pytest Testing Suite",
        pattern=re.compile(r"^(pytest|pytest-[a-z0-9_-]+)"),
        ecosystem="python",
    ),
]


class PackageBatch:
    """A cohesive group of dependency changes to be upgraded together in one PR."""

    def __init__(
        self,
        family_id: str,
        display_name: str,
        changes: list[DependencyChange] | None = None,
    ):
        self.family_id = family_id
        self.display_name = display_name
        self.changes: list[DependencyChange] = changes or []

    def add_change(self, change: DependencyChange) -> None:
        self.changes.append(change)

    @property
    def highest_tier(self) -> DependencyTier:
        if any(c.tier == DependencyTier.TIER_3_CRITICAL for c in self.changes):
            return DependencyTier.TIER_3_CRITICAL
        if any(c.tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR for c in self.changes):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR
        return DependencyTier.TIER_1_SAFE

    @property
    def primary_version(self) -> str:
        """Representative target version for the batch."""
        if not self.changes:
            return "update"
        # If any core package has a version, pick it
        for c in self.changes:
            if c.package_name in ("@angular/core", "react", "Microsoft.NET.TargetFramework", "fastapi"):
                return c.to_version.lstrip("^~>=<")
        return self.changes[0].to_version.lstrip("^~>=<")

    @property
    def pr_title(self) -> str:
        tier = self.highest_tier.value
        ver = self.primary_version
        if len(self.changes) == 1:
            return f"chore(deps): upgrade {self.changes[0].package_name} to {self.changes[0].to_version} ({tier})"
        return f"chore(deps): upgrade {self.display_name} to v{ver} ({tier})"

    @property
    def branch_slug(self) -> str:
        """Clean branch identifier slug (e.g. 'angular-framework-stack-18.2.0')."""
        clean_name = re.sub(r"[^a-zA-Z0-9]+", "-", self.display_name.lower()).strip("-")
        clean_ver = re.sub(r"[^a-zA-Z0-9.]+", "", self.primary_version)
        return f"{clean_name}-{clean_ver}"


def match_family(package_name: str, ecosystem: str | None = None) -> PackageFamilyRule | None:
    """Match a package name against known family rules."""
    for rule in FAMILY_RULES:
        if ecosystem and rule.ecosystem and rule.ecosystem != ecosystem:
            continue
        if rule.pattern.search(package_name):
            return rule
    return None


def group_dependency_changes(
    changes: list[DependencyChange],
    ecosystem: str | None = None,
) -> list[PackageBatch]:
    """
    Partition dependency changes into cohesive batches.

    Related packages (e.g. all @angular/* packages) are placed into the same batch.
    Standalone packages that don't belong to a known family each get their own batch.
    """
    if not changes:
        return []

    batches_by_family: dict[str, PackageBatch] = {}
    standalone_batches: list[PackageBatch] = []

    for change in changes:
        matched_rule = match_family(change.package_name, ecosystem=ecosystem)
        if matched_rule:
            if matched_rule.family_id not in batches_by_family:
                batches_by_family[matched_rule.family_id] = PackageBatch(
                    family_id=matched_rule.family_id,
                    display_name=matched_rule.display_name,
                )
            batches_by_family[matched_rule.family_id].add_change(change)
        else:
            # Standalone package batch
            batch = PackageBatch(
                family_id=f"pkg-{change.package_name.lower()}",
                display_name=change.package_name,
                changes=[change],
            )
            standalone_batches.append(batch)

    # Return cohesive family batches first, followed by standalone packages
    return list(batches_by_family.values()) + standalone_batches
