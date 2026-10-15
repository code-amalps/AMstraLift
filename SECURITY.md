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

1. **Isolated Working Tree (Stage A):** Upgrades execute in an isolated temporary workspace (`.amstralift_sandbox`). Host Git remotes are neutralized (`file:///dev/null`) and sensitive environment variables (`GITHUB_TOKEN`, `SSH_AUTH_SOCK`, `GIT_ASKPASS`) are scrubbed from subshell environments.
2. **Cryptographic Attestation:** Evidence bundles connecting Stage A sandbox results to Stage B pull requests are signed using HMAC-SHA256 with constant-time verification to detect tampering.
3. **No Dynamic Remote Execution:** AMstraLift operates on local clones and runs purely deterministic AST codemods, static linters, and compiler gates. Zero probabilistic LLMs are used in transformation execution.
4. **Network Access Disclosure:** In default mode, AMstraLift accesses standard official registries (`registry.npmjs.org`, `pypi.org`, `nuget.org`) and Google's `api.osv.dev` for vulnerability lookups. To disable all external network traffic entirely, run with `--offline` or `--corporate-safe`.
