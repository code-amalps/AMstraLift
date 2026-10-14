# Why I Built AMstraLift: Fixing What Bumping Version Strings Breaks

*Author: Amal P S*

Every software engineer maintaining long-lived applications knows this story:

You open GitHub on Monday morning and find 14 Dependabot or Renovate pull requests. Each one bumps a single package version in `package.json` or `*.csproj`. 

You click on the first one: **Build Failed**. 
Peer dependency conflicts. Deprecated configuration options. Breaking template syntax. Outdated compiler flags. 

The tool that was supposed to save you time simply handed you broken code on a silver platter. You spend the next three days reading framework migration guides, resolving npm peer dependency errors, rewriting `@NgModule` code to standalone components, adjusting `tsconfig.json`, and running `ng test` over and over.

That is why I built **AMstraLift**.

---

## The Problem With "Version Bumping"

Dependency tools today treat software upgrades as text replacement problems:

$$\text{"@angular/core": "\^14.0.0"} \longrightarrow \text{"@angular/core": "\^18.0.0"}$$

In reality, a major framework upgrade is an **architectural code migration**. Upgrading Angular from v14 to v18 isn't just editing a number—it involves:
1. Aligning companion packages (`@angular/cli`, `@angular-devkit/*`, `typescript`, `@angular-eslint`).
2. Regenerating lockfiles with clean peer dependency resolution.
3. Modernizing workspace schema definitions (`browserTarget` $\to$ `buildTarget` in `angular.json`).
4. Cleaning up deprecated compiler settings (`baseUrl`, `ignoreDeprecations` in `tsconfig.json`).
5. Running deterministic code transformations (converting templates to modern `@if` / `@for` control flow, inferring standalone component imports).
6. Compiling the application in an isolated sandbox.
7. Running the regression test suite with headless browser flags.
8. Verifying that existing HTTP routes and public exports are not silently broken.

If an automation tool cannot do that, the engineer still has to do it all manually.

---

## What AMstraLift Actually Does

AMstraLift is a local CLI and desktop application designed around a core philosophy: **never propose a pull request that hasn't proven it builds and passes tests.**

It does not use LLMs or generative AI in its execution loop. Everything it does is **deterministic**:
- It maps dependencies using official package registries and Google's OSV.dev vulnerability database.
- It applies precise AST transformations, schematics, and linters.
- It executes changes inside an isolated temporary sandbox workspace—your original working tree is never touched unless verification passes.
- It runs changes through **6 verification gates**:
  1. **Clean Install**: Verifies dependency graph resolves cleanly (`npm ci` / `dotnet restore`).
  2. **Build**: Ensures the project compiles without TypeScript or compiler errors.
  3. **Tests**: Executes existing unit tests (e.g., Karma with `--browsers=ChromeHeadless --watch=false`, Jest, Vitest, pytest).
  4. **Security Rescan**: Verifies target vulnerabilities were resolved without introducing secondary CVEs.
  5. **Strict Diff**: Confirms only expected files and dependencies were touched.
  6. **Semantic & Contract Diff**: Verifies public HTTP API routes and exported symbols weren't unintentionally broken.

If any gate fails, AMstraLift halts or rolls back safely. It can also open a draft PR with exact compiler diagnostic logs so you know exactly where the failure occurred.

---

## Real Example: Angular 15 to 18 Upgrade with Control-Flow Codemods

Here is a real example of what happens to a template during modernization:

### Before
```html
<div *ngIf="user$ | async as user">
  <span>Welcome, {{ user.name }}</span>
</div>

<ng-container *ngIf="isAdmin">
  <admin-panel></admin-panel>
</ng-container>

<ul>
  <li *ngFor="let item of items; trackBy: customTrack">
    {{ item }}
  </li>
</ul>
```

### After AMstraLift Execution
```html
@if (user$ | async; as user) {
  <div>
    <span>Welcome, {{ user.name }}</span>
  </div>
}

@if (isAdmin) {
  <admin-panel></admin-panel>
}

@for (let item of items; track customTrack) {
  <ul>
    <li>{{ item }}</li>
  </ul>
}
```

Alongside the template migration:
- TypeScript was aligned from `~4.9.5` to `^5.4.5`.
- `angular.json` was updated to use `@angular/build` and `buildTarget`.
- Multi-project libraries were compiled in dependency order before the application build.
- Verification gates 1 through 6 passed before the branch was created.

---

## Where AMstraLift Stands Today (Honest Status)

I want to be completely transparent about where the tool is mature and where it is still evolving:

- **Angular (v12 $\to$ v19)**: This is our most mature ecosystem. It handles AST-level standalone conversions, control flow, build toolchain migrations, and npm lockfile synchronization.
- **.NET**: Handles `.csproj` and `Directory.Build.props` updates, Central Package Management (CPM), and delegates syntax/style fixes to `dotnet format`. Requires local .NET SDK.
- **Python**: Handles `requirements.txt` and `pyproject.toml` updates with tiering. Syntax and formatting rely on external tools like `ruff` or `pyupgrade`.
- **React**: Handles `package.json` updates and JSX transform configuration (`react-jsx`). It does not yet perform automated AST refactoring of legacy class components to hooks.

The project currently has **300 automated tests** passing in CI, covering adapter logic, sandbox isolation, crypto attestation, and semantic contract extraction.

---

## How You Can Help

I am actively looking for software engineers who maintain real-world codebases to test AMstraLift against tricky upgrades.

You don't have to risk your main branch. You can test it safely in 2 minutes:

1. Create a throwaway branch on your repository:
   ```bash
   git checkout -b test/amstralift-trial
   ```
2. Run a dry run or preview:
   ```bash
   # If using standalone Windows binary:
   amstralift.exe audit --repo . --mode preview
   
   # Or run full modernization dry-run:
   amstralift.exe run --repo . --dry-run
   ```
3. Inspect the proposed changes, verification output, and compiler diagnostics.
4. Report what worked and what failed in our [GitHub Issue Tracker](https://github.com/code-amalps/AMstraLift/issues).

Whether it succeeds cleanly or fails on a weird edge case in your build config, that feedback is what will take AMstraLift from an active open-source project to an indispensable tool for every engineering team.

---

*Repository:* [https://github.com/code-amalps/AMstraLift](https://github.com/code-amalps/AMstraLift)  
*Author:* Amal P S ([@code-amalps](https://github.com/code-amalps))  
*License:* GNU AGPLv3
