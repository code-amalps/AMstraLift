"""Tests for Angular ecosystem adapter."""

import json
from pathlib import Path
from unittest.mock import patch

from amstralift.adapters.angular import AngularAdapter, classify_angular_tier
from amstralift.core.models import DependencyChange, DependencyTier


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

    (tmp_path / "angular.json").write_text(
        json.dumps(
            {
                "projects": {
                    "app": {
                        "architect": {
                            "test": {"builder": "@angular-devkit/build-angular:karma"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # 1. Karma / ng test script with configuration -> injects --watch=false --browsers=ChromeHeadless
    script = "npm run lint && ng test --configuration=test"
    cmd = adapter._resolve_angular_test_command(script, tmp_path, has_npm=True)
    assert "--watch=false" in cmd
    assert "--browsers=ChromeHeadless" in cmd
    assert cmd.startswith("npm exec -- ng test --configuration=test")
    assert "npm run lint" not in cmd

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
    chained_jest_cmd = adapter._resolve_angular_test_command(
        "npm run lint && jest --coverage", tmp_path, has_npm=True
    )
    assert chained_jest_cmd == "npm exec -- jest --coverage"

    # 4. Vitest -> injects --run
    vitest_cmd = adapter._resolve_angular_test_command("vitest", tmp_path, has_npm=True)
    assert "--run" in vitest_cmd
    chained_vitest_cmd = adapter._resolve_angular_test_command(
        "npm run lint && vitest", tmp_path, has_npm=True
    )
    assert chained_vitest_cmd == "npm exec -- vitest --run"

    # 5. Angular's unit-test builder does not accept Karma-only CLI flags.
    modern_workspace = tmp_path / "modern"
    modern_workspace.mkdir()
    (modern_workspace / "angular.json").write_text(
        json.dumps(
            {
                "projects": {
                    "app": {
                        "targets": {
                            "test": {"builder": "@angular/build:unit-test"}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (modern_workspace / "karma.conf.js").write_text(
        "browsers: ['ChromeHeadless']",
        encoding="utf-8",
    )
    modern_cmd = adapter._resolve_angular_test_command(
        "ng test --watch=false --no-progress --browsers=ChromeHeadless",
        modern_workspace,
        has_npm=True,
    )
    assert modern_cmd == "npm exec -- ng test"
    assert "--watch=false" not in modern_cmd
    assert "--no-progress" not in modern_cmd
    assert "--browsers=ChromeHeadless" not in modern_cmd


def test_angular_missing_test_target_is_reported_as_skipped(tmp_path: Path, monkeypatch):
    from amstralift.core.models import GateStatus

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "ng test --watch=false"}}),
        encoding="utf-8",
    )
    (tmp_path / "angular.json").write_text(
        json.dumps(
            {
                "projects": {
                    "app": {
                        "projectType": "application",
                        "architect": {"build": {"builder": "@angular-devkit/build-angular:browser"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "amstralift.adapters.angular.get_node_execution_env",
        lambda: {"PATH": "node-path"},
    )
    monkeypatch.setattr(
        "amstralift.adapters.angular.shutil.which",
        lambda name, path=None: "npm" if name == "npm" else None,
    )

    summary = AngularAdapter().run_build_and_tests(tmp_path)

    test_result = next(result for result in summary.results if result.name == "test")
    assert test_result.status == GateStatus.REQUIRED_SKIPPED
    assert "no configured test target" in test_result.stdout.lower()
    assert not summary.all_required_passed


def test_angular_run_build_and_tests_timeout_handling(tmp_path: Path, monkeypatch):
    import subprocess

    from amstralift.core.models import GateStatus

    adapter = AngularAdapter()
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "ng test", "build": "ng build"}}),
        encoding="utf-8",
    )

    def mock_run_cancellable(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr(
        "amstralift.adapters.angular.run_cancellable_subprocess",
        mock_run_cancellable,
    )

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
    "ignoreDeprecations": "6.0"
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

    # 3. Legacy test.ts with require.context for Angular 15+
    test_file = tmp_path / "test.ts"
    test_file.write_text(
        """import 'zone.js/testing';
declare const require: {
  context(
    path: string,
    deep?: boolean,
    filter?: RegExp
  ): {
    keys(): string[];
    <T>(id: string): T;
  };
};

getTestBed().initTestEnvironment();
// Then we find all the tests.
const context = require.context('./', true, /\\.spec\\.ts$/);
// And load the modules.
context.keys().map(context);
""",
        encoding="utf-8",
    )

    applied = _modernize_angular_source_files(tmp_path, target_major=17)
    assert any("Effect" in m for m in applied)
    assert any("HttpEvent" in m for m in applied)
    assert any("require.context" in m for m in applied)

    updated_effects = effects_file.read_text(encoding="utf-8")
    assert "Effect" not in updated_effects or "createEffect" in updated_effects
    assert "createEffect(" in updated_effects
    assert "@Effect()" not in updated_effects
    assert "@Effect({ dispatch: false })" not in updated_effects

    updated_interceptor = interceptor_file.read_text(encoding="utf-8")
    assert "type HttpEvent" in updated_interceptor

    updated_test_file = test_file.read_text(encoding="utf-8")
    assert "require.context" not in updated_test_file
    assert "declare const require" not in updated_test_file


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

    def mock_run_cancellable(cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Build ok", stderr="")

    monkeypatch.setattr(
        "amstralift.adapters.angular.run_cancellable_subprocess",
        mock_run_cancellable,
    )
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


def test_align_angular_ecosystem_dependencies(tmp_path: Path):
    from amstralift.adapters.angular import _align_angular_ecosystem_dependencies

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(
        json.dumps(
            {
                "dependencies": {
                    "@angular/core": "~12.2.6",
                    "@fortawesome/angular-fontawesome": "^0.7.0",
                    "@fortawesome/fontawesome-free": "^5.15.1",
                    "@fortawesome/fontawesome-svg-core": "^1.2.32",
                    "@fortawesome/free-solid-svg-icons": "^5.15.1",
                    "@ngx-translate/core": "^13.0.0",
                    "@ngx-translate/http-loader": "^6.0.0",
                    "bootstrap": "^5.0.1",
                    "tslib": "^2.2.0",
                }
            }
        ),
        encoding="utf-8",
    )

    applied = _align_angular_ecosystem_dependencies(tmp_path, target_major=22)
    assert len(applied) > 0

    data = json.loads(pkg_path.read_text(encoding="utf-8"))
    deps = data["dependencies"]
    assert deps["@fortawesome/angular-fontawesome"] == "^5.1.0"
    assert deps["@fortawesome/fontawesome-free"] == "^7.3.1"
    assert deps["@fortawesome/fontawesome-svg-core"] == "^7.3.1"
    assert deps["@fortawesome/free-solid-svg-icons"] == "^7.3.1"
    assert deps["@ngx-translate/core"] == "^17.0.0"
    assert deps["@ngx-translate/http-loader"] == "^16.0.0"
    assert deps["bootstrap"] == "^5.3.3"
    assert deps["tslib"] == "^2.8.1"


def test_discover_candidates_ecosystem_alignment(tmp_path: Path, monkeypatch):
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(
        json.dumps(
            {
                "dependencies": {
                    "@angular/core": "~12.2.6",
                    "@fortawesome/angular-fontawesome": "^0.7.0",
                    "@ngx-translate/core": "^13.0.0",
                    "bootstrap": "^5.0.1",
                },
                "devDependencies": {
                    "@angular/cli": "~12.2.6",
                    "typescript": "~4.2.4",
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = AngularAdapter()

    def fetch_version(package_name: str, target_major: int | None = None) -> str | None:
        return {
            "@angular/core": "22.2.0",
            "@angular/cli": "22.2.0",
            "typescript": "7.0.2",
        }.get(package_name)

    monkeypatch.setattr(adapter, "fetch_latest_version", fetch_version)

    candidates = adapter.discover_candidates(tmp_path)
    cand_dict = {c.package_name: c.to_version for c in candidates}

    assert cand_dict["@angular/core"] == "^22.2.0"
    assert cand_dict["typescript"] == "^6.0.3"
    assert cand_dict["@fortawesome/angular-fontawesome"] == "^5.1.0"
    assert cand_dict["@ngx-translate/core"] == "^17.0.0"
    assert cand_dict["bootstrap"] == "^5.3.3"


def test_modernize_angular_stylesheets_m2_theming(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_stylesheets

    scss_path = tmp_path / "styles.scss"
    scss_path.write_text(
        """@use '~@angular/material' as mat;
$my-palette: mat.define-palette(mat.$green-palette, 400);
$my-theme: mat.define-light-theme((color: (primary: $my-palette)));
@include mat.all-component-themes($my-theme);
""",
        encoding="utf-8",
    )

    applied = _modernize_angular_stylesheets(tmp_path, target_major=22)
    assert len(applied) > 0

    content = scss_path.read_text(encoding="utf-8")
    assert "@use '@angular/material' as mat;" in content
    assert "mat.m2-define-palette(mat.$m2-green-palette, 400)" in content
    assert "mat.m2-define-light-theme" in content
    assert "@include mat.all-component-themes" in content


def test_modernize_angular_source_files_forms_and_routing(tmp_path: Path):
    from amstralift.adapters.angular import _modernize_angular_source_files

    routing_path = tmp_path / "app-routing.module.ts"
    routing_path.write_text(
        """RouterModule.forRoot(routes, {
  scrollPositionRestoration: 'enabled',
  relativeLinkResolution: 'legacy'
})""",
        encoding="utf-8",
    )

    form_path = tmp_path / "my-form.component.ts"
    form_path.write_text(
        """import { Component } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';

@Component({ selector: 'app-form' })
export class MyFormComponent {
  form: FormGroup;
  constructor(private fb: FormBuilder) {
    this.form = this.fb.group({ name: ['', Validators.required] });
  }
}""",
        encoding="utf-8",
    )

    applied = _modernize_angular_source_files(tmp_path, target_major=22)
    assert len(applied) > 0

    routing_content = routing_path.read_text(encoding="utf-8")
    assert "relativeLinkResolution" not in routing_content

    form_content = form_path.read_text(encoding="utf-8")
    assert "UntypedFormBuilder" in form_content
    assert "UntypedFormGroup" in form_content
    assert "form: UntypedFormGroup;" in form_content
    assert "private fb: UntypedFormBuilder" in form_content


def test_modernize_angular_source_files_raw_loader(tmp_path: Path):
    """Verify legacy Webpack raw-loader require calls are inlined for esbuild compatibility."""
    from amstralift.adapters.angular import _modernize_angular_source_files

    theme_scss = tmp_path / "my-theme.scss"
    theme_scss.write_text(".my-class { color: red; }", encoding="utf-8")

    comp_ts = tmp_path / "my.component.ts"
    comp_ts.write_text(
        """import { Component } from '@angular/core';

@Component({ selector: 'my-comp' })
export class MyComponent {
  themeSrc: string = require('!raw-loader!./my-theme.scss')
    .default;
}""",
        encoding="utf-8",
    )

    applied = _modernize_angular_source_files(tmp_path, target_major=22)
    assert len(applied) > 0
    assert any("raw-loader" in a for a in applied)

    content = comp_ts.read_text(encoding="utf-8")
    assert "raw-loader" not in content
    assert ".my-class { color: red; }" in content


def test_modernize_angular_material_templates(tmp_path: Path):
    from amstralift.adapters.angular_standalone import modernize_angular_material_templates

    html_path = tmp_path / "my.component.html"
    html_path.write_text(
        """<mat-form-field>
  <mat-placeholder>Username</mat-placeholder>
  <input matInput>
</mat-form-field>
<mat-chip-list>
  <mat-chip>One</mat-chip>
</mat-chip-list>""",
        encoding="utf-8",
    )

    applied = modernize_angular_material_templates(tmp_path)
    assert len(applied) > 0

    content = html_path.read_text(encoding="utf-8")
    assert "<mat-label>Username</mat-label>" in content
    assert "<mat-placeholder>" not in content
    assert "<mat-chip-set>" in content
    assert "</mat-chip-set>" in content
    assert "<mat-chip-list>" not in content


def test_modernize_angular_standalone_components(tmp_path: Path):
    from amstralift.adapters.angular_standalone import modernize_angular_standalone_components

    # Create a SharedModule
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir(parents=True)
    (shared_dir / "shared.module.ts").write_text("export class SharedModule {}", encoding="utf-8")

    # Create a Shared Component
    shared_comp = shared_dir / "big-input.component.ts"
    shared_comp.write_text(
        """import { Component } from '@angular/core';
@Component({
  selector: 'app-big-input',
  template: '<mat-card [ngClass]="cls"><mat-icon>search</mat-icon></mat-card>'
})
export class BigInputComponent {}""",
        encoding="utf-8",
    )

    # Create a Feature Component
    feat_dir = tmp_path / "features" / "home"
    feat_dir.mkdir(parents=True)
    feat_comp = feat_dir / "home.component.ts"
    feat_comp.write_text(
        """import { Component } from '@angular/core';
@Component({
  selector: 'app-home',
  template: '<h1>Home</h1><router-outlet></router-outlet>'
})
export class HomeComponent {}""",
        encoding="utf-8",
    )

    applied = modernize_angular_standalone_components(tmp_path)
    assert len(applied) > 0

    shared_content = shared_comp.read_text(encoding="utf-8")
    assert "CommonModule" in shared_content
    assert "MatCardModule" in shared_content
    assert "MatIconModule" in shared_content

    feat_content = feat_comp.read_text(encoding="utf-8")
    assert "SharedModule" in feat_content
    assert "RouterModule" in feat_content


def test_modernize_angular_standalone_arbitrary_filenames_and_ngmodules(tmp_path: Path):
    from amstralift.adapters.angular_standalone import modernize_angular_standalone_components

    # Component not ending in .component.ts
    admin_comp = tmp_path / "admin-shell.ts"
    admin_comp.write_text(
        """import { Component } from '@angular/core';
@Component({
  selector: 'lab-admin',
  template: '<section><router-outlet></router-outlet></section>'
})
export class AdminShell {}""",
        encoding="utf-8",
    )

    settings_comp = tmp_path / "project-settings.ts"
    settings_comp.write_text(
        """import { Component } from '@angular/core';
import { UntypedFormBuilder, Validators } from '@angular/forms';
@Component({
  selector: 'lab-project-settings',
  template: '<form [formGroup]="form"><input formControlName="name"></form>'
})
export class ProjectSettings {
  constructor(private fb: UntypedFormBuilder) {}
}""",
        encoding="utf-8",
    )

    mod_file = tmp_path / "admin.module.ts"
    mod_file.write_text(
        """import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AdminShell } from './admin-shell';
import { ProjectSettings } from './project-settings';

@NgModule({
  declarations: [AdminShell, ProjectSettings],
  imports: [CommonModule]
})
export class AdminModule {}""",
        encoding="utf-8",
    )

    applied = modernize_angular_standalone_components(tmp_path)
    assert len(applied) >= 2

    # Check admin shell
    admin_txt = admin_comp.read_text(encoding="utf-8")
    assert "standalone: true" in admin_txt
    assert "RouterModule" in admin_txt
    assert "@angular/router" in admin_txt

    # Check settings
    settings_txt = settings_comp.read_text(encoding="utf-8")
    assert "standalone: true" in settings_txt
    assert "ReactiveFormsModule" in settings_txt
    assert "@angular/forms" in settings_txt

    # Check NgModule declarations moved to imports
    mod_txt = mod_file.read_text(encoding="utf-8")
    assert "declarations:" not in mod_txt or "declarations: []" in mod_txt
    assert "AdminShell" in mod_txt
    assert "ProjectSettings" in mod_txt
    assert "imports: [" in mod_txt


def test_modernize_angular_standalone_heals_double_commas_and_bogus_chunk_imports(tmp_path: Path):
    import re
    from amstralift.adapters.angular_standalone import modernize_angular_standalone_components

    # 1. Component with trailing comma before modernization to ensure NO double comma is generated
    comp1 = tmp_path / "notification.component.ts"
    comp1.write_text(
        """import { Component } from '@angular/core';

@Component({
  selector: 'app-notification',
  templateUrl: './notification.component.html',
  styleUrls: ['./notification.component.scss'],
})
export class NotificationComponent {}
""",
        encoding="utf-8",
    )
    (tmp_path / "notification.component.html").write_text("<p>notification</p>", encoding="utf-8")

    # 2. Component with existing double comma and bogus chunk import to ensure it gets healed
    comp2 = tmp_path / "unauthorized.component.ts"
    comp2.write_text(
        """import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { R } from '@angular/cdk/overlay.d-BdoMyOhX';

@Component({
  selector: 'app-unauthorized',
  templateUrl: './unauthorized.component.html',
  styleUrls: ['./unauthorized.component.scss'],,
  standalone: true,
  imports: [CommonModule, R]
})
export class UnauthorizedComponent {}
""",
        encoding="utf-8",
    )
    (tmp_path / "unauthorized.component.html").write_text("<router-outlet></router-outlet>", encoding="utf-8")

    # 3. Dummy chunk file that should be ignored by the component registry
    chunk_file = tmp_path / "overlay.d-BdoMyOhX.ts"
    chunk_file.write_text(
        """import { Component } from '@angular/core';
@Component({ selector: 'r' })
export class R {}
""",
        encoding="utf-8",
    )

    applied = modernize_angular_standalone_components(tmp_path)
    assert len(applied) >= 1

    # Verify comp1: has standalone: true, imports: [CommonModule], and NO double comma ',,'
    txt1 = comp1.read_text(encoding="utf-8")
    assert ",," not in txt1
    assert "standalone: true" in txt1
    assert "CommonModule" in txt1

    # Verify comp2: double comma healed, bogus import purged, R removed from imports
    txt2 = comp2.read_text(encoding="utf-8")
    assert ",," not in txt2
    assert "overlay.d-BdoMyOhX" not in txt2
    assert "import { R }" not in txt2
    assert "imports: [CommonModule, RouterModule]" in txt2 or "RouterModule" in txt2
    assert "R" not in [x.strip() for x in re.search(r"imports:\s*\[(.*?)\]", txt2).group(1).split(",")]


def test_modernize_angular_purges_date_adapter_chunk_import(tmp_path: Path):
    """Verify generic .d-<hash> imports (like date-adapter.d-CtKXiXkO) are purged and type fallbacks provided."""
    from amstralift.adapters.angular import _modernize_angular_source_files

    comp_file = tmp_path / "delete-risk-score.component.ts"
    comp_file.write_text("""import { Component } from '@angular/core';
import { D } from '@angular/material/date-adapter.d-CtKXiXkO';
import { DateAdapter } from '@angular/material/core';

@Component({
  selector: 'app-delete-risk-score',
  template: ''
})
export class DeleteRiskScoreComponent {
  constructor(private adapter: DateAdapter<D>) {}
}
""", encoding="utf-8")

    unused_file = tmp_path / "unused-chunk.component.ts"
    unused_file.write_text("""import { Component } from '@angular/core';
import { D } from '@angular/material/date-adapter.d-CtKXiXkO';

@Component({ selector: 'app-unused', template: '' })
export class UnusedChunkComponent {}
""", encoding="utf-8")

    _modernize_angular_source_files(tmp_path, target_major=19)

    txt = comp_file.read_text(encoding="utf-8")
    assert "date-adapter.d-CtKXiXkO" not in txt
    assert "type D = any;" in txt

    unused_txt = unused_file.read_text(encoding="utf-8")
    assert "date-adapter.d-CtKXiXkO" not in unused_txt
    assert "type D" not in unused_txt


def test_modernize_angular_builder_19_plus(tmp_path: Path):
    """Verify Angular 19+ upgrades preserve/heal devkit builders when @angular/build is not present."""
    from amstralift.adapters.angular import _modernize_angular_workspace_json, _align_angular_ecosystem_dependencies

    # 1. Existing @angular-devkit/build-angular:browser is preserved and not forcibly rewritten
    angular_json = tmp_path / "angular.json"
    angular_json.write_text(json.dumps({
        "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
        "version": 1,
        "projects": {
            "my-app": {
                "projectType": "application",
                "architect": {
                    "build": {
                        "builder": "@angular-devkit/build-angular:browser",
                        "options": {
                            "main": "src/main.ts",
                            "polyfills": "src/polyfills.ts",
                        }
                    },
                    "serve": {
                        "builder": "@angular-devkit/build-angular:dev-server",
                        "options": {
                            "browserTarget": "my-app:build"
                        }
                    }
                }
            }
        }
    }), encoding="utf-8")

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "dependencies": {},
        "devDependencies": {
            "@angular/cli": "^19.0.0",
            "@angular-devkit/build-angular": "^19.0.0"
        }
    }), encoding="utf-8")

    _modernize_angular_workspace_json(tmp_path, target_major=19)
    _align_angular_ecosystem_dependencies(tmp_path, target_major=19)

    updated_ws = json.loads(angular_json.read_text(encoding="utf-8"))
    build_arch = updated_ws["projects"]["my-app"]["architect"]["build"]
    serve_arch = updated_ws["projects"]["my-app"]["architect"]["serve"]

    assert build_arch["builder"] == "@angular-devkit/build-angular:browser"
    assert build_arch["options"]["main"] == "src/main.ts"
    assert serve_arch["builder"] == "@angular-devkit/build-angular:dev-server"
    assert serve_arch["options"]["buildTarget"] == "my-app:build"

    # 2. Heals @angular/build:application back to devkit when @angular/build is not installed
    angular_json.write_text(json.dumps({
        "version": 1,
        "projects": {
            "my-app": {
                "architect": {
                    "build": {
                        "builder": "@angular/build:application",
                        "options": {
                            "browser": "src/main.ts"
                        }
                    },
                    "serve": {
                        "builder": "@angular/build:dev-server",
                        "options": {
                            "buildTarget": "my-app:build"
                        }
                    }
                }
            }
        }
    }), encoding="utf-8")

    _modernize_angular_workspace_json(tmp_path, target_major=19)
    healed_ws = json.loads(angular_json.read_text(encoding="utf-8"))
    assert healed_ws["projects"]["my-app"]["architect"]["build"]["builder"] == "@angular-devkit/build-angular:browser"
    assert healed_ws["projects"]["my-app"]["architect"]["build"]["options"]["main"] == "src/main.ts"
    assert healed_ws["projects"]["my-app"]["architect"]["serve"]["builder"] == "@angular-devkit/build-angular:dev-server"


def test_modernize_angular_scripts_cross_platform(tmp_path: Path):
    """Verify package.json Unix cp commands and openssl workarounds are modernized for cross-platform execution."""
    from amstralift.adapters.angular import _modernize_angular_scripts

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "scripts": {
            "copy": "cp projects/rtcm-angular-lib/src/lib/rtcm-styles/rtcm-core-styles.scss dist/rtcm-angular-lib/",
            "copy:dir": "cp -r projects/assets dist/assets",
            "copy:wildcard": "cp projects/core-services/src/assets/i18n/* dist/core-services/assets/i18n/",
            "legacy": "node --openssl-legacy-provider ./node_modules/@angular/cli/bin/ng build"
        }
    }), encoding="utf-8")

    applied = _modernize_angular_scripts(tmp_path)
    assert len(applied) == 4

    updated = json.loads(pkg_json.read_text(encoding="utf-8"))
    scripts = updated["scripts"]

    assert "require('fs').cpSync" in scripts["copy"]
    assert "rtcm-core-styles.scss" in scripts["copy"]
    assert "dist/rtcm-angular-lib/rtcm-core-styles.scss" in scripts["copy"]
    assert "cpSync('projects/assets', 'dist/assets'" in scripts["copy:dir"]
    assert "cpSync('projects/core-services/src/assets/i18n', 'dist/core-services/assets/i18n'" in scripts["copy:wildcard"]
    assert "--openssl-legacy-provider" not in scripts["legacy"]
    assert scripts["legacy"] == "ng build"


def test_angular_apply_upgrade_transitive_overrides(tmp_path: Path):
    """Verify apply_upgrade writes transitive changes to package.json overrides."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "dependencies": {
            "@angular/core": "^18.0.0",
            "semver": "^5.0.0"
        },
        "devDependencies": {
            "@commitlint/cli": "^11.0.0"
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    changes = [
        DependencyChange(
            package_name="adm-zip",
            from_version="0.5.0",
            to_version="0.6.1",
            change_type="transitive",
        ),
        DependencyChange(
            package_name="semver",
            from_version="7.3.2",
            to_version="7.5.4",
            change_type="transitive",
            parent_package="@commitlint/cli",
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)

    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "overrides" in data
    assert data["overrides"]["adm-zip"] == "^0.6.1"
    assert data["overrides"]["@commitlint/cli"]["semver"] == "^7.5.4"


def test_align_angular_ecosystem_prunes_codelyzer(tmp_path: Path):
    """Verify _align_angular_ecosystem_dependencies removes codelyzer for Angular 14+ when eslint is present."""
    from amstralift.adapters.angular import _align_angular_ecosystem_dependencies

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "devDependencies": {
            "eslint": "^8.57.1",
            "codelyzer": "^6.0.0"
        }
    }), encoding="utf-8")

    _align_angular_ecosystem_dependencies(tmp_path, target_major=18)

    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "codelyzer" not in data.get("devDependencies", {})
    assert "eslint" in data["devDependencies"]


def test_angular_apply_upgrade_protects_higher_major_transitives(tmp_path: Path):
    """Verify that an older major security patch never globally downgrades a modern version in lockfile."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "devDependencies": {
            "protractor": "^7.0.0"
        }
    }), encoding="utf-8")

    lock_json = tmp_path / "package-lock.json"
    lock_json.write_text(json.dumps({
        "name": "my-app",
        "lockfileVersion": 3,
        "packages": {
            "node_modules/@angular-devkit/core/node_modules/ajv": {
                "version": "8.20.0"
            },
            "node_modules/protractor/node_modules/ajv": {
                "version": "6.12.6"
            },
            "node_modules/adm-zip": {
                "version": "0.5.10"
            }
        }
    }), encoding="utf-8")

    adapter = AngularAdapter()
    changes = [
        # Older major fix for legacy tool
        DependencyChange(
            package_name="ajv",
            from_version="6.12.6",
            to_version="6.14.0",
            change_type="transitive",
            parent_package="protractor",
        ),
        # Equal/higher major fix
        DependencyChange(
            package_name="adm-zip",
            from_version="0.5.10",
            to_version="0.6.1",
            change_type="transitive",
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)

    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "overrides" in data
    # adm-zip is safely global because target major 0 >= max major 0
    assert data["overrides"]["adm-zip"] == "^0.6.1"
    # ajv is NOT global because target major 6 < max major 8
    assert "ajv" not in data["overrides"]
    # ajv is scoped under protractor
    assert data["overrides"]["protractor"]["ajv"] == "^6.14.0"


def test_angular_apply_upgrade_excludes_native_binary_overrides(tmp_path: Path):
    """Verify that native binary wrapper packages (esbuild, @esbuild/*, @swc/*) are excluded from overrides."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps(
            {
                "name": "sample-app",
                "version": "1.0.0",
                "dependencies": {
                    "@angular/core": "^12.0.0",
                },
                "devDependencies": {
                    "@angular/cli": "^12.0.0",
                },
                "overrides": {
                    "esbuild": "^0.25.0",
                    "some-dep": {
                        "@swc/core": "^1.3.0",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    adapter = AngularAdapter()
    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="12.0.0",
            to_version="^22.0.0",
            change_type="direct",
        ),
        DependencyChange(
            package_name="esbuild",
            from_version="0.12.24",
            to_version="0.25.0",
            change_type="transitive",
            parent_package="@angular-devkit/build-angular",
        ),
        DependencyChange(
            package_name="@esbuild/win32-x64",
            from_version="0.12.24",
            to_version="0.25.0",
            change_type="transitive",
        ),
        DependencyChange(
            package_name="semver",
            from_version="7.0.0",
            to_version="7.5.4",
            change_type="transitive",
        ),
    ]

    adapter.apply_upgrade(tmp_path, changes)

    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    assert "overrides" in data
    # semver is retained
    assert data["overrides"]["semver"] == "^7.5.4"
    # esbuild and @esbuild/* must NOT be in overrides (and stale esbuild override cleaned up)
    assert "esbuild" not in data["overrides"]
    assert "@esbuild/win32-x64" not in data["overrides"]
    assert "some-dep" not in data["overrides"]


def test_modernize_angular_libraries(tmp_path: Path):
    from amstralift.adapters.angular import _align_angular_ecosystem_dependencies, _modernize_angular_libraries

    angular_json = {
        "version": 1,
        "projects": {
            "my-app": {"projectType": "application"},
            "abc-angular-lib": {"projectType": "library", "root": "projects/abc-angular-lib"},
        },
    }
    (tmp_path / "angular.json").write_text(json.dumps(angular_json), encoding="utf-8")

    root_pkg = {
        "name": "my-app",
        "devDependencies": {
            "ng-packagr": "^14.0.0",
        },
    }
    (tmp_path / "package.json").write_text(json.dumps(root_pkg), encoding="utf-8")

    lib_dir = tmp_path / "projects" / "abc-angular-lib"
    lib_dir.mkdir(parents=True)
    lib_pkg = {
        "name": "abc-angular-lib",
        "peerDependencies": {
            "@angular/core": "^14.0.0",
            "@angular/common": "^14.0.0",
        },
        "dependencies": {
            "tslib": "^2.3.0",
        },
    }
    (lib_dir / "package.json").write_text(json.dumps(lib_pkg), encoding="utf-8")
    (lib_dir / "src").mkdir(parents=True)
    (lib_dir / "src" / "public-api.ts").write_text("export const LIB = true;\n", encoding="utf-8")

    tsconfig = {
        "compilerOptions": {
            "paths": {
                "abc-angular-lib": ["dist/abc-angular-lib"],
            },
        },
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(tsconfig), encoding="utf-8")

    _align_angular_ecosystem_dependencies(tmp_path, target_major=18)
    applied = _modernize_angular_libraries(tmp_path, target_major=18)
    assert len(applied) > 0

    updated_root = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert updated_root["devDependencies"]["ng-packagr"] == "^18.0.0"

    updated_lib = json.loads((lib_dir / "package.json").read_text(encoding="utf-8"))
    assert updated_lib["peerDependencies"]["@angular/core"] == "^18.0.0"
    assert updated_lib["peerDependencies"]["@angular/common"] == "^18.0.0"
    assert updated_lib["dependencies"]["tslib"] == "^2.8.1"

    updated_tsconfig = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
    paths = updated_tsconfig["compilerOptions"]["paths"]["abc-angular-lib"]
    assert "dist/abc-angular-lib" in paths
    assert "projects/abc-angular-lib/src/public-api.ts" in paths

    assert (tmp_path / "dist" / "abc-angular-lib" / "package.json").exists()


def test_angular_never_upgrades_to_unreleased_major(tmp_path: Path):
    """Verify that when Angular is on peak GA version (e.g. 19.0.0), it never bumps to unreleased Angular 20."""
    adapter = AngularAdapter(incremental=True)
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        json.dumps({
            "dependencies": {
                "@angular/core": "^19.0.0",
                "@angular/common": "^19.0.0",
            }
        }),
        encoding="utf-8",
    )

    with patch.object(adapter, "fetch_latest_version", return_value="19.2.1"):
        candidates = adapter.discover_candidates(tmp_path)
        # Angular is already at peak released GA (19); must not upgrade to v20
        core_changes = [c for c in candidates if c.package_name == "@angular/core"]
        for c in core_changes:
            from amstralift.adapters.angular import extract_major_version
            assert extract_major_version(c.to_version) <= 19


def test_angular_fetch_latest_ignores_prerelease():
    """Verify AngularAdapter.fetch_latest_version rejects -rc, -next, -beta pre-releases."""
    adapter = AngularAdapter()

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "dist-tags": {
                    "latest": "20.0.0-next.1",
                },
                "versions": {
                    "18.2.0": {},
                    "19.2.1": {},
                    "20.0.0-next.1": {},
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

    with patch("amstralift.adapters.angular.httpx.Client", FakeClient):
        ver = adapter.fetch_latest_version("@angular/core")
        assert ver == "19.2.1"


def test_modernize_angular_scripts_strips_obsolete_cli_flags(tmp_path: Path):
    """Verify obsolete CLI flags like --build-optimizer and --vendor-chunk are stripped from package.json scripts."""
    from amstralift.adapters.angular import _modernize_angular_scripts

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "name": "my-app",
        "scripts": {
            "build:portal": "ng build --project=rtcm-boilerplate --aot --output-hashing=all --configuration=production --build-optimizer=true --optimization=true --base-href=/rtcm/",
            "build:legacy": "ng build --prod --vendor-chunk=false --build-optimizer"
        }
    }), encoding="utf-8")

    applied = _modernize_angular_scripts(tmp_path)
    assert len(applied) == 2

    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    bp = data["scripts"]["build:portal"]
    assert "--build-optimizer" not in bp
    assert "--optimization=true" in bp
    assert "--base-href=/rtcm/" in bp

    bl = data["scripts"]["build:legacy"]
    assert "--build-optimizer" not in bl
    assert "--vendor-chunk" not in bl
    assert "--prod" not in bl
    assert "--configuration=production" in bl


def test_modernize_angular_tsconfig_silences_baseurl_deprecation(tmp_path: Path):
    """Verify tsconfig with baseUrl sets ignoreDeprecations: '6.0' to silence TS5101."""
    from amstralift.adapters.angular import _modernize_angular_tsconfig

    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(json.dumps({
        "compilerOptions": {
            "baseUrl": "./",
            "paths": {
                "@lib/*": ["dist/lib/*"]
            },
            "types": ["jasmine"]
        }
    }), encoding="utf-8")

    applied = _modernize_angular_tsconfig(tmp_path, target_major=19)
    assert len(applied) > 0

    data = json.loads(tsconfig.read_text(encoding="utf-8"))
    opts = data["compilerOptions"]
    assert opts["ignoreDeprecations"] == "6.0"
    assert "node" in opts["types"]


def test_modernize_angular_source_files_cleans_console_imports(tmp_path: Path):
    """Verify accidental Node.js console imports in browser files are cleaned up (TS2591)."""
    from amstralift.adapters.angular import _modernize_angular_source_files

    svc_file = tmp_path / "my-service.ts"
    svc_file.write_text("""import { Injectable } from '@angular/core';
import { error } from 'console';

@Injectable({ providedIn: 'root' })
export class MyService {
  handle(err: any) {
    error("failed: ", err);
  }
}
""", encoding="utf-8")

    unused_file = tmp_path / "unused.ts"
    unused_file.write_text("""import { Component } from '@angular/core';
import { debug } from 'console';

@Component({ selector: 'app-u', template: '' })
export class UnusedComponent {}
""", encoding="utf-8")

    applied = _modernize_angular_source_files(tmp_path, target_major=19)

    svc_content = svc_file.read_text(encoding="utf-8")
    assert "from 'console'" not in svc_content
    assert "const error = console.error.bind(console);" in svc_content
    assert "error(\"failed: \", err);" in svc_content

    unused_content = unused_file.read_text(encoding="utf-8")
    assert "from 'console'" not in unused_content
    assert "debug" not in unused_content


def test_modernize_angular_source_files_enforces_standalone_true(tmp_path: Path):
    """Verify @Component with imports: is guaranteed to have standalone: true (NG2010)."""
    from amstralift.adapters.angular import _modernize_angular_source_files

    comp_file = tmp_path / "app.component.ts"
    comp_file.write_text("""import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.scss'],
  imports: [CommonModule]
})
export class AppComponent implements OnInit {}
""", encoding="utf-8")

    _modernize_angular_source_files(tmp_path, target_major=19)

    content = comp_file.read_text(encoding="utf-8")
    assert "standalone: true" in content
    assert "imports: [CommonModule]" in content


def test_align_angular_ecosystem_dependencies_purges_path_to_regexp_override(tmp_path: Path):
    """Verify that path-to-regexp in overrides is automatically cleaned up to prevent dev-server crash."""
    from amstralift.adapters.angular import _align_angular_ecosystem_dependencies

    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        json.dumps(
            {
                "dependencies": {"@angular/core": "^19.0.0"},
                "overrides": {
                    "path-to-regexp": "^0.1.10",
                    "foo": "^1.0.0",
                },
            }
        ),
        encoding="utf-8",
    )

    _align_angular_ecosystem_dependencies(tmp_path, target_major=19)

    data = json.loads(pkg_file.read_text(encoding="utf-8"))
    assert "path-to-regexp" not in data.get("overrides", {})
    assert data["overrides"]["foo"] == "^1.0.0"


def test_apply_upgrade_skips_path_to_regexp_transitive_override(tmp_path: Path):
    """Verify that apply_upgrade never adds path-to-regexp to overrides."""
    from amstralift.adapters.angular import AngularAdapter
    from amstralift.core.models import DependencyChange, DependencyTier

    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        json.dumps(
            {
                "dependencies": {"@angular/core": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )

    adapter = AngularAdapter()
    changes = [
        DependencyChange(
            package_name="path-to-regexp",
            from_version="0.1.7",
            to_version="0.1.10",
            ecosystem="angular",
            tier=DependencyTier.TIER_1_SAFE,
            change_type="transitive",
            cve_id="CVE-2024-45296",
        )
    ]

    adapter.apply_upgrade(tmp_path, changes)

    data = json.loads(pkg_file.read_text(encoding="utf-8"))
    assert "path-to-regexp" not in data.get("overrides", {})


def test_compatibility_selector_rejects_path_to_regexp_global_override():
    """Verify that CompatibilityAwareVersionSelector marks path-to-regexp as incompatible for global override."""
    from amstralift.security.compatibility_selector import CompatibilityAwareVersionSelector

    res = CompatibilityAwareVersionSelector.select_version(
        package_name="path-to-regexp",
        current_version="0.1.7",
        fixed_versions=["0.1.10", "8.0.0"],
        ecosystem="npm",
    )
    assert res.is_compatible is False
    assert res.target_version is None
    assert "path-to-regexp" in res.rationale


def test_sanitize_import_providers_from_purges_aggrid_and_standalone_components(tmp_path: Path):
    """Verify that sanitize_import_providers_from purges AgGridModule and standalone components to prevent NG0800."""
    from amstralift.adapters.angular_standalone import sanitize_import_providers_from

    main_ts = tmp_path / "src" / "main.ts"
    main_ts.parent.mkdir(parents=True, exist_ok=True)
    main_ts.write_text(
        """import { enableProdMode, importProvidersFrom } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
import { HttpClientModule } from '@angular/common/http';
import { AgGridModule } from 'ag-grid-angular';
import { HeaderComponent } from './app/header.component';
import { AppRoutingModule } from './app/app-routing.module';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent, {
  providers: [
    importProvidersFrom(
      BrowserAnimationsModule,
      HttpClientModule,
      AgGridModule,
      HeaderComponent,
      AppRoutingModule
    )
  ]
}).catch(err => console.error(err));
""",
        encoding="utf-8",
    )

    notes = sanitize_import_providers_from(tmp_path)
    assert len(notes) > 0
    assert "NG0800" in notes[0]

    updated = main_ts.read_text(encoding="utf-8")
    assert "AgGridModule" not in updated
    assert "HeaderComponent" not in updated
    assert "BrowserAnimationsModule" in updated
    assert "HttpClientModule" in updated
    assert "AppRoutingModule" in updated
    assert "importProvidersFrom" in updated
    assert "from 'ag-grid-angular'" not in updated
    assert "from './app/header.component'" not in updated


def test_sanitize_import_providers_from_removes_empty_call(tmp_path: Path):
    """Verify that when importProvidersFrom only had AgGridModule, the call and import are completely removed."""
    from amstralift.adapters.angular_standalone import sanitize_import_providers_from

    cfg_ts = tmp_path / "src" / "app" / "app.config.ts"
    cfg_ts.parent.mkdir(parents=True, exist_ok=True)
    cfg_ts.write_text(
        """import { ApplicationConfig, importProvidersFrom } from '@angular/core';
import { provideRouter } from '@angular/router';
import { AgGridModule } from 'ag-grid-angular';

export const appConfig: ApplicationConfig = {
  providers: [
    provideRouter([]),
    importProvidersFrom(AgGridModule)
  ]
};
""",
        encoding="utf-8",
    )

    notes = sanitize_import_providers_from(tmp_path)
    assert len(notes) > 0

    updated = cfg_ts.read_text(encoding="utf-8")
    assert "AgGridModule" not in updated
    assert "importProvidersFrom" not in updated
    assert "ApplicationConfig" in updated
    assert "provideRouter([])" in updated
    assert "from 'ag-grid-angular'" not in updated


def test_modernize_angular_standalone_components_injects_aggrid(tmp_path: Path):
    """Verify that modernize_angular_standalone_components injects AgGridAngular into components using ag-grid-angular."""
    from amstralift.adapters.angular_standalone import modernize_angular_standalone_components

    src_dir = tmp_path / "src" / "app"
    src_dir.mkdir(parents=True, exist_ok=True)

    grid_comp = src_dir / "grid.component.ts"
    grid_comp.write_text(
        """import { Component } from '@angular/core';

@Component({
  selector: 'app-grid',
  template: `<ag-grid-angular class="ag-theme-alpine" [rowData]="[]"></ag-grid-angular>`,
  styleUrls: ['./grid.component.scss']
})
export class GridComponent {}
""",
        encoding="utf-8",
    )

    notes = modernize_angular_standalone_components(tmp_path)
    assert len(notes) > 0

    updated = grid_comp.read_text(encoding="utf-8")
    assert "standalone: true" in updated
    assert "AgGridAngular" in updated
    assert "import { AgGridAngular } from 'ag-grid-angular';" in updated











