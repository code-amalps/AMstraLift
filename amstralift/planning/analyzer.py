"""Repository Analyzer for the Modernization Planning Engine (P17).

Inspects codebase manifests, configurations, source code, and templates
to discover framework versions, existing patterns, and modernization applicability.

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
import re
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

from amstralift.adapters.angular import extract_major_version


IGNORED_DIRS = {
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    "bin",
    "obj",
    ".vs",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "out",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".pytest_cache",
}


class RepoAnalysis(BaseModel):
    """Structured report produced by analyzing a target repository."""

    repo_path: str
    ecosystem: str
    current_version: str | None = None
    current_major: int | None = None
    manifest_files: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)


def safe_find_files(repo_path: Path, patterns: list[str], max_count: int = 100) -> list[Path]:
    """Find files matching patterns while skipping vendor, build, and virtualenv directories."""
    matched: list[Path] = []
    try:
        for p in repo_path.rglob("*"):
            if any(part in IGNORED_DIRS for part in p.parts):
                continue
            if p.is_file():
                for pat in patterns:
                    if p.match(pat):
                        matched.append(p)
                        if len(matched) >= max_count:
                            return matched
                        break
    except Exception:
        pass
    return matched


class RepoAnalyzer:
    """Discovers project structure, framework versions, and migration candidate patterns."""

    @classmethod
    def analyze(cls, repo_path: Path, ecosystem_hint: str | None = None) -> RepoAnalysis:
        """Run deep deterministic inspection on the repository."""
        repo_path = Path(repo_path).resolve()
        ecosystem = ecosystem_hint or cls.detect_ecosystem(repo_path)
        cur_version, cur_major = cls.detect_version(repo_path, ecosystem)
        features = cls.detect_features(repo_path, ecosystem)
        manifests = cls.find_manifests(repo_path, ecosystem)

        return RepoAnalysis(
            repo_path=str(repo_path),
            ecosystem=ecosystem,
            current_version=cur_version,
            current_major=cur_major,
            manifest_files=manifests,
            features=features,
        )

    @classmethod
    def detect_ecosystem(cls, repo_path: Path) -> str:
        """Identify the primary ecosystem of the repository."""
        pkg_file = repo_path / "package.json"
        if pkg_file.exists():
            try:
                data = json.loads(pkg_file.read_text(encoding="utf-8"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "@angular/core" in deps or "@angular/cli" in deps:
                    return "angular"
                if "react" in deps:
                    return "react"
            except Exception:
                pass

        if (
            list(repo_path.glob("*.sln"))
            or list(repo_path.glob("*.csproj"))
            or (repo_path / "Directory.Build.props").exists()
        ):
            return "dotnet"

        if (
            (repo_path / "pyproject.toml").exists()
            or (repo_path / "setup.py").exists()
            or (repo_path / "setup.cfg").exists()
            or (repo_path / "requirements.txt").exists()
        ):
            return "python"

        # Default fallback
        return "angular"

    @classmethod
    def detect_version(cls, repo_path: Path, ecosystem: str) -> tuple[str | None, int | None]:
        """Detect current declared framework version and major integer."""
        eco = ecosystem.lower()
        if eco == "angular":
            pkg_file = repo_path / "package.json"
            if pkg_file.exists():
                try:
                    data = json.loads(pkg_file.read_text(encoding="utf-8"))
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    ver = deps.get("@angular/core") or deps.get("@angular/cli")
                    if ver:
                        clean = ver.strip().lstrip("^~>=<")
                        major = extract_major_version(ver)
                        return clean, major
                except Exception:
                    pass
            return None, None

        if eco == "dotnet":
            csproj_files = safe_find_files(repo_path, ["*.csproj"], max_count=5)
            props_file = repo_path / "Directory.Build.props"
            candidates: list[Path] = [props_file] if props_file.exists() else []
            candidates.extend(csproj_files)

            for f in candidates:
                try:
                    content = f.read_text(encoding="utf-8")
                    m = re.search(r"<TargetFramework(?:s)?>([^<]+)</TargetFramework(?:s)?>", content)
                    if m:
                        raw_tfm = m.group(1).split(";")[0].strip()
                        num_m = re.search(r"net(\d+)", raw_tfm)
                        major = int(num_m.group(1)) if num_m else None
                        return raw_tfm, major
                except Exception:
                    pass
            return None, None

        if eco == "python":
            pyproject = repo_path / "pyproject.toml"
            if pyproject.exists():
                try:
                    content = pyproject.read_text(encoding="utf-8")
                    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content)
                    if m:
                        req = m.group(1).strip()
                        num_m = re.search(r"3\.(\d+)", req)
                        major = int(num_m.group(1)) if num_m else None
                        return req, major
                except Exception:
                    pass
            return None, None

        if eco == "react":
            pkg_file = repo_path / "package.json"
            if pkg_file.exists():
                try:
                    data = json.loads(pkg_file.read_text(encoding="utf-8"))
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    ver = deps.get("react")
                    if ver:
                        clean = ver.strip().lstrip("^~>=<")
                        major = extract_major_version(ver)
                        return clean, major
                except Exception:
                    pass
            return None, None

        return None, None

    @classmethod
    def detect_features(cls, repo_path: Path, ecosystem: str) -> dict[str, Any]:
        """Detect AST patterns, legacy constructs, and tooling configurations."""
        features: dict[str, Any] = {}
        eco = ecosystem.lower()

        if eco == "angular":
            # 1. Workspace JSON
            features["has_angular_json"] = (repo_path / "angular.json").exists()

            # 2. Structural directives (*ngIf, *ngFor, *ngSwitch)
            html_files = safe_find_files(repo_path, ["*.html"], max_count=50)
            has_structural = False
            has_empty_tags = False
            for hf in html_files:
                try:
                    txt = hf.read_text(encoding="utf-8")
                    if any(dir_name in txt for dir_name in ("*ngIf", "*ngFor", "*ngSwitch")):
                        has_structural = True
                    if re.search(r"<([a-zA-Z0-9]+-[a-zA-Z0-9\-_]+)[^>]*>\s*</\1>", txt):
                        has_empty_tags = True
                    if has_structural and has_empty_tags:
                        break
                except Exception:
                    pass
            features["has_structural_directives"] = has_structural
            features["has_empty_tags"] = has_empty_tags

            # 3. TypeScript AST features (@NgModule, constructor DI, @Input/@Output)
            ts_files = safe_find_files(repo_path, ["*.ts"], max_count=50)
            has_ngmodules = False
            has_constructor_di = False
            has_input_output = False
            for tf in ts_files:
                try:
                    txt = tf.read_text(encoding="utf-8")
                    if "@NgModule(" in txt:
                        has_ngmodules = True
                    if re.search(r"constructor\s*\([^)]*(?:private|public|protected)\s+", txt):
                        has_constructor_di = True
                    if "@Input(" in txt or "@Output(" in txt or "@ViewChild(" in txt:
                        has_input_output = True
                    if has_ngmodules and has_constructor_di and has_input_output:
                        break
                except Exception:
                    pass
            features["has_ngmodules"] = has_ngmodules
            features["has_constructor_di"] = has_constructor_di
            features["has_input_output"] = has_input_output

            # 4. ESLint config
            features["has_legacy_eslint"] = (
                (repo_path / ".eslintrc.json").exists()
                or (repo_path / ".eslintrc.js").exists()
                or (repo_path / ".eslintrc.yml").exists()
            )

        elif eco == "dotnet":
            features["has_cpm"] = (repo_path / "Directory.Packages.props").exists()
            packages_configs = safe_find_files(repo_path, ["packages.config"], max_count=5)
            features["has_legacy_packages_config"] = len(packages_configs) > 0

            csprojs = safe_find_files(repo_path, ["*.csproj"], max_count=10)
            has_nullable = False
            for cp in csprojs:
                try:
                    txt = cp.read_text(encoding="utf-8")
                    if "<Nullable>enable</Nullable>" in txt:
                        has_nullable = True
                        break
                except Exception:
                    pass
            features["has_nullable"] = has_nullable

        elif eco == "python":
            features["has_setup_py"] = (repo_path / "setup.py").exists()
            features["has_setup_cfg"] = (repo_path / "setup.cfg").exists()
            features["has_pyproject"] = (repo_path / "pyproject.toml").exists()
            features["has_ruff"] = (repo_path / "ruff.toml").exists()
            features["has_legacy_flake8"] = (repo_path / ".flake8").exists()

        elif eco == "react":
            tsx_files = safe_find_files(repo_path, ["*.tsx", "*.jsx"], max_count=20)
            has_classic_import = False
            for f in tsx_files:
                try:
                    txt = f.read_text(encoding="utf-8")
                    if "import React from 'react'" in txt or 'import React from "react"' in txt:
                        has_classic_import = True
                        break
                except Exception:
                    pass
            features["has_classic_react_import"] = has_classic_import

        return features

    @classmethod
    def find_manifests(cls, repo_path: Path, ecosystem: str) -> list[str]:
        """Return relative paths of manifest files present in repository."""
        manifests: list[str] = []
        eco = ecosystem.lower()
        candidates: list[str] = []
        if eco in ("angular", "react"):
            candidates = ["package.json", "package-lock.json", "angular.json", "tsconfig.json"]
        elif eco == "dotnet":
            candidates = ["Directory.Build.props", "Directory.Packages.props", "NuGet.config"]
        elif eco == "python":
            candidates = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"]

        for cand in candidates:
            if (repo_path / cand).exists():
                manifests.append(cand)

        return manifests
