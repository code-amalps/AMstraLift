"""Tests for AMstraLift CLI interface."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from amstralift.adapters.angular import AngularAdapter
from amstralift.cli import app
from tests.test_end_to_end_slice import init_mock_angular_repo

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "AMstraLift v" in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Automated Dependency & Framework Upgrade Engine" in result.stdout


def test_cli_run_dry_run(tmp_path: Path):
    repo_path = tmp_path / "angular-cli-repo"
    init_mock_angular_repo(repo_path, test_exit_code=0)

    bundle_output = tmp_path / "bundle.json"

    with patch.object(AngularAdapter, "fetch_latest_version", return_value="18.2.0"):
        result = runner.invoke(
            app,
            [
                "run",
                "--repo",
                str(repo_path),
                "--ecosystem",
                "angular",
                "--dry-run",
                "--output-bundle",
                str(bundle_output),
            ],
        )

    assert result.exit_code == 0
    assert "Upgrade workflow completed successfully" in result.stdout
    assert "Pull Request Proposal (Stage B)" in result.stdout
    assert bundle_output.exists()


def test_cli_rejects_remote_url():
    result = runner.invoke(app, ["run", "--repo", "https://github.com/foo/bar", "--dry-run"])
    assert result.exit_code != 0
    assert "Invalid repository path" in result.stdout
    assert "is a remote Git URL" in result.stdout


def test_cli_rejects_nonexistent_directory(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    result = runner.invoke(app, ["run", "--repo", str(nonexistent), "--dry-run"])
    assert result.exit_code != 0
    assert "Target directory does not exist" in result.stdout


def test_cli_publish_confirmation_declined(tmp_path: Path):
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()
    result = runner.invoke(app, ["run", "--repo", str(repo_path), "--publish"], input="n\n")
    assert result.exit_code == 0
    assert "Remote publishing aborted by user" in result.stdout


def test_cli_handles_already_up_to_date(tmp_path: Path, monkeypatch):
    """Verify that when no upgrades are discovered, CLI exits with 0 and shows friendly message."""
    from amstralift.execution.stage_a import NoUpgradesAvailableError
    from amstralift.service import UpgradeOrchestrator

    def mock_run_upgrade(*args, **kwargs):
        raise NoUpgradesAvailableError("Repository is already up to date.")

    monkeypatch.setattr(UpgradeOrchestrator, "run_upgrade", mock_run_upgrade)

    repo_path = tmp_path / "up-to-date-repo"
    repo_path.mkdir()
    result = runner.invoke(app, ["run", "--repo", str(repo_path), "--dry-run"])
    assert result.exit_code == 0
    assert "All dependencies are already up to date" in result.stdout


def test_cli_run_help_shows_test_options():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--test-timeout" in result.stdout
    assert "--allow-failed-gate" in result.stdout


def test_cli_passes_skip_tests_and_timeout(tmp_path: Path, monkeypatch):
    from amstralift.execution.stage_a import NoUpgradesAvailableError
    from amstralift.service import UpgradeOrchestrator

    captured_kwargs = {}

    def mock_run_upgrade(self, **kwargs):
        captured_kwargs.update(kwargs)
        raise NoUpgradesAvailableError("Mock done")

    monkeypatch.setattr(UpgradeOrchestrator, "run_upgrade", mock_run_upgrade)

    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()

    result = runner.invoke(
        app,
        ["run", "--repo", str(repo_path), "--skip-tests", "--test-timeout", "45", "--dry-run"],
    )
    assert result.exit_code == 0
    assert captured_kwargs.get("allow_failed_gates") is True
    assert captured_kwargs.get("test_timeout") == 45.0


def test_cli_audit_options_forwarding(tmp_path: Path, monkeypatch):
    """Verify that CLI flags --allow-major and --safe-only are forwarded to SecurityOrchestrator."""
    from amstralift.security.models import AuditReport, RemediationResult
    from amstralift.security.orchestrator import SecurityOrchestrator

    captured_kwargs = {}

    def mock_run_remediation(self, **kwargs):
        captured_kwargs.update(kwargs)
        return RemediationResult(
            mode="apply",
            report=AuditReport(repo_path=str(tmp_path), ecosystem="npm"),
            remediation_successful=True,
        )

    monkeypatch.setattr(SecurityOrchestrator, "run_remediation", mock_run_remediation)

    repo_path = tmp_path / "audit-repo"
    repo_path.mkdir()

    result = runner.invoke(
        app,
        ["audit", "--repo", str(repo_path), "--mode", "apply", "--allow-major"],
    )
    assert result.exit_code == 0
    assert captured_kwargs.get("allow_major") is True
    assert captured_kwargs.get("safe_only") is True


def test_suppress_console_skips_cli_commands(monkeypatch):
    from amstralift.cli import suppress_console_for_gui
    import sys

    monkeypatch.setattr(sys, "argv", ["amstralift", "run", "--repo", "."])
    suppress_console_for_gui()


def test_suppress_console_skips_help(monkeypatch):
    from amstralift.cli import suppress_console_for_gui
    import sys

    monkeypatch.setattr(sys, "argv", ["amstralift", "--help"])
    suppress_console_for_gui()


def test_suppress_console_gui_invocation_safe(monkeypatch):
    from amstralift.cli import suppress_console_for_gui
    import sys

    monkeypatch.setattr(sys, "argv", ["amstralift"])
    suppress_console_for_gui()


