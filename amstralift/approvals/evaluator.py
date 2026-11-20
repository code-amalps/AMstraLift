"""Risk classification and approval trigger evaluation engine (P23).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from amstralift.approvals.models import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalTrigger,
    ApprovalTriggerType,
    RiskTier,
)
from amstralift.planning.models import ModernizationPlan


class ApprovalEvaluator:
    """Evaluates planned modernizations against enterprise approval triggers."""

    DB_MIGRATION_PATTERNS = [
        re.compile(r"migrations?/.*\.py$", re.IGNORECASE),
        re.compile(r".*alembic/.*\.py$", re.IGNORECASE),
        re.compile(r".*flyway/.*\.sql$", re.IGNORECASE),
        re.compile(r".*\.sql$", re.IGNORECASE),
        re.compile(r".*schema\.prisma$", re.IGNORECASE),
        re.compile(r".*DbContext.*\.cs$", re.IGNORECASE),
    ]

    @classmethod
    def evaluate(
        cls,
        plan: ModernizationPlan,
        repo_path: Optional[Path] = None,
        policy_report: Optional[Any] = None,
    ) -> ApprovalRequest:
        """Evaluate a modernization plan and determine risk tier and approval triggers."""
        triggers: list[ApprovalTrigger] = []
        is_blocked = False

        # 1. Trigger: Major Version Jump >= 2
        curr_maj = cls._parse_major(plan.current_version)
        target_maj = cls._parse_major(plan.target_version)
        if curr_maj is not None and target_maj is not None:
            jump = target_maj - curr_maj
            if jump >= 2:
                triggers.append(
                    ApprovalTrigger(
                        trigger_type=ApprovalTriggerType.MAJOR_VERSION_JUMP,
                        severity="warning",
                        title=f"Multi-Major Framework Version Jump (+{jump} majors)",
                        description=(
                            f"Modernization jumps {jump} major versions from {plan.current_version} to "
                            f"{plan.target_version}. Multi-major upgrades may invalidate intermediate APIs."
                        ),
                        affected_targets=[plan.ecosystem],
                    )
                )

        # 2. Trigger: API Breaking Changes (Semantic contract shifts)
        if hasattr(plan, "metadata") and plan.metadata:
            semantic_diff = plan.metadata.get("semantic_diff", {})
            removed_routes = semantic_diff.get("removed_routes", [])
            removed_symbols = semantic_diff.get("removed_symbols", [])

            if removed_routes or removed_symbols:
                targets = [f"Route: {r}" for r in removed_routes] + [f"Symbol: {s}" for s in removed_symbols]
                triggers.append(
                    ApprovalTrigger(
                        trigger_type=ApprovalTriggerType.API_BREAKING_CHANGE,
                        severity="critical",
                        title="Public API Breaking Contract Mutation",
                        description=(
                            f"Detected removal of {len(removed_routes)} route(s) and {len(removed_symbols)} "
                            "exported symbol(s). Breaking changes require human sign-off."
                        ),
                        affected_targets=targets[:10],
                    )
                )

        # 3. Trigger: Risky Architectural Transformations
        risky_transforms: list[str] = []
        if hasattr(plan, "phases") and plan.phases:
            for ph in plan.phases:
                for t in ph.transformations:
                    risk_val = getattr(t, "risk", None)
                    status_val = getattr(t, "status", None)
                    risk_str = getattr(risk_val, "value", str(risk_val)).lower() if risk_val else ""
                    status_str = getattr(status_val, "value", str(status_val)).lower() if status_val else ""
                    if risk_str in ("high", "critical") or status_str in ("risky", "high"):
                        risky_transforms.append(t.id)
        elif getattr(plan, "risky_count", 0) > 0:
            risky_transforms.append("risky-transformation")

        if risky_transforms:
            triggers.append(
                ApprovalTrigger(
                    trigger_type=ApprovalTriggerType.RISKY_TRANSFORMATION,
                    severity="warning",
                    title=f"Architectural Modernization Transformations ({len(risky_transforms)} rules)",
                    description=(
                        "Plan includes high-risk architectural AST mutations requiring human oversight."
                    ),
                    affected_targets=risky_transforms,
                )
            )

        # 4. Trigger: Database Migration Mutation
        if repo_path and repo_path.is_dir():
            db_files = cls._detect_database_migrations(repo_path)
            if db_files:
                triggers.append(
                    ApprovalTrigger(
                        trigger_type=ApprovalTriggerType.DATABASE_MIGRATION,
                        severity="critical",
                        title=f"Database Migration Infrastructure Detected ({len(db_files)} files)",
                        description=(
                            "Target repository contains schema migration files. Framework upgrade may "
                            "impact database connection drivers or ORM schema mapping."
                        ),
                        affected_targets=db_files[:5],
                    )
                )

        # 5. Trigger: Policy-as-Code Evaluation Flags
        if policy_report is not None:
            if hasattr(policy_report, "blocking_violations") and policy_report.blocking_violations:
                is_blocked = True
                block_msgs = [v.message for v in policy_report.blocking_violations]
                triggers.append(
                    ApprovalTrigger(
                        trigger_type=ApprovalTriggerType.POLICY_REVIEW_FLAG,
                        severity="critical",
                        title="Critical Policy Violation (BLOCK)",
                        description=f"Enterprise policy failed with {len(block_msgs)} blocking violation(s).",
                        affected_targets=block_msgs[:5],
                    )
                )
            elif hasattr(policy_report, "review_violations") and policy_report.review_violations:
                review_msgs = [v.message for v in policy_report.review_violations]
                triggers.append(
                    ApprovalTrigger(
                        trigger_type=ApprovalTriggerType.POLICY_REVIEW_FLAG,
                        severity="warning",
                        title="Policy Governance Review Flag",
                        description=f"Enterprise policy flagged {len(review_msgs)} condition(s) for review.",
                        affected_targets=review_msgs[:5],
                    )
                )

        # Determine Risk Classification & Initial Status
        if is_blocked:
            risk = RiskTier.BLOCK
            status = ApprovalStatus.BLOCKED
        elif triggers:
            risk = RiskTier.REVIEW
            status = ApprovalStatus.PENDING
        else:
            risk = RiskTier.SAFE
            status = ApprovalStatus.AUTO_APPROVED

        return ApprovalRequest(
            plan_id=plan.plan_id,
            repo_name=repo_path.name if repo_path else "repository",
            repo_path=str(repo_path) if repo_path else ".",
            ecosystem=plan.ecosystem,
            current_version=plan.current_version,
            target_version=plan.target_version,
            status=status,
            risk_tier=risk,
            triggers=triggers,
            author="Amal P S",
        )

    @classmethod
    def _parse_major(cls, version_str: Optional[str]) -> Optional[int]:
        """Extract major version integer from version string."""
        if not version_str:
            return None
        match = re.search(r"(\d+)", version_str)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def _detect_database_migrations(cls, repo_path: Path) -> list[str]:
        """Detect migration files within repository."""
        detected = []
        try:
            for p in repo_path.glob("**/*"):
                if p.is_file() and not any(part.startswith(".") for part in p.parts):
                    rel = str(p.relative_to(repo_path)).replace("\\", "/")
                    if any(pat.search(rel) for pat in cls.DB_MIGRATION_PATTERNS):
                        detected.append(rel)
                        if len(detected) >= 10:
                            break
        except Exception:
            pass
        return detected
