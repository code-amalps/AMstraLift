# AMstraLift Strategic Roadmap

> **Thesis:** Evolve from **upgrade engine** → **verified modernization engine** → **modernization orchestration platform** → **autonomous-but-constrained modernization system**

---

## Generational Architecture

```text
Generation 1 (BUILT)          Generation 2 (NEXT)
┌───────────────────┐          ┌───────────────────┐
│ TRANSFORM         │          │ PLAN              │
│ VERIFY            │    →     │ ORCHESTRATE       │
│ ATTEST            │          │ POLICY            │
│ PUBLISH           │          │ RISK / APPROVE    │
└───────────────────┘          │ RESUME            │
                               └───────────────────┘
                                        │
                                        ▼
Generation 4 (FUTURISTIC)     Generation 3 (ENTERPRISE)
┌───────────────────┐          ┌───────────────────┐
│ AI PLANNING       │          │ MULTI-REPO        │
│ RULE DISCOVERY    │    ←     │ CAMPAIGNS         │
│ DETERMINISTIC     │          │ PROVENANCE        │
│   EXECUTION       │          │ ORG GOVERNANCE    │
│ SELF-VERIFICATION │          │ RUNTIME VERIFY    │
│ CONTINUOUS        │          └───────────────────┘
│   MODERNIZATION   │
└───────────────────┘
```

---

## Generation 1 — Already Built (v0.1.17, commit `12469e6`)

> 110 commits · 68 source files · ~15,950 LOC · ~7,200 test LOC · 74 verified tests

| # | System | Status |
|---|--------|--------|
| 1 | Multi-ecosystem upgrade engine (Angular, React, Python, .NET) | ✅ |
| 2 | Angular modernization suite (14+ AST transformations) | ✅ |
| 3 | Two-stage execution pipeline (Stage A → Stage B) | ✅ |
| 4 | 6-gate sandbox verification | ✅ |
| 5 | Cryptographic trust boundary (HMAC-SHA256) | ✅ |
| 6 | CVE remediation engine (OSV, dependency graph, safe fix) | ✅ |
| 7 | Transformation evidence model (P3) | ✅ frozen |
| 8 | Partial modernization / refusal engine (P4) | ✅ frozen |
| 9 | Gate 6 semantic & contract diff (P5) | ✅ frozen |
| 10 | Governance policies (LTS, EOL, vulnerability) | ✅ |
| 11 | Desktop GUI (Tkinter, dark/light, streaming) | ✅ |
| 12 | REST API (FastAPI + Swagger) | ✅ |
| 13 | Monorepo & polyglot support | ✅ |
| 14 | PR publishing & intelligent batching | ✅ |
| 15 | Standalone Windows distribution (PyInstaller) | ✅ |

### Trust pipeline as of Generation 1

```text
Candidate → Decision Engine → APPLY / REFUSE
                                │          │
                           Transform   Untouched
                            Record      Source
                                │          │
                                └────┬─────┘
                                     ▼
                              6-Gate Sandbox
                              ┌──┬──┬──┬──┬──┬──┐
                              │G1│G2│G3│G4│G5│G6│
                              └──┴──┴──┴──┴──┴──┘
                                     │
                              Evidence Bundle
                              (HMAC-attested)
                                     │
                              Report + PR
```

---

## Generation 2 — Next Development Cycle

### P16 — Transformation Dependency Graph (COMPLETED — v0.2.0)

> **Built:** Full Directed Acyclic Graph (DAG) engine in `amstralift.transforms` modeling prerequisites, conflicts, invalidation, topological execution phases, parallelizable sets, and atomic rollback boundaries. Visualizable via `amstralift graph`.

Today each transformation is independent. With hundreds of rules, AMstraLift needs to understand:

```text
Transformation Graph

T1 ──► T2 ──► T5
│             │
└──► T3 ──────┘
       │
       ▼
      T4
```

**What it models:**

| Relationship | Example |
|---|---|
| Prerequisites | Standalone migration must precede control-flow migration |
| Conflicts | Two rules modifying `angular.json` simultaneously |
| Invalidation | Symbol rename invalidates rules expecting old symbol |
| Ordering | Dependency upgrades before framework migration |
| Dependency chains | Template cleanup depends on standalone + control-flow |
| Rollback boundaries | Which transformations form atomic groups |
| Parallelizable sets | Independent rules that can execute concurrently |

**Example dependency chain:**

```text
Angular standalone migration
        ↓
control-flow migration
        ↓
template cleanup
        ↓
ESLint migration
        ↓
verification
```

**Value:** Makes large migrations safely orchestratable instead of relying on implicit ordering.

---

### P17 — Transformation Planning Engine (COMPLETED — v0.2.0)

> **Built:** Full declarative modernization planning engine in `amstralift.planning` producing staged execution plans, risk breakdowns (safe/dependent/risky/refused), canonical lifecycle phases, parallel batch partitioning, and YAML/JSON export via `amstralift plan`.

Today AMstraLift can *execute* transformations. P17 adds the ability to *decide* what should be transformed, in what order, and why.

```text
Repository
    ↓
Analysis
    ↓
Modernization Plan
    │
    ├── 23 safe transformations
    ├── 4 dependent transformations
    ├── 3 risky transformations
    └── 2 refused
    ↓
Execution Plan
```

**Plan structure:**

```yaml
plan:
  target: angular_21

  phases:
    - dependencies
    - framework_migration
    - template_migration
    - configuration
    - cleanup
    - verification
```

**Depends on:** P16 (transformation dependency graph)

---

### P18 — Cross-Transformation Conflict Detection (COMPLETED — v0.2.0)

> **Built:** Full pre-execution conflict analysis engine in `amstralift.conflicts` detecting explicit graph conflicts, precondition invalidations, file collisions, AST symbol collisions, and configuration key collisions before touching workspace files. Deterministic resolution via `ConflictResolver` and inspection CLI via `amstralift conflicts`.

```text
Rule A: modify angular.json
Rule B: modify angular.json
                                 → FILE_COLLISION detected (resolved via sequencing)

Rule A: rename exported symbol
Rule B: expects old symbol
                                 → SYMBOL_COLLISION detected (resolved via discard)
```

**Pre-execution conflict analysis:**

```text
Rule A ─────┐
            ├── Conflict → Resolved before execution (discard legacy / inject ordering)
Rule B ─────┘
```

**Pairs with:** P4 transactional architecture (rollback on conflict) and P16 (dependency graph expresses conflicts)

---

### P19 — Incremental / Resumeable Modernization (COMPLETED — v0.2.0)

> **Built:** Full persistent checkpoint engine in `amstralift.checkpoints` recording execution runs, phase progression, completed transformation IDs, and SHA-256 evidence integrity hashes. Integrated with `PlanExecutor` for seamless pause/failure recovery and CLI resumption via `amstralift resume` and inspection via `amstralift checkpoints`.

```text
Phase 1 ✓  ─── checkpoint ───┐
Phase 2 ✓  ─── checkpoint ───┤
Phase 3 ✓  ─── checkpoint ───┤
Phase 4 ✗                    │
                              ↓
                      amstralift resume [execution-id]
                              ↓
                         Phase 4 (resumed without re-executing 1-3!)
```

**Checkpoint model:**

```yaml
checkpoint:
  execution_id: exec_00042
  workspace_revision: abc123
  completed_transformations:
    - tr_001
    - tr_002
    - tr_003
  evidence_hash: sha256:...
  timestamp: 2026-10-06T23:30:00Z
  resumable: true
```

**CLI:** `amstralift resume [execution-id]`, `amstralift checkpoints`

---

### P20 — Policy-as-Code (COMPLETED — v0.2.0)

> **Built:** Full declarative enterprise Policy-as-Code engine in `amstralift.policy` validating ModernizationPlans against governance rules, risk tolerances, vulnerability thresholds, symbol contracts, and transformation whitelists/blacklists. Enforced via `amstralift policy check` and `amstralift plan --policy <file>`.

```yaml
policy:
  allow_major_upgrade: false

  security:
    critical_cve: block
    high_cve: review

  semantic:
    public_api_removal: block

  transformations:
    deterministic_only: true

  governance:
    eol_within_days: 180
```

**Evaluation flow:**

```text
Transformation / Plan
      ↓
Policy Engine (PolicyEvaluator)
      ↓
ALLOW / REVIEW / BLOCK
```

**Builds on:** Existing governance policies, P4 refusal engine, P5 Gate 6 semantic diff, and P17 Modernization Planning Engine.

---

### Generation 2 Priority Order (ALL COMPLETED — v0.2.0)

| Priority | Capability | Status |
|---|---|---|
| **P16** | Transformation Dependency Graph | ✅ **Completed** (v0.2.0) |
| **P17** | Modernization Planner | ✅ **Completed** (v0.2.0) |
| **P18** | Conflict Detection | ✅ **Completed** (v0.2.0) |
| **P19** | Resumeable Execution | ✅ **Completed** (v0.2.0) |
| **P20** | Policy-as-Code | ✅ **Completed** (v0.2.0) |

---

## Generation 3 — Enterprise Scale

### Generation 3 Priority Order (IN PROGRESS — v0.3.0)

| Priority | Capability | Status |
|---|---|---|
| **P21** | Multi-Repository Modernization | ✅ **Completed** (v0.3.0) |
| **P22** | Modernization Campaign Engine | ⏳ Planned |
| **P23** | Human Approval Workflow | ⏳ Planned |
| **P24** | Modernization Provenance Graph | ⏳ Planned |
| **P25** | Semantic Risk Engine | ⏳ Planned |
| **P26** | Runtime Verification | ⏳ Planned |
| **P27** | Pattern Mining Engine | ⏳ Planned |
| **P28** | Self-Learning Transformation Library | ⏳ Planned |
| **P29** | Universal Semantic Model | ⏳ Planned |
| **P30** | Autonomous Modernization Agent | ⏳ Planned |

---

### P21 — Multi-Repository Modernization ✅ (v0.3.0)

```text
Organization
│
├── repo-a (Angular 16)
├── repo-b (Angular 18)
├── repo-c (React 17)
├── repo-d (.NET 6)
└── repo-e (shared-lib)
```

**Cross-repo dependency awareness:**

```text
service-A
   ↓
shared-library        → upgrading this requires coordinated
   ↓                     modernization of 14 repositories
service-B
```

**Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).**
- **Discovery Engine**: `FleetScanner` recursively locates repositories, multi-project monorepos, and manifests across Angular, React, .NET, and Python.
- **Provider & Consumer Cross-Referencing**: Automatic extraction of published packages (npm names, NuGet PackageIds/assemblies, Python project names) and consumed dependencies to build inter-repo dependency edges.
- **Topological Wave Execution**: `FleetDependencyGraph` builds an acyclic graph of inter-repo links, detects inter-repo circular dependencies (`InterRepoCycleError`), and partitions modernization into parallelizable, staged execution waves (Wave 1: providers/libraries; Wave 2+: consumers).
- **Campaign Planning Engine**: `FleetPlanner` produces deterministic multi-repo modernization plans with dependency prerequisite matrices and JSON persistence.
- **CLI Commands**: `amstralift fleet scan`, `amstralift fleet graph`, `amstralift fleet plan`, with alias `amstralift multirepo`.

**Evolution from:** Existing monorepo topology scanner

---

### P22 — Modernization Campaign Engine

> **Turn individual upgrades into organization-wide campaigns.**

```text
Campaign: Q1 2027 Angular Modernization
│
├── 143 repositories
├── 68 Angular → 22
├── 31 React → 20
├── 24 Python → 3.14
└── 20 .NET → 10
```

**Dashboard metrics:**

```text
Total repos           143
Modernized             97
Partial                31
Refused                11
Failed                  4

CVE reduction          -87%
EOL reduction          -94%
Engineering hours saved 2,340
Upgrade success rate    92%
Refusal rate            7.7%
Mean verification time  4m 23s
```

**Builds on:** Existing pilot metrics infrastructure

---

### P23 — Human Approval Workflow

```text
AMstraLift
     ↓
Risk Classification
     ↓
┌──────────────┐
│ SAFE         │ → automatic PR merge
│ REVIEW       │ → human approval required
│ BLOCK        │ → stop, notify
└──────────────┘
```

**Approval triggers:**

- API breaking change
- Database migration dependency
- Major version jump
- Security override
- Unsupported transformation pattern

**Gives enterprise teams:** Controlled automation with human oversight where it matters

---

### P24 — Supply-Chain Attestation

> **From HMAC attestation → SLSA/in-toto-style provenance**

```text
Source Revision
      +
Transformation Rules (versioned)
      +
Tool Version
      +
Policy Version
      +
Dependency State
      +
Verification Results
      ↓
Signed Provenance Record
```

**Six-months-later query:**

> "Why was this code changed?"

```text
Repository revision:   abc123
Transformation:        migrate-ngmodule-to-standalone
Rule version:          angular-rule-14
Policy:                enterprise-policy-v3
Verification:          6/6 passed
Operator:              CI
Evidence:              cryptographically verified
```

**Evolution from:** Existing HMAC attestation in `core/crypto.py`

---

### P25 — Semantic Risk Engine

> **Gate 6 evolves from pass/fail → risk quantification**

```text
Semantic Change
       ↓
Risk Engine
       ↓
LOW / MEDIUM / HIGH / CRITICAL
```

| Change | Risk |
|--------|------|
| Added private helper | LOW |
| Added public method | LOW |
| Changed internal config | MEDIUM |
| Changed API response shape | HIGH |
| Removed API route | CRITICAL |
| Changed DB schema | CRITICAL |

**Risk feeds into:** P23 approval workflow (CRITICAL → BLOCK, HIGH → REVIEW, LOW → AUTO)

---

### P26 — Runtime Verification

> **Prove behavioral equivalence, not just build success.**

```text
Transformation
      ↓
Static verification (Gates 1-6)
      ↓
Sandbox deployment
      ↓
Synthetic traffic
      ↓
Runtime telemetry
      ↓
Behavior comparison
```

**Example:**

```text
Before:                         After:
POST /orders                    POST /orders
  latency: 120ms                  latency: 130ms
  status: 201                     status: 201
  body: { id: "..." }             body: { id: "..." }

Verdict: EQUIVALENT ✓
```

**AMstraLift can then prove:** *"It behaves equivalently under defined workloads"* — not just *"It builds."*

---

## Generation 4 — Futuristic

### P27 — AI-Assisted Rule Discovery

> **Not AI-generated arbitrary code. Pattern mining → candidate deterministic rules.**

```text
Repository corpus
    ↓
Pattern mining
    ↓
Repeated legacy pattern (4,821 occurrences across 73 repos)
    ↓
Candidate transformation rule
    ↓
Human review
    ↓
Deterministic rule (trusted)
```

**Critical constraint:** AI discovers patterns. Execution remains deterministic. Trust philosophy preserved.

---

### P28 — Self-Learning Transformation Library

```text
Successful modernizations
     ↓
Evidence corpus
     ↓
Pattern extraction
     ↓
Candidate Rule
     ↓
Validation Corpus
     ↓
Regression Tests
     ↓
Human Approval
     ↓
Trusted Rule
```

> [!IMPORTANT]
> **Never allow production executions to automatically create trusted transformation rules.** Same philosophy as the refusal/evidence system.

---

### P29 — Universal Semantic Model

> **Gate 6 becomes ecosystem-independent.**

```text
Angular ──┐
React ────┤
Python ───┤
.NET ─────┤
Java ─────┤
Go ───────┤
           ▼
    Universal Software
      Contract Model
```

**Represents:**

```text
Service · API · Route · Symbol · Dependency
Configuration · Database · Event · Queue
Authentication Boundary
```

**Could become:** One of AMstraLift's strongest architectural assets

---

### P30 — Autonomous Modernization Agent

> **AI as planner/reasoner, AMstraLift as deterministic execution and verification authority.**

```text
            ┌──────────────────┐
            │ AI Modernization │
            │ Planner          │
            └────────┬─────────┘
                     ↓
              Deterministic Plan
                     ↓
            ┌──────────────────┐
            │ AMstraLift       │
            │ Execution Engine │
            └────────┬─────────┘
                     ↓
             Transformation
                     ↓
          6-Gate Verification
                     ↓
           Semantic Verification
                     ↓
             Risk Assessment
                     ↓
         ┌───────────┴──────────┐
         ▼                      ▼
      Automatic             Human Review
         │                      │
         └───────────┬──────────┘
                     ↓
                 PR / Merge
```

**This is NOT:** An LLM coding agent that edits production repositories.

**This IS:** AI intelligence directing a deterministic, verified, attested execution pipeline with human oversight at risk boundaries.

---

## What NOT to Prioritize

| Don't | Why |
|-------|-----|
| Another 50 transformation rules | Execution foundation is solid; intelligence around it matters more |
| Another GUI redesign | Current GUI is functional; campaign dashboard comes with P22 |
| Another adapter just for ecosystem count | 4 ecosystems is sufficient to prove the model |
| LLM directly editing production repos | Violates the trust/determinism philosophy |
| Dashboard before campaign-scale exists | UI should follow capability, not precede it |

---

## Summary

```text
              AMstraLift Evolution
                     │
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
 Gen 1            Gen 2            Gen 3-4
 Upgrade          Modernization    Modernization
 Engine           Orchestration    Platform
                  Platform
    │                │                │
    ▼                ▼                ▼
 Transform        Plan             Campaign
 Verify           Orchestrate      Multi-repo
 Attest           Policy           Provenance
 Publish          Risk/Approve     Runtime verify
                  Resume           AI planning
```

> The existing architecture already points toward this: Stage A is isolated transformation, Stage B is controlled publication, and the 6 gates establish the verification boundary. The next moat is **intelligence around the execution**.
