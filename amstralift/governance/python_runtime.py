"""Python Runtime Default Policy Governance (Section 6).

Governed by platform/security team with safe no-match fallback.
- Precedence: repo's own `requires-python` always wins.
- Default selection: newest Python minor that is (a) stable >= 3 months,
  and (b) present in the org's approved base-image list.
- Safe no-match fallback: when no version satisfies both conditions,
  make NO automatic runtime change and flag for manual triage.
"""

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from amstralift.governance.angular_lts import PolicyError


class PythonRuntimeRelease(BaseModel):
    """Release metadata for a Python minor version."""

    minor: str  # e.g. "3.11", "3.12"
    release_date: date
    is_stable: bool = True


class PythonRuntimeConfig(BaseModel):
    """Schema for python-runtime-policy.yaml."""

    approved_base_images: list[str] = Field(min_length=1)  # e.g. ["python:3.11-slim", "python:3.12-slim"]
    known_releases: list[PythonRuntimeRelease] = Field(default_factory=list)
    minimum_stability_days: int = 90  # 3 months
    last_verified: date
    next_review_by: date
    verified_by: str
    owner: str = "Platform / Security Team"


class PythonRuntimeDecision(BaseModel):
    """Result of a Python runtime default policy evaluation."""

    action: Literal["USE_REPO_OVERRIDE", "APPLY_DEFAULT", "NO_CHANGE_FLAG_MANUAL_TRIAGE"]
    resolved_runtime: str | None
    reason: str
    is_stale: bool = False
    flagged_for_manual_triage: bool = False
    escalate_to: str | None = None


def _extract_minor_from_image(image_tag: str) -> str | None:
    """Extract minor version from image string like 'python:3.12-slim' or '3.12'."""
    match = re.search(r"3\.(\d+)", image_tag)
    if match:
        return f"3.{match.group(1)}"
    return None


class PythonRuntimeGovernance:
    """Evaluates and enforces the Python runtime governance rules."""

    @staticmethod
    def load_config(policy_path: Path) -> PythonRuntimeConfig:
        """Load and validate python-runtime-policy.yaml."""
        if not policy_path.exists():
            raise PolicyError(f"Python runtime policy file not found at: {policy_path}")

        try:
            raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise PolicyError("Policy YAML root must be a mapping/dictionary")
            return PythonRuntimeConfig.model_validate(raw)
        except Exception as e:
            raise PolicyError(f"Failed to parse python-runtime-policy.yaml: {e}") from e

    @classmethod
    def evaluate(
        cls,
        config: PythonRuntimeConfig,
        repo_requires_python: str | None = None,
        current_runtime: str | None = None,
        reference_date: date | None = None,
    ) -> PythonRuntimeDecision:
        """Evaluate Python runtime requirements for a repository."""
        today = reference_date or date.today()

        # 1. Precedence: repo declaration always wins
        if repo_requires_python and repo_requires_python.strip():
            clean_repo_req = repo_requires_python.strip()
            return PythonRuntimeDecision(
                action="USE_REPO_OVERRIDE",
                resolved_runtime=clean_repo_req,
                reason=(
                    f"Repository declared explicit 'requires-python' ({clean_repo_req}). "
                    "Precedence rule applies; repository declaration always wins (Section 6)."
                ),
                is_stale=False,
                flagged_for_manual_triage=False,
            )

        # 2. Staleness check
        if today > config.next_review_by:
            return PythonRuntimeDecision(
                action="NO_CHANGE_FLAG_MANUAL_TRIAGE",
                resolved_runtime=current_runtime,
                reason=(
                    f"Python default policy review deadline ({config.next_review_by}) has passed. "
                    "Stale policy cannot be applied silently. Current runtime left untouched; "
                    "flagged for manual platform-team triage."
                ),
                is_stale=True,
                flagged_for_manual_triage=True,
                escalate_to=config.owner,
            )

        # 3. Determine approved minors from images
        approved_minors = set()
        for img in config.approved_base_images:
            minor = _extract_minor_from_image(img)
            if minor:
                approved_minors.add(minor)

        # 4. Filter releases satisfying both (a) >= 90 days stable and (b) in approved base images
        qualifying_minors: list[tuple[int, str]] = []
        for rel in config.known_releases:
            if not rel.is_stable:
                continue
            age_days = (today - rel.release_date).days
            if age_days >= config.minimum_stability_days and rel.minor in approved_minors:
                minor_int = int(rel.minor.split(".")[1])
                qualifying_minors.append((minor_int, rel.minor))

        # 5. Check if any version qualified
        if not qualifying_minors:
            # Section 6 Invariant: No version satisfies both conditions simultaneously.
            # Make NO automatic runtime change. Flag for manual platform triage.
            return PythonRuntimeDecision(
                action="NO_CHANGE_FLAG_MANUAL_TRIAGE",
                resolved_runtime=current_runtime,
                reason=(
                    "No Python version satisfies both conditions simultaneously "
                    f"(stable for at least {config.minimum_stability_days} days and present in approved base images). "
                    "Pipeline makes NO automatic runtime change. Current runtime left untouched; "
                    "flagged for manual platform-team triage."
                ),
                is_stale=False,
                flagged_for_manual_triage=True,
                escalate_to=config.owner,
            )

        # Select the newest qualifying minor
        qualifying_minors.sort(key=lambda x: x[0], reverse=True)
        chosen_minor = qualifying_minors[0][1]

        return PythonRuntimeDecision(
            action="APPLY_DEFAULT",
            resolved_runtime=f">={chosen_minor}",
            reason=(
                f"Selected governed default Python {chosen_minor} "
                f"(stable >= {config.minimum_stability_days} days and present in approved base images). "
                f"Policy verified by {config.verified_by} through {config.next_review_by}."
            ),
            is_stale=False,
            flagged_for_manual_triage=False,
        )
