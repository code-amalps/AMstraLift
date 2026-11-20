"""Unit tests for Transformation Dependency Graph Engine (P16).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
import pytest
from typer.testing import CliRunner

from amstralift.cli import app
from amstralift.transforms import (
    ConflictingTransformationError,
    CycleDependencyError,
    DependencyEdge,
    EdgeType,
    InvalidationError,
    MissingPrerequisiteError,
    TransformationCategory,
    TransformationGraph,
    TransformationGraphError,
    TransformationNode,
    TransformationRisk,
    build_default_graph,
    get_standard_transformations,
    get_transformations_for_ecosystem,
)


runner = CliRunner()


def test_models_instantiation_and_serialization():
    """Verify node and edge model properties, defaults, and serializability."""
    node = TransformationNode(
        id="test:rule-a",
        name="Rule A",
        description="First rule",
        ecosystem="angular",
        category=TransformationCategory.AST,
        risk=TransformationRisk.LOW,
        affected_file_patterns=["**/*.ts"],
        atomic_boundary="test:boundary-1",
    )
    assert node.id == "test:rule-a"
    assert node.category == TransformationCategory.AST
    assert node.risk == TransformationRisk.LOW
    assert node.affected_file_patterns == ["**/*.ts"]

    data = node.model_dump()
    deserialized = TransformationNode(**data)
    assert deserialized == node

    edge = DependencyEdge(
        source="test:rule-a",
        target="test:rule-b",
        edge_type=EdgeType.PREREQUISITE,
        reason="A is required before B",
    )
    assert edge.source == "test:rule-a"
    assert edge.target == "test:rule-b"
    assert edge.edge_type == EdgeType.PREREQUISITE


def test_graph_add_nodes_and_edges():
    """Test adding nodes and edges, retrieving them, and idempotency."""
    graph = TransformationGraph()
    n1 = TransformationNode(id="t1", name="T1", ecosystem="angular")
    n2 = TransformationNode(id="t2", name="T2", ecosystem="angular")
    graph.add_node(n1)
    graph.add_node(n2)

    assert graph.has_node("t1")
    assert graph.has_node("t2")
    assert not graph.has_node("t3")
    assert graph.get_node("t1") == n1

    graph.add_edge("t1", "t2", EdgeType.PREREQUISITE, "t1 precedes t2")
    # Duplicate edge addition should be a no-op
    graph.add_edge("t1", "t2", EdgeType.PREREQUISITE, "t1 precedes t2")
    assert len(graph.edges) == 1


def test_prerequisites_direct_and_transitive():
    """Test direct and deep transitive prerequisite retrieval."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="a", name="A", ecosystem="test"))
    graph.add_node(TransformationNode(id="b", name="B", ecosystem="test"))
    graph.add_node(TransformationNode(id="c", name="C", ecosystem="test"))
    graph.add_node(TransformationNode(id="d", name="D", ecosystem="test"))

    graph.add_edge("a", "b", EdgeType.PREREQUISITE)
    graph.add_edge("b", "c", EdgeType.PREREQUISITE)
    graph.add_edge("c", "d", EdgeType.PREREQUISITE)

    assert graph.get_prerequisites("d", transitive=False) == {"c"}
    assert graph.get_prerequisites("d", transitive=True) == {"a", "b", "c"}
    assert graph.get_prerequisites("b", transitive=True) == {"a"}
    assert graph.get_prerequisites("a", transitive=True) == set()


def test_dependents_direct_and_transitive():
    """Test direct and deep transitive dependent retrieval."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="a", name="A", ecosystem="test"))
    graph.add_node(TransformationNode(id="b", name="B", ecosystem="test"))
    graph.add_node(TransformationNode(id="c", name="C", ecosystem="test"))

    graph.add_edge("a", "b", EdgeType.PREREQUISITE)
    graph.add_edge("b", "c", EdgeType.PREREQUISITE)

    assert graph.get_dependents("a", transitive=False) == {"b"}
    assert graph.get_dependents("a", transitive=True) == {"b", "c"}
    assert graph.get_dependents("c", transitive=True) == set()


def test_cycle_detection_simple():
    """Verify that a simple 2-node cycle is detected and raises CycleDependencyError."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="a", name="A", ecosystem="test"))
    graph.add_node(TransformationNode(id="b", name="B", ecosystem="test"))
    graph.add_edge("a", "b", EdgeType.PREREQUISITE)
    graph.add_edge("b", "a", EdgeType.PREREQUISITE)

    cycles = graph.detect_cycles()
    assert len(cycles) > 0
    with pytest.raises(CycleDependencyError) as exc_info:
        graph.validate()
    assert "Circular dependency cycle detected" in str(exc_info.value)


def test_cycle_detection_deep():
    """Verify cycle detection in a multi-hop loop: A -> B -> C -> D -> B."""
    graph = TransformationGraph()
    for x in ["a", "b", "c", "d"]:
        graph.add_node(TransformationNode(id=x, name=x.upper(), ecosystem="test"))
    graph.add_edge("a", "b", EdgeType.PREREQUISITE)
    graph.add_edge("b", "c", EdgeType.PREREQUISITE)
    graph.add_edge("c", "d", EdgeType.PREREQUISITE)
    graph.add_edge("d", "b", EdgeType.PREREQUISITE)

    with pytest.raises(CycleDependencyError):
        graph.topological_sort()


def test_self_cycle_detection():
    """Verify that a node having a self-dependency loop raises CycleDependencyError."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="self_loop", name="Loop", ecosystem="test"))
    graph.add_edge("self_loop", "self_loop", EdgeType.PREREQUISITE)

    cycles = graph.detect_cycles()
    assert len(cycles) > 0
    with pytest.raises(CycleDependencyError):
        graph.validate()


def test_missing_prerequisite_validation():
    """Verify that scheduling a node without its required prerequisite raises MissingPrerequisiteError."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="req", name="Required", ecosystem="test"))
    graph.add_node(TransformationNode(id="tgt", name="Target", ecosystem="test"))
    graph.add_edge("req", "tgt", EdgeType.PREREQUISITE, "req is mandatory")

    # When both are selected, validation succeeds
    graph.validate(selected_node_ids=["req", "tgt"])

    # When only target is selected, validation fails
    with pytest.raises(MissingPrerequisiteError) as exc_info:
        graph.validate(selected_node_ids=["tgt"])
    assert exc_info.value.node_id == "tgt"
    assert exc_info.value.missing_prerequisite_id == "req"


def test_explicit_conflict_detection():
    """Verify that scheduling two mutually exclusive transformations raises ConflictingTransformationError."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="new_way", name="New Way", ecosystem="test"))
    graph.add_node(TransformationNode(id="legacy_way", name="Legacy Way", ecosystem="test"))
    graph.add_edge("new_way", "legacy_way", EdgeType.CONFLICT, "Mutually incompatible approaches")

    # Individual execution is fine
    graph.validate(selected_node_ids=["new_way"])
    graph.validate(selected_node_ids=["legacy_way"])

    # Combined execution raises conflict error
    with pytest.raises(ConflictingTransformationError) as exc_info:
        graph.validate(selected_node_ids=["new_way", "legacy_way"])
    assert "new_way" in str(exc_info.value)
    assert "legacy_way" in str(exc_info.value)


def test_file_overlap_conflict_detection():
    """Verify resource overlap conflict detection for unordered nodes touching same files."""
    graph = TransformationGraph()
    graph.add_node(
        TransformationNode(
            id="rule_1",
            name="Rule 1",
            ecosystem="test",
            affected_file_patterns=["angular.json"],
        )
    )
    graph.add_node(
        TransformationNode(
            id="rule_2",
            name="Rule 2",
            ecosystem="test",
            affected_file_patterns=["angular.json"],
        )
    )

    conflicts = graph.find_conflicts(check_file_overlap=True)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "resource_overlap"
    assert "angular.json" in conflicts[0].reason


def test_ordered_file_overlap_allowed():
    """Verify that nodes touching the same file do NOT conflict if they are strictly ordered."""
    graph = TransformationGraph()
    graph.add_node(
        TransformationNode(
            id="rule_1",
            name="Rule 1",
            ecosystem="test",
            affected_file_patterns=["angular.json"],
        )
    )
    graph.add_node(
        TransformationNode(
            id="rule_2",
            name="Rule 2",
            ecosystem="test",
            affected_file_patterns=["angular.json"],
        )
    )
    # Establish strict ordering
    graph.add_edge("rule_1", "rule_2", EdgeType.ORDERING, "Rule 1 runs before Rule 2")

    conflicts = graph.find_conflicts(check_file_overlap=True)
    assert len(conflicts) == 0


def test_invalidation_detection():
    """Verify that invalidation edges are detected and trigger InvalidationError."""
    graph = TransformationGraph()
    graph.add_node(TransformationNode(id="modernizer", name="Modernizer", ecosystem="test"))
    graph.add_node(TransformationNode(id="legacy_fix", name="Legacy Fix", ecosystem="test"))
    graph.add_edge("modernizer", "legacy_fix", EdgeType.INVALIDATION, "Modernizer supersedes legacy fix")

    inv_map = graph.find_invalidations(["modernizer", "legacy_fix"])
    assert "legacy_fix" in inv_map
    assert inv_map["legacy_fix"] == ["modernizer"]

    with pytest.raises(InvalidationError) as exc_info:
        graph.validate(selected_node_ids=["modernizer", "legacy_fix"])
    assert exc_info.value.invalidated_id == "legacy_fix"
    assert exc_info.value.invalidator_id == "modernizer"


def test_topological_sort_deterministic():
    """Verify deterministic topological sorting honoring dependencies and tie-breaking."""
    graph = TransformationGraph()
    # Create diamond dependency: A -> B, A -> C, B -> D, C -> D
    for x in ["a", "b", "c", "d"]:
        graph.add_node(TransformationNode(id=x, name=x.upper(), ecosystem="test"))
    graph.add_edge("a", "b", EdgeType.PREREQUISITE)
    graph.add_edge("a", "c", EdgeType.PREREQUISITE)
    graph.add_edge("b", "d", EdgeType.PREREQUISITE)
    graph.add_edge("c", "d", EdgeType.PREREQUISITE)

    # Deterministic tie-breaking should sort B before C alphabetically
    order_1 = graph.topological_sort()
    order_2 = graph.topological_sort()
    assert order_1 == ["a", "b", "c", "d"]
    assert order_1 == order_2


def test_execution_phases_partitioning():
    """Verify partitioning of DAG into sequential execution waves."""
    graph = TransformationGraph()
    for x in ["r1", "r2", "mid1", "mid2", "final"]:
        graph.add_node(TransformationNode(id=x, name=x, ecosystem="test"))

    # Roots: r1, r2
    # Mid: mid1 depends on r1, mid2 depends on r2
    # Final: depends on mid1 and mid2
    graph.add_edge("r1", "mid1", EdgeType.PREREQUISITE)
    graph.add_edge("r2", "mid2", EdgeType.PREREQUISITE)
    graph.add_edge("mid1", "final", EdgeType.PREREQUISITE)
    graph.add_edge("mid2", "final", EdgeType.PREREQUISITE)

    phases = graph.get_execution_phases()
    assert phases == [
        ["r1", "r2"],
        ["mid1", "mid2"],
        ["final"],
    ]


def test_parallel_batches_disjoint_patterns():
    """Verify parallel batch partitioning based on affected file patterns."""
    graph = TransformationGraph()
    graph.add_node(
        TransformationNode(
            id="html_transform",
            name="HTML",
            ecosystem="angular",
            affected_file_patterns=["**/*.html"],
        )
    )
    graph.add_node(
        TransformationNode(
            id="ts_transform",
            name="TypeScript",
            ecosystem="angular",
            affected_file_patterns=["**/*.ts"],
        )
    )
    graph.add_node(
        TransformationNode(
            id="second_html_transform",
            name="HTML 2",
            ecosystem="angular",
            affected_file_patterns=["**/*.html"],
        )
    )

    batches = graph.get_parallel_batches(["html_transform", "ts_transform", "second_html_transform"])
    # html_transform and ts_transform have disjoint extensions, so they run concurrently in Batch 0
    # second_html_transform conflicts on **/*.html, so it runs in Batch 1
    assert len(batches) == 2
    batch_0_nodes = set(batches[0].nodes)
    assert "html_transform" in batch_0_nodes
    assert "ts_transform" in batch_0_nodes
    assert batches[0].is_concurrent is True
    assert batches[1].nodes == ["second_html_transform"]


def test_rollback_boundaries_atomic_group_and_dependents():
    """Verify rollback boundaries calculate atomic group peers and transitive dependents."""
    graph = TransformationGraph()
    # Quartet nodes sharing atomic boundary 'quartet'
    graph.add_node(
        TransformationNode(
            id="q_standalone",
            name="Standalone",
            ecosystem="angular",
            atomic_boundary="quartet",
        )
    )
    graph.add_node(
        TransformationNode(
            id="q_control_flow",
            name="Control Flow",
            ecosystem="angular",
            atomic_boundary="quartet",
        )
    )
    graph.add_node(
        TransformationNode(
            id="downstream_cleanup",
            name="Cleanup",
            ecosystem="angular",
        )
    )

    graph.add_edge("q_standalone", "q_control_flow", EdgeType.PREREQUISITE)
    graph.add_edge("q_control_flow", "downstream_cleanup", EdgeType.PREREQUISITE)

    boundaries = graph.get_rollback_boundaries()
    # Failing q_standalone must roll back q_standalone, q_control_flow (same boundary) AND downstream_cleanup
    assert boundaries["q_standalone"] == {"q_standalone", "q_control_flow", "downstream_cleanup"}
    # Failing q_control_flow also rolls back boundary peers and downstream dependents
    assert boundaries["q_control_flow"] == {"q_standalone", "q_control_flow", "downstream_cleanup"}


def test_filter_subgraph():
    """Verify subgraph isolation preserves only selected nodes and relevant internal edges."""
    default_g = build_default_graph()
    angular_g = default_g.filter_subgraph(["angular:standalone", "angular:control-flow"])

    assert len(angular_g.nodes) == 2
    assert "angular:standalone" in angular_g.nodes
    assert "angular:control-flow" in angular_g.nodes
    assert "dotnet:cpm" not in angular_g.nodes

    # The prerequisite edge between standalone and control-flow should be preserved
    assert len(angular_g.edges) == 1
    assert angular_g.edges[0].source == "angular:standalone"
    assert angular_g.edges[0].target == "angular:control-flow"


def test_graph_to_dict_and_from_dict():
    """Verify lossless serialization and deserialization of the transformation graph."""
    graph = build_default_graph()
    d = graph.to_dict()
    assert "nodes" in d
    assert "edges" in d

    restored = TransformationGraph.from_dict(d)
    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert restored.get_node("angular:control-flow") == graph.get_node("angular:control-flow")


def test_default_graph_registry():
    """Verify standard transformations and built-in rules across Angular, .NET, Python, and React."""
    all_transforms = get_standard_transformations()
    assert len(all_transforms) >= 20

    angular_transforms = get_transformations_for_ecosystem("angular")
    angular_ids = {t.id for t in angular_transforms}
    assert "angular:workspace-config" in angular_ids
    assert "angular:ecosystem-align" in angular_ids
    assert "angular:standalone" in angular_ids
    assert "angular:control-flow" in angular_ids
    assert "angular:signals" in angular_ids
    assert "angular:inject" in angular_ids
    assert "angular:self-closing" in angular_ids

    dotnet_transforms = get_transformations_for_ecosystem("dotnet")
    dotnet_ids = {t.id for t in dotnet_transforms}
    assert "dotnet:cpm" in dotnet_ids
    assert "dotnet:format" in dotnet_ids

    python_transforms = get_transformations_for_ecosystem("python")
    python_ids = {t.id for t in python_transforms}
    assert "python:ruff" in python_ids
    assert "python:pyproject-pep621" in python_ids

    react_transforms = get_transformations_for_ecosystem("react")
    react_ids = {t.id for t in react_transforms}
    assert "react:jsx-transform" in react_ids


def test_cli_graph_overview():
    """Verify `amstralift graph` standard overview CLI output."""
    result = runner.invoke(app, ["graph"])
    assert result.exit_code == 0
    assert "Transformation Dependency Graph (P16)" in result.stdout
    assert "angular:standalone" in result.stdout
    assert "dotnet:cpm" in result.stdout
    assert "python:ruff" in result.stdout


def test_cli_graph_filtered_by_ecosystem():
    """Verify `amstralift graph --ecosystem angular` filters to Angular only."""
    result = runner.invoke(app, ["graph", "--ecosystem", "angular"])
    assert result.exit_code == 0
    assert "angular:control-flow" in result.stdout
    assert "dotnet:cpm" not in result.stdout


def test_cli_graph_phases():
    """Verify `amstralift graph --phases` displays sequential execution waves."""
    result = runner.invoke(app, ["graph", "--ecosystem", "angular", "--phases"])
    assert result.exit_code == 0
    assert "Execution Waves" in result.stdout
    assert "Phase 1" in result.stdout


def test_cli_graph_batches():
    """Verify `amstralift graph --batches` displays concurrent batches."""
    result = runner.invoke(app, ["graph", "--ecosystem", "angular", "--batches"])
    assert result.exit_code == 0
    assert "Parallelizable Execution Batches" in result.stdout
    assert "Batch #1" in result.stdout
    assert "[CONCURRENT]" in result.stdout


def test_cli_graph_rollback():
    """Verify `amstralift graph --rollback <id>` displays blast radius."""
    result = runner.invoke(app, ["graph", "--rollback", "angular:standalone"])
    assert result.exit_code == 0
    assert "Atomic Rollback Boundary" in result.stdout
    assert "angular:ast-quartet" in result.stdout
    assert "angular:control-flow" in result.stdout


def test_cli_graph_json():
    """Verify `amstralift graph --json` produces valid JSON payload."""
    result = runner.invoke(app, ["graph", "--ecosystem", "react", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "nodes" in data
    assert "edges" in data
    assert any(n["id"] == "react:jsx-transform" for n in data["nodes"])


def test_cli_graph_unknown_ecosystem():
    """Verify `amstralift graph --ecosystem unknown` fails with exit code 1."""
    result = runner.invoke(app, ["graph", "--ecosystem", "unknown"])
    assert result.exit_code == 1
    assert "Unknown ecosystem" in result.stdout
