"""Global pytest fixtures and environment setup for AMstraLift."""

import os
import pytest

# Ensure Git operations always have author/committer identity in any runner/container
os.environ.setdefault("GIT_AUTHOR_NAME", "AMstraLift Test Bot")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "test-bot@amstralift.internal")
os.environ.setdefault("GIT_COMMITTER_NAME", "AMstraLift Test Bot")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "test-bot@amstralift.internal")


@pytest.fixture(autouse=True, scope="session")
def configure_test_git_identity():
    """Guarantee that Git subprocesses have an author and committer identity across all tests."""
    os.environ["GIT_AUTHOR_NAME"] = "AMstraLift Test Bot"
    os.environ["GIT_AUTHOR_EMAIL"] = "test-bot@amstralift.internal"
    os.environ["GIT_COMMITTER_NAME"] = "AMstraLift Test Bot"
    os.environ["GIT_COMMITTER_EMAIL"] = "test-bot@amstralift.internal"
