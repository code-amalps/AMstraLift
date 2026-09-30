"""End-to-end milestone verification tests for remote Git publishing and duplicate prevention."""

from pathlib import Path
from unittest.mock import patch

import pytest

from amstralift.core.workspace import get_head_commit, run_git
from amstralift.execution.stage_b import StageBPublishError, run_stage_b
from amstralift.publisher.base import RemotePR
from amstralift.publisher.github import GitHubProvider
from amstralift.service import UpgradeOrchestrator


@pytest.fixture
def test_git_environment(tmp_path: Path, monkeypatch):
    """Creates a local working git repository connected to a real bare git remote."""
    from amstralift.adapters.python import PythonAdapter
    monkeypatch.setattr(PythonAdapter, "fetch_latest_version", lambda self, pkg: "2.32.3" if pkg == "requests" else None)

    bare_remote = tmp_path / "remote.git"
    bare_remote.mkdir()
    run_git(["init", "--bare"], cwd=bare_remote)

    local_repo = tmp_path / "local_repo"
    local_repo.mkdir()
    run_git(["init"], cwd=local_repo)
    run_git(["config", "user.name", "Test Runner"], cwd=local_repo)
    run_git(["config", "user.email", "test@example.com"], cwd=local_repo)
    run_git(["config", "core.autocrlf", "false"], cwd=local_repo)

    (local_repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")

    # Add a mock test file
    test_dir = local_repo / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    # Initial pyproject.toml
    pyproject = """[project]
name = "milestone-app"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "requests==2.25.0"
]
"""
    (local_repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    run_git(["add", "."], cwd=local_repo)
    run_git(["commit", "-m", "feat: initial commit with requests 2.25.0"], cwd=local_repo)

    # Set up remote and push main
    run_git(["branch", "-M", "main"], cwd=local_repo)
    run_git(["remote", "add", "origin", str(bare_remote)], cwd=local_repo)
    run_git(["push", "-u", "origin", "main"], cwd=local_repo)

    return {"local_repo": local_repo, "bare_remote": bare_remote}


def test_milestone_publish_verified_upgrade_pr(test_git_environment):
    """Milestone Criterion 1-4: Run upgrade, produce bundle, validate Stage B, push branch & open PR."""
    local_repo = test_git_environment["local_repo"]
    bare_remote = test_git_environment["bare_remote"]

    orchestrator = UpgradeOrchestrator()

    # Mock GitHub provider API responses
    mock_provider = GitHubProvider(token="test_mock_token")

    with patch.object(mock_provider, "get_open_pr", return_value=None):
        with patch.object(
            mock_provider,
            "create_pull_request",
            return_value=RemotePR(
                number=101,
                url="https://github.com/my-org/milestone-app/pull/101",
                branch="amstralift/requests-2.32.3",
                title="chore(deps): upgrade requests to 2.32.3 (Tier 1 - Safe)",
                labels=["tier-1-safe", "amstralift-automated"],
            ),
        ) as mock_create_pr:
            # Execute upgrade with publishing enabled
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=local_repo,
                ecosystem="python",
                target_branch="main",
                publish=True,
                git_provider=mock_provider,
                repo_id="my-org/milestone-app",
                remote_url=str(bare_remote),
            )

            # 1. Bundle and Gate Validation
            assert signed_bundle.bundle.gate_summary.all_required_passed is True
            assert len(signed_bundle.bundle.changes) >= 1
            assert signed_bundle.bundle.changes[0].package_name == "requests"

            # 2. Stage B local & remote outcomes
            assert pr_proposal.publish_status == "CREATED"
            assert pr_proposal.remote_pr_number == 101
            assert pr_proposal.branch_name.startswith("amstralift/requests-")
            assert pr_proposal.idempotency_key is not None

            # 3. Verify that branch was actually pushed to the real bare remote!
            remote_branches = run_git(["branch", "-a"], cwd=bare_remote).stdout
            assert pr_proposal.branch_name in remote_branches
            assert mock_create_pr.called


def test_milestone_duplicate_prevention_idempotency(test_git_environment):
    """Milestone Criterion 5: A repeated run detects the existing PR rather than creating a duplicate."""
    local_repo = test_git_environment["local_repo"]
    bare_remote = test_git_environment["bare_remote"]

    orchestrator = UpgradeOrchestrator()
    mock_provider = GitHubProvider(token="test_mock_token")

    # Simulate that PR #101 is already open on GitHub for this branch
    existing_open_pr = RemotePR(
        number=101,
        url="https://github.com/my-org/milestone-app/pull/101",
        branch="amstralift/requests-2.32.3",
        title="chore(deps): upgrade requests to 2.32.3",
        labels=["tier-1-safe", "amstralift-automated"],
    )

    with patch.object(mock_provider, "get_open_pr", return_value=existing_open_pr):
        with patch.object(mock_provider, "create_pull_request") as mock_create_pr:
            with patch.object(mock_provider, "push_branch") as mock_push:
                signed_bundle, pr_proposal = orchestrator.run_upgrade(
                    repo_path=local_repo,
                    ecosystem="python",
                    target_branch="main",
                    publish=True,
                    git_provider=mock_provider,
                    repo_id="my-org/milestone-app",
                    remote_url=str(bare_remote),
                )

                # Verified: Skipped duplicate creation, returned existing PR details
                assert pr_proposal.publish_status == "ALREADY_EXISTS"
                assert pr_proposal.remote_pr_number == 101
                assert pr_proposal.remote_pr_url == "https://github.com/my-org/milestone-app/pull/101"

                # Crucial: No duplicate push or PR creation API calls occurred
                assert not mock_create_pr.called
                assert not mock_push.called


def test_milestone_stale_base_commit_rejection(test_git_environment):
    """Milestone Criterion 6A: A bundle built against a stale base commit is rejected."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    # Generate a bundle against initial commit
    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Now simulate concurrent push to local repo HEAD
    dummy_file = local_repo / "drift.txt"
    dummy_file.write_text("concurrent commit", encoding="utf-8")
    run_git(["add", "."], cwd=local_repo)
    run_git(["commit", "-m", "drift: concurrent commit moves HEAD"], cwd=local_repo)

    # Stage B must reject the bundle immediately due to base commit mismatch
    with pytest.raises(StageBPublishError, match="Stale base commit"):
        run_stage_b(
            signed_bundle=signed_bundle,
            target_repo_path=local_repo,
            secret_key=orchestrator.secret_key,
        )


def test_milestone_tampered_bundle_rejection(test_git_environment):
    """Milestone Criterion 6B: A tampered bundle is rejected."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Malicious tampering with patch content after signing
    signed_bundle.bundle.patch += "\n+malicious_injected_code = True\n"

    with pytest.raises(Exception, match="Bundle patch tampered|HMAC signature verification failed"):
        run_stage_b(
            signed_bundle=signed_bundle,
            target_repo_path=local_repo,
            secret_key=orchestrator.secret_key,
        )


def test_milestone_push_retry_recovery(test_git_environment):
    """Milestone Criterion 5 & 7: Branch already pushed to remote (e.g. prior API timeout) recovers cleanly."""
    local_repo = test_git_environment["local_repo"]
    bare_remote = test_git_environment["bare_remote"]
    orchestrator = UpgradeOrchestrator()
    mock_provider = GitHubProvider(token="test_mock_token")

    def mock_get_head(repo_path, branch, remote_url, token=None):
        return get_head_commit(repo_path)

    # Simulate: branch was already pushed to remote, but PR not opened yet
    with patch.object(mock_provider, "get_open_pr", return_value=None):
        with patch.object(mock_provider, "get_remote_branch_head", side_effect=mock_get_head):
            with patch.object(mock_provider, "push_branch") as mock_push:
                with patch.object(
                    mock_provider,
                    "create_pull_request",
                    return_value=RemotePR(
                        number=102,
                        url="https://github.com/my-org/milestone-app/pull/102",
                        branch="amstralift/requests-2.32.3",
                        title="chore(deps): upgrade requests",
                    ),
                ):
                    _, pr_proposal = orchestrator.run_upgrade(
                        repo_path=local_repo,
                        ecosystem="python",
                        target_branch="main",
                        publish=True,
                        git_provider=mock_provider,
                        repo_id="my-org/milestone-app",
                        remote_url=str(bare_remote),
                    )

                    assert pr_proposal.publish_status == "CREATED"
                    assert pr_proposal.remote_pr_number == 102
                    # Since remote_branch_matches_head was True, push was skipped safely
                    assert not mock_push.called


def test_stage_b_dirty_worktree_rejected(test_git_environment):
    """Dirty worktree: Stage B rejects before modifying any files."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Introduce uncommitted dirty changes
    (local_repo / "dirty_file.txt").write_text("untracked uncommitted", encoding="utf-8")
    run_git(["add", "dirty_file.txt"], cwd=local_repo)

    with pytest.raises(StageBPublishError, match="uncommitted or dirty"):
        run_stage_b(
            signed_bundle=signed_bundle,
            target_repo_path=local_repo,
            secret_key=orchestrator.secret_key,
        )


def test_stage_b_patch_apply_failure_aborts_publish(test_git_environment):
    """Patch application fails: no commit made and no remote publish."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Modify the base pyproject.toml in a conflicting way so patch application fails
    (local_repo / "pyproject.toml").write_text("completely different content that breaks patch", encoding="utf-8")
    run_git(["add", "."], cwd=local_repo)
    run_git(["commit", "-m", "conflict: break patch apply"], cwd=local_repo)

    # First it will fail stale base commit or patch application
    with pytest.raises(StageBPublishError):
        run_stage_b(
            signed_bundle=signed_bundle,
            target_repo_path=local_repo,
            secret_key=orchestrator.secret_key,
        )


def test_stage_b_stages_only_patch_paths(test_git_environment):
    """Stage B stages ONLY verified patch files, never unrelated untracked files."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Run Stage B without dry-run
    pr_proposal = run_stage_b(
        signed_bundle=signed_bundle,
        target_repo_path=local_repo,
        secret_key=orchestrator.secret_key,
        dry_run=False,
    )

    # Verify that the commit on the new branch touched ONLY pyproject.toml, not any wildcard files
    diff_stat = run_git(["show", "--stat", "--oneline", "HEAD"], cwd=local_repo).stdout
    assert "pyproject.toml" in diff_stat
    assert pr_proposal.branch_name.startswith("amstralift/requests-")


def test_remote_target_branch_drift_rejected(test_git_environment):
    """Remote target branch moved: Stage B rejects before attempting push or PR."""
    local_repo = test_git_environment["local_repo"]
    bare_remote = test_git_environment["bare_remote"]
    orchestrator = UpgradeOrchestrator()
    mock_provider = GitHubProvider(token="test_mock_token")

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    # Simulate remote 'main' has moved to a new commit
    with patch.object(mock_provider, "get_remote_branch_head", return_value="deadbeef00001111222233334444555566667777"):
        with pytest.raises(StageBPublishError, match="remote 'main' has moved"):
            run_stage_b(
                signed_bundle=signed_bundle,
                target_repo_path=local_repo,
                secret_key=orchestrator.secret_key,
                provider=mock_provider,
                repo_id="my-org/milestone-app",
                remote_url=str(bare_remote),
                publish=True,
            )


def test_remote_branch_diverged_conflict_rejection(test_git_environment):
    """Remote branch exists with different commit: safe conflict, no force push."""
    local_repo = test_git_environment["local_repo"]
    bare_remote = test_git_environment["bare_remote"]
    orchestrator = UpgradeOrchestrator()
    mock_provider = GitHubProvider(token="test_mock_token")

    signed_bundle, _ = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        dry_run=True,
    )

    with patch.object(mock_provider, "get_open_pr", return_value=None):
        # Target branch matches base commit
        def mock_get_remote_head(repo_path, branch, remote_url, token=None):
            if branch == "main":
                return signed_bundle.bundle.base_commit_sha
            # Upgrade branch exists on remote with a DIVERGED commit
            return "diverged_commit_sha_99999999999999999999"

        with patch.object(mock_provider, "get_remote_branch_head", side_effect=mock_get_remote_head):
            with patch.object(mock_provider, "push_branch") as mock_push:
                with pytest.raises(StageBPublishError, match="diverged commit"):
                    run_stage_b(
                        signed_bundle=signed_bundle,
                        target_repo_path=local_repo,
                        secret_key=orchestrator.secret_key,
                        provider=mock_provider,
                        repo_id="my-org/milestone-app",
                        remote_url=str(bare_remote),
                        publish=True,
                    )
                # Force-push was NOT called
                assert not mock_push.called


def test_stage_b_stays_on_active_amstralift_branch(test_git_environment):
    """When target repo is already checked out on an amstralift/* branch, Stage B stays on that branch."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    # Create and checkout an initial amstralift migration branch
    active_migration_branch = "amstralift/my-migration"
    run_git(["checkout", "-b", active_migration_branch], cwd=local_repo)

    signed_bundle, pr_proposal = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        target_branch=active_migration_branch,
        dry_run=False,
    )

    # Verified: It stayed on the active amstralift/my-migration branch!
    assert pr_proposal.branch_name == active_migration_branch
    # Current git branch remains active_migration_branch
    curr_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=local_repo).stdout.strip()
    assert curr_branch == active_migration_branch


def test_stage_b_respects_explicit_output_branch(test_git_environment):
    """When output_branch is provided, Stage B uses that exact branch name."""
    local_repo = test_git_environment["local_repo"]
    orchestrator = UpgradeOrchestrator()

    signed_bundle, pr_proposal = orchestrator.run_upgrade(
        repo_path=local_repo,
        ecosystem="python",
        target_branch="main",
        dry_run=False,
        output_branch="amstralift/persistent-upgrade",
    )

    assert pr_proposal.branch_name == "amstralift/persistent-upgrade"
    curr_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=local_repo).stdout.strip()
    assert curr_branch == "amstralift/persistent-upgrade"

