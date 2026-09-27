"""Stage A: Untrusted execution environment.

Runs in an isolated, sanitized workspace with NO write credentials.
Resolves dependencies, runs builds/tests, generates patch diff,
and returns an UnsignedAdvisoryBundle.

CRITICAL: Stage A never possesses the HMAC signing key.
"""

from pathlib import Path
from uuid import uuid4

from amstralift.adapters.base import BaseAdapter
from amstralift.core.crypto import compute_file_sha256, compute_sha256
from amstralift.core.models import UnsignedAdvisoryBundle
from amstralift.core.security import classify_migration_diff, validate_patch_security
from amstralift.core.workspace import generate_patch, get_head_commit


class StageAExecutionError(Exception):
    """Raised when Stage A execution fails."""

    pass


def _capture_lockfile_hashes(repo_path: Path) -> dict[str, str]:
    """Capture SHA-256 hashes of all lockfiles in the repository."""
    lockfiles = (
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "packages.lock.json",
    )
    hashes: dict[str, str] = {}
    for name in lockfiles:
        p = repo_path / name
        if p.exists() and p.is_file():
            hashes[name] = compute_file_sha256(str(p))
    return hashes


def run_stage_a(
    workspace_path: Path,
    adapter: BaseAdapter,
    repo_url: str,
    target_branch: str = "main",
    run_id: str | None = None,
) -> UnsignedAdvisoryBundle:
    """Execute Stage A inside the sanitized workspace."""
    run_id = run_id or f"run_{uuid4().hex[:12]}"
    base_commit_sha = get_head_commit(workspace_path)
    lockfiles_before = _capture_lockfile_hashes(workspace_path)

    # 1. Discover upgrade candidates
    candidates = adapter.discover_candidates(workspace_path)
    if not candidates:
        raise StageAExecutionError("No upgradable dependencies discovered by adapter.")

    # 2. Apply upgrades to manifests/lockfiles
    adapter.apply_upgrade(workspace_path, candidates)

    # 3. Run declared build and test gates
    gate_summary = adapter.run_build_and_tests(workspace_path)

    # 4. Generate unified git patch
    patch = generate_patch(workspace_path, base_commit_sha)
    if not patch.strip():
        raise StageAExecutionError("Upgrade resulted in an empty patch.")

    patch_sha256 = compute_sha256(patch)
    lockfiles_after = _capture_lockfile_hashes(workspace_path)

    # 5. Security & Migration diff classification
    touched_paths = validate_patch_security(patch)
    migration = classify_migration_diff(touched_paths, ecosystem=adapter.name)

    head_commit_sha = base_commit_sha  # Working tree is dirty; HEAD remains base until Stage B commits

    return UnsignedAdvisoryBundle(
        run_id=run_id,
        repo_url=repo_url,
        target_branch=target_branch,
        base_commit_sha=base_commit_sha,
        head_commit_sha=head_commit_sha,
        patch=patch,
        patch_sha256=patch_sha256,
        lockfile_hashes_before=lockfiles_before,
        lockfile_hashes_after=lockfiles_after,
        changes=candidates,
        gate_summary=gate_summary,
        migration=migration,
        advisory_notes=[
            f"Ecosystem: {adapter.name}",
            f"Touched files: {len(touched_paths)}",
            f"Migration risk: {migration.risk_level}",
        ],
    )
