"""Core Transformation Dependency Graph Engine (P16).

Implements Directed Acyclic Graph (DAG) modeling, topological sorting into execution waves,
deterministic parallel batch partitioning, atomic rollback boundary calculation,
and cross-transformation conflict and invalidation detection.

Engineered by Amal P S for AMstraLift Generation 2.
"""

from collections import defaultdict, deque
from collections.abc import Iterable
from fnmatch import fnmatch
from typing import Any

from amstralift.transforms.exceptions import (
    ConflictingTransformationError,
    CycleDependencyError,
    InvalidationError,
    MissingPrerequisiteError,
    TransformationGraphError,
)
from amstralift.transforms.models import (
    ConflictReport,
    DependencyEdge,
    EdgeType,
    ParallelBatch,
    TransformationNode,
)


def _file_patterns_conflict(patterns_a: list[str], patterns_b: list[str]) -> list[str]:
    """Return overlapping file patterns between two transformation nodes if any exist."""
    overlaps: list[str] = []
    for pa in patterns_a:
        for pb in patterns_b:
            clean_a = pa.strip().replace("\\", "/")
            clean_b = pb.strip().replace("\\", "/")
            if clean_a == clean_b:
                overlaps.append(f"{clean_a} == {clean_b}")
                continue
            # Universal wildcards
            if clean_a in ("*", "**/*", "**") or clean_b in ("*", "**/*", "**"):
                overlaps.append(f"{clean_a} <-> {clean_b}")
                continue
            # Extension or path overlap check
            if fnmatch(clean_a, clean_b) or fnmatch(clean_b, clean_a):
                overlaps.append(f"{clean_a} <-> {clean_b}")
                continue
            # Common file extension comparison
            ext_a = clean_a.rsplit(".", 1)[-1] if "." in clean_a else None
            ext_b = clean_b.rsplit(".", 1)[-1] if "." in clean_b else None
            if ext_a and ext_b and ext_a == ext_b and ("*" in clean_a or "*" in clean_b):
                overlaps.append(f"{clean_a} <-> {clean_b}")
    return sorted(list(set(overlaps)))


class TransformationGraph:
    """Directed Graph managing modernizations, prerequisites, conflicts, and rollback boundaries."""

    def __init__(
        self,
        nodes: list[TransformationNode] | None = None,
        edges: list[DependencyEdge] | None = None,
    ) -> None:
        self._nodes: dict[str, TransformationNode] = {}
        self._edges: list[DependencyEdge] = []

        if nodes:
            for node in nodes:
                self.add_node(node)
        if edges:
            for edge in edges:
                self.add_edge(edge.source, edge.target, edge.edge_type, edge.reason)

    @property
    def nodes(self) -> dict[str, TransformationNode]:
        """Mapping of node ID to TransformationNode."""
        return dict(self._nodes)

    @property
    def edges(self) -> list[DependencyEdge]:
        """List of all registered dependency edges."""
        return list(self._edges)

    def add_node(self, node: TransformationNode) -> None:
        """Register a transformation node in the graph."""
        self._nodes[node.id] = node

    def get_node(self, node_id: str) -> TransformationNode | None:
        """Retrieve a transformation node by ID, or None if absent."""
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        """Return True if the node exists in the graph."""
        return node_id in self._nodes

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: EdgeType = EdgeType.PREREQUISITE,
        reason: str = "",
    ) -> None:
        """Add a directed constraint edge from source to target."""
        # Avoid duplicate edges
        for existing in self._edges:
            if (
                existing.source == source
                and existing.target == target
                and existing.edge_type == edge_type
            ):
                return
        self._edges.append(
            DependencyEdge(source=source, target=target, edge_type=edge_type, reason=reason)
        )

    def get_prerequisites(self, node_id: str, transitive: bool = True) -> set[str]:
        """Return all node IDs that must precede node_id (PREREQUISITE edges)."""
        prereqs: set[str] = set()
        queue = deque([node_id])
        visited = {node_id}

        while queue:
            curr = queue.popleft()
            for edge in self._edges:
                if edge.target == curr and edge.edge_type == EdgeType.PREREQUISITE:
                    src = edge.source
                    if src not in prereqs:
                        prereqs.add(src)
                        if transitive and src not in visited:
                            visited.add(src)
                            queue.append(src)
        return prereqs

    def get_dependents(self, node_id: str, transitive: bool = True) -> set[str]:
        """Return all node IDs that depend on node_id (PREREQUISITE edges)."""
        deps: set[str] = set()
        queue = deque([node_id])
        visited = {node_id}

        while queue:
            curr = queue.popleft()
            for edge in self._edges:
                if edge.source == curr and edge.edge_type == EdgeType.PREREQUISITE:
                    tgt = edge.target
                    if tgt not in deps:
                        deps.add(tgt)
                        if transitive and tgt not in visited:
                            visited.add(tgt)
                            queue.append(tgt)
        return deps

    def detect_cycles(self, subset: Iterable[str] | None = None) -> list[list[str]]:
        """Detect any cycles among PREREQUISITE and ORDERING edges.

        Returns a list of cycle paths (e.g. [['A', 'B', 'C', 'A']]).
        """
        active_nodes = set(subset) if subset is not None else set(self._nodes.keys())
        adj: dict[str, list[str]] = defaultdict(list)

        for edge in self._edges:
            if (
                edge.edge_type in (EdgeType.PREREQUISITE, EdgeType.ORDERING)
                and edge.source in active_nodes
                and edge.target in active_nodes
            ):
                adj[edge.source].append(edge.target)

        # Ensure deterministic iteration order
        for u in adj:
            adj[u].sort()

        cycles: list[list[str]] = []
        visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited
        parent_map: dict[str, str] = {}

        def dfs(node: str, path: list[str]) -> None:
            visited[node] = 1
            for neighbor in adj.get(node, []):
                if neighbor == node:
                    cycles.append([node, node])
                elif visited.get(neighbor, 0) == 1:
                    # Cycle found
                    try:
                        idx = path.index(neighbor)
                        cycle = path[idx:] + [neighbor]
                        cycles.append(cycle)
                    except ValueError:
                        cycles.append([node, neighbor, node])
                elif visited.get(neighbor, 0) == 0:
                    dfs(neighbor, path + [neighbor])
            visited[node] = 2

        for node in sorted(active_nodes):
            if visited.get(node, 0) == 0:
                dfs(node, [node])

        return cycles

    def find_conflicts(
        self,
        selected_node_ids: Iterable[str] | None = None,
        check_file_overlap: bool = False,
    ) -> list[ConflictReport]:
        """Detect explicit conflicts and optional un-ordered resource/file overlaps."""
        active = set(selected_node_ids) if selected_node_ids is not None else set(self._nodes.keys())
        conflicts: list[ConflictReport] = []
        reported_pairs: set[frozenset[str]] = set()

        # 1. Explicit CONFLICT edges
        for edge in self._edges:
            if edge.edge_type == EdgeType.CONFLICT:
                if edge.source in active and edge.target in active:
                    pair = frozenset([edge.source, edge.target])
                    if pair not in reported_pairs:
                        reported_pairs.add(pair)
                        conflicts.append(
                            ConflictReport(
                                source_id=edge.source,
                                target_id=edge.target,
                                reason=edge.reason or "Explicit conflict declared between transformations",
                                conflict_type="explicit",
                            )
                        )

        # 2. Resource/file overlaps between unordered nodes
        if check_file_overlap:
            active_list = sorted(list(active))
            # Determine ordering or dependency relationships between nodes
            ordered_pairs: set[tuple[str, str]] = set()
            for u in active_list:
                for v in self.get_dependents(u, transitive=True):
                    ordered_pairs.add((u, v))
                for edge in self._edges:
                    if edge.edge_type == EdgeType.ORDERING:
                        ordered_pairs.add((edge.source, edge.target))

            for i in range(len(active_list)):
                node_a = self._nodes.get(active_list[i])
                if not node_a:
                    continue
                for j in range(i + 1, len(active_list)):
                    node_b = self._nodes.get(active_list[j])
                    if not node_b:
                        continue
                    pair = frozenset([node_a.id, node_b.id])
                    if pair in reported_pairs:
                        continue
                    # If they are strictly ordered, file overlap is permitted because they execute sequentially
                    if (node_a.id, node_b.id) in ordered_pairs or (node_b.id, node_a.id) in ordered_pairs:
                        continue

                    overlaps = _file_patterns_conflict(
                        node_a.affected_file_patterns,
                        node_b.affected_file_patterns,
                    )
                    if overlaps:
                        reported_pairs.add(pair)
                        conflicts.append(
                            ConflictReport(
                                source_id=node_a.id,
                                target_id=node_b.id,
                                reason=f"Unordered transformations affect overlapping file patterns: {', '.join(overlaps)}",
                                conflict_type="resource_overlap",
                                conflicting_patterns=overlaps,
                            )
                        )

        return conflicts

    def find_invalidations(self, selected_node_ids: Iterable[str]) -> dict[str, list[str]]:
        """Return mapping of invalidated target node IDs to the list of source node IDs that invalidated them."""
        active = set(selected_node_ids)
        invalidated: dict[str, list[str]] = defaultdict(list)

        for edge in self._edges:
            if edge.edge_type == EdgeType.INVALIDATION:
                if edge.source in active and edge.target in active:
                    invalidated[edge.target].append(edge.source)

        return dict(invalidated)

    def validate(
        self,
        selected_node_ids: Iterable[str] | None = None,
        check_file_overlap: bool = False,
    ) -> None:
        """Validate the consistency, prerequisites, acyclicity, and conflict-freedom of the graph.

        Raises:
            MissingPrerequisiteError: If a prerequisite transformation is not selected.
            CycleDependencyError: If a directed dependency cycle exists.
            ConflictingTransformationError: If mutually exclusive transformations are selected.
            InvalidationError: If a transformation is invalidated by another selected transformation.
        """
        active = set(selected_node_ids) if selected_node_ids is not None else set(self._nodes.keys())

        # 1. Verify existence of nodes
        for node_id in active:
            if node_id not in self._nodes:
                raise TransformationGraphError(f"Selected transformation '{node_id}' does not exist in graph")

        # 2. Check prerequisite satisfaction
        if selected_node_ids is not None:
            for node_id in sorted(active):
                for edge in self._edges:
                    if edge.target == node_id and edge.edge_type == EdgeType.PREREQUISITE:
                        if edge.source not in active:
                            raise MissingPrerequisiteError(
                                node_id=node_id,
                                missing_prerequisite_id=edge.source,
                                reason=edge.reason,
                            )

        # 3. Check for cycles
        cycles = self.detect_cycles(subset=active)
        if cycles:
            raise CycleDependencyError(cycles[0])

        # 4. Check for conflicts
        conflicts = self.find_conflicts(selected_node_ids=active, check_file_overlap=check_file_overlap)
        if conflicts:
            c = conflicts[0]
            raise ConflictingTransformationError(c.source_id, c.target_id, c.reason)

        # 5. Check for invalidations
        invalidations = self.find_invalidations(selected_node_ids=active)
        if invalidations:
            target = sorted(invalidations.keys())[0]
            source = invalidations[target][0]
            raise InvalidationError(target, source)

    def topological_sort(self, selected_node_ids: Iterable[str] | None = None) -> list[str]:
        """Perform a deterministic topological sort of the graph.

        Returns an ordered list of node IDs satisfying all PREREQUISITE and ORDERING constraints.
        Deterministic tie-breaking is enforced by node ID.
        """
        active = set(selected_node_ids) if selected_node_ids is not None else set(self._nodes.keys())
        self.validate(selected_node_ids=active)

        in_degree: dict[str, int] = {node_id: 0 for node_id in active}
        adj: dict[str, list[str]] = defaultdict(list)

        for edge in self._edges:
            if (
                edge.edge_type in (EdgeType.PREREQUISITE, EdgeType.ORDERING)
                and edge.source in active
                and edge.target in active
            ):
                adj[edge.source].append(edge.target)
                in_degree[edge.target] += 1

        # Use deterministic sorted list
        ready: list[str] = sorted([node for node, deg in in_degree.items() if deg == 0])
        result: list[str] = []

        while ready:
            curr = ready.pop(0)
            result.append(curr)

            for neighbor in sorted(adj.get(curr, [])):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    ready.append(neighbor)
            ready.sort()

        if len(result) != len(active):
            # Graph has an undetected cycle
            remaining = [node for node, deg in in_degree.items() if deg > 0]
            raise CycleDependencyError(remaining)

        return result

    def get_execution_phases(self, selected_node_ids: Iterable[str] | None = None) -> list[list[str]]:
        """Partition nodes into sequential execution waves/phases.

        Phase 0 contains all root nodes with 0 incoming dependencies.
        Phase k contains all nodes whose prerequisites are fully satisfied in earlier phases (< k).
        """
        active = set(selected_node_ids) if selected_node_ids is not None else set(self._nodes.keys())
        self.validate(selected_node_ids=active)

        in_degree: dict[str, int] = {node_id: 0 for node_id in active}
        adj: dict[str, list[str]] = defaultdict(list)

        for edge in self._edges:
            if (
                edge.edge_type in (EdgeType.PREREQUISITE, EdgeType.ORDERING)
                and edge.source in active
                and edge.target in active
            ):
                adj[edge.source].append(edge.target)
                in_degree[edge.target] += 1

        phases: list[list[str]] = []
        current_wave = sorted([node for node, deg in in_degree.items() if deg == 0])

        while current_wave:
            phases.append(list(current_wave))
            next_wave: list[str] = []
            for node in current_wave:
                for neighbor in adj.get(node, []):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_wave.append(neighbor)
            current_wave = sorted(next_wave)

        return phases

    def get_parallel_batches(
        self, selected_node_ids: Iterable[str] | None = None
    ) -> list[ParallelBatch]:
        """Group transformations within each execution phase into parallel-safe batches.

        Transformations with completely disjoint affected file patterns are grouped
        into the same concurrent batch. Nodes that share or overlap file patterns are
        isolated into sequential batches to avoid workspace races and merge conflicts.
        """
        phases = self.get_execution_phases(selected_node_ids=selected_node_ids)
        batches: list[ParallelBatch] = []
        batch_counter = 0

        for phase in phases:
            # If phase has only 1 node, trivial single-node batch
            if len(phase) <= 1:
                batches.append(
                    ParallelBatch(
                        batch_index=batch_counter,
                        nodes=phase,
                        is_concurrent=True,
                    )
                )
                batch_counter += 1
                continue

            # Greedy partitioning into disjoint file pattern groups
            sub_batches: list[list[str]] = []
            sub_batch_patterns: list[list[str]] = []

            for node_id in phase:
                node = self._nodes.get(node_id)
                patterns = node.affected_file_patterns if node else []

                placed = False
                for i, existing_patterns in enumerate(sub_batch_patterns):
                    overlaps = _file_patterns_conflict(patterns, existing_patterns)
                    if not overlaps:
                        # Safe to join this concurrent sub-batch
                        sub_batches[i].append(node_id)
                        sub_batch_patterns[i].extend(patterns)
                        placed = True
                        break

                if not placed:
                    # Create new sub-batch
                    sub_batches.append([node_id])
                    sub_batch_patterns.append(list(patterns))

            for sb in sub_batches:
                batches.append(
                    ParallelBatch(
                        batch_index=batch_counter,
                        nodes=sb,
                        is_concurrent=len(sb) > 1,
                    )
                )
                batch_counter += 1

        return batches

    def get_rollback_boundaries(
        self, selected_node_ids: Iterable[str] | None = None
    ) -> dict[str, set[str]]:
        """Calculate the atomic rollback boundary / blast radius for every node.

        If a node fails, the rollback boundary includes:
        1. The failed node itself.
        2. All peer nodes sharing the same `atomic_boundary` (transaction boundary).
        3. All downstream transitive dependents of the failed node or its boundary peers.
        """
        active = set(selected_node_ids) if selected_node_ids is not None else set(self._nodes.keys())
        boundaries: dict[str, set[str]] = {}

        # Precompute atomic boundary groups
        boundary_groups: dict[str, set[str]] = defaultdict(set)
        for node_id in active:
            node = self._nodes.get(node_id)
            if node and node.atomic_boundary:
                boundary_groups[node.atomic_boundary].add(node_id)

        for node_id in active:
            node = self._nodes.get(node_id)
            rollback_set: set[str] = {node_id}

            # 1. Include atomic boundary peers
            if node and node.atomic_boundary:
                peers = boundary_groups[node.atomic_boundary]
                rollback_set.update(peers)

            # 2. Include all transitive dependents of any node in rollback_set
            to_expand = deque(list(rollback_set))
            visited = set(rollback_set)
            while to_expand:
                curr = to_expand.popleft()
                for dep in self.get_dependents(curr, transitive=True):
                    if dep in active and dep not in rollback_set:
                        rollback_set.add(dep)
                        if dep not in visited:
                            visited.add(dep)
                            to_expand.append(dep)

            boundaries[node_id] = rollback_set

        return boundaries

    def filter_subgraph(self, selected_node_ids: Iterable[str]) -> "TransformationGraph":
        """Return an isolated subgraph containing only the selected nodes and relevant edges."""
        selected = set(selected_node_ids)
        sub_nodes = [node for node_id, node in self._nodes.items() if node_id in selected]
        sub_edges = [
            edge
            for edge in self._edges
            if edge.source in selected and edge.target in selected
        ]
        return TransformationGraph(nodes=sub_nodes, edges=sub_edges)

    def to_dict(self) -> dict[str, Any]:
        """Serialize graph to dictionary structure."""
        return {
            "nodes": [node.model_dump() for node in self._nodes.values()],
            "edges": [edge.model_dump() for edge in self._edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransformationGraph":
        """Deserialize graph from dictionary structure."""
        nodes = [TransformationNode(**n) for n in data.get("nodes", [])]
        edges = [DependencyEdge(**e) for e in data.get("edges", [])]
        return cls(nodes=nodes, edges=edges)
