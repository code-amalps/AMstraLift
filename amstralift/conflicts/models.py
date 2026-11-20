"""Data contracts and models for Pre-execution Cross-Transformation Conflict Detection (P18).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class ConflictType(str, Enum):
    """Categorization of detected cross-transformation conflicts."""

    EXPLICIT_CONFLICT = "explicit_conflict"                  # Direct CONFLICT edge in DAG
    FILE_COLLISION = "file_collision"                        # Unordered rules touching same configuration/file
    SYMBOL_COLLISION = "symbol_collision"                    # Rule A alters symbol required by Rule B
    CONFIG_KEY_COLLISION = "config_key_collision"            # Rules setting opposing configuration values
    PRECONDITION_INVALIDATION = "precondition_invalidation"  # Rule A invalidates preconditions of Rule B


# Backward compatibility alias
ConflictCategory = ConflictType


class ConflictSeverity(str, Enum):
    """Urgency and impact of a detected conflict."""

    BLOCKING = "blocking"  # Workspace execution must NOT proceed until resolved
    WARNING = "warning"    # Potential hazard; can proceed with caution or sequential isolation


class ConflictResolutionStrategy(str, Enum):
    """Automatic or manual resolution action."""

    ABORT = "abort"                    # Halt execution before touching workspace
    DISCARD_RULE = "discard_rule"      # Discard conflicting legacy/superseded transformation
    SEQUENCE_ORDER = "sequence_order"  # Inject deterministic ORDERING edge
    ISOLATE_PHASE = "isolate_phase"    # Isolate conflicting rules into distinct sequential phases


class DetectedConflict(BaseModel):
    """Details of a detected conflict between two modernization rules."""

    id: str = Field(description="Unique conflict identifier (e.g. 'conf_001')")
    conflict_type: ConflictType = Field(description="Classification of conflict")
    severity: ConflictSeverity = Field(default=ConflictSeverity.BLOCKING)
    rule_a: str = Field(description="ID of first conflicting transformation")
    rule_b: str = Field(description="ID of second conflicting transformation")
    description: str = Field(description="Human-readable explanation of why the conflict occurs")
    conflicting_resource: str = Field(default="", description="Specific file, symbol, or configuration key in dispute")
    resolution_strategy: ConflictResolutionStrategy = Field(default=ConflictResolutionStrategy.ABORT)
    resolution_summary: str = Field(default="", description="Recommended action to resolve the conflict")
    resolved: bool = Field(default=False, description="True if conflict was successfully resolved prior to execution")


class ConflictAnalysisReport(BaseModel):
    """Complete pre-execution conflict analysis report."""

    total_conflicts: int = Field(default=0)
    blocking_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    conflicts: list[DetectedConflict] = Field(default_factory=list)
    safe_to_execute: bool = Field(default=True, description="False if unresolved blocking conflicts remain")
    resolved_sequence: list[str] = Field(default_factory=list, description="Ordered transformation IDs after resolution")
    discarded_rules: list[str] = Field(default_factory=list, description="Rules excluded to prevent conflict")
    injected_orderings: list[tuple[str, str]] = Field(default_factory=list, description="Orderings added to resolve collisions")
    metadata: dict[str, Any] = Field(default_factory=dict)
