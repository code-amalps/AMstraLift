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
