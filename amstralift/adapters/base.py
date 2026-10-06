"""Abstract base class for all ecosystem adapters."""

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from amstralift.core.models import DependencyChange, GateSummary


def get_node_execution_env(workspace_path: Path | None = None) -> dict[str, str]:
    """Return an environment dictionary augmented with common Node.js, npm, and workspace paths."""
    env = os.environ.copy()
    current_paths = env.get("PATH", "").split(os.pathsep)
    if workspace_path:
        local_bin = str(Path(workspace_path) / "node_modules" / ".bin")
        if os.path.exists(local_bin) and local_bin not in current_paths:
            current_paths.insert(0, local_bin)
    if sys.platform == "win32":
        extra_paths = [
            r"C:\Program Files\nodejs",
            os.path.expandvars(r"%APPDATA%\npm"),
            r"C:\Program Files (x86)\nodejs",
        ]
        for ep in extra_paths:
            if os.path.exists(ep) and ep not in current_paths:
                current_paths.append(ep)
    env["PATH"] = os.pathsep.join(current_paths)
    return env



from collections.abc import Callable


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
    def run_build_and_tests(
        self,
        repo_path: Path,
        timeout_seconds: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GateSummary:
        """Execute build and test gates declared in the repository."""
        pass

    def get_declared_dependencies(self, repo_path: Path) -> dict[str, str]:
        """Return declared {package_name: version} for security auditing."""
        return {}

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply ecosystem modernizations (e.g. control-flow, standalone, syntax)."""
        return []

