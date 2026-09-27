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
    ecosystem: Annotated[str, typer.Option("--ecosystem", "-e", help="Target ecosystem (e.g. angular).")] = "angular",
    branch: Annotated[str, typer.Option("--branch", "-b", help="Target branch name.")] = "main",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate without creating git branch/commit.")] = False,
    output_bundle: Annotated[
        Path | None, typer.Option("--output-bundle", help="Save signed bundle JSON to disk.")
    ] = None,
):
    """Run full two-stage upgrade workflow on a target repository."""
    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift[/bold blue] - Initiating upgrade for [cyan]{repo.resolve()}[/cyan] ({ecosystem})",
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
        console.print(
            Panel(
                f"[bold]Branch:[/bold] {pr_proposal.branch_name}\n"
                f"[bold]Title:[/bold] {pr_proposal.title}\n"
                f"[bold]Labels:[/bold] {', '.join(pr_proposal.labels)}\n"
                f"[bold]24h SLA Deadline:[/bold] {pr_proposal.deadline_24h.isoformat()}\n"
                f"[bold]Base SHA:[/bold] {pr_proposal.base_commit_sha}\n"
                f"[bold]Dry Run:[/bold] {dry_run}",
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
