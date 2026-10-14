"""Unit tests for repository detection and starter project scaffolding."""

from pathlib import Path

from amstralift.core.scaffold import detect_repository_status, scaffold_starter_project
from amstralift.core.workspace import run_git


def test_detect_repository_status_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    status = detect_repository_status(nonexistent)
    assert status["exists"] is False
    assert status["is_dir"] is False
    assert status["is_empty"] is True
    assert status["is_git"] is False
    assert status["root_ecosystem"] is None


def test_detect_repository_status_empty(tmp_path: Path):
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    status = detect_repository_status(empty_dir)
    assert status["exists"] is True
    assert status["is_dir"] is True
    assert status["is_empty"] is True
    assert status["is_git"] is False
    assert status["root_ecosystem"] is None


def test_detect_repository_status_with_subprojects(tmp_path: Path):
    repo_root = tmp_path / "monorepo_root"
    deep_app = repo_root / "src" / "Services" / "WebUI"
    deep_app.mkdir(parents=True)
    (deep_app / "package.json").write_text('{"dependencies": {"@angular/core": "16.0.0"}}', encoding="utf-8")

    status = detect_repository_status(repo_root)
    assert status["exists"] is True
    assert status["is_empty"] is False
    assert status["root_ecosystem"] is None
    assert len(status["subprojects"]) == 1
    assert status["subprojects"][0].ecosystem == "angular"
    assert status["subprojects"][0].rel_path == "src/Services/WebUI"


def test_scaffold_angular_project(tmp_path: Path):
    target = tmp_path / "scaffold_ng"
    created = scaffold_starter_project(target, ecosystem="angular")

    assert "package.json" in created
    assert "angular.json" in created
    assert "tsconfig.json" in created
    assert "src/app/app.component.html" in created
    assert "src/app/app.component.ts" in created

    # Verify Git was initialized and files committed
    git_res = run_git(["log", "-1", "--oneline"], cwd=target)
    assert git_res.returncode == 0
    assert "Initial commit" in git_res.stdout


def test_scaffold_react_project(tmp_path: Path):
    target = tmp_path / "scaffold_react"
    created = scaffold_starter_project(target, ecosystem="react")

    assert "package.json" in created
    assert "tsconfig.json" in created
    assert "src/App.tsx" in created

    status = detect_repository_status(target)
    assert status["is_git"] is True
    assert status["root_ecosystem"] == "react"


def test_scaffold_dotnet_project(tmp_path: Path):
    target = tmp_path / "scaffold_dotnet"
    created = scaffold_starter_project(target, ecosystem="dotnet")

    assert "MyApplication.csproj" in created
    assert "Program.cs" in created

    status = detect_repository_status(target)
    assert status["is_git"] is True
    assert status["root_ecosystem"] == "dotnet"


def test_scaffold_python_project(tmp_path: Path):
    target = tmp_path / "scaffold_python"
    created = scaffold_starter_project(target, ecosystem="python")

    assert "requirements.txt" in created
    assert "main.py" in created

    status = detect_repository_status(target)
    assert status["is_git"] is True
    assert status["root_ecosystem"] == "python"
