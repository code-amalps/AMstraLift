# AMstraLift

> **Upgrade safely. Modernize confidently.**  
> *Architected & Engineered by Amal P S • Free Software under GNU AGPLv3*

AMstraLift is an automated dependency and framework upgrade engine built around an explicit, cryptographically bound trust boundary. Zero AI in execution. No auto-merge, ever, for any tier.

---

## 📌 Implementation Status

> **Core engine implementation, hardened remote publisher, CI templates, live container registry synchronization, REST API service, Dependency Security & Safe Upgrade Engine, and the Angular Automated Modernization Suite (Standalone & Control Flow) are complete.**  
> The test suite has **202 passing tests (100% pass rate)**.

### Test Taxonomy (202 Passing Tests)
- **Angular Modernization & Standalone Architecture (32 tests)**: Global selector-to-class symbol indexing, automated standalone component import inference, custom elements schemas (`CUSTOM_ELEMENTS_SCHEMA`), modern control flow transformations (`@if`, `@for`, `@switch`), Angular Material M2 Sass theming, deprecated template rewrites (`<mat-placeholder>`, `<mat-chip-list>`), reactive forms untyped fallback, and direct/incremental multi-major upgrade orchestration.
- **REST API & Swagger Service (7 tests)**: FastAPI endpoints (`/api/v1/health`, `/api/v1/policy`, `/api/v1/audit`, `/api/v1/upgrade`), root redirect to `/docs`, error handling, and CORS middleware.
- **Docker & Container Registry Sync (34 tests)**: Multi-stage Dockerfile parsing, suffix-preserving tags (`-alpine`, `-slim`, `-jammy`), live Microsoft Container Registry (MCR) queries for .NET images, Docker Hub Official API for Node.js and Python, and live Node.js LTS release schedule synchronization.
- **Dependency Security & Safe Upgrade Engine (26 tests)**:
  - **OSV.dev Scanner**: Zero-cost vulnerability queries across ecosystems (npm, NuGet, PyPI), batching, and cache hits.
  - **Direct & Transitive Dependency Graph**: Manifest and lockfile parsing across npm (v1, v2, v3 lockfiles), .NET (`Directory.Packages.props` CPM, direct `.csproj`, `packages.lock.json`), and Python (`pyproject.toml`, `requirements.txt`).
  - **Compatibility Version Selector**: TargetFramework constraints, peer dependencies, minimal secure version selection, and manual escalation triggers.
  - **Verification Engine & 6-Situation Matrix**: `VERIFIED_SAFE`, `BUILD_FAILED`, `TESTS_FAILED`, `UNVERIFIED_NO_TESTS`, `VERIFICATION_INCOMPLETE`, and `RESCAN_FAILED`.
  - **Governance & SLA Enforcement**: Policy file parsing (`.amstralift/security-policy.yaml`), custom severity SLAs, auto-remediation triggers, exceptions, and audit export.
- **Unit Tests (30 tests)**: Cryptographic signing (`HMAC-SHA256`), canonical bundle serialization, TTL expiry, governance policies (Angular LTS staleness checks, Python runtime default resolution with no-match fallback, time-boxed CVE exceptions, EOL escalation SLAs, Section 16 pilot metrics calculation), GitHub provider client unit tests (idempotency key hashing, deterministic branch names with short hash collision avoidance, 422 recovery, timeout PR recovery, label failure handling, in-memory token sanitization).
- **CI Template & Security Tests (6 tests)**: YAML schema verification, concurrency protection (`cancel-in-progress: false`), least-privilege permissions (`contents: write`, `pull-requests: write`, `issues: write`), timeout validation, and `amstralift init-ci` generator execution.
- **Adapter Integration Tests (21 tests)**: Real manifest and lockfile generation, version discovery, watch-mode timeout prevention (`REQUIRED_TIMEOUT`), headless browser detection, and build/test gates across Angular, Python (PyPI), .NET (NuGet), and React (npm).
- **Trust-Boundary & Security Tests (17 tests)**: Path traversal rejection (`..`), symlink blocking (`mode 120000`), pipeline & governance file protection (`.github/workflows/`, `CODEOWNERS`, `SECURITY.md`), git credential scrubbing (`.git/config`, hooks), and re-diff verification.
- **Remote Publishing & Stage B Hardening Tests (29 tests)**: End-to-end publishing against real bare Git remotes, clean worktree verification with ephemeral cache exclusion (`.angular/`, `.nx/`, `.turbo/`), selective patch path staging (no `git add .`), patch apply aborts, duplicate PR detection & idempotency (zero duplicate PRs), push retry recovery (reusing existing pushed branch on API timeout), diverged remote branch conflict rejection (no force push), remote target branch drift detection, stale-base commit rejection, and tampered bundle rejection.

---

## 🔒 Dependency Security & Safe Upgrade Engine

AMstraLift includes an enterprise-grade vulnerability scanner and safe remediation engine designed to patch third-party dependencies **without unnecessarily upgrading framework or runtime versions**.

### Core Capabilities

1. **Zero-Cost Live Vulnerability Scanning (OSV.dev)**:
   - Scans against Google's distributed Open Source Vulnerability database (OSV.dev) covering CVEs and GHSAs.
   - Zero API keys or subscriptions required; includes automatic batching and request deduplication.

2. **Direct & Transitive Dependency Analysis**:
   - Parses both top-level manifests and frozen lockfiles across **Angular, React, .NET, and Python**.
   - Resolves transitive dependency trees, identifying which top-level packages introduced a vulnerable sub-dependency.
   - Detects whether direct override capabilities exist (e.g., npm `overrides`, yarn `resolutions`, .NET Direct `<PackageReference>`, or Python pip constraints).

3. **Intelligent Compatibility Version Selector**:
   - **Minimal Version Selection**: Selects the closest secure version that satisfies framework constraints rather than jumping to breaking major releases.
   - **Framework-Aware Filtering**: Enforces runtime boundaries (e.g., .NET `TargetFramework`, Python `requires-python`, Angular peer dependencies).
   - If no compatible version exists without breaking framework rules, AMstraLift halts and outputs an actionable manual escalation recommendation.

4. **Rigorous 5-Gate Verification Engine**:
   Every proposed patch runs inside an isolated sandbox through five sequential verification gates:
   1. **Manifest & Graph Gate**: Manifests and lockfiles updated consistently.
   2. **Isolated Build Gate**: Application compiles cleanly in isolation.
   3. **Regression Test Gate**: Automated tests run; pass/fail evaluated.
   4. **Post-Upgrade Rescan Gate**: Validates that target CVE is resolved and no new CVEs were introduced.
   5. **Diff Gate**: Verifies only the intended dependency changed.

### The 6-Situation Verification Matrix

| Situation | Build Status | Test Status | Security Rescan | AMstraLift Action & PR Behavior |
| :--- | :---: | :---: | :---: | :--- |
| **1. Verified Safe** | ✅ Pass | ✅ Pass (>0 tests) | ✅ Resolved | **Safe to Ship**: Creates standard verified pull request. |
| **2. Build Failed** | ❌ Fail | — | — | **Halt**: Aborts remediation. No PR opened. Diagnostics captured. |
| **3. Tests Failed** | ✅ Pass | ❌ Fail | — | **Halt**: Regressions caught. Patch rejected. Logs recorded. |
| **4. Zero Tests Found** | ✅ Pass | ⚠️ 0 Tests Run | ✅ Resolved | **Policy Gate**: Opens PR with prominent `UNVERIFIED_NO_TESTS` warning banner & label. Requires explicit manual sign-off. |
| **5. Verification Incomplete** | ✅ Pass | ⚠️ Skipped / Tool Missing | ✅ Resolved | **Policy Gate**: Opens PR flagged with `VERIFICATION_INCOMPLETE`. |
| **6. Rescan Failed** | ✅ Pass | ✅ Pass | ❌ CVE Still Present | **Halt**: Patch rejected because vulnerability remains or new CVE introduced. |

---

## 📋 Organizational Security & SLA Policy

AMstraLift enforces customizable remediation SLAs and approval rules via an optional `.amstralift/security-policy.yaml`:

```yaml
# Organization Vulnerability Remediation Policy
default_sla_days:
  CRITICAL: 14     # Remediate critical CVEs within 14 days
  HIGH: 30         # Remediate high CVEs within 30 days
  MEDIUM: 60       # Remediate medium CVEs within 60 days
  LOW: 90          # Remediate low CVEs within 90 days

require_test_verification: true   # Fail if 0 automated tests exist
allow_untested_remediation: false # Block PR creation if untested

auto_remediation:
  enabled: true
  min_severity: "HIGH"
  max_version_bump: "MINOR"       # PATCH, MINOR, or MAJOR

exceptions:
  - vulnerability_id: "GHSA-xxxx-yyyy-zzzz"
    package_name: "example-pkg"
    reason: "Compensating firewall controls in place"
    approved_by: "sec-team@example.com"
    expires_at: "2026-12-31T23:59:59Z"
```

---

## ⚡ Automated Angular Modernization Suite (v12 ➔ v22+)

AMstraLift features a deterministic Angular modernization pipeline that enables leapfrog upgrades from legacy versions (Angular 12–17) straight to modern Angular (v18–22+) without breaking compilation or requiring manual refactoring.

### Key Capabilities

1. **Deterministic Standalone Component Modernization (`--modernize standalone`)**:
   - **Global Selector Registry**: Indexes all components and directives across the workspace to map template selectors and custom element tags to their TypeScript class declarations.
   - **Automated Import Inference**: Bridges the Angular 19+ "standalone by default" schematic gap by deterministically injecting `imports: [SharedModule, ChildComponent, ...]` into `@Component` decorators for all referenced child components, pipes (`translate`, `async`), and module exports.
   - **Web Components & Custom Elements**: Automatically detects non-Angular custom elements (e.g. `<mwc-button>`) and injects `CUSTOM_ELEMENTS_SCHEMA`.
   - **Standalone Bootstrap Migration**: Modernizes `main.ts` from legacy `platformBrowserDynamic().bootstrapModule(AppModule)` to `bootstrapApplication(AppComponent, { providers: [...] })` and prunes obsolete root `AppModule` files.

2. **Modern Control Flow Migration (`--modernize control-flow`)**:
   - Transforms legacy structural directives (`*ngIf`, `*ngFor`, `*ngSwitch`) into native Angular 17+ control flow syntax (`@if`, `@for`, `@switch`).

3. **Angular Material MDC & M2 Theming Modernization**:
   - **Template Tag Rewrites**: Automatically replaces deprecated Material elements (`<mat-placeholder>` $\rightarrow$ `<mat-label>`, `<mat-chip-list>` $\rightarrow$ `<mat-chip-set>`).
   - **Sass M2 Palette Namespaces**: Modernizes theming files for Angular Material 18+ by rewriting legacy palette calls to `mat.m2-define-palette` and `mat.$m2-*-palette`.

4. **Reactive Forms Type Safety**:
   - Automatically migrates form controls and groups to `UntypedFormBuilder` / `UntypedFormGroup` to prevent Angular 14+ strict typed forms compilation failures (`form.value` partial type mismatches) while preserving exact runtime behavior.

5. **Ecosystem Version Pinning**:
   - Enforces compatible major versions for critical companion libraries (e.g. `@ngx-translate/http-loader@^16.0.0`, `@fortawesome/angular-fontawesome@^5.1.0`).

6. **Ephemeral Cache Resilience**:
   - Stage B and patch verification automatically identify and ignore transient build/tool caches (`.angular/`, `.nx/`, `.turbo/`, `__pycache__`, `.cache/`), preventing false-positive dirty tree rejections.

7. **Flexible Upgrade Strategies**:
   - **Direct Mode (`--no-incremental`)**: Directly bumps manifests, aligns ecosystem packages, and applies all modernizations in a single unified run (~5 minutes).
   - **Incremental Mode (`--incremental`, default)**: Steps major-version by major-version ($12 \rightarrow 13 \rightarrow \dots \rightarrow 22$) running intermediate schematics sequentially.

---

## 🚀 Running AMstraLift

AMstraLift can be executed using `uv`, standard `pip`, or as a **zero-dependency standalone binary (`amstralift.exe`)**.

### 📥 Standalone Windows Executable (`amstralift.exe`)

No Python or package installation is required:
1. Download **`amstralift.exe`** from the [GitHub Releases](https://github.com/code-amalps/AMstraLift/releases) page.
2. **Double-click** `amstralift.exe` to launch the full interactive desktop GUI (with Dark/Light themes, live log stream, abort controls, and progress tracking).
3. Or run it directly from PowerShell / CMD as a command-line utility:
   ```powershell
   .\amstralift.exe --help
   .\amstralift.exe audit --repo "C:\Projects\my-app"
   .\amstralift.exe run --repo "C:\Projects\my-app"
   ```

---

### 1. Security Audit & Safe Remediation (`amstralift audit`)

The `audit` command inspects dependencies, checks OSV.dev, and optionally plans or applies surgical fixes.

```powershell
# 1. Audit Only (Scan dependencies, display vulnerability table, evaluate SLAs)
uv run amstralift audit --repo "C:\path\to\my-app"

# 2. Preview Mode (Dry-run plan: calculate minimal secure versions without changing files)
uv run amstralift audit --repo "C:\path\to\my-app" --mode preview

# 3. Apply Mode / Fix (Surgically update, compile, run test suite, rescan)
uv run amstralift audit --repo "C:\path\to\my-app" --fix

# 4. Fix & Publish PR to GitHub
uv run amstralift audit --repo "C:\path\to\my-app" --fix --publish --token $env:GITHUB_TOKEN

# 5. Enforce Custom Security Policy
uv run amstralift audit --repo "C:\path\to\my-app" --policy .amstralift/security-policy.yaml
```

### 2. Framework Upgrades (`amstralift run`)

The `run` command executes full two-stage major/minor framework upgrades (e.g. Angular 12 to 22, .NET 8 to .NET 9/10):

```powershell
# 1. Full Angular Leapfrog Upgrade (v12 -> v22 with Standalone + Control Flow)
uv run amstralift run `
  --repo "C:\Projects\my-angular-app" `
  --output-branch amstralift/upgrade `
  --modernize control-flow,standalone `
  --allow-failed-gates --no-incremental

# 2. Step-by-Step Incremental Upgrade (Sequentially runs schematics per major version)
uv run amstralift run --repo "C:\Projects\my-angular-app" --incremental

# 3. Dry-Run Upgrade Inspection
uv run amstralift run --repo "C:\path\to\my-app" --dry-run

# 4. Verified Upgrade & Automatic GitHub PR Creation
uv run amstralift run --repo "C:\path\to\my-app" --publish --token $env:GITHUB_TOKEN
```

### 3. Generate Turnkey CI Workflows (`amstralift init-ci`)

```powershell
# Generate turnkey GitHub Actions workflows in a repository
uv run amstralift init-ci --provider github --repo .
```

---

## 🌐 REST API Service (`amstralift serve`)

AMstraLift can run as an HTTP microservice with auto-generated OpenAPI / Swagger UI documentation, allowing teams to trigger scans and upgrades remotely without running local CLI commands.

### Starting the Server
```powershell
# Default: binds to http://127.0.0.1:8000
amstralift serve

# Custom host and port (e.g. for container/cloud deployment)
amstralift serve --host 0.0.0.0 --port 8080
```

### Interactive Documentation & Swagger UI
Once running, open your browser to:
- **Interactive Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc Documentation**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

### Key Endpoints

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/v1/health` | Healthcheck, version, available adapters, and host capabilities (Docker, Git). |
| `GET` | `/api/v1/policy` | Inspect active organizational SLA and exception policies. |
| `POST` | `/api/v1/audit` | Scan dependencies for CVEs and optionally preview or apply safe remediations. |
| `POST` | `/api/v1/upgrade` | Execute complete two-stage framework and dependency upgrades. |

#### Example: Triggering a Security Audit via curl
```bash
curl -X POST http://127.0.0.1:8000/api/v1/audit \
  -H "Content-Type: application/json" \
  -d '{
    "repo_path": "/workspace/my-app",
    "ecosystem": "angular",
    "mode": "audit"
  }'
```

---

## 📦 Standalone Executable & Distribution

AMstraLift can be packaged and run on **any machine without installing Python**.

### Option A: Standalone Single-File Binary (`amstralift.exe`)

The standalone binary packages the Python runtime, Typer CLI, Rich UI, OSV client, and all dependencies into a single self-extracting executable.

#### 1. Building the Executable
From the AMstraLift repository:
```powershell
uv run --with pyinstaller pyinstaller --name amstralift --onefile --clean --collect-all amstralift amstralift/cli.py
```
This produces `dist/amstralift.exe` (~19 MB).

#### 2. Running on Another Computer
1. Copy `amstralift.exe` to any folder on the target computer (e.g. `C:\Tools\amstralift.exe`).
2. *(Optional)* Add `C:\Tools` to your system `PATH` so you can invoke `amstralift` from anywhere.
3. Open PowerShell or Command Prompt and run:
   ```cmd
   amstralift.exe --help
   amstralift.exe audit --repo "C:\Projects\ClientApp"
   amstralift.exe audit --repo "C:\Projects\ClientApp" --fix
   ```

> [!NOTE]
> The target computer does **not** need Python installed. However, if you run `--fix` or verification gates, the target machine must have the project's native build tools installed (such as `dotnet` SDK for .NET projects, or `npm` / `node` for Angular/React projects).

---

### Option B: Distributing via Wheel (`pipx` or `uv tool`)

You can also distribute AMstraLift as a standard Python wheel:

#### 1. Build the Wheel
```powershell
uv build
```
This generates `dist/amstralift-0.1.0-py3-none-any.whl`.

#### 2. Install on Target Machine (Python 3.11+)
Using `pipx` (isolated user CLI):
```powershell
pipx install dist/amstralift-0.1.0-py3-none-any.whl
amstralift --help
```

Or using `uv tool`:
```powershell
uv tool install dist/amstralift-0.1.0-py3-none-any.whl
amstralift --help
```

---

## 🧪 Development & Testing

### Running Tests
```powershell
# Run the complete test suite (202 tests)
uv run pytest

# Run with verbose output
uv run pytest -v

# Run only security & vulnerability tests
uv run pytest tests/test_security_remediation.py
```

### Code Formatting & Linting
```powershell
uv run ruff check .
uv run ruff format .
```

---

## 🛡️ Production-Readiness Checklist

- [x] **Least-Privilege Credentials**: Tokens are supplied in-memory via `http.extraheader` / CLI options, never saved to `.git/config`, bundles, or logs.
- [x] **Publishing Kill-Switches**: Publishing is disabled by default (`--dry-run` and explicit `--publish` required).
- [x] **Zero-Cost Vulnerability Discovery**: Google OSV.dev API integration across npm, NuGet, and PyPI.
- [x] **Direct & Transitive Remediation**: Safe minimal version resolution with lockfile integrity.
- [x] **5-Gate Verification Engine**: Build, test, rescan, and diff gates strictly enforced.
- [x] **Configurable SLA Policies**: Custom deadlines and approval workflows for CVEs.
- [x] **Idempotency & Duplicate Prevention**: Deterministic branch naming and matching-branch lookup prevent duplicate PRs across repeated runs.
- [x] **Failure Visibility & Recovery**: Push retry recovery skips redundant pushes if the remote branch matches local HEAD; failures fail closed with clear status.
- [x] **Log & Bundle Scrubbing**: Tokens and basic auth credentials are fully scrubbed (`[REDACTED_TOKEN]`) from exceptions, bundles, and CLI outputs.
- [x] **Scheduled CI Concurrency**: Workflows enforce `cancel-in-progress: false` to protect running Stage B publisher operations.
- [x] **Standalone Binary Packaging**: Single-file `.exe` distribution tested for portability.

---

## ⚖️ License & Author Attribution

AMstraLift is architected and engineered by **Amal P S** and released under the **GNU Affero General Public License v3 (AGPL-3.0)**.

- **100% Free for Developers**: Individual software engineers, open-source maintainers, researchers, and students are free to use, run, inspect, and evaluate AMstraLift completely free of charge. No subscriptions, paywalls, or feature limits.
- **Corporate & Network Copyleft**: In accordance with the AGPL-3.0, any party deploying AMstraLift or modified versions thereof over a computer network (including internal corporate networks, CI/CD runners, or cloud APIs) must make the complete source code publicly available under AGPL-3.0.
- **Section 7(b) Mandatory Attribution**: All copies, forks, distributions, and interactive user interfaces (CLI outputs, GUI headers, and window titles) must retain the original author attribution: **"Engineered by Amal P S"** and the AMstraLift copyright notice. Uncredited rebranding or closed-source proprietary encapsulation is strictly prohibited.

See the full [`LICENSE`](LICENSE) file for complete legal terms.

