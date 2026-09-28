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

