"""Data contracts and schemas for Modernization Campaign Engine (P22).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import datetime
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RepoCampaignStatus(str, Enum):
    """Execution status of a single repository within a campaign."""

    PENDING = "pending"
    MODERNIZED = "modernized"
    PARTIAL = "partial"
    REFUSED = "refused"
    FAILED = "failed"
    SKIPPED = "skipped"


class CampaignStatus(str, Enum):
    """Lifecycle state of an organization-wide modernization campaign."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class RepoCampaignRecord(BaseModel):
    """Tracks the modernization trajectory and outcomes of a single repository."""

    repo_id: str = Field(description="Unique repository identifier within the fleet")
    repo_path: str = Field(description="Filesystem path to the repository")
    ecosystem: str = Field(default="unknown", description="Primary ecosystem (angular, react, dotnet, python)")
    initial_version: Optional[str] = Field(default=None, description="Starting framework version")
    target_version: Optional[str] = Field(default=None, description="Campaign target version")
    current_version: Optional[str] = Field(default=None, description="Current version after execution")
    status: RepoCampaignStatus = Field(default=RepoCampaignStatus.PENDING, description="Current campaign status")
    wave_number: int = Field(default=1, description="Topological execution wave index")
    cves_before: int = Field(default=0, description="Vulnerability count prior to modernization")
    cves_after: int = Field(default=0, description="Vulnerability count following modernization")
    is_eol_before: bool = Field(default=False, description="Whether version was EOL prior to upgrade")
    is_eol_after: bool = Field(default=False, description="Whether version is EOL following upgrade")
    verification_time_seconds: float = Field(default=0.0, description="Verification gate runtime in seconds")
    transforms_applied: int = Field(default=0, description="Count of AST/configuration transforms applied")
    transforms_refused: int = Field(default=0, description="Count of transforms refused by policy/boundary")
    pr_url: Optional[str] = Field(default=None, description="Published Pull Request URL if applicable")
    error_message: Optional[str] = Field(default=None, description="Diagnostic failure message if failed")
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class EcosystemCampaignMetrics(BaseModel):
    """Aggregated campaign performance partitioned by ecosystem."""

    ecosystem: str = Field(description="Ecosystem identifier (angular, react, dotnet, python)")
    total_repos: int = Field(default=0)
    modernized: int = Field(default=0)
    partial: int = Field(default=0)
    refused: int = Field(default=0)
    failed: int = Field(default=0)
    pending: int = Field(default=0)
    cves_eliminated: int = Field(default=0)
    eol_eliminated: int = Field(default=0)
    hours_saved: float = Field(default=0.0)


class CampaignDashboardMetrics(BaseModel):
    """Comprehensive executive dashboard metrics for an organization-wide campaign."""

    total_repos: int = Field(default=0, description="Total repositories participating in campaign")
    modernized_count: int = Field(default=0, description="Fully modernized repositories")
    partial_count: int = Field(default=0, description="Partially modernized repositories")
    refused_count: int = Field(default=0, description="Repositories refused by policy/boundary")
    failed_count: int = Field(default=0, description="Repositories that encountered verification failures")
    pending_count: int = Field(default=0, description="Repositories waiting to be scheduled")
    
    total_cves_before: int = Field(default=0, description="Total baseline CVE vulnerabilities")
    total_cves_after: int = Field(default=0, description="Remaining post-modernization CVEs")
    cve_reduction_pct: float = Field(default=0.0, description="Percentage CVE reduction (negative represents drop)")

    eol_repos_before: int = Field(default=0, description="Repositories running EOL frameworks initially")
    eol_repos_after: int = Field(default=0, description="Repositories remaining on EOL frameworks")
    eol_reduction_pct: float = Field(default=0.0, description="Percentage reduction in EOL repositories")

    engineering_hours_saved: float = Field(default=0.0, description="Calculated engineering person-hours saved")
    upgrade_success_rate: float = Field(default=0.0, description="Successful modernization rate (modernized+partial)")
    refusal_rate: float = Field(default=0.0, description="Percentage of repositories refused by policy")
    mean_verification_time_seconds: float = Field(default=0.0, description="Average verification runtime in seconds")
    mean_verification_time_display: str = Field(default="0m 0s", description="Formatted mean verification time")

    ecosystem_breakdown: dict[str, EcosystemCampaignMetrics] = Field(
        default_factory=dict, description="Metrics partitioned by ecosystem"
    )


class ModernizationCampaign(BaseModel):
    """An organization-wide modernization campaign spanning fleets of repositories."""

    campaign_id: str = Field(default_factory=lambda: f"campaign-{uuid.uuid4().hex[:8]}")
    name: str = Field(description="Campaign title (e.g. Q1 2027 Angular Modernization)")
    description: str = Field(default="", description="Executive charter and objective of campaign")
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    status: CampaignStatus = Field(default=CampaignStatus.DRAFT)
    targets: dict[str, int] = Field(
        default_factory=dict,
        description="Target framework major versions per ecosystem (e.g. angular: 18, react: 19)",
    )
    fleet_root: str = Field(default="", description="Base directory containing repositories or manifest")
    repository_records: dict[str, RepoCampaignRecord] = Field(
        default_factory=dict, description="Per-repository campaign execution records"
    )
    metrics: CampaignDashboardMetrics = Field(
        default_factory=CampaignDashboardMetrics, description="Aggregated campaign dashboard metrics"
    )
    author: str = Field(default="Amal P S", description="Author attribution")
