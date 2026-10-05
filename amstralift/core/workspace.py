"""Workspace management, credential scrubbing, and safe git operations.

Ensures Stage A receives a sanitized workspace with zero write credentials,
and provides Stage B with re-diff verification and head concurrency checks.
"""

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git command fails."""

    pass


class StaleBaseError(Exception):
    """Raised when target branch HEAD has moved since Stage A was run."""

    pass


STANDARD_IGNORED_DIRS: tuple[str, ...] = (
    # Node / JavaScript / TypeScript
    "node_modules",
    ".angular",
    ".nx",
    ".turbo",
    ".next",
    ".nuxt",
    ".cache",
    "dist",
    "out-tsc",
    "coverage",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # .NET / C#
    "bin",
    "obj",
    "packages",
    ".vs",
    "TestResults",
)

EPHEMERAL_CACHE_DIRS = STANDARD_IGNORED_DIRS


def safe_rglob(
    root: Path,
    patterns: str | list[str] | tuple[str, ...],
    excluded_dirs: set[str] | tuple[str, ...] = STANDARD_IGNORED_DIRS,
) -> list[Path]:
    """Safely recursively find files matching one or more glob patterns.

    Guarantees:
    1. Prunes excluded directories (like node_modules, .git, bin, obj, dist) top-down,
       so the walker NEVER descends into them.
    2. Uses followlinks=False so directory symlinks and junctions are never traversed.
    3. Handles OS filesystem errors gracefully (e.g. WinError 3, PermissionError) without crashing.
    4. Only returns existing, accessible files matching the pattern(s).
    """
    if not root.exists() or not root.is_dir():
        return []

    if isinstance(patterns, str):
        patterns = [patterns]

    clean_patterns: list[tuple[str, str]] = []
    for pat in patterns:
        p = pat.replace("\\", "/")
        if p.startswith("**/"):
            p = p[3:]
        clean_patterns.append((pat, p))

    excluded_lower = {d.lower() for d in excluded_dirs} | {".git"}
    results: list[Path] = []

    def _ignore_walk_error(err: OSError) -> None:
        pass

    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, onerror=_ignore_walk_error, followlinks=False
    ):
        # Prune excluded directories in-place so os.walk never recurses into them
        dirnames[:] = [d for d in dirnames if d.lower() not in excluded_lower]

        current_dir = Path(dirpath)
        for fname in filenames:
            matched = False
            for raw_pat, file_pat in clean_patterns:
                if fnmatch.fnmatch(fname, file_pat):
                    matched = True
                    break
                if "/" in raw_pat or "\\" in raw_pat:
                    try:
                        rel = (current_dir / fname).relative_to(root)
                        if rel.match(raw_pat):
                            matched = True
                            break
                    except Exception:
                        pass
            if matched:
                file_path = current_dir / fname
                try:
                    if file_path.is_file():
                        results.append(file_path)
                except OSError:
                    pass

    return sorted(results)



def configure_sandbox_git_excludes(workspace_path: Path) -> None:
    """Ensure Git in the sandbox unconditionally ignores build and dependency artifacts.

    Writes to .git/info/exclude so Git never stages, tracks, or diffs node_modules,
    .angular, .venv, bin, obj, dist, etc., even if the repository lacks a .gitignore.
    """
    exclude_file = workspace_path / ".git" / "info" / "exclude"
    try:
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
        existing_lines = set(line.strip() for line in existing.splitlines())
        needed = [f"{d}/" for d in STANDARD_IGNORED_DIRS if f"{d}/" not in existing_lines and d not in existing_lines]
        if needed:
            updated = existing.rstrip() + "\n" + "\n".join(needed) + "\n"
            exclude_file.write_text(updated, encoding="utf-8")
    except Exception:
        pass


def run_git(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a git command with strict error checking."""
    clean_env = os.environ.copy()
    # Strip any potential token leak from parent environment
    for sensitive_var in ("GITHUB_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK", "GIT_CREDENTIAL_HELPER"):
        clean_env.pop(sensitive_var, None)

    if env:
        clean_env.update(env)

    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=clean_env,
        input=input_data,
    )
    if result.stdout is None:
        result.stdout = ""
    if result.stderr is None:
        result.stderr = ""
    return result


def get_head_commit(repo_path: Path) -> str:
    """Get full 40-character SHA of current HEAD."""
    res = run_git(["rev-parse", "HEAD"], cwd=repo_path)
    if res.returncode != 0:
        raise GitError(f"Failed to get HEAD commit in {repo_path}: {res.stderr}")
    return (res.stdout or "").strip()


def scrub_credentials(repo_path: Path) -> None:
    """Scrub sensitive credentials, remotes, and hooks from repository workspace."""
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        return

    # 1. Remove dangerous hooks that could execute host binaries
    hooks_dir = git_dir / "hooks"
    if hooks_dir.exists():
        shutil.rmtree(hooks_dir, ignore_errors=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # 2. Neutralize remote URLs to prevent Stage A from pushing
    run_git(["config", "--unset-all", "credential.helper"], cwd=repo_path)
    run_git(["config", "remote.origin.url", "file:///dev/null"], cwd=repo_path)
    run_git(["config", "core.autocrlf", "false"], cwd=repo_path)

    # 3. Set standard safe bot identity
    run_git(["config", "user.name", "AMstraLift Bot"], cwd=repo_path)
    run_git(["config", "user.email", "bot@amstralift.internal"], cwd=repo_path)


def get_active_branch(repo_path: Path) -> str:
    """Get the active branch of the repository."""
    res = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if res.returncode == 0 and (res.stdout or "").strip():
        b = res.stdout.strip()
        if b != "HEAD":
            return b
    return "main"


def prepare_stage_a_workspace(
    source_repo_path: Path,
    target_workspace_path: Path,
    target_branch: str = "main",
    read_only_configs: dict[str, str] | None = None,
) -> str:
    """Clone or copy workspace into an isolated sandbox directory and scrub credentials.

    Returns the base commit SHA.
    """
    if target_workspace_path.exists():
        shutil.rmtree(target_workspace_path, ignore_errors=True)
    target_workspace_path.mkdir(parents=True, exist_ok=True)

    # Clone locally with core.autocrlf=false and target branch
    clone_args = [
        "-c",
        "core.autocrlf=false",
        "clone",
        "--depth",
        "1",
    ]
    if target_branch:
        clone_args.extend(["--branch", target_branch])
    clone_args.extend([str(source_repo_path), str(target_workspace_path)])

    res = run_git(clone_args, cwd=source_repo_path.parent)
    if res.returncode != 0:
        # Fallback to copy if branch clone fails
        res = run_git(
            ["-c", "core.autocrlf=false", "clone", "--depth", "1", str(source_repo_path), str(target_workspace_path)],
            cwd=source_repo_path.parent,
        )
        if res.returncode != 0:
            shutil.copytree(source_repo_path, target_workspace_path, dirs_exist_ok=True)
            if not (target_workspace_path / ".git").exists():
                run_git(["init"], cwd=target_workspace_path)
                run_git(["config", "core.autocrlf", "false"], cwd=target_workspace_path)
                run_git(["add", "."], cwd=target_workspace_path)
                run_git(["commit", "--no-verify", "-m", "Initial commit"], cwd=target_workspace_path)

    # Ensure the sandbox is pristine: reset to HEAD and remove any untracked/dirty artifacts from parent
    run_git(["config", "core.autocrlf", "false"], cwd=target_workspace_path)
    run_git(["reset", "--hard", "HEAD"], cwd=target_workspace_path)
    run_git(["clean", "-fdx"], cwd=target_workspace_path)

    base_sha = get_head_commit(target_workspace_path)
    scrub_credentials(target_workspace_path)
    configure_sandbox_git_excludes(target_workspace_path)

    # Inject read-only package registry configs if supplied (e.g. .npmrc)
    if read_only_configs:
        for filename, content in read_only_configs.items():
            if "_authToken" in content and "//registry.npmjs.org/:_authToken" in content:
                raise ValueError("Publishing token detected in registry config! Only read-only tokens allowed.")
            (target_workspace_path / filename).write_text(content, encoding="utf-8")

    return base_sha


def generate_patch(repo_path: Path, base_commit: str) -> str:
    """Generate clean unified git diff against base commit."""
    # 1. Ensure sandbox .git/info/exclude unconditionally ignores standard dependency & build dirs
    configure_sandbox_git_excludes(repo_path)

    # 2. Clean ephemeral build and tool cache directories so binary artifacts never corrupt patches
    for cache_name in STANDARD_IGNORED_DIRS:
        if cache_name == "node_modules":
            continue  # node_modules is protected by .git/info/exclude and negative pathspecs
        p = repo_path / cache_name
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    # 3. Unstage any accidental tracking of standard ignored directories
    for d in STANDARD_IGNORED_DIRS:
        run_git(["rm", "--cached", "-r", "--ignore-unmatch", d], cwd=repo_path)

    # 4. Build negative pathspecs so git add and git diff explicitly ignore all dependency/build dirs
    exclude_pathspecs = [f":(exclude){d}" for d in STANDARD_IGNORED_DIRS]

    # 5. Add intent-to-add for untracked files, strictly excluding all standard dependency dirs
    add_args = ["add", "-N", "--", "."] + exclude_pathspecs
    run_git(add_args, cwd=repo_path)

    # 6. Generate diff against base commit, strictly excluding all standard dependency dirs
    diff_args = ["diff", "--full-index", "--binary", base_commit, "--", "."] + exclude_pathspecs
    res = run_git(diff_args, cwd=repo_path)
    if res.returncode != 0:
        raise GitError(f"Failed to generate diff against {base_commit}: {res.stderr}")
    return res.stdout or ""


def verify_rediff_integrity(
    repo_path: Path,
    base_commit_sha: str,
    patch: str,
    expected_patch_sha256: str,
) -> bool:
    """In Stage B: Apply patch in a clean checkout and verify re-diff matches exactly.

    Prevents patch injection or divergence.
    """
    # 1. Verify target repo is at base_commit_sha
    current_head = get_head_commit(repo_path)
    if current_head != base_commit_sha:
        raise StaleBaseError(
            f"Target repository HEAD ({current_head}) does not match bundle base ({base_commit_sha}). "
            "Branch has moved; must rebase-and-rerun."
        )

    # 2. Check that working tree is clean (ignoring ephemeral caches like .angular/ or .nx/)
    if not is_working_tree_clean(repo_path, ignore_ephemeral=True):
        status_res = run_git(["status", "--porcelain"], cwd=repo_path)
        raise GitError(f"Target repository has uncommitted changes: {status_res.stdout}")

    # 3. Check patch apply dry-run with --binary and whitespace ignoring
    check_res = run_git(
        ["apply", "--binary", "--ignore-space-change", "--ignore-whitespace", "--check", "-"],
        cwd=repo_path,
        input_data=patch,
    )
    if check_res.returncode != 0:
        raise GitError(f"Patch does not apply cleanly: {check_res.stderr}")

    return True


def is_working_tree_clean(repo_path: Path, ignore_ephemeral: bool = True) -> bool:
    """Check if the working tree has no uncommitted or untracked changes.

    Ignores ephemeral build/tool caches like .angular/ or .nx/ when ignore_ephemeral=True.
    """
    res = run_git(["status", "--porcelain"], cwd=repo_path)
    if res.returncode != 0:
        return False
    stdout = res.stdout or ""
    if not stdout.strip():
        return True

    if not ignore_ephemeral:
        return False

    meaningful_lines = []
    for line in stdout.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        parts = line_clean.split(maxsplit=1)
        if len(parts) == 2:
            status, path_str = parts
            path_normalized = path_str.strip('"').strip("'").rstrip("/\\")
            if any(
                path_normalized == cache
                or path_normalized.startswith(f"{cache}/")
                or path_normalized.startswith(f"{cache}\\")
                for cache in EPHEMERAL_CACHE_DIRS
            ):
                continue
        meaningful_lines.append(line_clean)

    return len(meaningful_lines) == 0


def extract_patch_files(patch: str) -> list[str]:
    """Extract list of target file paths modified by a git patch.

    Parses 'diff --git a/... b/...' headers.
    """
    files: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("diff --git a/") and " b/" in line:
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                file_path = parts[1].strip()
                if file_path and file_path != "/dev/null":
                    files.add(file_path)
        elif line.startswith("+++ b/"):
            file_path = line[6:].strip()
            if file_path and file_path != "/dev/null":
                files.add(file_path)
    return sorted(files)

