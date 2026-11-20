"""Custom exceptions for Multi-Repository Modernization (P21).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations


class MultiRepoError(Exception):
    """Base exception for all multi-repo modernization errors."""


class InterRepoCycleError(MultiRepoError):
    """Raised when a circular dependency is detected between repositories in the fleet."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        cycle_str = " -> ".join(cycle)
        super().__init__(
            f"Circular inter-repository dependency cycle detected: {cycle_str}. "
            "Circular dependencies between repositories must be decoupled before coordinated modernization can proceed."
        )


class RepositoryNotFoundError(MultiRepoError):
    """Raised when a referenced repository cannot be located in the fleet."""

    def __init__(self, repo_id: str) -> None:
        self.repo_id = repo_id
        super().__init__(f"Repository '{repo_id}' not found in fleet topology.")
