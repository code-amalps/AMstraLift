"""Unit tests for Pre-execution Cross-Transformation Conflict Detection (P18).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
from pathlib import Path
from typer.testing import CliRunner

import pytest

from amstralift.cli import app
from amstralift.conflicts import (
    ConflictAnalysisReport,
    ConflictResolutionStrategy,
    ConflictSeverity,
    ConflictType,
    ConflictResolver,
    PreExecutionConflictDetector,
)
from amstralift.planning import ModernizationPlanner, TransformationStatus
from amstralift.transforms.graph import TransformationGraph
from amstralift.transforms.models import (
    EdgeType,
    TransformationCategory,
    TransformationNode,
    TransformationRisk,
)
from amstralift.transforms.registry import build_default_graph


@pytest.fixture
def empty_graph() -> TransformationGraph:
    return TransformationGraph()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_explicit_conflict_detection(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:rule-a",
        name="Rule A",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    node_b = TransformationNode(
        id="test:rule-b-legacy",
        name="Rule B Legacy",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)
    empty_graph.add_edge("test:rule-a", "test:rule-b-legacy", EdgeType.CONFLICT, "Incompatible architectural models")

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:rule-a", "test:rule-b-legacy"])

    assert report.total_conflicts >= 1
    assert report.blocking_count >= 1
    assert not report.safe_to_execute

    explicit_confs = [c for c in report.conflicts if c.conflict_type == ConflictType.EXPLICIT_CONFLICT]
    assert len(explicit_confs) == 1
    assert explicit_confs[0].severity == ConflictSeverity.BLOCKING
    assert explicit_confs[0].resolution_strategy == ConflictResolutionStrategy.DISCARD_RULE


def test_precondition_invalidation_detection(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:rule-a",
        name="Rule A",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    node_b = TransformationNode(
        id="test:rule-b",
        name="Rule B",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)
    empty_graph.add_edge("test:rule-a", "test:rule-b", EdgeType.INVALIDATION, "Invalidates AST structures")

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:rule-a", "test:rule-b"])

    inval_confs = [c for c in report.conflicts if c.conflict_type == ConflictType.PRECONDITION_INVALIDATION]
    assert len(inval_confs) == 1
    assert inval_confs[0].severity == ConflictSeverity.BLOCKING
    assert inval_confs[0].resolution_strategy == ConflictResolutionStrategy.DISCARD_RULE


def test_file_collision_detection(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:mod-a",
        name="Mod A",
        ecosystem="test",
        affected_file_patterns=["**/*.html"],
        category=TransformationCategory.AST,
    )
    node_b = TransformationNode(
        id="test:mod-b",
        name="Mod B",
        ecosystem="test",
        affected_file_patterns=["**/*.html"],
        category=TransformationCategory.AST,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:mod-a", "test:mod-b"])

    file_confs = [c for c in report.conflicts if c.conflict_type == ConflictType.FILE_COLLISION]
    assert len(file_confs) == 1
    assert file_confs[0].severity == ConflictSeverity.WARNING
    assert file_confs[0].resolution_strategy == ConflictResolutionStrategy.SEQUENCE_ORDER


def test_file_collision_suppressed_when_sequenced(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:mod-a",
        name="Mod A",
        ecosystem="test",
        affected_file_patterns=["**/*.html"],
        category=TransformationCategory.AST,
    )
    node_b = TransformationNode(
        id="test:mod-b",
        name="Mod B",
        ecosystem="test",
        affected_file_patterns=["**/*.html"],
        category=TransformationCategory.AST,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)
    # Add dependency or ordering edge
    empty_graph.add_edge("test:mod-a", "test:mod-b", EdgeType.ORDERING)

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:mod-a", "test:mod-b"])

    file_confs = [c for c in report.conflicts if c.conflict_type == ConflictType.FILE_COLLISION]
    assert len(file_confs) == 0


def test_symbol_collision_detection():
    graph = build_default_graph()
    detector = PreExecutionConflictDetector(graph)
    report = detector.detect_all(["angular:standalone", "angular:legacy-ngmodule-scaffold"])

    symbol_confs = [c for c in report.conflicts if c.conflict_type == ConflictType.SYMBOL_COLLISION]
    assert len(symbol_confs) == 1
    assert symbol_confs[0].conflicting_resource == "@NgModule"
    assert symbol_confs[0].severity == ConflictSeverity.BLOCKING


def test_resolver_discards_legacy_rule(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:modern",
        name="Modern Feature",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    node_b = TransformationNode(
        id="test:legacy-scaffold",
        name="Legacy Scaffold",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)
    empty_graph.add_edge("test:modern", "test:legacy-scaffold", EdgeType.CONFLICT)

    detector = PreExecutionConflictDetector(empty_graph)
    initial_report = detector.detect_all(["test:modern", "test:legacy-scaffold"])
    assert not initial_report.safe_to_execute

    resolver = ConflictResolver(empty_graph)
    resolved_report = resolver.resolve(initial_report, ["test:modern", "test:legacy-scaffold"])

    assert resolved_report.safe_to_execute
    assert "test:legacy-scaffold" in resolved_report.discarded_rules
    assert "test:modern" in resolved_report.resolved_sequence
    assert "test:legacy-scaffold" not in resolved_report.resolved_sequence


def test_resolver_injects_sequence_ordering(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:config-rule",
        name="Config Rule",
        ecosystem="test",
        category=TransformationCategory.CONFIG,
        affected_file_patterns=["config.json"],
    )
    node_b = TransformationNode(
        id="test:ast-rule",
        name="AST Rule",
        ecosystem="test",
        category=TransformationCategory.AST,
        affected_file_patterns=["config.json"],
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:config-rule", "test:ast-rule"])

    resolver = ConflictResolver(empty_graph)
    resolved_report = resolver.resolve(report, ["test:config-rule", "test:ast-rule"])

    assert len(resolved_report.injected_orderings) == 1
    # Config priority (2) should precede AST priority (3)
    assert resolved_report.injected_orderings[0] == ("test:config-rule", "test:ast-rule")
    assert resolved_report.safe_to_execute


def test_unresolvable_conflict_halts_execution(empty_graph: TransformationGraph):
    node_a = TransformationNode(
        id="test:alpha",
        name="Alpha",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    node_b = TransformationNode(
        id="test:beta",
        name="Beta",
        ecosystem="test",
        category=TransformationCategory.FRAMEWORK,
    )
    empty_graph.add_node(node_a)
    empty_graph.add_node(node_b)
    # Neither has 'legacy' in ID, so default strategy is ABORT
    empty_graph.add_edge("test:alpha", "test:beta", EdgeType.CONFLICT)

    detector = PreExecutionConflictDetector(empty_graph)
    report = detector.detect_all(["test:alpha", "test:beta"])

    resolver = ConflictResolver(empty_graph)
    resolved_report = resolver.resolve(report, ["test:alpha", "test:beta"])

    assert not resolved_report.safe_to_execute
    assert any(c.resolved is False for c in resolved_report.conflicts)


def test_modernization_planner_integrates_conflict_resolution(tmp_path: Path):
    planner = ModernizationPlanner()
    plan = planner.generate_plan(tmp_path, ecosystem="angular", target_major=18)

    # Legacy node should be placed in refused transformations
    refused_ids = [r.id for r in plan.refused_transformations]
    assert "angular:legacy-ngmodule-scaffold" in refused_ids

    # Plan should contain conflict analysis metadata
    assert "conflict_analysis" in plan.metadata
    conflict_meta = plan.metadata["conflict_analysis"]
    assert conflict_meta["safe_to_execute"] is True
    assert "angular:legacy-ngmodule-scaffold" in conflict_meta["discarded_rules"]


def test_cli_conflicts_command_angular(runner: CliRunner):
    res = runner.invoke(app, ["conflicts", "--ecosystem", "angular"])
    assert res.exit_code == 0
    assert "Pre-Execution Conflict Detection Report (P18)" in res.stdout
    assert "SAFE TO EXECUTE" in res.stdout
    assert "angular:legacy-ngmodule-scaffold" in res.stdout


def test_cli_conflicts_command_no_resolve_fails(runner: CliRunner):
    res = runner.invoke(app, ["conflicts", "--ecosystem", "angular", "--no-resolve"])
    assert res.exit_code == 1
    assert "BLOCKED BY CONFLICTS" in res.stdout


def test_cli_conflicts_command_json(runner: CliRunner):
    res = runner.invoke(app, ["conflicts", "--ecosystem", "angular", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["safe_to_execute"] is True
    assert data["total_conflicts"] > 0
    assert "angular:legacy-ngmodule-scaffold" in data["discarded_rules"]
