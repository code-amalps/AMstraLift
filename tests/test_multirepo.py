"""Unit tests for Multi-Repository Modernization & Fleet Orchestration Engine (P21).

Engineered by Amal P S for AMstraLift Generation 3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amstralift.cli import app
from amstralift.multirepo import (
    FleetDependencyGraph,
    FleetExecutionWave,
    FleetManifest,
    FleetManifestRepo,
    FleetPlanner,
    FleetScanner,
    FleetTopology,
    InterRepoCycleError,
    InterRepoDependency,
    MultiRepoModernizationPlan,
    PackageExport,
    PackageImport,
    RepositoryNode,
    RepositoryNotFoundError,
)

runner = CliRunner()


@pytest.fixture
def mock_fleet_dir(tmp_path: Path) -> Path:
    """Create a temporary enterprise fleet directory with multiple interconnected repositories."""
    fleet_root = tmp_path / "enterprise-org"
    fleet_root.mkdir()

    # 1. repo-shared-lib (Angular shared component library)
    lib_dir = fleet_root / "repo-shared-lib"
    lib_dir.mkdir()
    (lib_dir / ".git").mkdir()
    (lib_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (lib_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "@org/shared-ui",
                "version": "1.0.0",
                "dependencies": {
                    "@angular/core": "16.2.0",
                },
            }
        ),
        encoding="utf-8",
    )

    # 2. repo-service-a (Angular consumer of @org/shared-ui)
    svc_a = fleet_root / "repo-service-a"
    svc_a.mkdir()
    (svc_a / ".git").mkdir()
    (svc_a / ".git" / "HEAD").write_text("ref: refs/heads/feature/modernize\n", encoding="utf-8")
    (svc_a / "package.json").write_text(
        json.dumps(
            {
                "name": "service-a-portal",
                "version": "2.0.0",
                "dependencies": {
                    "@angular/core": "16.2.0",
                    "@org/shared-ui": "^1.0.0",
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. repo-service-b (React consumer of @org/shared-ui)
    svc_b = fleet_root / "repo-service-b"
    svc_b.mkdir()
    (svc_b / ".git").mkdir()
    (svc_b / "package.json").write_text(
        json.dumps(
            {
                "name": "service-b-dashboard",
                "version": "3.1.0",
                "dependencies": {
                    "react": "18.2.0",
                    "@org/shared-ui": "1.0.0",
                },
            }
        ),
        encoding="utf-8",
    )

    # 4. repo-isolated-dotnet (.NET 8 isolated microservice)
    dotnet_dir = fleet_root / "repo-dotnet-api"
    dotnet_dir.mkdir()
    (dotnet_dir / ".git").mkdir()
    (dotnet_dir / "BackendApi.csproj").write_text(
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <PackageId>Org.BackendApi</PackageId>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    # 5. repo-python-sdk (Python isolated library)
    py_dir = fleet_root / "repo-python-sdk"
    py_dir.mkdir()
    (py_dir / "pyproject.toml").write_text(
        """[project]
name = "org-common-sdk"
version = "0.5.0"
dependencies = [
    "pydantic>=2.0.0",
]
""",
        encoding="utf-8",
    )

    return fleet_root


# ==============================================================================
# Model Tests
# ==============================================================================


def test_models_instantiation():
    """Verify data models and methods."""
    export = PackageExport(package_name="@org/core", version="1.0.0", ecosystem="angular")
    import_dep = PackageImport(package_name="@org/core", version_spec="^1.0.0", ecosystem="angular")

    repo = RepositoryNode(
        repo_id="test-repo",
        repo_path="/fake/path",
        ecosystems=["angular"],
        primary_ecosystem="angular",
        exports=[export],
        imports=[import_dep],
    )
    assert "@org/core" in repo.exported_package_names()
    assert "@org/core" in repo.imported_package_names()

    dep = InterRepoDependency(
        consumer_repo_id="consumer",
        provider_repo_id="provider",
        package_name="@org/core",
    )
    assert dep.consumer_repo_id == "consumer"
    assert dep.provider_repo_id == "provider"

    topo = FleetTopology(
        root_path="/fake",
        repositories={"test-repo": repo},
        dependencies=[dep],
    )
    assert topo.get_consumers("provider") == ["consumer"]
    assert topo.get_providers("consumer") == ["provider"]


# ==============================================================================
# Scanner Tests
# ==============================================================================


def test_fleet_scanner_directory(mock_fleet_dir: Path):
    """Verify scanning directory discovers repos and consumer-provider relationships."""
    topology = FleetScanner.scan_directory(mock_fleet_dir)

    assert len(topology.repositories) == 5
    assert "repo-shared-lib" in topology.repositories
    assert "repo-service-a" in topology.repositories
    assert "repo-service-b" in topology.repositories
    assert "repo-dotnet-api" in topology.repositories
    assert "repo-python-sdk" in topology.repositories

    # Verify git branch detection
    assert topology.repositories["repo-shared-lib"].git_branch == "main"
    assert topology.repositories["repo-service-a"].git_branch == "feature/modernize"

    # Verify provider and consumer roles
    assert "repo-shared-lib" in topology.provider_repos
    assert "repo-service-a" in topology.consumer_repos
    assert "repo-service-b" in topology.consumer_repos
    assert "repo-dotnet-api" in topology.isolated_repos

    # Verify inter-repo dependencies
    assert len(topology.dependencies) == 2
    deps = {(d.provider_repo_id, d.consumer_repo_id) for d in topology.dependencies}
    assert ("repo-shared-lib", "repo-service-a") in deps
    assert ("repo-shared-lib", "repo-service-b") in deps


def test_fleet_scanner_manifest(tmp_path: Path):
    """Verify loading from fleet.yaml manifest."""
    manifest_root = tmp_path / "fleet-manifest-test"
    manifest_root.mkdir()

    repo1 = manifest_root / "repo-1"
    repo1.mkdir()
    (repo1 / "package.json").write_text(json.dumps({"name": "pkg-one", "dependencies": {}}), encoding="utf-8")

    repo2 = manifest_root / "repo-2"
    repo2.mkdir()
    (repo2 / "package.json").write_text(json.dumps({"name": "pkg-two", "dependencies": {}}), encoding="utf-8")

    manifest_file = manifest_root / "fleet.yaml"
    manifest_file.write_text(
        """name: test-manifest
version: '1.0'
repositories:
  - id: custom-lib
    path: repo-1
    tags: [core, library]
  - id: custom-app
    path: repo-2
    upstream: [custom-lib]
""",
        encoding="utf-8",
    )

    topology = FleetScanner.load_manifest(manifest_file)
    assert len(topology.repositories) == 2
    assert "custom-lib" in topology.repositories
    assert "custom-app" in topology.repositories
    assert topology.repositories["custom-lib"].tags == ["core", "library"]

    assert len(topology.dependencies) == 1
    dep = topology.dependencies[0]
    assert dep.consumer_repo_id == "custom-app"
    assert dep.provider_repo_id == "custom-lib"


# ==============================================================================
# Graph & Topological Wave Tests
# ==============================================================================


def test_graph_waves_topological_order(mock_fleet_dir: Path):
    """Verify topological execution waves schedule providers in Wave 1 and consumers in Wave 2."""
    topology = FleetScanner.scan_directory(mock_fleet_dir)
    graph = FleetDependencyGraph(topology)

    waves = graph.compute_execution_waves()
    assert len(waves) == 2

    wave1 = waves[0]
    wave2 = waves[1]

    assert wave1.wave_number == 1
    # repo-shared-lib must be in Wave 1
    assert "repo-shared-lib" in wave1.repo_ids
    # Independent repos with 0 upstream dependencies can also be in Wave 1
    assert "repo-dotnet-api" in wave1.repo_ids
    assert "repo-python-sdk" in wave1.repo_ids

    assert wave2.wave_number == 2
    # Consumers of repo-shared-lib must be scheduled in Wave 2
    assert "repo-service-a" in wave2.repo_ids
    assert "repo-service-b" in wave2.repo_ids


def test_graph_multi_tier_waves():
    """Verify multi-tier dependency waves (Wave 1 -> Wave 2 -> Wave 3)."""
    node_lib = RepositoryNode(repo_id="core-lib", repo_path="/lib")
    node_mid = RepositoryNode(repo_id="mid-service", repo_path="/mid")
    node_top = RepositoryNode(repo_id="top-portal", repo_path="/top")

    deps = [
        InterRepoDependency(consumer_repo_id="mid-service", provider_repo_id="core-lib", package_name="core"),
        InterRepoDependency(consumer_repo_id="top-portal", provider_repo_id="mid-service", package_name="mid"),
    ]

    topo = FleetTopology(
        root_path="/fake",
        repositories={"core-lib": node_lib, "mid-service": node_mid, "top-portal": node_top},
        dependencies=deps,
    )

    graph = FleetDependencyGraph(topo)
    waves = graph.compute_execution_waves()

    assert len(waves) == 3
    assert waves[0].repo_ids == ["core-lib"]
    assert waves[1].repo_ids == ["mid-service"]
    assert waves[2].repo_ids == ["top-portal"]


def test_graph_cycle_detection():
    """Verify inter-repository circular dependencies raise InterRepoCycleError."""
    node_a = RepositoryNode(repo_id="repo-a", repo_path="/a")
    node_b = RepositoryNode(repo_id="repo-b", repo_path="/b")

    deps = [
        InterRepoDependency(consumer_repo_id="repo-a", provider_repo_id="repo-b", package_name="pkg-b"),
        InterRepoDependency(consumer_repo_id="repo-b", provider_repo_id="repo-a", package_name="pkg-a"),
    ]

    topo = FleetTopology(
        root_path="/fake",
        repositories={"repo-a": node_a, "repo-b": node_b},
        dependencies=deps,
    )

    with pytest.raises(InterRepoCycleError) as exc_info:
        FleetDependencyGraph(topo)

    assert "repo-a" in str(exc_info.value)
    assert "repo-b" in str(exc_info.value)


def test_graph_queries():
    """Verify upstream and downstream dependency queries."""
    node_a = RepositoryNode(repo_id="repo-a", repo_path="/a")
    node_b = RepositoryNode(repo_id="repo-b", repo_path="/b")

    deps = [
        InterRepoDependency(consumer_repo_id="repo-b", provider_repo_id="repo-a", package_name="pkg-a"),
    ]

    topo = FleetTopology(
        root_path="/fake",
        repositories={"repo-a": node_a, "repo-b": node_b},
        dependencies=deps,
    )

    graph = FleetDependencyGraph(topo)
    assert graph.get_upstream("repo-b") == ["repo-a"]
    assert graph.get_downstream("repo-a") == ["repo-b"]
    assert graph.get_all_upstream("repo-b") == {"repo-a"}
    assert graph.get_all_downstream("repo-a") == {"repo-b"}

    with pytest.raises(RepositoryNotFoundError):
        graph.get_upstream("nonexistent-repo")


# ==============================================================================
# Planner Tests
# ==============================================================================


def test_fleet_planner_generation(mock_fleet_dir: Path):
    """Verify plan generation and persistence."""
    topology = FleetScanner.scan_directory(mock_fleet_dir)
    plan = FleetPlanner.generate_plan(topology, target_major=19, ecosystem="angular")

    assert plan.target_version == 19
    assert plan.ecosystem_filter == "angular"
    assert plan.total_repositories >= 2
    assert len(plan.waves) >= 1
    assert "Amal P S" in plan.author

    # Save and reload plan
    plan_file = mock_fleet_dir / "fleet-plan.json"
    FleetPlanner.save_plan(plan, plan_file)
    assert plan_file.is_file()

    loaded = FleetPlanner.load_plan(plan_file)
    assert loaded.plan_id == plan.plan_id
    assert loaded.target_version == 19


# ==============================================================================
# CLI Command Tests
# ==============================================================================


def test_cli_fleet_scan(mock_fleet_dir: Path):
    """Verify amstralift fleet scan command."""
    res = runner.invoke(app, ["fleet", "scan", "--dir", str(mock_fleet_dir)])
    assert res.exit_code == 0
    assert "Multi-Repository Fleet Topology" in res.stdout
    assert "repo-shared-lib" in res.stdout
    assert "repo-service-a" in res.stdout


def test_cli_fleet_scan_json(mock_fleet_dir: Path):
    """Verify amstralift fleet scan --json command."""
    res = runner.invoke(app, ["fleet", "scan", "--dir", str(mock_fleet_dir), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert "repositories" in data
    assert "repo-shared-lib" in data["repositories"]


def test_cli_fleet_graph(mock_fleet_dir: Path):
    """Verify amstralift fleet graph command."""
    res = runner.invoke(app, ["fleet", "graph", "--dir", str(mock_fleet_dir)])
    assert res.exit_code == 0
    assert "Cross-Repository Dependency Graph" in res.stdout
    assert "PASSED" in res.stdout


def test_cli_fleet_graph_json(mock_fleet_dir: Path):
    """Verify amstralift fleet graph --json command."""
    res = runner.invoke(app, ["fleet", "graph", "--dir", str(mock_fleet_dir), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert "dependencies" in data
    assert len(data["dependencies"]) == 2


def test_cli_fleet_plan(mock_fleet_dir: Path):
    """Verify amstralift fleet plan command."""
    plan_out = mock_fleet_dir / "generated-plan.json"
    res = runner.invoke(
        app,
        ["fleet", "plan", "--dir", str(mock_fleet_dir), "--target", "19", "-o", str(plan_out)],
    )
    assert res.exit_code == 0
    assert "Multi-Repository Modernization Plan" in res.stdout
    assert "Wave 1" in res.stdout
    assert plan_out.is_file()


def test_cli_fleet_plan_json(mock_fleet_dir: Path):
    """Verify amstralift fleet plan --json command."""
    res = runner.invoke(app, ["fleet", "plan", "--dir", str(mock_fleet_dir), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert "waves" in data
    assert "dependency_matrix" in data


def test_cli_multirepo_alias(mock_fleet_dir: Path):
    """Verify amstralift multirepo alias."""
    res = runner.invoke(app, ["multirepo", "scan", "--dir", str(mock_fleet_dir)])
    assert res.exit_code == 0
    assert "Multi-Repository Fleet Topology" in res.stdout
