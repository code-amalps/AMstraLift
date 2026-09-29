"""Docker Registry Client — queries MCR and Docker Hub for live image tags.

Fetches the latest stable patch tag for a given image base and major version,
preserving the user's chosen suffix variant (e.g. -alpine, -slim, -jammy).

Supported registries:
  - MCR (Microsoft Container Registry): mcr.microsoft.com/dotnet/*
  - Docker Hub Official Images: node, python

Design:
  - Live first, hardcoded fallback if any network/API error occurs.
  - Never raises — always returns a result (live or fallback).
  - Results are in-process cached to avoid repeated API calls per run.
  - Rejects preview/RC/nightly tags (e.g. 9.0.0-preview.7, rc.1, nightly).
  - Filters by major version prefix and suffix variant independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# Registry endpoints
# ─────────────────────────────────────────────────────────────────────────────

# MCR uses Docker Registry HTTP API v2
MCR_TAGS_URL = "https://mcr.microsoft.com/v2/{repository}/tags/list"

# Docker Hub public API (no auth required for Official images)
DOCKERHUB_TAGS_URL = "https://hub.docker.com/v2/repositories/library/{image}/tags/"

# Known MCR .NET image repositories
MCR_DOTNET_REPOS: dict[str, str] = {
    "mcr.microsoft.com/dotnet/aspnet": "dotnet/aspnet",
    "mcr.microsoft.com/dotnet/sdk": "dotnet/sdk",
    "mcr.microsoft.com/dotnet/runtime": "dotnet/runtime",
    "mcr.microsoft.com/dotnet/runtime-deps": "dotnet/runtime-deps",
}

# Known Docker Hub official images we manage
DOCKERHUB_OFFICIAL_IMAGES = {"node", "python"}

# Tags matching these patterns are unstable and must be excluded
_UNSTABLE_PATTERNS = re.compile(
    r"(preview|rc\.|alpha|beta|nightly|dev|canary|experimental)",
    re.IGNORECASE,
)

# Tag pattern for MCR .NET: "9.0.3", "9.0.3-alpine3.20", "9.0.3-jammy"
_DOTNET_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(-.+)?$")

# Tag pattern for Docker Hub node/python: "20.15.1", "20-alpine", "20.15.1-alpine3.20"
_NODE_TAG_RE = re.compile(r"^(\d+)(?:\.(\d+)\.(\d+))?(-.+)?$")
_PYTHON_TAG_RE = re.compile(r"^(3\.\d+)(?:\.(\d+))?(-.+)?$")


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResolvedImageTag:
    image: str          # e.g. "mcr.microsoft.com/dotnet/aspnet"
    tag: str            # e.g. "9.0.3-alpine3.20"
    source: str         # "live" | "fallback"
    reason: str         # human-readable explanation


@dataclass
class _TagFetchCache:
    """In-process cache for fetched tag lists."""
    _store: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str) -> list[str] | None:
        return self._store.get(key)

    def set(self, key: str, value: list[str]) -> None:
        self._store[key] = value


_CACHE = _TagFetchCache()


# ─────────────────────────────────────────────────────────────────────────────
# Fallback tables (used when registry is unreachable)
# ─────────────────────────────────────────────────────────────────────────────

# MCR .NET fallback: major → latest known stable tag prefix
DOTNET_FALLBACK_TAGS: dict[int, str] = {
    8: "8.0",
    9: "9.0",
    10: "10.0",
}

# Node.js fallback: major → latest known stable tag prefix
NODE_FALLBACK_TAGS: dict[int, str] = {
    18: "18",
    20: "20",
    22: "22",
}

# Python fallback: minor series → latest known stable tag prefix
PYTHON_FALLBACK_TAGS: dict[str, str] = {
    "3.11": "3.11",
    "3.12": "3.12",
    "3.13": "3.13",
}


# ─────────────────────────────────────────────────────────────────────────────
# Tag filtering helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_stable(tag: str) -> bool:
    """Return True if the tag does not match any unstable pattern."""
    return not _UNSTABLE_PATTERNS.search(tag)


def _extract_suffix(tag: str) -> str:
    """
    Extract the variant suffix from a tag.

    Examples:
      "9.0.3-alpine3.20"  → "-alpine"
      "20-alpine"          → "-alpine"
      "20-slim"            → "-slim"
      "3.12.1-slim-bullseye" → "-slim"  (normalise to base variant)
      "9.0.3"              → ""
    """
    # Normalize known suffix families
    lower = tag.lower()
    for variant in ("-alpine", "-slim", "-jammy", "-bookworm", "-bullseye", "-focal", "-buster"):
        if variant in lower:
            return variant
    return ""


def _suffix_matches(tag: str, required_suffix: str) -> bool:
    """Return True if the tag's normalized suffix matches required_suffix."""
    return _extract_suffix(tag) == required_suffix


def _semver_key_dotnet(tag: str) -> tuple[int, int, int]:
    """Sort key for .NET tags like '9.0.3', '9.0.3-alpine3.20'."""
    m = _DOTNET_TAG_RE.match(tag)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)


def _semver_key_node(tag: str) -> tuple[int, int, int]:
    """Sort key for Node tags like '20', '20.15.1', '20-alpine'."""
    m = _NODE_TAG_RE.match(tag)
    if m:
        return (
            int(m.group(1)),
            int(m.group(2)) if m.group(2) else 0,
            int(m.group(3)) if m.group(3) else 0,
        )
    return (0, 0, 0)


def _semver_key_python(tag: str) -> tuple[int, int, int]:
    """Sort key for Python tags like '3.12', '3.12.4', '3.12.4-slim'."""
    m = _PYTHON_TAG_RE.match(tag)
    if m:
        parts = m.group(1).split(".")
        return (int(parts[0]), int(parts[1]), int(m.group(2)) if m.group(2) else 0)
    return (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Registry fetch functions
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_mcr_tags(repository: str, timeout: float = 6.0) -> list[str]:
    """Fetch all tags for a MCR repository using Docker Registry API v2.

    MCR paginates via Link headers. We follow up to 5 pages.
    """
    cache_key = f"mcr:{repository}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    all_tags: list[str] = []
    url: str | None = MCR_TAGS_URL.format(repository=repository)
    pages = 0

    try:
        with httpx.Client(timeout=timeout) as client:
            while url and pages < 5:
                res = client.get(url)
                if res.status_code != 200:
                    break
                data = res.json()
                all_tags.extend(data.get("tags") or [])
                pages += 1

                # Follow pagination Link header if present
                link = res.headers.get("Link", "")
                next_url_match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                url = next_url_match.group(1) if next_url_match else None

    except Exception:
        pass

    _CACHE.set(cache_key, all_tags)
    return all_tags


def _fetch_dockerhub_tags(image: str, timeout: float = 6.0) -> list[str]:
    """Fetch tags for a Docker Hub Official image, up to 300 entries."""
    cache_key = f"dockerhub:{image}"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    all_tags: list[str] = []
    url: str | None = DOCKERHUB_TAGS_URL.format(image=image) + "?page_size=100"
    pages = 0

    try:
        with httpx.Client(timeout=timeout) as client:
            while url and pages < 3:
                res = client.get(url)
                if res.status_code != 200:
                    break
                data = res.json()
                for entry in data.get("results") or []:
                    name = entry.get("name", "")
                    if name:
                        all_tags.append(name)
                url = data.get("next")
                pages += 1
    except Exception:
        pass

    _CACHE.set(cache_key, all_tags)
    return all_tags


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class DockerRegistryClient:
    """
    Resolves the latest stable Docker image tag for a given image, major version,
    and suffix variant. Falls back to hardcoded tables if the registry is unreachable.

    Usage:
        tag = DockerRegistryClient.resolve_latest_tag(
            image="mcr.microsoft.com/dotnet/aspnet",
            target_major=9,
            current_suffix="-alpine",   # e.g. extracted from "8.0-alpine"
        )
        # → ResolvedImageTag(tag="9.0.3-alpine3.20", source="live", ...)
    """

    @staticmethod
    def resolve_dotnet(
        image: str,
        target_major: int,
        current_suffix: str = "",
    ) -> ResolvedImageTag:
        """
        Resolve the latest stable .NET image tag from MCR.

        Args:
            image: Full MCR image name, e.g. "mcr.microsoft.com/dotnet/aspnet"
            target_major: Target .NET major version, e.g. 9
            current_suffix: Variant suffix from current tag, e.g. "-alpine", "-slim", ""
        """
        repository = MCR_DOTNET_REPOS.get(image.lower().rstrip("/"))
        if not repository:
            # Not a known MCR .NET image — return fallback
            fallback_prefix = DOTNET_FALLBACK_TAGS.get(target_major, f"{target_major}.0")
            tag = f"{fallback_prefix}{current_suffix}" if current_suffix else fallback_prefix
            return ResolvedImageTag(
                image=image, tag=tag, source="fallback",
                reason=f"Image '{image}' is not a known MCR .NET image. Using fallback tag {tag}."
            )

        all_tags = _fetch_mcr_tags(repository)

        if all_tags:
            # Filter: correct major prefix, stable, suffix matches
            major_prefix = f"{target_major}."
            candidates = [
                t for t in all_tags
                if t.startswith(major_prefix)
                and _is_stable(t)
                and _suffix_matches(t, current_suffix)
                and _DOTNET_TAG_RE.match(t)  # must be full semver, not just "9.0"
            ]

            if not candidates and current_suffix:
                # Try without suffix constraint (some patch releases may drop old suffix)
                candidates = [
                    t for t in all_tags
                    if t.startswith(major_prefix)
                    and _is_stable(t)
                    and _DOTNET_TAG_RE.match(t)
                ]

            if candidates:
                best = sorted(candidates, key=_semver_key_dotnet, reverse=True)[0]
                return ResolvedImageTag(
                    image=image, tag=best, source="live",
                    reason=f"Latest stable .NET {target_major} tag from MCR: {best}"
                )

        # Fallback
        fallback_prefix = DOTNET_FALLBACK_TAGS.get(target_major, f"{target_major}.0")
        tag = f"{fallback_prefix}{current_suffix}" if current_suffix else fallback_prefix
        return ResolvedImageTag(
            image=image, tag=tag, source="fallback",
            reason=f"MCR registry unavailable or no matching tag found. Fallback: {tag}"
        )

    @staticmethod
    def resolve_node(
        target_major: int,
        current_suffix: str = "",
    ) -> ResolvedImageTag:
        """
        Resolve the latest stable Node.js image tag from Docker Hub.

        Args:
            target_major: Target Node.js major version, e.g. 20
            current_suffix: Variant suffix from current tag, e.g. "-alpine", "-slim", ""
        """
        all_tags = _fetch_dockerhub_tags("node")

        if all_tags:
            major_prefix = str(target_major)
            candidates = [
                t for t in all_tags
                if (t == major_prefix or t.startswith(f"{major_prefix}.") or t.startswith(f"{major_prefix}-"))
                and _is_stable(t)
                and _suffix_matches(t, current_suffix)
            ]

            if not candidates and current_suffix:
                candidates = [
                    t for t in all_tags
                    if (t == major_prefix or t.startswith(f"{major_prefix}."))
                    and _is_stable(t)
                ]

            if candidates:
                # Prefer tags with full semver over bare major
                full_semver = [t for t in candidates if _semver_key_node(t)[1] > 0]
                pool = full_semver if full_semver else candidates
                best = sorted(pool, key=_semver_key_node, reverse=True)[0]
                return ResolvedImageTag(
                    image="node", tag=best, source="live",
                    reason=f"Latest stable Node.js {target_major} tag from Docker Hub: {best}"
                )

        # Fallback
        fallback_prefix = NODE_FALLBACK_TAGS.get(target_major, str(target_major))
        tag = f"{fallback_prefix}{current_suffix}" if current_suffix else fallback_prefix
        return ResolvedImageTag(
            image="node", tag=tag, source="fallback",
            reason=f"Docker Hub unavailable or no matching Node tag. Fallback: {tag}"
        )

    @staticmethod
    def resolve_python(
        target_minor: str,
        current_suffix: str = "",
    ) -> ResolvedImageTag:
        """
        Resolve the latest stable Python image tag from Docker Hub.

        Args:
            target_minor: Target Python minor version string, e.g. "3.12"
            current_suffix: Variant suffix from current tag, e.g. "-slim", "-alpine", ""
        """
        all_tags = _fetch_dockerhub_tags("python")

        if all_tags:
            candidates = [
                t for t in all_tags
                if (t == target_minor or t.startswith(f"{target_minor}.") or t.startswith(f"{target_minor}-"))
                and _is_stable(t)
                and _suffix_matches(t, current_suffix)
                and _PYTHON_TAG_RE.match(t)
            ]

            if not candidates and current_suffix:
                candidates = [
                    t for t in all_tags
                    if (t == target_minor or t.startswith(f"{target_minor}."))
                    and _is_stable(t)
                    and _PYTHON_TAG_RE.match(t)
                ]

            if candidates:
                best = sorted(candidates, key=_semver_key_python, reverse=True)[0]
                return ResolvedImageTag(
                    image="python", tag=best, source="live",
                    reason=f"Latest stable Python {target_minor} tag from Docker Hub: {best}"
                )

        # Fallback
        fallback_prefix = PYTHON_FALLBACK_TAGS.get(target_minor, target_minor)
        tag = f"{fallback_prefix}{current_suffix}" if current_suffix else fallback_prefix
        return ResolvedImageTag(
            image="python", tag=tag, source="fallback",
            reason=f"Docker Hub unavailable or no matching Python tag. Fallback: {tag}"
        )
