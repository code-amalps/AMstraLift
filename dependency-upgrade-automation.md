# AMstraLift — Automated Dependency & Framework Upgrade Plan (v6, implementation-ready)

**Project name:** AMstraLift  
**Tagline:** Upgrade safely. Modernize confidently.

**What AMstraLift produces:** AMstraLift does **not generate a new application from scratch**. It upgrades existing application repositories by proposing dependency/framework changes and opening reviewable pull requests. The current planned adapters cover **Angular, .NET, Python, and React**; the generated output is an upgrade patch/branch, pull request, and supporting build/test evidence—not a newly scaffolded application.

**Goal:** Eliminate the weekly manual effort of chasing Aqua/Qualys scan findings across many repos by automatically detecting outdated frameworks/libraries, upgrading them safely, and routing the result to the existing support/review team — without breaking production functionality, and without the tool itself becoming a maintenance burden.

**Non-goals:** No AI/LLM in the execution path. No auto-merge, ever, for any tier. No single "do everything" script. No component requiring a human to keep a compatibility table up to date. No automated report, however well-bound, is ever treated as sufficient proof of safety on its own.

**v6 changes:** adds a completion deadline so pending CI checks can't hide behind indefinite rollover; requires a human reviewer action (not a pipeline-driven update) to reset the stale-PR clock; converts a missed EOL SLA into a formal, jointly-approved exception with compensating controls; and defines the safe no-action fallback when no Python version satisfies the runtime-default rule.

---

## 1. Architecture — Two-Stage Execution, With an Explicit Trust Boundary (unchanged)

```
┌───────────────────────────┐        ┌───────────────────────────┐        ┌──────────────────────────┐
│  STAGE A — EXECUTION       │        │  STAGE B — PUBLISH         │        │  ORG'S OWN CI + REVIEW    │
│  (untrusted, isolated)     │ bundle │  (trusted, minimal surface)│  PR    │  (independent of this      │
│  - real repo, real build/  │──────► │  - validates bundle        │──────► │  pipeline entirely)        │
│    test, produces evidence │        │  - applies patch, opens PR │        │  - required status checks  │
│  - NO write credentials    │        │  - holds the only write    │        │  - mandatory human review  │
│  - report is ADVISORY only │        │    credential               │        │  - gates merge, every tier,│
│                            │        │                             │        │    no exception (Tier 1    │
│                            │        │                             │        │    included)                │
└───────────────────────────┘        └───────────────────────────┘        └──────────────────────────┘
```

Stage A's report determines *what PR gets opened and how it's labeled* — never *whether it's safe to merge*. That belongs entirely to the org's own required CI checks and a mandatory human reviewer, every tier, no exception.

---

## 2. Version Discovery (unchanged)

| Ecosystem | Latest version source | Runtime requirement source |
|---|---|---|
| Angular (npm) | `registry.npmjs.org/@angular/core` → `dist-tags.latest` | `@angular/cli`'s `package.json` → `engines.node` |
| .NET (NuGet) | `api.nuget.org/v3-flatcontainer/{package}/index.json` | Microsoft's published releases-index |
| Python (PyPI) | `pypi.org/pypi/{package}/json` → `info.version` | `pyproject.toml` → `requires-python`, else the governed firm default (Section 6) |
| React (npm) | `registry.npmjs.org/react` → `dist-tags.latest` | N/A |

---

## 3. Compatibility Resolution — Real Repo, Full Peer Scope (unchanged)

Real manifest files on the working branch → real resolution command → immediate real build/test in the same environment on success; fail closed with an issue (never a PR) on conflict. TypeScript, RxJS, and Node are in scope automatically via Angular's own peer dependencies.

---

## 4. Report Integrity, Freshness, and Stage B Patch Validation (unchanged)

Every report bound to base commit SHA, patch hash, lockfile hashes, and raw structured test-tool output. Stage B rejects any bundle whose base SHA doesn't match current branch HEAD. Path allowlists, categorical rejection of traversal/symlinks/security-config changes, hash verification, size limits, and a post-push re-diff against the intended patch. This proves integrity, not honesty — Section 1's independent-CI-plus-human-review layer covers honesty.

---

## 5. Angular LTS Value — Owned, Governed, Self-Enforcing (unchanged)

Named owner (e.g. "Frontend Platform Lead") maintains `angular-lts-policy.yaml` with `current_lts_majors`, `last_verified`, `next_review_by`, `verified_by`. If `next_review_by` has passed, the pipeline treats the value as unverified, falls back to "latest stable" only, and escalates to the owner — it does not use stale data silently.

---

## 6. Python Runtime Default — Governance Defined, With a Safe No-Match Fallback (clarified)

**Precedence:** a repo's own `requires-python` always wins. Firm default applies only when absent.

**Default selection rule:** the newest Python minor that is (a) stable for at least 3 months, and (b) present in the org's approved base-image list. Owned by the platform/security team, reviewed on the same cadence and staleness-check pattern as Section 5.

**When no version satisfies both conditions simultaneously (new):** the pipeline makes **no automatic runtime change.** The current runtime is left untouched, and the repo is flagged for manual platform-team triage. This mirrors the plan's existing rule elsewhere — when a policy can't cleanly resolve, flag rather than guess, and never let "no clean answer" become "pick something anyway."

**Override:** a repo can override the default at any time by declaring its own `requires-python`.

---

## 7. Migration Safety (unchanged)

Required migrations only; opt-in migrations never invoked automatically. Application source changes touched by a migration get a mandatory manual-review flag unconditionally. Builder behavior-change detection independent of config diff. Unclassifiable output defaults to high-risk.

---

## 8. Dependency Classification and Tiering (unchanged from v5)

| Tier | Definition | Pipeline behavior |
|---|---|---|
| **1 — Safe** | No side effects on core behavior | Upgrade execution and PR creation are fully automated. **Merge is never automated** — same required CI checks and human reviewer as every other tier. |
| **2 — Verify behavior** | State mgmt, routing, interceptors | Auto-upgraded; PR labeled "requires functional QA" |
| **3 — Critical** | Auth, payments, security | Compatibility-gated; PR labeled "requires manual security verification"; existing auth/e2e test required and blocking if present |

Direct/transitive/dev dependency handling as in v5 — transitive-only fixes still get the full build/test gate; dev dependencies get a lighter tier since they can't cause a runtime incident but can still break CI.

---

## 9. Build & Test Gates (unchanged)

Per-repo declared commands; coverage classification (`unit+e2e` / `unit only` / `none`). PR status visually distinguishes *required and passed*, *required but skipped/unavailable*, and *optional passed* — never conflated.

---

## 10. Vulnerability Lifecycle (unchanged)

Detection → triage → remediation attempted → resolved or exception. Exceptions require a named security-team approver, documented evidence, severity-based expiry (Critical 30d / High 60d / Medium-Low 90d), with periodic re-check for a newly published fix.

---

## 11. Runtime EOL Escalation — Missed-SLA Path Added (clarified)

- **Acknowledgment SLA:** 2 business days.
- **Remediation SLA:** 30 days.
- **If acknowledgment is missed:** auto-escalates to a designated secondary (engineering manager or security lead); marked overdue in the tracking store.
- **If remediation is blocked or the 30-day target is missed (new):** this converts into a **formal documented exception** — the same rigor as a vulnerability exception (Section 10): joint named sign-off from security and engineering, a **compensating-controls field** describing what mitigates the risk in the interim, and its own expiry date subject to the same periodic re-check. The approvers must set an explicit exception duration and next review date; any extension requires renewed joint approval. A missed deadline never lapses silently; it becomes an explicit, owned, time-boxed decision.

---

## 12. Failure & Retry Semantics (unchanged)

Retryable = infrastructure-class only. Not retryable = build/test/migration/compatibility failures, straight to issue. Retries start from scratch; partial hop changes discarded. Base-branch divergence triggers rebase-and-rerun, never force-push. Duplicate PR/issue avoidance via matching-label lookup.

---

## 13. Execution Isolation & Security (unchanged)

Stage A: disposable, no write credential, install scripts disabled by default with onboarding-approved allowlist, restricted network, pre-execution audit of new packages. Stage B: disposable, holds the only short-lived write credential, never executes repository-controlled code.

---

## 14. Interface (unchanged)

CLI tool invoked by scheduled CI jobs; same tool usable ad hoc for a single repo.

---

## 15. Fleet-Scale Rollout (unchanged)

Central inventory + matrix invocation; staggered schedules; CODEOWNERS-style routing for Tier 3; staleness policy per Section 16.

---

## 16. Pilot Exit Criteria — Fully Precise (final clarifications applied)

**Measurement window:** each scheduled weekly cycle; reported per-window and cumulatively across 2–3 pilot windows.

| Criterion | Precise definition | Threshold |
|---|---|---|
| Scheduled run completion rate | Runs completing without infrastructure failure ÷ all scheduled runs, per window | ≥ 95% |
| PRs passing required CI checks | **Completion deadline: all required checks must succeed within 24 hours of PR creation.** Denominator = PRs whose required checks either completed or reached their 24-hour deadline within the window. Numerator = denominator PRs for which **every required check succeeded within 24 hours**. Any failed, skipped/unavailable, or still-pending required check at the 24-hour deadline makes that PR a failure. Only PRs still within their 24-hour window at measurement time roll to the next window. | ≥ 90% |
| Incorrect / unnecessary dependency change rate | Flagged-incorrect changes ÷ total changes proposed, per window | < 2% |
| Duplicate PR count | PRs opened for the same repo/dependency target while another was already open | 0 |
| Stale PR threshold | A PR is stale if open > 7 calendar days without merge, close, or a **human reviewer action** (comment, review, or response to a review request). **A pipeline-driven rebase or automated comment does not reset this clock** — only a person engaging with the PR does. | 0 stale PRs at each weekly checkpoint; if nonzero, the staleness policy must fire before the next cycle |
| Audit trail sufficiency | Explicit confirmation from the support/security team | Required, qualitative |

**Decision ownership:** rollout expansion requires explicit, named joint sign-off from an engineering lead and a security lead, against these thresholds as measured.

---

## 17. What Stays Explicitly Out of Scope (MVP)

- No AI/LLM anywhere in the execution path
- No auto-merge, for any tier, including Tier 1
- No cross-major/version skipping; no invoking optional/semantic migrations
- No dashboard/website in the MVP
- No maintained compatibility tables left to age silently — Angular LTS and Python default values are owned, dated, and self-check their own staleness
- No custom dependency resolver
- No write credential ever present in the environment executing repository-controlled code
- No Stage A report ever treated as sufficient proof of safety on its own
- **No indefinite silence on a missed SLA** — EOL and vulnerability deadline misses always convert into a formal, approved, time-boxed exception
- **No automatic runtime change when the Python default rule can't resolve cleanly** — manual triage instead

---

## 18. Build Order

1. Two-stage execution model + trust boundary (Sections 1, 4, 13).
2. Angular adapter: real-repo resolution (Section 3), migration diff classification (Section 7).
3. Build/test gates with required/skipped/passed distinction, including the 24-hour CI completion deadline (Section 9, Section 16).
4. Version-policy resolution: self-enforcing Angular LTS check (Section 5), governed Python default with no-match fallback (Section 6).
5. Vulnerability lifecycle (Section 10) and EOL escalation with missed-SLA exception path (Section 11).
6. Failure/retry/duplicate-avoidance logic, including human-action-only stale-PR reset (Section 12, Section 16).
7. Confirm org branch-protection rules (required checks + mandatory review, all tiers) are in place on pilot repos *before* the first automated PR.
8. Pilot on 1–2 low-risk repos → measure against Section 16's exact thresholds across 2–3 windows.
9. .NET adapter → Python adapter → React adapter.
10. Fleet-scale rollout only after named sign-off per Section 16.
