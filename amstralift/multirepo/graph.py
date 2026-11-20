"""Cross-repository dependency graph and topological wave engine (P21).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

from typing import Optional

from amstralift.multirepo.exceptions import InterRepoCycleError, RepositoryNotFoundError
from amstralift.multirepo.models import (
    FleetExecutionWave,
    FleetTopology,
    InterRepoDependency,
    RepositoryNode,
)


class FleetDependencyGraph:
    """Directed Acyclic Graph (DAG) modeling inter-repository dependencies.

    Directed edges: Provider -> Consumer.
    A Provider repository must be modernized, tested, and published before
    its downstream Consumer repositories can be upgraded.
    """

    def __init__(self, topology: FleetTopology) -> None:
        self.topology = topology
        self.nodes: dict[str, RepositoryNode] = dict(topology.repositories)
        # adj: provider -> set of consumers (downstream)
        self.adj: dict[str, set[str]] = {r: set() for r in self.nodes}
        # reverse_adj: consumer -> set of providers (upstream)
        self.reverse_adj: dict[str, set[str]] = {r: set() for r in self.nodes}

        self._build_graph(topology.dependencies)
        self.validate()

    def _build_graph(self, dependencies: list[InterRepoDependency]) -> None:
        """Populate adjacency and reverse adjacency maps from inter-repo dependencies."""
        for dep in dependencies:
            p = dep.provider_repo_id
            c = dep.consumer_repo_id
            if p in self.nodes and c in self.nodes:
                self.adj[p].add(c)
                self.reverse_adj[c].add(p)

    def validate(self) -> None:
        """Validate the graph and verify that no circular dependencies exist."""
        cycle = self.detect_cycle()
        if cycle:
            raise InterRepoCycleError(cycle)

    def detect_cycle(self) -> Optional[list[str]]:
        """Detect circular dependencies using DFS graph coloring.

        Returns the cycle path as a list of repo IDs if a cycle exists, or None.
        """
        color: dict[str, int] = {r: 0 for r in self.nodes}  # 0: unvisited, 1: visiting, 2: visited
        parent: dict[str, Optional[str]] = {r: None for r in self.nodes}
        cycle_nodes: list[str] = []

        def dfs(u: str) -> bool:
            color[u] = 1
            for v in sorted(list(self.adj[u])):
                if color[v] == 1:
                    # Found back-edge u -> v
                    cycle_nodes.append(v)
                    curr = u
                    while curr and curr != v:
                        cycle_nodes.append(curr)
                        curr = parent.get(curr)
                    cycle_nodes.append(v)
                    cycle_nodes.reverse()
                    return True
                if color[v] == 0:
                    parent[v] = u
                    if dfs(v):
                        return True
            color[u] = 2
            return False

        for node in sorted(list(self.nodes.keys())):
            if color[node] == 0:
                if dfs(node):
                    return cycle_nodes

        return None

    def get_upstream(self, repo_id: str) -> list[str]:
        """Return immediate provider repositories required by this repository."""
        if repo_id not in self.nodes:
            raise RepositoryNotFoundError(repo_id)
        return sorted(list(self.reverse_adj[repo_id]))

    def get_downstream(self, repo_id: str) -> list[str]:
        """Return immediate consumer repositories dependent on this repository."""
        if repo_id not in self.nodes:
            raise RepositoryNotFoundError(repo_id)
        return sorted(list(self.adj[repo_id]))

    def get_all_upstream(self, repo_id: str) -> set[str]:
        """Return transitive closure of all upstream provider repositories."""
        if repo_id not in self.nodes:
            raise RepositoryNotFoundError(repo_id)

        visited: set[str] = set()
        queue = list(self.reverse_adj[repo_id])
        while queue:
            curr = queue.pop(0)
            if curr not in visited:
                visited.add(curr)
                queue.extend(self.reverse_adj.get(curr, set()))
        return visited

    def get_all_downstream(self, repo_id: str) -> set[str]:
        """Return transitive closure of all downstream consumer repositories."""
        if repo_id not in self.nodes:
            raise RepositoryNotFoundError(repo_id)

        visited: set[str] = set()
        queue = list(self.adj[repo_id])
        while queue:
            curr = queue.pop(0)
            if curr not in visited:
                visited.add(curr)
                queue.extend(self.adj.get(curr, set()))
        return visited

    def compute_execution_waves(self, filter_repos: Optional[set[str]] = None) -> list[FleetExecutionWave]:
        """Compute staged, topologically ordered execution waves using Kahn's algorithm.

        Within each wave, all repositories are mutually independent and can be modernized
        concurrently.
        """
        active_nodes = set(self.nodes.keys()) if filter_repos is None else (set(self.nodes.keys()) & filter_repos)
        if not active_nodes:
            return []

        # Calculate initial in-degrees considering only active_nodes
        in_degrees: dict[str, int] = {
            r: len(self.reverse_adj[r] & active_nodes) for r in active_nodes
        }

        waves: list[FleetExecutionWave] = []
        wave_index = 1
        processed: set[str] = set()

        while len(processed) < len(active_nodes):
            current_wave_repos = [
                r for r in sorted(list(active_nodes))
                if r not in processed and in_degrees[r] == 0
            ]

            if not current_wave_repos:
                # Cycle encountered in active subset
                remaining = active_nodes - processed
                raise InterRepoCycleError(sorted(list(remaining)))

            # Sort current wave repos: providers first, then alphabetical
            def sort_key(r: str) -> tuple[int, str]:
                is_provider = 0 if len(self.adj.get(r, set())) > 0 else 1
                return (is_provider, r)

            current_wave_repos.sort(key=sort_key)

            # Generate wave description
            provider_count = sum(1 for r in current_wave_repos if len(self.adj.get(r, set())) > 0)
            consumer_count = len(current_wave_repos) - provider_count
            desc_parts = []
            if provider_count > 0:
                desc_parts.append(f"{provider_count} shared provider/library repos")
            if consumer_count > 0:
                desc_parts.append(f"{consumer_count} downstream/isolated consumer repos")
            desc = f"Wave {wave_index}: " + (", ".join(desc_parts) if desc_parts else f"{len(current_wave_repos)} repos")

            wave = FleetExecutionWave(
                wave_number=wave_index,
                name=f"Wave {wave_index}",
                repo_ids=current_wave_repos,
                description=desc,
            )
            waves.append(wave)

            # Mark processed and decrement in-degrees for downstream neighbors
            for r in current_wave_repos:
                processed.add(r)
                for consumer in self.adj.get(r, set()):
                    if consumer in in_degrees:
                        in_degrees[consumer] -= 1

            wave_index += 1

        return waves

    def dependency_matrix(self) -> dict[str, list[str]]:
        """Return a dictionary mapping each repo_id to its sorted list of upstream prerequisite repo_ids."""
        return {r: sorted(list(self.reverse_adj[r])) for r in sorted(list(self.nodes.keys()))}
