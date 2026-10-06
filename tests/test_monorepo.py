"""Unit tests for MonorepoScanner and polyglot topology discovery."""

import json
from pathlib import Path

from amstralift.core.monorepo import MonorepoScanner


def test_single_project_root_not_monorepo(tmp_path: Path):
    """A standard repo with manifest at root is classified with is_monorepo=False."""
    pkg = {
        "name": "my-app",
        "dependencies": {
            "@angular/core": "^16.2.0",
        },
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    topo = MonorepoScanner.discover(tmp_path)
    assert topo.is_monorepo is False
    assert len(topo.projects) == 1
    assert topo.projects[0].ecosystem == "angular"
    assert topo.projects[0].rel_path == "."
    assert topo.projects[0].framework_version == "16.2.0"
    assert topo.ecosystems == ["angular"]


def test_polyglot_monorepo_discovery(tmp_path: Path):
    """Detects multi-ecosystem monorepos across Angular, .NET, Python, and React."""
    # 1. Angular Client
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "package.json").write_text(
        json.dumps({"name": "enterprise-client", "dependencies": {"@angular/core": "17.0.0"}}),
        encoding="utf-8",
    )

    # 2. .NET Server
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    csproj_content = """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>"""
    (server_dir / "EnterpriseApi.csproj").write_text(csproj_content, encoding="utf-8")

    # 3. Python Service
    py_dir = tmp_path / "services" / "analytics"
    py_dir.mkdir(parents=True)
    pyproject_content = """[project]
name = "analytics"
version = "0.1.0"
requires-python = ">=3.11"
"""
    (py_dir / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

    # 4. React Admin
    admin_dir = tmp_path / "admin-portal"
    admin_dir.mkdir()
    (admin_dir / "package.json").write_text(
        json.dumps({"name": "admin-portal", "dependencies": {"react": "18.2.0", "react-dom": "18.2.0"}}),
        encoding="utf-8",
    )

    topo = MonorepoScanner.discover(tmp_path)
    assert topo.is_monorepo is True
    assert len(topo.projects) == 4
    assert set(topo.ecosystems) == {"angular", "dotnet", "python", "react"}

    project_map = {p.rel_path: p for p in topo.projects}
    assert "client" in project_map
    assert project_map["client"].ecosystem == "angular"
    assert project_map["client"].framework_version == "17.0.0"

    assert "server" in project_map
    assert project_map["server"].ecosystem == "dotnet"
    assert project_map["server"].framework_version == "net8.0"

    assert "services/analytics" in project_map
    assert project_map["services/analytics"].ecosystem == "python"
    assert project_map["services/analytics"].framework_version == "3.11"

    assert "admin-portal" in project_map
    assert project_map["admin-portal"].ecosystem == "react"
    assert project_map["admin-portal"].framework_version == "18.2.0"


def test_monorepo_scanner_ignores_excluded_directories(tmp_path: Path):
    """Ignored directories like node_modules, .venv, bin, obj are skipped."""
    nm = tmp_path / "node_modules" / "some-lib"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text(json.dumps({"dependencies": {"react": "18.0.0"}}), encoding="utf-8")

    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyproject.toml").write_text("requires-python = '3.11'", encoding="utf-8")

    # Real project
    app = tmp_path / "src"
    app.mkdir()
    (app / "package.json").write_text(
        json.dumps({"name": "real-app", "dependencies": {"@angular/core": "16.0.0"}}),
        encoding="utf-8",
    )

    topo = MonorepoScanner.discover(tmp_path)
    assert len(topo.projects) == 1
    assert topo.projects[0].name == "real-app"
    assert topo.projects[0].rel_path == "src"


def test_orchestrator_monorepo_detection(tmp_path: Path):
    """UpgradeOrchestrator detects monorepo topology and enforces subproject targeting."""
    import pytest
    from amstralift.service import OrchestrationError, UpgradeOrchestrator

    # Set up client and server
    client_dir = tmp_path / "frontend"
    client_dir.mkdir()
    (client_dir / "package.json").write_text(
        json.dumps({"name": "frontend-app", "dependencies": {"react": "18.2.0"}}),
        encoding="utf-8",
    )

    server_dir = tmp_path / "backend"
    server_dir.mkdir()
    (server_dir / "Backend.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )

    orch = UpgradeOrchestrator()
    topo = orch.discover_monorepo(tmp_path)
    assert topo.is_monorepo is True
    assert len(topo.projects) == 2

    # Auto detect without subproject raises helpful error instructing user to pick
    with pytest.raises(OrchestrationError, match="Polyglot monorepo detected with 2 projects"):
        orch.auto_detect_ecosystem(tmp_path)

    # With subproject specified, detects properly
    assert orch.auto_detect_ecosystem(tmp_path, subproject="frontend") == "react"
    assert orch.auto_detect_ecosystem(tmp_path, subproject="backend") == "dotnet"


def test_cli_projects_command(tmp_path: Path):
    """CLI 'amstralift projects' lists detected projects in rich table."""
    from typer.testing import CliRunner
    from amstralift.cli import app

    runner = CliRunner()

    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "package.json").write_text(
        json.dumps({"name": "client-app", "dependencies": {"@angular/core": "17.0.0"}}),
        encoding="utf-8",
    )

    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "api.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["projects", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Monorepo Topology" in result.output
    assert "client-app" in result.output
    assert "ANGULAR" in result.output
    assert "api" in result.output
    assert "DOTNET" in result.output
    assert "Polyglot Monorepo detected!" in result.output


def test_cli_audit_with_project(tmp_path: Path):
    """CLI 'amstralift audit --project <path>' targets specific monorepo subproject."""
    from typer.testing import CliRunner
    from amstralift.cli import app

    runner = CliRunner()
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "package.json").write_text(
        json.dumps({"name": "client-app", "dependencies": {"react": "18.2.0"}}),
        encoding="utf-8",
    )
    (client_dir / "package-lock.json").write_text(
        json.dumps({"name": "client-app", "lockfileVersion": 3, "packages": {}}),
        encoding="utf-8",
    )
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "api.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["audit", "--repo", str(tmp_path), "--project", "client"])
    assert result.exit_code == 0
    assert "react (client) project" in result.output
    assert "Security Audit" in result.output


def test_orchestrator_run_upgrade_subproject(tmp_path: Path):
    """UpgradeOrchestrator executes Stage A and diffs properly for subproject."""
    import subprocess
    from amstralift.core.models import DependencyChange, DependencyTier
    from amstralift.service import UpgradeOrchestrator

    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=tmp_path, check=True, capture_output=True)

    # Subprojects
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps({"name": "frontend", "dependencies": {"react": "18.0.0"}}),
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text(
        json.dumps({"name": "frontend", "lockfileVersion": 3, "packages": {}}),
        encoding="utf-8",
    )

    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "Backend.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, check=True, capture_output=True)

    orch = UpgradeOrchestrator()
    change = DependencyChange(
        package_name="react",
        from_version="18.0.0",
        to_version="18.2.0",
        tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ecosystem="react",
        project_path="frontend",
    )

    signed_bundle, proposal = orch.run_upgrade(
        repo_path=tmp_path,
        subproject="frontend",
        target_branch="main",
        explicit_changes=[change],
        allow_failed_gates=True,
        dry_run=True,
    )

    assert signed_bundle.bundle.changes[0].package_name == "react"
    assert signed_bundle.bundle.changes[0].project_path == "frontend"
    assert "Subproject: frontend" in signed_bundle.bundle.advisory_notes
    assert "frontend/package.json" in signed_bundle.bundle.patch
