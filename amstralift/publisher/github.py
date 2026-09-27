"""GitHub provider client for remote PR publishing, duplicate checks, and push."""

import base64
import logging
import re
import time
from pathlib import Path

import httpx

from amstralift.core.workspace import get_head_commit, run_git
from amstralift.publisher.base import BaseGitProvider, RemotePR

logger = logging.getLogger(__name__)


class GitHubPublishError(Exception):
    """Raised when GitHub API operations fail."""

    pass


def sanitize_message(msg: str, token: str | None) -> str:
    """Scrub authentication tokens from messages or logs."""
    if not token or not msg:
        return msg
    sanitized = msg.replace(token, "[REDACTED_TOKEN]")
    # Also scrub basic auth base64 representation if present
    token_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return sanitized.replace(token_b64, "[REDACTED_AUTH_HEADER]")


def parse_github_repo_id(remote_url: str) -> str:
    """Extract 'owner/repo' from a GitHub git remote URL.

    Supports:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - owner/repo (already parsed)
    """
    if "/" in remote_url and not remote_url.startswith(("http", "git@")):
        return remote_url.strip()

    # Matches https://github.com/owner/repo or git@github.com:owner/repo
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?$", remote_url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    raise ValueError(f"Could not parse GitHub 'owner/repo' from remote URL: {remote_url}")


class GitHubProvider(BaseGitProvider):
    """GitHub implementation for remote branch push and pull request operations."""

    def __init__(
        self,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_open_pr(self, repo_id: str, branch: str) -> RemotePR | None:
        """Find an existing open pull request for the given branch."""
        owner = repo_id.split("/")[0]
        # GitHub expects head in format: 'owner:branch'
        url = f"{self.api_base_url}/repos/{repo_id}/pulls"
        params = {"state": "open", "head": f"{owner}:{branch}"}

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.get(url, headers=self._get_headers(), params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        pr_data = data[0]
                        labels = [
                            lbl["name"]
                            for lbl in pr_data.get("labels", [])
                            if "name" in lbl
                        ]
                        return RemotePR(
                            number=pr_data["number"],
                            url=pr_data["html_url"],
                            branch=branch,
                            title=pr_data["title"],
                            state=pr_data["state"],
                            labels=labels,
                        )
                elif resp.status_code == 404:
                    return None
                else:
                    raise GitHubPublishError(
                        f"GitHub API returned {resp.status_code} while querying open PRs: {resp.text}"
                    )
        except httpx.RequestError as exc:
            raise GitHubPublishError(
                sanitize_message(f"Network error querying open PRs: {exc}", self.token)
            ) from exc

        return None

    def create_pull_request(
        self,
        repo_id: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> RemotePR:
        """Create a new pull request and attach labels with retries."""
        url = f"{self.api_base_url}/repos/{repo_id}/pulls"
        payload = {
            "title": title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
        }

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(url, headers=self._get_headers(), json=payload)

                    # If a PR already exists (422), recover it idempotently
                    if resp.status_code == 422:
                        open_pr = self.get_open_pr(repo_id, head_branch)
                        if open_pr:
                            return open_pr

                    if resp.status_code in (200, 201):
                        data = resp.json()
                        pr_number = data["number"]
                        pr_url = data["html_url"]

                        # Attach labels
                        if labels:
                            self._add_labels(client, repo_id, pr_number, labels)

                        return RemotePR(
                            number=pr_number,
                            url=pr_url,
                            branch=head_branch,
                            title=title,
                            state="open",
                            labels=labels,
                        )

                    # Retry on server errors
                    if resp.status_code in (500, 502, 503, 504):
                        last_error = f"GitHub API server error {resp.status_code}: {resp.text}"
                        # Check if PR was already created despite 5xx error
                        open_pr = self.get_open_pr(repo_id, head_branch)
                        if open_pr:
                            return open_pr
                        time.sleep(1.0 * (attempt + 1))
                        continue

                    raise GitHubPublishError(
                        f"GitHub API failed with {resp.status_code}: {resp.text}"
                    )

            except httpx.RequestError as exc:
                last_error = f"Network request error: {exc}"
                # Connection dropped or timeout: check if PR was created by GitHub before the drop
                try:
                    open_pr = self.get_open_pr(repo_id, head_branch)
                    if open_pr:
                        logger.info("Recovered open PR #%d after network error on branch %s", open_pr.number, head_branch)
                        return open_pr
                except Exception:
                    pass
                time.sleep(1.0 * (attempt + 1))

        sanitized_err = sanitize_message(
            last_error or "Unknown failure", self.token
        )
        raise GitHubPublishError(
            f"Failed to create PR after {self.max_retries + 1} attempts: {sanitized_err}"
        )

    def _add_labels(
        self, client: httpx.Client, repo_id: str, pr_number: int, labels: list[str]
    ) -> None:
        """Add labels to the created PR/issue."""
        url = f"{self.api_base_url}/repos/{repo_id}/issues/{pr_number}/labels"
        try:
            resp = client.post(url, headers=self._get_headers(), json={"labels": labels})
            if resp.status_code not in (200, 201):
                logger.warning("GitHub returned %d when attaching labels to PR #%d: %s", resp.status_code, pr_number, resp.text)
        except Exception as exc:
            sanitized = sanitize_message(str(exc), self.token)
            logger.warning("Failed to attach labels to PR #%d: %s", pr_number, sanitized)

    def get_remote_branch_head(
        self,
        repo_path: Path,
        branch_name: str,
        remote_url: str,
        token: str | None = None,
    ) -> str | None:
        """Get the commit SHA of a remote branch using ls-remote. Returns None if branch does not exist."""
        effective_token = token or self.token
        cmd = ["ls-remote"]
        cmd_prefix = []
        if effective_token and not remote_url.startswith(("file://", "/")):
            auth_b64 = base64.b64encode(f"x-access-token:{effective_token}".encode()).decode()
            cmd_prefix = ["-c", f"http.extraheader=AUTHORIZATION: basic {auth_b64}"]

        res = run_git([*cmd_prefix, *cmd, remote_url, f"refs/heads/{branch_name}"], cwd=repo_path)
        if res.returncode == 0 and res.stdout.strip():
            # Output format: "<SHA>\trefs/heads/<branch>"
            return res.stdout.strip().split()[0]
        return None

    def remote_branch_matches_head(
        self,
        repo_path: Path,
        branch_name: str,
        remote_url: str,
        token: str | None = None,
    ) -> bool:
        """Check if remote branch already exists and matches current local HEAD."""
        remote_sha = self.get_remote_branch_head(repo_path, branch_name, remote_url, token)
        if remote_sha:
            local_head = get_head_commit(repo_path)
            return remote_sha == local_head
        return False

    def push_branch(
        self,
        repo_path: Path,
        branch_name: str,
        remote_url: str,
        token: str | None = None,
    ) -> None:
        """Push a local branch to the remote repository.

        Credentials are passed in-memory via http.extraheader and NEVER saved to .git/config.
        """
        effective_token = token or self.token
        cmd_prefix = []
        if effective_token and not remote_url.startswith(("file://", "/")):
            auth_b64 = base64.b64encode(f"x-access-token:{effective_token}".encode()).decode()
            cmd_prefix = ["-c", f"http.extraheader=AUTHORIZATION: basic {auth_b64}"]

        # Push branch
        cmd = [*cmd_prefix, "push", "-u", remote_url, f"{branch_name}:{branch_name}"]
        res = run_git(cmd, cwd=repo_path)
        if res.returncode != 0:
            sanitized_err = sanitize_message(res.stderr, effective_token)
            raise GitHubPublishError(f"Failed to push branch '{branch_name}' to remote: {sanitized_err}")
