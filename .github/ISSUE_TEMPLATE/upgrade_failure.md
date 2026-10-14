---
name: Upgrade Failure Report
about: Report a failure during an upgrade, codemod, or verification gate
title: "[Upgrade Failure]: <Framework> <From Version> -> <To Version> failed at <Gate>"
labels: ["upgrade-failure", "needs-triage"]
assignees: []
---

<!-- 
IMPORTANT: DO NOT include private API keys, proprietary source code, secrets,
passwords, or internal corporate tokens in this report.
-->

### Environment
- **AMstraLift Version** (run `amstralift version`): 
- **Operating System** (e.g., Windows 11, Ubuntu 24.04, macOS Sonoma):
- **Execution Mode** (CLI / Desktop GUI / REST API):

### Target Repository Details
- **Ecosystem / Framework** (Angular / React / .NET / Python):
- **Current Version** (e.g., Angular 15.2.0, .NET 6.0, Python 3.9):
- **Target Version** (e.g., Angular 18.2.0, .NET 8.0):
- **Package Manager** (npm / yarn / pnpm / dotnet CLI / pip / poetry):
- **Repository Structure** (Single project / Monorepo):

### Command Executed
```bash
# Paste the exact command you ran, e.g.:
# amstralift run --repo ./my-project --dry-run
```

### Failed Verification Gate
- [ ] Gate 1: Clean Install (dependency resolution / lockfile failure)
- [ ] Gate 2: Project Build (TypeScript / compilation error)
- [ ] Gate 3: Test Suite (unit tests failed or timed out)
- [ ] Gate 4: Security Rescan (unresolved CVEs or secondary vulnerabilities introduced)
- [ ] Gate 5: Strict Diff (unauthorized file modifications)
- [ ] Gate 6: Semantic & Contract Diff (unintended route, symbol, or config violation)
- [ ] Refusal Engine / Pre-execution refusal
- [ ] Other / Crash

### Error Output / Logs
```text
<!-- Paste relevant terminal or GUI log output here. Redact company names or sensitive paths. -->
```

### Expected Behavior
What should have happened (e.g., "The standalone migration schematic should have updated imports in `app.module.ts` without failing compilation").

### Actual Behavior
What actually happened (e.g., "Gate 2 failed with TS2307: Cannot find module '@angular/core'").

### Minimal Reproduction (If available)
If you can share a public reproduction repo or the relevant snippets of your manifest (e.g., `package.json`, `.csproj`, `requirements.txt`), please link or paste it here:
