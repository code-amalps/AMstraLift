# Changelog

All notable changes to **AMstraLift** are documented in this file.
This project follows [Semantic Versioning](https://semver.org/).

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
