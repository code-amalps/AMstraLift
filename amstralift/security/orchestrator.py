"""Security Orchestrator coordinating the 16-step Safe Remediation Workflow across Audit, Preview, and Apply modes."""

import tempfile
from pathlib import Path

from amstralift.core.cancellation import (
    CancellableScope,
    CancellationToken,
    check_cancelled,
)
from amstralift.core.workspace import get_active_branch, prepare_stage_a_workspace
from amstralift.governance.vulnerabilities import (
    VulnerabilityManager,
    VulnerabilitySeverity,
)
from amstralift.security.dependency_graph import DependencyGraphAnalyzer
from amstralift.security.models import (
    RemediationResult,
    VerificationConfidence,
    VerificationStatus,
)
from amstralift.security.osv_client import OSVClient
from amstralift.security.plan_generator import RemediationPlanGenerator
from amstralift.security.remediation_engine import RemediationEngine
from amstralift.security.verification_engine import VerificationEngine
from amstralift.service import UpgradeOrchestrator


class SecurityOrchestrator:
    """Coordinates vulnerability auditing, previewing, and verified surgical remediation."""

    def __init__(
        self,
        policy_path: Path | None = None,
        scanner: OSVClient | None = None,
    ):
        policy = VulnerabilityManager.load_policy(policy_path)
        self.governance = VulnerabilityManager(policy=policy)
        self.scanner = scanner or OSVClient()
        self.upgrade_orchestrator = UpgradeOrchestrator()

    def run_remediation(
        self,
        repo_path: Path,
        ecosystem: str | None = None,
        mode: str = "audit",  # "audit", "preview", "apply"
        target_branch: str | None = None,
        dry_run: bool = False,
        publish: bool = False,
        git_token: str | None = None,
        repo_id: str | None = None,
        remote_url: str | None = None,
        allow_major: bool = False,
        safe_only: bool = True,
        cancellation_token: CancellationToken | None = None,
    ) -> RemediationResult:
        """Execute the safe remediation workflow."""
        with CancellableScope(cancellation_token):
            check_cancelled(cancellation_token)
            repo_path = repo_path.resolve()
            effective_ecosystem = ecosystem or self.upgrade_orchestrator.auto_detect_ecosystem(repo_path)
            effective_branch = target_branch or get_active_branch(repo_path)

            # 1. Discover all direct & transitive dependencies with introduction chains
            deps = DependencyGraphAnalyzer.analyze(repo_path, effective_ecosystem)
            check_cancelled(cancellation_token)

            # 2. Query vulnerability database with governance exemption evaluation
            report = self.scanner.scan_discovered_dependencies(
                deps,
                ecosystem=effective_ecosystem,
                repo_path=str(repo_path),
                governance_manager=self.governance,
            )
            check_cancelled(cancellation_token)

            # Mode: AUDIT
            if mode == "audit":
                return RemediationResult(
                    mode="audit",
                    report=report,
                    plan=None,
                    confidence=VerificationConfidence.VERIFIED_SAFE,
                    remediation_successful=True,
                )

            # 3. Generate compatibility-aware remediation plan
            plan = RemediationPlanGenerator.generate_plan(report)
            check_cancelled(cancellation_token)

            # Mode: PREVIEW
            if mode == "preview":
                return RemediationResult(
                    mode="preview",
                    report=report,
                    plan=plan,
                    confidence=VerificationConfidence.VERIFIED_SAFE,
                    remediation_successful=True,
                )

            # Mode: APPLY
            if mode != "apply":
                raise ValueError(f"Unknown remediation mode: {mode}. Must be 'audit', 'preview', or 'apply'.")

            if not plan.items:
                return RemediationResult(
                    mode="apply",
                    report=report,
                    plan=plan,
                    confidence=VerificationConfidence.VERIFIED_SAFE,
                    remediation_successful=True,
                    error_message="No fixable vulnerabilities available to apply.",
                )

            # 4. Handle major version upgrades and governance policy
            if allow_major:
                self.governance.policy.allow_major_version_upgrades = True

            if plan.has_major_upgrades and not allow_major:
                if safe_only:
                    safe_items = [item for item in plan.items if not item.is_major_bump]
                    major_items = [item for item in plan.items if item.is_major_bump]
                    for item in major_items:
                        plan.advisories.append(
                            f"Skipped breaking major upgrade for '{item.package_name}' "
                            f"({item.current_version} -> {item.target_version}) to remediate {item.cve_id} "
                            f"per enterprise governance policy. Requires explicit human developer review or run with --allow-major."
                        )
                    plan.items = safe_items
                    if not safe_items:
                        return RemediationResult(
                            mode="apply",
                            report=report,
                            plan=plan,
                            confidence=VerificationConfidence.GATES_FAILED,
                            remediation_successful=False,
                            error_message=(
                                "Governance policy restriction: All available remediations require breaking major "
                                f"version upgrades ({', '.join(item.package_name for item in major_items)}). Under default "
                                "governance policy, major upgrades require explicit human developer review. "
                                "Re-run with --allow-major to test major version upgrades."
                            ),
                        )
                else:
                    return RemediationResult(
                        mode="apply",
                        report=report,
                        plan=plan,
                        confidence=VerificationConfidence.GATES_FAILED,
                        remediation_successful=False,
                        error_message=(
                            "Governance policy restriction: Major version upgrades require explicit human developer review "
                            "and cannot be applied automatically. Use --safe-only to apply safe in-major fixes or --allow-major to proceed."
                        ),
                    )

            has_major = plan.has_major_upgrades
            severity_rank = {
                VulnerabilitySeverity.CRITICAL: 4,
                VulnerabilitySeverity.HIGH: 3,
                VulnerabilitySeverity.MEDIUM: 2,
                VulnerabilitySeverity.LOW: 1,
            }
            remediated_pkgs = {item.package_name for item in plan.items}
            remediated_findings = [f for f in report.findings if f.package_name in remediated_pkgs]
            highest_sev = (
                max(remediated_findings, key=lambda f: severity_rank.get(f.severity, 0)).severity
                if remediated_findings
                else None
            )
            if highest_sev:
                allowed, reason = self.governance.is_auto_remediation_allowed(
                    severity=highest_sev, is_major_upgrade=has_major
                )
                if not allowed:
                    return RemediationResult(
                        mode="apply",
                        report=report,
                        plan=plan,
                        confidence=VerificationConfidence.GATES_FAILED,
                        remediation_successful=False,
                        error_message=f"Governance policy restriction: {reason}",
                    )

            # 5. Create isolated Stage A sandbox workspace
            sandbox_dir = Path(tempfile.mkdtemp(prefix="amstralift_security_sandbox_"))

            try:
                check_cancelled(cancellation_token)
                prepare_stage_a_workspace(
                    source_repo_path=repo_path,
                    target_workspace_path=sandbox_dir,
                    target_branch=effective_branch,
                )
                check_cancelled(cancellation_token)

                # 6. Apply minimal direct and transitive dependency changes inside sandbox
                _modified_files = RemediationEngine.apply_plan(sandbox_dir, effective_ecosystem, plan)
                check_cancelled(cancellation_token)

                # 7. Execute the 5 Verification Gates
                (
                    gates_passed,
                    confidence,
                    gate_summary,
                    rescan_report,
                    uncertainty_warning,
                ) = VerificationEngine.execute_gates(
                    workspace_path=sandbox_dir,
                    ecosystem=effective_ecosystem,
                    plan=plan,
                    scanner=self.scanner,
                    governance_manager=self.governance,
                )
                check_cancelled(cancellation_token)

                if not gates_passed:
                    return RemediationResult(
                        mode="apply",
                        report=report,
                        plan=plan,
                        confidence=confidence,
                        gate_summary=gate_summary,
                        rescan_report=rescan_report,
                        uncertainty_warning=uncertainty_warning,
                        remediation_successful=False,
                        error_message=f"Remediation halted: Verification gates failed ({uncertainty_warning}). Working tree preserved untouched." if uncertainty_warning else "Remediation halted: Verification gates failed. Working tree preserved untouched.",
                    )

                # 8. Convert plan into DependencyChanges for Stage B publishing
                changes = RemediationPlanGenerator.plan_to_dependency_changes(plan)
                check_cancelled(cancellation_token)

                # Run full Stage A/B orchestration to sign bundle and commit/push PR
                signed_bundle, pr_proposal = self.upgrade_orchestrator.run_upgrade(
                    repo_path=repo_path,
                    ecosystem=effective_ecosystem,
                    target_branch=effective_branch,
                    dry_run=dry_run,
                    publish=publish,
                    git_token=git_token,
                    repo_id=repo_id,
                    remote_url=remote_url,
                    explicit_changes=changes,
                    allow_failed_gates=True,
                    cancellation_token=cancellation_token,
                )

                # Tag PR with uncertainty labels according to verification outcome
                if confidence == VerificationStatus.UNVERIFIED_NO_TESTS:
                    pr_proposal.labels.extend(["unverified-no-tests", "requires-manual-qa", "policy-manual-approval-required"])
                    pr_proposal.title = f"[UNVERIFIED - NO TESTS] {pr_proposal.title}"
                elif confidence == VerificationStatus.VERIFICATION_INCOMPLETE:
                    pr_proposal.labels.extend(["verification-incomplete", "requires-manual-qa", "policy-manual-approval-required"])
                    pr_proposal.title = f"[INCOMPLETE VERIFICATION] {pr_proposal.title}"
                else:
                    pr_proposal.labels.append("verified-safe")

                return RemediationResult(
                    mode="apply",
                    report=report,
                    plan=plan,
                    verification_status=confidence,
                    gate_summary=gate_summary,
                    rescan_report=rescan_report,
                    branch_name=pr_proposal.branch_name,
                    pr_proposal=pr_proposal,
                    uncertainty_warning=uncertainty_warning,
                    remediation_successful=True,
                )

            finally:
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
