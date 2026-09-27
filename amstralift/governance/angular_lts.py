"""Angular LTS Policy Governance (Section 5).

Owned, governed, and self-enforcing.
If next_review_by has passed, the pipeline treats the value as unverified,
falls back to 'latest stable' only, and escalates to the owner.
It does not use stale data silently.
"""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PolicyError(Exception):
    """Raised when policy loading or parsing fails."""

    pass


class AngularLTSConfig(BaseModel):
    """Schema for angular-lts-policy.yaml."""

    current_lts_majors: list[int] = Field(min_length=1)
    last_verified: date
    next_review_by: date
    verified_by: str
    alert_channel: str | None = None


class LTSDecision(BaseModel):
    """Result of an Angular LTS policy evaluation."""

    target_major: int | None
    use_latest_fallback: bool
    is_stale: bool
    reason: str
    escalate_to: str | None = None


class AngularLTSGovernance:
    """Evaluates and enforces the Angular LTS policy."""

    @staticmethod
    def load_config(policy_path: Path) -> AngularLTSConfig:
        """Load and validate angular-lts-policy.yaml from disk."""
        if not policy_path.exists():
            raise PolicyError(f"Angular LTS policy file not found at: {policy_path}")

        try:
            raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise PolicyError("Policy YAML root must be a mapping/dictionary")
            return AngularLTSConfig.model_validate(raw)
        except Exception as e:
            raise PolicyError(f"Failed to parse angular-lts-policy.yaml: {e}") from e

    @classmethod
    def evaluate(
        cls,
        config: AngularLTSConfig | None,
        reference_date: date | None = None,
    ) -> LTSDecision:
        """Evaluate LTS policy against current or reference date."""
        today = reference_date or date.today()

        if config is None:
            return LTSDecision(
                target_major=None,
                use_latest_fallback=True,
                is_stale=True,
                reason="No Angular LTS policy configured. Defaulting to latest stable.",
                escalate_to=None,
            )

        if today > config.next_review_by:
            # Stale value: fail safely to latest stable only and alert owner
            return LTSDecision(
                target_major=None,
                use_latest_fallback=True,
                is_stale=True,
                reason=(
                    f"Angular LTS review deadline ({config.next_review_by}) has passed. "
                    f"Policy is unverified and cannot be used silently. "
                    f"Falling back to latest stable only. Escalating to {config.verified_by}."
                ),
                escalate_to=config.verified_by,
            )

        # Fresh and verified
        target = max(config.current_lts_majors)
        return LTSDecision(
            target_major=target,
            use_latest_fallback=False,
            is_stale=False,
            reason=(
                f"Angular LTS policy verified by {config.verified_by} (valid through {config.next_review_by}). "
                f"Targeting active LTS major {target}."
            ),
            escalate_to=None,
        )
