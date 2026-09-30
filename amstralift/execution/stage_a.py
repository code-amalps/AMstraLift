"""Stage A: Untrusted execution environment.

Runs in an isolated, sanitized workspace with NO write credentials.
Resolves dependencies, runs builds/tests, generates patch diff,
and returns an UnsignedAdvisoryBundle.

CRITICAL: Stage A never possesses the HMAC signing key.
"""

import logging
from pathlib import Path
from uuid import uuid4

from amstralift.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)
from amstralift.core.crypto import compute_file_sha256, compute_sha256
from amstralift.core.models import DependencyChange, UnsignedAdvisoryBundle
from amstralift.core.security import classify_migration_diff, validate_patch_security
from amstralift.core.workspace import generate_patch, get_head_commit


class StageAExecutionError(Exception):
    """Raised when Stage A execution fails."""

    pass


class NoUpgradesAvailableError(StageAExecutionError):
    """Raised when repository dependencies are already at their latest versions."""

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
    explicit_changes: list[DependencyChange] | None = None,
    test_timeout: float = 300.0,
    modernize: list[str] | None = None,
    remediate_cves: bool = True,
) -> UnsignedAdvisoryBundle:
    """Execute Stage A inside the sanitized workspace."""
    run_id = run_id or f"run_{uuid4().hex[:12]}"
    base_commit_sha = get_head_commit(workspace_path)
    lockfiles_before = _capture_lockfile_hashes(workspace_path)

    # 1. Discover upgrade candidates or use explicit changes
    candidates = explicit_changes if explicit_changes is not None else adapter.discover_candidates(workspace_path)

    # 1b. Auto-discover direct dependencies with CVEs and add safe non-breaking remediation
    if remediate_cves and explicit_changes is None:
        try:
            from amstralift.core.models import DependencyTier
            from amstralift.security.dependency_graph import DependencyGraphAnalyzer
            from amstralift.security.osv_client import OSVClient
            from amstralift.security.plan_generator import RemediationPlanGenerator

            direct_deps = DependencyGraphAnalyzer.analyze(workspace_path, adapter.name)
            direct_only = [d for d in direct_deps if d.is_direct]
            if direct_only:
                scanner = OSVClient()
                audit_report = scanner.scan_discovered_dependencies(
                    direct_only,
                    ecosystem=adapter.name,
                    repo_path=str(workspace_path),
                )
                if audit_report.findings:
                    plan = RemediationPlanGenerator.generate_plan(audit_report)
                    cve_changes = RemediationPlanGenerator.plan_to_dependency_changes(plan)
                    existing_names = {c.package_name for c in candidates}
                    for change in cve_changes:
                        if change.package_name not in existing_names and change.tier != DependencyTier.TIER_3_CRITICAL:
                            candidates.append(change)
                            existing_names.add(change.package_name)
        except Exception as e:
            logger.warning("Optional direct dependency CVE remediation skipped: %s", e)

    if not candidates:
        raise NoUpgradesAvailableError("Repository is already up to date. No upgradable dependencies discovered.")

    # 2. Apply upgrades to manifests/lockfiles
    adapter.apply_upgrade(workspace_path, candidates)

    # 2b. Apply optional ecosystem modernizations (e.g. control-flow, standalone)
    applied_modernizations: list[str] = []
    if modernize:
        applied_modernizations = adapter.apply_modernizations(workspace_path, modernize)

    # 3. Run declared build and test gates
    gate_summary = adapter.run_build_and_tests(workspace_path, timeout_seconds=test_timeout)

    # 4. Generate unified git patch
    patch = generate_patch(workspace_path, base_commit_sha)
    if not (patch and patch.strip()):
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
        modernizations=applied_modernizations,
        advisory_notes=[
            f"Ecosystem: {adapter.name}",
            f"Touched files: {len(touched_paths)}",
            f"Migration risk: {migration.risk_level}",
        ],
    )
