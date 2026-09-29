"""Node.js Release Feed — fetches the live LTS schedule from nodejs.org.

Used by DockerfileUpdater to determine which Node.js major version a given
Angular or React version should map to, rather than relying on a hardcoded table.

Data source: https://nodejs.org/dist/index.json (official Node.js release index)
Fallback: hardcoded LTS table below (updated with each AMstraLift release).

Angular → Node mapping source:
  https://angular.dev/reference/releases#actively-supported-versions
  This is fetched via the npm registry dist-tags for @angular/core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# Official data sources
# ─────────────────────────────────────────────────────────────────────────────

NODEJS_SCHEDULE_URL = "https://raw.githubusercontent.com/nodejs/Release/main/schedule.json"
ANGULAR_NPM_URL = "https://registry.npmjs.org/@angular/core"

# ─────────────────────────────────────────────────────────────────────────────
# Fallback tables (used when network is unavailable)
# ─────────────────────────────────────────────────────────────────────────────

# Node.js major → is LTS (True) or Current (False), as of last code review
FALLBACK_NODE_LTS: dict[int, bool] = {
    18: True,   # LTS "Hydrogen" — maintenance
    20: True,   # LTS "Iron" — active
    22: True,   # LTS "Jod" — active
    21: False,  # Current (EOL)
    23: False,  # Current
    24: False,  # Current
}

# Angular major → minimum required Node.js LTS major
# Source: https://angular.dev/reference/releases
FALLBACK_ANGULAR_NODE_MAP: dict[int, int] = {
    12: 12,
    13: 12,
    14: 14,
    15: 14,
    16: 18,
    17: 18,
    18: 18,
    19: 20,
    20: 20,
    21: 20,
    22: 22,
}


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NodeRelease:
    major: int
    is_lts: bool
    lts_codename: str | None   # e.g. "Iron", "Hydrogen"
    start: date | None
    lts_start: date | None
    maintenance_start: date | None
    end: date | None

    @property
    def is_active_lts(self) -> bool:
        today = date.today()
        if not self.is_lts or not self.lts_start or not self.end:
            return False
        return self.lts_start <= today <= self.end

    @property
    def is_eol(self) -> bool:
        if self.end and date.today() > self.end:
            return True
        return False


@dataclass
class NodeLTSResult:
    recommended_major: int
    source: str           # "live" | "fallback"
    all_active_lts: list[int]
    reason: str


# ─────────────────────────────────────────────────────────────────────────────
# Feed fetcher
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except Exception:
        return None


class NodeReleaseFeed:
    """Fetches Node.js LTS release schedule and Angular→Node version mappings."""

    @staticmethod
    def fetch_releases(timeout: float = 5.0) -> list[NodeRelease]:
        """Fetch the Node.js release schedule JSON from GitHub (nodejs/Release)."""
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.get(NODEJS_SCHEDULE_URL)
                if res.status_code == 200:
                    data = res.json()
                    releases: list[NodeRelease] = []
                    for version_str, info in data.items():
                        # Keys look like "v18", "v20"
                        match = re.match(r"^v?(\d+)$", version_str.strip())
                        if not match:
                            continue
                        major = int(match.group(1))
                        lts_val = info.get("lts")
                        is_lts = bool(lts_val)
                        lts_codename = lts_val if isinstance(lts_val, str) else None

                        releases.append(NodeRelease(
                            major=major,
                            is_lts=is_lts,
                            lts_codename=lts_codename,
                            start=_parse_date(info.get("start")),
                            lts_start=_parse_date(info.get("lts") if isinstance(info.get("lts"), str) else None),
                            maintenance_start=_parse_date(info.get("maintenance")),
                            end=_parse_date(info.get("end")),
                        ))
                    if releases:
                        return releases
        except Exception:
            pass
        return []

    @staticmethod
    def recommended_lts_major(
        minimum_major: int | None = None,
        timeout: float = 5.0,
    ) -> NodeLTSResult:
        """
        Return the recommended (highest active) LTS Node.js major.

        Args:
            minimum_major: If set, only consider LTS versions >= this major.
        """
        releases = NodeReleaseFeed.fetch_releases(timeout=timeout)

        if releases:
            today = date.today()
            active_lts = [
                r for r in releases
                if r.is_lts
                and not r.is_eol
                and (r.end is None or r.end > today)
                and (minimum_major is None or r.major >= minimum_major)
            ]

            if active_lts:
                best = max(active_lts, key=lambda r: r.major)
                all_active = sorted({r.major for r in active_lts}, reverse=True)
                return NodeLTSResult(
                    recommended_major=best.major,
                    source="live",
                    all_active_lts=all_active,
                    reason=(
                        f"Live Node.js release schedule: recommended LTS is Node {best.major}"
                        + (f" ({best.lts_codename})" if best.lts_codename else "")
                    ),
                )

        # Fallback
        active_fallback = [
            major for major, is_lts in FALLBACK_NODE_LTS.items()
            if is_lts and (minimum_major is None or major >= minimum_major)
        ]
        if not active_fallback:
            active_fallback = list(FALLBACK_NODE_LTS.keys())

        best_fallback = max(active_fallback)
        return NodeLTSResult(
            recommended_major=best_fallback,
            source="fallback",
            all_active_lts=sorted(active_fallback, reverse=True),
            reason=f"Node.js release feed unavailable. Using fallback LTS: Node {best_fallback}",
        )

    @staticmethod
    def node_for_angular(angular_major: int, timeout: float = 5.0) -> int:
        """
        Return the recommended Node.js LTS major for a given Angular major.

        First checks the hardcoded Angular→Node map (which encodes Angular's
        official peer dependency requirement), then validates that version is
        still an active LTS. If the mapped version is EOL, recommends the next
        active LTS instead.

        Args:
            angular_major: Angular major version, e.g. 18, 19, 20
        """
        # Angular→Node minimum is a peer dependency requirement, not just a preference.
        # We read it from npm @angular/core peerDependencies if possible.
        min_node = _fetch_angular_min_node(angular_major, timeout=timeout)
        if min_node is None:
            # Fallback to hardcoded map
            min_node = FALLBACK_ANGULAR_NODE_MAP.get(angular_major)
            if min_node is None:
                # Unknown Angular version — default to current recommended LTS
                result = NodeReleaseFeed.recommended_lts_major(timeout=timeout)
                return result.recommended_major

        # Find the lowest active LTS >= min_node
        result = NodeReleaseFeed.recommended_lts_major(minimum_major=min_node, timeout=timeout)
        return result.recommended_major


def _fetch_angular_min_node(angular_major: int, timeout: float = 5.0) -> int | None:
    """
    Fetch the minimum required Node.js version for a given Angular major
    from the npm registry peerDependencies of @angular/core.

    Returns the minimum Node major, or None if the lookup fails.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(ANGULAR_NPM_URL)
            if res.status_code != 200:
                return None
            data = res.json()

            # Find any published version matching the requested Angular major
            versions_data = data.get("versions", {})
            matching_versions = [
                v for v in versions_data.keys()
                if v.startswith(f"{angular_major}.")
                and "-" not in v  # exclude prereleases
            ]

            if not matching_versions:
                return None

            # Use the latest matching version
            latest_ver = sorted(matching_versions)[-1]
            peer_deps = versions_data[latest_ver].get("peerDependencies", {})
            node_req = peer_deps.get("node") or peer_deps.get("node.js")

            if not node_req:
                engines = versions_data[latest_ver].get("engines", {})
                node_req = engines.get("node")

            if node_req:
                # Parse ">= 18.13.0" or "^20.0.0" → 18 or 20
                major_match = re.search(r"(\d+)\.", node_req)
                if major_match:
                    return int(major_match.group(1))

    except Exception:
        pass

    return None
