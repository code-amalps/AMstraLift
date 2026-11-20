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


def test_python_fetch_latest_ignores_prerelease():
    """Verify PythonAdapter.fetch_latest_version rejects pre-release and dev versions from PyPI."""
    adapter = PythonAdapter()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "info": {
                    "version": "2.0.0rc1",  # Latest on info is a pre-release
                },
                "releases": {
                    "1.9.0": [],
                    "1.9.2": [],
                    "2.0.0rc1": [],
                    "2.0.0.dev1": [],
                },
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def get(url):
            return FakeResponse()

    with patch("amstralift.adapters.python.httpx.Client", FakeClient):
        ver = adapter.fetch_latest_version("example-pkg")
        assert ver == "1.9.2"


def test_python_pyproject_toml_parsing_pep621_and_poetry(tmp_path: Path):
    """Verify structured TOML parsing of PEP 621 dependencies, optional-dependencies, and Poetry tables."""
    adapter = PythonAdapter()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
name = "enterprise-api"
requires-python = ">=3.11"
dependencies = [
    "uvicorn[standard]>=0.20.0",
    "pydantic~=2.5.0",
    "requests>=2.28.0"
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "ruff>=0.4.0"
]

[tool.poetry.dependencies]
fastapi = "^0.100.0"
celery = { version = ">=5.3.0", extras = ["redis"] }
""",
        encoding="utf-8",
    )

    # 1. Test get_declared_dependencies
    declared = adapter.get_declared_dependencies(tmp_path)
    assert declared["uvicorn"] == "0.20.0"
    assert declared["pydantic"] == "2.5.0"
    assert declared["requests"] == "2.28.0"
    assert declared["pytest"] == "8.0.0"
    assert declared["ruff"] == "0.4.0"
    assert declared["fastapi"] == "0.100.0"
    assert declared["celery"] == "5.3.0"

    # 2. Test discover_candidates
    mock_versions = {
        "uvicorn": "0.32.0",
        "pydantic": "2.9.2",
        "requests": "2.32.3",
        "pytest": "8.3.3",
        "ruff": "0.7.0",
        "fastapi": "0.115.0",
        "celery": "5.4.0",
    }
    with patch.object(adapter, "fetch_latest_version", side_effect=lambda pkg: mock_versions.get(pkg)):
        candidates = adapter.discover_candidates(tmp_path)

    pkg_candidates = {c.package_name: c for c in candidates}
    assert "uvicorn" in pkg_candidates
    assert pkg_candidates["uvicorn"].to_version == ">=0.32.0"
    assert "pydantic" in pkg_candidates
    assert pkg_candidates["pydantic"].tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert "fastapi" in pkg_candidates
    assert pkg_candidates["fastapi"].to_version == ">=0.115.0"
    assert "celery" in pkg_candidates
    assert pkg_candidates["celery"].tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR


def test_python_pyproject_toml_malformed_syntax_fallback(tmp_path: Path):
    """Verify malformed TOML does not crash adapter and safely falls back to regex matching."""
    adapter = PythonAdapter()
    pyproject = tmp_path / "pyproject.toml"
    # Unclosed bracket and invalid syntax that causes TOMLDecodeError
    pyproject.write_text(
        """[project
dependencies = [
    "requests>=2.28.0",
    "flask>=2.0.0"
""",
        encoding="utf-8",
    )

    mock_versions = {"requests": "2.32.3", "flask": "3.0.3"}
    with patch.object(adapter, "fetch_latest_version", side_effect=lambda pkg: mock_versions.get(pkg)):
        candidates = adapter.discover_candidates(tmp_path)

    pkg_candidates = {c.package_name: c.to_version for c in candidates}
    assert pkg_candidates.get("requests") == ">=2.32.3"
    assert pkg_candidates.get("flask") == ">=3.0.3"

    declared = adapter.get_declared_dependencies(tmp_path)
    assert declared.get("requests") == "2.28.0"
    assert declared.get("flask") == "2.0.0"


def test_python_apply_upgrade_preserves_extras_and_poetry(tmp_path: Path):
    """Verify apply_upgrade preserves PEP 508 extras and updates Poetry dependency sections."""
    adapter = PythonAdapter()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
dependencies = [
    "uvicorn[standard]>=0.20.0",
    'auth0-python[extra]>=4.0.0',
    "requests==2.28.0"
]

[tool.poetry.dependencies]
fastapi = "^0.100.0"
celery = { version = ">=5.3.0", extras = ["redis"] }
""",
        encoding="utf-8",
    )

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="uvicorn",
            from_version=">=0.20.0",
            to_version=">=0.32.0",
            tier=DependencyTier.TIER_1_SAFE,
        ),
        DependencyChange(
            package_name="auth0-python",
            from_version=">=4.0.0",
            to_version=">=4.7.1",
            tier=DependencyTier.TIER_3_CRITICAL,
        ),
        DependencyChange(
            package_name="fastapi",
            from_version="^0.100.0",
            to_version=">=0.115.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="celery",
            from_version=">=5.3.0",
            to_version=">=5.4.0",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)
    updated = pyproject.read_text(encoding="utf-8")
    assert '"uvicorn[standard]>=0.32.0"' in updated
    assert "'auth0-python[extra]>=4.7.1'" in updated
    assert 'fastapi = ">=0.115.0"' in updated
    assert 'celery = { version = ">=5.4.0", extras = ["redis"] }' in updated


def test_python_fetch_latest_canonicalization_and_resilience():
    """Verify PyPI lookup canonicalizes package names (PEP 503) and handles HTTP errors gracefully."""
    adapter = PythonAdapter()
    requested_urls = []

    class MockClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            requested_urls.append(url)
            class Resp:
                status_code = 404
                def json(self): return {}
            if "py-yaml" in url:
                Resp.status_code = 200
                Resp.json = staticmethod(lambda: {"info": {"version": "6.0.2"}, "releases": {"6.0.2": []}})
            return Resp()

    with patch("amstralift.adapters.python.httpx.Client", MockClient):
        # 1. Test canonicalization: "Py_YAML" should be queried as "py-yaml"
        ver = adapter.fetch_latest_version("Py_YAML")
        assert ver == "6.0.2"
        assert any("py-yaml" in u for u in requested_urls)

        # 2. Test 404 error returns None
        assert adapter.fetch_latest_version("nonexistent-pkg-404") is None

