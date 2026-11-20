"""Pre-execution Cross-Transformation Conflict Detection (P18) for AMstraLift Generation 2.

Engineered by Amal P S.
"""

from amstralift.conflicts.detector import PreExecutionConflictDetector
from amstralift.conflicts.models import (
    ConflictAnalysisReport,
    ConflictCategory,
    ConflictResolutionStrategy,
    ConflictSeverity,
    ConflictType,
    DetectedConflict,
)
from amstralift.conflicts.resolver import ConflictResolver

__all__ = [
    "PreExecutionConflictDetector",
    "ConflictResolver",
    "DetectedConflict",
    "ConflictAnalysisReport",
    "ConflictType",
    "ConflictSeverity",
    "ConflictResolutionStrategy",
    "ConflictCategory",
]
