# 🗺️ AMstraLift: Capabilities & Strategic Roadmap

> **Automated dependency and framework upgrade engine with a cryptographically bound trust boundary.**  
> *Architected & Engineered by **Amal P S** | Licensed under GNU AGPL-3.0*

---

## 📌 Executive Summary

**AMstraLift** is an enterprise-grade automated modernization engine that safely transitions aging software codebases to modern LTS frameworks and remediates security vulnerabilities (CVEs). 

Unlike conventional tools that blindly bump version strings and produce broken builds, AMstraLift enforces **deterministic AST code healing**, verifies changes inside an isolated **5-Gate Sandbox**, cryptographically seals change bundles with **HMAC-SHA256 signatures**, and delivers compilable, test-verified Pull Requests.

---

## 🚀 What We Have Implemented So Far (v0.1.0 – v0.1.17)

### 1. Multi-Ecosystem Architecture & Framework Adapters
AMstraLift provides tailored modernization adapters for the industry's four most common enterprise stacks:

* **Angular Adapter (`AngularAdapter`)**:
  - Full migration paths spanning **Angular v12 $\to$ v17+**.
  - **Automated AST Standalone Migration**: Transforms legacy `NgModule`-based components into standalone components with resolved imports.
  - **Modern Control Flow Healing**: AST transformation replacing legacy structural directives (`*ngIf`, `*ngFor`, `*ngSwitch`) with modern template syntax (`@if`, `@for`, `@switch`).
  - **Material & Sass Theming Healing**: Automatically migrates obsolete `@angular/material` Sass imports and M2 theme mixins.
  - **Multi-Project Library Build Ordering**: Analyzes inter-project dependency graphs in `angular.json` to ensure libraries compile before applications.
  - **Rollup Declaration Purge**: Eliminates internal declaration chunk imports (`.d-*`) that break builds during bundling.

* **React Adapter (`ReactAdapter`)**:
  - Modernization across **React 17, 18, and 19**.
  - Package alignment across React DOM, React Router, and common component libraries.
  - Lockfile reconciliation for both `npm` (`package-lock.json` v2/v3) and `yarn`.

* **.NET Adapter (`DotNetAdapter`)**:
  - Migration across **.NET 6 $\to$ .NET 8 / 9 LTS**.
  - **Central Package Management (CPM)**: Automated migration from scattered `.csproj` package references to `Directory.Packages.props`.
  - Automated `TargetFramework` updates, `global.json` SDK pinning, and `nuget.config` source management.

* **Python Adapter (`PythonAdapter`)**:
  - Multi-version runtime transitions across **Python 3.8 $\to$ 3.12+**.
  - Support across `pyproject.toml`, `requirements.txt`, Poetry, and Pipenv.
  - Safe constraint generation and transitive dependency pinning.

---

### 2. 5-Gate Sandboxed Verification Engine
Every proposed upgrade or security remediation is subjected to a zero-trust, 5-gate sandbox:

```text
Gate 1: Clean Install  ──>  Gate 2: Build  ──>  Gate 3: Test Suite  ──>  Gate 4: Post-Rescan  ──>  Gate 5: Manifest Diff
    (Zero artifacts)          (Compile check)      (Regression run)      (No new CVEs)          (Signed trust)
```

- **Strict Fail-Fast Execution**: If any compilation or test step fails, the operation immediately halts without polluting the target repository.
- **Isolated Workspaces**: Changes are tested in temporary worktrees and ephemeral sandboxes, keeping developer branches completely untouched until verified.

---

### 3. Cryptographic Trust Boundary (Stage A & Stage B)
- **Separation of Concerns**:
  - **Stage A (Analysis & Generation)**: Operates in read-mostly mode, detecting vulnerabilities, computing upgrade graphs, and generating change manifests.
  - **Stage B (Execution & Publication)**: Operates inside the verification boundary, validating cryptographic signatures before touching code or creating Git branches.
- **HMAC-SHA256 Signed Bundles**: Every patch bundle contains a cryptographic signature, nonces, and a strict Time-To-Live (TTL) expiration to prevent replay attacks or tampering.

---

### 4. Zero-Cost Live OSV.dev Vulnerability Engine
- Integrated directly with Google's **OSV.dev** distributed vulnerability database.
- Queries npm, NuGet, and PyPI ecosystems in real time.
- **Zero API Keys & Zero Subscriptions**: Does not depend on proprietary scanners (e.g., Snyk, SonarQube) or costly API tokens.
- Calculates deterministic minimum safe target versions that clear all known CVEs while maintaining semver compatibility.

---

### 5. Polyglot Monorepo Topology Detection (v0.1.17)
- **Recursive Monorepo Discovery**: Recursively detects mixed-language codebases (e.g., Angular frontend + .NET WebAPI backend + Python ML service under a single root).
- **Topology Mapping**: Identifies project boundaries, subproject types, package manifests, and relative paths.
- **Targeted & Coordinated Modernization**: Enables upgrading the entire monorepo holistically or targeting specific subprojects independently.

---

### 6. Interactive Desktop GUI & Cross-Platform CLI
- **Modern High-Contrast UI**: Engineered with custom dark and light themes, segmented navigation, and responsive card layouts.
- **Live Streamed Output**: ANSI-stripped live output console with progress tags and step indicators.
- **Monorepo Project Selector**: Interactive dropdown and multi-select cards for picking individual subprojects in complex workspaces.
- **Official Branding & Clean OS Integration**:
  - Multi-resolution squircle app icon (`16px` to `256px` + `.ico`).
  - **Tkinter Feather Removal**: Eliminates default Tk feather across the main window, all modal dialogs, and error messageboxes.
- **One-Click Cancellation**: Clean cancellation tokens that abort sandboxed background processes without leaving orphan processes or locked files.

---

### 7. CI/CD Release Pipeline & Standalone Distribution
- **Single Standalone Executable**: Pre-compiled Windows PE binary (`dist/amstralift.exe`, ~31.9 MB) bundling the GUI and CLI with zero Python or Node installation requirements.
- **Automated GitHub Actions CI/CD**: Pushing a `v*` tag triggers automated testing, PyInstaller packaging on `windows-latest`, and GitHub Release asset publishing.
- **Test Coverage**: **268 automated unit, adapter, integration, and security tests** running with 100% pass rate.
- **License**: Released under **GNU Affero General Public License v3.0 (AGPL-3.0)** with Section 7 author attribution.

---

## 🎯 Strategic Roadmap: Next Goals & Milestones

The future roadmap is split into **Engineering Depth & Consistency** (Milestones 1–3) and **Ecosystem Expansion** (Milestones 4–5).

```text
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│     Milestone 1: Consistency    │ ──>  │    Milestone 2: Environment     │
│  React 18/19, .NET & Python AST │      │ Dockerfile & CI/CD Workflow Sync│
└─────────────────────────────────┘      └─────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│     Milestone 3: Reporting      │ ──>  │     Milestone 4: Expansion      │
│ Executive HTML/PDF Audit Report │      │  Java / Spring Boot 2 ➔ 3 Engine│
└─────────────────────────────────┘      └─────────────────────────────────┘
```

---

### 🏁 Milestone 1: Deep AST Codemod Parity Across All Stacks
*Bring the same level of sophisticated AST code-healing currently present in Angular to React, .NET, and Python.*

- [ ] **React AST Codemods**:
  - Auto-migrate `ReactDOM.render(...)` $\to$ `createRoot(...)` for React 18/19.
  - Auto-migrate obsolete React Router v5 hooks (`useHistory` $\to$ `useNavigate`).
  - Flag or codemod deprecated lifecycle methods (`componentWillMount`, `componentWillReceiveProps`).
- [ ] **.NET AST Codemods**:
  - Automated transition from legacy `Startup.cs` / `ConfigureServices` $\to$ modern ASP.NET Core minimal hosting (`Program.cs`).
  - Auto-replace deprecated namespace and API usages flagged by .NET 8/9 analyzers.
- [ ] **Python AST Codemods**:
  - Auto-migrate `Pydantic v1` (`@validator`, `.dict()`, `.parse_obj()`) $\to$ `Pydantic v2` (`@field_validator`, `.model_dump()`, `.model_validate()`).
  - Auto-codemod `asyncio.get_event_loop()` $\to$ `asyncio.get_running_loop()`.

---

### 🐳 Milestone 2: Container & CI/CD Runtime Synchronization
*Ensure that modernized codebases do not break external CI/CD pipelines and deployment containers.*

- [ ] **Dockerfile Auto-Healing**:
  - Parse base image tags (e.g. `FROM node:16-alpine`, `FROM mcr.microsoft.com/dotnet/sdk:6.0`).
  - Automatically bump base images to match the modernized target framework (e.g. `node:20-alpine`, `dotnet/sdk:8.0`).
- [ ] **GitHub Actions & CI Pipeline Sync**:
  - Auto-detect workflow files in `.github/workflows/*.yml`, Jenkinsfiles, and Azure Pipelines (`azure-pipelines.yml`).
  - Update runner matrix variables (`node-version: [16]` $\to$ `[20]`, `dotnet-version: '6.0.x'` $\to$ `'8.0.x'`).
- [ ] **Docker Compose Harmonization**:
  - Validate and sync local development service images in `docker-compose.yml`.

---

### 📊 Milestone 3: Executive Compliance & Modernization Audit Report
*Provide clear, executive-ready documentation proving security improvements and engineering ROI.*

- [ ] **Self-Contained HTML/PDF Report Generation (`amstralift report`)**:
  - **Security Delta**: Visual bar/donut charts showing CVEs before and after (Critical, High, Medium, Low $\to$ 0).
  - **LTS Support Horizon**: Projected support end-dates (e.g., *"Extended vendor support through 2028"*).
  - **Engineering ROI Metrics**: Estimated developer hours saved based on AST transformations and package reconciliations.
  - **Cryptographic Attestation Badge**: Verified HMAC-SHA256 signature block proving code passed all 5 sandbox gates.

---

### ☕ Milestone 4: Enterprise Language Expansion (Java & Spring Boot)
*Tackle the #1 enterprise legacy migration challenge in financial and corporate infrastructure.*

- [ ] **Java / Maven / Gradle Adapter (`JavaAdapter`)**:
  - Discovery of `pom.xml` and `build.gradle` / `build.gradle.kts`.
  - Target runtime upgrade from **Java 8 / Java 11 $\to$ Java 17 / 21 LTS**.
- [ ] **Spring Boot 2.x $\to$ 3.x Migration Engine**:
  - **Jakarta EE Namespace Migration**: Deterministic AST refactoring of `javax.*` packages $\to$ `jakarta.*` (`javax.servlet`, `javax.persistence`, `javax.annotation`, etc.).
  - Dependency management reconciliation in Maven parent POMs and Gradle dependency blocks.
  - Automated sandbox verification via `./mvnw verify` or `./gradlew check`.

---

### 🖥️ Milestone 5: GUI Visual Diff & Snapshot Safety
*Empower developers with complete transparency before any file is touched.*

- [ ] **Side-by-Side Visual Diff Viewer**:
  - Embedded syntax-highlighted diff viewer in the GUI showing exact file changes generated in Stage A before running Stage B.
- [ ] **One-Click Rollback & Snapshot Manager**:
  - Automatic Git stash or snapshot checkpointing before applying migrations.
  - Single-click restore button if a developer decides to discard applied modifications.

---

## 📈 Release Matrix & Version History

| Version | Status | Key Innovations |
|---|---|---|
| **v0.1.0 – v0.1.9** | Released | Core architecture, Stage A/B crypto separation, initial Angular & .NET adapters. |
| **v0.1.10 – v0.1.14** | Released | TS5101 / NG2010 healing, Rollup `.d-*` purge, console suppression, `importProvidersFrom` fixes. |
| **v0.1.15** | Released | Multi-project library build ordering and initial branding assets. |
| **v0.1.16** | Released | Sandboxed multi-project verification, modal icon parenting, and high-DPI icons. |
| **v0.1.17** | **Current** | Polyglot monorepo scanner, GUI project picker, GNU AGPL-3.0 license, official squircle icons. |
| **v0.1.18** | *Planned* | React 18/19 & Python Pydantic v2 AST codemods, Dockerfile runtime synchronization. |
| **v0.1.19** | *Planned* | Executive HTML audit report generator and GUI side-by-side visual diff previewer. |
| **v0.2.0** | *Future* | Java / Spring Boot 2 $\to$ 3 + Jakarta EE migration engine. |

---

*AMstraLift is developed with a strict commitment to determinism, developer trust, and zero-hallucination code modernization.*
