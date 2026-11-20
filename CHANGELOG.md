# Changelog

All notable changes to **AMstraLift** are documented in this file.
This project follows [Semantic Versioning](https://semver.org/).

---

## [0.3.0] - 2026-11-20

### Added
- **P21 — Multi-Repository Modernization (`amstralift.multirepo`)**:
  - Enterprise multi-repository fleet discovery and dependency analysis engine (`FleetScanner`) supporting polyglot fleets containing Angular, React, .NET, and Python repositories.
  - Automatic extraction and cross-referencing of package exports (npm `package.json`, NuGet `.csproj` PackageIds/assemblies, Python `pyproject.toml` project names) and consumed imports across repositories.
  - Cross-repository Directed Acyclic Graph engine (`FleetDependencyGraph`) modeling directed `Provider -> Consumer` edges, ensuring shared libraries are upgraded and tested before dependent downstream services.
  - Strict cycle detection (`InterRepoCycleError`) identifying circular cross-repository dependencies using DFS cycle back-edge analysis.
  - Topological multi-repo execution wave engine (`compute_execution_waves`) partitioning large fleets into staged waves (Wave 1: leaf providers/shared libraries; Wave 2+: dependent consumer services).
  - Multi-repository modernization planning engine (`FleetPlanner`) synthesizing declarative modernization plans with cross-repo prerequisite matrices, execution concurrency metrics, and JSON persistence.
  - Declarative fleet manifest parser supporting `fleet.yaml` / `amstralift-fleet.yaml` specifications for explicitly tagged enterprise fleet definitions.
- **CLI Multi-Repository Fleet Engine (`amstralift fleet` / `amstralift multirepo`)**:
  - `amstralift fleet scan`: Discovers and displays interactive Rich table summaries of all repositories, ecosystems, framework versions, published package exports, and internal consumer/provider counts.
  - `amstralift fleet graph`: Validates DAG acyclicity and renders cross-repository dependency tables linking upstream providers to downstream consumers.
  - `amstralift fleet plan`: Generates coordinated multi-repo modernization campaign plans with execution waves, concurrency metrics, and dependency prerequisite matrices.
  - Full `--json` serialization across all fleet commands for headless CI/CD pipeline automation.

---

## [0.2.0] - 2026-11-20

### Added
- **P16 — Transformation Dependency Graph (`amstralift.transforms`)**:
  - Foundational Directed Acyclic Graph (DAG) architecture for AMstraLift Generation 2, modeling prerequisites, ordering constraints, mutual conflicts, precondition invalidations, and rollback boundaries across modernizations.
  - Multi-ecosystem transformation catalog covering Angular (AST quartet, workspace schema, ecosystem alignment, Material styling, ESLint), .NET (LTS framework target, Central Package Management, nullable reference types, code formatting), Python (PEP 621 declarative pyproject.toml, Ruff, PEP 585/604 modern typing), and React (JSX runtime, hooks strict mode).
  - Deterministic Kahn-based topological sort with reproducible tie-breaking to eliminate implicit ordering vulnerabilities.
  - Sequential execution wave generator (`get_execution_phases`) grouping independent dependencies into staged waves.
  - Concurrent parallel batch partitioner (`get_parallel_batches`) detecting disjoint vs overlapping file patterns to safely schedule concurrent transformation workers without race conditions.
  - Atomic transaction rollback boundary engine (`get_rollback_boundaries`) calculating blast radius across atomic boundary groups (e.g. `angular:ast-quartet`, `dotnet:cpm-boundary`) and downstream transitive dependents.
- **CLI Transformation Graph Inspector (`amstralift graph`)**:
  - `amstralift graph`: Interactive Rich table overview of all transformations, ecosystems, categories, risks, prerequisites, and atomic transaction boundaries.
  - `amstralift graph --phases`: Displays sequential execution waves.
  - `amstralift graph --batches`: Displays parallel-safe execution batches marked `[CONCURRENT]` or `[SEQUENTIAL]` with file pattern annotations.
  - `amstralift graph --rollback <id>`: Computes and displays the atomic rollback blast radius for a given transformation.
  - `amstralift graph --ecosystem <name>`: Filters transformations by ecosystem (`angular`, `dotnet`, `python`, `react`).
  - `amstralift graph --json`: Clean JSON serialization for automated modernization planning pipelines (P17).

- **P17 — Modernization Planning Engine (`amstralift.planning`)**:
  - Declarative modernization plan generator (`ModernizationPlan`) shifting AMstraLift from a reactive upgrade engine into a proactive modernization planner.
  - Deep repository inspection (`RepoAnalyzer`) discovering framework versions, structural directives (`*ngIf`, `*ngFor`), legacy NgModules, constructor dependency injection, empty template tags, Central Package Management, and setup.py configurations.
  - Risk classification dividing transformations into **Safe** (low-risk, idempotent), **Dependent** (prerequisite-chained), **Risky** (major architectural shifts), and **Refused** (conflicting, invalidated, or out-of-bounds).
  - Canonical 6-phase lifecycle: Dependencies & Package Alignment $\to$ Framework & Architecture Migration $\to$ Template & AST Modernization $\to$ Tooling & Compiler Configuration $\to$ Hygiene & Deprecated Artifact Pruning $\to$ Sandbox Verification & Attestation.
  - Lossless YAML and JSON serialization (`to_yaml`, `from_yaml`, `save`, `load`).
  - Staged plan execution engine (`PlanExecutor`) supporting progressive live execution, simulation dry-runs, and transaction rollback boundaries.
- **CLI Modernization Plan Generator (`amstralift plan`)**:
  - `amstralift plan`: Analyzes the workspace and renders a formatted Rich overview of all planned lifecycle phases, safe/dependent/risky breakdowns, parallel batches, and refused transformations with explicit refusal rationale.
  - `amstralift plan --target <version>`: Explicitly specifies target framework major version (e.g. `--target 18` or `--target 21`).
  - `amstralift plan -o modernization-plan.yaml`: Exports the declarative plan to file for enterprise policy review.
  - `amstralift plan --dry-run`: Simulates phase execution without touching workspace files.
  - `amstralift plan --yaml` / `--json`: Prints raw declarative plan definitions.

- **P18 — Pre-Execution Cross-Transformation Conflict Detection (`amstralift.conflicts`)**:
  - Pre-execution conflict detection engine (`PreExecutionConflictDetector`) analyzing candidates, DAG dependency constraints, file boundaries, AST symbol surfaces, and configuration options before workspace files are touched.
  - Conflict categorization modeling `EXPLICIT_CONFLICT` (mutual exclusivity edges in DAG), `FILE_COLLISION` (concurrent mutations of shared file patterns), `SYMBOL_COLLISION` (pruning or refactoring symbols required by downstream rules, e.g. `@NgModule`), `CONFIG_KEY_COLLISION` (contradictory builder/compiler targets), and `PRECONDITION_INVALIDATION` (invalidated rule preconditions).
  - Deterministic conflict resolution engine (`ConflictResolver`) automatically pruning superseded legacy rules and injecting deterministic category-prioritized ordering edges to eliminate workspace file collisions.
  - CLI conflict inspection command (`amstralift conflicts` with `--repo`, `--ecosystem`, `--resolve/--no-resolve`, `--json`).
  - Native integration into `amstralift plan`, embedding complete pre-execution conflict analysis reports directly into `plan.metadata` and displaying conflict resolution summaries.

- **P19 — Resumable / Checkpointed Modernization (`amstralift.checkpoints`)**:
  - Resumable checkpointing engine (`CheckpointStore` and `CheckpointManager`) automatically checkpointing progress between phases to eliminate redundant work during long-running modernizations.
  - Phase-level tracking recording execution IDs, plan metadata, starting/current git commit hashes, completed transformation IDs, and duration timestamps.
  - Cryptographic evidence integrity hashes (SHA-256) preventing corrupted or tampered checkpoint files from resuming execution.
  - Automatic resumption planner (`prepare_resumption`) dynamically pruning already-completed phases and AST transformations to resume execution at the exact interrupted point.
  - Dedicated CLI commands (`amstralift resume [id]` and `amstralift checkpoints` with `--repo` and `--json`).

- **P20 — Policy-as-Code (`amstralift.policy`)**:
  - Declarative enterprise modernization policy engine (`EnterprisePolicy`, `PolicyEvaluator`) supporting YAML policy specifications (`policy.yaml`).
  - Multi-tier rule enforcement covering governance (major jump limits, runtime deprecation, EOL horizon), AST risk tolerance (blocking critical/high risks, blacklisting rules, enforcing mandatory rules), security thresholds (critical/high CVE blocking), and semantic contracts (public API symbol removal thresholds).
  - Deterministic evaluation reports with `ALLOW`, `REVIEW`, and `BLOCK` verdicts.
  - Native integration into `amstralift plan` (`--policy <file.yaml>`) and execution safeguards halting runs upon policy violations.
  - Dedicated CLI commands: `amstralift policy init` (scaffolds enterprise `policy.yaml`) and `amstralift policy check` (evaluates workspace against policy with formatted panels and `--json` support).

---

## [0.1.19] - 2026-11-20

### Added
- **Automated Self-Healing & System Diagnostics (`amstralift doctor`)**:
  - Comprehensive health check covering core tools (Git SCM, Python 3.10+, Node.js/npm, .NET SDK, Docker), network registries (OSV.dev, npm, PyPI), and storage.
  - Automated self-repair mode (`amstralift doctor --fix` / `--heal`): restores missing config directories, repairs corrupted configuration JSON with automatic `.corrupted_bak` backups, and purges orphaned Stage A temporary sandboxes from system temp.
- **Automated Self-Update Engine (`amstralift self-update` / `amstralift update`)**:
  - Live version synchronization checking PyPI and GitHub Releases with release note display (`--check`).
  - In-place self-updating for Python packages and atomic Windows binary rotation (`amstralift_updater.bat`) with SHA-256 download checksum verification for standalone executables.
- **Desktop GUI Self-Healing & Update Pill**:
  - Silent, non-blocking background scanner on application startup (1.2s delay) that checks release registries and environment health without interrupting user workflows.
  - High-visibility dynamic header pill (`✨ Update to vX.Y.Z` or `🩺 Self-Heal Ready`) offering 1-click automated update and repair.
  - Dedicated `🩺 Doctor` button in Quick Actions toolbar streaming live health diagnostics to the terminal panel.
- **Angular Self-Closing Tags Modernization (`--modernize self-closing` / `--modernize all`)**:
  - Deterministic AST transformer converting void/empty custom component tags (`<app-card></app-card>` $\to$ `<app-card />`) across both `.html` files and inline TypeScript templates while strictly preserving standard HTML containers.
- **Security & Attestation Step 3**:
  - `--ignore-scripts / --no-ignore-scripts` flag preventing execution of untrusted package lifecycle scripts (`postinstall`, `preinstall`) during Stage A dependency installation.
  - Frozen exact dependency snapshot engine (`resolved_versions.py`) capturing exact versions across manifests/lockfiles and cryptographically binding all resolved dependencies into the HMAC-SHA256 evidence bundle.
- **Self-Healing Compiler & Runtime Configs**:
  - Automatic `tsconfig.json` patches injecting `"skipLibCheck": true` during major upgrades to prevent third-party typing mismatches from failing build gates.
  - Prunes obsolete TypeScript 5.5+ compiler options (`importsNotUsedAsValues`, `preserveValueImports`, `suppressImplicitAnyIndexErrors`, `baseUrl: "."`).
- **REST API Endpoints**:
  - Added `/api/v1/doctor`, `/api/v1/doctor/heal`, and `/api/v1/update/check`.

### Fixed
- **Scaffold Git Worktree Bleed**:
  - Replaced recursive `rev-parse --is-inside-work-tree` with strict local directory `.git` check in `scaffold_starter_project`, ensuring nested scaffolds initialize their own self-contained git repository.
- **Relative Path Resolution in Stage A Workspace**:
  - Resolved `source_repo_path` and `target_workspace_path` to absolute paths before local git cloning on Windows.
- **Tkinter Variable Cross-Thread Access in Python 3.14**:
  - Ensured all UI variables (`StringVar.get()`, `BooleanVar.get()`) are captured on the main UI thread prior to spawning background worker threads, preventing `RuntimeError: main thread is not in main loop`.

---

## [0.1.18] - 2026-10-14

### Added
- **Starter Project Scaffolding Engine (`amstralift init`)**:
  - Automatically initializes Git repositories and scaffolds starter templates across Angular 15, React 17, .NET 6.0, and Python.
  - Interactive GUI modal prompt when opening empty or uninitialized project directories.
- **Deep Monorepo Discovery**:
  - Increased default directory recursion depth to 5 levels to detect deeply nested client apps in enterprise solutions (e.g. `src/Company.App/WebClientApp`).
  - Auto-selects single discovered subproject when no project manifest exists at root.
- **Unit Tests**:
  - Added `tests/test_scaffold.py` covering repository status inspection and multi-ecosystem template scaffolding.

### Fixed
- **Deadlock in npm Lockfile Reader**:
  - Replaced blocking pipes with daemon reader threads in `_run_with_heartbeat` to prevent Windows OS pipe buffer deadlocks during heavy enterprise `npm install`.
  - Added npm performance flags (`--no-progress`, `--prefer-offline`, `--fetch-retries=2`) and reduced lockfile regeneration timeout to 90s with instant fallback to surgical JSON patching.
- **Control Flow Syntax Corrections**:
  - Fixed `@for` syntax generator in `angular_control_flow.py` to strip the deprecated `let` keyword and properly format custom `trackBy` function calls as `track fnName($index, item)`.

---

## [0.1.17] - 2026-10-06

### Added
- **Phase P5 Gate 6 Semantic & Contract Diff Engine**:
  - Validates preservation of exported Angular routes, public TypeScript symbols, C# namespaces, and Python functions.
  - Generates machine-readable `semantic_diff` records in the attested evidence bundle.
- **Transactional Refusal Architecture (P4)**:
  - First-class support for `PARTIALLY_MODERNIZED` status where unsafe transformations are cleanly refused while safe transformations are preserved.

---

## [0.1.16] - 2026-10-06

### Added
- **HMAC Attestation & Trust Boundary (P3)**:
  - Constant-time HMAC-SHA256 signature verification over Stage A transformation and gate evidence bundles.
  - Built-in tamper detection rejecting modified evidence files or spoofed record IDs.

---

## [0.1.15] - 2026-10-06

### Added
- **Desktop Graphical User Interface**:
  - High-contrast, responsive desktop UI with dark and light themes.
  - Live console stream redirection and one-click task cancellation tokens.
- **REST API (`amstralift serve`)**:
  - FastAPI server with interactive Swagger UI (`/docs`) and ReDoc endpoints for enterprise orchestration.

---

## [0.1.0] - 2026-09-28

### Added
- Initial release of AMstraLift.
- Two-stage execution architecture (Stage A Sandbox $\to$ Stage B PR Publisher).
- Angular, React, .NET, and Python adapters.
- 6-gate sandbox verification pipeline.
- OSV.dev automated dependency vulnerability remediation.
