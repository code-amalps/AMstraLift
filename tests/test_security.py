"""Tests for security validation, path allowlists, and migration classifications."""

import pytest

from amstralift.core.security import (
    SecurityViolationError,
    classify_migration_diff,
    extract_paths_from_patch,
    validate_patch_security,
)


def test_extract_paths_from_patch():
    patch = (
        "diff --git a/package.json b/package.json\n"
        "--- a/package.json\n"
        "+++ b/package.json\n"
        "@@ -1,3 +1,3 @@\n"
        "diff --git a/src/app.ts b/src/app.ts\n"
        "--- a/src/app.ts\n"
        "+++ b/src/app.ts\n"
    )
    paths = extract_paths_from_patch(patch)
    assert paths == ["package.json", "src/app.ts"]


def test_validate_safe_manifest_patch():
    patch = "diff --git a/package.json b/package.json\n--- a/package.json\n+++ b/package.json\n@@ -10,3 +10,3 @@\n"
    paths = validate_patch_security(patch)
    assert paths == ["package.json"]


@pytest.mark.parametrize(
    "forbidden_path",
    [
        "../escape.sh",
        "foo/../../bar.js",
        "/etc/passwd",
        ".github/workflows/deploy.yml",
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        "Jenkinsfile",
        "CODEOWNERS",
        "SECURITY.md",
        ".env",
        "src/payload.exe",
        "scripts/malicious.sh",
    ],
)
def test_reject_forbidden_paths(forbidden_path):
    patch = (
        f"diff --git a/{forbidden_path} b/{forbidden_path}\n"
        f"--- a/{forbidden_path}\n"
        f"+++ b/{forbidden_path}\n"
        "@@ -1 +1 @@\n"
    )
    with pytest.raises(SecurityViolationError):
        validate_patch_security(patch)


def test_reject_symlink_creation():
    patch = (
        "diff --git a/symlink_file b/symlink_file\n"
        "new file mode 120000\n"
        "index 0000000..abcdef1\n"
        "--- /dev/null\n"
        "+++ b/symlink_file\n"
        "@@ -0,0 +1 @@\n"
        "+/etc/shadow\n"
    )
    with pytest.raises(SecurityViolationError, match="symlinks"):
        validate_patch_security(patch)


def test_classify_manifest_only_migration():
    classification = classify_migration_diff(["package.json", "package-lock.json"])
    assert classification.risk_level == "LOW"
    assert not classification.application_source_modified
    assert classification.manifest_or_lockfile_only


def test_classify_source_modified_migration():
    classification = classify_migration_diff(["package.json", "src/app/main.ts"])
    assert classification.risk_level == "HIGH"
    assert classification.application_source_modified
    assert not classification.manifest_or_lockfile_only
    assert "Mandatory human review" in classification.rationale


def test_generate_patch_ignores_node_modules_and_build_dirs_without_gitignore(tmp_path: Path):
    """Verify generate_patch unconditionally excludes node_modules, .angular, and build dirs even without .gitignore."""
    from amstralift.core.workspace import generate_patch, get_head_commit, run_git

    repo = tmp_path / "repo-no-gitignore"
    repo.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=repo)
    run_git(["config", "user.name", "Test"], cwd=repo)
    run_git(["config", "user.email", "test@test.com"], cwd=repo)
    run_git(["config", "core.autocrlf", "false"], cwd=repo)

    (repo / "package.json").write_text('{"name": "test", "version": "1.0.0"}\n', encoding="utf-8")
    run_git(["add", "."], cwd=repo)
    run_git(["commit", "-m", "Initial commit"], cwd=repo)
    base_commit = get_head_commit(repo)

    # Modify package.json (legitimate change)
    (repo / "package.json").write_text('{"name": "test", "version": "2.0.0"}\n', encoding="utf-8")

    # Create unignored dependency and cache artifacts that must NEVER be diffed
    nm_file = repo / "node_modules" / "some-pkg" / "weird.doc"
    nm_file.parent.mkdir(parents=True, exist_ok=True)
    nm_file.write_text("binary-or-unsupported-content", encoding="utf-8")

    angular_cache = repo / ".angular" / "cache" / "cache.bin"
    angular_cache.parent.mkdir(parents=True, exist_ok=True)
    angular_cache.write_text("angular-cache", encoding="utf-8")

    patch = generate_patch(repo, base_commit)
    assert "package.json" in patch
    assert "node_modules" not in patch
    assert ".angular" not in patch

