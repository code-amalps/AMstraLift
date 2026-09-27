# AMstraLift

> **Upgrade safely. Modernize confidently.**

AMstraLift is an automated dependency and framework upgrade engine built around an explicit, cryptographically bound trust boundary. Zero AI in execution. No auto-merge, ever, for any tier.

---

## 📌 Implementation Status

> **Core engine implementation, remote publisher, and CI templates complete; fleet orchestration remains.**  
> The implemented scope has **76 passing tests (85% coverage)**.

### Test Taxonomy (76 Passing Tests)
- **Unit Tests (26 tests)**: Cryptographic signing (`HMAC-SHA256`), canonical bundle serialization, TTL expiry, governance policies (Angular LTS staleness checks, Python runtime default resolution with no-match fallback, time-boxed CVE exceptions, EOL escalation SLAs, Section 16 pilot metrics calculation), GitHub provider client unit tests (idempotency key hashing, deterministic branch names, 422 recovery, token sanitization).
- **CI Template & Security Tests (6 tests)**: YAML schema verification, concurrency protection (`cancel-in-progress: false`), least-privilege permissions (`contents: write`, `pull-requests: write`, `issues: write`), timeout validation, and `amstralift init-ci` generator execution.
- **Adapter Integration Tests (16 tests)**: Real manifest and lockfile generation, version discovery, and build/test gates across Angular, Python (PyPI), .NET (NuGet), and React (npm).
- **Trust-Boundary & Security Tests (17 tests)**: Path traversal rejection (`..`), symlink blocking (`mode 120000`), pipeline & governance file protection (`.github/workflows/`, `CODEOWNERS`, `SECURITY.md`), git credential scrubbing (`.git/config`, hooks), and re-diff verification.
- **Remote Publishing & Milestone Integration Tests (11 tests)**: End-to-end publishing against real bare Git remotes, duplicate PR detection & idempotency (zero duplicate PRs), push retry recovery (reusing existing pushed branch on API timeout), stale-base commit rejection, and tampered bundle rejection.

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

## 🚀 Quick Start (Local Run)

```powershell
# Run AMstraLift locally in dry-run mode
uv run amstralift run --repo "path/to/target-repo" --dry-run
```
