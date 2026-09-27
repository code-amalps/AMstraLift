"""Unit and integration tests for Python ecosystem adapter."""

from pathlib import Path
from unittest.mock import patch

from amstralift.adapters.python import PythonAdapter, classify_python_tier
from amstralift.core.models import DependencyTier
from amstralift.core.workspace import get_head_commit, run_git
from amstralift.service import UpgradeOrchestrator


def test_classify_python_tier():
    assert classify_python_tier("cryptography") == DependencyTier.TIER_3_CRITICAL
    assert classify_python_tier("pyjwt") == DependencyTier.TIER_3_CRITICAL
    assert classify_python_tier("fastapi") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_python_tier("sqlalchemy") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_python_tier("ruff") == DependencyTier.TIER_1_SAFE
    assert classify_python_tier("requests") == DependencyTier.TIER_1_SAFE


def test_python_adapter_detect(tmp_path: Path):
    adapter = PythonAdapter()
    assert not adapter.detect(tmp_path)

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    assert adapter.detect(tmp_path)


def test_python_adapter_apply_upgrade_pyproject(tmp_path: Path):
    adapter = PythonAdapter()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-service"\ndependencies = [\n    "fastapi>=0.100.0",\n    "ruff==0.1.0"\n]\n',
        encoding="utf-8",
    )

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="fastapi",
            from_version=">=0.100.0",
            to_version=">=0.115.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="ruff",
            from_version="==0.1.0",
            to_version=">=0.4.0",
            tier=DependencyTier.TIER_1_SAFE,
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)
    updated = pyproject.read_text(encoding="utf-8")
    assert '"fastapi>=0.115.0"' in updated
    assert '"ruff>=0.4.0"' in updated


def test_python_adapter_apply_upgrade_requirements(tmp_path: Path):
    adapter = PythonAdapter()
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("fastapi>=0.100.0\nrequests==2.28.0\n", encoding="utf-8")

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="requests",
            from_version="==2.28.0",
            to_version=">=2.32.0",
            tier=DependencyTier.TIER_1_SAFE,
        )
    ]

    adapter.apply_upgrade(tmp_path, changes)
    updated = req_file.read_text(encoding="utf-8")
    assert "requests>=2.32.0" in updated
    assert "fastapi>=0.100.0" in updated


def test_python_end_to_end_workflow(tmp_path: Path):
    """Verify full two-stage upgrade workflow on a Python repository."""
    repo_path = tmp_path / "python-repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=repo_path)
    run_git(["config", "user.name", "Test User"], cwd=repo_path)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_path)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_path)

    (repo_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")

    pyproject_content = (
        "[project]\n"
        "name = 'mock-python-service'\n"
        "version = '0.1.0'\n"
        "dependencies = [\n"
        "    'cryptography>=41.0.0',\n"
        "    'requests>=2.28.0'\n"
        "]\n"
    )
    (repo_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

    # Add a mock test file
    test_dir = repo_path / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    run_git(["add", "."], cwd=repo_path)
    run_git(["commit", "-m", "Initial Python repo"], cwd=repo_path)
    base_sha = get_head_commit(repo_path)

    secret = b"test-secret-key-python-orchestrator"
    orchestrator = UpgradeOrchestrator(secret_key=secret)

    def mock_fetch(pkg: str):
        versions = {
            "cryptography": "43.0.1",
            "requests": "2.32.3",
        }
        return versions.get(pkg)

    with patch.object(PythonAdapter, "fetch_latest_version", side_effect=mock_fetch):
        signed_bundle, pr_proposal = orchestrator.run_upgrade(
            repo_path=repo_path,
            ecosystem="python",
            target_branch="main",
            dry_run=False,
        )

    bundle = signed_bundle.bundle
    assert bundle.base_commit_sha == base_sha
    assert bundle.highest_tier == DependencyTier.TIER_3_CRITICAL  # Because of cryptography
    assert "tier-3-critical" in pr_proposal.labels
    assert "requires-manual-security-verification" in pr_proposal.labels
    assert pr_proposal.tier == DependencyTier.TIER_3_CRITICAL

    # Verify working tree updated and committed
    current_head = get_head_commit(repo_path)
    assert current_head != base_sha
    updated_pyproject = (repo_path / "pyproject.toml").read_text(encoding="utf-8")
    assert "cryptography>=43.0.1" in updated_pyproject
