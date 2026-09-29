"""Tests for the intelligent dependency grouping and batching module."""

from amstralift.core.grouping import group_dependency_changes, match_family
from amstralift.core.models import DependencyChange, DependencyTier


def test_match_family():
    # Angular
    rule_core = match_family("@angular/core", ecosystem="angular")
    assert rule_core is not None
    assert rule_core.family_id == "angular-core"

    rule_ngrx = match_family("@ngrx/store", ecosystem="angular")
    assert rule_ngrx is not None
    assert rule_ngrx.family_id == "ngrx"

    # React
    rule_react = match_family("react", ecosystem="react")
    assert rule_react is not None
    assert rule_react.family_id == "react-core"

    # .NET
    rule_aspnet = match_family("Microsoft.AspNetCore.Authentication", ecosystem="dotnet")
    assert rule_aspnet is not None
    assert rule_aspnet.family_id == "aspnetcore"

    # Standalone / Unknown
    rule_unknown = match_family("lodash", ecosystem="angular")
    assert rule_unknown is None


def test_group_angular_stack():
    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="14.2.0",
            to_version="14.3.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="@angular/common",
            from_version="14.2.0",
            to_version="14.3.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="@angular/router",
            from_version="14.2.0",
            to_version="14.3.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]

    batches = group_dependency_changes(changes, ecosystem="angular")
    assert len(batches) == 1
    batch = batches[0]
    assert batch.family_id == "angular-core"
    assert batch.display_name == "Angular Framework Stack"
    assert len(batch.changes) == 3
    assert batch.primary_version == "14.3.0"
    assert "Angular Framework Stack" in batch.pr_title
    assert "v14.3.0" in batch.pr_title
    assert "angular-framework-stack-14.3.0" in batch.branch_slug


def test_group_mixed_packages():
    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="14.0.0",
            to_version="14.3.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="@ngrx/store",
            from_version="14.0.0",
            to_version="14.3.3",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="lodash",
            from_version="4.17.20",
            to_version="4.17.21",
            tier=DependencyTier.TIER_1_SAFE,
        ),
    ]

    batches = group_dependency_changes(changes, ecosystem="angular")
    assert len(batches) == 3

    family_ids = [b.family_id for b in batches]
    assert "angular-core" in family_ids
    assert "ngrx" in family_ids
    assert "pkg-lodash" in family_ids

    lodash_batch = next(b for b in batches if b.family_id == "pkg-lodash")
    assert lodash_batch.display_name == "lodash"
    assert "upgrade lodash to 4.17.21" in lodash_batch.pr_title


def test_group_dotnet_and_python():
    dotnet_changes = [
        DependencyChange(
            package_name="Microsoft.AspNetCore.Mvc",
            from_version="8.0.0",
            to_version="9.0.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="Microsoft.EntityFrameworkCore.SqlServer",
            from_version="8.0.0",
            to_version="9.0.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]
    dotnet_batches = group_dependency_changes(dotnet_changes, ecosystem="dotnet")
    assert len(dotnet_batches) == 2
    assert {b.family_id for b in dotnet_batches} == {"aspnetcore", "efcore"}

    python_changes = [
        DependencyChange(
            package_name="fastapi",
            from_version="0.100.0",
            to_version="0.111.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="pydantic",
            from_version="2.0.0",
            to_version="2.7.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]
    python_batches = group_dependency_changes(python_changes, ecosystem="python")
    assert len(python_batches) == 1
    assert python_batches[0].family_id == "fastapi-stack"
