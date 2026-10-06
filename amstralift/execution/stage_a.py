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


from typing import Callable, Optional
from amstralift.core.cancellation import CancellationToken, check_cancelled


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
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token: Optional[CancellationToken] = None,
) -> UnsignedAdvisoryBundle:
    """Execute Stage A inside the sanitized workspace."""
    run_id = run_id or f"run_{uuid4().hex[:12]}"
    base_commit_sha = get_head_commit(workspace_path)
    lockfiles_before = _capture_lockfile_hashes(workspace_path)

    def _notify(step: int, total: int, pct: int, msg: str):
        if cancellation_token:
            cancellation_token.check_cancelled()
        print(f"[{step}/{total}] {msg}", flush=True)
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    # 1. Discover upgrade candidates or use explicit changes
    _notify(1, 5, 10, "🔍 Analyzing repository and discovering dependencies...")
    candidates = explicit_changes if explicit_changes is not None else adapter.discover_candidates(workspace_path)
    print(f"   ↳ Discovered {len(candidates)} upgradable dependency candidate(s).", flush=True)

    # 1b. Auto-discover direct and transitive dependencies with CVEs and add safe non-breaking remediation
    if remediate_cves and explicit_changes is None:
        _notify(2, 5, 25, "🛡️ Running pre-upgrade CVE security scan & remediation planning...")
        try:
            from amstralift.core.models import DependencyTier
            from amstralift.security.dependency_graph import DependencyGraphAnalyzer
            from amstralift.security.osv_client import OSVClient
            from amstralift.security.plan_generator import RemediationPlanGenerator

            print(f"   ↳ Analyzing dependency tree across workspace ({adapter.name})...", flush=True)
            all_deps = DependencyGraphAnalyzer.analyze(workspace_path, adapter.name)
            if all_deps:
                print(f"   ↳ Discovered {len(all_deps)} direct & transitive packages.", flush=True)
                scanner = OSVClient()
                audit_report = scanner.scan_discovered_dependencies(
                    all_deps,
                    ecosystem=adapter.name,
                    repo_path=str(workspace_path),
                    progress_callback=lambda msg: print(f"   {msg}", flush=True),
                )
                if audit_report.findings:
                    plan = RemediationPlanGenerator.generate_plan(audit_report)
                    cve_changes = RemediationPlanGenerator.plan_to_dependency_changes(plan)
                    print(f"   ↳ Evaluated vulnerabilities: {len(audit_report.findings)} advisories, {len(cve_changes)} fixes planned.", flush=True)
                    existing_names = {c.package_name for c in candidates}
                    for change in cve_changes:
                        if change.package_name in existing_names:
                            continue
                        if change.tier == DependencyTier.TIER_3_CRITICAL:
                            continue
                        # If a transitive CVE was introduced by any parent package that is already
                        # being upgraded to a new version, skip it: the upgraded parent brings its
                        # own modern, secure dependency tree and stale overrides will cause conflicts.
                        if change.change_type == "transitive":
                            parents = set(change.introduced_by)
                            if change.parent_package:
                                parents.add(change.parent_package)
                            if parents and any(p in existing_names for p in parents):
                                continue
                        # Native binary packages and multi-major routing packages (like path-to-regexp)
                        # break if globally overridden; skip them to maintain install scripts and runtime routing.
                        if (
                            change.package_name in ("esbuild", "path-to-regexp")
                            or change.package_name.startswith(("@esbuild/", "@swc/", "@rollup/"))
                        ):
                            continue
                        candidates.append(change)
                        existing_names.add(change.package_name)
        except Exception as e:
            logger.warning("Optional dependency CVE remediation skipped: %s", e)

    if not candidates:
        raise NoUpgradesAvailableError("Repository is already up to date. No upgradable dependencies discovered.")

    # 2. Apply upgrades to manifests/lockfiles
    _notify(3, 5, 45, "📦 Applying version updates to manifests and regenerating lockfiles...")
    adapter.apply_upgrade(workspace_path, candidates)

    # 2a. Post-upgrade transitive CVE remediation pass
    _notify(4, 5, 65, "🛡️ Running post-upgrade CVE checks and framework modernizations...")
    if remediate_cves and explicit_changes is None:
        try:
            from amstralift.security.dependency_graph import DependencyGraphAnalyzer
            from amstralift.security.osv_client import OSVClient
            from amstralift.security.plan_generator import RemediationPlanGenerator

            post_deps = DependencyGraphAnalyzer.analyze(workspace_path, adapter.name)
            if post_deps:
                scanner = OSVClient()
                post_report = scanner.scan_discovered_dependencies(
                    post_deps,
                    ecosystem=adapter.name,
                    repo_path=str(workspace_path),
                )
                if post_report.findings:
                    post_plan = RemediationPlanGenerator.generate_plan(post_report)
                    post_cve_changes = RemediationPlanGenerator.plan_to_dependency_changes(post_plan)
                    existing_names = {c.package_name for c in candidates}
                    clean_post_changes = []
                    for change in post_cve_changes:
                        if change.package_name in existing_names:
                            continue
                        if change.tier == DependencyTier.TIER_3_CRITICAL:
                            continue
                        if change.change_type == "transitive":
                            parents = set(change.introduced_by)
                            if change.parent_package:
                                parents.add(change.parent_package)
                            if parents and any(p in existing_names for p in parents):
                                continue
                        if change.package_name == "esbuild" or change.package_name.startswith(("@esbuild/", "@swc/", "@rollup/")):
                            continue
                        clean_post_changes.append(change)
                        existing_names.add(change.package_name)

                    if clean_post_changes:
                        adapter.apply_upgrade(workspace_path, clean_post_changes)
                        candidates.extend(clean_post_changes)
        except Exception as e:
            logger.warning("Post-upgrade CVE remediation pass skipped: %s", e)

    # 2b. Apply optional ecosystem modernizations (e.g. control-flow, standalone)
    applied_modernizations: list[str] = []
    if modernize:
        applied_modernizations = adapter.apply_modernizations(workspace_path, modernize)

    # 3. Run declared build and test gates
    _notify(5, 5, 80, "⚡ Executing build and test verification gates...")
    def _gate_progress(msg: str):
        if progress_callback:
            try:
                progress_callback(85, msg)
            except Exception:
                pass

    try:
        gate_summary = adapter.run_build_and_tests(
            workspace_path,
            timeout_seconds=test_timeout,
            progress_callback=_gate_progress,
        )
    except TypeError:
        gate_summary = adapter.run_build_and_tests(workspace_path, timeout_seconds=test_timeout)

    # 4. Generate unified git patch
    if progress_callback:
        try:
            progress_callback(95, "🔒 Generating cryptographic patch & classifying migration safety...")
        except Exception:
            pass
    print("🔒 Generating cryptographic patch & classifying migration safety...", flush=True)
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
