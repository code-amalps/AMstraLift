"""Unit tests for automated modernization (control-flow, standalone, etc.)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from amstralift.adapters.angular import AngularAdapter
from amstralift.adapters.base import BaseAdapter
from amstralift.core.crypto import compute_sha256, sign_bundle
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
    UnsignedAdvisoryBundle,
)
from amstralift.execution.stage_b import run_stage_b


def test_base_adapter_default_apply_modernizations(tmp_path: Path):
    class DummyAdapter(BaseAdapter):
        @property
        def name(self) -> str:
            return "dummy"

        def detect(self, repo_path: Path) -> bool:
            return True

        def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
            return []

        def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
            pass

        def run_build_and_tests(self, repo_path: Path, timeout_seconds: float = 300.0) -> GateSummary:
            return GateSummary()

    adapter = DummyAdapter()
    result = adapter.apply_modernizations(tmp_path, ["control-flow"])
    assert result == []


def test_angular_modernize_skips_control_flow_below_v17(tmp_path: Path):
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({
        "dependencies": {
            "@angular/core": "^13.4.0",
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    applied = adapter.apply_modernizations(tmp_path, ["control-flow"])
    assert len(applied) == 1
    assert "requires Angular 17+" in applied[0]


def test_angular_modernize_skips_standalone_below_v15(tmp_path: Path):
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({
        "dependencies": {
            "@angular/core": "^12.2.0",
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    applied = adapter.apply_modernizations(tmp_path, ["standalone"])
    assert len(applied) == 1
    assert "requires Angular 15+" in applied[0]


def test_angular_modernize_runs_control_flow_on_v17(tmp_path: Path):
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({
        "dependencies": {
            "@angular/core": "^17.3.0",
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    with patch("shutil.which", return_value="/bin/npx"), \
         patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        applied = adapter.apply_modernizations(tmp_path, ["control-flow"])

        assert len(applied) == 1
        assert "control flow" in applied[0]
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args[0][0]
        assert "@angular/core:control-flow" in cmd


def test_angular_modernize_runs_standalone_on_v17(tmp_path: Path):
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({
        "dependencies": {
            "@angular/core": "^17.3.0",
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    with patch("shutil.which", return_value="/bin/npx"), \
         patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        applied = adapter.apply_modernizations(tmp_path, ["standalone"])

        assert len(applied) == 1
        assert "Standalone" in applied[0]
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args[0][0]
        assert "@angular/core:standalone" in cmd


def test_stage_b_includes_modernizations_in_pr_body(tmp_path: Path):
    patch_text = """diff --git a/package.json b/package.json
index 1111111..2222222 100644
--- a/package.json
+++ b/package.json
@@ -1,3 +1,3 @@
 {
-  "version": "1.0.0"
+  "version": "2.0.0"
 }
"""
    unsigned = UnsignedAdvisoryBundle(
        repo_url="/dummy/repo",
        target_branch="main",
        base_commit_sha="a" * 40,
        head_commit_sha="a" * 40,
        patch=patch_text,
        patch_sha256=compute_sha256(patch_text),
        changes=[
            DependencyChange(
                package_name="@angular/core",
                from_version="16.0.0",
                to_version="17.0.0",
                tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
            )
        ],
        gate_summary=GateSummary(
            results=[
                GateResult(
                    name="build",
                    command="npm run build",
                    status=GateStatus.REQUIRED_PASSED,
                    exit_code=0,
                    duration_seconds=1.2,
                )
            ]
        ),
        modernizations=[
            "Migrated templates to modern Angular control flow (@if, @for, @switch)",
        ],
    )
    secret_key = b"secret" * 8
    signed = sign_bundle(unsigned, secret_key)

    with patch("amstralift.execution.stage_b.is_working_tree_clean", return_value=True), \
         patch("amstralift.execution.stage_b.get_head_commit", return_value="a" * 40), \
         patch("amstralift.execution.stage_b.verify_rediff_integrity"), \
         patch("amstralift.execution.stage_b.get_active_branch", return_value="main"), \
         patch("amstralift.execution.stage_b.run_git") as mock_git, \
         patch("amstralift.execution.stage_b.extract_patch_files", return_value=["package.json"]):

        mock_git.return_value = MagicMock(returncode=0, stdout="", stderr="")

        proposal = run_stage_b(
            signed_bundle=signed,
            target_repo_path=tmp_path,
            secret_key=secret_key,
            dry_run=False,
        )

        assert "Automated Modernizations Applied" in proposal.body
        assert "@if, @for, @switch" in proposal.body
