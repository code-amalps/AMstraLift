"""Unit tests for Modernization Campaign Engine (P22).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amstralift.campaign import (
    CampaignDashboardMetrics,
    CampaignEngine,
    CampaignMetricsCalculator,
    CampaignStatus,
    CampaignStore,
    EcosystemCampaignMetrics,
    ModernizationCampaign,
    RepoCampaignRecord,
    RepoCampaignStatus,
)
from amstralift.cli import app
from tests.test_multirepo import mock_fleet_dir

runner = CliRunner()


# ==============================================================================
# Model & Metrics Calculator Tests
# ==============================================================================


def test_campaign_models_instantiation():
    """Verify campaign records and models."""
    record = RepoCampaignRecord(
        repo_id="repo-portal",
        repo_path="/path/portal",
        ecosystem="angular",
        initial_version="15.0.0",
        target_version="18",
        status=RepoCampaignStatus.MODERNIZED,
        wave_number=1,
        cves_before=10,
        cves_after=0,
        is_eol_before=True,
        is_eol_after=False,
        verification_time_seconds=263.0,
        transforms_applied=14,
    )
    assert record.repo_id == "repo-portal"
    assert record.status == RepoCampaignStatus.MODERNIZED

    campaign = ModernizationCampaign(
        name="Q1 Modernization",
        repository_records={"repo-portal": record},
    )
    assert campaign.name == "Q1 Modernization"
    assert "repo-portal" in campaign.repository_records


def test_metrics_calculator_exact_benchmarks():
    """Verify exact formula metrics matching roadmap requirements."""
    # Create 10 mock records: 7 modernized, 2 partial, 1 refused
    records: dict[str, RepoCampaignRecord] = {}

    for i in range(7):
        records[f"repo-mod-{i}"] = RepoCampaignRecord(
            repo_id=f"repo-mod-{i}",
            repo_path=f"/path/{i}",
            ecosystem="angular" if i < 4 else "dotnet",
            initial_version="15.0.0",
            target_version="18",
            status=RepoCampaignStatus.MODERNIZED,
            cves_before=10,
            cves_after=1,
            is_eol_before=True,
            is_eol_after=False,
            verification_time_seconds=200.0,
            transforms_applied=8,
        )

    for i in range(2):
        records[f"repo-part-{i}"] = RepoCampaignRecord(
            repo_id=f"repo-part-{i}",
            repo_path=f"/path/p{i}",
            ecosystem="react",
            initial_version="16.8.0",
            target_version="18",
            status=RepoCampaignStatus.PARTIAL,
            cves_before=12,
            cves_after=3,
            is_eol_before=True,
            is_eol_after=False,
            verification_time_seconds=300.0,
            transforms_applied=5,
        )

    records["repo-refused-0"] = RepoCampaignRecord(
        repo_id="repo-refused-0",
        repo_path="/path/refused",
        ecosystem="angular",
        initial_version="12.0.0",
        target_version="18",
        status=RepoCampaignStatus.REFUSED,
        cves_before=6,
        cves_after=6,
        is_eol_before=True,
        is_eol_after=True,
        verification_time_seconds=10.0,
        transforms_refused=10,
    )

    metrics = CampaignMetricsCalculator.calculate(records)

    assert metrics.total_repos == 10
    assert metrics.modernized_count == 7
    assert metrics.partial_count == 2
    assert metrics.refused_count == 1
    assert metrics.failed_count == 0

    # Success rate: (7 + 2) / 10 = 90.0%
    assert metrics.upgrade_success_rate == 90.0
    # Refusal rate: 1 / 10 = 10.0%
    assert metrics.refusal_rate == 10.0

    # Baseline CVEs: 7*10 + 2*12 + 6 = 70 + 24 + 6 = 100
    # After CVEs: 7*1 + 2*3 + 6 = 7 + 6 + 6 = 19
    # Reduction: (19 - 100) / 100 = -81.0%
    assert metrics.total_cves_before == 100
    assert metrics.total_cves_after == 19
    assert metrics.cve_reduction_pct == -81.0

    # EOL reduction: 10 EOL initially -> 1 remaining EOL = -90.0%
    assert metrics.eol_repos_before == 10
    assert metrics.eol_repos_after == 1
    assert metrics.eol_reduction_pct == -90.0

    # Verify hours saved is positive and non-zero
    assert metrics.engineering_hours_saved > 200.0

    # Ecosystem breakdown
    assert "angular" in metrics.ecosystem_breakdown
    assert "react" in metrics.ecosystem_breakdown
    assert "dotnet" in metrics.ecosystem_breakdown
    assert metrics.ecosystem_breakdown["angular"].total_repos == 5


def test_format_duration():
    """Verify human duration formatter."""
    assert CampaignMetricsCalculator.format_duration(0) == "0s"
    assert CampaignMetricsCalculator.format_duration(45) == "45s"
    assert CampaignMetricsCalculator.format_duration(263) == "4m 23s"
    assert CampaignMetricsCalculator.format_duration(3600) == "60m 00s"


# ==============================================================================
# Engine Tests
# ==============================================================================


def test_campaign_init_engine(mock_fleet_dir: Path):
    """Verify campaign initialization from fleet directory."""
    campaign = CampaignEngine.init_campaign(
        name="Q1 2027 Angular Modernization",
        fleet_root=mock_fleet_dir,
        description="Comprehensive organization-wide migration",
        targets={"angular": 18, "react": 19, "dotnet": 9, "python": 3},
    )

    assert campaign.name == "Q1 2027 Angular Modernization"
    assert campaign.status == CampaignStatus.DRAFT
    assert len(campaign.repository_records) == 5
    assert "repo-shared-lib" in campaign.repository_records
    assert campaign.repository_records["repo-shared-lib"].wave_number == 1
    assert campaign.repository_records["repo-service-a"].wave_number == 2


def test_campaign_run_simulation(mock_fleet_dir: Path):
    """Verify campaign execution advances status, waves, and metrics."""
    campaign = CampaignEngine.init_campaign(
        name="Simulation Campaign",
        fleet_root=mock_fleet_dir,
        targets={"angular": 18, "react": 19, "dotnet": 9, "python": 3},
    )

    executed = CampaignEngine.run_campaign(campaign, dry_run=True)

    assert executed.status == CampaignStatus.COMPLETED
    assert executed.metrics.total_repos == 5
    assert executed.metrics.modernized_count >= 4
    assert executed.metrics.cve_reduction_pct <= 0.0
    assert executed.metrics.engineering_hours_saved > 0.0


# ==============================================================================
# Storage & Reporting Tests
# ==============================================================================


def test_campaign_store_save_load(mock_fleet_dir: Path):
    """Verify saving and loading campaigns from disk."""
    campaign = CampaignEngine.init_campaign(
        name="Persistence Test",
        fleet_root=mock_fleet_dir,
    )

    save_path = CampaignStore.save(campaign, base_dir=mock_fleet_dir)
    assert save_path.is_file()

    loaded = CampaignStore.load(campaign.campaign_id, base_dir=mock_fleet_dir)
    assert loaded.campaign_id == campaign.campaign_id
    assert loaded.name == "Persistence Test"

    all_campaigns = CampaignStore.list_campaigns(base_dir=mock_fleet_dir)
    assert len(all_campaigns) >= 1


def test_campaign_markdown_report_export(mock_fleet_dir: Path):
    """Verify executive markdown report generation."""
    campaign = CampaignEngine.init_campaign(
        name="Report Test Campaign",
        fleet_root=mock_fleet_dir,
    )
    CampaignEngine.run_campaign(campaign, dry_run=True)

    report_path = mock_fleet_dir / "test-report.md"
    out = CampaignStore.export_markdown_report(campaign, report_path)
    assert out.is_file()

    content = out.read_text(encoding="utf-8")
    assert "# Modernization Campaign Report: Report Test Campaign" in content
    assert "Executive Dashboard Metrics" in content
    assert "Ecosystem Breakdown" in content
    assert "repo-shared-lib" in content
    assert "Amal P S" in content


# ==============================================================================
# CLI Tests
# ==============================================================================


def test_cli_campaign_init(mock_fleet_dir: Path):
    """Verify amstralift campaign init command."""
    res = runner.invoke(
        app,
        [
            "campaign",
            "init",
            "--dir",
            str(mock_fleet_dir),
            "--name",
            "CLI Enterprise Campaign",
        ],
    )
    assert res.exit_code == 0
    assert "Modernization Campaign Initialized" in res.stdout
    assert "CLI Enterprise Campaign" in res.stdout


def test_cli_campaign_init_json(mock_fleet_dir: Path):
    """Verify amstralift campaign init --json command."""
    res = runner.invoke(
        app,
        [
            "campaign",
            "init",
            "--dir",
            str(mock_fleet_dir),
            "--name",
            "JSON Campaign",
            "--json",
        ],
    )
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["name"] == "JSON Campaign"
    assert "repository_records" in data


def test_cli_campaign_run(mock_fleet_dir: Path):
    """Verify amstralift campaign run command."""
    res = runner.invoke(app, ["campaign", "run", "--dir", str(mock_fleet_dir)])
    assert res.exit_code == 0
    assert "Modernization Campaign Dashboard" in res.stdout
    assert "Ecosystem Breakdown" in res.stdout


def test_cli_campaign_status(mock_fleet_dir: Path):
    """Verify amstralift campaign status command."""
    # Ensure campaign is initialized and run first
    runner.invoke(app, ["campaign", "run", "--dir", str(mock_fleet_dir)])
    res = runner.invoke(app, ["campaign", "status", "--dir", str(mock_fleet_dir)])
    assert res.exit_code == 0
    assert "Modernization Campaign Status" in res.stdout
    assert "Repository Progress Overview" in res.stdout


def test_cli_campaign_report(mock_fleet_dir: Path):
    """Verify amstralift campaign report command."""
    runner.invoke(app, ["campaign", "run", "--dir", str(mock_fleet_dir)])
    rep_file = mock_fleet_dir / "campaign-report.md"
    res = runner.invoke(
        app,
        ["campaign", "report", "--dir", str(mock_fleet_dir), "-o", str(rep_file)],
    )
    assert res.exit_code == 0
    assert "Executive campaign report exported successfully" in res.stdout
    assert rep_file.is_file()
