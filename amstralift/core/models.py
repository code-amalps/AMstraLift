"""Core Pydantic models and data contracts for AMstraLift."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class DependencyTier(str, Enum):
    """Dependency tiers based on risk level (Section 8)."""

    TIER_1_SAFE = "TIER_1_SAFE"
    TIER_2_VERIFY_BEHAVIOR = "TIER_2_VERIFY_BEHAVIOR"
    TIER_3_CRITICAL = "TIER_3_CRITICAL"

    @property
    def labels(self) -> list[str]:
        match self:
            case DependencyTier.TIER_1_SAFE:
                return ["tier-1-safe"]
            case DependencyTier.TIER_2_VERIFY_BEHAVIOR:
                return ["tier-2-verify-behavior", "requires-functional-qa"]
            case DependencyTier.TIER_3_CRITICAL:
                return ["tier-3-critical", "requires-manual-security-verification"]


class DependencyChange(BaseModel):
    """Individual package change proposal."""

    package_name: str
    from_version: str
    to_version: str
    change_type: Literal["direct", "transitive", "dev"] = "direct"
    tier: DependencyTier = DependencyTier.TIER_1_SAFE
    rationale: str | None = None
    parent_package: str | None = None
    introduced_by: list[str] = Field(default_factory=list)
    project_path: str = "."  # relative path within repository (e.g. 'client' or '.')


class GateStatus(str, Enum):
    """Build and test gate result classification (Section 9)."""

    REQUIRED_PASSED = "REQUIRED_PASSED"
    REQUIRED_SKIPPED = "REQUIRED_SKIPPED"      # tooling absent — uncertain
    REQUIRED_TIMEOUT = "REQUIRED_TIMEOUT"      # runner hung (e.g. watch mode) — uncertain
    REQUIRED_FAILED = "REQUIRED_FAILED"        # ran and explicitly failed
    OPTIONAL_PASSED = "OPTIONAL_PASSED"
    OPTIONAL_FAILED = "OPTIONAL_FAILED"


class GateResult(BaseModel):
    """Result of an individual build, test, or lint check."""

    name: str
    command: str
    status: GateStatus
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class GateSummary(BaseModel):
    """Aggregated status of all execution gates."""

    results: list[GateResult] = Field(default_factory=list)

    @computed_field
    def all_required_passed(self) -> bool:
        required = [
            r
            for r in self.results
            if r.status in (
                GateStatus.REQUIRED_PASSED,
                GateStatus.REQUIRED_FAILED,
                GateStatus.REQUIRED_SKIPPED,
                GateStatus.REQUIRED_TIMEOUT,
            )
        ]
        if not required:
            return False
        return all(r.status == GateStatus.REQUIRED_PASSED for r in required)

    @computed_field
    def has_required_failures(self) -> bool:
        """True only when tests actually ran and explicitly failed (not timeout/skipped)."""
        return any(r.status == GateStatus.REQUIRED_FAILED for r in self.results)

    @computed_field
    def has_required_skips(self) -> bool:
        return any(r.status == GateStatus.REQUIRED_SKIPPED for r in self.results)

    @computed_field
    def has_required_timeouts(self) -> bool:
        """True when a required gate timed out — test result is uncertain, not confirmed broken."""
        return any(r.status == GateStatus.REQUIRED_TIMEOUT for r in self.results)


class MigrationClassification(BaseModel):
    """Migration safety classification (Section 7)."""

    touched_files: list[str] = Field(default_factory=list)
    application_source_modified: bool = False
    manifest_or_lockfile_only: bool = True
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    rationale: str = ""


class UnsignedAdvisoryBundle(BaseModel):
    """Raw advisory output produced by untrusted Stage A.

    Contains proposed diff, raw tool logs, and lockfile hashes.
    Does NOT contain publishing credentials or cryptographic signature.
    """

    bundle_id: str = Field(default_factory=lambda: f"bnd_{uuid4().hex[:12]}")
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    repo_url: str
    target_branch: str = "main"
    base_commit_sha: str
    head_commit_sha: str
    patch: str
    patch_sha256: str
    lockfile_hashes_before: dict[str, str] = Field(default_factory=dict)
    lockfile_hashes_after: dict[str, str] = Field(default_factory=dict)
    changes: list[DependencyChange] = Field(default_factory=list)
    gate_summary: GateSummary = Field(default_factory=GateSummary)
    migration: MigrationClassification = Field(default_factory=MigrationClassification)
    advisory_notes: list[str] = Field(default_factory=list)
    modernizations: list[str] = Field(default_factory=list)
    # Modernizations that Stage A could not apply (require live node_modules) — Stage B runs these
    # on the real repo after the patch is committed.
    pending_modernizations: list[str] = Field(default_factory=list)

    @computed_field
    def highest_tier(self) -> DependencyTier:
        if any(c.tier == DependencyTier.TIER_3_CRITICAL for c in self.changes):
            return DependencyTier.TIER_3_CRITICAL
        if any(c.tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR for c in self.changes):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR
        return DependencyTier.TIER_1_SAFE


class SignedAdvisoryBundle(BaseModel):
    """Cryptographically bound bundle signed by the Trusted Host.

    Verifiable by Stage B prior to any publishing action.
    """

    bundle: UnsignedAdvisoryBundle
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    signature: str

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class UpgradeRequest(BaseModel):
    """Specification of an upgrade run."""

    repo_path_or_url: str
    target_branch: str = "main"
    ecosystem: Literal["angular", "dotnet", "python", "react"] = "angular"
    package_targets: list[str] | None = None
    dry_run: bool = False
    allow_breaking: bool = False
    publish: bool = False
    git_token: str | None = None
    git_provider: str = "github"
    repo_id: str | None = None
    remote_url: str | None = None


class PullRequestProposal(BaseModel):
    """Stage B output ready for review."""

    branch_name: str
    title: str
    body: str
    labels: list[str]
    deadline_24h: datetime
    base_commit_sha: str
    patch_sha256: str
    tier: DependencyTier
    idempotency_key: str | None = None
    remote_pr_number: int | None = None
    remote_pr_url: str | None = None
    publish_status: str = "LOCAL_ONLY"


class DiscoveredProject(BaseModel):
    """An individual software project discovered in a repository or monorepo."""

    name: str                                  # Display name (e.g. 'frontend' or 'MyApi')
    rel_path: str                              # Relative directory from repo root (e.g. 'client' or '.')
    abs_path: str                              # Absolute filesystem path
    ecosystem: str                             # 'angular', 'react', 'dotnet', 'python'
    framework_version: str | None = None       # e.g. '17.2.0', 'net8.0', '3.11'
    manifest_file: str                         # e.g. 'package.json', 'Api.csproj', 'pyproject.toml'


class MonorepoTopology(BaseModel):
    """Topology of discovered projects across single-project repos or polyglot monorepos."""

    is_monorepo: bool                          # True if repo contains multiple projects or sub-projects
    projects: list[DiscoveredProject]          # All detected projects
    ecosystems: list[str]                      # Unique list of ecosystems present


