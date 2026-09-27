# AMstraLift

> **Upgrade safely. Modernize confidently.**

AMstraLift is an automated dependency and framework upgrade engine built around an explicit, cryptographically bound trust boundary. Zero AI in execution. No auto-merge, ever, for any tier.

---

## 📌 Implementation Status

> **Core engine implementation complete; production operationalization remains.**  
> The implemented scope has 58 passing tests. Remote publishing, scheduled execution, and fleet orchestration are still pending.

### Test Taxonomy (58 Passing Tests)
- **Unit Tests (19 tests)**: Cryptographic signing (`HMAC-SHA256`), canonical bundle serialization, TTL expiry, governance policies (Angular LTS staleness checks, Python runtime default resolution with no-match fallback, time-boxed CVE exceptions, EOL escalation SLAs, Section 16 pilot metrics calculation).
- **Adapter Integration Tests (16 tests)**: Real manifest and lockfile generation, version discovery, and build/test gates across Angular, Python (PyPI), .NET (NuGet), and React (npm).
- **Trust-Boundary & Security Tests (17 tests)**: Path traversal rejection (`..`), symlink blocking (`mode 120000`), pipeline & governance file protection (`.github/workflows/`, `CODEOWNERS`, `SECURITY.md`), git credential scrubbing (`.git/config`, hooks), and re-diff verification.
- **CLI & Local Git Integration Tests (6 tests)**: CLI option parsing, local repository sandbox execution, and local git branch patch commits.
- *End-to-end tests with a real Git provider*: Pending (see Next Milestone).
- *Scheduled CI execution tests*: Pending.

---

## 🛡️ Production-Readiness Checklist

Before enabling scheduled runs against real production repositories, the following operational safeguards must be verified:

- [ ] **Least-Privilege Credentials**: Tokens are least-privilege, short-lived where possible, and stored securely.
- [ ] **Publishing Kill-Switches**: Publishing can be disabled globally and per-repository (via config flag / dry-run default).
- [ ] **Idempotency & Duplicate Prevention**: Retries and recurring schedules cannot create duplicate branches or PRs.
- [ ] **Failure Visibility & Recovery**: Failed and partial runs are visible, alerted, and recoverable without manual repo cleanup.
- [ ] **Log & Bundle Scrubbing**: Logs and bundles do not expose credentials or sensitive repository content.
- [ ] **Rate Limiting & Concurrency**: Strict limits on concurrency, retries, and Git provider API rate usage.
- [ ] **Documented Rollback Procedure**: A documented rollback, close-out, or disable procedure is established.
- [ ] **Gated Pilot Approval**: A pilot runs against a small, explicitly approved repository set before fleet-wide rollout.

---

## 🗺️ Workstream Roadmap & Priorities

| Priority | Work Item | Objective | Status |
| :---: | :--- | :--- | :---: |
| **P1** | **Remote Git Provider Publishing** | Push verified branch to `origin` and open a reviewable PR via GitHub/GitLab API. | **Current Milestone** |
| **P2** | **Duplicate PR Detection & Idempotency** | Prevent repeated runs from creating duplicate branches or PRs using matching-label lookups. | **Current Milestone** |
| **P3** | **CI Workflow Templates** | Provide turnkey `.github/workflows/` and `.gitlab-ci.yml` definitions for scheduled cron execution. | Pending |
| **P4** | **Fleet Manifest & Runner** | Central `fleet.yaml` inventory with matrix/batch invocation across multiple repositories. | Pending |
| **P5** | **Demonstration Suite** | Ready-to-run demo apps and walkthrough script for rapid validation. | Pending |
| **P6** | **Platform Layer (DB, API, UI)** | Centralized database, FastAPI control plane, and Next.js dashboard for enterprise scale. | Post-MVP |

---

## 🎯 Current Milestone: Publish One Verified Upgrade PR to a Test Repository

**Acceptance Criteria:**
1. Run AMstraLift against a disposable test repository.
2. Complete Stage A in an isolated workspace and produce a signed advisory bundle.
3. Stage B validates the cryptographic signature, TTL, path allowlists, and required test gates.
4. The publisher pushes the upgrade branch to remote `origin` and opens a PR with labels and 24h SLA deadline.
5. A repeated run detects the existing open PR rather than creating a duplicate.
6. A stale base commit or tampered bundle is rejected.
7. The result, audit trail, and any failure reasons are recorded.

---

## 🚀 Quick Start (Local Run)

```powershell
# Run AMstraLift locally in dry-run mode
uv run amstralift run --repo "path/to/target-repo" --dry-run
```
