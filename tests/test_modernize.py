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
        # First call: npm install (sync), then 3x schematic phases
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        applied = adapter.apply_modernizations(tmp_path, ["standalone"])

        # Must have exactly 1 success message (Standalone architecture)
        assert any("Standalone" in a for a in applied)
        # subprocess called at least 4 times: npm install + 3 schematic phases
        assert mock_sub.call_count >= 4
        # Verify all 3 modes were invoked
        all_calls = " ".join(str(c) for c in mock_sub.call_args_list)
        assert "convert-to-standalone" in all_calls
        assert "prune-ng-modules" in all_calls
        assert "standalone-bootstrap" in all_calls


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


def test_dotnet_modernize_runs_format_style(tmp_path: Path):
    from amstralift.adapters.dotnet import DotNetAdapter

    adapter = DotNetAdapter()
    with patch("shutil.which", return_value="/bin/dotnet"), \
         patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        applied = adapter.apply_modernizations(tmp_path, ["style"])

        assert len(applied) == 1
        assert "dotnet format style" in applied[0]


def test_python_modernize_runs_ruff_when_available(tmp_path: Path):
    from amstralift.adapters.python import PythonAdapter

    adapter = PythonAdapter()
    with patch("shutil.which", side_effect=lambda x: "/bin/ruff" if x == "ruff" else None), \
         patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        applied = adapter.apply_modernizations(tmp_path, ["syntax"])

        assert len(applied) == 1
        assert "Ruff" in applied[0]


def test_react_modernize_updates_jsx_transform(tmp_path: Path):
    from amstralift.adapters.react import ReactAdapter

    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{\n  "compilerOptions": {\n    "jsx": "react"\n  }\n}', encoding="utf-8")

    adapter = ReactAdapter()
    applied = adapter.apply_modernizations(tmp_path, ["jsx"])

    assert len(applied) == 1
    assert "react-jsx" in applied[0]
    updated_text = tsconfig.read_text(encoding="utf-8")
    assert '"jsx": "react-jsx"' in updated_text


def test_transform_angular_control_flow_directives():
    from amstralift.adapters.angular_control_flow import transform_angular_control_flow

    template = """
    <div *ngIf="user$ | async as user">
      <span>Welcome, {{ user.name }}</span>
    </div>
    <ng-container *ngIf="isAdmin">
      <admin-panel></admin-panel>
    </ng-container>
    <ul>
      <li *ngFor="let item of items; trackBy: customTrack">
        {{ item }}
      </li>
    </ul>
    """
    transformed, count = transform_angular_control_flow(template)
    assert count == 3
    assert "@if (user$ | async; as user)" in transformed
    assert "@if (isAdmin)" in transformed
    assert "@for (let item of items; track customTrack)" in transformed
    assert "<ng-container" not in transformed


def test_angular_modernize_falls_back_when_npx_fails(tmp_path: Path):
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({
        "dependencies": {
            "@angular/core": "^18.2.0",
        }
    }), encoding="utf-8")

    html_file = tmp_path / "test.component.html"
    html_file.write_text('<button *ngIf="isLoggedIn">Logout</button>', encoding="utf-8")

    adapter = AngularAdapter()
    with patch("shutil.which", return_value="/bin/npx"), \
         patch("subprocess.run") as mock_sub:
        # Simulate Angular CLI error (e.g. exit code 127 or schematic missing)
        mock_sub.return_value = MagicMock(returncode=127, stdout="", stderr="Package does not support schematics")

        applied = adapter.apply_modernizations(tmp_path, ["control-flow"])

        assert len(applied) == 1
        assert "Migrated 1 template(s)" in applied[0]
        assert "@if" in applied[0]

        # Verify template on disk was actually migrated
        updated_html = html_file.read_text(encoding="utf-8")
        assert "@if (isLoggedIn)" in updated_html
        assert "*ngIf" not in updated_html


