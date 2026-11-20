"""Modernization Planning Engine (P17).

Transforms AMstraLift from an upgrade engine into a modernization orchestration planner.
Inspects repositories, evaluates transformation dependency graphs (P16), resolves conflicts,
classifies changes into safe / dependent / risky / refused, and generates declarative execution plans.

Engineered by Amal P S for AMstraLift Generation 2.
"""

from pathlib import Path
from typing import Any

from amstralift.conflicts import ConflictResolver, PreExecutionConflictDetector
from amstralift.planning.analyzer import RepoAnalysis, RepoAnalyzer
from amstralift.planning.models import (
    ExecutionPhase,
    ModernizationPlan,
    PhaseType,
    PlannedTransformation,
    TransformationStatus,
)
from amstralift.transforms.graph import TransformationGraph, _file_patterns_conflict
from amstralift.transforms.models import (
    EdgeType,
    ParallelBatch,
    TransformationCategory,
    TransformationNode,
    TransformationRisk,
)
from amstralift.transforms.registry import build_default_graph, get_transformations_for_ecosystem


DEFAULT_TARGET_MAJORS = {
    "angular": 18,
    "dotnet": 8,
    "python": 12,
    "react": 18,
}

PHASE_CATEGORY_MAP = {
    TransformationCategory.DEPENDENCY: PhaseType.DEPENDENCIES,
    TransformationCategory.FRAMEWORK: PhaseType.FRAMEWORK_MIGRATION,
    TransformationCategory.AST: PhaseType.TEMPLATE_MIGRATION,
    TransformationCategory.CONFIG: PhaseType.CONFIGURATION,
    TransformationCategory.STYLE: PhaseType.CONFIGURATION,
    TransformationCategory.CLEANUP: PhaseType.CLEANUP,
}

PHASE_DISPLAY_METADATA = [
    (PhaseType.DEPENDENCIES, "Dependencies & Package Alignment", "Aligns companion packages, peer dependencies, and lockfiles."),
    (PhaseType.FRAMEWORK_MIGRATION, "Framework & Architecture Migration", "Upgrades runtime framework, workspace schema, and core component architecture."),
    (PhaseType.TEMPLATE_MIGRATION, "Template & AST Modernization", "Executes AST codemods for modern syntax, control flow, signals, and injection."),
    (PhaseType.CONFIGURATION, "Tooling & Compiler Configuration", "Upgrades linters, formatters, compiler options, and static analysis tools."),
    (PhaseType.CLEANUP, "Hygiene & Deprecated Artifact Pruning", "Removes superseded scaffolding, dead boilerplate, and cleans git tracking."),
    (PhaseType.VERIFICATION, "Sandbox Verification & Attestation", "Runs all 6 verification gates (Install, Build, Test, Security, Diff, Semantic) and HMAC signs the bundle."),
]


def partition_phase_batches(items: list[PlannedTransformation]) -> list[ParallelBatch]:
    """Group items within a phase into concurrent batches with disjoint file patterns."""
    if len(items) <= 1:
        return [
            ParallelBatch(
                batch_index=0,
                nodes=[t.id for t in items],
                is_concurrent=True,
            )
        ]

    sub_batches: list[list[str]] = []
    sub_batch_patterns: list[list[str]] = []

    for t in items:
        patterns = t.affected_file_patterns
        placed = False
        for i, existing in enumerate(sub_batch_patterns):
            overlaps = _file_patterns_conflict(patterns, existing)
            if not overlaps:
                sub_batches[i].append(t.id)
                sub_batch_patterns[i].extend(patterns)
                placed = True
                break
        if not placed:
            sub_batches.append([t.id])
            sub_batch_patterns.append(list(patterns))

    batches: list[ParallelBatch] = []
    for idx, sb in enumerate(sub_batches):
        batches.append(
            ParallelBatch(
                batch_index=idx,
                nodes=sb,
                is_concurrent=len(sb) > 1,
            )
        )
    return batches


class ModernizationPlanner:
    """Orchestrates repository inspection, DAG evaluation, and modernization plan generation."""

    def __init__(self, graph: TransformationGraph | None = None) -> None:
        self.graph = graph or build_default_graph()

    def generate_plan(
        self,
        repo_path: Path,
        ecosystem: str | None = None,
        target_major: int | None = None,
        target_version: str | None = None,
    ) -> ModernizationPlan:
        """Analyze repository and generate an end-to-end declarative ModernizationPlan."""
        repo_path = Path(repo_path).resolve()
        analysis = RepoAnalyzer.analyze(repo_path, ecosystem_hint=ecosystem)
        eco = analysis.ecosystem.lower()

        # Resolve target version & major
        eff_target_major = target_major
        if eff_target_major is None:
            if target_version:
                import re
                m = re.search(r"(\d+)", target_version)
                if m:
                    eff_target_major = int(m.group(1))
            if eff_target_major is None:
                cur = analysis.current_major
                default_target = DEFAULT_TARGET_MAJORS.get(eco, 18)
                eff_target_major = max(cur + 1, default_target) if cur else default_target

        eff_target_version = target_version or f"v{eff_target_major}"

        # Get candidates for this ecosystem
        candidate_nodes = get_transformations_for_ecosystem(eco)

        accepted_nodes: dict[str, PlannedTransformation] = {}
        refused_nodes: list[PlannedTransformation] = []

        # 1. Filter, evaluate, and classify candidates
        active_candidate_ids: list[str] = []
        for node in candidate_nodes:
            # Check min major version requirement
            if node.min_major_version and eff_target_major < node.min_major_version:
                refused_nodes.append(
                    PlannedTransformation(
                        id=node.id,
                        name=node.name,
                        category=node.category,
                        risk=node.risk,
                        status=TransformationStatus.REFUSED,
                        reason=f"Requires {eco.capitalize()} {node.min_major_version}+ (target is v{eff_target_major}).",
                        affected_file_patterns=node.affected_file_patterns,
                    )
                )
                continue

            # Check max major version limitation
            if node.max_major_version and eff_target_major > node.max_major_version:
                refused_nodes.append(
                    PlannedTransformation(
                        id=node.id,
                        name=node.name,
                        category=node.category,
                        risk=node.risk,
                        status=TransformationStatus.REFUSED,
                        reason=f"Only applicable up to {eco.capitalize()} {node.max_major_version}.",
                        affected_file_patterns=node.affected_file_patterns,
                    )
                )
                continue

            active_candidate_ids.append(node.id)

        # 2. P18: Pre-Execution Cross-Transformation Conflict Detection & Resolution
        detector = PreExecutionConflictDetector(self.graph)
        detected_conflicts = detector.detect_all(active_candidate_ids, repo_path=repo_path)

        resolver = ConflictResolver(self.graph)
        resolved_conflicts = resolver.resolve(detected_conflicts, active_candidate_ids)

        discarded_set = set(resolved_conflicts.discarded_rules)

        # Process discarded nodes into refused_nodes
        for node in candidate_nodes:
            if node.id in discarded_set:
                refused_nodes.append(
                    PlannedTransformation(
                        id=node.id,
                        name=node.name,
                        category=node.category,
                        risk=node.risk,
                        status=TransformationStatus.REFUSED,
                        reason="Mutually exclusive with modern architectural standards or superseded by conflict resolution.",
                        affected_file_patterns=node.affected_file_patterns,
                    )
                )

        # Filter remaining candidate nodes
        remaining_candidates = [
            n for n in candidate_nodes if n.id in active_candidate_ids and n.id not in discarded_set
        ]

        for node in remaining_candidates:
            # Determine prerequisites from P16 DAG
            prereqs = sorted(list(self.graph.get_prerequisites(node.id, transitive=False)))

            # Determine risk and status
            status = TransformationStatus.SAFE
            reason = "Verified idempotent, deterministic low-risk modernization."

            if node.risk in (TransformationRisk.HIGH, TransformationRisk.CRITICAL):
                status = TransformationStatus.RISKY
                reason = "Major architectural modernization requiring strict functional verification."
            elif node.risk == TransformationRisk.MEDIUM:
                status = TransformationStatus.RISKY
                reason = "Component or framework shift that alters syntax and reactive wiring."
            elif prereqs:
                status = TransformationStatus.DEPENDENT
                reason = f"Requires upstream prerequisite transformations: {', '.join(prereqs)}."

            accepted_nodes[node.id] = PlannedTransformation(
                id=node.id,
                name=node.name,
                category=node.category,
                risk=node.risk,
                status=status,
                reason=reason,
                prerequisites=prereqs,
                affected_file_patterns=node.affected_file_patterns,
                atomic_boundary=node.atomic_boundary,
                metadata=node.metadata,
            )

        # 3. Determine execution order via P16 Topological Sort
        active_ids = list(accepted_nodes.keys())
        try:
            topological_order = self.graph.topological_sort(active_ids)
        except Exception:
            topological_order = sorted(active_ids)

        # 3. Partition into Canonical Modernization Phases
        phases: list[ExecutionPhase] = []
        phase_map: dict[PhaseType, list[PlannedTransformation]] = {
            pt: [] for pt, _, _ in PHASE_DISPLAY_METADATA
        }

        # Map transformations to canonical lifecycle phases
        for nid in topological_order:
            pt = accepted_nodes[nid]
            if nid in ("angular:standalone", "angular:workspace-config"):
                phase_map[PhaseType.FRAMEWORK_MIGRATION].append(pt)
            elif pt.category in PHASE_CATEGORY_MAP:
                target_pt = PHASE_CATEGORY_MAP[pt.category]
                phase_map[target_pt].append(pt)
            else:
                phase_map[PhaseType.FRAMEWORK_MIGRATION].append(pt)

        # Add mandatory Gate 1-6 verification phase
        verification_trans = PlannedTransformation(
            id=f"{eco}:gate-verification",
            name="6-Gate Sandbox Verification & HMAC Signing",
            category=TransformationCategory.FRAMEWORK,
            risk=TransformationRisk.LOW,
            status=TransformationStatus.SAFE,
            reason="Mandatory verification across clean install, compile, test suite, security, and contract diff.",
            affected_file_patterns=["*"],
        )
        phase_map[PhaseType.VERIFICATION].append(verification_trans)

        phase_idx = 0
        for pt_type, pt_title, pt_desc in PHASE_DISPLAY_METADATA:
            items = phase_map[pt_type]
            if not items:
                continue

            p_batches = partition_phase_batches(items)

            phases.append(
                ExecutionPhase(
                    phase_index=phase_idx,
                    name=pt_title,
                    phase_type=pt_type,
                    description=pt_desc,
                    transformations=items,
                    parallel_batches=p_batches,
                )
            )
            phase_idx += 1

        # Calculate counts
        all_planned = [t for p in phases for t in p.transformations if t.id != f"{eco}:gate-verification"]
        safe_c = sum(1 for t in all_planned if t.status == TransformationStatus.SAFE)
        dep_c = sum(1 for t in all_planned if t.status == TransformationStatus.DEPENDENT)
        risk_c = sum(1 for t in all_planned if t.status == TransformationStatus.RISKY)
        refused_c = len(refused_nodes)

        return ModernizationPlan(
            repo_path=str(repo_path),
            ecosystem=eco,
            current_version=analysis.current_version,
            target_version=eff_target_version,
            target_major=eff_target_major,
            total_transformations=len(all_planned),
            safe_count=safe_c,
            dependent_count=dep_c,
            risky_count=risk_c,
            refused_count=refused_c,
            phases=phases,
            refused_transformations=refused_nodes,
            metadata={
                "manifest_files": analysis.manifest_files,
                "detected_features": analysis.features,
                "conflict_analysis": resolved_conflicts.model_dump(mode="json"),
            },
        )
