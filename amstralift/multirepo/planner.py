"""Multi-Repository Modernization Planning Engine (P21).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from amstralift.multirepo.graph import FleetDependencyGraph
from amstralift.multirepo.models import (
    FleetTopology,
    MultiRepoModernizationPlan,
)


class FleetPlanner:
    """Planning engine that synthesizes multi-repo topology into staged modernization campaigns."""

    @classmethod
    def generate_plan(
        cls,
        topology: FleetTopology,
        target_major: Optional[int] = None,
        ecosystem: Optional[str] = None,
    ) -> MultiRepoModernizationPlan:
        """Generate a deterministic, staged multi-repository modernization plan."""
        graph = FleetDependencyGraph(topology)

        # Determine target repositories
        target_repo_ids = set(topology.repositories.keys())

        if ecosystem:
            eco_clean = ecosystem.lower().strip()
            # Include repositories matching the ecosystem, plus any upstream providers
            matching_repos = {
                r_id for r_id, r in topology.repositories.items()
                if eco_clean in [e.lower() for e in r.ecosystems] or r.primary_ecosystem.lower() == eco_clean
            }
            # Include upstream providers so they get modernized first
            needed_upstreams: set[str] = set()
            for r_id in matching_repos:
                needed_upstreams.update(graph.get_all_upstream(r_id))

            target_repo_ids = matching_repos | needed_upstreams

        # Compute topological waves for participating repositories
        waves = graph.compute_execution_waves(filter_repos=target_repo_ids)

        # Count cross-repo edges among participating repos
        active_edges = [
            d for d in topology.dependencies
            if d.consumer_repo_id in target_repo_ids and d.provider_repo_id in target_repo_ids
        ]

        # Build dependency matrix
        dep_matrix: dict[str, list[str]] = {}
        for r_id in sorted(list(target_repo_ids)):
            upstreams = [u for u in graph.get_upstream(r_id) if u in target_repo_ids]
            dep_matrix[r_id] = sorted(upstreams)

        # Formulate human-readable summary
        provider_count = len({d.provider_repo_id for d in active_edges})
        consumer_count = len({d.consumer_repo_id for d in active_edges})
        wave_count = len(waves)
        total_repos = len(target_repo_ids)

        summary_parts = [
            f"Fleet Modernization Plan coordinating {total_repos} repositories across {wave_count} staged execution wave(s)."
        ]
        if active_edges:
            summary_parts.append(
                f"{len(active_edges)} cross-repo dependency link(s) detected between {provider_count} provider(s) and {consumer_count} consumer(s)."
            )
            if wave_count > 1:
                wave1_repos = waves[0].repo_ids
                summary_parts.append(
                    f"Wave 1 modernizes foundational shared libraries ({', '.join(wave1_repos)}) before downstream consumer waves proceed."
                )
        else:
            summary_parts.append("All participating repositories are independent and can be modernized in parallel.")

        return MultiRepoModernizationPlan(
            target_version=target_major,
            ecosystem_filter=ecosystem,
            waves=waves,
            total_repositories=total_repos,
            cross_repo_edges=len(active_edges),
            dependency_matrix=dep_matrix,
            summary=" ".join(summary_parts),
            author="Amal P S",
        )

    @classmethod
    def save_plan(cls, plan: MultiRepoModernizationPlan, file_path: Path) -> None:
        """Persist a modernization plan to disk as formatted JSON."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load_plan(cls, file_path: Path) -> MultiRepoModernizationPlan:
        """Load a modernization plan from disk."""
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return MultiRepoModernizationPlan(**data)
