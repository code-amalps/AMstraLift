"""Tests for Angular ecosystem adapter."""

import json
from pathlib import Path

from amstralift.adapters.angular import AngularAdapter, classify_angular_tier
from amstralift.core.models import DependencyTier


def test_classify_angular_tier():
    # Tier 3 (Critical / Auth / Security / Payments)
    assert classify_angular_tier("@angular/fire/auth") == DependencyTier.TIER_3_CRITICAL
    assert classify_angular_tier("msal-angular") == DependencyTier.TIER_3_CRITICAL
    assert classify_angular_tier("stripe-angular") == DependencyTier.TIER_3_CRITICAL

    # Tier 2 (State / Routing / Core behavior)
    assert classify_angular_tier("@angular/router") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_angular_tier("@angular/forms") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_angular_tier("@ngrx/store") == DependencyTier.TIER_2_VERIFY_BEHAVIOR

    # Tier 1 (Safe utilities / dev tools)
    assert classify_angular_tier("tslib") == DependencyTier.TIER_1_SAFE
    assert classify_angular_tier("lodash") == DependencyTier.TIER_1_SAFE


def test_angular_adapter_detect(tmp_path: Path):
    adapter = AngularAdapter()

    # Empty repo -> False
    assert not adapter.detect(tmp_path)

    # Repo with package.json but not Angular -> False
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "4.18.0"}}), encoding="utf-8")
    assert not adapter.detect(tmp_path)

    # Repo with @angular/core -> True
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"@angular/core": "^17.0.0"}}), encoding="utf-8")
    assert adapter.detect(tmp_path)


def test_fetch_latest_version_uses_highest_stable_semver_in_major(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "versions": {
                    "1.9.0": {},
                    "1.10.0": {},
                    "1.10.0-rc.1": {},
                    "1.10.2": {},
                    "2.0.0": {},
                }
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

    monkeypatch.setattr("amstralift.adapters.angular.httpx.Client", FakeClient)

    assert AngularAdapter().fetch_latest_version("example", target_major=1) == "1.10.2"


def test_latest_angular_target_uses_supported_typescript_major(tmp_path: Path, monkeypatch):
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(
        json.dumps(
            {
                "dependencies": {"@angular/core": "~12.2.6"},
                "devDependencies": {"typescript": "~4.2.4"},
            }
        ),
        encoding="utf-8",
    )
    adapter = AngularAdapter()

    def fetch_version(package_name: str, target_major: int | None = None) -> str | None:
        return {
            "@angular/core": "22.2.0",
            "typescript": "7.0.2",
        }.get(package_name)

    monkeypatch.setattr(adapter, "fetch_latest_version", fetch_version)

    candidates = adapter.discover_candidates(tmp_path)
    typescript = next(change for change in candidates if change.package_name == "typescript")

    assert typescript.to_version == "^6.0.3"
    assert "Angular 22 compatibility" in typescript.rationale


def test_angular_adapter_apply_upgrade(tmp_path: Path, monkeypatch):
    adapter = AngularAdapter()
    pkg_path = tmp_path / "package.json"
    lock_path = tmp_path / "package-lock.json"

    pkg_path.write_text(
        json.dumps(
            {
                "dependencies": {"@angular/core": "^17.0.0"},
                "devDependencies": {"typescript": "^5.2.0"},
            }
        ),
        encoding="utf-8",
    )
    lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@angular/core": {"version": "17.0.0"},
                }
            }
        ),
        encoding="utf-8",
    )

    from amstralift.core.models import DependencyChange

    monkeypatch.setattr(
        "amstralift.adapters.npm_lockfile.install_npm_dependencies",
        lambda *args, **kwargs: None,
    )

    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="^17.0.0",
            to_version="^18.1.0",
            change_type="direct",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        )
    ]

    adapter.apply_upgrade(tmp_path, changes)

    updated_pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert updated_pkg["dependencies"]["@angular/core"] == "^18.1.0"

    updated_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert updated_lock["packages"]["node_modules/@angular/core"]["version"] == "18.1.0"


def test_resolve_angular_test_command(tmp_path: Path):
    adapter = AngularAdapter()

    # 1. Karma / ng test script with configuration -> injects --watch=false --browsers=ChromeHeadless
    script = "npm run lint && ng test --configuration=test"
    cmd = adapter._resolve_angular_test_command(script, tmp_path, has_npm=True)
    assert "--watch=false" in cmd
    assert "--browsers=ChromeHeadless" in cmd
    assert "npm test --" in cmd

    # 2. Already headless karma.conf.js in a subproject
    subproject_dir = tmp_path / "projects" / "my-app"
    subproject_dir.mkdir(parents=True)
    (subproject_dir / "karma.conf.js").write_text("browsers: ['ChromeHeadless']", encoding="utf-8")

    cmd2 = adapter._resolve_angular_test_command("ng test", tmp_path, has_npm=True)
    assert "--watch=false" in cmd2
    # Should not duplicate --browsers if already ChromeHeadless in karma.conf.js
    assert cmd2.count("--browsers=ChromeHeadless") <= 1

    # 3. Jest script -> kept as npm run test without Karma flags
    jest_cmd = adapter._resolve_angular_test_command("jest --coverage", tmp_path, has_npm=True)
    assert jest_cmd == "npm run test"

    # 4. Vitest -> injects --run
    vitest_cmd = adapter._resolve_angular_test_command("vitest", tmp_path, has_npm=True)
    assert "--run" in vitest_cmd


def test_angular_run_build_and_tests_timeout_handling(tmp_path: Path, monkeypatch):
    import subprocess

    from amstralift.core.models import GateStatus

    adapter = AngularAdapter()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "ng test", "build": "ng build"}}),
        encoding="utf-8",
    )

    def mock_subprocess_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    summary = adapter.run_build_and_tests(tmp_path, timeout_seconds=10.0)
    test_result = next((r for r in summary.results if r.name == "test"), None)
    assert test_result is not None
    assert test_result.status == GateStatus.REQUIRED_TIMEOUT
    assert test_result.exit_code == 124
    assert "timed out" in test_result.stdout.lower()
    assert summary.has_required_timeouts
    assert not summary.has_required_failures  # Timeout is classified as UNCERTAIN, not confirmed broken


def test_modernize_angular_workspace_json(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_workspace_json

    workspace_file = tmp_path / "angular.json"
    workspace_file.write_text(
        json.dumps(
            {
                "version": 1,
                "defaultProject": "legacy-app",
                "projects": {
                    "legacy-app": {
                        "architect": {
                            "serve": {
                                "options": {"browserTarget": "legacy-app:build"},
                                "configurations": {
                                    "production": {"browserTarget": "legacy-app:build:production"}
                                },
                            },
                            "extract-i18n": {
                                "options": {"browserTarget": "legacy-app:build"}
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    _modernize_angular_workspace_json(tmp_path, target_major=17)

    updated = json.loads(workspace_file.read_text(encoding="utf-8"))
    assert "defaultProject" not in updated
    serve_opts = updated["projects"]["legacy-app"]["architect"]["serve"]["options"]
    assert "buildTarget" in serve_opts
    assert serve_opts["buildTarget"] == "legacy-app:build"
    assert "browserTarget" not in serve_opts
    serve_prod = updated["projects"]["legacy-app"]["architect"]["serve"]["configurations"]["production"]
    assert serve_prod["buildTarget"] == "legacy-app:build:production"
    assert "browserTarget" not in serve_prod
    i18n_opts = updated["projects"]["legacy-app"]["architect"]["extract-i18n"]["options"]
    assert i18n_opts["buildTarget"] == "legacy-app:build"


def test_modernize_angular_tsconfig(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_tsconfig

    tsconfig_file = tmp_path / "tsconfig.json"
    tsconfig_file.write_text(
        """// Leading tsconfig comment
{
  "compileOnSave": false,
  "compilerOptions": {
    "baseUrl": "./src",
    "moduleResolution": "node",
    "module": "esnext",
    "target": "es2015",
    "lib": ["es2018", "dom"],
  },
  "angularCompilerOptions": {
    "strictTemplates": true,
    "fullTemplateTypeCheck": true
  }
}
""",
        encoding="utf-8",
    )

    applied = _modernize_angular_tsconfig(tmp_path, target_major=22)
    assert len(applied) >= 1

    updated = json.loads(tsconfig_file.read_text(encoding="utf-8"))
    opts = updated["compilerOptions"]
    assert opts["moduleResolution"] == "bundler"
    assert opts["target"] == "ES2022"
    assert opts["lib"] == ["ES2022", "dom"]
    assert opts["useDefineForClassFields"] is False
    assert opts["ignoreDeprecations"] == "6.0"
    assert "fullTemplateTypeCheck" not in updated.get("angularCompilerOptions", {})
    assert updated["angularCompilerOptions"]["strictTemplates"] is True


def test_modernize_angular_stylesheets(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_stylesheets

    scss_file = tmp_path / "styles.scss"
    scss_file.write_text(
        """@use '~@angular/material' as mat;
@import 'styles-variables';
@import '~bootstrap/scss/bootstrap-reboot';
@import '~bootstrap/scss/bootstrap-grid';
@import url('~font-awesome/css/font-awesome.css');
""",
        encoding="utf-8",
    )

    applied = _modernize_angular_stylesheets(tmp_path)
    assert len(applied) == 1
    assert "Modernized 1 stylesheet(s)" in applied[0]

    updated = scss_file.read_text(encoding="utf-8")
    assert "@use '@angular/material' as mat;" in updated
    assert "@import 'styles-variables';" in updated
    assert "@import 'bootstrap/scss/bootstrap-reboot';" in updated
    assert "@import 'bootstrap/scss/bootstrap-grid';" in updated
    assert "@import url('font-awesome/css/font-awesome.css');" in updated
    assert "~" not in updated


def test_modernize_angular_source_files(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_source_files

    # 1. Obsolete Effect from @ngrx/effects
    effects_file = tmp_path / "sample.effects.ts"
    effects_file.write_text(
        """import { Injectable } from '@angular/core';
import { Actions, Effect, ofType } from '@ngrx/effects';

@Injectable()
export class SampleEffects {
  @Effect()
  loadSomething$ = this.actions$.pipe(ofType('LOAD'));

  @Effect({ dispatch: false })
  logSomething$ = this.actions$.pipe(ofType('LOG'));
}
""",
        encoding="utf-8",
    )

    # 2. Type-only HttpEvent from @angular/common/http
    interceptor_file = tmp_path / "custom.interceptor.ts"
    interceptor_file.write_text(
        """import { Injectable } from '@angular/core';
import { HttpClient, HttpEvent, HttpHandler, HttpRequest } from '@angular/common/http';
import { Observable } from 'rxjs';

@Injectable()
export class CustomInterceptor {
  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
    return next.handle(req);
  }
}
""",
        encoding="utf-8",
    )

    applied = _modernize_angular_source_files(tmp_path, target_major=17)
    assert any("Effect" in m for m in applied)
    assert any("HttpEvent" in m for m in applied)

    updated_effects = effects_file.read_text(encoding="utf-8")
    assert "Effect" not in updated_effects or "createEffect" in updated_effects
    assert "createEffect(" in updated_effects
    assert "@Effect()" not in updated_effects
    assert "@Effect({ dispatch: false })" not in updated_effects

    updated_interceptor = interceptor_file.read_text(encoding="utf-8")
    assert "type HttpEvent" in updated_interceptor


def test_angular_run_build_and_tests_removes_openssl_legacy_for_v17(tmp_path: Path, monkeypatch):
    import subprocess

    adapter = AngularAdapter()
    (tmp_path / "package.json").write_text(
        json.dumps({
            "dependencies": {"@angular/core": "^18.2.0"},
            "scripts": {"build": "ng build"},
        }),
        encoding="utf-8",
    )

    captured_env = {}

    def mock_subprocess_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Build ok", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
    monkeypatch.setattr("shutil.which", lambda *args, **kwargs: "npm")

    summary = adapter.run_build_and_tests(tmp_path)
    build_result = next((r for r in summary.results if r.name == "build"), None)
    assert build_result is not None
    assert build_result.status.value == "REQUIRED_PASSED"
    # Modern Angular >= 17 should NOT have --openssl-legacy-provider in NODE_OPTIONS
    assert "--openssl-legacy-provider" not in captured_env.get("NODE_OPTIONS", "")


def test_is_working_tree_clean_ignores_ephemeral_cache(tmp_path: Path, monkeypatch):
    import subprocess
    from amstralift.core.workspace import is_working_tree_clean

    # 1. Ephemeral caches like .angular/ or .nx/ should be ignored
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="?? .angular/\n?? .nx/\n", stderr=""
        ),
    )
    assert is_working_tree_clean(tmp_path) is True

    # 2. Meaningful uncommitted changes should NOT be ignored
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="?? .angular/\nM  src/app/app.component.ts\n", stderr=""
        ),
    )
    assert is_working_tree_clean(tmp_path) is False


def test_modernize_angular_gitignore(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_gitignore

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\ndist/\n", encoding="utf-8")

    _modernize_angular_gitignore(tmp_path)

    updated = gitignore.read_text(encoding="utf-8")
    assert ".angular" in updated


