"""Tests for CI workflow templates, YAML validity, security assertions, and generator CLI."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from amstralift.cli import app
from amstralift.templates.loader import (
    generate_ci_template,
    get_template_content,
)

runner = CliRunner()


def test_github_scheduled_template_syntax_and_security():
    """Verify GitHub Actions scheduled workflow YAML syntax and security invariants."""
    content = get_template_content(provider="github", variant="scheduled")
    data = yaml.safe_load(content)

    assert data["name"] == "AMstraLift Scheduled Dependency Upgrade"

    # Invariant 1: Concurrency control present with cancel-in-progress: false
    assert "concurrency" in data
    assert data["concurrency"]["cancel-in-progress"] is False

    # Invariant 2: Least-privilege permissions explicitly declared
    assert "permissions" in data
    perms = data["permissions"]
    assert perms["contents"] == "write"
    assert perms["pull-requests"] == "write"
    assert perms["issues"] == "write"

    # Invariant 3: Scheduled cron and workflow_dispatch triggers
    triggers = data[True] if True in data else data.get("on", {})
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers

    # Invariant 4: Hard timeout specified on job
    job = data["jobs"]["amstralift-upgrade"]
    assert job["timeout-minutes"] == 30


def test_github_reusable_template_syntax_and_security():
    """Verify GitHub Actions reusable workflow YAML syntax and security invariants."""
    content = get_template_content(provider="github", variant="reusable")
    data = yaml.safe_load(content)

    assert data["name"] == "AMstraLift Reusable Upgrade Workflow"
    triggers = data[True] if True in data else data.get("on", {})
    assert "workflow_call" in triggers

    job = data["jobs"]["upgrade"]
    assert job["timeout-minutes"] == 30
    assert job["permissions"]["contents"] == "write"
    assert job["permissions"]["pull-requests"] == "write"


def test_gitlab_template_syntax():
    """Verify GitLab CI pipeline YAML syntax."""
    content = get_template_content(provider="gitlab")
    data = yaml.safe_load(content)

    assert "stages" in data
    assert "upgrade" in data["stages"]
    assert "amstralift:upgrade" in data
    assert data["amstralift:upgrade"]["stage"] == "upgrade"


def test_generate_ci_template_github(tmp_path: Path):
    """Test generating GitHub workflow files into target repo."""
    out_file = generate_ci_template(tmp_path, provider="github", variant="scheduled")
    assert out_file.exists()
    assert out_file.name == "amstralift-scheduled.yml"
    assert out_file.parent.name == "workflows"
    assert out_file.parent.parent.name == ".github"

    # Fail on collision without overwrite
    with pytest.raises(FileExistsError):
        generate_ci_template(tmp_path, provider="github", variant="scheduled", overwrite=False)

    # Succeed with overwrite
    updated_file = generate_ci_template(tmp_path, provider="github", variant="scheduled", overwrite=True)
    assert updated_file.exists()


def test_generate_ci_template_gitlab(tmp_path: Path):
    """Test generating GitLab CI template file."""
    out_file = generate_ci_template(tmp_path, provider="gitlab")
    assert out_file.exists()
    assert out_file.name == ".gitlab-ci-amstralift.yml"


def test_cli_init_ci_command(tmp_path: Path):
    """Test 'amstralift init-ci' CLI command."""
    res = runner.invoke(app, ["init-ci", "--repo", str(tmp_path), "--provider", "github"])
    assert res.exit_code == 0
    assert "CI template generated successfully" in res.output

    expected_file = tmp_path / ".github" / "workflows" / "amstralift-scheduled.yml"
    assert expected_file.exists()
