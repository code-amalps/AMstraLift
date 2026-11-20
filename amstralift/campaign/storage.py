"""Storage and executive reporting for Modernization Campaigns (P22).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from amstralift.campaign.models import ModernizationCampaign


class CampaignStore:
    """Manages persistence, loading, and executive report generation for campaigns."""

    DEFAULT_CAMPAIGNS_DIR = Path(".amstralift") / "campaigns"

    @classmethod
    def save(
        cls,
        campaign: ModernizationCampaign,
        file_path: Optional[Path] = None,
        base_dir: Optional[Path] = None,
    ) -> Path:
        """Persist campaign data to disk as JSON."""
        if file_path is None:
            directory = (base_dir / cls.DEFAULT_CAMPAIGNS_DIR) if base_dir else cls.DEFAULT_CAMPAIGNS_DIR
            directory.mkdir(parents=True, exist_ok=True)
            file_path = directory / f"{campaign.campaign_id}.json"
        else:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(campaign.model_dump_json(indent=2), encoding="utf-8")
        return file_path

    @classmethod
    def load(
        cls,
        path_or_id: str | Path,
        base_dir: Optional[Path] = None,
    ) -> ModernizationCampaign:
        """Load campaign from a file path or campaign ID."""
        candidate = Path(path_or_id)
        if candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return ModernizationCampaign(**data)

        # Look in default directory
        directory = (base_dir / cls.DEFAULT_CAMPAIGNS_DIR) if base_dir else cls.DEFAULT_CAMPAIGNS_DIR
        candidate_file = directory / f"{path_or_id}.json"
        if candidate_file.is_file():
            data = json.loads(candidate_file.read_text(encoding="utf-8"))
            return ModernizationCampaign(**data)

        raise FileNotFoundError(f"Campaign not found at path or ID: {path_or_id}")

    @classmethod
    def list_campaigns(cls, base_dir: Optional[Path] = None) -> list[ModernizationCampaign]:
        """List all stored campaigns sorted by updated_at descending."""
        directory = (base_dir / cls.DEFAULT_CAMPAIGNS_DIR) if base_dir else cls.DEFAULT_CAMPAIGNS_DIR
        if not directory.is_dir():
            return []

        campaigns: list[ModernizationCampaign] = []
        for f in directory.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                campaigns.append(ModernizationCampaign(**data))
            except Exception:
                pass

        campaigns.sort(key=lambda c: c.updated_at, reverse=True)
        return campaigns

    @classmethod
    def export_markdown_report(
        cls, campaign: ModernizationCampaign, output_path: Path
    ) -> Path:
        """Generate an executive-ready Markdown campaign report."""
        m = campaign.metrics
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            f"# Modernization Campaign Report: {campaign.name}",
            "",
            f"> **Campaign ID:** `{campaign.campaign_id}` | **Status:** `{campaign.status.value.upper()}` | **Generated:** `{campaign.updated_at[:19].replace('T', ' ')} UTC`",
            "",
            f"**Executive Charter:** {campaign.description or 'Organization-wide modernization campaign.'}",
            "",
            "---",
            "",
            "## Executive Dashboard Metrics",
            "",
            "| Metric | Value | Reference Benchmark |",
            "| :--- | :---: | :--- |",
            f"| **Total Repositories** | **{m.total_repos}** | Participating fleet size |",
            f"| **Fully Modernized** | **{m.modernized_count}** | All verification gates passed (1-6) |",
            f"| **Partially Modernized** | **{m.partial_count}** | Refusal boundaries isolated |",
            f"| **Policy Refusals** | **{m.refused_count}** | Breaking API / compliance blocks |",
            f"| **Verification Failures** | **{m.failed_count}** | Build / regression test breaks |",
            f"| **Upgrade Success Rate** | **{m.upgrade_success_rate}%** | Target >= 90% |",
            f"| **Policy Refusal Rate** | **{m.refusal_rate}%** | Target < 10% |",
            f"| **CVE Reduction** | **{m.cve_reduction_pct}%** | Baseline {m.total_cves_before} $\\to$ Remaining {m.total_cves_after} |",
            f"| **EOL Reduction** | **{m.eol_reduction_pct}%** | Baseline {m.eol_repos_before} $\\to$ Remaining {m.eol_repos_after} |",
            f"| **Engineering Hours Saved** | **{m.engineering_hours_saved:,.1f} hrs** | Person-hours manual dev saved |",
            f"| **Mean Verification Time** | **{m.mean_verification_time_display}** | Average sandbox gate duration |",
            "",
            "---",
            "",
            "## Ecosystem Breakdown",
            "",
            "| Ecosystem | Total | Modernized | Partial | Refused | Failed | CVEs Eliminated | EOL Eliminated | Hours Saved |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]

        for eco, em in sorted(m.ecosystem_breakdown.items()):
            lines.append(
                f"| **{eco.upper()}** | {em.total_repos} | {em.modernized} | {em.partial} | {em.refused} | {em.failed} | {em.cves_eliminated} | {em.eol_eliminated} | {em.hours_saved:,.1f} hrs |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## Participating Repositories",
            "",
            "| Repository | Ecosystem | Starting Ver | Target Ver | Status | Wave | Transforms | CVEs (Bef $\\to$ Aft) | EOL | Runtime |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        for r_id, r in sorted(campaign.repository_records.items()):
            cve_str = f"{r.cves_before} $\\to$ {r.cves_after}"
            eol_str = "Eliminated" if r.is_eol_before and not r.is_eol_after else ("Yes" if r.is_eol_after else "No")
            time_str = f"{r.verification_time_seconds:.1f}s"
            lines.append(
                f"| `{r_id}` | {r.ecosystem.upper()} | {r.initial_version or 'unknown'} | {r.target_version or 'latest'} | `{r.status.value.upper()}` | Wave {r.wave_number} | {r.transforms_applied} | {cve_str} | {eol_str} | {time_str} |"
            )

        lines.extend([
            "",
            "---",
            "",
            f"*Engineered by {campaign.author} for AMstraLift Generation 3 (Enterprise Scale).*",
        ])

        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path
