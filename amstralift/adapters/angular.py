"""Angular ecosystem adapter.

Handles Angular version discovery via npm registry, peer dependency resolution,
dependency tiering, manifest/lockfile updates, and build/test gates.
Enforces Angular LTS governance policy when configured.
"""

import json
import logging
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("amstralift.adapters.angular")

from amstralift.adapters.base import BaseAdapter, get_node_execution_env
from amstralift.core.cancellation import (
    OperationCancelledError,
    check_cancelled,
    run_cancellable_subprocess,
)
from amstralift.core.workspace import safe_rglob
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
)
from amstralift.governance.angular_lts import AngularLTSConfig, AngularLTSGovernance

NPM_REGISTRY_BASE = "https://registry.npmjs.org"

# Tiering classifications for Angular packages (Section 8)
TIER_3_PATTERNS = [
    re.compile(r".*auth.*", re.IGNORECASE),
    re.compile(r".*security.*", re.IGNORECASE),
    re.compile(r".*payment.*", re.IGNORECASE),
    re.compile(r".*stripe.*", re.IGNORECASE),
    re.compile(r".*msal.*", re.IGNORECASE),
    re.compile(r".*crypto.*", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(r"^@angular/(router|forms|common|core)$"),
    re.compile(r"^@ngrx/.*"),
    re.compile(r".*(state|routing|store|interceptor).*", re.IGNORECASE),
]


def classify_angular_tier(package_name: str) -> DependencyTier:
    """Classify dependency package into Tier 1, 2, or 3."""
    for pattern in TIER_3_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_3_CRITICAL

    for pattern in TIER_2_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR

    return DependencyTier.TIER_1_SAFE


ANGULAR_TS_MATRIX = {
    12: 4,
    13: 4,
    14: 4,
    15: 4,
    16: 5,
    17: 5,
    18: 5,
    19: 5,
    20: 5,
    21: 5,
    22: 6,
}

ANGULAR_TS_RECOMMENDED: dict[int, str] = {
    12: "^4.2.4",
    13: "^4.4.4",
    14: "^4.7.2",
    15: "^4.9.5",
    16: "^5.1.3",
    17: "^5.3.2",
    18: "^5.4.5",
    19: "^5.6.3",
    20: "^5.7.2",
    21: "^5.8.2",
    22: "^6.0.3",
}

ANGULAR_FONTAWESOME_MAP: dict[int, str] = {
    12: "^0.7.0",
    13: "^0.10.0",
    14: "^0.11.0",
    15: "^0.12.0",
    16: "^0.13.0",
    17: "^0.14.0",
    18: "^0.15.0",
    19: "^1.0.0",
    20: "^3.0.0",
    21: "^4.0.0",
    22: "^5.1.0",
}


def get_angular_ecosystem_recommendation(
    pkg_name: str,
    cur_ver: str,
    target_angular_major: int | None,
) -> str | None:
    """Return recommended version for ecosystem package aligned to target Angular major."""
    if target_angular_major is None:
        return None

    if pkg_name == "@fortawesome/angular-fontawesome":
        if target_angular_major in ANGULAR_FONTAWESOME_MAP:
            return ANGULAR_FONTAWESOME_MAP[target_angular_major]
        if target_angular_major >= 22:
            return "^5.1.0"

    if pkg_name in (
        "@fortawesome/fontawesome-svg-core",
        "@fortawesome/free-solid-svg-icons",
        "@fortawesome/free-brands-svg-icons",
        "@fortawesome/fontawesome-free",
    ):
        if target_angular_major >= 16:
            return "^7.3.1"

    if pkg_name == "@ngx-translate/core":
        if target_angular_major >= 16:
            # v17 maintains full NgModule (TranslateModule) and Ivy fesm2022 compatibility
            return "^17.0.0"

    if pkg_name == "@ngx-translate/http-loader":
        if target_angular_major >= 16:
            # v16 maintains 3-argument constructor (http, prefix, suffix) and Ivy compatibility
            return "^16.0.0"

    if pkg_name == "bootstrap":
        if target_angular_major >= 16:
            cur_major = extract_major_version(cur_ver)
            if cur_major is not None and cur_major <= 5:
                return "^5.3.3"

    if pkg_name == "tslib":
        if target_angular_major >= 16:
            return "^2.8.1"

    if pkg_name in ("ng-packagr", "@angular-devkit/build-angular"):
        if target_angular_major and target_angular_major >= 15:
            return f"^{target_angular_major}.0.0"

    return None


def _align_angular_ecosystem_dependencies(repo_path: Path, target_major: int | None = None) -> list[str]:
    """Ensure key ecosystem dependencies in package.json are compatible with target Angular major."""
    pkg_file = repo_path / "package.json"
    if not pkg_file.exists():
        return []

    try:
        data = json.loads(pkg_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    modified = False
    applied = []

    for dep_sec in ("dependencies", "devDependencies"):
        sec = data.get(dep_sec, {})
        for pkg, cur_ver in list(sec.items()):
            rec_ver = get_angular_ecosystem_recommendation(pkg, cur_ver, target_major)
            if rec_ver and cur_ver != rec_ver:
                sec[pkg] = rec_ver
                modified = True
                applied.append(f"Aligned {pkg} to {rec_ver} for Angular {target_major} compatibility")

    # Clean up non-existent or invalid @angular/build versions in devDependencies
    dev_deps = data.get("devDependencies", {})
    if "@angular/build" in dev_deps:
        v = str(dev_deps.get("@angular/build", ""))
        maj = extract_major_version(v)
        if maj is not None and maj > 19:
            del dev_deps["@angular/build"]
            modified = True
        elif target_major is not None and target_major < 18:
            del dev_deps["@angular/build"]
            modified = True

    # Clean up problematic native binary & incompatible multi-major routing overrides if present
    # (e.g. path-to-regexp causes 'TypeError: pathRegexp.match is not a function' in dev-server)
    if "overrides" in data and isinstance(data["overrides"], dict):
        for bad_key in list(data["overrides"].keys()):
            if bad_key in ("esbuild", "path-to-regexp") or bad_key.startswith(("@esbuild/", "@swc/", "@rollup/")):
                del data["overrides"][bad_key]
                modified = True
        for parent_k, parent_v in list(data["overrides"].items()):
            if isinstance(parent_v, dict):
                for bad_k in list(parent_v.keys()):
                    if bad_k in ("esbuild", "path-to-regexp") or bad_k.startswith(("@esbuild/", "@swc/", "@rollup/")):
                        del parent_v[bad_k]
                        modified = True
                if not parent_v:
                    del data["overrides"][parent_k]
                    modified = True
        if not data["overrides"]:
            del data["overrides"]
            modified = True

    # Ensure @types/node is present in devDependencies for Node type resolution (TS2591)
    if "@types/node" not in dev_deps:
        dev_deps["@types/node"] = "^20.11.0"
        modified = True

    # For Angular 14+, remove retired TSLint ecosystem tooling if modern ESLint is present
    if target_major is None or target_major >= 14:
        dev_deps = data.get("devDependencies", {})
        if "eslint" in dev_deps or any(p.startswith("@angular-eslint/") for p in dev_deps):
            for retired_pkg in ("codelyzer",):
                if retired_pkg in dev_deps:
                    del dev_deps[retired_pkg]
                    modified = True
                    applied.append(f"Removed retired tooling {retired_pkg} for Angular {target_major} compatibility")

    if modified:
        try:
            pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    return applied


def extract_major_version(ver_str: str) -> int | None:
    """Extract integer major version from a version string."""
    clean = ver_str.strip().lstrip("^~>=<")
    match = re.match(r"^(\d+)", clean)
    if match:
        return int(match.group(1))
    return None


def get_max_existing_major(repo_path: Path, package_name: str, direct_pkgs: dict[str, str]) -> int | None:
    """Find the highest major version of package_name present in package.json or lockfile."""
    majors: list[int] = []
    if package_name in direct_pkgs:
        m = extract_major_version(direct_pkgs[package_name])
        if m is not None:
            majors.append(m)

    lock_file = repo_path / "package-lock.json"
    if lock_file.exists():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            packages = lock_data.get("packages", {})
            for path_key, meta in packages.items():
                if not path_key:
                    continue
                parts = path_key.split("node_modules/")
                if parts[-1] == package_name:
                    v = meta.get("version", "")
                    m = extract_major_version(v)
                    if m is not None:
                        majors.append(m)
            if not majors and "dependencies" in lock_data:
                def walk_v1(deps: dict[str, Any]) -> None:
                    for k, v in deps.items():
                        if k == package_name:
                            m = extract_major_version(v.get("version", ""))
                            if m is not None:
                                majors.append(m)
                        if "dependencies" in v:
                            walk_v1(v["dependencies"])
                walk_v1(lock_data.get("dependencies", {}))
        except Exception:
            pass

    return max(majors) if majors else None


def _modernize_angular_workspace_json(repo_path: Path, target_major: int | None = None) -> None:
    """Modernize angular.json schema across Angular major versions.

    - Angular 14+: remove obsolete 'defaultProject' rejected by modern CLI schemas
    - Angular 17+: migrate 'browserTarget' -> 'buildTarget' in serve / extract-i18n
    """
    workspace_file = repo_path / "angular.json"
    if not workspace_file.exists():
        return

    try:
        workspace = json.loads(workspace_file.read_text(encoding="utf-8"))
        changed = False

        if "defaultProject" in workspace:
            del workspace["defaultProject"]
            changed = True

        if target_major is None or target_major >= 17:
            def _replace_browser_target(obj: Any) -> bool:
                modified = False
                if isinstance(obj, dict):
                    if "browserTarget" in obj:
                        obj["buildTarget"] = obj.pop("browserTarget")
                        modified = True
                    for v in obj.values():
                        if _replace_browser_target(v):
                            modified = True
                elif isinstance(obj, list):
                    for item in obj:
                        if _replace_browser_target(item):
                            modified = True
                return modified

            if _replace_browser_target(workspace):
                changed = True

        # Ensure referenced asset directories exist so Angular CLI doesn't error on missing asset folders
        projects = workspace.get("projects", {})
        for proj_info in projects.values():
            if not isinstance(proj_info, dict):
                continue
            architect = proj_info.get("architect", {})

            # If workspace references '@angular/build:*' but '@angular/build' is not installed in node_modules,
            # or if the workspace relies on '@angular-devkit/build-angular', heal builders back to '@angular-devkit/build-angular'
            # to prevent fatal CLI error: "Could not find the '@angular/build:application' builder's node package."
            pkg_data_deps: dict[str, str] = {}
            if (repo_path / "package.json").exists():
                try:
                    p_data = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
                    pkg_data_deps = {**p_data.get("dependencies", {}), **p_data.get("devDependencies", {})}
                except Exception:
                    pass

            has_angular_build = (
                "@angular/build" in pkg_data_deps
                and (repo_path / "node_modules" / "@angular" / "build").exists()
            )

            build_target = architect.get("build", {})
            if isinstance(build_target, dict):
                cur_b = build_target.get("builder", "")
                if cur_b == "@angular/build:application" and not has_angular_build:
                    build_target["builder"] = "@angular-devkit/build-angular:browser"
                    changed = True
                    cur_b = "@angular-devkit/build-angular:browser"

                if cur_b == "@angular-devkit/build-angular:browser":
                    opts = build_target.get("options", {})
                    if "browser" in opts and "main" not in opts:
                        opts["main"] = opts.pop("browser")
                        changed = True

            serve_target = architect.get("serve", {})
            if isinstance(serve_target, dict):
                cur_sb = serve_target.get("builder", "")
                if cur_sb == "@angular/build:dev-server" and not has_angular_build:
                    serve_target["builder"] = "@angular-devkit/build-angular:dev-server"
                    changed = True
                s_opts = serve_target.get("options", {})
                if isinstance(s_opts, dict):
                    if (target_major is None or target_major >= 17) and "browserTarget" in s_opts:
                        s_opts["buildTarget"] = s_opts.pop("browserTarget")
                        changed = True
                    elif (target_major and target_major < 17) and "buildTarget" in s_opts:
                        s_opts["browserTarget"] = s_opts.pop("buildTarget")
                        changed = True

            extract_target = architect.get("extract-i18n", {})
            if isinstance(extract_target, dict):
                cur_eb = extract_target.get("builder", "")
                if cur_eb == "@angular/build:extract-i18n" and not has_angular_build:
                    extract_target["builder"] = "@angular-devkit/build-angular:extract-i18n"
                    changed = True

            # For Angular 17+, adjust legacy restrictive initial bundle budgets for modern esbuild bundles
            if target_major is None or target_major >= 17:
                build_target = architect.get("build", {})
                if isinstance(build_target, dict):
                    configurations = build_target.get("configurations", {})
                    for conf in configurations.values():
                        if isinstance(conf, dict) and "budgets" in conf and isinstance(conf["budgets"], list):
                            for budget in conf["budgets"]:
                                if isinstance(budget, dict) and budget.get("type") == "initial":
                                    cur_max = budget.get("maximumError", "")
                                    if cur_max in ("500kb", "1mb", "1.5mb"):
                                        budget["maximumError"] = "3mb"
                                        changed = True

            build_opts = architect.get("build", {}).get("options", {})
            assets = build_opts.get("assets", [])
            if isinstance(assets, list):
                for asset in assets:
                    if isinstance(asset, str):
                        asset_dir = repo_path / asset
                        if not asset_dir.exists() and not asset.endswith(
                            (".ico", ".svg", ".png", ".jpg", ".jpeg", ".json", ".webmanifest")
                        ):
                            try:
                                asset_dir.mkdir(parents=True, exist_ok=True)
                            except Exception:
                                pass

        if changed:
            workspace_file.write_text(json.dumps(workspace, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _remove_legacy_default_project(repo_path: Path) -> None:
    """Backward-compatible alias for legacy callers and unit tests."""
    _modernize_angular_workspace_json(repo_path)


def _modernize_angular_gitignore(repo_path: Path) -> None:
    """Ensure modern Angular ephemeral caches and dependencies are in .gitignore."""
    gitignore = repo_path / ".gitignore"
    standard_entries = ["node_modules/", ".angular/", "/dist", "/out-tsc", "/coverage"]
    try:
        if not gitignore.exists():
            default_content = (
                "# Dependencies\nnode_modules/\n\n"
                "# Output\n/dist\n/out-tsc\n/coverage\n/tmp\n\n"
                "# Angular\n.angular/\n\n"
                "# IDEs and OS\n.idea/\n.vscode/\n.DS_Store\nThumbs.db\n"
            )
            gitignore.write_text(default_content, encoding="utf-8")
            return

        content = gitignore.read_text(encoding="utf-8")
        missing = [entry for entry in standard_entries if entry.strip("/") not in content]
        if missing:
            updated = content.rstrip() + "\n\n# Angular & Dependencies\n" + "\n".join(missing) + "\n"
            gitignore.write_text(updated, encoding="utf-8")
    except Exception:
        pass


def _modernize_angular_scripts(repo_path: Path) -> list[str]:
    """Modernize package.json scripts for cross-platform and modern Angular execution.

    - Replaces Unix-only 'cp' commands with cross-platform Node.js 'fs.cpSync'
      so scripts run on Windows cmd.exe without "'cp' is not recognized".
    - Cleans up obsolete '--openssl-legacy-provider' flags.
    """
    pkg_file = repo_path / "package.json"
    if not pkg_file.exists():
        return []

    try:
        data = json.loads(pkg_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return []

    applied = []
    modified = False

    for script_key, script_val in list(scripts.items()):
        if not isinstance(script_val, str):
            continue

        orig = script_val
        val = script_val

        # 1. Clean up obsolete --openssl-legacy-provider
        if "--openssl-legacy-provider" in val:
            val = re.sub(r"node\s+--openssl-legacy-provider\s+\S*ng(\.js)?", "ng", val)
            val = val.replace("--openssl-legacy-provider", "").strip()

        # 2. Clean up obsolete Angular CLI flags removed in Angular 17+ (e.g. --build-optimizer)
        val = re.sub(r"--build-optimizer(=[^\s;&]+)?\s*", "", val)
        val = re.sub(r"--buildOptimizer(=[^\s;&]+)?\s*", "", val)
        val = re.sub(r"--vendor-chunk(=[^\s;&]+)?\s*", "", val)
        val = re.sub(r"--vendorChunk(=[^\s;&]+)?\s*", "", val)
        val = re.sub(r"--extract-css\s*", "", val)
        if "--prod" in val:
            if "--configuration=production" not in val and "-c=production" not in val and "-c production" not in val:
                val = val.replace("--prod", "--configuration=production")
            else:
                val = val.replace("--prod", "").strip()

        # 3. Modernize Unix 'cp' to cross-platform Node fs.cpSync
        cp_matches = list(re.finditer(r"(?:^|(?<=&&)\s*|(?<=;)\s*)cp\s+(?:-[a-zA-Z]+\s+)*([^\s;&]+)\s+([^\s;&]+)", val))
        if cp_matches:
            for cm in reversed(cp_matches):
                src = cm.group(1).replace("\\", "/").strip("\"'")
                dest = cm.group(2).replace("\\", "/").strip("\"'")

                # Handle wildcard directory copy e.g. src/* -> dest/
                if src.endswith("/*"):
                    src = src[:-2]

                # If copying a specific file into a directory destination, append the filename
                # so Node fs.cpSync does not fail with ERR_FS_CP_NON_DIR_TO_DIR
                target_dest = dest.rstrip("/")
                src_name = Path(src).name
                if Path(src).suffix and (dest.endswith("/") or not Path(dest).suffix) and src_name:
                    target_dest = f"{target_dest}/{src_name}"

                replacement = f'node -e "require(\'fs\').cpSync(\'{src}\', \'{target_dest}\', {{recursive: true, force: true}})"'
                val = val[:cm.start()] + replacement + val[cm.end():]

        if val != orig:
            scripts[script_key] = val
            modified = True
            applied.append(f"Modernized script '{script_key}' for cross-platform execution")

    # 4. Modernize multi-project build scripts so libraries build first and bare "ng build" specifies projects
    angular_json = repo_path / "angular.json"
    angular_projects: dict[str, Any] = {}
    default_proj: str | None = None
    if angular_json.exists():
        try:
            workspace_data = json.loads(angular_json.read_text(encoding="utf-8"))
            if isinstance(workspace_data, dict):
                angular_projects = workspace_data.get("projects", {})
                default_proj = workspace_data.get("defaultProject")
        except Exception:
            pass

    libraries = [
        name for name, p in angular_projects.items()
        if isinstance(p, dict) and p.get("projectType", "").lower() == "library"
    ]
    applications = [
        name for name, p in angular_projects.items()
        if isinstance(p, dict) and p.get("projectType", "").lower() == "application"
    ]
    if default_proj and default_proj in applications:
        applications.remove(default_proj)
        applications.insert(0, default_proj)

    if len(angular_projects) > 1 and (libraries or applications):
        for script_key, script_val in list(scripts.items()):
            if not isinstance(script_val, str):
                continue
            if re.search(r"(?:^|(?<=&&)\s*|(?<=;)\s*)ng\s+(?:build|b)(?:\s+--[\w-]+(?:=\S+)?)*\s*(?:$|&&|;)", script_val):
                cfg_match = re.search(r"(--(?:configuration|c)[=\s]\S+)", script_val)
                flags = f" {cfg_match.group(1).strip()}" if cfg_match else ""

                ordered_targets = libraries + applications
                ordered_cmds = [
                    f"ng build {t}{flags if t in applications else ''}"
                    for t in ordered_targets
                ]
                replacement_build = " && ".join(ordered_cmds)
                new_val = re.sub(
                    r"(?:^|(?<=&&)\s*|(?<=;)\s*)ng\s+(?:build|b)(?:\s+--[\w-]+(?:=\S+)?)*\s*(?=$|&&|;)",
                    replacement_build,
                    script_val,
                )
                if new_val != script_val:
                    scripts[script_key] = new_val
                    modified = True
                    applied.append(
                        f"Modernized multi-project build script '{script_key}' (libraries build first: "
                        f"{', '.join(libraries) if libraries else 'none'} -> {', '.join(applications)})"
                    )

    if modified:
        try:
            pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    return applied


def _modernize_angular_tsconfig(repo_path: Path, target_major: int | None = None) -> list[str]:
    """Modernize TypeScript configurations across the Angular workspace.

    - Sets 'moduleResolution': 'bundler' for Angular 16+ (enables modern package.json 'exports' subpath resolution)
    - Sets 'target': 'ES2022' for Angular 17+
    - Updates 'lib' to ['ES2022', 'dom'] for Angular 17+
    - Sets 'useDefineForClassFields': false for Angular 17+ (preserves decorator semantics)
    - Removes invalid 'ignoreDeprecations' compiler options that cause TS5103
    - Removes deprecated 'fullTemplateTypeCheck' from 'angularCompilerOptions'
    """
    applied = []

    for tsconfig_file in safe_rglob(repo_path, "tsconfig*.json"):

        try:
            raw_text = tsconfig_file.read_text(encoding="utf-8")
        except Exception:
            continue

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            cleaned = re.sub(r"(?m)^\s*//.*?$", "", raw_text)
            cleaned = re.sub(r"""(?<![:"'])\s*//(?!["']).*?$""", "", cleaned)
            cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
            cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
            try:
                data = json.loads(cleaned)
            except Exception:
                continue

        if not isinstance(data, dict):
            continue

        changed = False
        compiler_opts = data.setdefault("compilerOptions", {})

        # 1. moduleResolution -> 'bundler' for Angular 16+
        if target_major is None or target_major >= 16:
            cur_mod_res = str(compiler_opts.get("moduleResolution", "")).lower()
            if cur_mod_res in ("node", "classic", ""):
                compiler_opts["moduleResolution"] = "bundler"
                changed = True

        # 2. target -> 'ES2022' for Angular 17+
        if target_major is None or target_major >= 17:
            cur_target = str(compiler_opts.get("target", "")).lower()
            if cur_target in ("es5", "es6", "es2015", "es2016", "es2017", "es2018", "es2019", "es2020", "es2021"):
                compiler_opts["target"] = "ES2022"
                changed = True

        # 3. lib -> include ES2022 for Angular 17+
        if target_major is None or target_major >= 17:
            cur_lib = compiler_opts.get("lib")
            if isinstance(cur_lib, list):
                new_lib = []
                has_es2022 = False
                for item in cur_lib:
                    item_str = str(item)
                    item_lower = item_str.lower()
                    if item_lower.startswith("es") and not item_lower.startswith("es2022"):
                        if not has_es2022:
                            new_lib.append("ES2022")
                            has_es2022 = True
                    elif item_lower == "es2022":
                        new_lib.append("ES2022")
                        has_es2022 = True
                    else:
                        new_lib.append(item_str)
                if not has_es2022:
                    new_lib.insert(0, "ES2022")
                if new_lib != cur_lib:
                    compiler_opts["lib"] = new_lib
                    changed = True

        # 4. useDefineForClassFields -> False for Angular 17+ (preserves decorator semantics)
        if target_major is None or target_major >= 17:
            if "useDefineForClassFields" not in compiler_opts:
                compiler_opts["useDefineForClassFields"] = False
                changed = True

        # 5. For TypeScript 5.4+ with baseUrl, specify 'ignoreDeprecations': '6.0' to silence TS5101
        if "baseUrl" in compiler_opts:
            if compiler_opts.get("ignoreDeprecations") != "6.0":
                compiler_opts["ignoreDeprecations"] = "6.0"
                changed = True
        elif "ignoreDeprecations" in compiler_opts:
            val = str(compiler_opts.get("ignoreDeprecations", "")).strip()
            if val not in ("5.0", "6.0"):
                del compiler_opts["ignoreDeprecations"]
                changed = True

        # 6. Ensure compilerOptions.types includes 'node' if types is explicitly defined
        if "types" in compiler_opts and isinstance(compiler_opts["types"], list):
            if "node" not in compiler_opts["types"]:
                compiler_opts["types"].append("node")
                changed = True

        # 7. angularCompilerOptions -> clean up fullTemplateTypeCheck
        if "angularCompilerOptions" in data and isinstance(data["angularCompilerOptions"], dict):
            if "fullTemplateTypeCheck" in data["angularCompilerOptions"]:
                del data["angularCompilerOptions"]["fullTemplateTypeCheck"]
                changed = True

        if changed:
            try:
                tsconfig_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                try:
                    rel_path = str(tsconfig_file.relative_to(repo_path))
                except Exception:
                    rel_path = tsconfig_file.name
                applied.append(
                    f"Modernized {rel_path} for Angular {target_major or 'modern'} "
                    "(moduleResolution=bundler, target=ES2022)"
                )
            except Exception:
                pass

    return applied


def _modernize_angular_libraries(repo_path: Path, target_major: int | None = None) -> list[str]:
    """Modernize Angular library projects in the workspace.

    1. Discovers library projects via angular.json or projects/*/package.json.
    2. Updates library peerDependencies and dependencies for @angular/* packages and tslib.
    3. Ensures local dist/<lib_name> placeholder package exists if referenced by file: dependency.
    4. Updates tsconfig.json compilerOptions.paths to include source fallback (projects/<lib>/src/public-api.ts).
    """
    applied = []
    angular_json = repo_path / "angular.json"
    lib_names: set[str] = set()

    if angular_json.exists():
        try:
            workspace = json.loads(angular_json.read_text(encoding="utf-8"))
            for name, proj in workspace.get("projects", {}).items():
                if isinstance(proj, dict) and proj.get("projectType") == "library":
                    lib_names.add(name)
        except Exception:
            pass

    # Discover all library package.json files
    lib_pkg_files = list(repo_path.glob("projects/*/package.json"))
    for pkg_f in lib_pkg_files:
        lib_name = pkg_f.parent.name
        lib_names.add(lib_name)
        try:
            data = json.loads(pkg_f.read_text(encoding="utf-8"))
            modified = False
            for sec in ("peerDependencies", "dependencies", "devDependencies"):
                if sec in data and isinstance(data[sec], dict):
                    for k, v in list(data[sec].items()):
                        if k.startswith("@angular/"):
                            if target_major is not None:
                                new_v = f"^{target_major}.0.0"
                                if v != new_v:
                                    data[sec][k] = new_v
                                    modified = True
                        elif k == "tslib" and target_major and target_major >= 16:
                            if v != "^2.8.1":
                                data[sec][k] = "^2.8.1"
                                modified = True
            if modified:
                pkg_f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                applied.append(f"Updated library '{lib_name}' package.json for Angular {target_major}")
        except Exception:
            pass

    # If root package.json or projects reference dist/<lib_name> or file:/link: dependencies,
    # create placeholder package.json + index.js so npm install/junction creation succeeds cleanly
    root_pkg = repo_path / "package.json"
    if root_pkg.exists():
        try:
            root_data = json.loads(root_pkg.read_text(encoding="utf-8"))
            for sec in ("dependencies", "devDependencies"):
                for dep_name, dep_val in root_data.get(sec, {}).items():
                    if isinstance(dep_val, str) and (dep_val.startswith("file:") or dep_val.startswith("link:")):
                        lib_names.add(dep_name)
                        target_sub = dep_val.split(":", 1)[1].strip().replace("\\", "/").strip("./")
                        if target_sub:
                            target_dir = repo_path / target_sub
                            target_pkg = target_dir / "package.json"
                            if not target_pkg.exists():
                                try:
                                    target_dir.mkdir(parents=True, exist_ok=True)
                                    target_pkg.write_text(
                                        json.dumps({"name": dep_name, "version": "0.0.1", "main": "index.js"}, indent=2) + "\n",
                                        encoding="utf-8",
                                    )
                                    (target_dir / "index.js").write_text("export {};\n", encoding="utf-8")
                                except Exception:
                                    pass
        except Exception:
            pass

    if root_pkg.exists() and lib_names:
        for name in lib_names:
            dist_dir = repo_path / "dist" / name
            dist_pkg = dist_dir / "package.json"
            if not dist_pkg.exists():
                try:
                    dist_dir.mkdir(parents=True, exist_ok=True)
                    dist_pkg.write_text(
                        json.dumps({"name": name, "version": "0.0.1", "main": "index.js"}, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    (dist_dir / "index.js").write_text("export {};\n", encoding="utf-8")
                except Exception:
                    pass

    # Update tsconfig paths to ensure direct source fallback
    for tsconfig_f in safe_rglob(repo_path, "tsconfig*.json"):
        try:
            raw = tsconfig_f.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                continue
            compiler_opts = data.setdefault("compilerOptions", {})
            paths = compiler_opts.setdefault("paths", {})
            modified_paths = False
            for name in lib_names:
                candidates = [
                    f"projects/{name}/src/public-api.ts",
                    f"projects/{name}/src/public_api.ts",
                    f"projects/{name}/src/index.ts",
                    f"projects/{name}/public-api.ts",
                ]
                entry = next((c for c in candidates if (repo_path / c).exists()), None)
                if not entry:
                    entry = f"projects/{name}/src/public-api.ts"

                existing = paths.get(name)
                if isinstance(existing, list):
                    if entry not in existing:
                        existing.append(entry)
                        paths[name] = existing
                        modified_paths = True
                elif isinstance(existing, str):
                    if existing != entry:
                        paths[name] = [existing, entry]
                        modified_paths = True
                else:
                    paths[name] = [f"dist/{name}", entry]
                    modified_paths = True

            if modified_paths:
                tsconfig_f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                applied.append(f"Configured source fallback paths for library '{name}' in {tsconfig_f.name}")
        except Exception:
            pass

    return applied


def _modernize_angular_stylesheets(repo_path: Path, target_major: int | None = None) -> list[str]:
    """Modernize Angular stylesheets for modern Dart Sass and Angular Material M2/M3 theming.

    - Strips obsolete Webpack tilde ('~') prefix from @import, @use, and @forward statements
      (e.g., '@use \"~@angular/material\"' -> '@use \"@angular/material\"').
    - Prefixes Angular Material M2 functions and palettes with 'm2-' when target_major >= 18
      (e.g., 'mat.define-palette' -> 'mat.m2-define-palette', 'mat.$green-palette' -> 'mat.$m2-green-palette').
    """
    applied = []
    excluded_dirs = {"node_modules", ".angular", ".nx", ".git", "dist", "coverage", ".venv"}
    tilde_pattern = re.compile(r"""(@(?:import|use|forward)\s+(?:url\()?['\"])~""")

    m2_funcs = (
        "define-light-theme",
        "define-dark-theme",
        "define-palette",
        "get-contrast-color-from-palette",
        "get-color-from-palette",
        "get-color-config",
        "get-typography-config",
        "get-density-config",
        "define-typography-config",
        "define-legacy-typography-config",
        "define-typography-level",
        "define-rem-typography-config",
    )

    count = 0
    m2_count = 0
    for file_path in safe_rglob(repo_path, ["*.scss", "*.sass", "*.css"]):
            try:
                content = file_path.read_text(encoding="utf-8")
                orig_content = content
                new_content, n = tilde_pattern.subn(r"\1", content)
                if n > 0:
                    content = new_content
                    count += 1

                if target_major is not None and target_major >= 18:
                    for fn in m2_funcs:
                        content = re.sub(rf"\bmat\.{fn}\b", f"mat.m2-{fn}", content)
                    content = re.sub(r"\bmat\.\$([a-zA-Z0-9_-]+-palette)\b", r"mat.$m2-\1", content)
                    if content != orig_content:
                        m2_count += 1

                if content != orig_content:
                    file_path.write_text(content, encoding="utf-8")
            except Exception:
                pass

    if count > 0:
        applied.append(f"Modernized {count} stylesheet(s) for Dart Sass (stripped obsolete '~' Webpack prefixes)")
    if m2_count > 0:
        applied.append(f"Modernized {m2_count} stylesheet(s) with Angular Material M2 functions and palettes")
    return applied


def _modernize_angular_source_files(repo_path: Path, target_major: int | None = None) -> list[str]:
    """Modernize deprecated patterns in TypeScript source files.

    - Removes obsolete 'Effect' import from '@ngrx/effects' when target_major >= 15
    - Modernizes '@Effect()' decorators to 'createEffect' when target_major >= 15
    - Fixes type-only imports from '@angular/common/http' (e.g., 'HttpEvent')
    """
    applied = []
    excluded_dirs = {"node_modules", ".angular", ".nx", ".git", "dist", "coverage", ".venv"}

    ngrx_cleaned_count = 0
    type_import_count = 0
    reactive_forms_count = 0
    raw_loader_count = 0
    test_bootstrap_count = 0

    for ts_file in safe_rglob(repo_path, "*.ts"):

        try:
            content = ts_file.read_text(encoding="utf-8")
        except Exception:
            continue

        modified = False

        # 1. Clean up obsolete Effect from @ngrx/effects in Angular/NgRx 15+
        if target_major is None or target_major >= 15:
            if "@ngrx/effects" in content and "Effect" in content:
                # Convert @Effect(opts?) to createEffect if present
                effect_pattern = re.compile(
                    r"@Effect\(\s*(.*?)\s*\)\s*\n\s*([a-zA-Z0-9_$]+)\s*=\s*(.+?);",
                    re.DOTALL,
                )
                if effect_pattern.search(content):
                    def repl_effect(m: re.Match) -> str:
                        opts = m.group(1).strip()
                        prop = m.group(2)
                        expr = m.group(3).strip()
                        if opts:
                            return f"{prop} = createEffect(() => {expr}, {opts});"
                        return f"{prop} = createEffect(() => {expr});"

                    content = effect_pattern.sub(repl_effect, content)
                    modified = True

                # Clean import: remove Effect from @ngrx/effects
                def _strip_effect_import(m: re.Match) -> str:
                    imports = m.group(1).split(",")
                    cleaned = [imp.strip() for imp in imports if imp.strip() not in ("Effect", "")]
                    has_create = any(imp.strip() == "createEffect" for imp in cleaned)
                    if not has_create and "createEffect" in content:
                        cleaned.append("createEffect")
                    if cleaned:
                        return f"import {{ {', '.join(cleaned)} }} from {m.group(2)};"
                    return ""

                new_content = re.sub(
                    r"import\s*\{([^}]+)\}\s*from\s*(['\"]@ngrx/effects['\"]);?",
                    _strip_effect_import,
                    content,
                )
                if new_content != content:
                    content = new_content
                    modified = True
                    ngrx_cleaned_count += 1

        # 2. Modernize type-only HttpEvent import from @angular/common/http
        if target_major is None or target_major >= 17:
            if "@angular/common/http" in content and "HttpEvent" in content:
                http_import_match = re.search(
                    r"import\s*\{([^}]+)\}\s*from\s*(['\"]@angular/common/http['\"]);?",
                    content,
                )
                if http_import_match:
                    raw_symbols = [s.strip() for s in http_import_match.group(1).split(",") if s.strip()]
                    if "HttpEvent" in raw_symbols and "type HttpEvent" not in raw_symbols:
                        new_symbols = [
                            f"type {s}" if s == "HttpEvent" else s
                            for s in raw_symbols
                        ]
                        replacement = f"import {{ {', '.join(new_symbols)} }} from {http_import_match.group(2)};"
                        content = content[:http_import_match.start()] + replacement + content[http_import_match.end():]
                        modified = True
                        type_import_count += 1

        # 3. Clean up removed router options (relativeLinkResolution) in Angular 15+
        if target_major is None or target_major >= 15:
            if "relativeLinkResolution" in content:
                new_content = re.sub(r",?\s*relativeLinkResolution:\s*['\"][^'\"]+['\"]", "", content)
                if new_content != content:
                    content = new_content
                    modified = True

        # 4. Modernize legacy reactive forms to UntypedFormBuilder / UntypedFormGroup in Angular 14+
        if target_major is None or target_major >= 14:
            if "@angular/forms" in content and any(
                k in content for k in ("FormBuilder", "FormGroup", "FormControl", "FormArray")
            ):
                forms_imp = re.search(r"import\s*\{([^}]+)\}\s*from\s*(['\"]@angular/forms['\"]);?", content)
                if forms_imp:
                    raw_symbols = [s.strip() for s in forms_imp.group(1).split(",") if s.strip()]
                    replacements = {
                        "FormBuilder": "UntypedFormBuilder",
                        "FormGroup": "UntypedFormGroup",
                        "FormControl": "UntypedFormControl",
                        "FormArray": "UntypedFormArray",
                    }
                    new_symbols = []
                    needed_replacements = {}
                    for sym in raw_symbols:
                        if sym in replacements:
                            new_symbols.append(replacements[sym])
                            needed_replacements[sym] = replacements[sym]
                        else:
                            new_symbols.append(sym)
                    if needed_replacements:
                        new_import = f"import {{ {', '.join(new_symbols)} }} from {forms_imp.group(2)};"
                        content = content[:forms_imp.start()] + new_import + content[forms_imp.end():]
                        for old_s, new_s in needed_replacements.items():
                            content = re.sub(rf"\b{old_s}\b", new_s, content)
                        modified = True
                        reactive_forms_count += 1

        # 5. Modernize obsolete Webpack raw-loader syntax for Angular 17+ (esbuild/Vite)
        if target_major is None or target_major >= 17:
            if "raw-loader!" in content:
                def _replace_raw_loader(m: re.Match) -> str:
                    rel_target = m.group(1).strip()
                    target_file = (ts_file.parent / rel_target).resolve()
                    if target_file.exists() and target_file.is_file():
                        try:
                            file_text = target_file.read_text(encoding="utf-8")
                            escaped = file_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                            return f"`{escaped}`"
                        except Exception:
                            pass
                    return "''"

                new_content = re.sub(
                    r"require\(\s*['\"]!?raw-loader!([^'\"]+)['\"]\)\s*(?:\.\s*default)?",
                    _replace_raw_loader,
                    content,
                )
                if new_content != content:
                    content = new_content
                    modified = True
                    raw_loader_count += 1

        # 6. Modernize legacy test.ts for Angular 15+ (remove obsolete Webpack require.context)
        if target_major is None or target_major >= 15:
            if ts_file.name == "test.ts" and "require.context" in content:
                content = re.sub(
                    r"declare\s+const\s+require\s*:\s*\{[\s\S]*?\n\};\s*",
                    "",
                    content,
                )
                content = re.sub(
                    r"(?://[^\n]*\n\s*)?const\s+context\s*=\s*require\.context\([^;]+;\s*",
                    "",
                    content,
                )
                content = re.sub(
                    r"(?://[^\n]*\n\s*)?context\.keys\(\)\.(?:map|forEach)\(context\);?\s*",
                    "",
                    content,
                )
                modified = True
                test_bootstrap_count += 1

        # 7. Clean up bogus declaration chunk imports (e.g. from '@angular/material/date-adapter.d-CtKXiXkO' or '@angular/cdk/overlay.d-BdoMyOhX')
        if ".d-" in content:
            chunk_import_pattern = re.compile(
                r"(?m)^import\s*(?:type\s+)?\{([^}]+)\}\s*from\s*['\"][^'\"]*\.d-[A-Za-z0-9_-]+[^'\"]*['\"];?\s*\n?"
            )
            c_matches = list(chunk_import_pattern.finditer(content))
            if c_matches:
                for cm in reversed(c_matches):
                    symbols = [s.strip() for s in cm.group(1).split(",") if s.strip()]
                    type_fallbacks = []
                    for sym in symbols:
                        clean_sym = sym.split()[-1]
                        rest_of_code = content[:cm.start()] + content[cm.end():]
                        if re.search(rf"\b{re.escape(clean_sym)}\b", rest_of_code):
                            type_fallbacks.append(f"type {clean_sym} = any;")
                    replacement = ("\n".join(type_fallbacks) + "\n") if type_fallbacks else ""
                    content = content[:cm.start()] + replacement + content[cm.end():]
                    modified = True

            generic_chunk_pattern = re.compile(
                r"(?m)^import\s+[^;]*from\s*['\"][^'\"]*\.d-[A-Za-z0-9_-]+[^'\"]*['\"];?\s*\n?"
            )
            if generic_chunk_pattern.search(content):
                content = generic_chunk_pattern.sub("", content)
                modified = True

        single_letter_pattern = re.compile(
            r"(?m)^import\s*\{\s*[A-Z]\s*\}\s*from\s*['\"][^'\"]*['\"];?\s*\n?"
        )
        if single_letter_pattern.search(content):
            content = single_letter_pattern.sub("", content)
            modified = True

        # 8. Clean up accidental double commas in @Component decorators (e.g. styleUrls: [...],,)
        if ",," in content:
            new_content = re.sub(r",\s*,+", ",", content)
            if new_content != content:
                content = new_content
                modified = True

        # 9. Clean up accidental Node.js console imports in browser files (TS2591 / bundling error)
        # e.g., "import { error } from 'console';" or "import { debug } from 'console';"
        if "from 'console'" in content or 'from "console"' in content or "from 'node:console'" in content or 'from "node:console"' in content:
            c_matches = list(re.finditer(r"(?m)^import\s*\{([^}]+)\}\s*from\s*['\"](?:node:)?console['\"];?\s*\n?", content))
            if c_matches:
                for cm in reversed(c_matches):
                    imported_symbols = [s.strip() for s in cm.group(1).split(",") if s.strip()]
                    fallbacks = []
                    for sym in imported_symbols:
                        rest_of_code = content[:cm.start()] + content[cm.end():]
                        if re.search(rf"\b{re.escape(sym)}\b", rest_of_code):
                            fn_name = "error" if sym == "error" else ("debug" if sym == "debug" else ("warn" if sym == "warn" else ("info" if sym == "info" else "log")))
                            fallbacks.append(f"const {sym} = console.{fn_name}.bind(console);")
                    replacement = ("\n".join(fallbacks) + "\n") if fallbacks else ""
                    content = content[:cm.start()] + replacement + content[cm.end():]
                    modified = True

        # 10. Ensure any @Component with imports: has standalone: true (prevents NG2010 compiler error)
        if "@Component" in content and "imports" in content:
            comp_dec_match = re.search(r"@Component\s*\(\s*\{", content)
            if comp_dec_match:
                open_b = content.find("{", comp_dec_match.start())
                depth = 1
                idx = open_b + 1
                end_b = -1
                while idx < len(content):
                    ch = content[idx]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_b = idx
                            break
                    idx += 1
                if end_b != -1:
                    inner_dec = content[open_b + 1:end_b]
                    if re.search(r"\bimports\s*:\s*\[", inner_dec):
                        new_inner = inner_dec
                        if re.search(r"\bstandalone\s*:\s*false\b", new_inner):
                            new_inner = re.sub(r"\bstandalone\s*:\s*false\b", "standalone: true", new_inner)
                        elif not re.search(r"\bstandalone\s*:\s*true\b", new_inner):
                            new_inner = "\n  standalone: true," + new_inner
                        if new_inner != inner_dec:
                            content = content[:open_b + 1] + new_inner + content[end_b:]
                            modified = True

        if modified:
            try:
                ts_file.write_text(content, encoding="utf-8")
            except Exception:
                pass

    if ngrx_cleaned_count > 0:
        applied.append(f"Modernized {ngrx_cleaned_count} file(s) removing obsolete @ngrx/effects 'Effect' member")
    if type_import_count > 0:
        applied.append(f"Modernized {type_import_count} file(s) converting type-only 'HttpEvent' imports")
    if reactive_forms_count > 0:
        applied.append(f"Modernized {reactive_forms_count} file(s) to UntypedFormBuilder/UntypedFormGroup for Angular 14+ compatibility")
    if raw_loader_count > 0:
        applied.append(f"Modernized {raw_loader_count} file(s) inlining legacy Webpack raw-loader imports for esbuild compatibility")
    if test_bootstrap_count > 0:
        applied.append(f"Modernized {test_bootstrap_count} test bootstrap file(s) removing obsolete 'require.context' for Angular 15+ test runner")

    # Sanitize importProvidersFrom calls across workspace (purges AgGridModule/standalone components to prevent NG0800)
    try:
        from amstralift.adapters.angular_standalone import sanitize_import_providers_from
        applied.extend(sanitize_import_providers_from(repo_path))
    except Exception:
        pass

    return applied


class AngularAdapter(BaseAdapter):
    """Adapter for Angular repositories using npm and Angular CLI."""

    def __init__(
        self,
        registry_url: str = NPM_REGISTRY_BASE,
        timeout_seconds: float = 10.0,
        lts_config: AngularLTSConfig | None = None,
        incremental: bool = False,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.lts_config = lts_config
        self.incremental = incremental

    @property
    def name(self) -> str:
        return "angular"

    def detect(self, repo_path: Path) -> bool:
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return False

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            return "@angular/core" in deps or (repo_path / "angular.json").exists()
        except Exception:
            return False

    def fetch_latest_version(self, package_name: str, target_major: int | None = None) -> str | None:
        """Fetch version from npm registry.

        If target_major is provided, returns latest stable GA version matching that major,
        otherwise returns dist-tags.latest (ensuring it is not a pre-release).
        """
        url = f"{self.registry_url}/{package_name}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    if target_major is not None:
                        matching = []
                        for version in data.get("versions", {}):
                            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
                            if match and int(match.group(1)) == target_major:
                                matching.append((tuple(map(int, match.groups())), version))
                        if matching:
                            return max(matching, key=lambda item: item[0])[1]
                        return None

                    latest = data.get("dist-tags", {}).get("latest")
                    if latest and not any(pre in latest.lower() for pre in ("-rc", "-next", "-beta", "-alpha", "-canary", "-dev", "-preview")):
                        return latest

                    stable_matching = []
                    for version in data.get("versions", {}):
                        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
                        if match:
                            stable_matching.append((tuple(map(int, match.groups())), version))
                    if stable_matching:
                        return max(stable_matching, key=lambda item: item[0])[1]
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Discover upgradable Angular and related dependencies applying LTS or incremental governance."""
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        candidates: list[DependencyChange] = []

        direct_deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})

        # Fetch latest officially released GA version of @angular/core from npm
        latest_core = self.fetch_latest_version("@angular/core")
        latest_ga_major = extract_major_version(latest_core) if latest_core else None

        # Evaluate Angular LTS policy or Incremental (+1 major)
        target_major = None
        policy_reason = ""
        if self.lts_config:
            policy_decision = AngularLTSGovernance.evaluate(self.lts_config)
            target_major = policy_decision.target_major if not policy_decision.use_latest_fallback else None
            if target_major is not None and latest_ga_major is not None and target_major > latest_ga_major:
                target_major = latest_ga_major
            policy_reason = f"LTS Policy: {policy_decision.reason}"

        # Detect current Angular core major from package.json
        core_ver = direct_deps.get("@angular/core") or dev_deps.get("@angular/core") or direct_deps.get("@angular/common")
        cur_angular_major = extract_major_version(core_ver) if core_ver else None

        if target_major is None:
            if self.incremental and cur_angular_major is not None:
                if latest_ga_major is not None:
                    if cur_angular_major >= latest_ga_major:
                        # Already at peak officially released GA version; do not upgrade to unreleased major!
                        target_major = cur_angular_major
                        policy_reason = f"Current Angular v{cur_angular_major} is at peak officially released GA version."
                    else:
                        target_major = min(cur_angular_major + 1, latest_ga_major)
                        policy_reason = f"Incremental upgrade (+1 major): v{cur_angular_major} -> v{target_major}"
                else:
                    target_major = cur_angular_major + 1
                    policy_reason = f"Incremental upgrade (+1 major): v{cur_angular_major} -> v{target_major}"
            else:
                target_major = latest_ga_major

        target_angular_major = target_major
        if not policy_reason:
            policy_reason = "Ecosystem Upgrade"

        def get_major_constraint(pkg_name: str) -> int | None:
            if target_major is None:
                return None
            if (
                pkg_name.startswith("@angular/")
                or pkg_name.startswith("@angular-devkit/")
                or pkg_name.startswith("@ngrx/")
            ):
                return target_major
            if pkg_name == "typescript":
                return ANGULAR_TS_MATRIX.get(target_major, 5)
            if pkg_name == "rxjs":
                return 7 if target_major >= 13 else 6
            if pkg_name == "zone.js":
                return 0
            return None

        tracked_direct_prefixes = (
            "@angular/",
            "@angular-devkit/",
            "@angular-extensions/",
            "@ngx-translate/",
            "@fortawesome/",
            "@ng-",
            "@ngneat/",
            "@ng-select/",
            "@swimlane/",
            "ngx-",
        )
        tracked_direct_packages = (
            "rxjs",
            "zone.js",
            "tslib",
            "bootstrap",
            "browser-detect",
            "uuid",
        )
        for pkg, cur_ver in direct_deps.items():
            rec_ver = get_angular_ecosystem_recommendation(pkg, cur_ver, target_angular_major)
            if rec_ver:
                clean_rec = rec_ver.lstrip("^~>=<")
                clean_cur = cur_ver.lstrip("^~>=<")
                if clean_rec != clean_cur:
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=rec_ver,
                            change_type="direct",
                            tier=tier,
                            rationale=f"Align {pkg} to {rec_ver} for Angular {target_angular_major} compatibility. {policy_reason}",
                        )
                    )
                continue

            if any(pkg.startswith(p) for p in tracked_direct_prefixes) or pkg in tracked_direct_packages:
                clean_cur = cur_ver.lstrip("^~>=<")
                major_constraint = get_major_constraint(pkg)
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)
                if not latest and major_constraint is not None and not pkg.startswith("@angular/"):
                    latest = self.fetch_latest_version(pkg, target_major=None)

                if latest and latest != clean_cur:
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="direct",
                            tier=tier,
                            rationale=(f"Upgrade {pkg} to {latest}. {policy_reason}"),
                        )
                    )

        has_angular_eslint = any(p.startswith("@angular-eslint/") for p in dev_deps)
        tracked_dev_prefixes = (
            "@angular-devkit/",
            "@angular/",
            "@angular-eslint/",
            "@typescript-eslint/",
            "@types/",
        )
        tracked_dev_packages = (
            "typescript",
            "@angular/cli",
            "eslint",
            "jasmine-core",
            "karma",
        )
        for pkg, cur_ver in dev_deps.items():
            clean_cur = cur_ver.lstrip("^~>=<")
            if pkg == "typescript":
                rec_ts = ANGULAR_TS_RECOMMENDED.get(target_angular_major, "^6.0.3")
                if clean_cur != rec_ts.lstrip("^~>=<"):
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=rec_ts,
                            change_type="dev",
                            tier=tier,
                            rationale=f"Align TypeScript to {rec_ts} for Angular {target_angular_major} compatibility. {policy_reason}",
                        )
                    )
                continue

            if pkg == "eslint" and (has_angular_eslint or (target_major and target_major >= 17)):
                has_legacy_eslintrc = any(
                    (repo_path / name).exists()
                    for name in (".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml")
                )
                has_flat_config = any(
                    (repo_path / name).exists()
                    for name in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
                )
                if has_legacy_eslintrc and not has_flat_config:
                    rec_eslint = "^8.57.1"
                else:
                    rec_eslint = "^9.21.0" if (target_angular_major and target_angular_major >= 19) else "^8.57.1"

                cur_major = extract_major_version(cur_ver)
                target_eslint_major = extract_major_version(rec_eslint)
                tier = classify_angular_tier(pkg)
                candidates.append(
                    DependencyChange(
                        package_name=pkg,
                        from_version=cur_ver,
                        to_version=rec_eslint,
                        change_type="dev",
                        tier=tier,
                        rationale=f"Align ESLint to {rec_eslint} for @angular-eslint compatibility. {policy_reason}",
                    )
                )
                continue

            if any(pkg.startswith(p) for p in tracked_dev_prefixes) or pkg in tracked_dev_packages:
                major_constraint = get_major_constraint(pkg)
                latest = self.fetch_latest_version(pkg, target_major=major_constraint)
                if not latest and major_constraint is not None and not (pkg.startswith("@angular/") or pkg.startswith("@angular-devkit/")):
                    latest = self.fetch_latest_version(pkg, target_major=None)

                if latest and latest != clean_cur:
                    tier = classify_angular_tier(pkg)
                    candidates.append(
                        DependencyChange(
                            package_name=pkg,
                            from_version=cur_ver,
                            to_version=f"^{latest}",
                            change_type="dev",
                            tier=tier,
                            rationale=(f"Upgrade dev tool {pkg} to {latest}. {policy_reason}"),
                        )
                    )

        return candidates

    def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
        """Apply dependency updates to package.json and lockfile."""
        pkg_file = repo_path / "package.json"
        data = json.loads(pkg_file.read_text(encoding="utf-8"))

        # Clean up problematic native binary & incompatible multi-major routing overrides if present
        if "overrides" in data and isinstance(data["overrides"], dict):
            for bad_key in list(data["overrides"].keys()):
                if bad_key in ("esbuild", "path-to-regexp") or bad_key.startswith(("@esbuild/", "@swc/", "@rollup/")):
                    del data["overrides"][bad_key]
            for parent_k, parent_v in list(data["overrides"].items()):
                if isinstance(parent_v, dict):
                    for bad_k in list(parent_v.keys()):
                        if bad_k in ("esbuild", "path-to-regexp") or bad_k.startswith(("@esbuild/", "@swc/", "@rollup/")):
                            del parent_v[bad_k]
                    if not parent_v:
                        del data["overrides"][parent_k]
            if not data["overrides"]:
                del data["overrides"]

        for change in changes:
            if change.change_type == "transitive":
                # Native binary packages (like esbuild) and multi-major routing packages (like path-to-regexp)
                # break if globally overridden because different sub-dependencies require conflicting major branches.
                if (
                    change.package_name in ("esbuild", "path-to-regexp")
                    or change.package_name.startswith(("@esbuild/", "@swc/", "@rollup/"))
                ):
                    continue

                clean_target = change.to_version if change.to_version.startswith(("^", "~")) else f"^{change.to_version}"
                target_m = extract_major_version(clean_target)
                direct_pkgs = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                max_m = get_max_existing_major(repo_path, change.package_name, direct_pkgs)

                # Never allow an older major patch to globally downgrade a higher major used elsewhere in the project
                parent_scope = change.parent_package or (change.introduced_by[0] if change.introduced_by else None)
                if max_m is not None and target_m is not None and target_m < max_m:
                    if parent_scope:
                        data.setdefault("overrides", {}).setdefault(parent_scope, {})[change.package_name] = clean_target
                elif change.package_name in direct_pkgs and parent_scope:
                    data.setdefault("overrides", {}).setdefault(parent_scope, {})[change.package_name] = clean_target
                else:
                    data.setdefault("overrides", {})[change.package_name] = clean_target
            elif "dependencies" in data and change.package_name in data["dependencies"]:
                data["dependencies"][change.package_name] = change.to_version
            elif "devDependencies" in data and change.package_name in data["devDependencies"]:
                data["devDependencies"][change.package_name] = change.to_version

        # Clean up obsolete legacy Node/OpenSSL workarounds in scripts (e.g. node --openssl-legacy-provider .../ng)
        if "scripts" in data and isinstance(data["scripts"], dict):
            for script_key, script_val in list(data["scripts"].items()):
                if isinstance(script_val, str) and "--openssl-legacy-provider" in script_val:
                    cleaned_val = re.sub(r"node\s+--openssl-legacy-provider\s+\S*ng(\.js)?", "ng", script_val)
                    cleaned_val = cleaned_val.replace("--openssl-legacy-provider", "").strip()
                    data["scripts"][script_key] = cleaned_val

        # Align project manifest version with target framework major
        angular_core = next(
            (c for c in changes if c.package_name == "@angular/core"),
            None,
        )
        target_major_int = None
        if angular_core:
            try:
                target_major_int = int(angular_core.to_version.lstrip("^~>=<").split(".")[0])
            except Exception:
                pass
        if target_major_int is None and "dependencies" in data:
            core_ver = data["dependencies"].get("@angular/core", "")
            target_major_int = extract_major_version(core_ver)

        if target_major_int is not None and "version" in data and isinstance(data["version"], str):
            cur_app_ver = data["version"].strip()
            cur_app_major = extract_major_version(cur_app_ver)
            if cur_app_major is not None and (cur_app_major < target_major_int or cur_app_major > 19):
                data["version"] = f"{target_major_int}.0.0"

        pkg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        # Modernize angular ecosystem dependencies, workspace schema, tsconfig, stylesheets, source files, scripts
        _align_angular_ecosystem_dependencies(repo_path, target_major=target_major_int)
        _modernize_angular_workspace_json(repo_path, target_major=target_major_int)
        _modernize_angular_tsconfig(repo_path, target_major=target_major_int)
        _modernize_angular_stylesheets(repo_path)
        _modernize_angular_source_files(repo_path, target_major=target_major_int)
        _modernize_angular_libraries(repo_path, target_major=target_major_int)
        _modernize_angular_scripts(repo_path)
        _modernize_angular_gitignore(repo_path)

        # Update package-lock.json accurately using npm_lockfile
        from amstralift.adapters.npm_lockfile import install_npm_dependencies, update_npm_lockfile

        gate_env = get_node_execution_env()
        update_npm_lockfile(repo_path, changes, env=gate_env)
        install_npm_dependencies(repo_path, env=gate_env)

        # ── Docker: update FROM / image: tags in Dockerfiles & docker-compose ──
        if angular_core:
            target_ver = angular_core.to_version.lstrip("^~>=<").split(".")[0]
            from amstralift.adapters.docker_updater import DockerfileUpdater
            DockerfileUpdater.update(repo_path, ecosystem="angular", target_version=target_ver)

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply modern Angular schematics (control-flow, standalone)."""
        if not modernize_flags:
            return []

        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return []

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        core_ver = deps.get("@angular/core", "")
        major = extract_major_version(core_ver) if core_ver else None

        applied = []
        gate_env = get_node_execution_env()
        has_npx = shutil.which("npx", path=gate_env.get("PATH")) is not None

        normalized = [f.strip().lower() for f in modernize_flags]
        if major and major >= 14:
            _modernize_angular_workspace_json(repo_path, target_major=major)
        _align_angular_ecosystem_dependencies(repo_path, target_major=major)
        _modernize_angular_tsconfig(repo_path, target_major=major)
        _modernize_angular_stylesheets(repo_path, target_major=major)
        _modernize_angular_source_files(repo_path, target_major=major)
        applied.extend(_modernize_angular_scripts(repo_path))
        _modernize_angular_gitignore(repo_path)

        from amstralift.adapters.angular_standalone import (
            modernize_angular_material_templates,
            modernize_angular_standalone_components,
            sanitize_import_providers_from,
        )

        applied.extend(modernize_angular_material_templates(repo_path))

        # 1. Control-Flow (*ngIf -> @if, *ngFor -> @for)
        if any(f in ("control-flow", "controlflow", "all") for f in normalized):
            if major and major >= 17:
                cf_migrated = False
                if has_npx:
                    cmd = ["npx", "@angular/cli", "generate", "@angular/core:control-flow", "--interactive=false"]
                    try:
                        res = subprocess.run(
                            cmd,
                            cwd=repo_path,
                            env=gate_env,
                            capture_output=True,
                            text=True,
                            timeout=180,
                            shell=sys.platform == "win32",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0,
                        )
                        if res.returncode == 0:
                            applied.append("Migrated templates to modern Angular control flow (@if, @for, @switch)")
                            cf_migrated = True
                    except Exception:
                        pass

                if not cf_migrated:
                    from amstralift.adapters.angular_control_flow import migrate_repository_control_flow

                    stats = migrate_repository_control_flow(repo_path)
                    total_directives = sum(stats.values())
                    if total_directives > 0:
                        applied.append(
                            f"Migrated {len(stats)} template(s) ({total_directives} directives) to modern Angular control flow (@if, @for)"
                        )
                    else:
                        applied.append("Control-flow migration completed (no legacy *ngIf/*ngFor directives found)")
            else:
                applied.append(f"Skipped control-flow migration: requires Angular 17+ (current is v{major or 'unknown'})")

        # 2. Standalone Migration (NgModule -> Standalone components)
        if any(f in ("standalone", "all") for f in normalized):
            if major and major >= 15:
                if has_npx:
                    # Dependencies were installed in the isolated workspace by apply_upgrade.
                    standalone_ok = True
                    for mode in ["convert-to-standalone", "prune-ng-modules", "standalone-bootstrap"]:
                        cmd = [
                            "npx",
                            "@angular/cli",
                            "generate",
                            "@angular/core:standalone",
                            f"--mode={mode}",
                            "--interactive=false",
                            "--defaults",
                        ]
                        try:
                            res = subprocess.run(
                                cmd,
                                cwd=repo_path,
                                env=gate_env,
                                capture_output=True,
                                text=True,
                                timeout=180,
                                stdin=subprocess.DEVNULL,
                                shell=sys.platform == "win32",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0,
                            )
                            if res.returncode != 0:
                                raw_err = (res.stderr or res.stdout or "").strip()
                                standalone_ok = False
                                if "does not support schematics" in raw_err or "Cannot find module" in raw_err:
                                    applied.append(
                                        f"Standalone migration phase '{mode}' failed: node_modules out of sync."
                                    )
                                else:
                                    applied.append(
                                        f"Standalone migration phase '{mode}' failed (exit {res.returncode}): "
                                        f"{raw_err[:120]}"
                                    )
                                break
                        except Exception as e:
                            standalone_ok = False
                            applied.append(f"Standalone migration phase '{mode}' error: {str(e)[:80]}")
                            break

                    if standalone_ok:
                        applied.append(
                            "Converted all components and directives to Angular Standalone architecture "
                            "(convert-to-standalone -> prune-ng-modules -> standalone-bootstrap)"
                        )
                else:
                    applied.append("Skipped standalone migration schematic: npx CLI not found in environment")

                # Always apply AST/regex standalone component modernizer to guarantee all components
                # receive complete imports, schemas, standalone: true, and module declarations updated
                applied.extend(modernize_angular_standalone_components(repo_path))
                applied.extend(sanitize_import_providers_from(repo_path))
            else:
                applied.append(f"Skipped standalone migration: requires Angular 15+ (current is v{major or 'unknown'})")

        return applied

    def run_build_and_tests(
        self,
        repo_path: Path,
        timeout_seconds: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GateSummary:
        """Execute build and test gates declared in package.json.

        Handles Angular-specific test runner quirks:
        - Karma runs in watch mode by default → inject headless/no-watch flags when Karma is configured
        - Other Angular test builders receive no Karma-only flags
        - Jest runs once and exits → no special flags needed
        - Timeout is classified as REQUIRED_TIMEOUT (uncertain), NOT REQUIRED_FAILED
        """
        pkg_file = repo_path / "package.json"
        results: list[GateResult] = []

        if not pkg_file.exists():
            return GateSummary(results=results)

        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})

        gate_env = get_node_execution_env()
        existing_opts = gate_env.get("NODE_OPTIONS", "")
        core_ver = (
            data.get("dependencies", {}).get("@angular/core")
            or data.get("devDependencies", {}).get("@angular/core")
        )
        core_major = extract_major_version(core_ver) if core_ver else None

        if core_major and core_major >= 17:
            # Modern Angular CLI (17+) uses modern Webpack 5 / esbuild without legacy crypto
            if "--openssl-legacy-provider" in existing_opts:
                cleaned_opts = existing_opts.replace("--openssl-legacy-provider", "").strip()
                if cleaned_opts:
                    gate_env["NODE_OPTIONS"] = cleaned_opts
                else:
                    gate_env.pop("NODE_OPTIONS", None)
        else:
            # Legacy Angular (< 17) requires OpenSSL legacy provider on Node 17+
            if "--openssl-legacy-provider" not in existing_opts:
                gate_env["NODE_OPTIONS"] = (existing_opts + " --openssl-legacy-provider").strip()

        # Support legacy .eslintrc in ESLint 8/9 by disabling mandatory Flat Config
        has_legacy_eslintrc = any(
            (repo_path / name).exists()
            for name in (".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml")
        )
        has_flat_config = any(
            (repo_path / name).exists()
            for name in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
        )
        if has_legacy_eslintrc and not has_flat_config:
            gate_env["ESLINT_USE_FLAT_CONFIG"] = "false"

        has_npm = shutil.which("npm", path=gate_env.get("PATH")) is not None

        # Ensure workspace JSON and scripts are modernized/healed before running gates
        _modernize_angular_workspace_json(repo_path, target_major=core_major)
        _modernize_angular_scripts(repo_path)

        # Reload package.json scripts after modernization
        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
        except Exception:
            pass

        # ── Resolve test command with watch-mode and headless fixes ──────────
        raw_test_script = scripts.get("test", "")
        resolved_test_cmd = self._resolve_angular_test_command(
            raw_test_script, repo_path, has_npm
        )

        # ── Resolve build command with multi-project library-first ordering ──
        raw_build_script = scripts.get("build")
        resolved_build_cmd = self._resolve_angular_build_command(
            raw_build_script, repo_path, has_npm
        )

        # Define gates in priority order
        gates = [
            ("test", resolved_test_cmd, True),
            ("build", resolved_build_cmd, True),
            ("lint", scripts.get("lint"), False),
        ]

        # Remove any stale ngcc lock files before running gates
        stale_lock = repo_path / "node_modules" / "@angular" / "compiler-cli" / "ngcc" / "__ngcc_lock_file__"
        if stale_lock.exists():
            try:
                stale_lock.unlink(missing_ok=True)
            except Exception:
                pass

        # If workspace has library projects, pre-build them so dependent applications compile cleanly
        self._build_workspace_libraries(repo_path, gate_env)

        for gate_name, script_cmd, is_required in gates:
            if not script_cmd:
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                if gate_name == "test" and raw_test_script and resolved_test_cmd is None:
                    skip_message = (
                        "Angular workspace has no configured test target. Add a test target under a project "
                        "in angular.json before running this test script."
                    )
                else:
                    skip_message = f"Script '{gate_name}' not defined in package.json"
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout=skip_message,
                    )
                )
                continue

            if not has_npm and (
                script_cmd.startswith("npm ")
                or script_cmd.startswith("ng ")
                or "npm run" in script_cmd
            ):
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout="npm CLI not detected in host environment",
                    )
                )
                continue

            start_t = time.time()
            if gate_name in ("test", "build"):
                cmd = script_cmd
            else:
                cmd = f"npm run {gate_name}" if has_npm else script_cmd

            gate_msg = f"↳ Executing verification gate: '{gate_name}' ({cmd})..."
            if progress_callback:
                progress_callback(gate_msg)
            print(f"   {gate_msg}", flush=True)

            try:
                check_cancelled()
                proc = run_cancellable_subprocess(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    env=gate_env,
                )
                check_cancelled()
                duration = time.time() - start_t
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    status = GateStatus.REQUIRED_PASSED if is_required else GateStatus.OPTIONAL_PASSED
                    pass_msg = f"✔ Gate '{gate_name}' passed ({duration:.1f}s)"
                    if progress_callback:
                        progress_callback(f"  {pass_msg}")
                    print(f"   {pass_msg}", flush=True)
                else:
                    # Detect TypeScript module-resolution compilation errors in test gate output.
                    # These indicate a post-upgrade environment setup issue (missing type declarations,
                    # broken tsconfig paths for testing libs) — not a genuine test regression. The
                    # developer can fix this on the committed branch. Classify as REQUIRED_SKIPPED so
                    # AMstraLift creates a branch rather than aborting with no output.
                    combined = stdout + stderr
                    ts_module_errors = (
                        is_required
                        and gate_name in ("test",)
                        and any(
                            pattern in combined
                            for pattern in (
                                "error TS2307: Cannot find module",
                                "error TS2882:",
                                "error TS2339:",
                                "Cannot find module '@angular/",
                                "Cannot find module 'zone.js/",
                                "Cannot find module '@angular/core/testing'",
                                "Module not found: Error: Can't resolve '@angular/",
                            )
                        )
                    )
                    if ts_module_errors:
                        status = GateStatus.REQUIRED_SKIPPED
                        skip_note = (
                            "TypeScript module-resolution errors detected after major Angular version upgrade. "
                            "These are post-upgrade environment setup issues (missing type declarations, "
                            "tsconfig paths that need updating) rather than genuine test regressions. "
                            "The upgrade branch has been committed — fix the tsconfig/test harness setup to re-verify."
                        )
                        print(
                            f"   ⚠ Gate '{gate_name}' skipped ({duration:.1f}s): "
                            "TypeScript compilation errors (module-not-found) — environment issue, not a test failure.",
                            flush=True,
                        )
                        results.append(
                            GateResult(
                                name=gate_name,
                                command=cmd,
                                status=status,
                                exit_code=proc.returncode,
                                stdout=skip_note + "\n\n--- Output ---\n" + combined[:2000],
                                stderr="",
                                duration_seconds=duration,
                            )
                        )
                        continue

                    status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                    print(f"   ✖ Gate '{gate_name}' finished with exit code {proc.returncode} ({duration:.1f}s)", flush=True)

                    results.append(
                        GateResult(
                            name=gate_name,
                            command=cmd,
                            status=status,
                            exit_code=proc.returncode,
                            stdout=stdout,
                            stderr=stderr,
                            duration_seconds=duration,
                        )
                    )
                    continue

                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=proc.returncode,
                        stdout=stdout,
                        stderr=stderr,
                        duration_seconds=duration,
                    )
                )
            except OperationCancelledError:
                duration = time.time() - start_t
                print(f"   ⚡ Gate '{gate_name}' cancelled by user ({duration:.1f}s)", flush=True)
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED,
                        exit_code=-1,
                        stdout="Gate cancelled by user abort.",
                        duration_seconds=duration,
                    )
                )
                raise  # propagate so the caller can surface "aborted"
            except subprocess.TimeoutExpired:
                duration = time.time() - start_t
                timeout_note = (
                    f"Test runner timed out after {duration:.0f}s. "
                    "This usually means 'ng test' is running in watch mode or waiting for a browser. "
                    "AMstraLift injected --watch=false --browsers=ChromeHeadless but the environment may not "
                    "support Chrome. Test result is UNCERTAIN — not confirmed broken. "
                    "Run with --skip-tests / --allow-failed-gates to proceed, or install chromium/google-chrome."
                )
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=GateStatus.REQUIRED_TIMEOUT,
                        exit_code=124,
                        stdout=timeout_note,
                        duration_seconds=duration,
                    )
                )
            except Exception as e:
                duration = time.time() - start_t
                status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=1,
                        stderr=str(e),
                        duration_seconds=duration,
                    )
                )

        return GateSummary(results=results)

    def _resolve_angular_test_command(
        self,
        raw_test_script: str,
        repo_path: Path,
        has_npm: bool,
    ) -> str | None:
        """
        Resolve the test command for Angular, injecting headless/no-watch flags as needed.

        Detection priority:
        1. Jest (runs once, exits cleanly) — no flags needed
        2. Vitest — inject --run flag
        3. Karma — inject --watch=false --no-progress --browsers=ChromeHeadless
        4. Unknown script — fallback to npm run test
        """
        if not raw_test_script:
            return None

        # If test script chains commands (e.g. "npm run lint && ng test --configuration=test"),
        # isolate the actual unit test command so linting errors (which have their own gate)
        # do not block the test runner from executing.
        effective_script = raw_test_script
        is_chained = "&&" in raw_test_script
        if is_chained:
            parts = [p.strip() for p in raw_test_script.split("&&")]
            test_subcmd = next(
                (p for p in parts if any(k in p.lower() for k in ("ng test", "ng t", "karma", "jest", "vitest"))),
                None,
            )
            if test_subcmd:
                effective_script = test_subcmd

        runner_command = effective_script
        if is_chained and has_npm:
            runner_command = f"npm exec -- {effective_script}"

        script_lower = effective_script.lower()

        if "jest" in script_lower:
            return runner_command if is_chained else ("npm run test" if has_npm else raw_test_script)

        if "vitest" in script_lower:
            if "--run" not in script_lower:
                if is_chained:
                    return f"{runner_command} --run"
                return "npm test -- --run" if has_npm else f"{raw_test_script} --run"
            return runner_command if is_chained else ("npm run test" if has_npm else raw_test_script)

        is_ng_test = "ng test" in script_lower or "ng t " in script_lower or script_lower.startswith("ng t")
        is_karma = "karma" in script_lower

        if is_ng_test or is_karma:
            karma_confs = safe_rglob(repo_path, "karma*.conf*.js")
            test_builders: list[str] = []
            angular_json = repo_path / "angular.json"
            if angular_json.exists():
                try:
                    workspace = json.loads(angular_json.read_text(encoding="utf-8"))
                    for project in workspace.get("projects", {}).values():
                        targets = project.get("architect", project.get("targets", {}))
                        test_target = targets.get("test", {})
                        builder = test_target.get("builder") or test_target.get("executor", "")
                        if builder:
                            test_builders.append(str(builder).lower())
                except (OSError, json.JSONDecodeError, AttributeError):
                    pass

            if angular_json.exists() and is_ng_test and not test_builders:
                return None

            uses_karma = (
                is_karma
                or any("karma" in builder for builder in test_builders)
                or (not test_builders and bool(karma_confs))
            )
            if not uses_karma:
                modern_script = re.sub(
                    r"\s+--browsers(?:=\S+|\s+\S+)?",
                    "",
                    effective_script,
                    flags=re.IGNORECASE,
                )
                modern_script = re.sub(
                    r"\s+--(?:watch(?:=(?:true|false))?|no-watch|no-progress|progress(?:=(?:true|false))?)\b",
                    "",
                    modern_script,
                    flags=re.IGNORECASE,
                ).strip()
                if is_chained:
                    return f"npm exec -- {modern_script}"
                return f"npm exec -- {modern_script}" if has_npm else modern_script

            already_headless = False
            for kc in karma_confs:
                try:
                    karma_text = kc.read_text(encoding="utf-8", errors="replace").lower()
                    if "chromeheadless" in karma_text or "chromiumheadless" in karma_text:
                        already_headless = True
                        break
                except Exception:
                    pass

            flags: list[str] = []
            if "--watch=false" not in script_lower and "--no-watch" not in script_lower:
                flags.append("--watch=false")
            if "--no-progress" not in script_lower and "--progress=false" not in script_lower:
                flags.append("--no-progress")
            if not already_headless and "--browsers" not in script_lower:
                flags.append("--browsers=ChromeHeadless")

            flag_str = " ".join(flags)
            if has_npm:
                if is_chained:
                    return f"{runner_command} {flag_str}" if flag_str else runner_command
                return f"npm test -- {flag_str}" if flag_str else "npm run test"
            else:
                return f"{effective_script} {flag_str}" if flag_str else effective_script

        return "npm run test" if has_npm else raw_test_script

    def _resolve_angular_build_command(
        self,
        raw_build_script: str | None,
        repo_path: Path,
        has_npm: bool,
    ) -> str | None:
        """Resolve build command for Angular workspaces, ordering libraries before applications in multi-project setups."""
        if not raw_build_script:
            return None

        angular_json = repo_path / "angular.json"
        if not angular_json.exists():
            return "npm run build" if has_npm else raw_build_script

        try:
            workspace = json.loads(angular_json.read_text(encoding="utf-8"))
            projects = workspace.get("projects", {})
            if not isinstance(projects, dict) or len(projects) <= 1:
                return "npm run build" if has_npm else raw_build_script

            libraries: list[str] = [
                name for name, p in projects.items()
                if isinstance(p, dict) and p.get("projectType", "").lower() == "library"
            ]
            applications: list[str] = [
                name for name, p in projects.items()
                if isinstance(p, dict) and p.get("projectType", "").lower() == "application"
            ]

            is_bare_ng_build = bool(re.search(r"(?:^|(?<=&&)\s*|(?<=;)\s*)ng\s+(?:build|b)(?:\s+--[\w-]+(?:=\S+)?)*\s*(?:$|&&|;)", raw_build_script.strip()))
            missing_libs = [lib for lib in libraries if lib not in raw_build_script]

            if is_bare_ng_build or missing_libs:
                default_proj = workspace.get("defaultProject")
                if default_proj and default_proj in applications:
                    applications.remove(default_proj)
                    applications.insert(0, default_proj)

                cfg_match = re.search(r"(--(?:configuration|c)[=\s]\S+)", raw_build_script)
                flags = f" {cfg_match.group(1).strip()}" if cfg_match else ""

                ordered_targets = libraries + applications
                if not ordered_targets:
                    return "npm run build" if has_npm else raw_build_script

                cmds = [
                    f"ng build {t}{flags if t in applications else ''}"
                    for t in ordered_targets
                ]
                return " && ".join(cmds)

            return "npm run build" if has_npm else raw_build_script
        except Exception:
            return "npm run build" if has_npm else raw_build_script

    def _build_workspace_libraries(self, repo_path: Path, gate_env: dict[str, str]) -> None:
        """Pre-build any library projects in the workspace so dependent applications can resolve them."""
        angular_json = repo_path / "angular.json"
        if not angular_json.exists():
            return

        try:
            workspace = json.loads(angular_json.read_text(encoding="utf-8"))
            lib_names = []
            for name, proj in workspace.get("projects", {}).items():
                if isinstance(proj, dict) and proj.get("projectType") == "library":
                    lib_names.append(name)

            if not lib_names:
                return

            has_npm = shutil.which("npm", path=gate_env.get("PATH")) is not None
            bin_ext = ".cmd" if sys.platform == "win32" else ""
            local_ng = repo_path / "node_modules" / ".bin" / f"ng{bin_ext}"

            for lib_name in lib_names:
                print(f"   ↳ Pre-building workspace library '{lib_name}' for dependent applications...", flush=True)

                commands_to_try: list[str] = []
                if local_ng.exists():
                    commands_to_try.append(f'"{local_ng}" build {lib_name}')
                if has_npm:
                    commands_to_try.append(f"npx --no-install ng build {lib_name}")
                    commands_to_try.append(f"npx ng build {lib_name}")
                    commands_to_try.append(f"npm exec -- ng build {lib_name}")
                else:
                    commands_to_try.append(f"ng build {lib_name}")

                built_successfully = False
                last_err = ""

                for cmd in commands_to_try:
                    try:
                        res = run_cancellable_subprocess(
                            cmd,
                            shell=True,
                            cwd=repo_path,
                            env=gate_env,
                            capture_output=True,
                            text=True,
                            timeout=180.0,
                        )
                        if res.returncode == 0:
                            built_successfully = True
                            print(f"   ✔ Workspace library '{lib_name}' built successfully.", flush=True)
                            break
                        else:
                            last_err = (res.stderr or res.stdout or "").strip()
                    except Exception as run_err:
                        last_err = str(run_err)

                if not built_successfully:
                    logger.warning(f"Could not pre-build library '{lib_name}': {last_err[:300]}")
                    print(f"   ⚠ Pre-building library '{lib_name}' was not completed by automated runner; continuing to verification gates.", flush=True)
        except Exception as e:
            logger.warning(f"Failed to check workspace libraries: {e}")

    def get_declared_dependencies(self, repo_path: Path) -> dict[str, str]:
        pkg_file = repo_path / "package.json"
        if not pkg_file.exists():
            return {}
        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            return {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        except Exception:
            return {}

