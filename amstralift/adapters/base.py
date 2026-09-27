"""Abstract base class for all ecosystem adapters."""

from abc import ABC, abstractmethod
from pathlib import Path

from amstralift.core.models import DependencyChange, GateSummary


class BaseAdapter(ABC):
    """Abstract interface each ecosystem adapter must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the ecosystem (e.g. 'angular', 'dotnet', 'python', 'react')."""
        pass

    @abstractmethod
    def detect(self, repo_path: Path) -> bool:
        """Return True if this repository belongs to this ecosystem."""
        pass

    @abstractmethod
    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Scan repository manifests and return candidate package upgrades."""
        pass

    @abstractmethod
    def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
        """Apply the chosen upgrades to the working directory manifests/lockfiles."""
        pass

    @abstractmethod
    def run_build_and_tests(self, repo_path: Path) -> GateSummary:
        """Execute build and test gates declared in the repository."""
        pass
