# AMstraLift

> **Upgrade safely. Modernize confidently.**

AMstraLift is an automated dependency and framework upgrade engine built around an explicit, cryptographically bound trust boundary. Zero AI in execution. No auto-merge, ever, for any tier.

---

## 📌 Implementation Status

> **Core engine implementation, hardened remote publisher, and CI templates complete; fleet orchestration remains.**  
> The implemented scope has **85 passing tests (85% coverage)**.

### Test Taxonomy (85 Passing Tests)
- **Unit Tests (30 tests)**: Cryptographic signing (`HMAC-SHA256`), canonical bundle serialization, TTL expiry, governance policies (Angular LTS staleness checks, Python runtime default resolution with no-match fallback, time-boxed CVE exceptions, EOL escalation SLAs, Section 16 pilot metrics calculation), GitHub provider client unit tests (idempotency key hashing, deterministic branch names with short hash collision avoidance, 422 recovery, timeout PR recovery, label failure handling, in-memory token sanitization).
- **CI Template & Security Tests (6 tests)**: YAML schema verification, concurrency protection (`cancel-in-progress: false`), least-privilege permissions (`contents: write`, `pull-requests: write`, `issues: write`), timeout validation, and `amstralift init-ci` generator execution.
- **Adapter Integration Tests (16 tests)**: Real manifest and lockfile generation, version discovery, and build/test gates across Angular, Python (PyPI), .NET (NuGet), and React (npm).
- **Trust-Boundary & Security Tests (17 tests)**: Path traversal rejection (`..`), symlink blocking (`mode 120000`), pipeline & governance file protection (`.github/workflows/`, `CODEOWNERS`, `SECURITY.md`), git credential scrubbing (`.git/config`, hooks), and re-diff verification.
- **Remote Publishing & Stage B Hardening Tests (16 tests)**: End-to-end publishing against real bare Git remotes, clean worktree verification, selective patch path staging (no `git add .`), patch apply aborts, duplicate PR detection & idempotency (zero duplicate PRs), push retry recovery (reusing existing pushed branch on API timeout), diverged remote branch conflict rejection (no force push), remote target branch drift detection, stale-base commit rejection, and tampered bundle rejection.

---

## 🛡️ Production-Readiness Checklist

- [x] **Least-Privilege Credentials**: Tokens are supplied in-memory via `http.extraheader` / CLI options, never saved to `.git/config`, bundles, or logs.
- [x] **Publishing Kill-Switches**: Publishing is disabled by default (`--dry-run` and explicit `--publish` required).
- [x] **Idempotency & Duplicate Prevention**: Deterministic branch naming and matching-branch lookup prevent duplicate PRs across repeated runs.
- [x] **Failure Visibility & Recovery**: Push retry recovery skips redundant pushes if the remote branch matches local HEAD; failures fail closed with clear status.
- [x] **Log & Bundle Scrubbing**: Tokens and basic auth credentials are fully scrubbed (`[REDACTED_TOKEN]`) from exceptions, bundles, and CLI outputs.
- [x] **Scheduled CI Concurrency**: Workflows enforce `cancel-in-progress: false` to protect running Stage B publisher operations.
- [ ] **Rate Limiting & Concurrency**: Strict limits on concurrency, retries, and Git provider API rate usage across fleet batches.
- [ ] **Documented Rollback Procedure**: A documented rollback, close-out, or disable procedure is established.
- [ ] **Gated Pilot Approval**: A pilot runs against a small, explicitly approved repository set before fleet-wide rollout.

---

## 🗺️ Workstream Roadmap & Priorities

| Priority | Work Item | Objective | Status |
| :---: | :--- | :--- | :---: |
| **P1** | **Remote Git Provider Publishing** | Push verified branch to `origin` and open a reviewable PR via GitHub API. | **Completed** |
| **P2** | **Duplicate PR Detection & Idempotency** | Prevent repeated runs from creating duplicate branches or PRs using matching-branch lookups. | **Completed** |
| **P3** | **CI Workflow Templates** | Provide turnkey `.github/workflows/` and `.gitlab-ci.yml` definitions for scheduled cron execution. | **Completed** |
| **P4** | **Fleet Manifest & Runner** | Central `fleet.yaml` inventory with matrix/batch invocation across multiple repositories. | **Next Priority** |
| **P5** | **Demonstration Suite** | Ready-to-run demo apps and walkthrough script for rapid validation. | Pending |
| **P6** | **Platform Layer (DB, API, UI)** | Centralized database, FastAPI control plane, and Next.js dashboard for enterprise scale. | Post-MVP |

---

## 🎯 Completed Milestone: Publish One Verified Upgrade PR to a Test Repository

**Verification Results:**
1. ✅ **Run against disposable repo**: Executed in test harness against real bare Git remote.
2. ✅ **Stage A isolated workspace**: Produces untrusted advisory bundle with gate results.
3. ✅ **Stage B validation**: Validates cryptographic signature, TTL, path allowlists, and required test gates.
4. ✅ **Branch push & PR creation**: Pushes deterministic branch (`amstralift/{pkg}-{ver}`) and opens PR with labels and 24h SLA deadline.
5. ✅ **Idempotency**: Repeated run detects existing open PR; returns `ALREADY_EXISTS` with zero duplicate PRs or pushes.
6. ✅ **Stale & tampered bundle rejection**: Rejected immediately with explicit errors before touching Git remotes.
7. ✅ **Failure recovery**: Pre-existing matching remote branch is safely reused without failing or corrupting state.
7. The result, audit trail, and any failure reasons are recorded.

---

## 🚀 Quick Start & Testing Guide

You can run and test AMstraLift using either standard **`pip`** or **`uv`**.

### Method 1: Using Standard `pip` (Python 3.11+)

1. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   # (On Linux/macOS: source .venv/bin/activate)
   ```

2. **Install dependencies:**
   ```powershell
   # Option A: Install from requirements-dev.txt (includes tests & linting)
   pip install -r requirements-dev.txt
   pip install -e .

   # Option B: Or install directly in editable mode with dev dependencies
   pip install -e ".[dev]"
   ```

3. **Run the test suite:**
   ```powershell
   pytest
   ```

4. **Run the CLI:**
   ```powershell
   amstralift --help
   amstralift run --repo "path/to/target-repo" --dry-run
   ```

---

### Method 2: Using `uv` (Recommended — zero manual env setup)

If you have `uv` installed, it manages the virtual environment automatically:

1. **Run the test suite:**
   ```powershell
   uv run pytest
   ```

2. **Run lint checks:**
   ```powershell
   uv run ruff check .
   ```

3. **Run the CLI:**
   ```powershell
   uv run amstralift --help
   uv run amstralift run --repo "path/to/target-repo" --dry-run
   ```

4. **Generate CI Workflows:**
   ```powershell
   # Generate turnkey GitHub Actions workflows in a target repository
   uv run amstralift init-ci --provider github --repo .
   ```
