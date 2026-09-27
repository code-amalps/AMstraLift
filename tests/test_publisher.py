"""Unit tests for Git provider abstractions, GitHub client, and idempotency logic."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from amstralift.core.models import DependencyChange, DependencyTier
from amstralift.publisher.base import (
    RemotePR,
    calculate_idempotency_key,
    derive_deterministic_branch_name,
)
from amstralift.publisher.github import (
    GitHubProvider,
    parse_github_repo_id,
    sanitize_message,
)


def test_sanitize_message():
    secret = "ghp_super_secret_token_12345"
    raw_error = f"Failed connecting with Bearer {secret} to GitHub"
    sanitized = sanitize_message(raw_error, secret)
    assert secret not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized


def test_parse_github_repo_id():
    assert parse_github_repo_id("https://github.com/my-org/my-repo.git") == "my-org/my-repo"
    assert parse_github_repo_id("git@github.com:my-org/my-repo.git") == "my-org/my-repo"
    assert parse_github_repo_id("my-org/my-repo") == "my-org/my-repo"

    with pytest.raises(ValueError, match="Could not parse"):
        parse_github_repo_id("https://gitlab.com/other/repo.git")


def test_idempotency_key_and_branch_name():
    changes = [
        DependencyChange(
            package_name="requests",
            from_version="2.25.0",
            to_version="2.32.3",
            tier=DependencyTier.TIER_1_SAFE,
        )
    ]
    key1 = calculate_idempotency_key("my-org/app", "main", changes, "commit_aaa")
    key2 = calculate_idempotency_key("my-org/app", "main", changes, "commit_aaa")
    key_diff = calculate_idempotency_key("my-org/app", "main", changes, "commit_bbb")

    assert key1 == key2
    assert key1 != key_diff
    assert len(key1) == 64  # SHA256 hex

    # With idempotency key hash suffix
    branch = derive_deterministic_branch_name("requests", "2.32.3", idempotency_key=key1)
    assert branch == f"amstralift/requests-2.32.3-{key1[:8]}"

    angular_branch = derive_deterministic_branch_name("@angular/core", "19.1.0", idempotency_key=key1)
    assert angular_branch == f"amstralift/angular-core-19.1.0-{key1[:8]}"


def test_branch_collision_avoidance():
    """Ensure branch names do NOT collide when target branch or dependency change differs."""
    changes_single = [
        DependencyChange(package_name="requests", from_version="2.25.0", to_version="2.32.3", tier=DependencyTier.TIER_1_SAFE)
    ]
    changes_multi = [
        DependencyChange(package_name="requests", from_version="2.25.0", to_version="2.32.3", tier=DependencyTier.TIER_1_SAFE),
        DependencyChange(package_name="urllib3", from_version="1.26.0", to_version="2.2.0", tier=DependencyTier.TIER_1_SAFE),
    ]

    key_main = calculate_idempotency_key("my-org/app", "main", changes_single, "commit_111")
    key_release = calculate_idempotency_key("my-org/app", "release-1.0", changes_single, "commit_111")
    key_multi = calculate_idempotency_key("my-org/app", "main", changes_multi, "commit_111")
    key_diff_base = calculate_idempotency_key("my-org/app", "main", changes_single, "commit_222")

    b_main = derive_deterministic_branch_name("requests", "2.32.3", idempotency_key=key_main)
    b_release = derive_deterministic_branch_name("requests", "2.32.3", idempotency_key=key_release)
    b_multi = derive_deterministic_branch_name("requests", "2.32.3", idempotency_key=key_multi)
    b_diff_base = derive_deterministic_branch_name("requests", "2.32.3", idempotency_key=key_diff_base)

    # All branch names must be distinct due to the short hash suffix
    assert b_main != b_release
    assert b_main != b_multi
    assert b_main != b_diff_base



def test_github_get_open_pr():
    provider = GitHubProvider(token="test_token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "number": 42,
            "html_url": "https://github.com/owner/repo/pull/42",
            "title": "chore(deps): upgrade requests",
            "state": "open",
            "labels": [{"name": "amstralift-automated"}, {"name": "tier-1-safe"}],
        }
    ]

    with patch("httpx.Client.get", return_value=mock_resp):
        pr = provider.get_open_pr("owner/repo", "amstralift/requests-2.32.3")
        assert pr is not None
        assert pr.number == 42
        assert pr.branch == "amstralift/requests-2.32.3"
        assert "amstralift-automated" in pr.labels


def test_github_get_open_pr_none_found():
    provider = GitHubProvider(token="test_token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []

    with patch("httpx.Client.get", return_value=mock_resp):
        pr = provider.get_open_pr("owner/repo", "amstralift/nonexistent")
        assert pr is None


def test_github_create_pr_success():
    provider = GitHubProvider(token="test_token")

    create_resp = MagicMock()
    create_resp.status_code = 201
    create_resp.json.return_value = {
        "number": 101,
        "html_url": "https://github.com/owner/repo/pull/101",
    }

    labels_resp = MagicMock()
    labels_resp.status_code = 200

    with patch("httpx.Client.post") as mock_post:
        mock_post.side_effect = [create_resp, labels_resp]

        pr = provider.create_pull_request(
            repo_id="owner/repo",
            head_branch="amstralift/requests-2.32.3",
            base_branch="main",
            title="chore(deps): upgrade requests",
            body="PR body",
            labels=["amstralift-automated"],
        )

        assert pr.number == 101
        assert pr.url == "https://github.com/owner/repo/pull/101"
        assert pr.state == "open"


def test_github_create_pr_422_recovers_existing():
    provider = GitHubProvider(token="test_token")

    err_resp = MagicMock()
    err_resp.status_code = 422
    err_resp.text = "A pull request already exists for owner:amstralift/requests-2.32.3"

    existing_pr = RemotePR(
        number=77,
        url="https://github.com/owner/repo/pull/77",
        branch="amstralift/requests-2.32.3",
        title="Existing PR",
    )

    with patch("httpx.Client.post", return_value=err_resp):
        with patch.object(provider, "get_open_pr", return_value=existing_pr):
            pr = provider.create_pull_request(
                repo_id="owner/repo",
                head_branch="amstralift/requests-2.32.3",
                base_branch="main",
                title="New PR",
                body="Body",
                labels=[],
            )
            assert pr.number == 77
            assert pr.url == "https://github.com/owner/repo/pull/77"


def test_github_create_pr_timeout_recovers_existing():
    """If network times out after GitHub created PR, client recovers open PR."""
    provider = GitHubProvider(token="test_token")

    existing_pr = RemotePR(
        number=99,
        url="https://github.com/owner/repo/pull/99",
        branch="amstralift/requests-2.32.3",
        title="Created but timed out response",
    )

    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Connection timed out")):
        with patch.object(provider, "get_open_pr", return_value=existing_pr):
            pr = provider.create_pull_request(
                repo_id="owner/repo",
                head_branch="amstralift/requests-2.32.3",
                base_branch="main",
                title="Title",
                body="Body",
                labels=["amstralift-automated"],
            )
            assert pr.number == 99
            assert pr.url == "https://github.com/owner/repo/pull/99"


def test_github_labels_failure_handled():
    """If labels API call fails, PR creation still succeeds and logs warning."""
    provider = GitHubProvider(token="test_token")

    create_resp = MagicMock()
    create_resp.status_code = 201
    create_resp.json.return_value = {
        "number": 105,
        "html_url": "https://github.com/owner/repo/pull/105",
    }

    labels_resp = MagicMock()
    labels_resp.status_code = 500
    labels_resp.text = "Internal Server Error"

    with patch("httpx.Client.post") as mock_post:
        mock_post.side_effect = [create_resp, labels_resp]

        pr = provider.create_pull_request(
            repo_id="owner/repo",
            head_branch="amstralift/requests-2.32.3",
            base_branch="main",
            title="chore(deps): upgrade requests",
            body="PR body",
            labels=["tier-1-safe"],
        )

        assert pr.number == 105
        assert pr.url == "https://github.com/owner/repo/pull/105"


def test_token_scrubbing_on_git_failure(tmp_path: Path):
    """If git command fails with token in error, token is scrubbed."""
    provider = GitHubProvider(token="secret_token_abc_xyz")
    with pytest.raises(Exception) as exc_info:
        # Invalid remote URL to force git push failure
        provider.push_branch(
            repo_path=tmp_path,
            branch_name="test-branch",
            remote_url="https://github.com/nonexistent/repo.git",
        )
    err_str = str(exc_info.value)
    assert "secret_token_abc_xyz" not in err_str

