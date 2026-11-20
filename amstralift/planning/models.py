"""Data models and declarative contracts for the Modernization Planning Engine (P17).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from amstralift.transforms.models import (
    ParallelBatch,
    TransformationCategory,
    TransformationRisk,
)


class TransformationStatus(str, Enum):
    """Execution disposition of a transformation within a modernization plan."""

    SAFE = "safe"            # Low risk, verified idempotent, ready to apply
    DEPENDENT = "dependent"  # Requires upstream transformations in prior phases
    RISKY = "risky"          # Structural/architectural change requiring functional verification
    REFUSED = "refused"      # Conflicting, superseded, or incompatible with target
    SKIPPED = "skipped"      # Preconditions not met (e.g. no applicable files found)


class PhaseType(str, Enum):
    """Canonical lifecycle phases of an enterprise modernization plan."""

    DEPENDENCIES = "dependencies"
    FRAMEWORK_MIGRATION = "framework_migration"
    TEMPLATE_MIGRATION = "template_migration"
    CONFIGURATION = "configuration"
    CLEANUP = "cleanup"
    VERIFICATION = "verification"


class PlannedTransformation(BaseModel):
    """An individual transformation scheduled inside an execution phase."""

    id: str = Field(description="Unique transformation identifier (e.g. 'angular:control-flow')")
    name: str = Field(description="Human-readable title")
    category: TransformationCategory = Field(default=TransformationCategory.AST)
    risk: TransformationRisk = Field(default=TransformationRisk.LOW)
    status: TransformationStatus = Field(default=TransformationStatus.SAFE)
    reason: str = Field(default="", description="Justification for inclusion, risk rating, or refusal")
    prerequisites: list[str] = Field(default_factory=list, description="IDs of upstream prerequisite transformations")
    affected_file_patterns: list[str] = Field(default_factory=list, description="Glob patterns of files touched")
    atomic_boundary: str | None = Field(default=None, description="Rollback transaction group if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPhase(BaseModel):
    """A discrete execution wave containing ordered transformations and parallel batches."""

    phase_index: int = Field(description="0-based sequential phase index")
    name: str = Field(description="Descriptive phase name")
    phase_type: PhaseType = Field(description="Standardized phase type")
    description: str = Field(default="")
    transformations: list[PlannedTransformation] = Field(default_factory=list)
    parallel_batches: list[ParallelBatch] = Field(default_factory=list)


class ModernizationPlan(BaseModel):
    """The complete declarative modernization plan produced by the Planning Engine (P17)."""

    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex[:8]}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    repo_path: str = Field(description="Path to target repository")
    ecosystem: str = Field(description="Ecosystem (angular, dotnet, python, react)")
    current_version: str | None = Field(default=None, description="Current detected framework version")
    target_version: str | None = Field(default=None, description="Target framework version")
    target_major: int | None = Field(default=None, description="Target framework major version")

    total_transformations: int = Field(default=0)
    safe_count: int = Field(default=0)
    dependent_count: int = Field(default=0)
    risky_count: int = Field(default=0)
    refused_count: int = Field(default=0)

    phases: list[ExecutionPhase] = Field(default_factory=list)
    refused_transformations: list[PlannedTransformation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_yaml(self) -> str:
        """Serialize modernization plan to clean declarative YAML string."""
        data = self.model_dump(mode="json")
        return yaml.dump(data, sort_keys=False, default_flow_style=False)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "ModernizationPlan":
        """Deserialize modernization plan from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls(**data)

    def save(self, file_path: Path) -> None:
        """Write plan to file (.yaml, .yml, or .json)."""
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.suffix.lower() == ".json":
            file_path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        else:
            file_path.write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def load(cls, file_path: Path) -> "ModernizationPlan":
        """Load plan from file (.yaml, .yml, or .json)."""
        file_path = Path(file_path)
        content = file_path.read_text(encoding="utf-8")
        if file_path.suffix.lower() == ".json":
            return cls.model_validate_json(content)
        return cls.from_yaml(content)
