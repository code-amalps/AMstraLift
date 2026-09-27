"""Stage B: Trusted publisher environment.

Operates with minimal attack surface, holds PR publishing credentials,
and NEVER executes repository-controlled code or scripts.

Validates bundle authenticity, expiration, branch head concurrency,
path allowlists, and required gate passes before opening any PR.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from amstralift.core.crypto import verify_bundle
from amstralift.core.models import PullRequestProposal, SignedAdvisoryBundle
from amstralift.core.security import validate_patch_security
from amstralift.core.workspace import get_head_commit, run_git, verify_rediff_integrity


class StageBPublishError(Exception):
    """Raised when Stage B validation or publishing fails."""

    pass


def run_stage_b(
    signed_bundle: SignedAdvisoryBundle,
    target_repo_path: Path,
    secret_key: bytes,
    dry_run: bool = False,
) -> PullRequestProposal:
    """Validate signed advisory bundle and create a reviewable pull request proposal."""
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

    # 6. Construct Branch Name and PR Metadata
    tier = bundle.highest_tier
    primary_pkg = bundle.changes[0].package_name if bundle.changes else "dependencies"
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    branch_name = f"amstralift/{primary_pkg.replace('/', '-')}-{timestamp_slug}"

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
            f"- **CI Completion Deadline (24h SLA):** `{deadline_24h.isoformat()}`",
            "",
            "> ⚠️ **MANDATORY POLICY ENFORCEMENT:** Auto-merge is strictly disabled for all tiers. "
            "A human reviewer action is required to approve and merge this pull request.",
        ]
    )

    pr_proposal = PullRequestProposal(
        branch_name=branch_name,
        title=f"chore(deps): upgrade {primary_pkg} ({tier.value})",
        body="\n".join(body_lines),
        labels=labels,
        deadline_24h=deadline_24h,
        base_commit_sha=bundle.base_commit_sha,
        patch_sha256=bundle.patch_sha256,
        tier=tier,
    )

    # 7. Apply patch to branch if not dry-run
    if not dry_run:
        # Create and checkout upgrade branch
        run_git(["checkout", "-b", branch_name], cwd=target_repo_path)
        # Apply patch with input_data and --binary
        apply_res = run_git(
            ["apply", "--binary", "--ignore-space-change", "--ignore-whitespace", "-"],
            cwd=target_repo_path,
            input_data=bundle.patch,
        )
        if apply_res.returncode != 0:
            raise StageBPublishError(f"Failed to apply patch in Stage B: {apply_res.stderr}")
        # Stage all changes
        run_git(["add", "."], cwd=target_repo_path)
        run_git(["commit", "-m", f"chore(deps): {pr_proposal.title}"], cwd=target_repo_path)

    return pr_proposal
