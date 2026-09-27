"""Stage B: Trusted publisher environment.

Operates with minimal attack surface, holds PR publishing credentials,
and NEVER executes repository-controlled code or scripts.

Validates bundle authenticity, expiration, branch head concurrency,
path allowlists, and required gate passes before opening any PR.
Provides idempotent remote PR publishing and duplicate detection.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amstralift.core.crypto import verify_bundle
from amstralift.core.models import PullRequestProposal, SignedAdvisoryBundle
from amstralift.core.security import validate_patch_security
from amstralift.core.workspace import get_head_commit, run_git, verify_rediff_integrity
from amstralift.publisher.base import (
    BaseGitProvider,
    calculate_idempotency_key,
    derive_deterministic_branch_name,
)

logger = logging.getLogger(__name__)


class StageBPublishError(Exception):
    """Raised when Stage B validation or publishing fails."""

    pass


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
) -> PullRequestProposal:
    """Validate signed advisory bundle, handle duplicate checks, and create or open a PR."""
    bundle = signed_bundle.bundle

    # 1. Cryptographic signature and TTL verification
    verify_bundle(signed_bundle, secret_key)

    # 2. Strict path allowlists and security rejections
    validate_patch_security(bundle.patch)

    # 3. Branch head concurrency check (base commit must match current HEAD)
    current_head = get_head_commit(target_repo_path)
    if current_head != bundle.base_commit_sha:
        raise StageBPublishError(
            f"Stale base commit: repo HEAD is {current_head}, bundle was built against {bundle.base_commit_sha}. "
            "Rebase-and-rerun required."
        )

    # 4. Required gates verification (Section 1: never open a PR if required checks failed)
    if not bundle.gate_summary.all_required_passed:
        raise StageBPublishError(
            "Cannot open PR: Not all required build/test gates passed in Stage A. "
            "Per Section 1 and 3, AMstraLift fails closed with an issue, never a PR."
        )

    # 5. Re-diff and clean application check
    verify_rediff_integrity(
        target_repo_path,
        bundle.base_commit_sha,
        bundle.patch,
        bundle.patch_sha256,
    )

    # 6. Construct Deterministic Branch Name and Idempotency Key
    tier = bundle.highest_tier
    primary_pkg = bundle.changes[0].package_name if bundle.changes else "dependencies"
    target_ver = bundle.changes[0].to_version if bundle.changes else "update"
    branch_name = derive_deterministic_branch_name(primary_pkg, target_ver)

    effective_repo_id = repo_id or target_repo_path.name
    idempotency_key = calculate_idempotency_key(
        repo_id=effective_repo_id,
        target_branch=bundle.target_branch,
        changes=bundle.changes,
        base_commit_sha=bundle.base_commit_sha,
    )

    now = datetime.now(timezone.utc)
    deadline_24h = now + timedelta(hours=24)

    # Compose PR labels based on tier and migration impact
    labels = list(tier.labels)
    if bundle.migration.application_source_modified:
        labels.append("migration-manual-review-required")
    labels.append("amstralift-automated")

    # Format PR Body
    body_lines = [
        f"## 🚀 AMstraLift Automated Dependency Upgrade ({tier.value})",
        "",
        "### 📦 Proposed Changes:",
    ]
    for c in bundle.changes:
        body_lines.append(f"- **`{c.package_name}`**: `{c.from_version}` → `{c.to_version}` ({c.tier.value})")

    body_lines.extend(
        [
            "",
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
        title=f"chore(deps): upgrade {primary_pkg} to {target_ver} ({tier.value})",
        body="\n".join(body_lines),
        labels=labels,
        deadline_24h=deadline_24h,
        base_commit_sha=bundle.base_commit_sha,
        patch_sha256=bundle.patch_sha256,
        tier=tier,
        idempotency_key=idempotency_key,
        publish_status="LOCAL_ONLY",
    )

    # 7. Check for existing open duplicate PR before any remote or local branch work
    if provider and effective_repo_id:
        existing_pr = provider.get_open_pr(effective_repo_id, branch_name)
        if existing_pr:
            logger.info("Existing open PR #%d already found for branch %s. Idempotent skip.", existing_pr.number, branch_name)
            pr_proposal.remote_pr_number = existing_pr.number
            pr_proposal.remote_pr_url = existing_pr.url
            pr_proposal.publish_status = "ALREADY_EXISTS"
            return pr_proposal

    # 8. Apply patch to local branch if not dry-run
    if not dry_run:
        # Check if local branch already exists
        branch_exists = run_git(["rev-parse", "--verify", branch_name], cwd=target_repo_path).returncode == 0
        if not branch_exists:
            run_git(["checkout", "-b", branch_name], cwd=target_repo_path)
        else:
            run_git(["checkout", branch_name], cwd=target_repo_path)

        # Apply patch if changes not already committed
        apply_res = run_git(
            ["apply", "--binary", "--ignore-space-change", "--ignore-whitespace", "-"],
            cwd=target_repo_path,
            input_data=bundle.patch,
        )
        if apply_res.returncode == 0:
            run_git(["add", "."], cwd=target_repo_path)
            run_git(["commit", "-m", f"chore(deps): {pr_proposal.title}"], cwd=target_repo_path)

        # 9. Remote Publishing (if enabled, provider provided, and remote_url configured)
        if publish and provider and remote_url and effective_repo_id:
            # Check if remote branch already exists and matches current HEAD (push retry recovery)
            if not provider.remote_branch_matches_head(target_repo_path, branch_name, remote_url, git_token):
                provider.push_branch(target_repo_path, branch_name, remote_url, git_token)

            # Create the remote PR
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
