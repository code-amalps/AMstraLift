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
            "--skip-tests",
            help="Allow Stage B to commit the upgrade branch locally even if sandbox verification gates fail or time out.",
        ),
    ] = False,
    test_timeout: Annotated[
        int,
        typer.Option(
            "--test-timeout",
            help="Timeout in seconds for each build/test gate (default: 300).",
        ),
    ] = 300,
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
            "You specified --publish. This will push a new branch to the remote repository and open a Pull Request. Continue?",
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
                test_timeout=float(test_timeout),
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
        raise typer.Exit(code=0) from None
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

            if e.gate_summary.has_required_timeouts and not e.gate_summary.has_required_failures:
                console.print(
                    "\n[yellow]⏱ Test gate timed out — the test runner is likely in watch mode or Chrome is unavailable.[/yellow]\n"
                    "[dim]AMstraLift injected --no-watch --browsers=ChromeHeadless for Karma/ng test, but Chrome must be installed.[/dim]\n"
                    "[dim]• Install chromium:  sudo apt-get install -y chromium  (Linux) or brew install chromium (macOS)[/dim]\n"
                    "[dim]• Then re-run to get a verified result, OR use '--allow-failed-gates' to proceed with unverified tests.[/dim]\n"
                )
            else:
                console.print(
                    "\n[yellow]💡 Tip: In a live run (without --dry-run), all required gates must pass in the sandbox before a branch/PR can be created.[/yellow]\n"
                    "[dim]To inspect proposed package changes without enforcing gates, run with '--dry-run'.[/dim]"
                )
        raise typer.Exit(code=1) from e
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
    mode: Annotated[str, typer.Option("--mode", "-m", help="Remediation mode: 'audit' (scan only), 'preview' (plan only), or 'apply' (sandbox remediation).")] = "audit",
    fix: Annotated[bool, typer.Option("--fix", help="Alias for --mode apply.")] = False,
    policy: Annotated[Path | None, typer.Option("--policy", "-p", help="Path to organizational security policy YAML.")] = None,
    branch: Annotated[str | None, typer.Option("--branch", "-b", help="Target git branch to branch from.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate fixes without modifying the repository.")] = False,
    publish: Annotated[bool, typer.Option("--publish", help="Publish pull request to remote Git provider.")] = False,
    token: Annotated[str | None, typer.Option("--token", "-t", help="Git provider authentication token.")] = None,
):
    """Scan dependencies for security vulnerabilities (CVEs) and safely remediate them across Angular, React, .NET, and Python."""
    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    effective_mode = "apply" if fix else mode.lower().strip()
    if effective_mode not in ("audit", "preview", "apply"):
        console.print(f"[bold red]✖ Error:[/bold red] Invalid mode '{mode}'. Supported modes: audit, preview, apply.")
        raise typer.Exit(code=1)

    from amstralift.security.orchestrator import SecurityOrchestrator
    from amstralift.security.ui import SecurityUI

    orchestrator = SecurityOrchestrator(policy_path=policy)

    with console.status(f"[bold cyan]Scanning repository and resolving dependency graph ({effective_mode} mode)..."):
        try:
            result = orchestrator.run_remediation(
                repo_path=repo,
                ecosystem=ecosystem,
                mode=effective_mode,
                target_branch=branch,
                dry_run=dry_run,
                publish=publish,
                git_token=token,
            )
        except Exception as e:
            console.print(f"[bold red]✖ Security audit failed:[/bold red] {e}")
            raise typer.Exit(code=1) from e

    # 1. Always render the Vulnerability Dashboard
    SecurityUI.render_dashboard(result.report, console)

    # 2. Render Preview Plan if preview or apply mode
    if effective_mode in ("preview", "apply") and result.plan:
        SecurityUI.render_preview(result.plan, console)

    # 3. Render Execution & Verification Results if apply mode
    if effective_mode == "apply":
        SecurityUI.render_result(result, console)
        if not result.remediation_successful:
            raise typer.Exit(code=1)
        raise typer.Exit(code=0)

    # Mode advice for audit mode
    if effective_mode == "audit" and result.report.findings:
        console.print(
            "\n[yellow]💡 Tip: Run 'amstralift audit --mode preview' to preview compatible upgrade plans, "
            "or 'amstralift audit --mode apply' (or --fix) to execute sandbox verification gates and create fix branches.[/yellow]\n"
        )
        raise typer.Exit(code=1 if result.report.has_critical_or_high else 0)

    raise typer.Exit(code=0)


@app.command()
def version():
    """Display AMstraLift version."""
    from amstralift import __version__

    console.print(f"AMstraLift v{__version__}")


if __name__ == "__main__":
    app()
