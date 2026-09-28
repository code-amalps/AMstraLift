"""CLI entry points for AMstraLift using Typer and Rich."""

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from amstralift.core.workspace import get_active_branch, run_git
from amstralift.execution.stage_a import NoUpgradesAvailableError
from amstralift.execution.stage_b import StageBPublishError
from amstralift.service import UpgradeOrchestrator

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = typer.Typer(
    name="amstralift",
    help="AMstraLift: Automated Dependency & Framework Upgrade Engine with an explicit trust boundary.",
    add_completion=False,
)
console = Console(safe_box=True)


@app.command()
def run(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Path to target repository.")] = Path("."),
    ecosystem: Annotated[str | None, typer.Option("--ecosystem", "-e", help="Target ecosystem (angular, python, dotnet, react). Auto-detected if omitted.")] = None,
    branch: Annotated[str | None, typer.Option("--branch", "-b", help="Target base branch name (defaults to active branch).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate without modifying git branch/commit.")] = False,
    publish: Annotated[bool, typer.Option("--publish", help="Push branch and open reviewable PR on remote Git provider.")] = False,
    token: Annotated[str | None, typer.Option("--token", envvar="GITHUB_TOKEN", help="Least-privilege Git provider access token.")] = None,
    repo_id: Annotated[str | None, typer.Option("--repo-id", help="Repository identifier (e.g. owner/repo).")] = None,
    remote_url: Annotated[str | None, typer.Option("--remote-url", help="Explicit Git remote URL for push.")] = None,
    output_bundle: Annotated[
        Path | None, typer.Option("--output-bundle", help="Save signed bundle JSON to disk.")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip interactive confirmation before remote push/PR.")
    ] = False,
    incremental: Annotated[
        bool,
        typer.Option(
            "--incremental/--no-incremental",
            "-i",
            help="Upgrade framework dependencies incrementally by one major version (e.g. Angular 12 -> 13).",
        ),
    ] = True,
    allow_failed_gates: Annotated[
        bool,
        typer.Option(
            "--allow-failed-gates",
            "--skip-gates",
            help="Allow Stage B to commit the upgrade branch locally even if sandbox verification gates fail.",
        ),
    ] = False,
):
    """Run full two-stage upgrade workflow on a target repository."""
    repo_str = str(repo)
    if (
        repo_str.startswith(("http://", "https://", "http:\\", "https:\\", "git@", "ssh://"))
        or "://" in repo_str
        or "github.com" in repo_str
        or "gitlab.com" in repo_str
    ):
        console.print(
            f"[bold red]✖ Invalid repository path:[/bold red] '{repo_str}' is a remote Git URL.\n"
            f"[yellow]AMstraLift operates on a local cloned repository so it can execute builds and tests in Stage A.[/yellow]\n"
            f"[dim]Tip: Clone the repository first with 'git clone <url>' and pass the local folder path (e.g. --repo .)[/dim]"
        )
        raise typer.Exit(code=1)

    if not repo.exists() or not repo.is_dir():
        console.print(
            f"[bold red]✖ Target directory does not exist:[/bold red] '{repo.resolve()}'\n"
            f"[dim]Please ensure the directory exists and contains your project files.[/dim]"
        )
        raise typer.Exit(code=1)

    if publish and not yes:
        confirmed = typer.confirm(
            f"You specified --publish. This will push a new branch to the remote repository and open a Pull Request. Continue?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Remote publishing aborted by user. Exited safely without modifying remote.[/yellow]")
            raise typer.Exit(code=0)

    effective_branch = branch
    if not effective_branch:
        active = get_active_branch(repo)
        # Check if active branch has project manifests in Git tree
        manifest_names = ("package.json", "pyproject.toml", "requirements.txt")
        has_manifest = any(
            run_git(["cat-file", "-e", f"{active}:{m}"], cwd=repo).returncode == 0
            for m in manifest_names
        )
        if not has_manifest:
            # Fallback to master or main if they contain manifests
            for candidate in ("master", "main"):
                if candidate != active and run_git(["rev-parse", "--verify", candidate], cwd=repo).returncode == 0:
                    if any(
                        run_git(["cat-file", "-e", f"{candidate}:{m}"], cwd=repo).returncode == 0
                        for m in manifest_names
                    ):
                        active = candidate
                        break
        effective_branch = active

    eco_str = ecosystem or "auto-detect"
    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift[/bold blue] - Initiating upgrade for [cyan]{repo.resolve()}[/cyan] ({eco_str})\n"
            f"[dim]Branch: {effective_branch} | Publish: {publish} | Dry-run: {dry_run}[/dim]",
            border_style="blue",
        )
    )

    orchestrator = UpgradeOrchestrator(incremental=incremental)

    try:
        with console.status("[bold green]Executing Stage A sandbox & Stage B publisher..."):
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=repo,
                ecosystem=ecosystem,
                target_branch=effective_branch,
                dry_run=dry_run,
                publish=publish,
                git_token=token,
                repo_id=repo_id,
                remote_url=remote_url,
                allow_failed_gates=allow_failed_gates,
            )

        console.print("[bold green]✔ Upgrade workflow completed successfully![/bold green]\n")
        if not dry_run:
            console.print(
                f"[bold green]✔ Committed upgrade patch to branch:[/bold green] [cyan]{pr_proposal.branch_name}[/cyan]\n"
                f"[dim]Run 'git checkout {pr_proposal.branch_name}' to inspect the modified files in your editor.[/dim]\n"
            )

        # Display Changes Table
        table = Table(title="Proposed Package Changes", show_header=True, header_style="bold magenta")
        table.add_column("Package")
        table.add_column("Current")
        table.add_column("Target")
        table.add_column("Tier")

        for c in signed_bundle.bundle.changes:
            table.add_row(c.package_name, c.from_version, c.to_version, c.tier.value)
        console.print(table)

        # Display Gate Results
        gate_table = Table(title="Build & Test Gates", show_header=True, header_style="bold green")
        gate_table.add_column("Gate")
        gate_table.add_column("Command")
        gate_table.add_column("Status")
        gate_table.add_column("Duration")

        for g in signed_bundle.bundle.gate_summary.results:
            gate_table.add_row(g.name, g.command, g.status.value, f"{g.duration_seconds:.2f}s")
        console.print(gate_table)

        # PR Summary
        pr_lines = [
            f"[bold]Branch:[/bold] {pr_proposal.branch_name}",
            f"[bold]Title:[/bold] {pr_proposal.title}",
            f"[bold]Publish Status:[/bold] [yellow]{pr_proposal.publish_status}[/yellow]",
        ]
        if pr_proposal.remote_pr_url:
            pr_lines.append(f"[bold]Remote PR URL:[/bold] [cyan]{pr_proposal.remote_pr_url}[/cyan]")
        if pr_proposal.idempotency_key:
            pr_lines.append(f"[bold]Idempotency Key:[/bold] {pr_proposal.idempotency_key[:16]}...")
        pr_lines.extend(
            [
                f"[bold]Labels:[/bold] {', '.join(pr_proposal.labels)}",
                f"[bold]24h SLA Deadline:[/bold] {pr_proposal.deadline_24h.isoformat()}",
                f"[bold]Base SHA:[/bold] {pr_proposal.base_commit_sha}",
                f"[bold]Dry Run:[/bold] {dry_run}",
            ]
        )

        console.print(
            Panel(
                "\n".join(pr_lines),
                title="Pull Request Proposal (Stage B)",
                border_style="green",
            )
        )

        if output_bundle:
            output_bundle.write_text(signed_bundle.model_dump_json(indent=2), encoding="utf-8")
            console.print(f"[dim]Signed bundle saved to: {output_bundle.resolve()}[/dim]")

    except NoUpgradesAvailableError:
        console.print(
            Panel.fit(
                "[bold green]✔ All dependencies are already up to date![/bold green]\n\n"
                "[dim]No new upgradable versions were discovered in the package registry.\n"
                "Your repository is running the latest available releases.\n"
                "AMstraLift will check for updates again once a new version is published.[/dim]",
                title="Ecosystem Status",
                border_style="green",
            )
        )
        raise typer.Exit(code=0)
    except StageBPublishError as e:
        console.print(f"[bold red]✖ Upgrade failed:[/bold red] {e}\n")
        if e.gate_summary and e.gate_summary.results:
            gate_table = Table(title="Build & Test Gates (Stage A Verification)", show_header=True, header_style="bold red")
            gate_table.add_column("Gate")
            gate_table.add_column("Command")
            gate_table.add_column("Status")
            gate_table.add_column("Diagnostic Details / Reason")
            for g in e.gate_summary.results:
                reason = (g.stdout or g.stderr or f"completed in {g.duration_seconds:.2f}s").strip().replace("\r", "").split("\n")[0]
                gate_table.add_row(g.name, g.command, g.status.value, reason[:80])
            console.print(gate_table)
            console.print(
                "\n[yellow]💡 Tip: In a live run (without --dry-run), all required gates must pass in the sandbox before a branch/PR can be created.[/yellow]\n"
                "[dim]To inspect proposed package changes without enforcing gates, run with '--dry-run'.[/dim]"
            )
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]✖ Upgrade failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command("init-ci")
def init_ci(
    provider: Annotated[str, typer.Option("--provider", "-p", help="CI provider: 'github' or 'gitlab'.")] = "github",
    variant: Annotated[str, typer.Option("--variant", help="Template variant: 'scheduled' or 'reusable'.")] = "scheduled",
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Target repository directory.")] = Path("."),
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing configuration file.")] = False,
):
    """Generate turnkey CI workflow configuration in the target repository."""
    from amstralift.templates.loader import generate_ci_template

    try:
        out_file = generate_ci_template(
            target_dir=repo,
            provider=provider,
            variant=variant,
            overwrite=overwrite,
        )
        console.print(f"[bold green]✔ CI template generated successfully:[/bold green] [cyan]{out_file}[/cyan]")
    except Exception as e:
        console.print(f"[bold red]✖ Failed to generate CI template:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def audit(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Target repository directory to audit.")] = Path("."),
    ecosystem: Annotated[str | None, typer.Option("--ecosystem", "-e", help="Ecosystem override: 'angular', 'react', 'dotnet', 'python'.")] = None,
    branch: Annotated[str | None, typer.Option("--branch", "-b", help="Target git branch to branch from.")] = None,
    fix: Annotated[bool, typer.Option("--fix", help="Automatically apply minimal surgical security patches.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate fixes without modifying the repository.")] = False,
    publish: Annotated[bool, typer.Option("--publish", help="Publish pull request to remote Git provider.")] = False,
    token: Annotated[str | None, typer.Option("--token", "-t", help="Git provider authentication token.")] = None,
):
    """Scan dependencies for security vulnerabilities (CVEs) via OSV.dev and apply surgical patches."""
    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    from amstralift.governance.vulnerabilities import VulnerabilitySeverity
    from amstralift.security.osv_client import OSVClient
    from amstralift.security.patch_planner import VulnerabilityPatchPlanner

    orchestrator = UpgradeOrchestrator()
    try:
        eco_name = ecosystem or orchestrator.auto_detect_ecosystem(repo)
        adapter = orchestrator.get_adapter(eco_name)
    except Exception as e:
        console.print(f"[bold red]✖ Error detecting ecosystem:[/bold red] {e}")
        raise typer.Exit(code=1)

    deps = adapter.get_declared_dependencies(repo)
    if not deps:
        console.print(f"[yellow]No declared dependencies found to audit in {repo.resolve()}[/yellow]")
        raise typer.Exit(code=0)

    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift Security Auditor[/bold blue] - Scanning [cyan]{repo.resolve()}[/cyan] ({eco_name})\n"
            f"[dim]Dependencies declared: {len(deps)} | Fix Mode: {fix} | Dry-run: {dry_run}[/dim]",
            border_style="blue",
        )
    )

    scanner = OSVClient()
    with console.status(f"[bold cyan]Querying Google OSV.dev vulnerability database across {len(deps)} packages..."):
        report = scanner.scan_dependencies(deps, ecosystem=eco_name, repo_path=str(repo.resolve()))

    if not report.findings:
        console.print(
            Panel.fit(
                f"[bold green]✔ Zero Vulnerabilities Found![/bold green]\n\n"
                f"[dim]All {report.scanned_packages_count} scanned dependencies are clean according to OSV database.\n"
                f"No known security advisories affecting this repository.[/dim]",
                title="Security Audit Passed",
                border_style="green",
            )
        )
        raise typer.Exit(code=0)

    # Render Vulnerabilities Table
    table = Table(
        title=f"Security Vulnerabilities Detected ({len(report.findings)} advisories across {report.vulnerable_packages_count} packages)",
        show_header=True,
        header_style="bold red",
    )
    table.add_column("Advisory / CVE", style="bold")
    table.add_column("Package")
    table.add_column("Installed")
    table.add_column("Severity")
    table.add_column("Fixed Version", style="bold green")
    table.add_column("Summary")

    for f in report.findings:
        sev_color = (
            "bold red"
            if f.severity == VulnerabilitySeverity.CRITICAL
            else (
                "red"
                if f.severity == VulnerabilitySeverity.HIGH
                else ("yellow" if f.severity == VulnerabilitySeverity.MEDIUM else "cyan")
            )
        )
        fix_str = f.fixed_version if f.fixed_version else "[red]No patch yet[/red]"
        summary_short = f.summary[:60] + "..." if len(f.summary) > 60 else f.summary
        table.add_row(
            f.cve_id,
            f.package_name,
            f.current_version,
            f"[{sev_color}]{f.severity.value}[/{sev_color}]",
            fix_str,
            summary_short,
        )

    console.print(table)

    if not fix:
        console.print(
            "\n[yellow]💡 Tip: Run 'amstralift audit --fix' to automatically apply minimal surgical patches, "
            "verify compilation/tests in Stage A sandbox, and create security fix pull requests.[/yellow]\n"
        )
        raise typer.Exit(code=1 if report.has_critical_or_high else 0)

    # Fix Mode: Plan surgical patches
    surgical_changes = VulnerabilityPatchPlanner.plan_remediation(report)
    if not surgical_changes:
        console.print("[yellow]⚠ None of the detected vulnerabilities have an upstream fix available yet.[/yellow]")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]Initiating surgical remediation for {len(surgical_changes)} vulnerable packages...[/bold green]\n"
    )

    effective_branch = branch
    if not effective_branch:
        active = get_active_branch(repo)
        manifest_names = ("package.json", "pyproject.toml", "requirements.txt")
        has_manifest = any(
            run_git(["cat-file", "-e", f"{active}:{m}"], cwd=repo).returncode == 0
            for m in manifest_names
        )
        if not has_manifest:
            for candidate in ("master", "main"):
                if candidate != active and run_git(["rev-parse", "--verify", candidate], cwd=repo).returncode == 0:
                    if any(
                        run_git(["cat-file", "-e", f"{candidate}:{m}"], cwd=repo).returncode == 0
                        for m in manifest_names
                    ):
                        active = candidate
                        break
        effective_branch = active or "main"

    try:
        with console.status("[bold green]Executing Stage A sandbox verification & Stage B publisher..."):
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=repo,
                ecosystem=eco_name,
                target_branch=effective_branch,
                dry_run=dry_run,
                publish=publish,
                git_token=token,
                explicit_changes=surgical_changes,
            )

        console.print("[bold green]✔ Security remediation completed successfully![/bold green]\n")
        if not dry_run:
            console.print(
                f"[bold green]✔ Committed surgical security patch to branch:[/bold green] [cyan]{pr_proposal.branch_name}[/cyan]\n"
                f"[dim]Run 'git checkout {pr_proposal.branch_name}' to inspect the remediated dependencies.[/dim]\n"
            )

        gate_table = Table(title="Build & Test Verification Gates", show_header=True, header_style="bold green")
        gate_table.add_column("Gate")
        gate_table.add_column("Command")
        gate_table.add_column("Status")
        gate_table.add_column("Duration")
        for g in signed_bundle.bundle.gate_summary.results:
            gate_table.add_row(g.name, g.command, g.status.value, f"{g.duration_seconds:.2f}s")
        console.print(gate_table)

    except Exception as e:
        console.print(f"[bold red]✖ Security remediation failed verification:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def version():
    """Display AMstraLift version."""
    from amstralift import __version__

    console.print(f"AMstraLift v{__version__}")


if __name__ == "__main__":
    app()
