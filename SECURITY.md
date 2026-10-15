# Security Policy

AMstraLift is designed as a secure framework modernization and vulnerability remediation engine. We take the security of this engine and the codebases it modifies seriously.

---

## Supported Versions

Only the latest release of AMstraLift receives security updates and vulnerability patches.

| Version | Supported |
| :--- | :---: |
| >= 0.1.18 | :white_check_mark: |
| < 0.1.18 | :x: |

---

## Reporting a Vulnerability

If you discover a security vulnerability or security boundary flaw in AMstraLift (including sandbox breakouts, path traversal vulnerabilities, credential leaks, or HMAC attestation bypasses):

1. **Do NOT open a public issue.**
2. Send an email to **[amal.p.s.official@gmail.com](mailto:amal.p.s.official@gmail.com)** with the subject line:  
   `[SECURITY] AMstraLift Vulnerability Report`
3. Include the following details:
   - Type of vulnerability (e.g., path traversal, command injection, token exposure)
   - Step-by-step reproduction instructions or a minimal reproducible repository
   - Potential impact
   - Suggested remediation or patch (if available)

### Response Commitments

- **Acknowledgment:** We will acknowledge receipt of your vulnerability report within **48 hours**.
- **Assessment:** An initial severity assessment and verification will be provided within **5 business days**.
- **Fix & Disclosure:** Critical security vulnerabilities will be prioritized, and a fix will be published with a CVE / GitHub Advisory notice following coordinated responsible disclosure.

---

## Security Architecture & Design Principles

AMstraLift enforces strict security invariants in its execution:

1. **Isolated Workspace (Stage A):** Upgrades execute in an isolated temporary clone directory (`.amstralift_workspace`). Host Git remotes are neutralized (`file:///dev/null`) to prevent unauthorized network pushes, and dangerous git hooks are removed.
2. **Subprocess Credential Scrubbing:** All subprocesses executed during Stage A have ambient host secrets automatically purged from their environment:
   - **Git & SCM Tokens:** `GITHUB_TOKEN`, `GH_TOKEN`, `GITLAB_TOKEN`, `GL_TOKEN`, `BITBUCKET_TOKEN`, `GIT_ASKPASS`, `SSH_AUTH_SOCK`, `GIT_CREDENTIAL_HELPER`
   - **Package Manager Tokens:** `NODE_AUTH_TOKEN`, `NPM_TOKEN`, `NPM_AUTH_TOKEN`, `PYPI_API_TOKEN`, `TWINE_PASSWORD`, `NUGET_API_KEY`
   - **Cloud Provider Secrets:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `GOOGLE_APPLICATION_CREDENTIALS`
3. **Local Process Boundary:** Build and test verification gates (`npm install`, `ng build`, `npm test`, `dotnet build`) execute on the host machine under the invoking user's account. Install scripts (`postinstall`) and tests run with local user privileges. True containerized isolation (Docker/rootless containers) is on the active roadmap.
4. **Cryptographic Attestation:** Evidence bundles connecting Stage A verification results to Stage B pull requests are signed using HMAC-SHA256 (`AMSTRALIFT_HMAC_SECRET` or ephemeral session key) with constant-time verification to detect tampering.
5. **Deterministic Transformations:** AMstraLift operates on local clones and runs deterministic AST codemods, static linters, and compiler gates. Zero probabilistic LLMs are used in transformation execution.
6. **Network & Privacy Notice:** AMstraLift collects zero telemetry or analytics. In default mode, AMstraLift accesses official registries (`registry.npmjs.org`, `pypi.org`, `nuget.org`) and Google's `api.osv.dev` for vulnerability lookups (transmitting package names and versions only). To disable external network traffic entirely, run with `--offline`.
