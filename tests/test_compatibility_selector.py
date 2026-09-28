"""Unit tests for CompatibilityAwareVersionSelector."""

from amstralift.security.compatibility_selector import CompatibilityAwareVersionSelector


def test_selects_patch_within_same_minor():
    """Prefers a minimal patch bump over higher minor or major releases."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="lodash",
        current_version="4.17.15",
        fixed_versions=["4.17.19", "4.17.21", "4.18.0", "5.0.0"],
        ecosystem="npm",
    )
    assert res.is_compatible
    assert res.target_version == "4.17.19"  # Lowest patch in 4.17.x
    assert not res.is_major_bump
    assert "patch release" in res.rationale
    assert len(res.breaking_risks) == 0


def test_selects_minor_when_no_patch_exists():
    """Selects lowest minor in same major when no patch exists in the current minor."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="express",
        current_version="4.16.4",
        fixed_versions=["4.17.3", "5.0.0"],
        ecosystem="npm",
    )
    assert res.is_compatible
    assert res.target_version == "4.17.3"
    assert not res.is_major_bump
    assert "same major (4.x)" in res.rationale


def test_falls_back_to_major_with_breaking_warning():
    """When only a higher major fix exists, flags is_major_bump and attaches breaking risk."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="webpack-dev-middleware",
        current_version="3.7.3",
        fixed_versions=["5.3.4", "6.0.0"],
        ecosystem="npm",
    )
    assert res.is_compatible
    assert res.target_version == "5.3.4"
    assert res.is_major_bump
    assert any("Major version bump from 3 to 5" in r for r in res.breaking_risks)


def test_prevents_unsolicited_framework_major_desync():
    """Prevents framework core packages from jumping major without framework upgrade."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="@angular/core",
        current_version="18.1.0",
        fixed_versions=["19.0.0", "20.0.0"],
        ecosystem="npm",
        project_context={"framework_major": 18},
    )
    assert not res.is_compatible
    assert res.target_version is None
    assert any("desyncs from core project framework" in reason for reason in res.rejected_candidates.values())
    assert res.advisory is not None


def test_unfixable_advisory_when_no_fixed_versions():
    """Generates remediation advisory when no fix is reported upstream."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="abandoned-lib",
        current_version="1.0.0",
        fixed_versions=[],
        ecosystem="npm",
    )
    assert not res.is_compatible
    assert res.target_version is None
    assert "No fixed versions reported" in res.rationale
    assert "abandoned-lib" in res.advisory


def test_rejects_dotnet_candidate_exceeding_target_framework():
    """Rejects .NET package version that requires a higher TargetFramework than the project."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="Microsoft.Extensions.Logging",
        current_version="9.0.0",
        fixed_versions=["10.0.1"],
        ecosystem="dotnet",
        project_context={"target_framework": "net9.0"},
    )
    assert not res.is_compatible
    assert res.target_version is None
    assert "10.0.1" in res.rejected_candidates
    assert "Requires TargetFramework net10.0, which exceeds project framework net9.0" in res.rejected_candidates["10.0.1"]
    assert "Manual Remediation Recommended" in res.advisory


def test_rejects_candidate_violating_peer_dependencies():
    """Rejects candidate whose peer dependencies conflict with installed project dependencies."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="@angular/material",
        current_version="18.0.0",
        fixed_versions=["19.0.2"],
        ecosystem="npm",
        project_context={
            "installed_dependencies": {"@angular/core": "18.2.0"},
            "peer_dependencies": {"19.0.2": {"@angular/core": "19.0.0"}},
        },
    )
    assert not res.is_compatible
    assert res.target_version is None
    assert "19.0.2" in res.rejected_candidates
    assert "peerDependency '@angular/core' '19.0.0' is not satisfied by installed 18.2.0" in res.rejected_candidates["19.0.2"]


def test_rejects_candidate_violating_manifest_version_constraint():
    """Rejects candidate that violates explicit upper-bound manifest constraint."""
    res = CompatibilityAwareVersionSelector.select_version(
        package_name="example-lib",
        current_version="4.2.0",
        fixed_versions=["5.1.0"],
        ecosystem="npm",
        project_context={"version_constraints": {"example-lib": "< 5.0.0"}},
    )
    assert not res.is_compatible
    assert res.target_version is None
    assert "5.1.0" in res.rejected_candidates
    assert "Violates project manifest constraint '< 5.0.0'" in res.rejected_candidates["5.1.0"]
