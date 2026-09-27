"""Security validation, path allowlists, and migration diff safety checks.

Protects Stage B against malicious, out-of-scope, or dangerous patch payloads.
"""

import re
from pathlib import PurePosixPath

from amstralift.core.models import MigrationClassification


class SecurityViolationError(Exception):
    """Raised when a patch attempts illegal file access or touches forbidden paths."""

    pass


FORBIDDEN_PATH_PREFIXES = (
    ".github/",
    ".gitlab/",
    ".circleci/",
    ".azure/",
    ".git/",
    ".vscode/",
    ".idea/",
)

FORBIDDEN_FILENAMES = {
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "jenkinsfile",
    "codeowners",
    "security.md",
    ".gitattributes",
    ".gitmodules",
    ".env",
}

FORBIDDEN_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".sh",
    ".bat",
    ".cmd",
    ".ps1",
    ".pem",
    ".key",
    ".pfx",
}


def extract_paths_from_patch(patch: str) -> list[str]:
    """Extract all modified, added, or deleted target file paths from unified git diff."""
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            paths.add(line[6:].strip())
        elif line.startswith("--- a/"):
            paths.add(line[6:].strip())
        elif line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    paths.add(b_path[2:].strip())
    return sorted([p for p in paths if p and p != "/dev/null"])


def validate_patch_security(patch: str) -> list[str]:
    """Validate that patch does not touch forbidden paths, CI configs, or use traversals.

    Returns the list of validated relative file paths.
    Raises SecurityViolationError if any violation is detected.
    """
    # 1. Reject symlinks in git diff (mode 120000)
    if re.search(r"new file mode 120000", patch):
        raise SecurityViolationError("Patch creates symlinks, which is strictly prohibited.")

    touched_paths = extract_paths_from_patch(patch)
    if not touched_paths:
        raise SecurityViolationError("Patch contains no file modifications.")

    for path_str in touched_paths:
        # Normalize to POSIX style
        posix = PurePosixPath(path_str.replace("\\", "/"))

        # Traversal check
        if ".." in posix.parts or posix.is_absolute():
            raise SecurityViolationError(f"Path traversal or absolute path detected: {path_str}")

        path_lower = str(posix).lower()
        file_name = posix.name.lower()

        # CI / Workflow / Repository governance protections
        if any(path_lower.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
            raise SecurityViolationError(f"Patch attempts to modify protected directory: {path_str}")

        if file_name in FORBIDDEN_FILENAMES:
            raise SecurityViolationError(f"Patch attempts to modify protected security/CI file: {path_str}")

        if posix.suffix.lower() in FORBIDDEN_EXTENSIONS:
            raise SecurityViolationError(f"Patch attempts to introduce binary/executable/key file: {path_str}")

    return touched_paths


def classify_migration_diff(
    touched_paths: list[str],
    ecosystem: str = "angular",
) -> MigrationClassification:
    """Analyze touched files to classify migration safety (Section 7).

    Required migrations only; any application source modified mandates manual review.
    """
    manifest_extensions = {".json", ".lock", ".yaml", ".yml", ".toml"}
    manifest_names = {
        "package.json",
        "package-lock.json",
        "angular.json",
        "tsconfig.json",
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        "pipfile",
        "pipfile.lock",
        "nuget.config",
        "packages.lock.json",
    }

    app_source_extensions = {
        ".ts",
        ".js",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
        ".scss",
        ".sass",
        ".cs",
        ".py",
        ".rs",
        ".go",
    }

    app_source_touched: list[str] = []
    manifest_only = True

    for p in touched_paths:
        posix = PurePosixPath(p)
        name = posix.name.lower()
        suffix = posix.suffix.lower()

        if name not in manifest_names and suffix not in manifest_extensions:
            manifest_only = False

        if suffix in app_source_extensions:
            app_source_touched.append(p)

    if app_source_touched:
        return MigrationClassification(
            touched_files=touched_paths,
            application_source_modified=True,
            manifest_or_lockfile_only=False,
            risk_level="HIGH",
            rationale=(
                f"Automated migration touched {len(app_source_touched)} application source file(s) "
                f"({', '.join(app_source_touched[:3])}). Mandatory human review required unconditionally (Section 7)."
            ),
        )

    return MigrationClassification(
        touched_files=touched_paths,
        application_source_modified=False,
        manifest_or_lockfile_only=manifest_only,
        risk_level="LOW",
        rationale="Patch modifies only dependency manifests, configurations, or lockfiles.",
    )
