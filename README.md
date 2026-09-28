# AMstraLift

> **Upgrade safely. Modernize confidently.**

AMstraLift is an automated dependency and framework upgrade engine built around an explicit, cryptographically bound trust boundary. Zero AI in execution. No auto-merge, ever, for any tier.

---

## 📌 Implementation Status

> **Core engine implementation, hardened remote publisher, CI templates, and the Dependency Security & Safe Upgrade Engine are complete.**  
> The test suite has **111 passing tests (100% pass rate)**.

### Test Taxonomy (111 Passing Tests)
- **Dependency Security & Safe Upgrade Engine (26 tests)**:
  - **OSV.dev Scanner**: Zero-cost vulnerability queries across ecosystems (npm, NuGet, PyPI), batching, and cache hits.
  - **Direct & Transitive Dependency Graph**: Manifest and lockfile parsing across npm (v1, v2, v3 lockfiles), .NET (`Directory.Packages.props` CPM, direct `.csproj`, `packages.lock.json`), and Python (`pyproject.toml`, `requirements.txt`).
  - **Compatibility Version Selector**: TargetFramework constraints, peer dependencies, minimal secure version selection, and manual escalation triggers.
  - **Verification Engine & 6-Situation Matrix**: `VERIFIED_SAFE`, `BUILD_FAILED`, `TESTS_FAILED`, `UNVERIFIED_NO_TESTS`, `VERIFICATION_INCOMPLETE`, and `RESCAN_FAILED`.
  - **Governance & SLA Enforcement**: Policy file parsing (`.amstralift/security-policy.yaml`), custom severity SLAs, auto-remediation triggers, exceptions, and audit export.
- **Unit Tests (30 tests)**: Cryptographic signing (`HMAC-SHA256`), canonical bundle serialization, TTL expiry, governance policies (Angular LTS staleness checks, Python runtime default resolution with no-match fallback, time-boxed CVE exceptions, EOL escalation SLAs, Section 16 pilot metrics calculation), GitHub provider client unit tests (idempotency key hashing, deterministic branch names with short hash collision avoidance, 422 recovery, timeout PR recovery, label failure handling, in-memory token sanitization).
- **CI Template & Security Tests (6 tests)**: YAML schema verification, concurrency protection (`cancel-in-progress: false`), least-privilege permissions (`contents: write`, `pull-requests: write`, `issues: write`), timeout validation, and `amstralift init-ci` generator execution.
- **Adapter Integration Tests (16 tests)**: Real manifest and lockfile generation, version discovery, and build/test gates across Angular, Python (PyPI), .NET (NuGet), and React (npm).
- **Trust-Boundary & Security Tests (17 tests)**: Path traversal rejection (`..`), symlink blocking (`mode 120000`), pipeline & governance file protection (`.github/workflows/`, `CODEOWNERS`, `SECURITY.md`), git credential scrubbing (`.git/config`, hooks), and re-diff verification.
- **Remote Publishing & Stage B Hardening Tests (16 tests)**: End-to-end publishing against real bare Git remotes, clean worktree verification, selective patch path staging (no `git add .`), patch apply aborts, duplicate PR detection & idempotency (zero duplicate PRs), push retry recovery (reusing existing pushed branch on API timeout), diverged remote branch conflict rejection (no force push), remote target branch drift detection, stale-base commit rejection, and tampered bundle rejection.

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

## 🚀 Running AMstraLift

You can run AMstraLift using `uv`, standard `pip`, or as a **standalone compiled binary (`amstralift.exe`)**.

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

The `run` command executes full two-stage major/minor framework upgrades (e.g. .NET 8 to .NET 9/10, Angular migrations):

```powershell
# Run framework upgrade check in dry-run mode
uv run amstralift run --repo "C:\path\to\my-app" --dry-run

# Run framework upgrade and open GitHub PR
uv run amstralift run --repo "C:\path\to\my-app" --publish --token $env:GITHUB_TOKEN
```

### 3. Generate Turnkey CI Workflows (`amstralift init-ci`)

```powershell
# Generate turnkey GitHub Actions workflows in a repository
uv run amstralift init-ci --provider github --repo .
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
# Run the complete test suite (111 tests)
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
