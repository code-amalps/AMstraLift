"""Exceptions for the Transformation Dependency Graph (P16).

Engineered by Amal P S for AMstraLift Generation 2.
"""


class TransformationGraphError(Exception):
    """Base exception for all transformation graph anomalies."""
    pass


class CycleDependencyError(TransformationGraphError):
    """Raised when a circular prerequisite or ordering cycle is detected in the graph."""

    def __init__(self, cycle_path: list[str]):
        self.cycle_path = cycle_path
        chain_repr = " -> ".join(cycle_path)
        super().__init__(f"Circular dependency cycle detected: {chain_repr}")


class ConflictingTransformationError(TransformationGraphError):
    """Raised when conflicting transformations are scheduled for execution together."""

    def __init__(self, source_id: str, target_id: str, reason: str = ""):
        self.source_id = source_id
        self.target_id = target_id
        self.reason = reason
        msg = f"Conflicting transformations cannot be executed together: '{source_id}' <-> '{target_id}'"
        if reason:
            msg += f" (Reason: {reason})"
        super().__init__(msg)


class MissingPrerequisiteError(TransformationGraphError):
    """Raised when a required prerequisite transformation is not satisfied in the graph or selection."""

    def __init__(self, node_id: str, missing_prerequisite_id: str, reason: str = ""):
        self.node_id = node_id
        self.missing_prerequisite_id = missing_prerequisite_id
        self.reason = reason
        msg = f"Transformation '{node_id}' requires prerequisite '{missing_prerequisite_id}' which is not scheduled"
        if reason:
            msg += f" (Reason: {reason})"
        super().__init__(msg)


class InvalidationError(TransformationGraphError):
    """Raised when an invalidated transformation is scheduled alongside its invalidating predecessor."""

    def __init__(self, invalidated_id: str, invalidator_id: str, reason: str = ""):
        self.invalidated_id = invalidated_id
        self.invalidator_id = invalidator_id
        self.reason = reason
        msg = f"Transformation '{invalidated_id}' was invalidated by '{invalidator_id}'"
        if reason:
            msg += f" (Reason: {reason})"
        super().__init__(msg)
