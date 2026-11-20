"""Data contracts and schemas for Human Approval Workflow (P23).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    """Approval lifecycle status for a modernization request."""

    AUTO_APPROVED = "auto_approved"  # Zero review triggers; automatic PR merge
    PENDING = "pending"              # Requires human approval before publication
    APPROVED = "approved"            # Explicitly approved by authorized reviewer
    REJECTED = "rejected"            # Modernization rejected by human reviewer
    BLOCKED = "blocked"              # Critical policy or risk boundary; blocked


class RiskTier(str, Enum):
    """Categorical risk classification governing automation autonomy."""

    SAFE = "safe"      # Automatic execution permitted
    REVIEW = "review"  # Human approval required
    BLOCK = "block"    # Automated execution halted


class ApprovalTriggerType(str, Enum):
    """Specific event or condition triggering human sign-off."""

    API_BREAKING_CHANGE = "api_breaking_change"
    DATABASE_MIGRATION = "database_migration"
    MAJOR_VERSION_JUMP = "major_version_jump"
    SECURITY_OVERRIDE = "security_override"
    RISKY_TRANSFORMATION = "risky_transformation"
    POLICY_REVIEW_FLAG = "policy_review_flag"


class ApprovalTrigger(BaseModel):
    """A specific condition flagged for human review."""

    trigger_type: ApprovalTriggerType = Field(description="Category of the trigger")
    severity: str = Field(default="warning", description="Severity (warning, critical)")
    title: str = Field(description="Brief summary of the flagged condition")
    description: str = Field(default="", description="Detailed rationale explaining why review is needed")
    affected_targets: list[str] = Field(
        default_factory=list, description="Files, symbols, or packages affected"
    )


class HumanDecision(BaseModel):
    """Cryptographically sealed human review sign-off record."""

    decision_id: str = Field(default_factory=lambda: f"dec-{uuid.uuid4().hex[:8]}")
    decision: ApprovalStatus = Field(description="Verdict: approved or rejected")
    reviewer_name: str = Field(description="Full name of human reviewer")
    reviewer_email: str = Field(description="Verified corporate email of reviewer")
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    rationale: str = Field(description="Justification or business rationale for sign-off")
    signature: str = Field(default="", description="HMAC-SHA256 decision attestation signature")

    def generate_signature(self, secret_key: str = "amstralift-approval-secret") -> str:
        """Generate HMAC-SHA256 signature sealing reviewer identity, decision, and rationale."""
        payload = f"{self.decision_id}|{self.decision.value}|{self.reviewer_name}|{self.reviewer_email}|{self.timestamp}|{self.rationale}"
        return hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_signature(self, secret_key: str = "amstralift-approval-secret") -> bool:
        """Verify the integrity of the approval decision signature."""
        if not self.signature:
            return False
        expected = self.generate_signature(secret_key)
        return hmac.compare_digest(self.signature, expected)


class ApprovalRequest(BaseModel):
    """A queued modernization proposal subject to human governance."""

    request_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:8]}")
    plan_id: str = Field(description="Associated modernization plan ID")
    repo_name: str = Field(description="Name or ID of repository")
    repo_path: str = Field(description="Path to repository")
    ecosystem: str = Field(default="unknown", description="Ecosystem (angular, react, dotnet, python)")
    current_version: Optional[str] = Field(default=None, description="Starting framework version")
    target_version: Optional[str] = Field(default=None, description="Target framework version")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, description="Current approval status")
    risk_tier: RiskTier = Field(default=RiskTier.REVIEW, description="Risk classification")
    triggers: list[ApprovalTrigger] = Field(
        default_factory=list, description="All flagged review triggers"
    )
    decision: Optional[HumanDecision] = Field(
        default=None, description="Reviewer decision if evaluated"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    author: str = Field(default="Amal P S", description="Author attribution")
