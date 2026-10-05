"""Unit and integration tests for React ecosystem adapter."""

import json
from pathlib import Path
from unittest.mock import patch

from amstralift.adapters.react import ReactAdapter, classify_react_tier
from amstralift.core.models import DependencyTier
from amstralift.core.workspace import get_head_commit, run_git
from amstralift.service import UpgradeOrchestrator


def test_classify_react_tier():
    assert classify_react_tier("@auth0/auth0-react") == DependencyTier.TIER_3_CRITICAL
    assert classify_react_tier("@stripe/react-stripe-js") == DependencyTier.TIER_3_CRITICAL
    assert classify_react_tier("react-router-dom") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_react_tier("react") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_react_tier("redux") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_react_tier("react-is") == DependencyTier.TIER_1_SAFE
    assert classify_react_tier("@types/react") == DependencyTier.TIER_1_SAFE


def test_react_adapter_detect(tmp_path: Path):
    adapter = ReactAdapter()
    assert not adapter.detect(tmp_path)

    # Angular project with react dependency should NOT be detected as React
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"@angular/core": "^17.0.0", "react": "18.2.0"}}),
        encoding="utf-8",
    )
    assert not adapter.detect(tmp_path)

    # Pure React project
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18.2.0", "react-dom": "18.2.0"}}),
        encoding="utf-8",
    )
    assert adapter.detect(tmp_path)


def test_react_adapter_apply_upgrade(tmp_path: Path):
    adapter = ReactAdapter()
    pkg_file = tmp_path / "package.json"
    lock_file = tmp_path / "package-lock.json"

    pkg_file.write_text(
        json.dumps(
            {
                "dependencies": {"react": "^18.2.0", "react-router-dom": "^6.20.0"},
            }
        ),
        encoding="utf-8",
    )
    lock_file.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/react": {"version": "18.2.0"},
                }
            }
        ),
        encoding="utf-8",
    )

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="react",
            from_version="^18.2.0",
            to_version="^18.3.1",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        )
    ]

    adapter.apply_upgrade(tmp_path, changes)
    updated_pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    assert updated_pkg["dependencies"]["react"] == "^18.3.1"
    updated_lock = json.loads(lock_file.read_text(encoding="utf-8"))
    assert updated_lock["packages"]["node_modules/react"]["version"] == "18.3.1"


def test_react_end_to_end_workflow(tmp_path: Path):
    """Verify full two-stage upgrade workflow on a React repository."""
    repo_path = tmp_path / "react-repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=repo_path)
    run_git(["config", "user.name", "Test User"], cwd=repo_path)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_path)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_path)

    pkg_data = {
        "name": "mock-react-app",
        "version": "1.0.0",
        "scripts": {
            "test": 'python -c "import sys; sys.exit(0)"',
            "build": 'python -c "import sys; sys.exit(0)"',
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-router-dom": "^6.20.0",
        },
    }
    (repo_path / "package.json").write_text(json.dumps(pkg_data, indent=2) + "\n", encoding="utf-8")
    (repo_path / "package-lock.json").write_text('{"packages": {}}\n', encoding="utf-8")

    src_dir = repo_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "App.jsx").write_text("export default function App() { return <h1>App</h1>; }\n", encoding="utf-8")

    run_git(["add", "."], cwd=repo_path)
    run_git(["commit", "-m", "Initial React app"], cwd=repo_path)
    base_sha = get_head_commit(repo_path)

    secret = b"test-react-secret-key-32b-secret"
    orchestrator = UpgradeOrchestrator(secret_key=secret)

    def mock_fetch(pkg: str, *args, **kwargs):
        versions = {
            "react": "18.3.1",
            "react-router-dom": "6.26.0",
        }
        return versions.get(pkg)

    with patch.object(ReactAdapter, "fetch_latest_version", side_effect=mock_fetch):
        signed_bundle, pr_proposal = orchestrator.run_upgrade(
            repo_path=repo_path,
            ecosystem="react",
            target_branch="main",
            dry_run=False,
        )

    bundle = signed_bundle.bundle
    assert bundle.base_commit_sha == base_sha
    assert bundle.highest_tier == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert "tier-2-verify-behavior" in pr_proposal.labels
    assert "requires-functional-qa" in pr_proposal.labels

    current_head = get_head_commit(repo_path)
    assert current_head != base_sha
    updated_pkg = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
    assert updated_pkg["dependencies"]["react"] == "^18.3.1"


def test_react_never_upgrades_to_unreleased_major(tmp_path: Path):
    """Verify that when React is on peak GA version (e.g. 19.0.0), it never bumps to unreleased React 20."""
    adapter = ReactAdapter(incremental=True)
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        json.dumps({
            "dependencies": {
                "react": "^19.0.0",
                "react-dom": "^19.0.0",
            }
        }),
        encoding="utf-8",
    )

    # When latest released GA version on npm is 19.0.0
    with patch.object(adapter, "fetch_latest_version", return_value="19.0.0"):
        candidates = adapter.discover_candidates(tmp_path)
        # React is already at latest released GA; no candidates to unreleased v20
        react_changes = [c for c in candidates if c.package_name in ("react", "react-dom")]
        assert len(react_changes) == 0


def test_react_fetch_latest_ignores_prerelease():
    """Verify ReactAdapter.fetch_latest_version rejects -rc, -canary, -next pre-releases."""
    adapter = ReactAdapter()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "dist-tags": {
                    "latest": "19.1.0-canary-20261005",
                },
                "versions": {
                    "18.3.1": {},
                    "19.0.0": {},
                    "19.1.0-canary-20261005": {},
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

    with patch("amstralift.adapters.react.httpx.Client", FakeClient):
        ver = adapter.fetch_latest_version("react")
        assert ver == "19.0.0"

