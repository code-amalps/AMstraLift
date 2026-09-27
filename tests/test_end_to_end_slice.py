"""End-to-End integration and trust boundary tests for the Phase 1 Angular vertical slice."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from amstralift.adapters.angular import AngularAdapter
from amstralift.core.models import DependencyTier
from amstralift.core.workspace import get_head_commit, run_git
from amstralift.execution.stage_b import StageBPublishError
from amstralift.service import UpgradeOrchestrator


def init_mock_angular_repo(repo_path: Path, test_exit_code: int = 0) -> str:
    """Initialize a git repo with a mock Angular application."""
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=repo_path)
    run_git(["config", "user.name", "Test User"], cwd=repo_path)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_path)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_path)

    # Use a cross-platform command that succeeds/fails according to test_exit_code
    if test_exit_code == 0:
        test_cmd = 'python -c "import sys; sys.exit(0)"'
        build_cmd = 'python -c "import sys; sys.exit(0)"'
    else:
        test_cmd = 'python -c "import sys; sys.exit(1)"'
        build_cmd = 'python -c "import sys; sys.exit(0)"'

    pkg_data = {
        "name": "mock-angular-app",
        "version": "1.0.0",
        "scripts": {
            "test": test_cmd,
            "build": build_cmd,
        },
        "dependencies": {
            "@angular/core": "^17.0.0",
            "@angular/router": "^17.0.0",
            "tslib": "^2.6.0",
        },
        "devDependencies": {
            "typescript": "^5.2.0",
        },
    }

    lock_data = {
        "name": "mock-angular-app",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "mock-angular-app"},
            "node_modules/@angular/core": {"version": "17.0.0"},
            "node_modules/@angular/router": {"version": "17.0.0"},
        },
    }

    (repo_path / "package.json").write_text(json.dumps(pkg_data, indent=2) + "\n", encoding="utf-8")
    (repo_path / "package-lock.json").write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
    (repo_path / "angular.json").write_text('{"version": 1, "projects": {}}\n', encoding="utf-8")

    src_dir = repo_path / "src" / "app"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.component.ts").write_text("export class AppComponent { title = 'mock'; }\n", encoding="utf-8")

    run_git(["add", "."], cwd=repo_path)
    run_git(["commit", "-m", "Initial Angular baseline"], cwd=repo_path)
    return get_head_commit(repo_path)


def test_angular_end_to_end_success(tmp_path: Path):
    """Verify that a healthy Angular repo produces a signed bundle and reviewable PR."""
    repo_path = tmp_path / "angular-repo"
    base_sha = init_mock_angular_repo(repo_path, test_exit_code=0)

    secret = b"test-orchestrator-secret-key-32b"
    orchestrator = UpgradeOrchestrator(secret_key=secret)

    # Mock npm registry responses so test runs hermetically without network
    def mock_fetch(pkg: str, target_major: int | None = None, **kwargs):
        versions = {
            "@angular/core": "18.2.0",
            "@angular/router": "18.2.0",
            "tslib": "2.7.0",
            "typescript": "5.5.0",
        }
        return versions.get(pkg)

    with patch.object(AngularAdapter, "fetch_latest_version", side_effect=mock_fetch):
        signed_bundle, pr_proposal = orchestrator.run_upgrade(
            repo_path=repo_path,
            ecosystem="angular",
            target_branch="main",
            dry_run=False,
        )

    # 1. Verify Signed Bundle Properties
    bundle = signed_bundle.bundle
    assert bundle.base_commit_sha == base_sha
    assert bundle.highest_tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert bundle.gate_summary.all_required_passed
    assert not signed_bundle.is_expired()
    assert signed_bundle.signature.startswith("hmac-sha256:")

    # 2. Verify Pull Request Proposal
    assert pr_proposal.tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert "tier-2-verify-behavior" in pr_proposal.labels
    assert "requires-functional-qa" in pr_proposal.labels
    assert "amstralift-automated" in pr_proposal.labels
    assert "MANDATORY POLICY ENFORCEMENT" in pr_proposal.body
    assert pr_proposal.base_commit_sha == base_sha

    # 3. Verify Repository Working Tree State
    # A branch was created and committed to
    current_head = get_head_commit(repo_path)
    assert current_head != base_sha

    updated_pkg = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
    assert updated_pkg["dependencies"]["@angular/core"] == "^18.2.0"
    assert updated_pkg["dependencies"]["@angular/router"] == "^18.2.0"


def test_angular_end_to_end_gate_failure_aborts_pr(tmp_path: Path):
    """Verify that failing required tests in Stage A stops Stage B from opening a PR."""
    repo_path = tmp_path / "failing-repo"
    init_mock_angular_repo(repo_path, test_exit_code=1)

    orchestrator = UpgradeOrchestrator(secret_key=b"secret-key")

    with patch.object(AngularAdapter, "fetch_latest_version", return_value="18.2.0"):
        with pytest.raises(StageBPublishError, match="Cannot open PR: Not all required build/test gates passed"):
            orchestrator.run_upgrade(
                repo_path=repo_path,
                ecosystem="angular",
                target_branch="main",
                dry_run=False,
            )


def test_angular_end_to_end_stale_base_aborts_pr(tmp_path: Path):
    """Verify Stage B rejects bundle if target branch HEAD moved during Stage A execution."""
    repo_path = tmp_path / "stale-race-repo"
    base_sha = init_mock_angular_repo(repo_path, test_exit_code=0)

    secret = b"test-orchestrator-secret-key-32b"
    orchestrator = UpgradeOrchestrator(secret_key=secret)

    with patch.object(AngularAdapter, "fetch_latest_version", return_value="18.2.0"):
        # We manually simulate a race condition where repo advances HEAD before Stage B
        sandbox_dir = tmp_path / "temp_sandbox"
        from amstralift.core.crypto import sign_bundle
        from amstralift.core.workspace import prepare_stage_a_workspace
        from amstralift.execution.stage_a import run_stage_a
        from amstralift.execution.stage_b import run_stage_b

        prepare_stage_a_workspace(repo_path, sandbox_dir)
        unsigned = run_stage_a(sandbox_dir, orchestrator.get_adapter("angular"), str(repo_path))
        signed = sign_bundle(unsigned, secret)

        # Race: repo gets a new commit on main!
        (repo_path / "hotfix.txt").write_text("critical hotfix", encoding="utf-8")
        run_git(["add", "hotfix.txt"], cwd=repo_path)
        run_git(["commit", "-m", "Unrelated hotfix on main"], cwd=repo_path)

        new_head = get_head_commit(repo_path)
        assert new_head != base_sha

        # Stage B must reject the bundle as stale!
        with pytest.raises(StageBPublishError, match="Stale base commit"):
            run_stage_b(signed, target_repo_path=repo_path, secret_key=secret)
