"""Stage B: Trusted publisher environment.

Operates with minimal attack surface, holds PR publishing credentials,
and NEVER executes repository-controlled code or scripts.

Validates bundle authenticity, expiration, branch head concurrency,
remote target branch drift, path allowlists, and required gate passes before opening any PR.
Provides idempotent remote PR publishing, collision-free branch naming, and conflict avoidance.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amstralift.core.crypto import verify_bundle
from amstralift.core.models import GateStatus, GateSummary, PullRequestProposal, SignedAdvisoryBundle
from amstralift.core.security import validate_patch_security
from amstralift.core.workspace import (
    extract_patch_files,
    get_head_commit,
    is_working_tree_clean,
    run_git,
    verify_rediff_integrity,
)
from amstralift.publisher.base import (
    BaseGitProvider,
    calculate_idempotency_key,
    derive_deterministic_branch_name,
)

logger = logging.getLogger(__name__)


class StageBPublishError(Exception):
    """Raised when Stage B validation or publishing fails."""

    def __init__(self, message: str, gate_summary: GateSummary | None = None):
        super().__init__(message)
        self.gate_summary = gate_summary


def run_stage_b(
    signed_bundle: SignedAdvisoryBundle,
    target_repo_path: Path,
    secret_key: bytes,
    dry_run: bool = False,
    provider: BaseGitProvider | None = None,
    repo_id: str | None = None,
    remote_url: str | None = None,
    git_token: str | None = None,
    publish: bool = False,
    allow_failed_gates: bool = False,
) -> PullRequestProposal:
    """Validate signed advisory bundle, handle duplicate checks, and create or open a PR."""
    bundle = signed_bundle.bundle

    # 1. Require clean local working tree before any modifications
    if not is_working_tree_clean(target_repo_path):
        dirty_output = (run_git(["status", "--short"], cwd=target_repo_path).stdout or "").strip()
        if not dry_run:
            raise StageBPublishError(
                f"Target repository working tree at {target_repo_path} has uncommitted or dirty changes:\n"
                f"{dirty_output}\n"
                "Stage B requires a clean repository to safely apply patches."
            )
        else:
            logger.info("Target repository has uncommitted changes, proceeding in dry-run mode.")

    # 2. Cryptographic signature and TTL verification
    verify_bundle(signed_bundle, secret_key)

    # 3. Strict path allowlists and security rejections
    validate_patch_security(bundle.patch)

    # 4. Target branch head concurrency check (base commit must match target branch)
    res = run_git(["rev-parse", bundle.target_branch], cwd=target_repo_path)
    if res.returncode == 0 and (res.stdout or "").strip():
        branch_head = res.stdout.strip()
    else:
        branch_head = get_head_commit(target_repo_path)

    if branch_head != bundle.base_commit_sha:
        raise StageBPublishError(
            f"Stale base commit: branch '{bundle.target_branch}' is at {branch_head}, bundle was built against {bundle.base_commit_sha}. "
            "Rebase-and-rerun required."
        )

    # 5. Remote target branch drift check (validate remote target branch still points to base commit)
    if publish and provider and remote_url:
        remote_target_sha = provider.get_remote_branch_head(
            target_repo_path, bundle.target_branch, remote_url, git_token
        )
        if remote_target_sha is not None and remote_target_sha != bundle.base_commit_sha:
            raise StageBPublishError(
                f"Stale base commit: remote '{bundle.target_branch}' has moved to {remote_target_sha}, "
                f"bundle was built against {bundle.base_commit_sha}. Remote branch has diverged; rebase-and-rerun required."
            )

    # 6. Required gates verification (Section 1: never open a PR if required checks failed)
    if not dry_run and not allow_failed_gates and not bundle.gate_summary.all_required_passed:
        issues = []
        for r in bundle.gate_summary.results:
            if r.status == GateStatus.REQUIRED_FAILED:
                msg = (r.stderr or r.stdout or f"exit code {r.exit_code}").strip().replace("\r", "").replace("\n", " ")
                issues.append(f"'{r.name}' FAILED: {msg[:120]}")
            elif r.status == GateStatus.REQUIRED_TIMEOUT:
                msg = (r.stdout or "runner timed out").strip().replace("\r", "").replace("\n", " ")
                issues.append(f"'{r.name}' TIMED OUT (uncertain — not confirmed broken): {msg[:120]}")
            elif r.status == GateStatus.REQUIRED_SKIPPED:
                msg = (r.stdout or "tooling not available in environment").strip().replace("\r", "").replace("\n", " ")
                issues.append(f"'{r.name}' SKIPPED: {msg[:80]}")
        details = f" ({'; '.join(issues)})" if issues else ""

        if bundle.gate_summary.has_required_timeouts and not bundle.gate_summary.has_required_failures:
            tip = (
                " Test runner timed out — this usually means Chrome/Chromium is not installed. "
                "Install chromium on this host, or re-run with '--allow-failed-gates' to proceed "
                "with an explicit acknowledgement that tests are unverified."
            )
        else:
            tip = ""

        raise StageBPublishError(
            f"Cannot open PR: Not all required build/test gates passed in Stage A{details}.{tip} "
            "Per Section 1 and 3, AMstraLift fails closed with an issue, never a PR.",
            gate_summary=bundle.gate_summary,
        )

    # 7. Re-diff and clean application check
    if not dry_run:
        verify_rediff_integrity(
            target_repo_path,
            bundle.base_commit_sha,
            bundle.patch,
            bundle.patch_sha256,
        )

    # 8. Construct Deterministic, Collision-Free Branch Name and Idempotency Key
    from amstralift.core.grouping import group_dependency_changes

    tier = bundle.highest_tier
    batches = group_dependency_changes(bundle.changes)
    if batches:
        lead_batch = batches[0]
        primary_pkg = lead_batch.family_id if len(lead_batch.changes) > 1 else lead_batch.changes[0].package_name
        target_ver = lead_batch.primary_version
        pr_title = lead_batch.pr_title
    else:
        primary_pkg = "dependencies"
        target_ver = "update"
        pr_title = f"chore(deps): upgrade dependencies ({tier.value})"

    effective_repo_id = repo_id or target_repo_path.name
    idempotency_key = calculate_idempotency_key(
        repo_id=effective_repo_id,
        target_branch=bundle.target_branch,
        changes=bundle.changes,
        base_commit_sha=bundle.base_commit_sha,
    )
    # Short hash from idempotency key guarantees collision-free branch naming across branches/commits
    branch_name = derive_deterministic_branch_name(
        primary_pkg, target_ver, idempotency_key=idempotency_key
    )

    now = datetime.now(timezone.utc)
    deadline_24h = now + timedelta(hours=24)

    # Compose PR labels based on tier and migration impact
    labels = list(tier.labels)
    if bundle.migration.application_source_modified:
        labels.append("migration-manual-review-required")
    if not bundle.gate_summary.all_required_passed:
        labels.append("gates-failed-warning")
    labels.append("amstralift-automated")

    # Format PR Body with Cohesive Batches
    body_lines = [
        f"## 🚀 AMstraLift Automated Dependency Upgrade ({tier.value})",
        "",
        "### 📦 Proposed Changes:",
    ]
    if batches:
        for batch in batches:
            if len(batches) > 1 or len(batch.changes) > 1:
                body_lines.append(f"#### {batch.display_name}")
            for c in batch.changes:
                body_lines.append(f"- **`{c.package_name}`**: `{c.from_version}` → `{c.to_version}` ({c.tier.value})")
            body_lines.append("")
    else:
        for c in bundle.changes:
            body_lines.append(f"- **`{c.package_name}`**: `{c.from_version}` → `{c.to_version}` ({c.tier.value})")

    body_lines.extend(
        [
            "### 🛡️ Build & Test Evidence (Stage A):",
        ]
    )
    for g in bundle.gate_summary.results:
        status_icon = "✅" if "PASSED" in g.status.value else "❌"
        body_lines.append(
            f"- {status_icon} **{g.name}** (`{g.command}`): `{g.status.value}` (in {g.duration_seconds:.2f}s)"
        )

    body_lines.extend(
        [
            "",
            "### 🔒 Cryptographic Audit Trail:",
            f"- **Run ID:** `{bundle.run_id}`",
            f"- **Base Commit SHA:** `{bundle.base_commit_sha}`",
            f"- **Patch Hash (SHA-256):** `{bundle.patch_sha256}`",
            f"- **Idempotency Key:** `{idempotency_key}`",
            f"- **CI Completion Deadline (24h SLA):** `{deadline_24h.isoformat()}`",
            "",
            "> ⚠️ **MANDATORY POLICY ENFORCEMENT:** Auto-merge is strictly disabled for all tiers. "
            "A human reviewer action is required to approve and merge this pull request.",
        ]
    )

    pr_proposal = PullRequestProposal(
        branch_name=branch_name,
        title=pr_title,
        body="\n".join(body_lines),
        labels=labels,
        deadline_24h=deadline_24h,
        base_commit_sha=bundle.base_commit_sha,
        patch_sha256=bundle.patch_sha256,
        tier=tier,
        idempotency_key=idempotency_key,
        publish_status="LOCAL_ONLY",
    )


    # 9. Duplicate check: Look up existing open PR before making any local or remote modifications
    if provider and effective_repo_id:
        existing_pr = provider.get_open_pr(effective_repo_id, branch_name)
        if existing_pr:
            logger.info("Existing open PR #%d already found for branch %s. Idempotent skip.", existing_pr.number, branch_name)
            pr_proposal.remote_pr_number = existing_pr.number
            pr_proposal.remote_pr_url = existing_pr.url
            pr_proposal.publish_status = "ALREADY_EXISTS"
            return pr_proposal

    # 10. Local branch checkout, patch apply, selective file staging, and commit
    if not dry_run:
        # Check if local branch already exists
        branch_check = run_git(["rev-parse", "--verify", branch_name], cwd=target_repo_path)
        if branch_check.returncode != 0:
            checkout_res = run_git(["checkout", "-b", branch_name, bundle.base_commit_sha], cwd=target_repo_path)
        else:
            checkout_res = run_git(["checkout", branch_name], cwd=target_repo_path)

        if checkout_res.returncode != 0:
            raise StageBPublishError(f"Failed to checkout branch '{branch_name}': {checkout_res.stderr}")

        # Apply patch with checked error
        apply_res = run_git(
            ["apply", "--binary", "--ignore-space-change", "--ignore-whitespace", "-"],
            cwd=target_repo_path,
            input_data=bundle.patch,
        )
        if apply_res.returncode != 0:
            run_git(["checkout", bundle.target_branch], cwd=target_repo_path)
            raise StageBPublishError(f"Failed to apply patch in Stage B: {apply_res.stderr}")

        # Stage ONLY verified patch paths, never git add .
        patch_files = extract_patch_files(bundle.patch)
        if not patch_files:
            run_git(["checkout", bundle.target_branch], cwd=target_repo_path)
            raise StageBPublishError("Patch diff contains no valid target files to stage.")

        for file_path in patch_files:
            add_res = run_git(["add", file_path], cwd=target_repo_path)
            if add_res.returncode != 0:
                run_git(["checkout", bundle.target_branch], cwd=target_repo_path)
                raise StageBPublishError(f"Failed to stage verified patch file '{file_path}': {add_res.stderr}")

        # Commit with checked error
        commit_res = run_git(["commit", "-m", pr_proposal.title], cwd=target_repo_path)
        if commit_res.returncode != 0:
            if "nothing to commit" not in commit_res.stdout:
                run_git(["checkout", bundle.target_branch], cwd=target_repo_path)
                raise StageBPublishError(f"Failed to commit upgrade patch: {commit_res.stderr}")

        # 11. Remote Publishing: Safe Push Recovery & Conflict Avoidance
        if publish and provider and remote_url and effective_repo_id:
            local_head = get_head_commit(target_repo_path)
            remote_branch_sha = provider.get_remote_branch_head(
                target_repo_path, branch_name, remote_url, git_token
            )

            if remote_branch_sha is not None:
                if remote_branch_sha == local_head:
                    logger.info("Remote branch '%s' already matches local HEAD commit %s. Skipping push.", branch_name, local_head)
                else:
                    # CONFLICT! The remote branch exists with a diverged commit. Refuse to force push!
                    raise StageBPublishError(
                        f"Remote branch '{branch_name}' already exists on remote with diverged commit {remote_branch_sha} "
                        f"(local commit is {local_head}). Refusing to force-push. "
                        "Please inspect, merge, or delete the remote branch before re-running."
                    )
            else:
                # Remote branch does not exist yet: push it
                provider.push_branch(target_repo_path, branch_name, remote_url, git_token)

            # Create remote PR with automatic retry and duplicate recovery
            remote_pr = provider.create_pull_request(
                repo_id=effective_repo_id,
                head_branch=branch_name,
                base_branch=bundle.target_branch,
                title=pr_proposal.title,
                body=pr_proposal.body,
                labels=pr_proposal.labels,
            )
            pr_proposal.remote_pr_number = remote_pr.number
            pr_proposal.remote_pr_url = remote_pr.url
            pr_proposal.publish_status = "CREATED"

    return pr_proposal
