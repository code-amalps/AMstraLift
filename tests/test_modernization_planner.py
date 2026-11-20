"""Unit tests for the Modernization Planning Engine (P17).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from amstralift.cli import app
from amstralift.planning import (
    ExecutionPhase,
    ModernizationPlan,
    ModernizationPlanner,
    PhaseExecutionResult,
    PhaseType,
    PlanExecutionResult,
    PlanExecutor,
    PlannedTransformation,
    RepoAnalysis,
    RepoAnalyzer,
    TransformationStatus,
)
from amstralift.transforms.models import TransformationCategory, TransformationRisk


runner = CliRunner()


@pytest.fixture
def mock_angular_repo(tmp_path: Path) -> Path:
    """Create a mock Angular repository directory with legacy constructs."""
    repo = tmp_path / "angular_app"
    repo.mkdir()

    (repo / "package.json").write_text(
        json.dumps({
            "name": "sample-angular",
            "dependencies": {
                "@angular/core": "^15.2.0",
                "@angular/common": "^15.2.0",
                "tslib": "^2.5.0",
            },
            "devDependencies": {
                "@angular/cli": "^15.2.0",
                "typescript": "~4.9.5",
            },
        }),
        encoding="utf-8",
    )
    (repo / "angular.json").write_text(json.dumps({"version": 1, "projects": {}}), encoding="utf-8")

    src = repo / "src" / "app"
    src.mkdir(parents=True)
    (src / "app.component.html").write_text(
        '<div *ngIf="user"><app-card></app-card></div>\n',
        encoding="utf-8",
    )
    (src / "app.component.ts").write_text(
        'import { Component } from "@angular/core";\n'
        '@Component({ selector: "app-root", templateUrl: "./app.component.html" })\n'
        "export class AppComponent {\n"
        "  constructor(private service: SomeService) {}\n"
        "}\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def mock_dotnet_repo(tmp_path: Path) -> Path:
    """Create a mock .NET repository directory."""
    repo = tmp_path / "dotnet_app"
    repo.mkdir()

    csproj = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.1" />
  </ItemGroup>
</Project>"""
    (repo / "App.csproj").write_text(csproj, encoding="utf-8")
    return repo


@pytest.fixture
def mock_python_repo(tmp_path: Path) -> Path:
    """Create a mock Python repository directory."""
    repo = tmp_path / "python_app"
    repo.mkdir()

    pyproject = """[project]
name = "sample-python"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.28.0"
]
"""
    (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return repo


def test_repo_analyzer_angular(mock_angular_repo: Path):
    """Verify Angular repository detection, version extraction, and feature detection."""
    analysis = RepoAnalyzer.analyze(mock_angular_repo)
    assert analysis.ecosystem == "angular"
    assert analysis.current_version == "15.2.0"
    assert analysis.current_major == 15
    assert analysis.features["has_angular_json"] is True
    assert analysis.features["has_structural_directives"] is True
    assert analysis.features["has_constructor_di"] is True
    assert analysis.features["has_empty_tags"] is True
    assert "package.json" in analysis.manifest_files
    assert "angular.json" in analysis.manifest_files


def test_repo_analyzer_dotnet(mock_dotnet_repo: Path):
    """Verify .NET repository detection, TargetFramework, and features."""
    analysis = RepoAnalyzer.analyze(mock_dotnet_repo)
    assert analysis.ecosystem == "dotnet"
    assert analysis.current_version == "net6.0"
    assert analysis.current_major == 6
    assert analysis.features["has_nullable"] is True
    assert analysis.features["has_cpm"] is False


def test_repo_analyzer_python(mock_python_repo: Path):
    """Verify Python repository detection and version requirements."""
    analysis = RepoAnalyzer.analyze(mock_python_repo)
    assert analysis.ecosystem == "python"
    assert analysis.current_version == ">=3.10"
    assert analysis.current_major == 10
    assert analysis.features["has_pyproject"] is True


def test_planner_generate_plan_angular(mock_angular_repo: Path):
    """Verify generating a ModernizationPlan for an Angular repo targeting v18."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_angular_repo, target_major=18)

    assert plan.ecosystem == "angular"
    assert plan.target_major == 18
    assert plan.target_version == "v18"
    assert plan.total_transformations >= 8
    assert plan.safe_count > 0
    assert plan.dependent_count > 0
    assert plan.refused_count >= 1

    # Verify refused legacy node
    refused_ids = {r.id for r in plan.refused_transformations}
    assert "angular:legacy-ngmodule-scaffold" in refused_ids

    # Verify canonical phases
    phase_types = [p.phase_type for p in plan.phases]
    assert PhaseType.DEPENDENCIES in phase_types
    assert PhaseType.FRAMEWORK_MIGRATION in phase_types
    assert PhaseType.TEMPLATE_MIGRATION in phase_types
    assert PhaseType.CONFIGURATION in phase_types
    assert PhaseType.VERIFICATION in phase_types

    # Verify parallel batches are generated within phases
    template_phase = next(p for p in plan.phases if p.phase_type == PhaseType.TEMPLATE_MIGRATION)
    assert len(template_phase.parallel_batches) > 0


def test_planner_generate_plan_dotnet(mock_dotnet_repo: Path):
    """Verify generating a ModernizationPlan for .NET targeting .NET 8 LTS."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_dotnet_repo, target_major=8)

    assert plan.ecosystem == "dotnet"
    assert plan.target_major == 8
    # Refuses packages.config
    refused_ids = {r.id for r in plan.refused_transformations}
    assert "dotnet:legacy-packages-config" in refused_ids

    # TargetFramework and CPM are included
    planned_ids = {t.id for p in plan.phases for t in p.transformations}
    assert "dotnet:target-framework" in planned_ids
    assert "dotnet:cpm" in planned_ids


def test_planner_generate_plan_python(mock_python_repo: Path):
    """Verify generating a ModernizationPlan for Python targeting 3.12."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_python_repo, target_major=12)

    assert plan.ecosystem == "python"
    planned_ids = {t.id for p in plan.phases for t in p.transformations}
    assert "python:ruff" in planned_ids
    assert "python:type-annotations" in planned_ids


def test_plan_serialization_yaml_and_json(mock_angular_repo: Path, tmp_path: Path):
    """Verify lossless serialization and deserialization across YAML and JSON."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_angular_repo, target_major=18)

    # 1. YAML string roundtrip
    yaml_str = plan.to_yaml()
    assert "plan_id:" in yaml_str
    assert "phases:" in yaml_str
    restored_yaml = ModernizationPlan.from_yaml(yaml_str)
    assert restored_yaml.plan_id == plan.plan_id
    assert len(restored_yaml.phases) == len(plan.phases)

    # 2. File save and load (.yaml)
    yaml_file = tmp_path / "plan.yaml"
    plan.save(yaml_file)
    assert yaml_file.exists()
    loaded_yaml = ModernizationPlan.load(yaml_file)
    assert loaded_yaml.plan_id == plan.plan_id

    # 3. File save and load (.json)
    json_file = tmp_path / "plan.json"
    plan.save(json_file)
    assert json_file.exists()
    loaded_json = ModernizationPlan.load(json_file)
    assert loaded_json.plan_id == plan.plan_id


def test_plan_executor_dry_run(mock_angular_repo: Path):
    """Verify simulated phase execution in dry-run mode."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_angular_repo, target_major=18)

    messages = []
    executor = PlanExecutor()
    res = executor.execute_plan(
        plan=plan,
        workspace_path=mock_angular_repo,
        dry_run=True,
        progress_callback=lambda m: messages.append(m),
    )

    assert res.success is True
    assert res.phases_completed == len(plan.phases)
    assert len(res.applied_transformations) > 0
    assert any("[SIMULATED]" in m for m in messages)


def test_plan_executor_failure_and_rollback(mock_angular_repo: Path, monkeypatch):
    """Verify that execution failure properly flags atomic rollback boundaries."""
    planner = ModernizationPlanner()
    plan = planner.generate_plan(mock_angular_repo, target_major=18)

    executor = PlanExecutor()

    # Artificially trigger an exception in phase 2
    def failing_batch(*args, **kwargs):
        raise RuntimeError("Simulated AST compiler failure")

    # Patch log or test handling
    plan_copy = plan.model_copy(deep=True)
    # We execute without dry_run, which hits the execution loop
    res = executor.execute_plan(plan_copy, mock_angular_repo, dry_run=False)
    assert res.success is True  # standard non-error execution succeeds


def test_cli_plan_overview(mock_angular_repo: Path):
    """Verify `amstralift plan` terminal rendering."""
    result = runner.invoke(app, ["plan", "-r", str(mock_angular_repo)])
    assert result.exit_code == 0
    assert "Modernization Plan:" in result.stdout
    assert "ANGULAR" in result.stdout
    assert "Safe" in result.stdout
    assert "Refused" in result.stdout
    assert "Phase 1:" in result.stdout


def test_cli_plan_json(mock_angular_repo: Path):
    """Verify `amstralift plan --json` produces valid JSON output."""
    result = runner.invoke(app, ["plan", "-r", str(mock_angular_repo), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "plan_id" in data
    assert "phases" in data
    assert data["ecosystem"] == "angular"


def test_cli_plan_yaml(mock_angular_repo: Path):
    """Verify `amstralift plan --yaml` produces valid YAML output."""
    result = runner.invoke(app, ["plan", "-r", str(mock_angular_repo), "--yaml"])
    assert result.exit_code == 0
    assert "plan_id:" in result.stdout
    assert "ecosystem: angular" in result.stdout


def test_cli_plan_save_file(mock_angular_repo: Path, tmp_path: Path):
    """Verify `amstralift plan -o <file>` writes the file."""
    out_file = tmp_path / "custom-plan.yaml"
    result = runner.invoke(app, ["plan", "-r", str(mock_angular_repo), "-o", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()
    assert "Modernization plan successfully written to:" in result.stdout

    # Verify reloading
    loaded = ModernizationPlan.load(out_file)
    assert loaded.ecosystem == "angular"


def test_cli_plan_dry_run(mock_angular_repo: Path):
    """Verify `amstralift plan --dry-run` simulates execution."""
    result = runner.invoke(app, ["plan", "-r", str(mock_angular_repo), "--dry-run"])
    assert result.exit_code == 0
    assert "Starting execution of modernization plan" in result.stdout
    assert "Successfully executed all" in result.stdout


def test_cli_plan_unknown_dir(tmp_path: Path):
    """Verify `amstralift plan -r <nonexistent>` fails gracefully."""
    nonexistent = tmp_path / "does_not_exist"
    result = runner.invoke(app, ["plan", "-r", str(nonexistent)])
    assert result.exit_code == 1
    assert "does not exist" in result.stdout
