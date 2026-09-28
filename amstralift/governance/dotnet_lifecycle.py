"""Microsoft .NET Runtime & TargetFramework Lifecycle Governance.

Fetches and caches official Microsoft .NET release channels from:
https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/releases-index.json

Identifies LTS vs STS channels, support phases (active, maintenance, eol),
and end-of-life dates.
"""

from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel

DOTNET_RELEASES_INDEX_URL = "https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/releases-index.json"

# Fallback release index in case of offline execution or network timeouts
FALLBACK_DOTNET_CHANNELS = [
    {
        "channel-version": "10.0",
        "release-type": "lts",
        "support-phase": "active",
        "eol-date": "2028-11-14",
    },
    {
        "channel-version": "9.0",
        "release-type": "sts",
        "support-phase": "maintenance",
        "eol-date": "2026-11-10",
    },
    {
        "channel-version": "8.0",
        "release-type": "lts",
        "support-phase": "maintenance",
        "eol-date": "2026-11-10",
    },
    {
        "channel-version": "7.0",
        "release-type": "sts",
        "support-phase": "eol",
        "eol-date": "2024-05-14",
    },
    {
        "channel-version": "6.0",
        "release-type": "lts",
        "support-phase": "eol",
        "eol-date": "2024-11-12",
    },
]


class DotNetChannel(BaseModel):
    """Metadata for a .NET major channel."""

    channel_version: str  # e.g. "10.0", "9.0"
    release_type: Literal["lts", "sts"]
    support_phase: str  # "active", "maintenance", "eol", "preview", "go-live"
    eol_date: date | None = None

    @property
    def major(self) -> int:
        return int(self.channel_version.split(".")[0])

    @property
    def is_lts(self) -> bool:
        return self.release_type.lower() == "lts"

    @property
    def is_eol(self) -> bool:
        return self.support_phase.lower() == "eol"


class DotNetLifecycleDecision(BaseModel):
    """Result of .NET lifecycle evaluation."""

    current_tfm: str  # e.g. "net9.0"
    current_major: int
    current_release_type: Literal["lts", "sts"]
    current_support_phase: str
    current_eol_date: date | None
    target_tfm: str  # e.g. "net10.0"
    target_major: int
    target_release_type: Literal["lts", "sts"]
    should_upgrade: bool
    reason: str


class DotNetLifecycleGovernance:
    """Evaluates .NET TargetFramework against official Microsoft lifecycle releases."""

    @classmethod
    def fetch_channels(cls, timeout_seconds: float = 5.0) -> list[DotNetChannel]:
        """Fetch live channels from Microsoft or fallback to known releases."""
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                res = client.get(DOTNET_RELEASES_INDEX_URL)
                if res.status_code == 200:
                    raw = res.json().get("releases-index", [])
                    channels: list[DotNetChannel] = []
                    for item in raw:
                        cv = item.get("channel-version")
                        rt = item.get("release-type", "sts").lower()
                        sp = item.get("support-phase", "active").lower()
                        eol = item.get("eol-date")
                        eol_d = date.fromisoformat(eol) if eol else None
                        if cv and ("." in cv):
                            channels.append(
                                DotNetChannel(
                                    channel_version=cv,
                                    release_type="lts" if rt == "lts" else "sts",
                                    support_phase=sp,
                                    eol_date=eol_d,
                                )
                            )
                    if channels:
                        return channels
        except Exception:
            pass

        # Return fallback channels if live fetch is unavailable
        fallback_channels: list[DotNetChannel] = []
        for item in FALLBACK_DOTNET_CHANNELS:
            eol_d = date.fromisoformat(item["eol-date"]) if item.get("eol-date") else None
            fallback_channels.append(
                DotNetChannel(
                    channel_version=item["channel-version"],
                    release_type=item["release-type"],
                    support_phase=item["support-phase"],
                    eol_date=eol_d,
                )
            )
        return fallback_channels

    @classmethod
    def evaluate_tfm(
        cls,
        tfm: str,
        prefer_lts: bool = True,
        incremental: bool = True,
    ) -> DotNetLifecycleDecision | None:
        """Evaluate a TargetFramework like 'net9.0' and recommend target upgrade."""
        import re

        match = re.match(r"^net(\d+)\.0$", tfm.strip().lower())
        if not match:
            return None

        cur_major = int(match.group(1))
        channels = cls.fetch_channels()
        channel_map = {c.major: c for c in channels}

        cur_channel = channel_map.get(cur_major)
        cur_type = cur_channel.release_type if cur_channel else ("lts" if cur_major % 2 == 0 else "sts")
        cur_phase = cur_channel.support_phase if cur_channel else "active"
        cur_eol = cur_channel.eol_date if cur_channel else None

        # Filter valid upgrade candidate channels (greater than cur_major, not eol, not preview)
        supported_candidates = [
            c for c in channels
            if c.major > cur_major and not c.is_eol and c.support_phase in ("active", "maintenance", "go-live")
        ]

        if not supported_candidates:
            # Check if there is a higher channel even if not yet active
            future_candidates = [c for c in channels if c.major > cur_major]
            if future_candidates:
                supported_candidates = future_candidates

        if not supported_candidates:
            return DotNetLifecycleDecision(
                current_tfm=tfm,
                current_major=cur_major,
                current_release_type=cur_type,
                current_support_phase=cur_phase,
                current_eol_date=cur_eol,
                target_tfm=tfm,
                target_major=cur_major,
                target_release_type=cur_type,
                should_upgrade=False,
                reason=f"Current .NET {cur_major} is at the peak supported version.",
            )

        if incremental:
            # In incremental mode, step to cur_major + 1
            next_major = cur_major + 1
            matching = [c for c in supported_candidates if c.major == next_major]
            chosen = matching[0] if matching else supported_candidates[-1]
        elif prefer_lts:
            # Prefer highest available LTS
            lts_candidates = [c for c in supported_candidates if c.is_lts]
            chosen = lts_candidates[0] if lts_candidates else supported_candidates[0]
        else:
            chosen = supported_candidates[0]

        target_tfm = f"net{chosen.major}.0"
        should_upgrade = chosen.major > cur_major

        status_msg = f".NET {cur_major} ({cur_type.upper()})"
        if cur_phase == "maintenance":
            status_msg += f" is in maintenance (EOL: {cur_eol})"
        elif cur_phase == "eol":
            status_msg += f" is END OF LIFE (EOL: {cur_eol})"

        target_msg = f".NET {chosen.major} ({chosen.release_type.upper()})"
        reason = f"Upgrade from {status_msg} to {target_msg}."

        return DotNetLifecycleDecision(
            current_tfm=tfm,
            current_major=cur_major,
            current_release_type=cur_type,
            current_support_phase=cur_phase,
            current_eol_date=cur_eol,
            target_tfm=target_tfm,
            target_major=chosen.major,
            target_release_type=chosen.release_type,
            should_upgrade=should_upgrade,
            reason=reason,
        )
