"""Policy evaluation engine for Policy-as-Code (P20).

Evaluates declarative EnterprisePolicy rules against a ModernizationPlan.
Produces deterministic PolicyEvaluationReports with ALLOW, REVIEW, or BLOCK verdicts.

Engineered by Amal P S for AMstraLift Generation 2.
"""

import re
from amstralift.planning.models import ModernizationPlan
from amstralift.policy.models import (
    EnterprisePolicy,
    PolicyAction,
    PolicyEvaluationReport,
    PolicySeverity,
    PolicyViolation,
)
from amstralift.transforms.models import TransformationRisk


class PolicyEvaluator:
    """Evaluates declarative EnterprisePolicy against ModernizationPlans."""

    @staticmethod
    def evaluate(
        plan: ModernizationPlan,
        policy: EnterprisePolicy,
    ) -> PolicyEvaluationReport:
        """Run complete policy compliance verification over a ModernizationPlan."""
        violations: list[PolicyViolation] = []
        passed_checks: list[str] = []

        # 1. Governance Policy Evaluation
        gov = policy.governance
        cur_major = None
        if plan.current_version:
            m = re.search(r"(\d+)", str(plan.current_version))
            if m:
                cur_major = int(m.group(1))

        tgt_major = plan.target_major

        if cur_major is not None and tgt_major is not None:
            is_major = tgt_major > cur_major
            jump = tgt_major - cur_major

            if is_major and not gov.allow_major_upgrade:
                violations.append(
                    PolicyViolation(
                        rule_name="governance.allow_major_upgrade",
                        action=PolicyAction.BLOCK,
                        target=f"v{cur_major} -> v{tgt_major}",
                        message=f"Major upgrades are disallowed by policy ({plan.current_version} -> v{tgt_major}).",
                        severity=PolicySeverity.CRITICAL,
                    )
                )
            elif is_major and jump > gov.max_major_jump:
                violations.append(
                    PolicyViolation(
                        rule_name="governance.max_major_jump",
                        action=PolicyAction.BLOCK,
                        target=f"+{jump} majors",
                        message=f"Major version jump (+{jump}) exceeds policy limit of maximum +{gov.max_major_jump} majors.",
                        severity=PolicySeverity.CRITICAL,
                    )
                )
            else:
                passed_checks.append("governance.version_progression")
        else:
            passed_checks.append("governance.version_progression")

        # 2. Transformation Policy Evaluation
        trans_pol = policy.transformations
        all_planned = [
            t for phase in plan.phases for t in phase.transformations
            if not t.id.endswith(":gate-verification")
        ]
        planned_ids = {t.id for t in all_planned}

        # Check blacklisted / disallowed transformations
        for t in all_planned:
            if t.id in trans_pol.disallowed_transformations:
                violations.append(
                    PolicyViolation(
                        rule_name="transformations.disallowed_transformations",
                        action=PolicyAction.BLOCK,
                        target=t.id,
                        message=f"Transformation '{t.id}' is explicitly blacklisted by enterprise policy.",
                        severity=PolicySeverity.CRITICAL,
                    )
                )

        # Check risk tiers
        for t in all_planned:
            if t.risk == TransformationRisk.CRITICAL and not trans_pol.allow_critical_risk:
                violations.append(
                    PolicyViolation(
                        rule_name="transformations.allow_critical_risk",
                        action=PolicyAction.BLOCK,
                        target=t.id,
                        message=f"Transformation '{t.id}' has CRITICAL risk tier which is blocked by enterprise policy.",
                        severity=PolicySeverity.CRITICAL,
                    )
                )
            elif t.risk == TransformationRisk.HIGH and not trans_pol.allow_high_risk:
                violations.append(
                    PolicyViolation(
                        rule_name="transformations.allow_high_risk",
                        action=PolicyAction.REVIEW,
                        target=t.id,
                        message=f"Transformation '{t.id}' has HIGH risk tier and requires human review sign-off.",
                        severity=PolicySeverity.WARNING,
                    )
                )

        # Check required transformations
        for req_id in trans_pol.required_transformations:
            req_prefix = req_id.split(":", 1)[0]
            if req_prefix == plan.ecosystem.lower() and req_id not in planned_ids:
                violations.append(
                    PolicyViolation(
                        rule_name="transformations.required_transformations",
                        action=PolicyAction.BLOCK,
                        target=req_id,
                        message=f"Mandatory transformation '{req_id}' is required by enterprise policy but missing from the plan.",
                        severity=PolicySeverity.CRITICAL,
                    )
                )

        if not any(v.rule_name.startswith("transformations.") for v in violations):
            passed_checks.append("transformations.risk_and_membership")

        # 3. Security Policy Evaluation
        sec_pol = policy.security
        security_findings = plan.metadata.get("security_findings", [])
        for finding in security_findings:
            sev = finding.get("severity", "").lower()
            cve_id = finding.get("id", "UNKNOWN-CVE")
            if sev == "critical" and sec_pol.critical_cve != PolicyAction.ALLOW:
                violations.append(
                    PolicyViolation(
                        rule_name="security.critical_cve",
                        action=sec_pol.critical_cve,
                        target=cve_id,
                        message=f"Vulnerability {cve_id} (CRITICAL) triggers {sec_pol.critical_cve.value.upper()} enforcement.",
                        severity=PolicySeverity.CRITICAL,
                    )
                )
            elif sev == "high" and sec_pol.high_cve != PolicyAction.ALLOW:
                violations.append(
                    PolicyViolation(
                        rule_name="security.high_cve",
                        action=sec_pol.high_cve,
                        target=cve_id,
                        message=f"Vulnerability {cve_id} (HIGH) triggers {sec_pol.high_cve.value.upper()} enforcement.",
                        severity=PolicySeverity.WARNING,
                    )
                )

        if not any(v.rule_name.startswith("security.") for v in violations):
            passed_checks.append("security.vulnerability_thresholds")

        # 4. Semantic Policy Evaluation
        sem_pol = policy.semantic
        semantic_findings = plan.metadata.get("semantic_findings", {})
        removed_symbols = semantic_findings.get("removed_symbols", [])
        if len(removed_symbols) > sem_pol.max_removed_symbols:
            violations.append(
                PolicyViolation(
                    rule_name="semantic.public_api_removal",
                    action=sem_pol.public_api_removal,
                    target=f"{len(removed_symbols)} symbols removed",
                    message=f"Removed {len(removed_symbols)} public API symbols, violating policy maximum of {sem_pol.max_removed_symbols}.",
                    severity=PolicySeverity.CRITICAL if sem_pol.public_api_removal == PolicyAction.BLOCK else PolicySeverity.WARNING,
                )
            )
        else:
            passed_checks.append("semantic.api_preservation")

        # 5. Compile Verdict
        blocking = [v for v in violations if v.action == PolicyAction.BLOCK]
        review = [v for v in violations if v.action == PolicyAction.REVIEW]

        if blocking:
            overall = PolicyAction.BLOCK
            allowed = False
            summary = f"Plan violates {len(blocking)} blocking enterprise policy rules. Execution prohibited."
        elif review:
            overall = PolicyAction.REVIEW
            allowed = True
            summary = f"Plan passes blocking checks but requires human sign-off on {len(review)} review item(s)."
        else:
            overall = PolicyAction.ALLOW
            allowed = True
            summary = "Plan fully adheres to all enterprise modernization policies."

        return PolicyEvaluationReport(
            policy_name=policy.name,
            overall_action=overall,
            allowed=allowed,
            total_violations=len(violations),
            blocking_violations=blocking,
            review_violations=review,
            passed_checks=passed_checks,
            summary=summary,
            metadata={
                "plan_id": plan.plan_id,
                "ecosystem": plan.ecosystem,
                "target_version": plan.target_version,
            },
        )
