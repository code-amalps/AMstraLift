"""Deterministic Metrics and Scorecard Calculator for Campaigns (P22).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import math
from typing import Mapping

from amstralift.campaign.models import (
    CampaignDashboardMetrics,
    EcosystemCampaignMetrics,
    RepoCampaignRecord,
    RepoCampaignStatus,
)


class CampaignMetricsCalculator:
    """Calculates organization-wide modernization KPIs, reduction percentages, and hours saved."""

    # Industry standard human person-hour benchmarks for manual upgrades
    HOURS_PER_MAJOR_UPGRADE = 24.0
    HOURS_PER_AST_TRANSFORM = 0.5
    HOURS_PER_CVE_REMEDIATED = 4.0
    HOURS_PER_VERIFICATION_GATE = 2.0

    @classmethod
    def calculate(
        cls, records: Mapping[str, RepoCampaignRecord]
    ) -> CampaignDashboardMetrics:
        """Compute comprehensive dashboard metrics across all repository records."""
        total_repos = len(records)
        if total_repos == 0:
            return CampaignDashboardMetrics()

        modernized = 0
        partial = 0
        refused = 0
        failed = 0
        pending = 0

        total_cves_before = 0
        total_cves_after = 0

        eol_before = 0
        eol_after = 0

        total_verification_time = 0.0
        verified_count = 0
        total_hours_saved = 0.0

        # Ecosystem breakdown buckets
        eco_buckets: dict[str, list[RepoCampaignRecord]] = {}

        for r in records.values():
            eco = r.ecosystem.lower().strip() or "unknown"
            eco_buckets.setdefault(eco, []).append(r)

            if r.status == RepoCampaignStatus.MODERNIZED:
                modernized += 1
            elif r.status == RepoCampaignStatus.PARTIAL:
                partial += 1
            elif r.status == RepoCampaignStatus.REFUSED:
                refused += 1
            elif r.status == RepoCampaignStatus.FAILED:
                failed += 1
            elif r.status == RepoCampaignStatus.PENDING:
                pending += 1

            total_cves_before += r.cves_before
            total_cves_after += r.cves_after

            if r.is_eol_before:
                eol_before += 1
            if r.is_eol_after:
                eol_after += 1

            if r.verification_time_seconds > 0:
                total_verification_time += r.verification_time_seconds
                verified_count += 1

            # Calculate engineering hours saved for successful/partial work
            if r.status in (RepoCampaignStatus.MODERNIZED, RepoCampaignStatus.PARTIAL):
                # Major upgrade base saving
                repo_savings = cls.HOURS_PER_MAJOR_UPGRADE if r.status == RepoCampaignStatus.MODERNIZED else (cls.HOURS_PER_MAJOR_UPGRADE * 0.5)
                # AST transform savings
                repo_savings += r.transforms_applied * cls.HOURS_PER_AST_TRANSFORM
                # CVE fix savings
                cve_diff = max(0, r.cves_before - r.cves_after)
                repo_savings += cve_diff * cls.HOURS_PER_CVE_REMEDIATED
                # Automated verification savings
                repo_savings += cls.HOURS_PER_VERIFICATION_GATE
                total_hours_saved += repo_savings

        evaluated_repos = modernized + partial + refused + failed

        # Success rate = (modernized + partial) / evaluated_repos * 100
        upgrade_success_rate = (
            ((modernized + partial) / evaluated_repos * 100.0)
            if evaluated_repos > 0
            else 0.0
        )

        # Refusal rate = refused / evaluated_repos * 100
        refusal_rate = (
            (refused / evaluated_repos * 100.0) if evaluated_repos > 0 else 0.0
        )

        # CVE reduction percentage (e.g. -87.0%)
        if total_cves_before > 0:
            cve_reduction_pct = round(((total_cves_after - total_cves_before) / total_cves_before) * 100.0, 1)
        else:
            cve_reduction_pct = 0.0

        # EOL reduction percentage (e.g. -94.0%)
        if eol_before > 0:
            eol_reduction_pct = round(((eol_after - eol_before) / eol_before) * 100.0, 1)
        else:
            eol_reduction_pct = 0.0

        # Mean verification time
        mean_ver_sec = (total_verification_time / verified_count) if verified_count > 0 else 0.0
        mean_ver_display = cls.format_duration(mean_ver_sec)

        # Calculate ecosystem-level breakdown
        breakdown: dict[str, EcosystemCampaignMetrics] = {}
        for eco_name, eco_records in eco_buckets.items():
            e_total = len(eco_records)
            e_mod = sum(1 for x in eco_records if x.status == RepoCampaignStatus.MODERNIZED)
            e_part = sum(1 for x in eco_records if x.status == RepoCampaignStatus.PARTIAL)
            e_ref = sum(1 for x in eco_records if x.status == RepoCampaignStatus.REFUSED)
            e_fail = sum(1 for x in eco_records if x.status == RepoCampaignStatus.FAILED)
            e_pen = sum(1 for x in eco_records if x.status == RepoCampaignStatus.PENDING)
            e_cve_elim = sum(max(0, x.cves_before - x.cves_after) for x in eco_records)
            e_eol_elim = sum(1 for x in eco_records if x.is_eol_before and not x.is_eol_after)

            e_hours = 0.0
            for x in eco_records:
                if x.status in (RepoCampaignStatus.MODERNIZED, RepoCampaignStatus.PARTIAL):
                    base = cls.HOURS_PER_MAJOR_UPGRADE if x.status == RepoCampaignStatus.MODERNIZED else (cls.HOURS_PER_MAJOR_UPGRADE * 0.5)
                    e_hours += base + (x.transforms_applied * cls.HOURS_PER_AST_TRANSFORM) + (max(0, x.cves_before - x.cves_after) * cls.HOURS_PER_CVE_REMEDIATED) + cls.HOURS_PER_VERIFICATION_GATE

            breakdown[eco_name] = EcosystemCampaignMetrics(
                ecosystem=eco_name,
                total_repos=e_total,
                modernized=e_mod,
                partial=e_part,
                refused=e_ref,
                failed=e_fail,
                pending=e_pen,
                cves_eliminated=e_cve_elim,
                eol_eliminated=e_eol_elim,
                hours_saved=round(e_hours, 1),
            )

        return CampaignDashboardMetrics(
            total_repos=total_repos,
            modernized_count=modernized,
            partial_count=partial,
            refused_count=refused,
            failed_count=failed,
            pending_count=pending,
            total_cves_before=total_cves_before,
            total_cves_after=total_cves_after,
            cve_reduction_pct=cve_reduction_pct,
            eol_repos_before=eol_before,
            eol_repos_after=eol_after,
            eol_reduction_pct=eol_reduction_pct,
            engineering_hours_saved=round(total_hours_saved, 1),
            upgrade_success_rate=round(upgrade_success_rate, 1),
            refusal_rate=round(refusal_rate, 1),
            mean_verification_time_seconds=round(mean_ver_sec, 1),
            mean_verification_time_display=mean_ver_display,
            ecosystem_breakdown=breakdown,
        )

    @classmethod
    def format_duration(cls, seconds: float) -> str:
        """Format seconds into readable 'Xm Ys' or 'Xs' format."""
        if seconds <= 0:
            return "0s"
        total_sec = int(round(seconds))
        minutes = total_sec // 60
        rem_sec = total_sec % 60
        if minutes > 0:
            return f"{minutes}m {rem_sec:02d}s"
        return f"{rem_sec}s"
