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
