"""Data contracts and models for Resumable / Checkpointed Modernization (P19).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

from amstralift.planning.models import PhaseType


class CheckpointStatus(str, Enum):
    """Lifecycle status of a modernization execution checkpoint."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    FAILED = "failed"
    COMPLETED = "completed"


class PhaseCheckpoint(BaseModel):
    """State record for a single modernization phase execution."""

    phase_index: int = Field(description="0-indexed phase position")
    phase_name: str = Field(description="Canonical title of the phase")
    phase_type: PhaseType = Field(description="Classification of the phase")
    status: CheckpointStatus = Field(default=CheckpointStatus.PENDING)
    completed_transformations: list[str] = Field(default_factory=list)
    failed_transformation: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
    git_commit_sha: str | None = Field(default=None)
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = Field(default=None)
    duration_seconds: float = Field(default=0.0)


class ModernizationCheckpoint(BaseModel):
    """Complete checkpoint record for an interrupted or resumable modernization run."""

    execution_id: str = Field(description="Unique execution run identifier, e.g. 'exec_20261120_123456_a1b2'")
    plan_id: str = Field(description="ID of the ModernizationPlan being executed")
    repo_path: str = Field(description="Absolute path of the repository workspace")
    ecosystem: str = Field(description="Target ecosystem (angular, dotnet, python, react)")
    target_version: str = Field(description="Target framework/runtime version (e.g. 'v18')")
    base_commit_sha: str = Field(description="Git HEAD commit SHA before modernization commenced")
    current_commit_sha: str = Field(description="Git commit SHA at latest saved checkpoint")
    status: CheckpointStatus = Field(default=CheckpointStatus.IN_PROGRESS)
    resumable: bool = Field(default=True, description="False if workspace or state cannot be safely resumed")
    total_phases: int = Field(description="Total number of execution phases in the plan")
    completed_phases_count: int = Field(default=0)
    completed_transformation_ids: list[str] = Field(default_factory=list)
    phases: list[PhaseCheckpoint] = Field(default_factory=list)
    evidence_hash: str = Field(default="", description="Cryptographic integrity hash of completed steps")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)
