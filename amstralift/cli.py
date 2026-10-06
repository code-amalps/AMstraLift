"""CLI entry points for AMstraLift using Typer and Rich."""

import sys
from pathlib import Path
from typing import Annotated


def suppress_console_for_gui() -> None:
    """Suppress the Windows console window when AMstraLift is running in desktop GUI mode.

    If the executable was launched by double-clicking in File Explorer, a desktop shortcut,
    or the Start Menu, Windows automatically allocates a console window because the PE header
    specifies the console subsystem. This function hides that console window immediately so
    that only the graphical application window appears on screen.

    If AMstraLift was launched from an interactive terminal (CMD, PowerShell, Git Bash), the
    terminal is preserved so command output and logs remain visible.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        # If user passed explicit CLI subcommands (run, audit, etc.), do not suppress console
        args = [a.lower() for a in sys.argv[1:]]
        cli_commands = {"run", "audit", "projects", "topology", "version", "--help", "-h", "governance", "report", "package", "pack"}
        if any(a in cli_commands for a in args):
            return

        is_gui = len(args) == 0 or any(a in ("gui", "--gui") for a in args)
        if not is_gui:
            return

        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return

        # Check processes attached to this console
        pids = (wintypes.DWORD * 32)()
        count = kernel32.GetConsoleProcessList(pids, 32)
        if count <= 2:
            # Dedicated console allocated by Windows for this process; hide it immediately
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
            return

        # Check if any attached process is an interactive shell
        interactive_shells = {
            "cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe",
            "zsh.exe", "sh.exe", "wt.exe", "windowsterminal.exe",
        }
        has_interactive_shell = False
        process_query_limited = 0x1000
        current_pid = kernel32.GetCurrentProcessId()
        for i in range(count):
            pid = pids[i]
            if pid == current_pid:
                continue
            h_proc = kernel32.OpenProcess(process_query_limited, False, pid)
            if h_proc:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                    proc_name = buf.value.split("\\")[-1].lower()
                    if proc_name in interactive_shells:
                        has_interactive_shell = True
                kernel32.CloseHandle(h_proc)
            if has_interactive_shell:
                break

        if not has_interactive_shell:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


# Run early console suppression before loading heavy dependencies
suppress_console_for_gui()

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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    gui_flag: Annotated[bool, typer.Option("--gui", help="Launch interactive graphical user interface.")] = False,
):
    """AMstraLift: Automated Dependency & Framework Upgrade Engine."""
    if ctx.invoked_subcommand is None:
        from amstralift.gui import launch_gui
        launch_gui()


@app.command()
def gui():
    """Launch interactive desktop GUI."""
    from amstralift.gui import launch_gui
    launch_gui()


@app.command()
def run(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Path to target repository.")] = Path("."),
    ecosystem: Annotated[str | None, typer.Option("--ecosystem", "-e", help="Target ecosystem (angular, python, dotnet, react). Auto-detected if omitted.")] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            "-p",
            help="Relative subproject path within a monorepo (e.g. 'client', 'server', 'frontend', 'backend').",
        ),
    ] = None,
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
    output_branch: Annotated[
        str | None,
        typer.Option(
            "--output-branch",
            help="Target branch to commit the upgrade onto (defaults to staying on current amstralift/* branch, or generating an upgrade branch).",
        ),
    ] = None,
    draft_on_fail: Annotated[
        bool,
        typer.Option(
            "--draft-on-fail",
            help="If verification gates fail, commit as a draft branch with smart diagnostics for senior review.",
        ),
    ] = False,
    modernize: Annotated[
        str | None,
        typer.Option(
            "--modernize",
            "-m",
            help="Comma-separated list of post-upgrade modernizations (e.g. 'control-flow', 'standalone', 'style', 'all').",
        ),
    ] = None,
    remediate_cves: Annotated[
        bool,
        typer.Option(
            "--remediate-cves",
            help="Automatically discover and safely remediate known direct dependency CVEs during migration.",
        ),
    ] = True,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            "--corporate-safe",
            help="Run in corporate/offline safe mode (skips external CVE lookups).",
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
        prefix = f"{project}/" if project else ""
        manifest_names = (f"{prefix}package.json", f"{prefix}pyproject.toml", f"{prefix}requirements.txt")
        has_manifest = any(
            run_git(["cat-file", "-e", f"{active}:{m}"], cwd=repo).returncode == 0
            for m in manifest_names
        )
        if not has_manifest or (active and active.startswith("amstralift/")):
            # Fallback to base development branch (Dev, main, master) if current branch lacks manifests or is an amstralift branch
            for candidate in ("Dev", "main", "master"):
                if candidate != active and run_git(["rev-parse", "--verify", candidate], cwd=repo).returncode == 0:
                    if any(
                        run_git(["cat-file", "-e", f"{candidate}:{m}"], cwd=repo).returncode == 0
                        for m in manifest_names
                    ):
                        active = candidate
                        break
        effective_branch = active

    eco_str = ecosystem or "auto-detect"
    proj_str = f" | Subproject: [yellow]{project}[/yellow]" if project else ""
    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift[/bold blue] - Initiating upgrade for [cyan]{repo.resolve()}[/cyan] ({eco_str})\n"
            f"[dim]Branch: {effective_branch} | Publish: {publish} | Dry-run: {dry_run}{proj_str}[/dim]",
            border_style="blue",
        )
    )

    orchestrator = UpgradeOrchestrator(incremental=incremental)

    modernize_list = [x.strip() for x in modernize.split(",") if x.strip()] if modernize else None

    try:
        with console.status("[bold green]Executing Stage A sandbox & Stage B publisher..."):
            signed_bundle, pr_proposal = orchestrator.run_upgrade(
                repo_path=repo,
                ecosystem=ecosystem,
                subproject=project,
                target_branch=effective_branch,
                dry_run=dry_run,
                publish=publish,
                git_token=token,
                repo_id=repo_id,
                remote_url=remote_url,
                allow_failed_gates=allow_failed_gates,
                test_timeout=float(test_timeout),
                output_branch=output_branch,
                draft_on_fail=draft_on_fail,
                modernize=modernize_list,
                remediate_cves=remediate_cves and not offline,
            )

        console.print("[bold green]✔ Upgrade workflow completed successfully![/bold green]\n")
        if not dry_run:
            if pr_proposal.publish_status in ("DRAFT_COMMITTED", "DRAFT_PUBLISHED"):
                console.print(
                    Panel.fit(
                        f"[bold yellow]⚠️ Verification Interrupted — Created DRAFT branch:[/bold yellow] [cyan]{pr_proposal.branch_name}[/cyan]\n\n"
                        f"[dim]Automated gates did not fully pass, but your migration progress was safely preserved in a draft branch.\n"
                        f"Check out this branch to inspect the failing file or hand off to a senior engineer:[/dim]\n"
                        f"  [bold cyan]git checkout {pr_proposal.branch_name}[/bold cyan]\n",
                        title="Draft Migration Handoff",
                        border_style="yellow",
                    )
                )
            elif pr_proposal.publish_status in ("ALREADY_COMMITTED", "ALREADY_APPLIED"):
                console.print(
                    f"[bold green]✔ Branch [cyan]{pr_proposal.branch_name}[/cyan] is already up to date with this upgrade![/bold green]\n"
                    f"[yellow]To continue upgrading to the next major version, checkout this branch and run AMstraLift again:[/yellow]\n"
                    f"  [bold cyan]git checkout {pr_proposal.branch_name}[/bold cyan]\n"
                    f"  [bold cyan]uv run amstralift run[/bold cyan]\n"
                )
            else:
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

        # Display Automated Modernizations
        if getattr(signed_bundle.bundle, "modernizations", None):
            mod_table = Table(title="Automated Modernizations Applied", show_header=False, border_style="cyan")
            mod_table.add_column("Modernization")
            for m in signed_bundle.bundle.modernizations:
                mod_table.add_row(f"[bold cyan]⚡[/bold cyan] {m}")
            console.print(mod_table)

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
        if getattr(e, "diagnostic", None):
            from amstralift.core.diagnostics import format_diagnostic_panel

            console.print(format_diagnostic_panel(e.diagnostic))
            console.print(
                "\n[yellow]💡 Tip: You can re-run with '--draft-on-fail' to preserve your progress on a draft branch for senior review:[/yellow]\n"
                f"  [bold cyan]uv run amstralift run --draft-on-fail[/bold cyan]\n"
            )
        elif e.gate_summary and e.gate_summary.results:
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


@app.command("projects")
@app.command("topology")
def projects(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Target repository directory.")] = Path("."),
    depth: Annotated[int, typer.Option("--depth", "-d", help="Maximum directory scan depth.")] = 3,
):
    """Discover and display all projects in a monorepo or polyglot repository."""
    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    from amstralift.core.monorepo import MonorepoScanner

    topo = MonorepoScanner.discover(repo, max_depth=depth)
    if not topo.projects:
        console.print(f"[yellow]No recognized projects (Angular, React, .NET, Python) found in {repo.resolve()}[/yellow]")
        raise typer.Exit(code=0)

    title = f"Monorepo Topology ({len(topo.projects)} projects discovered)" if topo.is_monorepo else "Repository Project Topology"
    table = Table(
        title=title,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Project Name", style="bold")
    table.add_column("Ecosystem", style="green")
    table.add_column("Framework Version", style="yellow")
    table.add_column("Subproject Path", style="cyan")
    table.add_column("Manifest File", style="dim")

    for p in topo.projects:
        fw = p.framework_version or "latest"
        table.add_row(
            p.name,
            p.ecosystem.upper(),
            fw,
            p.rel_path,
            p.manifest_file,
        )

    console.print(table)
    if topo.is_monorepo:
        console.print(
            f"\n[bold green]✔ Polyglot Monorepo detected![/bold green] Ecosystems: [cyan]{', '.join(topo.ecosystems)}[/cyan]\n"
            f"[dim]To target a specific project, run: [/dim][bold cyan]amstralift run --project <path>[/bold cyan] [dim]or [/dim][bold cyan]amstralift audit --project <path>[/bold cyan]"
        )


@app.command()
def audit(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Target repository directory to audit.")] = Path("."),
    ecosystem: Annotated[str | None, typer.Option("--ecosystem", "-e", help="Ecosystem override: 'angular', 'react', 'dotnet', 'python'.")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Relative subproject path within a monorepo (e.g. 'client', 'server', 'frontend', 'backend').")] = None,
    mode: Annotated[str, typer.Option("--mode", "-m", help="Remediation mode: 'audit' (scan only), 'preview' (plan only), or 'apply' (sandbox remediation).")] = "audit",
    fix: Annotated[bool, typer.Option("--fix", help="Alias for --mode apply.")] = False,
    allow_major: Annotated[bool, typer.Option("--allow-major", help="Allow applying breaking major version upgrades during remediation.")] = False,
    safe_only: Annotated[bool, typer.Option("--safe-only/--all-upgrades", help="Apply only safe backward-compatible fixes and skip breaking major leaps.")] = True,
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
                subproject=project,
                mode=effective_mode,
                target_branch=branch,
                dry_run=dry_run,
                publish=publish,
                git_token=token,
                allow_major=allow_major,
                safe_only=safe_only,
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
def serve(
    host: Annotated[str, typer.Option("--host", "-h", help="Host interface to bind the API server to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to bind the API server to.")] = 8000,
):
    """Start the AMstraLift REST API server with interactive Swagger UI."""
    import uvicorn

    from amstralift.api.app import create_app

    console.print(
        Panel.fit(
            f"[bold blue]AMstraLift REST API[/bold blue]\n\n"
            f"Server running at: [bold cyan]http://{host}:{port}[/bold cyan]\n"
            f"Interactive Swagger UI: [bold green]http://{host}:{port}/docs[/bold green]\n"
            f"ReDoc Documentation: [bold magenta]http://{host}:{port}/redoc[/bold magenta]\n"
            f"[dim]Press Ctrl+C to stop.[/dim]",
            title="Service Online",
            border_style="blue",
        )
    )

    api_app = create_app()
    uvicorn.run(api_app, host=host, port=port)


@app.command()
def version():
    """Display AMstraLift version and author attribution."""
    from amstralift import __author__, __license__, __version__

    console.print(f"AMstraLift v{__version__} — Engineered by {__author__} ({__license__})")


if __name__ == "__main__":
    app()
