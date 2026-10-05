"""Unit and integration tests for .NET ecosystem adapter."""

from pathlib import Path
from unittest.mock import patch

from amstralift.adapters.dotnet import DotNetAdapter, classify_dotnet_tier
from amstralift.core.models import DependencyTier
from amstralift.core.workspace import get_head_commit, run_git
from amstralift.service import UpgradeOrchestrator


def test_classify_dotnet_tier():
    assert classify_dotnet_tier("Microsoft.AspNetCore.Authentication.JwtBearer") == DependencyTier.TIER_3_CRITICAL
    assert classify_dotnet_tier("Stripe.net") == DependencyTier.TIER_3_CRITICAL
    assert classify_dotnet_tier("Microsoft.EntityFrameworkCore.SqlServer") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_dotnet_tier("MediatR") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_dotnet_tier("Serilog") == DependencyTier.TIER_1_SAFE
    assert classify_dotnet_tier("Newtonsoft.Json") == DependencyTier.TIER_1_SAFE


def test_dotnet_adapter_detect(tmp_path: Path):
    adapter = DotNetAdapter()
    assert not adapter.detect(tmp_path)

    (tmp_path / "App.csproj").write_text("<Project Sdk='Microsoft.NET.Sdk'></Project>", encoding="utf-8")
    assert adapter.detect(tmp_path)


def test_dotnet_adapter_apply_upgrade(tmp_path: Path):
    adapter = DotNetAdapter()
    csproj = tmp_path / "MyService.csproj"
    csproj_content = """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Serilog" Version="3.0.1" />
    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />
  </ItemGroup>
</Project>
"""
    csproj.write_text(csproj_content, encoding="utf-8")

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="Serilog",
            from_version="3.0.1",
            to_version="4.0.0",
            tier=DependencyTier.TIER_1_SAFE,
        ),
        DependencyChange(
            package_name="Microsoft.EntityFrameworkCore",
            from_version="8.0.0",
            to_version="8.0.8",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)
    updated = csproj.read_text(encoding="utf-8")
    assert '<PackageReference Include="Serilog" Version="4.0.0"' in updated
    assert '<PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.8"' in updated


def test_dotnet_end_to_end_workflow(tmp_path: Path):
    """Verify full two-stage upgrade workflow on a .NET repository."""
    repo_path = tmp_path / "dotnet-repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=repo_path)
    run_git(["config", "user.name", "Test User"], cwd=repo_path)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_path)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_path)

    csproj_content = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Authentication.JwtBearer" Version="8.0.0" />
    <PackageReference Include="Serilog" Version="3.0.0" />
  </ItemGroup>
</Project>
"""
    (repo_path / "Api.csproj").write_text(csproj_content, encoding="utf-8")
    (repo_path / "Program.cs").write_text("// App entry point\n", encoding="utf-8")

    run_git(["add", "."], cwd=repo_path)
    run_git(["commit", "-m", "Initial .NET service"], cwd=repo_path)
    base_sha = get_head_commit(repo_path)

    secret = b"test-dotnet-secret-key-32b-secret"
    orchestrator = UpgradeOrchestrator(secret_key=secret)
    # Use cross-platform test/build commands for the hermetic mock repo
    orchestrator.adapters["dotnet"] = DotNetAdapter(
        test_command='python -c "import sys; sys.exit(0)"',
        build_command='python -c "import sys; sys.exit(0)"',
    )

    def mock_fetch(pkg: str):
        versions = {
            "Microsoft.AspNetCore.Authentication.JwtBearer": "8.0.8",
            "Serilog": "4.0.1",
        }
        return versions.get(pkg)

    with patch.object(DotNetAdapter, "fetch_latest_version", side_effect=mock_fetch):
        signed_bundle, pr_proposal = orchestrator.run_upgrade(
            repo_path=repo_path,
            ecosystem="dotnet",
            target_branch="main",
            dry_run=False,
        )

    bundle = signed_bundle.bundle
    assert bundle.base_commit_sha == base_sha
    assert bundle.highest_tier == DependencyTier.TIER_3_CRITICAL  # Auth package
    assert "tier-3-critical" in pr_proposal.labels
    assert "requires-manual-security-verification" in pr_proposal.labels

    current_head = get_head_commit(repo_path)
    assert current_head != base_sha
    updated_csproj = (repo_path / "Api.csproj").read_text(encoding="utf-8")
    assert 'Version="8.0.8"' in updated_csproj


def test_dotnet_lifecycle_governance():
    from amstralift.governance.dotnet_lifecycle import DotNetLifecycleGovernance

    decision = DotNetLifecycleGovernance.evaluate_tfm("net9.0", prefer_lts=True, incremental=True)
    assert decision is not None
    assert decision.should_upgrade is True
    assert decision.current_major == 9
    assert decision.current_release_type == "sts"
    assert decision.target_tfm == "net10.0"
    assert decision.target_major == 10
    assert decision.target_release_type == "lts"


def test_dotnet_target_framework_upgrade(tmp_path: Path):
    adapter = DotNetAdapter()
    csproj = tmp_path / "Service.csproj"
    csproj.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )

    candidates = adapter.discover_candidates(tmp_path)
    tfm_cand = [c for c in candidates if c.package_name == "Microsoft.NET.TargetFramework"]
    assert len(tfm_cand) == 1
    assert tfm_cand[0].to_version == "net10.0"

    adapter.apply_upgrade(tmp_path, candidates)
    updated = csproj.read_text(encoding="utf-8")
    assert "<TargetFramework>net10.0</TargetFramework>" in updated


def test_dotnet_cpm_discovery_and_upgrade(tmp_path: Path):
    """Verify Central Package Management (Directory.Packages.props) and Directory.Build.props support."""
    adapter = DotNetAdapter()

    # Directory.Packages.props (Central Package Management)
    cpm_props = tmp_path / "Directory.Packages.props"
    cpm_props.write_text(
        """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageVersion Include="Microsoft.Extensions.Logging" Version="7.0.0" />
    <PackageVersion Include="Newtonsoft.Json">
      <Version>12.0.1</Version>
    </PackageVersion>
    <GlobalPackageReference Include="SonarAnalyzer.CSharp" Version="8.0.0" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    # Directory.Build.props (Central TargetFramework)
    build_props = tmp_path / "Directory.Build.props"
    build_props.write_text(
        """<Project>
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
</Project>""",
        encoding="utf-8",
    )

    # App.csproj with no Version attributes (CPM pattern)
    subproject = tmp_path / "src" / "Api"
    subproject.mkdir(parents=True, exist_ok=True)
    csproj = subproject / "Api.csproj"
    csproj.write_text(
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Logging" />
    <PackageReference Include="Newtonsoft.Json" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    assert adapter.detect(tmp_path) is True

    # 1. Test get_declared_dependencies
    declared = adapter.get_declared_dependencies(tmp_path)
    assert declared.get("Microsoft.Extensions.Logging") == "7.0.0"
    assert declared.get("Newtonsoft.Json") == "12.0.1"
    assert declared.get("SonarAnalyzer.CSharp") == "8.0.0"

    # 2. Test discover_candidates (with mocked latest versions)
    def mock_fetch(pkg: str):
        versions = {
            "Microsoft.Extensions.Logging": "8.0.0",
            "Newtonsoft.Json": "13.0.3",
            "SonarAnalyzer.CSharp": "9.0.0",
        }
        return versions.get(pkg)

    with patch.object(DotNetAdapter, "fetch_latest_version", side_effect=mock_fetch):
        candidates = adapter.discover_candidates(tmp_path)

    pkg_names = {c.package_name: c.to_version for c in candidates}
    assert pkg_names.get("Microsoft.Extensions.Logging") == "8.0.0"
    assert pkg_names.get("Newtonsoft.Json") == "13.0.3"
    assert pkg_names.get("SonarAnalyzer.CSharp") == "9.0.0"
    assert pkg_names.get("Microsoft.NET.TargetFramework") == "net10.0"

    # 3. Test apply_upgrade updates both Directory.Packages.props and Directory.Build.props
    adapter.apply_upgrade(tmp_path, candidates)

    updated_cpm = cpm_props.read_text(encoding="utf-8")
    assert 'PackageVersion Include="Microsoft.Extensions.Logging" Version="8.0.0"' in updated_cpm
    assert "13.0.3" in updated_cpm
    assert 'GlobalPackageReference Include="SonarAnalyzer.CSharp" Version="9.0.0"' in updated_cpm

    updated_build_props = build_props.read_text(encoding="utf-8")
    assert "<TargetFramework>net10.0</TargetFramework>" in updated_build_props


def test_dotnet_format_timeout_resilience(tmp_path: Path):
    """Verify that dotnet format timing out does not abort the migration."""
    import subprocess

    adapter = DotNetAdapter()

    with patch("shutil.which", return_value="C:\\Program Files\\dotnet\\dotnet.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["dotnet", "format", "style"], timeout=60)):
        results = adapter.apply_modernizations(tmp_path, ["style"])

    assert len(results) == 1
    assert "timed out after 60s; upgrade preserved" in results[0]


