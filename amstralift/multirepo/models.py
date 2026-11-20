"""Data models and schemas for Multi-Repository Modernization (P21).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import datetime
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class RepoEcosystem(str, Enum):
    """Supported repository ecosystems."""

    ANGULAR = "angular"
    REACT = "react"
    DOTNET = "dotnet"
    PYTHON = "python"
    POLYGLOT = "polyglot"
    UNKNOWN = "unknown"


class PackageExport(BaseModel):
    """A library, package, or assembly published or exported by a repository."""

    package_name: str = Field(description="Exported package name, NuGet PackageId, or library name")
    version: Optional[str] = Field(default=None, description="Exported package version if declared")
    ecosystem: str = Field(default="unknown", description="Ecosystem (angular, react, dotnet, python)")
    manifest_path: str = Field(default="", description="Relative path to manifest defining the export")


class PackageImport(BaseModel):
    """A library, package, or assembly consumed/imported by a repository."""

    package_name: str = Field(description="Imported package name")
    version_spec: str = Field(default="*", description="Declared version constraint or range")
    ecosystem: str = Field(default="unknown", description="Ecosystem (angular, react, dotnet, python)")
    is_dev: bool = Field(default=False, description="Whether this is a dev or test dependency")


class RepositoryNode(BaseModel):
    """A standalone repository within an enterprise fleet."""

    repo_id: str = Field(description="Unique repository identifier within the fleet")
    repo_path: str = Field(description="Absolute filesystem path to the repository root")
    rel_path: str = Field(default=".", description="Relative path to the repository root from fleet root")
    git_branch: Optional[str] = Field(default=None, description="Active git branch if in a git repository")
    ecosystems: list[str] = Field(default_factory=list, description="All detected ecosystems in repository")
    primary_ecosystem: str = Field(default="unknown", description="Primary ecosystem (angular, react, dotnet, python)")
    framework_version: Optional[str] = Field(default=None, description="Primary framework version detected")
    exports: list[PackageExport] = Field(default_factory=list, description="Packages published by this repo")
    imports: list[PackageImport] = Field(default_factory=list, description="Packages consumed by this repo")
    tags: list[str] = Field(default_factory=list, description="Categorical enterprise tags (e.g. core, service)")

    def exported_package_names(self) -> set[str]:
        """Return a normalized set of exported package names."""
        return {e.package_name.lower() for e in self.exports}

    def imported_package_names(self) -> set[str]:
        """Return a normalized set of imported package names."""
        return {i.package_name.lower() for i in self.imports}


class InterRepoDependency(BaseModel):
    """A directed dependency between two repositories in the fleet.

    Provider must be modernized/verified before Consumer can upgrade.
    """

    consumer_repo_id: str = Field(description="Downstream repository consuming the package")
    provider_repo_id: str = Field(description="Upstream repository providing the package")
    package_name: str = Field(description="Package name connecting the two repositories")
    version_required: str = Field(default="*", description="Version requirement declared by consumer")
    ecosystem: str = Field(default="polyglot", description="Ecosystem of the package")
    dependency_type: str = Field(default="runtime", description="runtime, dev, or explicit")


class FleetTopology(BaseModel):
    """Complete cross-repository topology for an enterprise fleet."""

    root_path: str = Field(description="Root path from which the fleet was discovered")
    repositories: dict[str, RepositoryNode] = Field(default_factory=dict, description="Discovered repository nodes")
    dependencies: list[InterRepoDependency] = Field(default_factory=list, description="Inter-repo dependencies")

    def get_consumers(self, repo_id: str) -> list[str]:
        """Return sorted list of repo IDs that consume packages from the given repo."""
        consumers = {d.consumer_repo_id for d in self.dependencies if d.provider_repo_id == repo_id}
        return sorted(list(consumers))

    def get_providers(self, repo_id: str) -> list[str]:
        """Return sorted list of repo IDs that the given repo consumes packages from."""
        providers = {d.provider_repo_id for d in self.dependencies if d.consumer_repo_id == repo_id}
        return sorted(list(providers))

    @property
    def isolated_repos(self) -> list[str]:
        """Repositories with neither incoming nor outgoing inter-repo dependencies."""
        connected = {d.consumer_repo_id for d in self.dependencies} | {d.provider_repo_id for d in self.dependencies}
        return sorted([r for r in self.repositories.keys() if r not in connected])

    @property
    def provider_repos(self) -> list[str]:
        """Repositories that provide packages to other repositories in the fleet."""
        return sorted(list({d.provider_repo_id for d in self.dependencies}))

    @property
    def consumer_repos(self) -> list[str]:
        """Repositories that consume packages from other repositories in the fleet."""
        return sorted(list({d.consumer_repo_id for d in self.dependencies}))

    @property
    def ecosystems(self) -> list[str]:
        """All distinct ecosystems across all repositories in the fleet."""
        ecos = set()
        for r in self.repositories.values():
            ecos.update(r.ecosystems)
        return sorted(list(ecos))


class FleetExecutionWave(BaseModel):
    """A stage of repositories that can be modernized concurrently."""

    wave_number: int = Field(description="1-based wave index")
    name: str = Field(description="Descriptive wave name")
    repo_ids: list[str] = Field(default_factory=list, description="Repositories scheduled in this wave")
    description: str = Field(default="", description="Summary of wave responsibilities")


class MultiRepoModernizationPlan(BaseModel):
    """Declarative, deterministic modernization plan for an enterprise fleet."""

    plan_id: str = Field(default_factory=lambda: f"fleet-plan-{uuid.uuid4().hex[:8]}")
    created_at: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    target_version: Optional[int] = Field(default=None, description="Global target version override")
    ecosystem_filter: Optional[str] = Field(default=None, description="Optional ecosystem filter")
    waves: list[FleetExecutionWave] = Field(default_factory=list, description="Topologically ordered execution waves")
    total_repositories: int = Field(default=0, description="Total repositories participating in this plan")
    cross_repo_edges: int = Field(default=0, description="Total inter-repo dependency edges")
    dependency_matrix: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map from repo_id to required upstream prerequisite repo_ids",
    )
    summary: str = Field(default="", description="Executive summary of the fleet modernization campaign")
    author: str = Field(default="Amal P S", description="Author attribution")


class FleetManifestRepo(BaseModel):
    """Repository item declared in a fleet.yaml manifest."""

    id: str = Field(description="Repository identifier")
    path: str = Field(description="Relative or absolute filesystem path")
    ecosystem: Optional[str] = Field(default=None, description="Optional ecosystem hint")
    tags: list[str] = Field(default_factory=list, description="Tags or labels")
    upstream: list[str] = Field(default_factory=list, description="Explicit upstream repository IDs")


class FleetManifest(BaseModel):
    """Declarative fleet manifest file (fleet.yaml)."""

    name: str = Field(default="enterprise-fleet", description="Fleet name")
    version: str = Field(default="1.0", description="Manifest schema version")
    repositories: list[FleetManifestRepo] = Field(default_factory=list, description="Repositories in fleet")
    targets: dict[str, int] = Field(default_factory=dict, description="Target major versions per ecosystem")
