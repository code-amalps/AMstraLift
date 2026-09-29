"""Pydantic data models for the AMstraLift REST API."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health status and environment capabilities."""
    status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
    version: str
    available_adapters: list[str]
    docker_available: bool
    git_available: bool


class AuditApiRequest(BaseModel):
    """Request payload to scan and optionally remediate vulnerabilities."""
    repo_path: str = Field(description="Local path to the target repository directory.")
    ecosystem: str | None = Field(default=None, description="Optional ecosystem override ('angular', 'react', 'dotnet', 'python').")
    mode: Literal["audit", "preview", "apply"] = Field(default="audit", description="Remediation mode.")
    policy_path: str | None = Field(default=None, description="Optional path to custom security-policy.yaml.")
    branch: str | None = Field(default=None, description="Optional git branch to target.")
    dry_run: bool = Field(default=False, description="Simulate fixes without modifying the repository.")
    publish: bool = Field(default=False, description="Publish pull request to remote Git provider.")
    git_token: str | None = Field(default=None, description="Optional Git provider access token.")


class AuditApiResponse(BaseModel):
    """Response payload for dependency security audit and remediation."""
    mode: str
    ecosystem: str
    findings_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    remediation_successful: bool
    branch_name: str | None = None
    pr_url: str | None = None
    report: dict[str, Any]
    plan: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None


class UpgradeApiRequest(BaseModel):
    """Request payload to run full framework/dependency upgrades."""
    repo_path: str = Field(description="Local path to the target repository directory.")
    ecosystem: str | None = Field(default=None, description="Target ecosystem. Auto-detected if omitted.")
    target_branch: str = Field(default="main", description="Target base branch name.")
    dry_run: bool = Field(default=False, description="Simulate without modifying git branch/commit.")
    publish: bool = Field(default=False, description="Push branch and open reviewable PR on remote.")
    incremental: bool = Field(default=True, description="Upgrade framework dependencies incrementally (+1 major).")
    allow_failed_gates: bool = Field(default=False, description="Allow Stage B to commit even if verification gates fail/timeout.")
    test_timeout: float = Field(default=300.0, description="Timeout in seconds for build/test gates.")
    git_token: str | None = Field(default=None, description="Least-privilege Git provider access token.")
    repo_id: str | None = Field(default=None, description="Repository identifier (e.g. owner/repo).")
    remote_url: str | None = Field(default=None, description="Explicit Git remote URL for push.")
    output_branch: str | None = Field(default=None, description="Target branch name for the upgrade commits.")


class UpgradeApiResponse(BaseModel):
    """Response payload for framework and dependency upgrades."""
    success: bool
    ecosystem: str
    branch_name: str | None = None
    pr_title: str | None = None
    publish_status: str | None = None
    remote_pr_url: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)
    gate_results: list[dict[str, Any]] = Field(default_factory=list)
    all_required_passed: bool = False
    bundle_id: str | None = None
    run_id: str | None = None
    error_message: str | None = None


class PolicyApiResponse(BaseModel):
    """Active organizational SLA and vulnerability governance policy."""
    policy_loaded: bool
    policy_path: str | None
    slas: dict[str, int]
    exceptions_count: int
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
