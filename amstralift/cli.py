"""CLI entry points for AMstraLift using Typer and Rich."""

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from amstralift.core.workspace import get_active_branch, run_git
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

    orchestrator = UpgradeOrchestrator()

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
            )

        console.print("[bold green]✔ Upgrade workflow completed successfully![/bold green]\n")

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
def version():
    """Display AMstraLift version."""
    from amstralift import __version__

    console.print(f"AMstraLift v{__version__}")


if __name__ == "__main__":
    app()
