# Changelog

All notable changes to **AMstraLift** are documented in this file.
This project follows [Semantic Versioning](https://semver.org/).

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
