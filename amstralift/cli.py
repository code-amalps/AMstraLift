"""CLI entry points for AMstraLift using Typer and Rich."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from amstralift.service import UpgradeOrchestrator

app = typer.Typer(
    name="amstralift",
    help="AMstraLift: Automated Dependency & Framework Upgrade Engine with an explicit trust boundary.",
    add_completion=False,
)
console = Console()


@app.command()
def run(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Path to target repository.")] = Path("."),
    ecosystem: Annotated[str | None, typer.Option("--ecosystem", "-e", help="Target ecosystem (angular, python, dotnet, react). Auto-detected if omitted.")] = None,
    branch: Annotated[str, typer.Option("--branch", "-b", help="Target base branch name.")] = "main",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate without modifying git branch/commit.")] = False,
    publish: Annotated[bool, typer.Option("--publish", help="Push branch and open reviewable PR on remote Git provider.")] = False,
    token: Annotated[str | None, typer.Option("--token", envvar="GITHUB_TOKEN", help="Least-privilege Git provider access token.")] = None,
    repo_id: Annotated[str | None, typer.Option("--repo-id", help="Repository identifier (e.g. owner/repo).")] = None,
    remote_url: Annotated[str | None, typer.Option("--remote-url", help="Explicit Git remote URL for push.")] = None,
    output_bundle: Annotated[
        Path | None, typer.Option("--output-bundle", help="Save signed bundle JSON to disk.")
    ] = None,
):
    """Run full two-stage upgrade workflow on a target repository."""
    eco_str = ecosystem or "auto-detect"
    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift[/bold blue] - Initiating upgrade for [cyan]{repo.resolve()}[/cyan] ({eco_str})\n"
            f"[dim]Publish: {publish} | Dry-run: {dry_run}[/dim]",
            border_style="blue",
        )
    )

    orchestrator = UpgradeOrchestrator()

    try:
        with console.status("[bold green]Executing Stage A sandbox & Stage B publisher..."):
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=repo,
                ecosystem=ecosystem,
                target_branch=branch,
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

    except Exception as e:
        console.print(f"[bold red]✖ Upgrade failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def version():
    """Display AMstraLift version."""
    from amstralift import __version__

    console.print(f"AMstraLift v{__version__}")


if __name__ == "__main__":
    app()
