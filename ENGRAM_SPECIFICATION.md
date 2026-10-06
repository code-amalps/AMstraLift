# Engram

> **Engineering memory and change intelligence for AI coding agents.**

---

## 1. Vision

Engram is an engineering intelligence layer that continuously builds and maintains an evidence-backed understanding of a software project.

The goal is **not** to replace the AI coding agent.

The goal is:

> **Engram understands globally. The AI agent reasons and executes locally.**

Instead of making an AI agent rediscover a large codebase for every task, Engram provides the minimum sufficient context, relevant constraints, expected change boundary, and validation information needed to work efficiently with fewer unnecessary tokens and fewer implementation/regression errors.

---

## 2. Core Problem

AI coding agents repeatedly spend tokens and time rediscovering:

- project structure
- folders and modules
- files and symbols
- dependencies
- architecture
- coding patterns
- naming conventions
- API relationships
- test relationships
- configuration
- historical patterns
- project-specific constraints

But repository rediscovery is only half of the problem. Once an agent starts editing, developers also need to know whether the implementation stayed within the intended change boundary.

Engram therefore addresses four connected problems:

### 2.1 Repository rediscovery
Agents repeatedly explore the same architecture and relationships. This consumes tokens, tool calls, latency, and reasoning capacity.

### 2.2 Task ambiguity
Agents can begin implementation while important requirements, constraints, dependencies, or validation expectations remain unknown.

### 2.3 Unexpected change impact
An agent may legitimately need to touch several files, but the developer should be able to see when the actual change is materially different from the expected change. The problem is not simply a large diff; it is **unexplained deviation from intended scope or architecture**.

### 2.4 Lost engineering knowledge
After a change is accepted, important decisions, exceptions, architecture knowledge, and historical evidence can disappear into the repository history and force future agents to rediscover them.

Engram should build this understanding once, maintain it as the repository changes, use it to compile task-specific context, compare intended vs actual change, and preserve accepted engineering knowledge across tasks and agents.

### Core principles

> **Don't make AI rediscover information that Engram already knows.**

> **Know what should change. Know what actually changed. Remember why.**

Token reduction and change safety are coupled goals: Engram should reduce unnecessary discovery **without sacrificing correctness, functionality, or regression safety**.

---

## 3. Product Boundary

Engram is an **aid for developers**, not an enforcement system.

### Engram does
- analyze the project
- maintain engineering memory
- identify relevant areas
- detect important gaps and ambiguity
- compile task-specific context
- identify expected change impact
- provide constraints and recommendations
- validate actual changes against expected impact
- update its memory after accepted changes

### AI Agent does
- reason about the requested change
- write and modify code
- interact with the developer
- execute the implementation

### Developer remains the authority
Engram should inform and warn rather than force developers into a prescribed workflow.

Example:

```text
Expected impact:
  4 files
  8 symbols
  2 modules

Actual impact:
  7 files
  16 symbols
  4 modules

Warning:
The implementation exceeded the expected change boundary.

[Review] [Continue anyway] [Re-plan]
```

A developer should be able to override Engram with an explicit decision/reason where appropriate.

---

## 4. Core Architecture

Conceptually:

```text
                         DEVELOPER
                             |
                             | task
                             v
                    +-------------------+
                    |      ENGRAM       |
                    |                   |
                    | Engineering       |
                    | Memory            |
                    |       |           |
                    |       v           |
                    | Gap Detection     |
                    |       |           |
                    |       v           |
                    | Context Compiler  |
                    |       |           |
                    |       v           |
                    | Change Analysis   |
                    +---------+---------+
                              |
                              | relevant context,
                              | constraints,
                              | expected impact
                              v
                       +--------------+
                       |  AI AGENT    |
                       |              |
                       | Reason       |
                       | Implement    |
                       +------+-------+
                              |
                              | changed repository
                              v
                    +-------------------+
                    |      ENGRAM       |
                    | Change Guard      |
                    | Validation        |
                    | Memory Update     |
                    +-------------------+
```

---

## 5. Architectural Subsystems

Rather than forcing the system into an arbitrary engine count, Engram cleanly distinguishes **Core Engines** from **Supporting Intelligence**.

```text
Engram Architecture
├── Core Engines
│   ├── Repository Analyzer
│   ├── Engineering Memory
│   ├── Context Compiler
│   ├── Change Intelligence
│   └── Memory Updater
│
└── Supporting Intelligence
    ├── Gap Detector
    ├── Plan Mode
    ├── Change Contract
    └── Change Guard
```

### 5.1 Core Engines

* **Repository Analyzer:** Builds the initial engineering model via deterministic static analysis (symbols, files, AST, dependencies, Git history, conventions).
* **Engineering Memory:** Manages persistent models, separating permanent team intent from derived analytical state.
* **Context Compiler:** Compiles minimum sufficient context for tasks under progressive token budget constraints.
* **Change Intelligence:** Compares expected change boundaries against actual repository modifications.
* **Memory Updater:** Reconciles accepted changes, prunes invalidated knowledge, and promotes proven changes into durable memory.

### 5.2 Supporting Intelligence

* **Gap Detector:** Identifies material ambiguities in scope, behavior, or constraints.
* **Plan Mode:** Facilitates focused clarification questions before token burn begins.
* **Change Contract:** Machine-readable specification of intended change boundaries.
* **Change Guard:** Advisory validator checking diffs for unexpected dependencies, architecture drift, and missing tests.

---

## 6. What Does "Understand" Mean?

Engram should not claim magical or absolute understanding.

Working definition:

> **Engram understands a project when it can identify the structure, relationships, conventions, constraints, behavior boundaries, and relevant history necessary to safely reason about a requested change.**

Understanding includes:
- **Structural:** Projects, folders, files, modules, classes, interfaces, methods, packages.
- **Relationships:** Calls, implements, inherits, depends-on, tests, exposes-API.
- **Conventions:** Naming conventions, folder layout, DI registration, logging, testing patterns.
- **Architecture:** Boundaries (Presentation $\to$ Application $\to$ Domain), allowed vs forbidden dependencies.
- **Behavioral:** Endpoint to service to database workflows.
- **Historical:** Co-changing components and commit stability evidence.

---

## 7. The Canonical Knowledge Model (Formal Ontology)

To ensure Phase 0 data modeling is rigorous, executable, and durable, Engram defines a concrete knowledge schema rather than an abstract graph.

### 7.1 Core Entity Types

```text
Canonical Model
├── Entity              (Project, Module, Package, File, Symbol, ApiEndpoint, TestEntity)
├── Relationship        (Typed directed edge between entities)
├── Evidence            (Provenance envelope binding observations/inferences to entities/edges)
├── Observation         (Immutable static fact extracted from a specific revision)
├── Inference           (Calculated conclusion derived from heuristics or graph centrality)
├── Prescription        (Machine-evaluatable normative rule declared in Constitution)
├── Revision            (Git commit hash, branch state, or dirty tree snapshot)
├── Change              (Delta observed in code: added/modified/deleted symbols, origin)
├── Task                (User goal, token budget, intent grounding context)
├── ChangeContract      (Pre-edit contract defining expected boundaries & constraints)
├── Decision            (Developer review, override reason, audit record)
└── MemoryEntry         (Durable, promoted engineering knowledge)
```

### 7.2 Entity Hierarchy & AST Structural Fingerprinting

Entities are organized hierarchically (`Module -> File -> Class -> Method`). To ensure entity identity survives file moves, renames, and refactorings without losing historical continuity, every entity carries an immutable structural fingerprint:

```json
{
  "entity_id": "ent_sym_01J7K9...",
  "kind": "SYMBOL",
  "subkind": "METHOD",
  "name": "ExportCsv",
  "parent_id": "ent_cls_order_service",
  "location": {
    "file_path": "src/Orders/OrderService.cs",
    "range": { "start_line": 42, "end_line": 85 }
  },
  "ast_fingerprint": "sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
  "signature": "(orders: List[Order], format: ExportFormat) -> FileStream",
  "status": "OBSERVED"
}
```

* **AST Fingerprint:** A normalized hash of the AST subtree (parameter types, return type, control-flow shape, stripped of local variable naming trivia).
* **Identity Continuity:** When `OrderManager.ExportCsv()` is renamed to `OrderService.ExportCsv()`, matching AST fingerprints + signature match + Git rename heuristics preserve the same `entity_id`.

### 7.3 Typed Relationship Taxonomy (Edge Semantics)

Edges in Engram are strictly typed, directed, and carry provenance:

```text
(Source Entity) ---[ Typed Relationship Edge ]---> (Target Entity)
```

| Class | Relationship Types | Metadata Properties |
| :--- | :--- | :--- |
| **Syntactic (AST)** | `CONTAINS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `CALLS`, `INSTANTIATES` | Source line, static invocation count, visibility |
| **Domain / Architectural** | `EXPOSES_ENDPOINT`, `QUERIES_TABLE`, `DISPATCHES_EVENT` | Route path, HTTP verb, schema contract |
| **Verification** | `TESTS`, `MOCKS`, `ASSERTS` | Test runner, suite name, assertion count |
| **Statistical (Git)** | `CO_CHANGES_WITH` | Commit support count, co-change ratio, last observed commit |

### 7.4 Machine-Evaluatable Prescription Schema (Rules Engine)

Rules in `.engram/constitution.yaml` are mapped to machine-evaluatable `Prescription` entities evaluated by the Change Guard:

```json
{
  "prescription_id": "rule_clean_arch_domain_isolation",
  "intent": "ARCHITECTURAL_BOUNDARY",
  "severity": "ERROR",
  "selector": {
    "source_kind": "SYMBOL",
    "path_pattern": "src/Domain/**"
  },
  "constraint": {
    "forbidden_relationship": "DEPENDS_ON",
    "forbidden_target_pattern": "src/Infrastructure/**"
  },
  "exceptions": [
    "src/Infrastructure/Logging/ILogger.cs"
  ]
}
```

### 7.5 Revision Spans & Bi-Temporal Modeling (Preventing Graph Bloat)

To avoid duplicating the entire graph on every commit, Engram uses **bi-temporal revision spans**:
- Every `Entity` and `Relationship` has `created_at_revision` (commit hash) and `invalidated_at_revision` (nullable commit hash).
- When a commit modifies 2 files out of 5,000:
  - Only the affected nodes have their `invalidated_at_revision` set to the new commit hash.
  - New nodes are inserted with `created_at_revision = new_commit`.
  - The remaining 4,998 files are untouched and shared across revisions.

```sql
-- Querying current HEAD state in SQLite:
SELECT * FROM entities 
WHERE created_at_revision <= :head_commit 
  AND (invalidated_at_revision IS NULL OR invalidated_at_revision > :head_commit);
```

---

## 8. The Evidence & Confidence Model

Engram must never treat inference as fact. Every entity property and relationship edge is backed by an **Evidence Envelope**.

### 8.1 The Six Evidence Tiers

```text
Tier 1: EXPLICIT     (Declared in Constitution, manual developer overrides, human ground truth)
Tier 2: VERIFIED     (Runtime test execution, successful compiler type-check, build validation)
Tier 3: EXTRACTED    (Deterministic AST static analysis, parser import graphs)
Tier 4: HISTORICAL   (Git commit frequency, empirical co-change patterns)
Tier 5: INFERRED     (Heuristics, PageRank centrality, naming conventions, BM25 matching)
Tier 6: PROVISIONAL  (Unmerged / unvalidated agent working tree edits)
```

### 8.2 The Evidence Envelope Schema

```json
{
  "evidence_id": "evi_01J7KZ...",
  "target_id": "rel_order_controller_calls_order_service",
  "tier": "EXTRACTED",
  "confidence_score": 0.99,
  "source_type": "STATIC_AST_PARSER",
  "provenance": {
    "file_path": "src/Orders/OrderController.cs",
    "line_number": 42,
    "parser_version": "tree-sitter-csharp@0.20.0"
  },
  "recorded_at": "2026-10-05T21:40:00Z",
  "decay_half_life_commits": null,
  "verification_status": "UNCONTESTED"
}
```

### 8.3 Mathematical Confidence & Decay Formulas

1. **Statistical Co-Change Confidence:**
   Calculated from Git log mining:
   $$P(B \mid A) = \frac{\text{Commits}(A \cap B)}{\text{Commits}(A)}$$
   Where $A$ and $B$ are files or symbols modified together within the same commit.

2. **Temporal Decay for Historical Evidence:**
   Old historical patterns decay in relevance so ancient architectural decisions do not dominate current recommendations:
   $$\text{Confidence}(t) = \text{Confidence}_0 \times 2^{-\frac{\Delta \text{commits}}{\text{half\_life}}}$$
   *Default half-life:* 200 commits for historical co-changes. Explicit rules and Extracted AST edges do **not** decay.

3. **Strict Conflict Resolution Policy:**
   When two pieces of evidence contradict:
   $$\text{EXPLICIT} > \text{VERIFIED} > \text{EXTRACTED} > \text{HISTORICAL} > \text{INFERRED} > \text{PROVISIONAL}$$
   *Rule:* A higher tier strictly supersedes a lower tier. An inferred relationship is immediately replaced when static AST analysis extracts the true binding.

---

## 9. Context Compiler & Progressive Expansion

Given: *"Add CSV export for orders"*, Engram compiles the minimum sufficient context:

```text
TASK: Add CSV export for orders.
RELEVANT AREA: Orders
LIKELY FILES: OrderController.cs, OrderService.cs, OrderRepository.cs
RELATED PATTERN: Existing Customer CSV export
DEPENDENCIES: OrderController -> OrderService -> OrderRepository
TESTS: OrderServiceTests, OrderControllerTests
CONVENTIONS: Export services use IExportService; CSV uses project library.
CONSTRAINTS: No breaking API changes; follow existing export pattern.
```

### Progressive Expansion Levels:
- **Level 1:** Project/module metadata
- **Level 2:** Relevant symbols & relationship signatures
- **Level 3:** Critical source code snippets
- **Level 4:** Deep dependency source code

**The objective:** Minimum sufficient context, not maximum context.

---

## 10. Gap Detection & Plan Mode

Engram identifies material unknowns (`GAP-001` Target ambiguity through `GAP-007` Validation gap).

When a request is ambiguous (*"Fix the customer issue"*), Engram enters **Plan Mode**:
```text
UNKNOWN -> DISCOVERING -> PARTIALLY_UNDERSTOOD -> PLAN_MODE 
        -> CLARIFICATION -> UNDERSTOOD -> CHANGE_CONTRACT 
        -> IMPLEMENTING -> VALIDATING -> COMPLETED
```
Engram asks a targeted clarification question to save thousands of exploratory tokens.

---

## 11. Change Contract & Impact Analysis

Before editing, Engram describes the expected change boundary:
```text
Change Contract
Task: Add CSV export for orders
Expected files: 4 | Expected symbols: 8 | Expected tests: 2
Constraints: No breaking API changes
```

After editing, Engram calculates actual impact:
```text
Actual impact: Files: 6 | Symbols: 14 | Modules: 3 | Tests: 4
```
Engram surfaces unexpected dependencies, scope expansion, and architecture violations.

---

## 12. Change Guard

Validates post-implementation state:
1. **Expected vs Actual** diff boundaries
2. **Architecture rules** (e.g., Domain must not touch Infrastructure)
3. **Contracts** (public APIs preserved)
4. **Tests** (expected tests added and passing)
5. **Dependencies** (no unexpected dependencies introduced)

Engram warns and explains rather than blindly blocking the developer.

---

## 13. Project Constitution

Maintained in `.engram/constitution.yaml` (committed to Git):
```yaml
architecture:
  pattern: clean_architecture
rules:
  - domain_must_not_depend_on_infrastructure
  - controllers_must_not_access_database_directly
conventions:
  service_suffix: Service
  repository_suffix: Repository
testing:
  service_changes_require_unit_tests
security:
  authentication_required:
    - /api/*
```

---

## 14. Historical Change Intelligence & Drift

- **Historical Intelligence:** Co-changing files (PaymentService changes with PaymentServiceTests in 72% of commits). Evidence, not hard rules.
- **Project Drift:** Surfaces gradual architectural decay over commits.
- **Knowledge Debt:** Explicitly tracks unresolved dynamic areas, missing docs, and unindexed generated code.

---

## 15. Safe-to-Change Analysis

Calculates risk profiles using concrete counts and categorical confidence—**never fake precision percentages**:

```text
Target:
  PaymentService

Understanding:
  HIGH

Dependency coverage:
  42/45 relevant relationships resolved

Test coverage:
  8 related tests identified

Architecture confidence:
  HIGH

Risks:
  ⚠ Reflection detected
  ⚠ Dynamic configuration dependency

Recommendation:
  Proceed with caution
```

---

## 16. Agent Agnostic via MCP

Engram operates as an advisory Model Context Protocol (MCP) server. Compatible with Cursor, Claude Code, Windsurf, Roo Code, and custom agent harnesses. Engram provides intelligence; the agent executes.

---

## 17. LLM Strategy

The core engineering understanding is **deterministic and local**. LLMs are optional and reserved for:
- ambiguous documentation & business-rule extraction
- complex semantic summaries & clarification questions

A paid external LLM is never required for baseline repository understanding.

---

## 18. Token Efficiency & Correctness Principle

Engram must never optimize token reduction at the expense of correctness:
> **Provide the minimum sufficient context required for correct implementation, while eliminating unnecessary repository discovery.**

Measured by: discovery tokens, tool calls, compilation latency, task success rate, regression rate, and unexpected-impact detection accuracy.

---

## 19. V1 Scope & Implementation Strategy

To de-risk development, prevent polyglot fatigue, and guarantee delivery, V1 follows an explicit **sequenced delivery strategy**.

### 19.1 Language Sequencing (The Golden Spike)

Rather than building 3 compilers concurrently, language support is strictly staged:

```text
Phase 1 (Lead Testbed):     TypeScript / JavaScript
                            ├── Clean AST, Node/NPM ecosystem, universal in fullstack
                            └── Immediate feedback on real agent tasks
                                  ↓
Phase 2 (Dynamic Testbed):  Python
                            ├── Duck typing, decorators (@app.get), dynamic imports
                            └── Validates framework-aware heuristics
                                  ↓
Phase 3 (Enterprise Typed): C#
                            ├── Roslyn / Tree-sitter, DI container registration
                            └── Validates clean architecture boundary enforcement
```

### 19.2 The Six Core V1 Deliverables

1. **CLI Ingestion Engine:** `engram init` with Tree-sitter file indexer and categorized `.engramignore` scanner.
2. **Local Analytical Store:** SQLite (WAL mode) schema implementing the Canonical Knowledge Model with bi-temporal revision spans.
3. **Task Grounding Engine:** Deterministic BM25 lexical tokenizer + symbol identifier search.
4. **Context Compiler:** Progressive Levels 1–4 packer constrained by `token_budget`.
5. **Change Contract & Change Guard:** Git diff parser comparing working tree mutations against the contract boundary.
6. **Local MCP Server:** Exposing the 7 core tools over standard `stdio`.

### 19.3 Explicit Non-Goals for V1 (Deferred)

- **NO** cloud synchronization or distributed team databases (Git stores Constitution; analytical store remains local).
- **NO** custom GUI or web dashboard (CLI and MCP only).
- **NO** runtime bytecode or heap inspection (static analysis and test outputs only).
- **NO** language support beyond TypeScript, Python, and C# in V1.

### 19.4 V1 Acceptance & Exit Criteria

V1 is complete when benchmarked on two real-world open-source repositories against an unassisted baseline agent:
1. **$\ge 60\%$ reduction in discovery tokens and exploratory tool calls** before the first line of code is edited.
2. **$0\%$ false-positive blocking warnings** on valid in-boundary modifications.
3. **$100\%$ detection rate of intentional architectural boundary violations** in synthetic mutation tests.

---

## 20. Zero-Friction Developer Experience

`engram init` auto-detects toolchains, frameworks, test runners, and Git configurations without requiring the developer to configure graph databases or AST pipelines manually.

---

## 21. What We Should NOT Build

Engram is **not**:
- another generic repository graph visualizer
- another code search engine
- another chatbot over a repository
- another generic RAG system
- another autonomous coding agent
- an IDE replacement
- a mandatory workflow

---

## 22. Differentiation & Competitive Positioning

### 22.1 The Engineering Change Loop
Generic context engines only ask: *"What information should the agent see?"*  
Engram asks: *"What was the agent expected to change, what actually changed, was the difference meaningful, and what should the system remember afterward?"*

### 22.2 Blast Radius & Change Boundaries
File count alone is not a violation: a large but justified refactor can be valid; a small 1-line change that bypasses domain security is invalid.

### 22.3 MCP as Distribution
MCP allows Engram to power existing agents (Cursor, Claude Code, Windsurf) without building a proprietary IDE.

### 22.4 Discovery Path Reduction
Engram replaces 20+ iterative exploratory tool calls with an immediate, grounded context payload.

---

## 23. Core Product Statement

> **Engram gives AI coding agents persistent engineering memory and change intelligence for your software system.**  
> **Know what should change. Know what actually changed. Remember why.**

---

## 24. Performance & Cache Architecture

Performance and cache behavior are architectural concerns, not implementation details:

1. **Analyze once, reuse repeatedly.**
2. **Prefer incremental analysis over full repository re-analysis.**
3. **Cache correctness beats cache hit rate:** *"Engram may trade cache hit rate for correctness, but never correctness for cache hit rate."*
4. **Robust MCP Query Boundary:**
   > **Normal MCP queries should use validated cached knowledge whenever possible and must not trigger an unnecessary full repository re-analysis. If required knowledge is stale or missing, Engram should perform the smallest necessary incremental analysis before answering.**
5. **Versioned Content-Addressed Analysis Artifacts:**
   A content hash alone is insufficient. Caches are addressed via a composite key:
   $$\text{AnalysisKey} = \text{hash}(\text{content\_hash} + \text{analyzer\_version} + \text{parser\_version} + \text{language\_version} + \text{analysis\_config})$$
   This guarantees that parser upgrades or rule changes automatically invalidate stale analysis even if file contents remain identical.
6. **Dual Efficiency Targets:**
   - *Computational efficiency:* Fast parsing, minimal CPU overhead.
   - *Agent efficiency:* Minimum discovery tokens, focused context payloads.

---

## 25. Storage Topology: Git vs Local Analytical Store

**Decision: Hybrid repository + local analytical store.**

```text
repository/
├── .engram/
│   ├── constitution.yaml    <-- Committed to Git (Team Intent)
│   ├── config.yaml          <-- Committed to Git
│   └── ignore               <-- Committed to Git
└── source code

local machine:
~/.engram/cache/<project-id>/ <-- Local machine only (.gitignored)
    ├── derived_symbols.db    <-- Local analytical store (SQLite as V1 candidate)
    └── ast_cache/
```
* **Git stores engineering intent and team rules.**
* **Local machine stores derived, rebuildable engineering knowledge.**  
* **Architecture-driven storage:** SQLite is the current V1 implementation choice, subject to Phase 0 model validation. The data model drives storage, not vice versa.

---

## 26. Secrets & Sensitive Data

Default exclusions (`.env`, `*.pem`, `*.key`, `node_modules/`, `build/`).  
Deterministic regex scanning redacts secrets (tokens, connection strings, JWTs). Engram stores only `[REDACTED]` metadata and never transmits sensitive values to LLMs.

---

## 27. Task Grounding & Intent Resolution

Hybrid deterministic grounding:
```text
User task -> Lexical token extraction -> Symbol & Path matching 
          -> Framework metadata -> Dependency traversal 
          -> Candidate scoring -> Minimum sufficient context
```
Optional local embeddings or small models handle ambiguous semantic mappings without dumping the repository.

---

## 28. Grounding Confidence & Risk Model

Decisions combine grounding confidence, evidence coverage, and task risk:
- **High Confidence + Low/Med Risk:** Generate Change Contract.
- **Medium Confidence:** Retrieve progressive Level 3 context.
- **Low Confidence OR High Risk with Unresolved Dependencies:** Enter Plan Mode / Clarification.

---

## 29. Git Lifecycle, Branching & Dirty Trees

- **Branch switching:** Revision-aware derived state; incremental invalidation using Git commit deltas rather than full re-indexing.
- **Syntactically broken code:** Error-tolerant parsing (Tree-sitter); partial ASTs retain known symbols while marking broken regions with reduced confidence.
- **Rollbacks (`git reset --hard`):** Provisional memory transactions are discarded immediately upon Git revision regression.

---

## 30. Dependency Injection & Dynamic Languages

- **DI Resolution:** Static analysis records evidence-backed potential bindings (`IOrderService` bound to `OrderService`). Ambiguities expose confidence scores rather than false certainty.
- **Python / Dynamic:** Multi-layered evidence (AST $\to$ Framework decorators $\to$ Type hints $\to$ Runtime traces $\to$ `UNKNOWN/DYNAMIC` uncertainty flags).

---

## 31. Memory Poisoning & Learning Hygiene

**Code that exists is not automatically a convention.**

Five Evidence Tiers:
- **Tier 1: Explicit** (Developer/team-authored rule in Constitution)
- **Tier 2: Verified** (Repeated, structurally confirmed project pattern)
- **Tier 3: Historical** (Observed frequently in accepted commits)
- **Tier 4: Inferred** (Heuristic pattern based on current code)
- **Tier 5: Provisional** (Observed in a recent/unvalidated change)

An agent introducing hardcoded SQL once is recorded as an *Observed Behavior*, never as a *Project Convention*.

---

## 32. Memory Promotion Lifecycle & Pruning

Engram updates its working model continuously, but strictly gates trusted memory promotion:

```text
Observed Working Code
        ↓
Provisional State
        ↓
Validated / Accepted Change
        ↓
Promoted Engineering Memory
```

- **Architecture rules:** Retained until changed.
- **Current dependencies:** Invalidated when source changes.
- **Historical patterns:** Timestamped; **relevance decays over time** while historical truth is preserved.

---

## 33. MCP Tool Surface

V1 advisory tools:
- `engram_get_context(task, token_budget)`
- `engram_analyze_task(task)`
- `engram_create_change_contract(task_id)`
- `engram_get_impact(task_id)`
- `engram_validate_change(task_id)`
- `engram_record_decision(decision, reason)`
- `engram_update_memory(task_id)`

---

## 34. Developer Overrides & Monorepos

- **Overrides:** Explicit audit logging with developer rationale.
- **Monorepos:** Root-level initialization (`engram init`) or scoped package initialization (`engram init ./packages/orders`).
- **Classified Ignores (`.engramignore`):** Distinguishes `SOURCE`, `GENERATED` (non-editable but relationship-relevant), `VENDORED`, `BUILD`, and `SECRET`.

---

## 35. Verification & Benchmarking Framework

Three benchmark suites:
1. **Repository Understanding:** Recall/precision of target symbols, dependencies, and test mappings.
2. **Task Grounding:** Discovery tokens, total tokens, tool calls, and time-to-context on realistic tasks.
3. **Change Safety:** Controlled synthetic mutations (architecture violations, unexpected dependencies) testing true/false positive detection rates.

---

## 36. Operational Edge Cases

### 36.1 Interleaved Human + Agent Editing
If a developer manually fixes a file while an agent is running under an active Change Contract:
- The human change is classified as `AUTHORITATIVE_HUMAN_INTERVENTION`.
- Engram reconciles the contract delta without discarding the agent's valid provisional work.

### 36.2 Context Budget Parameter (`token_budget`)
`engram_get_context` accepts a `token_budget` constraint:
- Optimizes context utility per token cost.
- Stops at Level 2 (signatures) if sufficient, rather than stuffing Level 3 source code needlessly.

### 36.3 Symbol Identity & Refactoring Continuity
Entities have stable internal IDs (`entity_id`) backed by structural AST fingerprints and Git rename detection. Renaming `OrderManager` $\to$ `OrderService` preserves historical metrics and test relationships.

---

## 37. The Core Design Triad

> **Engram never confuses "observed in the codebase" with "recommended by the codebase."**

```text
OBSERVED: What exists.
INFERRED: What Engram calculates is likely true.
PRESCRIBED: What the project explicitly mandates (Constitution).
```

---

## 38. Build Order (Phases 0 – 7)

- **Phase 0: Formal Model** (Entity schema, relationships, evidence, lifecycle)
- **Phase 1: Core Analyzer** (Discovery, Tree-sitter extraction, dependencies)
- **Phase 2: Engineering Memory** (Analytical store persistence & query engine)
- **Phase 3: Context Compiler** (Task grounding, progressive budget packing)
- **Phase 4: Gap Detection & Plan Mode** (Target clarification questions)
- **Phase 5: Change Contract & Impact** (Expected boundary definition & diff analysis)
- **Phase 6: Agent Integration** (MCP server implementation)
- **Phase 7: Change Guard & Memory Update** (Validation, reconciliation, promotion)

---

## 39. Phase 0 Requirements (The 12 Model Rules)

Before writing parser code, the data model schema must explicitly define:
1. How stable entity IDs are generated and maintained.
2. How identity survives renames and file moves via AST fingerprints.
3. How repository revisions and bi-temporal spans are represented.
4. How Change Contracts are versioned.
5. How agent and human changes are distinguished (`ChangeOrigin`).
6. How provisional changes are isolated from permanent memory.
7. How human interventions reconcile an active contract.
8. How context budgets constrain context compilation.
9. How insufficient context and gaps are represented.
10. How accepted vs rejected changes affect memory promotion.
11. How branch transitions invalidate or reuse knowledge via `AnalysisKey`.
12. How evidence tiers, decay formulas, and conflict resolution attach to all entities and relationships.

---

## 40. The Compounding Memory Flywheel

```text
Persistent engineering memory
            ↓
Better task grounding
            ↓
Less repository rediscovery
            ↓
Less unnecessary context / fewer tokens
            ↓
Better implementation focus
            ↓
Change Contract
            ↓
Expected vs actual validation
            ↓
Fewer meaningful errors / regressions
            ↓
Accepted knowledge returned to memory
            └───────────────→ next task
```

---

## Summary Statement

> **Engram is not the developer's boss. It is the developer's engineering memory.**  
> The developer decides. The AI agent executes. Engram provides the understanding, context, evidence, and safety intelligence between them.
