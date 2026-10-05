"""Dockerfile and docker-compose.yml updater.

Parses FROM instructions in Dockerfiles and updates base image tags
to stay consistent with framework/runtime version upgrades.

Design principles:
- Purely additive: called AFTER each adapter finishes its own apply_upgrade().
  If no Dockerfile exists, this is a no-op and nothing is touched.
- Never modifies image names (only tags): e.g. node:18-alpine → node:20-alpine.
  It does NOT switch from alpine to debian or change the registry.
- Multi-stage aware: updates every matching FROM line (builder + runtime stages).
- docker-compose.yml aware: updates image: entries that match the same base.
- Silent on Docker absence: no error if docker is not installed.
- All changes are logged in DockerUpdateResult for audit transparency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from amstralift.core.workspace import safe_rglob

# ─────────────────────────────────────────────────────────────────────────────
# Image tag mapping tables
# ─────────────────────────────────────────────────────────────────────────────

# Maps .NET SDK major version string → recommended base image tag suffixes.
# Key: (image_base, old_major) → new_tag
# image_base examples: "mcr.microsoft.com/dotnet/aspnet",
#                      "mcr.microsoft.com/dotnet/sdk",
#                      "mcr.microsoft.com/dotnet/runtime"
DOTNET_IMAGE_BASES = {
    "mcr.microsoft.com/dotnet/aspnet",
    "mcr.microsoft.com/dotnet/sdk",
    "mcr.microsoft.com/dotnet/runtime",
    "mcr.microsoft.com/dotnet/runtime-deps",
}

# Maps Node.js major version → recommended stable tag.
# Covers common suffixes (-alpine, -slim, -bookworm, -bullseye, bare).
NODE_LTS_VERSIONS: dict[int, int] = {
    # current → recommended LTS major
    14: 20,
    16: 20,
    18: 20,
    20: 20,
    21: 22,
    22: 22,
}

# Maps Python major.minor → recommended tag
PYTHON_IMAGE_BASE = "python"
PYTHON_LTS_VERSIONS: dict[str, str] = {
    "3.8": "3.12",
    "3.9": "3.12",
    "3.10": "3.12",
    "3.11": "3.12",
    "3.12": "3.12",
    "3.13": "3.13",
}


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DockerImageChange:
    """Records a single FROM-line or image: change."""
    file: str           # Relative path of file changed
    line_number: int    # 1-indexed line number
    original: str       # Original FROM / image: value
    updated: str        # Replacement value
    reason: str         # Human-readable rationale


@dataclass
class DockerUpdateResult:
    """Summary of all Docker-related file changes applied."""
    dockerfiles_found: list[str] = field(default_factory=list)
    compose_files_found: list[str] = field(default_factory=list)
    changes: list[DockerImageChange] = field(default_factory=list)
    skipped_lines: list[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.changes)

    @property
    def had_changes(self) -> bool:
        return bool(self.changes)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

# Matches: FROM image:tag [AS alias]
# Groups: (image_with_registry, tag, alias_clause)
_FROM_RE = re.compile(
    r"^(FROM\s+)(\S+):([^\s]+)(\s+AS\s+\S+)?\s*$",
    re.IGNORECASE,
)

# Matches bare FROM without a tag: FROM ubuntu
_FROM_NO_TAG_RE = re.compile(r"^FROM\s+(\S+)\s*$", re.IGNORECASE)

# docker-compose image: node:18-alpine or image: mcr.microsoft.com/dotnet/aspnet:8.0
_COMPOSE_IMAGE_RE = re.compile(
    r"^(\s*image:\s*)(\S+):([^\s#]+)(.*)?$",
    re.IGNORECASE,
)


def _find_dockerfiles(repo_path: Path) -> list[Path]:
    """Find all Dockerfiles (e.g. Dockerfile, Dockerfile.*, *Dockerfile*, *.dockerfile) in the repository."""
    candidates = safe_rglob(repo_path, ["Dockerfile*", "*Dockerfile*", "*.dockerfile"])
    found = []
    seen = set()
    for f in candidates:
        if "dockerfile" in f.name.lower() and f not in seen:
            seen.add(f)
            found.append(f)
    return found


def _find_compose_files(repo_path: Path) -> list[Path]:
    """Find docker-compose.yml / docker-compose.yaml / compose.yml variants."""
    patterns = [
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.*.yml",
        "docker-compose.*.yaml",
        "compose.yml",
        "compose.yaml",
    ]
    found: list[Path] = []
    for pattern in patterns:
        for p in repo_path.glob(pattern):
            if p.is_file():
                found.append(p)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Tag resolution logic
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_dotnet_tag(image: str, old_tag: str, target_version: str) -> str | None:
    """
    Given a .NET base image and its current tag, compute the new tag for target_version.

    Examples:
      image=mcr.microsoft.com/dotnet/aspnet, old_tag=8.0, target_version=9 → "9.0"
      image=mcr.microsoft.com/dotnet/sdk, old_tag=8.0-alpine, target_version=9 → "9.0-alpine"
    """
    image_lower = image.lower().rstrip("/")
    if not any(image_lower.startswith(base) for base in DOTNET_IMAGE_BASES):
        return None

    # Extract the suffix variant (e.g. "-alpine", "-jammy", "")
    # .NET tags look like "8.0", "8.0-alpine", "8.0-alpine3.20", "8.0-jammy"
    tag_version_re = re.match(r"^(\d+)\.(\d+)(.*)?$", old_tag)
    if not tag_version_re:
        return None

    # Parse target major from target_version (e.g. "9" or "net9.0" or "net9")
    target_major_match = re.search(r"(\d+)", str(target_version))
    if not target_major_match:
        return None
    target_major = int(target_major_match.group(1))

    suffix = tag_version_re.group(3) or ""
    new_tag = f"{target_major}.0{suffix}"
    return new_tag


def _resolve_node_tag(image: str, old_tag: str, target_node_major: int) -> str | None:
    """
    Given a node base image and its current tag, compute the new tag for target_node_major.

    Examples:
      image=node, old_tag=18-alpine, target_node_major=20 → "20-alpine"
      image=node, old_tag=18,        target_node_major=20 → "20"
      image=node, old_tag=18.15-slim, target_node_major=20 → "20-slim"
    """
    image_lower = image.lower()
    if image_lower not in ("node", "node.js") and not image_lower.startswith("node:"):
        # Accept if image == "node" exactly (after splitting at colon above, image is just base)
        if image_lower != "node":
            return None

    # Extract old major and suffix from tag like "18-alpine", "18", "18.15.0-slim"
    tag_re = re.match(r"^(\d+)(?:[\.\d]*)?(-.+)?$", old_tag)
    if not tag_re:
        return None

    suffix = tag_re.group(2) or ""
    new_tag = f"{target_node_major}{suffix}"
    return new_tag


def _resolve_python_tag(image: str, old_tag: str, target_python: str) -> str | None:
    """
    Given a python base image and tag, compute the new tag.

    Examples:
      image=python, old_tag=3.11-slim, target_python=3.12 → "3.12-slim"
      image=python, old_tag=3.9,       target_python=3.12 → "3.12"
    """
    if image.lower() != "python":
        return None

    tag_re = re.match(r"^(3\.\d+)(.*)?$", old_tag)
    if not tag_re:
        return None

    suffix = tag_re.group(2) or ""
    new_tag = f"{target_python}{suffix}"
    return new_tag


# ─────────────────────────────────────────────────────────────────────────────
# Main updater class
# ─────────────────────────────────────────────────────────────────────────────

class DockerfileUpdater:
    """
    Updates Dockerfiles and docker-compose files when framework/runtime versions change.

    Usage:
        result = DockerfileUpdater.update(
            repo_path=Path("/path/to/repo"),
            ecosystem="dotnet",          # "angular" | "react" | "dotnet" | "python"
            target_version="9",          # The new version being upgraded to
        )
    """

    @staticmethod
    def update(
        repo_path: Path,
        ecosystem: str,
        target_version: str,
    ) -> DockerUpdateResult:
        """
        Find all Dockerfiles and docker-compose files and update base image tags
        that match the given ecosystem and target_version.

        Returns a DockerUpdateResult with a full audit log of what changed.
        Never raises — on any file error, the file is skipped.
        """
        result = DockerUpdateResult()

        dockerfiles = _find_dockerfiles(repo_path)
        compose_files = _find_compose_files(repo_path)

        result.dockerfiles_found = [str(p.relative_to(repo_path)) for p in dockerfiles]
        result.compose_files_found = [str(p.relative_to(repo_path)) for p in compose_files]

        for df in dockerfiles:
            DockerfileUpdater._update_dockerfile(
                df, repo_path, ecosystem, target_version, result
            )

        for cf in compose_files:
            DockerfileUpdater._update_compose(
                cf, repo_path, ecosystem, target_version, result
            )

        return result

    @staticmethod
    def _resolve_new_tag(
        image: str,
        old_tag: str,
        ecosystem: str,
        target_version: str,
    ) -> str | None:
        """
        Resolve the latest stable tag for a given image, ecosystem, and target version.

        Uses live registry clients (MCR / Docker Hub) to find the actual latest
        stable patch release matching the target major and current suffix variant.
        Falls back to simple version substitution if the registry is unreachable.
        """
        import re as _re

        from amstralift.adapters.docker_registry_client import (
            MCR_DOTNET_REPOS,
            DockerRegistryClient,
            _extract_suffix,
        )
        from amstralift.adapters.node_release_feed import NodeReleaseFeed

        ecosystem_lower = ecosystem.lower()

        if ecosystem_lower == "dotnet":
            image_lower = image.lower().rstrip("/")
            if any(image_lower.startswith(base) for base in MCR_DOTNET_REPOS):
                target_major_match = _re.search(r"(\d+)", str(target_version))
                if not target_major_match:
                    return _resolve_dotnet_tag(image, old_tag, target_version)
                target_major = int(target_major_match.group(1))
                suffix = _extract_suffix(old_tag)
                resolved = DockerRegistryClient.resolve_dotnet(image, target_major, suffix)
                return resolved.tag
            return _resolve_dotnet_tag(image, old_tag, target_version)

        if ecosystem_lower in ("angular", "react"):
            if image.lower() != "node":
                return None
            try:
                framework_major_match = _re.search(r"(\d+)", str(target_version))
                if not framework_major_match:
                    return None
                framework_major = int(framework_major_match.group(1))
                if ecosystem_lower == "angular":
                    node_major = NodeReleaseFeed.node_for_angular(framework_major)
                else:
                    node_major = NodeReleaseFeed.recommended_lts_major().recommended_major
                suffix = _extract_suffix(old_tag)
                resolved = DockerRegistryClient.resolve_node(node_major, suffix)
                return resolved.tag
            except Exception:
                # Offline fallback for Angular/React → Node mapping
                _fallback_map: dict[int, int] = {
                    12: 14, 13: 16, 14: 16, 15: 16, 16: 18,
                    17: 18, 18: 18, 19: 20, 20: 20, 21: 20, 22: 22,
                }
                try:
                    _fm = int(re.search(r"(\d+)", target_version).group(1))  # type: ignore[union-attr]
                    _node = _fallback_map.get(_fm, 20)
                    return _resolve_node_tag(image, old_tag, _node)
                except (AttributeError, ValueError):
                    return None

        if ecosystem_lower == "python":
            if image.lower() != "python":
                return None
            try:
                import re as _re

                from amstralift.adapters.docker_registry_client import (
                    DockerRegistryClient,
                    _extract_suffix,
                )
                ver_match = _re.search(r"(3\.\d+)", str(target_version))
                if ver_match:
                    target_minor = ver_match.group(1)
                    suffix = _extract_suffix(old_tag)
                    resolved = DockerRegistryClient.resolve_python(target_minor, suffix)
                    return resolved.tag
            except Exception:
                ver_match = re.search(r"(3\.\d+)", target_version)
                if ver_match:
                    return _resolve_python_tag(image, old_tag, ver_match.group(1))

        return None

    @staticmethod
    def _update_dockerfile(
        dockerfile: Path,
        repo_path: Path,
        ecosystem: str,
        target_version: str,
        result: DockerUpdateResult,
    ) -> None:
        """Update FROM instructions in a single Dockerfile."""
        try:
            lines = dockerfile.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception:
            return

        new_lines: list[str] = []
        changed = False

        for i, line in enumerate(lines, start=1):
            stripped = line.rstrip("\r\n")
            eol = line[len(stripped):]

            m = _FROM_RE.match(stripped)
            if not m:
                # Check for bare FROM (no tag) — we don't touch those
                new_lines.append(line)
                continue

            keyword = m.group(1)       # "FROM "
            image = m.group(2)         # e.g. "mcr.microsoft.com/dotnet/aspnet"
            old_tag = m.group(3)       # e.g. "8.0"
            alias = m.group(4) or ""   # e.g. " AS build"

            new_tag = DockerfileUpdater._resolve_new_tag(image, old_tag, ecosystem, target_version)

            if new_tag and new_tag != old_tag:
                updated_line = f"{keyword}{image}:{new_tag}{alias}{eol}"
                new_lines.append(updated_line)
                changed = True
                result.changes.append(DockerImageChange(
                    file=str(dockerfile.relative_to(repo_path)),
                    line_number=i,
                    original=f"FROM {image}:{old_tag}{alias}",
                    updated=f"FROM {image}:{new_tag}{alias}",
                    reason=f"[{ecosystem}] Base image tag updated from {old_tag} → {new_tag}",
                ))
            else:
                if new_tag is None and _FROM_RE.match(stripped):
                    # image was recognized pattern but no mapping available — log as skipped
                    result.skipped_lines.append(
                        f"{dockerfile.relative_to(repo_path)}:{i}: {stripped.strip()} (no mapping)"
                    )
                new_lines.append(line)

        if changed:
            try:
                dockerfile.write_text("".join(new_lines), encoding="utf-8")
            except Exception:
                pass

    @staticmethod
    def _update_compose(
        compose_file: Path,
        repo_path: Path,
        ecosystem: str,
        target_version: str,
        result: DockerUpdateResult,
    ) -> None:
        """Update image: entries in a docker-compose file."""
        try:
            lines = compose_file.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception:
            return

        new_lines: list[str] = []
        changed = False

        for i, line in enumerate(lines, start=1):
            stripped = line.rstrip("\r\n")
            eol = line[len(stripped):]

            m = _COMPOSE_IMAGE_RE.match(stripped)
            if not m:
                new_lines.append(line)
                continue

            prefix = m.group(1)    # "    image: "
            image = m.group(2)     # "mcr.microsoft.com/dotnet/aspnet"
            old_tag = m.group(3)   # "8.0"
            trailing = m.group(4) or ""  # inline comment etc.

            new_tag = DockerfileUpdater._resolve_new_tag(image, old_tag, ecosystem, target_version)

            if new_tag and new_tag != old_tag:
                updated_line = f"{prefix}{image}:{new_tag}{trailing}{eol}"
                new_lines.append(updated_line)
                changed = True
                result.changes.append(DockerImageChange(
                    file=str(compose_file.relative_to(repo_path)),
                    line_number=i,
                    original=f"image: {image}:{old_tag}",
                    updated=f"image: {image}:{new_tag}",
                    reason=f"[{ecosystem}] docker-compose image tag updated from {old_tag} → {new_tag}",
                ))
            else:
                new_lines.append(line)

        if changed:
            try:
                compose_file.write_text("".join(new_lines), encoding="utf-8")
            except Exception:
                pass
