"""Security Orchestrator coordinating the 16-step Safe Remediation Workflow across Audit, Preview, and Apply modes."""

import tempfile
from pathlib import Path
from typing import Any

from amstralift.core.workspace import get_active_branch, prepare_stage_a_workspace
from amstralift.governance.vulnerabilities import VulnerabilityManager
from amstralift.security.dependency_graph import DependencyGraphAnalyzer
from amstralift.security.models import (
    AuditReport,
    RemediationPlan,
    RemediationResult,
    VerificationConfidence,
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
    ) -> RemediationResult:
        """Execute the safe remediation workflow."""
        repo_path = repo_path.resolve()
        effective_ecosystem = ecosystem or self.upgrade_orchestrator.auto_detect_ecosystem(repo_path)
        effective_branch = target_branch or get_active_branch(repo_path)

        # 1. Discover all direct & transitive dependencies with introduction chains
        deps = DependencyGraphAnalyzer.analyze(repo_path, effective_ecosystem)

        # 2. Query vulnerability database with governance exemption evaluation
        report = self.scanner.scan_discovered_dependencies(
            deps,
            ecosystem=effective_ecosystem,
            repo_path=str(repo_path),
            governance_manager=self.governance,
        )

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

        # 4. Check governance policy (e.g. major version approval, auto-remediation eligibility)
        has_major = plan.has_major_upgrades
        highest_sev = report.findings[0].severity if report.findings else None
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
            prepare_stage_a_workspace(
                source_repo_path=repo_path,
                target_workspace_path=sandbox_dir,
                target_branch=effective_branch,
            )

            # 6. Apply minimal direct and transitive dependency changes inside sandbox
            modified_files = RemediationEngine.apply_plan(sandbox_dir, effective_ecosystem, plan)

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
                    error_message="Remediation halted: Verification gates failed. Working tree preserved untouched.",
                )

            # 8. Convert plan into DependencyChanges for Stage B publishing
            changes = RemediationPlanGenerator.plan_to_dependency_changes(plan)

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
            )

            # Tag PR with uncertainty label if 0 tests were discovered
            if confidence == VerificationConfidence.COMPILED_UNVERIFIED:
                pr_proposal.labels.append("unverified-no-tests-discovered")

            return RemediationResult(
                mode="apply",
                report=report,
                plan=plan,
                confidence=confidence,
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
