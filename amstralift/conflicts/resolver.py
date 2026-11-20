"""Pre-execution Conflict Resolver (P18).

Applies deterministic resolution strategies (discarding superseded legacy rules,
injecting sequence orderings for file collisions) to turn candidate conflict sets
into safe, conflict-free modernization plans.

Engineered by Amal P S for AMstraLift Generation 2.
"""

from amstralift.conflicts.models import (
    ConflictAnalysisReport,
    ConflictResolutionStrategy,
    ConflictSeverity,
    DetectedConflict,
)
from amstralift.transforms.graph import TransformationGraph
from amstralift.transforms.models import EdgeType


CATEGORY_PRIORITY = {
    "dependency": 0,
    "framework": 1,
    "config": 2,
    "ast": 3,
    "style": 4,
    "cleanup": 5,
}


class ConflictResolver:
    """Resolves detected cross-transformation conflicts prior to workspace execution."""

    def __init__(self, graph: TransformationGraph) -> None:
        self.graph = graph

    def resolve(
        self,
        report: ConflictAnalysisReport,
        active_node_ids: list[str],
    ) -> ConflictAnalysisReport:
        """Resolve all resolvable conflicts and return updated analysis report."""
        resolved_nodes = list(active_node_ids)
        discarded_rules: list[str] = []
        injected_orderings: list[tuple[str, str]] = []

        updated_conflicts: list[DetectedConflict] = []

        for conflict in report.conflicts:
            c = conflict.model_copy()

            if c.resolution_strategy == ConflictResolutionStrategy.DISCARD_RULE:
                # Determine which rule to discard
                to_discard = c.rule_b if "legacy" in c.rule_b else c.rule_a
                if to_discard in resolved_nodes:
                    resolved_nodes.remove(to_discard)
                    discarded_rules.append(to_discard)
                c.resolved = True
                updated_conflicts.append(c)

            elif c.resolution_strategy == ConflictResolutionStrategy.SEQUENCE_ORDER:
                # Inject ordering between rule_a and rule_b
                node_a = self.graph.get_node(c.rule_a)
                node_b = self.graph.get_node(c.rule_b)

                prio_a = CATEGORY_PRIORITY.get(node_a.category.value if node_a else "", 99)
                prio_b = CATEGORY_PRIORITY.get(node_b.category.value if node_b else "", 99)

                # Prioritize lower category priority number or alphabetical
                if prio_a <= prio_b:
                    first, second = c.rule_a, c.rule_b
                else:
                    first, second = c.rule_b, c.rule_a

                self.graph.add_edge(
                    first,
                    second,
                    EdgeType.ORDERING,
                    f"Injected ordering to resolve file collision on {c.conflicting_resource}",
                )
                injected_orderings.append((first, second))
                c.resolved = True
                updated_conflicts.append(c)

            elif c.resolution_strategy == ConflictResolutionStrategy.ISOLATE_PHASE:
                c.resolved = True
                updated_conflicts.append(c)

            else:
                # Unresolvable ABORT conflict
                c.resolved = False
                updated_conflicts.append(c)

        # Topological sort of the resolved nodes
        try:
            final_sequence = self.graph.topological_sort(resolved_nodes)
        except Exception:
            final_sequence = sorted(resolved_nodes)

        unresolved_blocking = [
            c for c in updated_conflicts if not c.resolved and c.severity == ConflictSeverity.BLOCKING
        ]

        return ConflictAnalysisReport(
            total_conflicts=len(updated_conflicts),
            blocking_count=len([c for c in updated_conflicts if c.severity == ConflictSeverity.BLOCKING]),
            warning_count=len([c for c in updated_conflicts if c.severity == ConflictSeverity.WARNING]),
            conflicts=updated_conflicts,
            safe_to_execute=len(unresolved_blocking) == 0,
            resolved_sequence=final_sequence,
            discarded_rules=sorted(list(set(discarded_rules))),
            injected_orderings=injected_orderings,
            metadata=report.metadata,
        )
