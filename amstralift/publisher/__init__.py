"""AMstraLift Publisher package.

Provides provider-neutral Git publisher abstractions and provider implementations
(e.g., GitHub) for Stage B remote PR creation, duplicate lookup, and idempotency.
"""

from amstralift.publisher.base import BaseGitProvider, PublishResult, PublishStatus, RemotePR
from amstralift.publisher.github import GitHubProvider

__all__ = [
    "BaseGitProvider",
    "GitHubProvider",
    "PublishResult",
    "PublishStatus",
    "RemotePR",
]
