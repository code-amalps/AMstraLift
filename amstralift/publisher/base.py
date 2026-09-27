"""Provider-neutral Git publisher abstractions and models."""

import hashlib
import re
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from amstralift.core.models import DependencyChange


class PublishStatus(str, Enum):
    """Outcome status of a remote publish attempt."""

    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class RemotePR(BaseModel):
    """Details of a remote pull request on GitHub/GitLab."""

    number: int
    url: str
    branch: str
    title: str
    state: str = "open"
    labels: list[str] = Field(default_factory=list)


class PublishResult(BaseModel):
    """Result of a publish operation in Stage B."""

    status: PublishStatus
    pr: RemotePR | None = None
    message: str
    idempotency_key: str
    branch_name: str


def calculate_idempotency_key(
    repo_id: str,
    target_branch: str,
    changes: list[DependencyChange],
    base_commit_sha: str,
) -> str:
    """Calculate a deterministic idempotency key for an upgrade operation.

    Bound to repo identifier, target branch, sorted dependency changes, and base commit.
    """
    sorted_changes = sorted(
        [f"{c.package_name}:{c.from_version}->{c.to_version}" for c in changes]
    )
    payload = f"{repo_id}|{target_branch}|{','.join(sorted_changes)}|{base_commit_sha}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def derive_deterministic_branch_name(primary_package: str, target_version: str) -> str:
    """Derive a deterministic, idempotent branch name without timestamps.

    Strictly sanitizes invalid Git ref characters (^, ~, :, ?, *, [, \\, >, <, =, @, /).
    Example: '@angular/core' and '^19.1.0' -> 'amstralift/angular-core-19.1.0'
    Example: 'requests' and '>=2.32.3' -> 'amstralift/requests-2.32.3'
    """
    clean_pkg = re.sub(r"[^a-zA-Z0-9_-]", "-", primary_package.lower()).strip("-")
    clean_version = re.sub(r"[^a-zA-Z0-9_.-]", "", target_version.lower()).strip("-.")
    return f"amstralift/{clean_pkg}-{clean_version or 'latest'}"



class BaseGitProvider(ABC):
    """Abstract interface for remote Git hosting providers (GitHub, GitLab, etc.)."""

    @abstractmethod
    def get_open_pr(self, repo_id: str, branch: str) -> RemotePR | None:
        """Find an existing open pull request for the given branch."""
        pass

    @abstractmethod
    def create_pull_request(
        self,
        repo_id: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> RemotePR:
        """Create a new pull request and attach labels."""
        pass

    @abstractmethod
    def push_branch(
        self,
        repo_path: Path,
        branch_name: str,
        remote_url: str,
        token: str | None = None,
    ) -> None:
        """Push a local branch to the remote repository.

        Credentials must be passed in-memory and NEVER persisted to .git/config or logs.
        """
        pass

    @abstractmethod
    def remote_branch_matches_head(
        self,
        repo_path: Path,
        branch_name: str,
        remote_url: str,
        token: str | None = None,
    ) -> bool:
        """Check if the remote branch already exists and matches current local HEAD."""
        pass
