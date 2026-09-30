"""FastAPI application factory and endpoints for AMstraLift REST API."""

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from amstralift import __version__
from amstralift.api.models import (
    AuditApiRequest,
    AuditApiResponse,
    HealthResponse,
    PolicyApiResponse,
    UpgradeApiRequest,
    UpgradeApiResponse,
)
from amstralift.execution.stage_b import StageBPublishError
from amstralift.governance.vulnerabilities import VulnerabilityManager
from amstralift.security.orchestrator import SecurityOrchestrator
from amstralift.service import OrchestrationError, UpgradeOrchestrator


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="AMstraLift API",
        version=__version__,
        description=(
            "Automated Dependency & Framework Upgrade Engine with a cryptographically bound trust boundary, "
            "live container synchronization, and vulnerability remediation across Angular, React, .NET, and Python."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Enable CORS for internal developer portals and dashboards
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    def root():
        """Redirect root to interactive Swagger UI documentation."""
        return RedirectResponse(url="/docs")

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["Health"])
    def get_health():
        """Return server health status, available ecosystem adapters, and host capabilities."""
        docker_available = shutil.which("docker") is not None
        git_available = shutil.which("git") is not None

        return HealthResponse(
            status="healthy" if git_available else "degraded",
            version=__version__,
            available_adapters=["angular", "react", "dotnet", "python"],
            docker_available=docker_available,
            git_available=git_available,
        )

    @app.get("/api/v1/policy", response_model=PolicyApiResponse, tags=["Governance"])
    def get_policy(path: str | None = None):
        """Inspect the active organizational SLA and exception policy."""
        policy_path = Path(path) if path else None
        policy = VulnerabilityManager.load_policy(policy_path=policy_path)
        manager = VulnerabilityManager(policy=policy)

        slas_formatted = {
            k.value if hasattr(k, "value") else str(k): v
            for k, v in policy.sla_days.items()
        }

        exceptions_list: list[dict[str, Any]] = [
            {
                "exception_id": e.exception_id,
                "cve_id": e.cve_id,
                "package_name": e.package_name,
                "severity": e.severity.value,
                "expires_at": e.expiry_date.isoformat(),
                "security_approver": e.security_approver,
                "engineering_approver": e.engineering_approver,
                "is_active": e.is_active(),
            }
            for e in manager.exceptions.values()
        ]

        return PolicyApiResponse(
            policy_loaded=policy_path is not None and policy_path.exists(),
            policy_path=str(policy_path.resolve()) if policy_path and policy_path.exists() else None,
            slas=slas_formatted,
            exceptions_count=len(exceptions_list),
            exceptions=exceptions_list,
        )


    @app.post("/api/v1/audit", response_model=AuditApiResponse, tags=["Security"])
    def run_security_audit(request: AuditApiRequest):
        """Scan dependencies for known vulnerabilities (CVEs) and optionally plan/apply safe fixes."""
        repo_dir = Path(request.repo_path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target repository path does not exist or is not a directory: {request.repo_path}",
            )

        policy_file = Path(request.policy_path) if request.policy_path else None
        orchestrator = SecurityOrchestrator(policy_path=policy_file)

        try:
            result = orchestrator.run_remediation(
                repo_path=repo_dir,
                ecosystem=request.ecosystem,
                mode=request.mode,
                target_branch=request.branch,
                dry_run=request.dry_run,
                publish=request.publish,
                git_token=request.git_token,
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Security audit execution failed: {str(e)}",
            ) from e

        report = result.report
        plan_dict = result.plan.model_dump() if result.plan else None
        verif_dict = {
            "status": (
                result.verification_status.value
                if hasattr(result.verification_status, "value")
                else str(result.verification_status)
            ),
            "uncertainty_warning": result.uncertainty_warning,
        }

        from amstralift.governance.vulnerabilities import VulnerabilitySeverity

        critical_count = sum(1 for f in report.findings if f.severity == VulnerabilitySeverity.CRITICAL)
        high_count = sum(1 for f in report.findings if f.severity == VulnerabilitySeverity.HIGH)
        medium_count = sum(1 for f in report.findings if f.severity == VulnerabilitySeverity.MEDIUM)
        low_count = sum(1 for f in report.findings if f.severity == VulnerabilitySeverity.LOW)

        return AuditApiResponse(
            mode=request.mode,
            ecosystem=result.report.ecosystem,
            findings_count=len(report.findings),
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            remediation_successful=result.remediation_successful,
            branch_name=result.branch_name,
            pr_url=result.pr_proposal.remote_pr_url if result.pr_proposal else None,
            report=report.model_dump(),
            plan=plan_dict,
            verification=verif_dict,
        )

    @app.post("/api/v1/upgrade", response_model=UpgradeApiResponse, tags=["Upgrades"])
    def run_framework_upgrade(request: UpgradeApiRequest):
        """Run full two-stage framework and dependency upgrade workflow."""
        repo_dir = Path(request.repo_path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target repository path does not exist or is not a directory: {request.repo_path}",
            )

        orchestrator = UpgradeOrchestrator(incremental=request.incremental)

        try:
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=repo_dir,
                ecosystem=request.ecosystem,
                target_branch=request.target_branch,
                dry_run=request.dry_run,
                publish=request.publish,
                git_token=request.git_token,
                repo_id=request.repo_id,
                remote_url=request.remote_url,
                allow_failed_gates=request.allow_failed_gates,
                test_timeout=request.test_timeout,
                output_branch=request.output_branch,
                draft_on_fail=request.draft_on_fail,
                modernize=request.modernize,
            )

            changes_data = [
                {
                    "package_name": c.package_name,
                    "from_version": c.from_version,
                    "to_version": c.to_version,
                    "change_type": c.change_type,
                    "tier": c.tier.value,
                    "rationale": c.rationale,
                }
                for c in signed_bundle.bundle.changes
            ]

            gates_data = [
                {
                    "name": g.name,
                    "command": g.command,
                    "status": g.status.value,
                    "exit_code": g.exit_code,
                    "duration_seconds": g.duration_seconds,
                    "diagnostic": g.stdout or g.stderr,
                }
                for g in signed_bundle.bundle.gate_summary.results
            ]

            is_draft = pr_proposal.publish_status in ("DRAFT_COMMITTED", "DRAFT_PUBLISHED")

            return UpgradeApiResponse(
                success=True,
                ecosystem=request.ecosystem or "auto-detected",
                branch_name=pr_proposal.branch_name,
                pr_title=pr_proposal.title,
                publish_status=pr_proposal.publish_status,
                remote_pr_url=pr_proposal.remote_pr_url,
                is_draft=is_draft,
                modernizations=signed_bundle.bundle.modernizations,
                changes=changes_data,
                gate_results=gates_data,
                all_required_passed=signed_bundle.bundle.gate_summary.all_required_passed,
                bundle_id=signed_bundle.bundle.bundle_id,
                run_id=signed_bundle.bundle.run_id,
            )

        except OrchestrationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Orchestration error: {str(e)}",
            ) from e
        except StageBPublishError as e:
            diag_dict = e.diagnostic.model_dump() if getattr(e, "diagnostic", None) else None
            return UpgradeApiResponse(
                success=False,
                ecosystem=request.ecosystem or "unknown",
                error_message=str(e),
                diagnostic=diag_dict,
            )
        except Exception as e:
            return UpgradeApiResponse(
                success=False,
                ecosystem=request.ecosystem or "unknown",
                error_message=str(e),
            )

    return app
