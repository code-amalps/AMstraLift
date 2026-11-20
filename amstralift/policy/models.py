"""Data contracts and schemas for Policy-as-Code (P20).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from enum import Enum
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
import yaml


class PolicyAction(str, Enum):
    """Enforcement outcome of an enterprise policy evaluation."""

    ALLOW = "allow"    # Compliant, execution proceeds unhindered
    REVIEW = "review"  # Warning / review flag; requires human sign-off
    BLOCK = "block"    # Non-compliant; execution halted immediately


class PolicySeverity(str, Enum):
    """Urgency of a policy violation."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SecurityPolicy(BaseModel):
    """Declarative rules for vulnerability thresholds and security posture."""

    critical_cve: PolicyAction = Field(default=PolicyAction.BLOCK)
    high_cve: PolicyAction = Field(default=PolicyAction.REVIEW)
    medium_cve: PolicyAction = Field(default=PolicyAction.ALLOW)
    low_cve: PolicyAction = Field(default=PolicyAction.ALLOW)
    block_unfixable_cve: bool = Field(default=False)


class SemanticPolicy(BaseModel):
    """Declarative rules for breaking API and contract changes."""

    public_api_removal: PolicyAction = Field(default=PolicyAction.BLOCK)
    route_change: PolicyAction = Field(default=PolicyAction.REVIEW)
    max_removed_symbols: int = Field(default=0)


class TransformationPolicy(BaseModel):
    """Declarative rules governing AST transformations and risk tiers."""

    deterministic_only: bool = Field(default=True, description="Strictly prohibit non-deterministic changes")
    allow_high_risk: bool = Field(default=True, description="Allow transformations with HIGH risk tier")
    allow_critical_risk: bool = Field(default=False, description="Block transformations with CRITICAL risk tier")
    disallowed_transformations: list[str] = Field(default_factory=list, description="Explicit blacklist of transformation IDs")
    required_transformations: list[str] = Field(default_factory=list, description="Transformations that must be scheduled if ecosystem matches")


class GovernancePolicy(BaseModel):
    """Declarative rules for framework lifecycles and runtime posture."""

    allow_major_upgrade: bool = Field(default=True)
    max_major_jump: int = Field(default=5, description="Maximum number of major versions allowed in a single jump")
    disallow_eol_runtime: bool = Field(default=True)
    eol_warning_days: int = Field(default=180)
    prefer_lts_only: bool = Field(default=True)


class EnterprisePolicy(BaseModel):
    """Top-level enterprise modernization policy specification."""

    version: str = Field(default="1.0")
    name: str = Field(default="enterprise-default")
    description: str = Field(default="Enterprise modernization policy and compliance rules")
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)
    semantic: SemanticPolicy = Field(default_factory=SemanticPolicy)
    transformations: TransformationPolicy = Field(default_factory=TransformationPolicy)
    governance: GovernancePolicy = Field(default_factory=GovernancePolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_yaml(self) -> str:
        """Serialize policy to YAML format."""
        raw_dict = self.model_dump(mode="json")
        return yaml.dump({"policy": raw_dict}, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "EnterprisePolicy":
        """Deserialize policy from YAML content."""
        data = yaml.safe_load(yaml_content)
        if isinstance(data, dict) and "policy" in data:
            return cls.model_validate(data["policy"])
        return cls.model_validate(data)

    def save(self, path: Path | str) -> Path:
        """Save policy to a file."""
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_yaml(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path | str) -> "EnterprisePolicy":
        """Load policy from a YAML or JSON file."""
        p = Path(path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Policy file not found: {p}")
        return cls.from_yaml(p.read_text(encoding="utf-8"))


class PolicyViolation(BaseModel):
    """Details of a single policy non-compliance event."""

    rule_name: str = Field(description="Name of violated policy constraint (e.g. 'governance.max_major_jump')")
    action: PolicyAction = Field(description="Action mandated by policy: BLOCK, REVIEW, or ALLOW")
    target: str = Field(description="Component, transformation, or version violating the policy")
    message: str = Field(description="Human-readable explanation of violation")
    severity: PolicySeverity = Field(default=PolicySeverity.WARNING)


class PolicyEvaluationReport(BaseModel):
    """Aggregate result of evaluating a ModernizationPlan against an EnterprisePolicy."""

    policy_name: str
    overall_action: PolicyAction = Field(default=PolicyAction.ALLOW)
    allowed: bool = Field(default=True, description="True if no BLOCK violations exist")
    total_violations: int = Field(default=0)
    blocking_violations: list[PolicyViolation] = Field(default_factory=list)
    review_violations: list[PolicyViolation] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)
    summary: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
