"""Checkpoint lifecycle and resumption manager for AMstraLift Modernization Plans (P19).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from datetime import datetime, timezone
from pathlib import Path
import uuid

from amstralift.checkpoints.models import CheckpointStatus, ModernizationCheckpoint, PhaseCheckpoint
from amstralift.checkpoints.storage import CheckpointStore
from amstralift.core.workspace import get_head_commit
from amstralift.planning.models import ExecutionPhase, ModernizationPlan, PhaseType


class CheckpointManager:
    """Coordinates lifecycle tracking, state persistence, and resumption for execution plans."""

    def __init__(self, workspace_path: Path | str) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.store = CheckpointStore(self.workspace_path)

    def _get_git_sha(self) -> str:
        try:
            return get_head_commit(self.workspace_path)
        except Exception:
            return "0000000000000000000000000000000000000000"

    def start_execution(
        self,
        plan: ModernizationPlan,
        dry_run: bool = False,
    ) -> ModernizationCheckpoint:
        """Initialize a new execution run checkpoint and persist it to disk."""
        now_str = datetime.now(timezone.utc).isoformat()
        current_sha = self._get_git_sha()
        exec_id = f"exec_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        phases = [
            PhaseCheckpoint(
                phase_index=p.phase_index,
                phase_name=p.name,
                phase_type=p.phase_type,
                status=CheckpointStatus.PENDING,
            )
            for p in plan.phases
        ]

        checkpoint = ModernizationCheckpoint(
            execution_id=exec_id,
            plan_id=plan.plan_id,
            repo_path=str(self.workspace_path),
            ecosystem=plan.ecosystem,
            target_version=plan.target_version,
            base_commit_sha=current_sha,
            current_commit_sha=current_sha,
            status=CheckpointStatus.IN_PROGRESS,
            resumable=True,
            total_phases=len(plan.phases),
            completed_phases_count=0,
            completed_transformation_ids=[],
            phases=phases,
            created_at=now_str,
            updated_at=now_str,
            metadata={
                "dry_run": dry_run,
                "plan_data": plan.model_dump(mode="json"),
            },
        )
        self.store.save(checkpoint)
        return checkpoint

    def record_phase_start(
        self,
        checkpoint: ModernizationCheckpoint,
        phase_index: int,
    ) -> None:
        """Mark a phase as in-progress."""
        if 0 <= phase_index < len(checkpoint.phases):
            checkpoint.phases[phase_index].status = CheckpointStatus.IN_PROGRESS
            checkpoint.phases[phase_index].started_at = datetime.now(timezone.utc).isoformat()
            checkpoint.updated_at = datetime.now(timezone.utc).isoformat()
            self.store.save(checkpoint)

    def record_phase_success(
        self,
        checkpoint: ModernizationCheckpoint,
        phase_index: int,
        applied_transformations: list[str],
        duration_seconds: float = 0.0,
    ) -> None:
        """Record successful completion of an execution phase."""
        current_sha = self._get_git_sha()
        now_str = datetime.now(timezone.utc).isoformat()

        if 0 <= phase_index < len(checkpoint.phases):
            pc = checkpoint.phases[phase_index]
            pc.status = CheckpointStatus.COMPLETED
            pc.completed_transformations = list(applied_transformations)
            pc.git_commit_sha = current_sha
            pc.completed_at = now_str
            pc.duration_seconds = duration_seconds

        for tid in applied_transformations:
            if tid not in checkpoint.completed_transformation_ids:
                checkpoint.completed_transformation_ids.append(tid)

        checkpoint.completed_phases_count += 1
        checkpoint.current_commit_sha = current_sha
        checkpoint.updated_at = now_str

        # If all phases finished, mark checkpoint COMPLETED
        if checkpoint.completed_phases_count >= checkpoint.total_phases:
            checkpoint.status = CheckpointStatus.COMPLETED

        self.store.save(checkpoint)

    def record_phase_failure(
        self,
        checkpoint: ModernizationCheckpoint,
        phase_index: int,
        failed_transformation: str,
        error_message: str,
        applied_so_far: list[str] | None = None,
        duration_seconds: float = 0.0,
    ) -> None:
        """Record a phase failure with blast radius and pause status."""
        current_sha = self._get_git_sha()
        now_str = datetime.now(timezone.utc).isoformat()

        if 0 <= phase_index < len(checkpoint.phases):
            pc = checkpoint.phases[phase_index]
            pc.status = CheckpointStatus.FAILED
            pc.failed_transformation = failed_transformation
            pc.error_message = error_message
            pc.git_commit_sha = current_sha
            pc.completed_at = now_str
            pc.duration_seconds = duration_seconds
            if applied_so_far:
                pc.completed_transformations = list(applied_so_far)
                for tid in applied_so_far:
                    if tid not in checkpoint.completed_transformation_ids:
                        checkpoint.completed_transformation_ids.append(tid)

        checkpoint.status = CheckpointStatus.FAILED
        checkpoint.current_commit_sha = current_sha
        checkpoint.updated_at = now_str
        self.store.save(checkpoint)

    def prepare_resumption(
        self,
        execution_id: str | None = None,
    ) -> tuple[ModernizationCheckpoint, ModernizationPlan, int]:
        """Verify repository readiness and generate trimmed resumption plan."""
        if execution_id:
            checkpoint = self.store.load(execution_id)
        else:
            checkpoint = self.store.get_latest_resumable()
            if not checkpoint:
                raise ValueError("No resumable modernization checkpoint found in workspace.")

        if not checkpoint.resumable:
            raise ValueError(f"Checkpoint '{checkpoint.execution_id}' is marked non-resumable: {checkpoint.metadata.get('integrity_error', 'Invalid state')}")

        if checkpoint.status == CheckpointStatus.COMPLETED:
            raise ValueError(f"Modernization execution '{checkpoint.execution_id}' has already completed.")

        # Reconstruct full plan from checkpoint metadata
        plan_data = checkpoint.metadata.get("plan_data")
        if not plan_data:
            raise ValueError(f"Checkpoint '{checkpoint.execution_id}' does not contain serialized plan data.")

        full_plan = ModernizationPlan.model_validate(plan_data)

        # Identify starting phase
        starting_phase_idx = checkpoint.completed_phases_count
        completed_set = set(checkpoint.completed_transformation_ids)

        # Build resumed phases
        resumed_phases: list[ExecutionPhase] = []
        for phase in full_plan.phases:
            if phase.phase_index < starting_phase_idx:
                continue

            # Filter out transformations already completed
            pending_transformations = [
                t for t in phase.transformations if t.id not in completed_set
            ]
            if not pending_transformations:
                continue

            # Partition pending into batches
            from amstralift.planning.planner import partition_phase_batches

            resumed_phase = ExecutionPhase(
                phase_index=phase.phase_index,
                name=phase.name,
                phase_type=phase.phase_type,
                description=phase.description,
                transformations=pending_transformations,
                parallel_batches=partition_phase_batches(pending_transformations),
            )
            resumed_phases.append(resumed_phase)

        resumed_plan = full_plan.model_copy(
            update={
                "phases": resumed_phases,
                "metadata": {
                    **full_plan.metadata,
                    "resumed_from_execution_id": checkpoint.execution_id,
                    "original_phase_count": len(full_plan.phases),
                },
            }
        )

        # Set checkpoint status back to IN_PROGRESS upon resumption
        checkpoint.status = CheckpointStatus.IN_PROGRESS
        checkpoint.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.save(checkpoint)

        return checkpoint, resumed_plan, starting_phase_idx
