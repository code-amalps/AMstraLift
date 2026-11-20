"""Pre-execution Cross-Transformation Conflict Detector (P18).

Analyzes candidates, dependencies, file boundaries, AST symbol surfaces, and configuration
keys to detect conflicts before touching workspace files or executing upgrades.

Engineered by Amal P S for AMstraLift Generation 2.
"""

from pathlib import Path
from typing import Any

from amstralift.conflicts.models import (
    ConflictAnalysisReport,
    ConflictCategory,
    ConflictResolutionStrategy,
    ConflictSeverity,
    ConflictType,
    DetectedConflict,
)
from amstralift.transforms.graph import TransformationGraph, _file_patterns_conflict
from amstralift.transforms.models import EdgeType, TransformationNode


# Known symbol interactions and breaking migrations
KNOWN_SYMBOL_COLLISIONS: list[dict[str, Any]] = [
    {
        "rule_a": "angular:standalone",
        "rule_b": "angular:legacy-ngmodule-scaffold",
        "resource": "@NgModule",
        "description": "Standalone architecture prunes and eliminates NgModule registration symbols.",
        "resolution": ConflictResolutionStrategy.DISCARD_RULE,
    },
    {
        "rule_a": "dotnet:cpm",
        "rule_b": "dotnet:legacy-packages-config",
        "resource": "packages.config / PackageReference",
        "description": "Central Package Management requires Directory.Packages.props and eliminates packages.config XML elements.",
        "resolution": ConflictResolutionStrategy.DISCARD_RULE,
    },
    {
        "rule_a": "python:ruff",
        "rule_b": "python:legacy-flake8-black",
        "resource": ".flake8 / [tool.black]",
        "description": "Ruff unifies formatting and linting, superseding legacy black/flake8 configuration blocks.",
        "resolution": ConflictResolutionStrategy.DISCARD_RULE,
    },
]

# Known configuration key collisions
KNOWN_CONFIG_COLLISIONS: list[dict[str, Any]] = [
    {
        "rule_a": "angular:workspace-config",
        "rule_b": "angular:legacy-browser-target",
        "file": "angular.json",
        "key": "architect.build.builder",
        "description": "Modern Angular workspace uses @angular-devkit/build-angular:application while legacy uses :browser.",
        "resolution": ConflictResolutionStrategy.DISCARD_RULE,
    },
]


class PreExecutionConflictDetector:
    """Detects file collisions, AST symbol conflicts, and configuration mismatches before execution."""

    def __init__(self, graph: TransformationGraph) -> None:
        self.graph = graph

    def detect_all(
        self,
        active_node_ids: list[str],
        repo_path: Path | None = None,
    ) -> ConflictAnalysisReport:
        """Run complete pre-execution conflict analysis over scheduled transformations."""
        active_set = set(active_node_ids)
        conflicts: list[DetectedConflict] = []
        conf_counter = 1

        # 1. Explicit CONFLICT Edges
        for edge in self.graph.edges:
            if edge.edge_type == EdgeType.CONFLICT:
                if edge.source in active_set and edge.target in active_set:
                    strategy = ConflictResolutionStrategy.ABORT
                    if "legacy" in edge.target or "legacy" in edge.source:
                        strategy = ConflictResolutionStrategy.DISCARD_RULE

                    conflicts.append(
                        DetectedConflict(
                            id=f"conf_{conf_counter:03d}",
                            conflict_type=ConflictType.EXPLICIT_CONFLICT,
                            severity=ConflictSeverity.BLOCKING,
                            rule_a=edge.source,
                            rule_b=edge.target,
                            description=edge.reason or "Explicit conflict declared in dependency DAG",
                            conflicting_resource=f"{edge.source} <-> {edge.target}",
                            resolution_strategy=strategy,
                            resolution_summary=f"Discard '{edge.target if 'legacy' in edge.target else edge.source}' or sequence execution.",
                        )
                    )
                    conf_counter += 1

        # 2. Invalidation Edges (Precondition Invalidation)
        for edge in self.graph.edges:
            if edge.edge_type == EdgeType.INVALIDATION:
                if edge.source in active_set and edge.target in active_set:
                    conflicts.append(
                        DetectedConflict(
                            id=f"conf_{conf_counter:03d}",
                            conflict_type=ConflictType.PRECONDITION_INVALIDATION,
                            severity=ConflictSeverity.BLOCKING,
                            rule_a=edge.source,
                            rule_b=edge.target,
                            description=f"Execution of '{edge.source}' invalidates the preconditions of '{edge.target}'.",
                            conflicting_resource=edge.target,
                            resolution_strategy=ConflictResolutionStrategy.DISCARD_RULE,
                            resolution_summary=f"Exclude invalidated rule '{edge.target}' from modernization plan.",
                        )
                    )
                    conf_counter += 1

        # 3. File Collisions for Unordered Transformations
        active_list = sorted(list(active_set))
        # Build set of ordered pairs
        ordered_pairs: set[tuple[str, str]] = set()
        for u in active_list:
            for v in self.graph.get_dependents(u, transitive=True):
                ordered_pairs.add((u, v))
            for edge in self.graph.edges:
                if edge.edge_type == EdgeType.ORDERING:
                    ordered_pairs.add((edge.source, edge.target))

        for i in range(len(active_list)):
            node_a = self.graph.get_node(active_list[i])
            if not node_a:
                continue
            for j in range(i + 1, len(active_list)):
                node_b = self.graph.get_node(active_list[j])
                if not node_b:
                    continue

                # If they are ordered, sequential execution prevents workspace file races
                if (node_a.id, node_b.id) in ordered_pairs or (node_b.id, node_a.id) in ordered_pairs:
                    continue

                overlaps = _file_patterns_conflict(
                    node_a.affected_file_patterns,
                    node_b.affected_file_patterns,
                )
                if overlaps:
                    conflicts.append(
                        DetectedConflict(
                            id=f"conf_{conf_counter:03d}",
                            conflict_type=ConflictType.FILE_COLLISION,
                            severity=ConflictSeverity.WARNING,
                            rule_a=node_a.id,
                            rule_b=node_b.id,
                            description=f"Unordered transformations mutate the same target file patterns: {', '.join(overlaps)}",
                            conflicting_resource=", ".join(overlaps),
                            resolution_strategy=ConflictResolutionStrategy.SEQUENCE_ORDER,
                            resolution_summary=f"Inject ordering constraint or isolate into distinct execution phases.",
                        )
                    )
                    conf_counter += 1

        # 4. Known Symbol Collisions
        for sc in KNOWN_SYMBOL_COLLISIONS:
            if sc["rule_a"] in active_set and sc["rule_b"] in active_set:
                # Check if already caught
                already_reported = any(
                    c.conflict_type == ConflictType.SYMBOL_COLLISION
                    and set([c.rule_a, c.rule_b]) == set([sc["rule_a"], sc["rule_b"]])
                    for c in conflicts
                )
                if not already_reported:
                    conflicts.append(
                        DetectedConflict(
                            id=f"conf_{conf_counter:03d}",
                            conflict_type=ConflictType.SYMBOL_COLLISION,
                            severity=ConflictSeverity.BLOCKING,
                            rule_a=sc["rule_a"],
                            rule_b=sc["rule_b"],
                            description=sc["description"],
                            conflicting_resource=sc["resource"],
                            resolution_strategy=sc["resolution"],
                            resolution_summary=f"Discard incompatible rule '{sc['rule_b']}'.",
                        )
                    )
                    conf_counter += 1

        # 5. Known Configuration Key Collisions
        for cc in KNOWN_CONFIG_COLLISIONS:
            if cc["rule_a"] in active_set and cc["rule_b"] in active_set:
                conflicts.append(
                    DetectedConflict(
                        id=f"conf_{conf_counter:03d}",
                        conflict_type=ConflictType.CONFIG_KEY_COLLISION,
                        severity=ConflictSeverity.BLOCKING,
                        rule_a=cc["rule_a"],
                        rule_b=cc["rule_b"],
                        description=cc["description"],
                        conflicting_resource=f"{cc['file']}::{cc['key']}",
                        resolution_strategy=cc["resolution"],
                        resolution_summary=f"Discard conflicting configuration rule '{cc['rule_b']}'.",
                    )
                )
                conf_counter += 1

        blocking = [c for c in conflicts if c.severity == ConflictSeverity.BLOCKING]
        warnings = [c for c in conflicts if c.severity == ConflictSeverity.WARNING]

        return ConflictAnalysisReport(
            total_conflicts=len(conflicts),
            blocking_count=len(blocking),
            warning_count=len(warnings),
            conflicts=conflicts,
            safe_to_execute=len(blocking) == 0,
            metadata={
                "scanned_rules": active_node_ids,
                "repo_path": str(repo_path) if repo_path else None,
            },
        )
