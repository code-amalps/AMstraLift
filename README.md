# 🚀 AMstraLift

> **Automated dependency and framework upgrade engine with a cryptographically bound trust boundary.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Built with Python](https://img.shields.io/badge/Built_with-Python_3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()
[![Tests](https://img.shields.io/badge/Tests-74%20Passed-brightgreen.svg)]()
[![Release](https://img.shields.io/badge/Release-v0.1.16-informational.svg)](https://github.com/code-amalps/AMstraLift/releases)
[![Architect](https://img.shields.io/badge/Architect-Amal%20P%20S-black.svg)](https://github.com/code-amalps)

AMstraLift is an enterprise-grade automated modernization engine that safely upgrades frameworks and remediates security vulnerabilities (CVEs) across **Angular, React, .NET, and Python** codebases.

Unlike tools that blindly bump version strings and leave you with broken builds, AMstraLift performs **automated AST code healing**, executes a **strict 5-gate sandboxed verification**, cryptographically signs every change bundle, and delivers a clean, compiling pull request. **Zero AI in execution. No auto-merge, ever.**

---

## 🧸 Explain Like I'm 5 (What is AMstraLift?)

Imagine hiring an assistant to remodel your kitchen:

1. **Without AMstraLift (The Dependabot / Renovate experience):**  
   The worker replaces your 110V appliances with 240V industrial models, changes the water pipes without checking the wall diameter, triggers a fuse blow, floods the basement, and leaves 20 separate sticky notes on your fridge marked *"Broken — please fix."* You spend your entire weekend manually repairing the damage.

2. **With AMstraLift:**  
   AMstraLift builds a private temporary replica of your kitchen in a workshop. It tests the voltage, rewires the connections, auto-converts outdated wiring to modern electrical standards, tests the appliances under full load, verifies that no new fire hazards exist, cryptographically signs the inspection certificate, and only then brings the completed, working installation to your home.

---

## ⚡ Key Highlights

* 🛡️ **Zero-Hallucination Determinism:** Uses deterministic AST parsers and syntax codemods—not probabilistic LLM guesses that introduce subtle bugs.
* 🧪 **5-Gate Sandbox Verification:** Every change must pass 5 sequential gates inside an isolated sandbox: Clean Install $\to$ Project Build $\to$ Test Suite $\to$ Post-Upgrade Rescan $\to$ Manifest Diff.
* 📦 **Single Standalone Executable:** Comes with a compiled Windows application (`dist/amstralift.exe`) bundling both the interactive GUI and CLI—zero Python or Node runtime installation required.
* 🌐 **Modern Framework Code Healing:**
  - **Angular (v12 $\to$ v17+):** Automated standalone component refactoring, modern control flow (`@if`, `@for`, `@switch`), M2 Sass theming healing, and multi-project library build ordering.
  - **.NET:** Central Package Management (CPM) migration, TargetFramework updates, and direct package pin overrides.
  - **Python:** Runtime boundaries, dependency tiering, and pip constraints.
* 🔒 **Zero-Cost Live Vulnerability Discovery:** Direct integration with Google's OSV.dev database across npm, NuGet, and PyPI—zero API keys or expensive subscriptions needed.
* 🔏 **Cryptographic Trust Boundary:** Changes are packaged into HMAC-SHA256 signed canonical bundles with strict TTL expiry and tamper detection before reaching Stage B.

---

## 🔄 The AMstraLift Modernization Loop

```text
       TARGET REPOSITORY
               │
               ▼
     ┌───────────────────┐
     │  Scan & Classify  │  <- Google OSV.dev & Manifest Dependency Graph
     └─────────┬─────────┘
               │
               ▼
     ┌───────────────────┐
     │ Compatibility Map │  <- Minimal secure version selection within framework limits
     └─────────┬─────────┘
               │
               ▼
     ┌───────────────────┐
     │ Sandbox & Healing │  <- Isolated Stage A workspace: AST transforms, Standalone, tsconfig
     └─────────┬─────────┘
               │
               ▼
     ┌───────────────────┐
     │  5 Verification   │  <- Gate 1: Install | Gate 2: Build | Gate 3: Tests
     │       Gates       │  <- Gate 4: CVE Rescan | Gate 5: Strict Diff
     └─────────┬─────────┘
               │
               ▼
     ┌───────────────────┐
     │ Crypto Signing    │  <- HMAC-SHA256 canonical signature & audit payload
     └─────────┬─────────┘
               │
               ▼
     ┌───────────────────┐
     │ Verified PR / Br  │  <- Stage B: Publishes clean pull request proposal
     └───────────────────┘
```

---

## 🚀 Quickstart: Run in 60 Seconds

### Option A: Standalone Windows App (No Python Needed)
1. Download **`amstralift.exe`** from [Latest GitHub Releases](https://github.com/code-amalps/AMstraLift/releases).
2. Double-click **`amstralift.exe`** to open the interactive desktop GUI.
3. Select your repository folder and click **Run Security Audit** or **Run AMstraLift Upgrade**!

### Option B: Run via Python CLI
```bash
# Clone the repository
git clone https://github.com/code-amalps/AMstraLift.git
cd AMstraLift

# Install with dev dependencies using uv or pip
uv sync --group dev

# Launch desktop GUI
uv run amstralift gui

# Or run CLI directly
uv run amstralift version
```

---

## 📖 3-Minute Tutorial: Real-World Use Cases

### 1. Launch the Desktop GUI
```bash
amstralift gui
```
*Opens the responsive desktop interface with Dark/Light themes, real-time terminal log stream, visual progress bar, and Quick Actions (Open in VS Code, Git Status).*

### 2. Preview Vulnerability Fixes (Safe Mode)
```bash
amstralift audit --repo ./my-angular-app --mode preview
```
*Queries OSV.dev, maps direct and transitive dependency trees, computes a compatibility-aware plan, and renders a rich terminal dashboard without modifying any files.*

### 3. Apply Verified Security Patches
```bash
amstralift audit --repo ./my-angular-app --mode apply --target-branch main
```
*Applies fixes in an isolated sandbox, runs build and test gates, rescans for secondary CVEs, cryptographically signs the bundle, and commits a verified patch branch.*

### 4. Full Framework Modernization & Upgrade
```bash
amstralift run --repo ./my-angular-app --ecosystem angular --dry-run
```
*Runs two-stage modernization with automated AST codemods (standalone components, control flow, build ordering) and outputs a signed PR proposal bundle.*

### 5. Launch Headless REST API with Swagger
```bash
amstralift serve --host 127.0.0.1 --port 8000
```
*Starts high-performance FastAPI server with interactive Swagger documentation at `http://127.0.0.1:8000/docs`.*

---

## 💻 CLI Commands Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| `amstralift gui` | Launches interactive desktop GUI | `amstralift gui` |
| `amstralift audit` | Scans and remediates dependencies & CVEs | `amstralift audit --repo . --mode apply` |
| `amstralift run` | Executes full two-stage framework upgrade | `amstralift run --repo . --ecosystem angular` |
| `amstralift serve` | Starts REST API server with Swagger UI | `amstralift serve --port 8000` |
| `amstralift init-ci` | Generates turnkey GitHub Actions CI workflow | `amstralift init-ci --repo .` |
| `amstralift version` | Displays version and author attribution | `amstralift version` |

---

## 🛡️ The 5 Verification Gates

Every upgrade and security remediation must pass five strict gates in an isolated sandbox:

| Gate | Name | Enforcement | Purpose |
| :--- | :--- | :--- | :--- |
| **Gate 1** | **Clean Install** | Mandatory | Ensures dependency lockfiles resolve cleanly with zero peer dependency conflicts (`npm ci`, `dotnet restore`). |
| **Gate 2** | **Project Build** | Mandatory | Compiles application code and libraries in multi-project workspaces with library-first ordering. |
| **Gate 3** | **Regression Tests** | Adaptive | Executes automated test suites. Gracefully detects absence of test runners and flags uncertainty labels. |
| **Gate 4** | **Post-Rescan** | Mandatory | Rescans the sandbox with OSV.dev to verify the CVE was actually eliminated and no secondary CVEs were introduced. |
| **Gate 5** | **Strict Diff** | Mandatory | Validates that only intended dependency manifests were modified and no unauthorized files leaked into the patch. |

---

## 🧪 Comprehensive Test Suite

AMstraLift is backed by an automated test suite verifying adapters, trust boundaries, AST transforms, and remediation logic:

```bash
uv run pytest -v
```

All **74 core tests** execute and pass in under 20 seconds:
```text
tests/test_cli.py ............. PASSED [ 17%]
tests/test_safe_remediation.py ............... PASSED [ 37%]
tests/test_angular_adapter.py .......................................... PASSED [100%]

============================= 74 passed in 18.69s =============================
```

---

## 📜 Copyright & License

Copyright (C) 2026 **Amal P S**. All rights reserved.

This software is licensed under the **[GNU Affero General Public License v3.0 (AGPL-3.0-or-later)](LICENSE)**.

### Freedom for Developers & Open Source
* **100% Free for Developers:** Software engineers, open-source maintainers, researchers, and students are completely free to inspect, download, use, evaluate, and build upon AMstraLift without any cost or subscription.
* **Network & Copyleft Protection:** Any modified version of AMstraLift deployed over a network (including internal corporate networks, CI/CD runners, or cloud APIs) must have its complete corresponding source code made publicly available under AGPL-3.0.
* **Mandatory Attribution (Section 7b):** Under Sections 5(d) and 7(b) of the AGPLv3, all author attributions and copyright notices for **Amal P S** must remain prominently preserved in all copies, distributions, documentation, and user-facing interfaces (CLI banners and GUI headers). Uncredited rebranding or closed-source proprietary encapsulation is strictly prohibited.

---

### Author & Contact
* **Architect & Author:** Amal P S
* **GitHub Profile:** [https://github.com/code-amalps](https://github.com/code-amalps)
* **Project Repository:** [https://github.com/code-amalps/AMstraLift](https://github.com/code-amalps/AMstraLift)
