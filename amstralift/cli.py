"""CLI entry points for AMstraLift using Typer and Rich."""

import sys
from pathlib import Path
from typing import Annotated, Optional


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
        cli_commands = {
            "run",
            "audit",
            "projects",
            "topology",
            "version",
            "--help",
            "-h",
            "governance",
            "report",
            "package",
            "pack",
            "init",
            "doctor",
            "self-update",
            "update",
            "serve",
            "init-ci",
            "graph",
            "plan",
            "conflicts",
            "resume",
            "checkpoints",
            "policy",
            "fleet",
            "multirepo",
            "campaign",
        }
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
def init(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="Target directory to initialize and scaffold.")] = Path("."),
    ecosystem: Annotated[
        str,
        typer.Option(
            "--ecosystem",
            "-e",
            help="Ecosystem starter template: 'angular', 'react', 'dotnet', or 'python'.",
        ),
    ] = "angular",
    force: Annotated[bool, typer.Option("--force", "-f", help="Force scaffolding even if directory is not empty.")] = False,
):
    """Initialize a Git repository and scaffold a starter project ready for AMstraLift modernization."""
    from amstralift.core.scaffold import detect_repository_status, scaffold_starter_project

    status = detect_repository_status(repo)
    if not status["is_empty"] and not force:
        console.print(
            f"[yellow]⚠️ Warning:[/yellow] Directory [cyan]{repo.resolve()}[/cyan] is not empty.\n"
            f"[dim]Existing files may conflict. Use --force to proceed anyway.[/dim]"
        )
        if not typer.confirm("Do you wish to proceed?", default=False):
            raise typer.Exit(code=0)

    try:
        created = scaffold_starter_project(repo, ecosystem=ecosystem)
        console.print(
            Panel.fit(
                f"[bold green]✔ Git repository initialized and {ecosystem.upper()} starter project scaffolded![/bold green]\n\n"
                f"[dim]Created {len(created)} files in [cyan]{repo.resolve()}[/cyan]:\n"
                + "\n".join(f"  • {f}" for f in created[:8])
                + (f"\n  • ...and {len(created) - 8} more" if len(created) > 8 else "")
                + "\n\n[bold]Next Step:[/bold]\n"
                f"  Run [bold cyan]amstralift run[/bold cyan] (or [bold cyan]amstralift audit[/bold cyan]) to modernize this project!",
                title="Repository Initialized",
                border_style="green",
            )
        )
    except Exception as exc:
        console.print(f"[bold red]✖ Failed to initialize starter project:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


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
            help="Comma-separated list of post-upgrade modernizations (e.g. 'control-flow', 'standalone', 'signals', 'inject', 'self-closing', 'style', 'all').",
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
    ignore_scripts: Annotated[
        bool,
        typer.Option(
            "--ignore-scripts/--no-ignore-scripts",
            help="Block third-party lifecycle scripts (preinstall/postinstall) during Stage A workspace package resolution.",
        ),
    ] = True,
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

    from amstralift.core.scaffold import detect_repository_status, scaffold_starter_project

    status = detect_repository_status(repo)
    if status["is_empty"]:
        console.print(
            f"[yellow]⚠️ Directory [cyan]{repo.resolve()}[/cyan] is empty — no Git repository or project files found.[/yellow]\n"
        )
        should_init = yes
        if not should_init:
            try:
                should_init = typer.confirm(
                    "Would you like AMstraLift to initialize Git and scaffold a starter project now?",
                    default=False,
                )
            except Exception:
                should_init = False
        if should_init:
            chosen_eco = ecosystem or "angular"
            created = scaffold_starter_project(repo, ecosystem=chosen_eco)
            console.print(
                f"[bold green]✔ Scaffolded {chosen_eco.upper()} starter project ({len(created)} files committed).[/bold green]\n"
            )
        else:
            console.print("[dim]Tip: Run 'amstralift init' to scaffold a starter project here, or specify an existing repository.[/dim]")
            raise typer.Exit(code=1)
    elif not status["root_ecosystem"] and not status["subprojects"] and not status["is_git"]:
        console.print(
            f"[bold red]✖ No supported Git repository or project manifests found in:[/bold red] [cyan]{repo.resolve()}[/cyan]\n"
            f"[dim]AMstraLift supports Angular, React, .NET, and Python repositories.\n"
            f"Tip: Run [bold cyan]amstralift init[/bold cyan] to scaffold a starter repository here.[/dim]"
        )
        raise typer.Exit(code=1)
    elif not status["root_ecosystem"] and status["subprojects"] and not project:
        if len(status["subprojects"]) == 1:
            project = status["subprojects"][0].rel_path
            console.print(
                f"[bold cyan]ℹ Auto-detected subproject:[/bold cyan] [yellow]{project}[/yellow] ({status['subprojects'][0].ecosystem.upper()})"
            )
        else:
            console.print(
                f"[yellow]ℹ Multiple subprojects discovered in [cyan]{repo.resolve()}[/cyan]:[/yellow]"
            )
            for sp in status["subprojects"]:
                console.print(f"  • [bold cyan]{sp.rel_path}[/bold cyan] ({sp.ecosystem.upper()})")
            console.print(
                f"\n[yellow]Please specify which subproject to target using [bold cyan]--project <path>[/bold cyan][/yellow]"
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
                ignore_scripts=ignore_scripts,
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

    from amstralift.core.scaffold import detect_repository_status, scaffold_starter_project

    status = detect_repository_status(repo)
    if status["is_empty"]:
        console.print(
            f"[yellow]⚠️ Directory [cyan]{repo.resolve()}[/cyan] is empty — no Git repository or project files found.[/yellow]\n"
        )
        should_init = False
        try:
            should_init = typer.confirm(
                "Would you like AMstraLift to initialize Git and scaffold a starter project now?",
                default=False,
            )
        except Exception:
            should_init = False
        if should_init:
            chosen_eco = ecosystem or "angular"
            created = scaffold_starter_project(repo, ecosystem=chosen_eco)
            console.print(
                f"[bold green]✔ Scaffolded {chosen_eco.upper()} starter project ({len(created)} files committed).[/bold green]\n"
            )
        else:
            console.print("[dim]Tip: Run 'amstralift init' to scaffold a starter project here, or specify an existing repository.[/dim]")
            raise typer.Exit(code=1)
    elif not status["root_ecosystem"] and not status["subprojects"] and not status["is_git"]:
        console.print(
            f"[bold red]✖ No supported Git repository or project manifests found in:[/bold red] [cyan]{repo.resolve()}[/cyan]\n"
            f"[dim]AMstraLift supports Angular, React, .NET, and Python repositories.\n"
            f"Tip: Run [bold cyan]amstralift init[/bold cyan] to scaffold a starter repository here.[/dim]"
        )
        raise typer.Exit(code=1)
    elif not status["root_ecosystem"] and status["subprojects"] and not project:
        if len(status["subprojects"]) == 1:
            project = status["subprojects"][0].rel_path
            console.print(
                f"[bold cyan]ℹ Auto-detected subproject:[/bold cyan] [yellow]{project}[/yellow] ({status['subprojects'][0].ecosystem.upper()})"
            )
        else:
            console.print(
                f"[yellow]ℹ Multiple subprojects discovered in [cyan]{repo.resolve()}[/cyan]:[/yellow]"
            )
            for sp in status["subprojects"]:
                console.print(f"  • [bold cyan]{sp.rel_path}[/bold cyan] ({sp.ecosystem.upper()})")
            console.print(
                f"\n[yellow]Please specify which subproject to audit using [bold cyan]--project <path>[/bold cyan][/yellow]"
            )
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


@app.command("doctor")
def doctor_cmd(
    fix: Annotated[
        bool,
        typer.Option("--fix", "-f", "--heal", help="Automatically repair detected configuration or cache issues."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output diagnostic report as JSON."),
    ] = False,
):
    """Check and self-heal AMstraLift environment, dependencies, and configuration."""
    from amstralift.core.doctor import CheckStatus, DoctorEngine

    engine = DoctorEngine()
    report = engine.diagnose_and_heal(auto_fix=fix)

    if json_output:
        import json
        console.print(json.dumps(report.to_dict(), indent=2))
        raise typer.Exit(code=1 if report.has_errors else 0)

    table = Table(
        title="🩺 AMstraLift System Health & Self-Healing Diagnostics",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Category", style="dim", no_wrap=True)
    table.add_column("Check Component", style="bold", no_wrap=True)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Details & Guidance", style="white")

    for item in report.items:
        if item.status == CheckStatus.OK:
            st = "[bold green]✔ OK[/bold green]"
        elif item.status == CheckStatus.REPAIRED:
            st = "[bold cyan]🔧 REPAIRED[/bold cyan]"
        elif item.status == CheckStatus.WARNING:
            st = "[bold yellow]⚠️ WARNING[/bold yellow]"
        else:
            st = "[bold red]✖ ERROR[/bold red]"

        msg = item.message
        if item.details:
            msg += f"\n[dim]{item.details}[/dim]"
        table.add_row(item.category, item.name, st, msg)

    console.print(table)

    if report.repairs_applied:
        repairs_text = "\n".join(f"• {r}" for r in report.repairs_applied)
        console.print(
            Panel(
                f"[bold green]The following automated self-healing repairs were applied:[/bold green]\n\n{repairs_text}",
                title="Self-Healing Applied",
                border_style="green",
            )
        )
    elif not fix and any(i.repairable for i in report.items):
        console.print(
            "\n[dim]Tip: Run [bold cyan]amstralift doctor --fix[/bold cyan] to automatically repair configuration and cache issues.[/dim]"
        )

    if report.has_errors:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command("self-update")
@app.command("update")
def self_update_cmd(
    check_only: Annotated[
        bool,
        typer.Option("--check", "-c", help="Check for available updates without applying."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force reinstall/upgrade even if already on the latest version."),
    ] = False,
    target_version: Annotated[
        Optional[str],
        typer.Option("--version", "-v", help="Specific version of AMstraLift to install."),
    ] = None,
):
    """Check for and automatically install updates to AMstraLift."""
    from amstralift import __version__
    from amstralift.core.self_update import SelfUpdater

    with console.status("[bold green]Querying AMstraLift release registries (PyPI & GitHub)..."):
        check = SelfUpdater.check_for_updates()

    if check_only:
        update_st = "[bold green]Update Available![/bold green]" if check.update_available else "[bold]Up to date[/bold]"
        notes_str = f"\nRelease Notes:\n[dim]{check.release_notes}[/dim]" if check.release_notes else ""
        console.print(
            Panel.fit(
                f"[bold blue]AMstraLift Version Status[/bold blue]\n\n"
                f"Installed Version: [bold]{__version__}[/bold]\n"
                f"Latest Available:  [bold cyan]{check.latest_version}[/bold cyan] ({check.source})\n"
                f"Update Status:     {update_st}\n"
                + notes_str,
                border_style="blue",
            )
        )
        raise typer.Exit(code=0)

    if not check.update_available and not force and not target_version:
        console.print(
            f"[bold green]✔ AMstraLift is already up to date[/bold green] (v{__version__}).\n"
            f"[dim]Use --force to reinstall or re-upgrade.[/dim]"
        )
        raise typer.Exit(code=0)

    target_desc = target_version or check.latest_version
    console.print(f"[bold cyan]Upgrading AMstraLift:[/bold cyan] {__version__} -> {target_desc}...")

    success, msg = SelfUpdater.execute_update(
        target_version=target_version,
        force=force,
        progress_callback=lambda m: console.print(f"  ↳ {m}"),
    )

    if success:
        console.print(f"[bold green]✔ {msg}[/bold green]")
        raise typer.Exit(code=0)
    else:
        console.print(f"[bold red]✖ {msg}[/bold red]")
        raise typer.Exit(code=1)


@app.command("graph")
def graph_cmd(
    ecosystem: Annotated[
        str | None,
        typer.Option("--ecosystem", "-e", help="Filter transformations by ecosystem (angular, dotnet, python, react)."),
    ] = None,
    phases: Annotated[
        bool,
        typer.Option("--phases", help="Display topological execution waves/phases."),
    ] = False,
    batches: Annotated[
        bool,
        typer.Option("--batches", help="Display parallel-safe execution batches with disjoint file patterns."),
    ] = False,
    rollback: Annotated[
        str | None,
        typer.Option("--rollback", help="Display atomic rollback boundary for a specific transformation ID."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output transformation graph as JSON."),
    ] = False,
):
    """Inspect the Generation 2 Transformation Dependency Graph (P16)."""
    import json
    from rich.panel import Panel
    from rich.tree import Tree
    from amstralift.transforms import (
        EdgeType,
        build_default_graph,
        get_transformations_for_ecosystem,
    )

    graph = build_default_graph()

    # Filter by ecosystem if requested
    if ecosystem:
        eco = ecosystem.strip().lower()
        active_nodes = [t.id for t in get_transformations_for_ecosystem(eco)]
        if not active_nodes:
            console.print(f"[bold red]✖ Error:[/bold red] Unknown ecosystem '{ecosystem}'. Supported: angular, dotnet, python, react.")
            raise typer.Exit(code=1)
    else:
        active_nodes = list(graph.nodes.keys())

    subgraph = graph.filter_subgraph(active_nodes)

    if as_json:
        typer.echo(json.dumps(subgraph.to_dict(), indent=2))
        raise typer.Exit(code=0)

    if rollback:
        boundaries = graph.get_rollback_boundaries(active_nodes)
        target = rollback.strip()
        if target not in boundaries:
            console.print(f"[bold red]✖ Error:[/bold red] Transformation ID '{target}' not found.")
            raise typer.Exit(code=1)
        rb_set = sorted(list(boundaries[target]))
        target_node = graph.get_node(target)
        boundary_label = f" (Atomic Boundary: {target_node.atomic_boundary})" if target_node and target_node.atomic_boundary else ""
        console.print(
            Panel(
                f"[bold cyan]Transformation:[/bold cyan] {target}{boundary_label}\n"
                f"[bold yellow]Rollback Blast Radius ({len(rb_set)} transformations):[/bold yellow]\n"
                + "\n".join(f"  • [red]{t}[/red]" for t in rb_set),
                title="Atomic Rollback Boundary (P16)",
                border_style="cyan",
            )
        )
        raise typer.Exit(code=0)

    if phases:
        # Non-conflicting nodes for phase calculation
        conflicts = subgraph.find_conflicts()
        excluded = {c.target_id for c in conflicts if "legacy" in c.target_id} | {c.source_id for c in conflicts if "legacy" in c.source_id}
        valid_nodes = [n for n in active_nodes if n not in excluded]
        wave_list = graph.get_execution_phases(valid_nodes)
        console.print(Panel(f"[bold green]Execution Waves ({len(wave_list)} sequential phases)[/bold green]", border_style="green"))
        for i, wave in enumerate(wave_list):
            table = Table(title=f"Phase {i + 1}", header_style="bold cyan", border_style="dim")
            table.add_column("Transformation ID", style="bold")
            table.add_column("Category", style="yellow")
            table.add_column("Risk", style="green")
            table.add_column("Affected File Patterns", style="dim")
            for nid in wave:
                node = graph.get_node(nid)
                if node:
                    table.add_row(
                        node.id,
                        node.category.value.upper(),
                        node.risk.value.upper(),
                        ", ".join(node.affected_file_patterns) or "(none)",
                    )
            console.print(table)
        raise typer.Exit(code=0)

    if batches:
        conflicts = subgraph.find_conflicts()
        excluded = {c.target_id for c in conflicts if "legacy" in c.target_id} | {c.source_id for c in conflicts if "legacy" in c.source_id}
        valid_nodes = [n for n in active_nodes if n not in excluded]
        batch_list = graph.get_parallel_batches(valid_nodes)
        console.print(Panel(f"[bold green]Parallelizable Execution Batches ({len(batch_list)} batches)[/bold green]", border_style="green"))
        for b in batch_list:
            mode_tag = "[bold green][CONCURRENT][/bold green]" if b.is_concurrent else "[dim][SEQUENTIAL][/dim]"
            console.print(f"Batch #{b.batch_index + 1} {mode_tag}:")
            for nid in b.nodes:
                node = graph.get_node(nid)
                patterns = f" [dim]({', '.join(node.affected_file_patterns)})[/dim]" if node and node.affected_file_patterns else ""
                console.print(f"  ↳ [cyan]{nid}[/cyan]{patterns}")
        raise typer.Exit(code=0)

    # Standard Graph Overview
    table = Table(
        title=f"Transformation Dependency Graph (P16) — {len(active_nodes)} transformations",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Transformation ID", style="bold", no_wrap=True)
    table.add_column("Ecosystem", style="green")
    table.add_column("Category", style="yellow")
    table.add_column("Risk", style="magenta")
    table.add_column("Prerequisites", style="cyan")
    table.add_column("Atomic Boundary", style="dim")

    for nid in sorted(active_nodes):
        node = graph.get_node(nid)
        if not node:
            continue
        prereqs = graph.get_prerequisites(nid, transitive=False)
        prereq_str = ", ".join(sorted(prereqs)) if prereqs else "-"
        boundary_str = node.atomic_boundary or "-"
        table.add_row(
            node.id,
            node.ecosystem.upper(),
            node.category.value.upper(),
            node.risk.value.upper(),
            prereq_str,
            boundary_str,
        )

    console.print(table)
    console.print(
        "\n[dim]Tips: Use [/dim][bold cyan]amstralift graph --phases[/bold cyan][dim] to view sequential waves, [/dim]"
        "[bold cyan]--batches[/bold cyan][dim] for parallel concurrency, or [/dim]"
        "[bold cyan]--rollback <id>[/bold cyan][dim] for atomic rollback boundaries.[/dim]"
    )


@app.command("plan")
def plan_cmd(
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Target repository directory to analyze and plan."),
    ] = Path("."),
    ecosystem: Annotated[
        str | None,
        typer.Option("--ecosystem", "-e", help="Target ecosystem override (angular, dotnet, python, react)."),
    ] = None,
    target: Annotated[
        int | None,
        typer.Option("--target", "-t", help="Target major framework version (e.g. 18, 21, 8)."),
    ] = None,
    output_file: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="File to save the modernization plan (.yaml, .yml, or .json)."),
    ] = None,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Execute the generated modernization plan."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Simulate execution without modifying files."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output modernization plan as JSON."),
    ] = False,
    as_yaml: Annotated[
        bool,
        typer.Option("--yaml", help="Output modernization plan as raw YAML."),
    ] = False,
    policy_file: Annotated[
        Path | None,
        typer.Option("--policy", "-p", help="Path to declarative enterprise policy file (.yaml)."),
    ] = None,
):
    """Generate a declarative modernization plan (P17) based on codebase analysis and the DAG."""
    from rich.panel import Panel
    from amstralift.planning import ModernizationPlanner, PlanExecutor, TransformationStatus

    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    planner = ModernizationPlanner()
    plan = planner.generate_plan(
        repo_path=repo,
        ecosystem=ecosystem,
        target_major=target,
        policy=policy_file,
    )

    if as_json:
        typer.echo(plan.model_dump_json(indent=2))
        raise typer.Exit(code=0)

    if as_yaml:
        typer.echo(plan.to_yaml())
        raise typer.Exit(code=0)

    # Rich Terminal Output
    status_summary = (
        f"[bold green]✔ {plan.safe_count} Safe[/bold green] · "
        f"[bold cyan]↳ {plan.dependent_count} Dependent[/bold cyan] · "
        f"[bold magenta]▲ {plan.risky_count} Risky[/bold magenta] · "
        f"[bold red]✖ {plan.refused_count} Refused[/bold red]"
    )

    console.print(
        Panel(
            f"[bold]Repository:[/bold] {plan.repo_path}\n"
            f"[bold]Ecosystem:[/bold] [cyan]{plan.ecosystem.upper()}[/cyan] (Current: [yellow]{plan.current_version or 'unknown'}[/yellow] → Target: [bold green]{plan.target_version}[/bold green])\n"
            f"[bold]Total Transformations Planned:[/bold] [bold white]{plan.total_transformations}[/bold white]\n"
            f"[bold]Transformation Breakdown:[/bold] {status_summary}",
            title=f"Modernization Plan: {plan.plan_id} (P17)",
            border_style="cyan",
        )
    )

    # Render each phase
    status_badges = {
        TransformationStatus.SAFE: "[bold green]SAFE[/bold green]",
        TransformationStatus.DEPENDENT: "[cyan]DEPENDENT[/cyan]",
        TransformationStatus.RISKY: "[bold magenta]RISKY[/bold magenta]",
        TransformationStatus.REFUSED: "[red]REFUSED[/red]",
        TransformationStatus.SKIPPED: "[dim]SKIPPED[/dim]",
    }

    for phase in plan.phases:
        table = Table(
            title=f"Phase {phase.phase_index + 1}: {phase.name} ({len(phase.transformations)} items)",
            header_style="bold cyan",
            border_style="dim",
        )
        table.add_column("Transformation ID", style="bold", no_wrap=True)
        table.add_column("Status", style="bold")
        table.add_column("Risk", style="green")
        table.add_column("Prerequisites", style="cyan")
        table.add_column("Affected Files", style="dim")
        table.add_column("Rationale", style="italic")

        for t in phase.transformations:
            prereq_str = ", ".join(t.prerequisites) if t.prerequisites else "-"
            files_str = ", ".join(t.affected_file_patterns) if t.affected_file_patterns else "-"
            badge = status_badges.get(t.status, str(t.status))
            table.add_row(
                t.id,
                badge,
                t.risk.value.upper(),
                prereq_str,
                files_str,
                t.reason,
            )
        console.print(table)

    # Render refused transformations if any
    if plan.refused_transformations:
        refused_table = Table(
            title=f"Refused Transformations ({len(plan.refused_transformations)} items)",
            header_style="bold red",
            border_style="red dim",
        )
        refused_table.add_column("Transformation ID", style="bold red", no_wrap=True)
        refused_table.add_column("Category", style="dim")
        refused_table.add_column("Refusal Rationale", style="yellow")
        for rt in plan.refused_transformations:
            refused_table.add_row(rt.id, rt.category.value.upper(), rt.reason)
        console.print(refused_table)

    # Save to file if requested
    if output_file:
        plan.save(output_file)
        console.print(f"[bold green]✔ Modernization plan successfully written to:[/bold green] {output_file.resolve()}")

    conflict_meta = plan.metadata.get("conflict_analysis", {})
    if conflict_meta.get("total_conflicts", 0) > 0:
        c_count = conflict_meta.get("total_conflicts", 0)
        c_discarded = len(conflict_meta.get("discarded_rules", []))
        c_injected = len(conflict_meta.get("injected_orderings", []))
        console.print(
            f"\n[dim]ℹ Pre-execution conflict detector resolved {c_count} conflict(s): "
            f"discarded {c_discarded} incompatible rule(s), injected {c_injected} ordering constraint(s).[/dim]"
        )

    policy_meta = plan.metadata.get("policy_evaluation", {})
    if policy_meta:
        pol_action = policy_meta.get("overall_action", "allow")
        pol_name = policy_meta.get("policy_name", "enterprise-default")
        if pol_action == "block":
            console.print(f"\n[bold red]✖ Enterprise Policy Verdict ({pol_name}): BLOCKED[/bold red] - {policy_meta.get('summary')}")
        elif pol_action == "review":
            console.print(f"\n[bold yellow]⚠️ Enterprise Policy Verdict ({pol_name}): REVIEW REQUIRED[/bold yellow] - {policy_meta.get('summary')}")
        else:
            console.print(f"\n[dim]✔ Enterprise Policy Verdict ({pol_name}): COMPLIANT[/dim]")

    # Execute if requested
    if execute or dry_run:
        if execute and policy_meta.get("overall_action") == "block":
            console.print("[bold red]✖ Execution aborted: Modernization plan violates enterprise policy.[/bold red]")
            raise typer.Exit(code=1)

        executor = PlanExecutor()
        console.print(f"\n[bold cyan]Starting plan execution...[/bold cyan]")
        exec_res = executor.execute_plan(
            plan=plan,
            workspace_path=repo,
            dry_run=dry_run,
            progress_callback=lambda m: console.print(f"  {m}"),
        )
        if exec_res.success:
            console.print(f"[bold green]✔ {exec_res.message}[/bold green]")
        else:
            console.print(f"[bold red]✖ {exec_res.message}[/bold red]")
            raise typer.Exit(code=1)


@app.command("conflicts")
def conflicts_cmd(
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Target repository directory to analyze."),
    ] = Path("."),
    ecosystem: Annotated[
        str | None,
        typer.Option("--ecosystem", "-e", help="Target ecosystem override (angular, dotnet, python, react)."),
    ] = None,
    resolve: Annotated[
        bool,
        typer.Option("--resolve/--no-resolve", help="Simulate automatic conflict resolution."),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output conflict report as raw JSON."),
    ] = False,
):
    """Detect and analyze pre-execution cross-transformation conflicts (P18)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.conflicts import ConflictResolver, ConflictSeverity, PreExecutionConflictDetector
    from amstralift.planning.analyzer import RepoAnalyzer
    from amstralift.transforms.registry import build_default_graph, get_transformations_for_ecosystem

    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    analysis = RepoAnalyzer.analyze(repo, ecosystem_hint=ecosystem)
    eco = analysis.ecosystem.lower()

    graph = build_default_graph()
    candidate_nodes = get_transformations_for_ecosystem(eco)
    active_ids = [n.id for n in candidate_nodes]

    detector = PreExecutionConflictDetector(graph)
    report = detector.detect_all(active_ids, repo_path=repo)

    if resolve:
        resolver = ConflictResolver(graph)
        report = resolver.resolve(report, active_ids)

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        raise typer.Exit(code=0)

    # Rich display
    safe_badge = "[bold green]SAFE TO EXECUTE[/bold green]" if report.safe_to_execute else "[bold red]BLOCKED BY CONFLICTS[/bold red]"
    console.print(
        Panel(
            f"[bold]Target Ecosystem:[/bold] [cyan]{eco.upper()}[/cyan]\n"
            f"[bold]Execution Status:[/bold] {safe_badge}\n"
            f"[bold]Conflicts Found:[/bold] {report.total_conflicts} "
            f"([red]{report.blocking_count} Blocking[/red], [yellow]{report.warning_count} Warnings[/yellow])\n"
            f"[bold]Discarded Rules:[/bold] {', '.join(report.discarded_rules) or 'None'}\n"
            f"[bold]Injected Orderings:[/bold] {len(report.injected_orderings)}",
            title="Pre-Execution Conflict Detection Report (P18)",
            border_style="cyan" if report.safe_to_execute else "red",
        )
    )

    if report.conflicts:
        table = Table(
            title=f"Detected Conflicts ({len(report.conflicts)} items)",
            header_style="bold cyan",
            border_style="dim",
        )
        table.add_column("ID", style="bold", no_wrap=True)
        table.add_column("Type", style="bold")
        table.add_column("Severity", style="magenta")
        table.add_column("Transformations", style="yellow")
        table.add_column("Resource in Dispute", style="dim")
        table.add_column("Resolution Strategy", style="cyan")
        table.add_column("Resolved?", style="bold")

        for c in report.conflicts:
            res_str = "[bold green]YES[/bold green]" if c.resolved else "[bold red]NO[/bold red]"
            sev_str = "[bold red]BLOCKING[/bold red]" if c.severity == ConflictSeverity.BLOCKING else "[yellow]WARNING[/yellow]"
            table.add_row(
                c.id,
                c.conflict_type.value,
                sev_str,
                f"{c.rule_a} ⚡ {c.rule_b}",
                c.conflicting_resource or "-",
                c.resolution_strategy.value,
                res_str,
            )
        console.print(table)
    else:
        console.print("[bold green]✔ Zero cross-transformation conflicts detected. Clean execution path confirmed.[/bold green]")

    if not report.safe_to_execute:
        raise typer.Exit(code=1)


@app.command("resume")
def resume_cmd(
    execution_id: Annotated[
        str | None,
        typer.Argument(help="Optional execution run ID to resume (e.g. 'exec_20261120_...'). Defaults to latest resumable checkpoint."),
    ] = None,
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Path to workspace root directory."),
    ] = Path("."),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Simulate resumption without modifying files."),
    ] = False,
):
    """Resume an interrupted or failed modernization execution from its last saved checkpoint (P19)."""
    from rich.panel import Panel

    from amstralift.checkpoints import CheckpointManager, CheckpointStatus
    from amstralift.planning import PlanExecutor

    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    mgr = CheckpointManager(repo)

    try:
        checkpoint, resumed_plan, starting_phase = mgr.prepare_resumption(execution_id)
    except Exception as ex:
        console.print(f"[bold red]✖ Cannot resume modernization:[/bold red] {ex}")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            f"[bold]Execution Run ID:[/bold] [cyan]{checkpoint.execution_id}[/cyan]\n"
            f"[bold]Modernization Plan:[/bold] [yellow]{checkpoint.plan_id}[/yellow]\n"
            f"[bold]Target Ecosystem:[/bold] [cyan]{checkpoint.ecosystem.upper()}[/cyan] ({checkpoint.target_version})\n"
            f"[bold]Progress Before Resumption:[/bold] {checkpoint.completed_phases_count}/{checkpoint.total_phases} phases completed\n"
            f"[bold]Resuming From Phase:[/bold] [bold green]Phase {starting_phase + 1}[/bold green] ({len(resumed_plan.phases)} remaining phases)",
            title="Resuming Modernization Execution (P19)",
            border_style="cyan",
        )
    )

    executor = PlanExecutor()
    res = executor.execute_plan(
        plan=resumed_plan,
        workspace_path=repo,
        dry_run=dry_run,
        progress_callback=lambda m: console.print(f"  {m}"),
        enable_checkpointing=True,
        existing_checkpoint=checkpoint,
    )

    if res.success:
        console.print(f"\n[bold green]✔ Modernization successfully resumed and completed all phases![/bold green]")
    else:
        console.print(f"\n[bold red]✖ Resumed execution halted:[/bold red] {res.message}")
        raise typer.Exit(code=1)


@app.command("checkpoints")
def checkpoints_cmd(
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Path to workspace root directory."),
    ] = Path("."),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output checkpoints as raw JSON."),
    ] = False,
):
    """List historical and active modernization execution checkpoints (P19)."""
    import json
    from rich.table import Table

    from amstralift.checkpoints import CheckpointManager, CheckpointStatus

    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    mgr = CheckpointManager(repo)
    checkpoints = mgr.store.list_checkpoints()

    if as_json:
        data = [cp.model_dump(mode="json") for cp in checkpoints]
        typer.echo(json.dumps(data, indent=2))
        raise typer.Exit(code=0)

    if not checkpoints:
        console.print(f"[dim]No modernization checkpoints found in {repo.resolve()}[/dim]")
        raise typer.Exit(code=0)

    table = Table(
        title=f"Modernization Execution Checkpoints ({len(checkpoints)} found)",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Execution ID", style="bold", no_wrap=True)
    table.add_column("Ecosystem", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Progress", style="magenta")
    table.add_column("Resumable?", style="green")
    table.add_column("Updated At", style="dim")

    status_colors = {
        CheckpointStatus.COMPLETED: "[bold green]COMPLETED[/bold green]",
        CheckpointStatus.IN_PROGRESS: "[bold yellow]IN PROGRESS[/bold yellow]",
        CheckpointStatus.FAILED: "[bold red]FAILED[/bold red]",
        CheckpointStatus.PAUSED: "[bold cyan]PAUSED[/bold cyan]",
        CheckpointStatus.PENDING: "[dim]PENDING[/dim]",
    }

    for cp in checkpoints:
        badge = status_colors.get(cp.status, cp.status.value.upper())
        prog = f"{cp.completed_phases_count}/{cp.total_phases} phases"
        resumable_badge = "[bold green]YES[/bold green]" if cp.resumable and cp.status != CheckpointStatus.COMPLETED else "[dim]NO[/dim]"
        table.add_row(
            cp.execution_id,
            cp.ecosystem.upper(),
            badge,
            prog,
            resumable_badge,
            cp.updated_at[:19].replace("T", " "),
        )

    console.print(table)


policy_app = typer.Typer(
    name="policy",
    help="Enterprise Policy-as-Code governance and compliance enforcement (P20).",
    no_args_is_help=True,
)
app.add_typer(policy_app, name="policy")


@policy_app.command("init")
def policy_init_cmd(
    output_file: Annotated[
        Path,
        typer.Option("--output", "-o", help="Target path to create enterprise policy file."),
    ] = Path("policy.yaml"),
):
    """Scaffold a turnkey enterprise modernization policy file (P20)."""
    from amstralift.policy import EnterprisePolicy

    if output_file.exists():
        console.print(f"[bold red]✖ Error:[/bold red] Policy file already exists: {output_file.resolve()}")
        raise typer.Exit(code=1)

    policy = EnterprisePolicy(
        name="corporate-standard-policy",
        description="Corporate baseline for framework upgrades, vulnerability tolerance, and semantic API preservation.",
    )
    policy.save(output_file)
    console.print(f"[bold green]✔ Enterprise policy template created successfully at:[/bold green] {output_file.resolve()}")


@policy_app.command("check")
def policy_check_cmd(
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Target repository directory to evaluate."),
    ] = Path("."),
    policy_file: Annotated[
        Path | None,
        typer.Option("--policy", "-p", help="Path to declarative enterprise policy file (.yaml)."),
    ] = None,
    target: Annotated[
        int | None,
        typer.Option("--target", "-t", help="Target major framework version."),
    ] = None,
    ecosystem: Annotated[
        str | None,
        typer.Option("--ecosystem", "-e", help="Ecosystem override (angular, dotnet, python, react)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output evaluation report as raw JSON."),
    ] = False,
):
    """Evaluate repository modernization against declarative enterprise policy (P20)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.planning import ModernizationPlanner
    from amstralift.policy import PolicyAction, PolicyEvaluator, PolicySeverity, load_policy

    if not repo.exists() or not repo.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {repo.resolve()}")
        raise typer.Exit(code=1)

    policy = load_policy(repo_path=repo, explicit_policy_path=policy_file)
    planner = ModernizationPlanner()
    plan = planner.generate_plan(
        repo_path=repo,
        ecosystem=ecosystem,
        target_major=target,
        policy=policy,
    )

    report = PolicyEvaluator.evaluate(plan, policy)

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        raise typer.Exit(code=0 if report.allowed else 1)

    badge_map = {
        PolicyAction.ALLOW: "[bold green]COMPLIANT (ALLOW)[/bold green]",
        PolicyAction.REVIEW: "[bold yellow]REQUIRES HUMAN REVIEW[/bold yellow]",
        PolicyAction.BLOCK: "[bold red]NON-COMPLIANT (BLOCK)[/bold red]",
    }
    badge = badge_map.get(report.overall_action, report.overall_action.value.upper())

    border_color = (
        "green"
        if report.overall_action == PolicyAction.ALLOW
        else ("yellow" if report.overall_action == PolicyAction.REVIEW else "red")
    )

    console.print(
        Panel(
            f"[bold]Policy Specification:[/bold] [cyan]{policy.name}[/cyan] (v{policy.version})\n"
            f"[bold]Target Ecosystem:[/bold] [cyan]{plan.ecosystem.upper()}[/cyan] ({plan.current_version or 'unknown'} → {plan.target_version})\n"
            f"[bold]Compliance Verdict:[/bold] {badge}\n"
            f"[bold]Summary:[/bold] {report.summary}\n"
            f"[bold]Passed Rule Checks:[/bold] {len(report.passed_checks)} ({', '.join(report.passed_checks)})",
            title="Enterprise Policy-as-Code Evaluation (P20)",
            border_style=border_color,
        )
    )

    all_violations = report.blocking_violations + report.review_violations
    if all_violations:
        table = Table(
            title=f"Policy Violations & Governance Flags ({len(all_violations)} items)",
            header_style="bold red",
            border_style="dim",
        )
        table.add_column("Rule Name", style="bold", no_wrap=True)
        table.add_column("Action", style="bold")
        table.add_column("Severity", style="magenta")
        table.add_column("Target", style="yellow")
        table.add_column("Message", style="italic")

        for v in all_violations:
            act_str = "[bold red]BLOCK[/bold red]" if v.action == PolicyAction.BLOCK else "[yellow]REVIEW[/yellow]"
            sev_str = "[bold red]CRITICAL[/bold red]" if v.severity == PolicySeverity.CRITICAL else "[yellow]WARNING[/yellow]"
            table.add_row(v.rule_name, act_str, sev_str, v.target, v.message)

        console.print(table)
    else:
        console.print("[bold green]✔ Zero policy violations detected. Plan fully compliant with enterprise governance standards.[/bold green]")

    if not report.allowed:
        raise typer.Exit(code=1)


fleet_app = typer.Typer(
    name="fleet",
    help="Multi-Repository Modernization & Cross-Repo Orchestration Engine (P21).",
    no_args_is_help=True,
)
app.add_typer(fleet_app, name="fleet")
app.add_typer(fleet_app, name="multirepo")


@fleet_app.command("scan")
def fleet_scan_cmd(
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Root directory containing repositories to scan."),
    ] = Path("."),
    manifest: Annotated[
        Optional[Path],
        typer.Option("--manifest", "-m", help="Path to declarative fleet.yaml manifest."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output fleet topology as raw JSON."),
    ] = False,
):
    """Scan directory or manifest for repositories and inter-repository dependencies (P21)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.multirepo import FleetScanner

    if manifest:
        if not manifest.is_file():
            console.print(f"[bold red]✖ Error:[/bold red] Fleet manifest file not found: {manifest.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.load_manifest(manifest)
    else:
        if not directory.is_dir():
            console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {directory.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.scan_directory(directory)

    if as_json:
        typer.echo(topology.model_dump_json(indent=2))
        return

    repo_count = len(topology.repositories)
    dep_count = len(topology.dependencies)
    provider_count = len(topology.provider_repos)
    consumer_count = len(topology.consumer_repos)
    isolated_count = len(topology.isolated_repos)
    ecos = ", ".join(topology.ecosystems) if topology.ecosystems else "None"

    console.print(
        Panel(
            f"[bold]Root Location:[/bold] [cyan]{topology.root_path}[/cyan]\n"
            f"[bold]Total Repositories:[/bold] [green]{repo_count}[/green]\n"
            f"[bold]Detected Ecosystems:[/bold] [yellow]{ecos}[/yellow]\n"
            f"[bold]Cross-Repo Dependencies:[/bold] [cyan]{dep_count}[/cyan]\n"
            f"[bold]Provider Repos (Shared Libraries):[/bold] [magenta]{provider_count}[/magenta] ({', '.join(topology.provider_repos) or 'none'})\n"
            f"[bold]Consumer Repos (Downstream Services):[/bold] [blue]{consumer_count}[/blue] ({', '.join(topology.consumer_repos) or 'none'})\n"
            f"[bold]Isolated Repos:[/bold] [white]{isolated_count}[/white]",
            title="Multi-Repository Fleet Topology (P21) — Engineered by Amal P S",
            border_style="cyan",
        )
    )

    if not topology.repositories:
        console.print("[yellow]⚠ No projects or repositories discovered in the target directory.[/yellow]")
        return

    table = Table(
        title=f"Discovered Fleet Repositories ({repo_count} repos)",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Repository ID", style="bold green", no_wrap=True)
    table.add_column("Ecosystem", style="cyan")
    table.add_column("Framework Version", style="yellow")
    table.add_column("Published Exports", style="magenta")
    table.add_column("Upstream Providers", style="blue")
    table.add_column("Downstream Consumers", style="white")

    for r_id in sorted(list(topology.repositories.keys())):
        repo = topology.repositories[r_id]
        exports_str = ", ".join([e.package_name for e in repo.exports]) or "[dim]none[/dim]"
        providers = topology.get_providers(r_id)
        consumers = topology.get_consumers(r_id)
        prov_str = ", ".join(providers) if providers else "[dim]none (root)[/dim]"
        cons_str = ", ".join(consumers) if consumers else "[dim]none (leaf)[/dim]"

        table.add_row(
            r_id,
            repo.primary_ecosystem.upper(),
            repo.framework_version or "[dim]n/a[/dim]",
            exports_str,
            prov_str,
            cons_str,
        )

    console.print(table)


@fleet_app.command("graph")
def fleet_graph_cmd(
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Root directory containing repositories to scan."),
    ] = Path("."),
    manifest: Annotated[
        Optional[Path],
        typer.Option("--manifest", "-m", help="Path to declarative fleet.yaml manifest."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output dependency graph as raw JSON."),
    ] = False,
):
    """Analyze and display cross-repository dependency topology and cycle detection (P21)."""
    import json as json_lib
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.multirepo import FleetDependencyGraph, FleetScanner, InterRepoCycleError

    if manifest:
        if not manifest.is_file():
            console.print(f"[bold red]✖ Error:[/bold red] Fleet manifest file not found: {manifest.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.load_manifest(manifest)
    else:
        if not directory.is_dir():
            console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {directory.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.scan_directory(directory)

    try:
        graph = FleetDependencyGraph(topology)
        waves = graph.compute_execution_waves()
    except InterRepoCycleError as e:
        console.print(f"[bold red]✖ Circular Dependency Detected:[/bold red] {e}")
        raise typer.Exit(code=1)

    if as_json:
        payload = {
            "repositories": sorted(list(topology.repositories.keys())),
            "dependencies": [d.model_dump() for d in topology.dependencies],
            "dependency_matrix": graph.dependency_matrix(),
            "execution_waves": [w.model_dump() for w in waves],
        }
        typer.echo(json_lib.dumps(payload, indent=2))
        return

    console.print(
        Panel(
            f"[bold]Active Repositories:[/bold] [green]{len(topology.repositories)}[/green]\n"
            f"[bold]Cross-Repository Links:[/bold] [cyan]{len(topology.dependencies)}[/cyan]\n"
            f"[bold]Calculated Execution Waves:[/bold] [magenta]{len(waves)}[/magenta]\n"
            f"[bold]DAG Cycle Validation:[/bold] [bold green]✔ PASSED (Acyclic Directed Graph)[/bold green]",
            title="Cross-Repository Dependency Graph (P21) — Engineered by Amal P S",
            border_style="green",
        )
    )

    if topology.dependencies:
        table = Table(
            title="Inter-Repository Dependency Links (Provider → Consumer)",
            header_style="bold magenta",
            border_style="dim",
        )
        table.add_column("Provider (Upstream)", style="bold magenta")
        table.add_column("Consumer (Downstream)", style="bold blue")
        table.add_column("Package / Link", style="yellow")
        table.add_column("Required Version", style="white")
        table.add_column("Ecosystem", style="cyan")
        table.add_column("Type", style="dim")

        for dep in topology.dependencies:
            table.add_row(
                dep.provider_repo_id,
                dep.consumer_repo_id,
                dep.package_name,
                dep.version_required,
                dep.ecosystem.upper(),
                dep.dependency_type,
            )

        console.print(table)
    else:
        console.print("[dim green]✔ No cross-repository dependencies detected. All repositories can be upgraded independently.[/dim green]")


@fleet_app.command("plan")
def fleet_plan_cmd(
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Root directory containing repositories to scan."),
    ] = Path("."),
    manifest: Annotated[
        Optional[Path],
        typer.Option("--manifest", "-m", help="Path to declarative fleet.yaml manifest."),
    ] = None,
    target: Annotated[
        Optional[int],
        typer.Option("--target", "-t", help="Target framework version override."),
    ] = None,
    ecosystem: Annotated[
        Optional[str],
        typer.Option("--ecosystem", "-e", help="Filter modernization to a specific ecosystem (angular, react, dotnet, python)."),
    ] = None,
    output_file: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="File path to save modernization plan JSON."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output modernization plan as raw JSON to stdout."),
    ] = False,
):
    """Generate a coordinated, staged multi-repository modernization campaign plan (P21)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.multirepo import FleetPlanner, FleetScanner, InterRepoCycleError

    if manifest:
        if not manifest.is_file():
            console.print(f"[bold red]✖ Error:[/bold red] Fleet manifest file not found: {manifest.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.load_manifest(manifest)
    else:
        if not directory.is_dir():
            console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {directory.resolve()}")
            raise typer.Exit(code=1)
        topology = FleetScanner.scan_directory(directory)

    try:
        plan = FleetPlanner.generate_plan(
            topology=topology,
            target_major=target,
            ecosystem=ecosystem,
        )
    except InterRepoCycleError as e:
        console.print(f"[bold red]✖ Planning Aborted due to Cycle:[/bold red] {e}")
        raise typer.Exit(code=1)

    if output_file:
        FleetPlanner.save_plan(plan, output_file)
        console.print(f"[bold green]✔ Modernization plan persisted to:[/bold green] {output_file.resolve()}")

    if as_json:
        typer.echo(plan.model_dump_json(indent=2))
        return

    console.print(
        Panel(
            f"[bold]Campaign Plan ID:[/bold] [cyan]{plan.plan_id}[/cyan]\n"
            f"[bold]Participating Repositories:[/bold] [green]{plan.total_repositories}[/green]\n"
            f"[bold]Staged Execution Waves:[/bold] [magenta]{len(plan.waves)}[/magenta]\n"
            f"[bold]Cross-Repo Dependency Edges:[/bold] [yellow]{plan.cross_repo_edges}[/yellow]\n"
            f"[bold]Ecosystem Filter:[/bold] [cyan]{(plan.ecosystem_filter or 'All Ecosystems').upper()}[/cyan]\n"
            f"[bold]Target Framework Version:[/bold] [yellow]{plan.target_version or 'Latest Stable LTS'}[/yellow]\n"
            f"[bold]Summary:[/bold] {plan.summary}",
            title="Multi-Repository Modernization Plan (P21) — Engineered by Amal P S",
            border_style="magenta",
        )
    )

    if plan.waves:
        wave_table = Table(
            title=f"Coordinated Execution Waves ({len(plan.waves)} stages)",
            header_style="bold magenta",
            border_style="dim",
        )
        wave_table.add_column("Wave", style="bold magenta", no_wrap=True)
        wave_table.add_column("Wave Name", style="bold cyan")
        wave_table.add_column("Scheduled Repositories", style="green")
        wave_table.add_column("Concurrency", style="yellow")
        wave_table.add_column("Wave Responsibilities & Staging", style="italic")

        for w in plan.waves:
            wave_table.add_row(
                f"Stage {w.wave_number}",
                w.name,
                ", ".join(w.repo_ids),
                f"{len(w.repo_ids)} parallel worker(s)",
                w.description,
            )

        console.print(wave_table)

    if any(prereqs for prereqs in plan.dependency_matrix.values()):
        matrix_table = Table(
            title="Cross-Repository Dependency Prerequisite Matrix",
            header_style="bold blue",
            border_style="dim",
        )
        matrix_table.add_column("Repository", style="bold green")
        matrix_table.add_column("Required Upstream Prerequisite Repositories", style="magenta")

        for r_id, prereqs in sorted(plan.dependency_matrix.items()):
            prereq_str = ", ".join(prereqs) if prereqs else "[dim green]none (ready in Wave 1)[/dim green]"
            matrix_table.add_row(r_id, prereq_str)

        console.print(matrix_table)


campaign_app = typer.Typer(
    name="campaign",
    help="Organization-wide Modernization Campaign Engine (P22).",
    no_args_is_help=True,
)
app.add_typer(campaign_app, name="campaign")


@campaign_app.command("init")
def campaign_init_cmd(
    name: Annotated[
        str,
        typer.Option("--name", "-n", help="Campaign title (e.g. 'Q1 2027 Modernization')."),
    ] = "Organization Modernization Campaign",
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Base directory containing repositories or fleet manifest."),
    ] = Path("."),
    manifest: Annotated[
        Optional[Path],
        typer.Option("--manifest", "-m", help="Path to declarative fleet.yaml manifest."),
    ] = None,
    target_angular: Annotated[
        Optional[int],
        typer.Option("--target-angular", help="Target major version for Angular repositories."),
    ] = 18,
    target_dotnet: Annotated[
        Optional[int],
        typer.Option("--target-dotnet", help="Target major version for .NET repositories."),
    ] = 9,
    target_react: Annotated[
        Optional[int],
        typer.Option("--target-react", help="Target major version for React repositories."),
    ] = 19,
    output_file: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Target path to save campaign definition JSON."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output campaign definition as raw JSON."),
    ] = False,
):
    """Scaffold a new organization-wide modernization campaign across fleet repositories (P22)."""
    from rich.panel import Panel

    from amstralift.campaign import CampaignEngine, CampaignStore

    if not directory.is_dir():
        console.print(f"[bold red]✖ Error:[/bold red] Target directory does not exist: {directory.resolve()}")
        raise typer.Exit(code=1)

    targets: dict[str, int] = {}
    if target_angular:
        targets["angular"] = target_angular
    if target_dotnet:
        targets["dotnet"] = target_dotnet
    if target_react:
        targets["react"] = target_react

    campaign = CampaignEngine.init_campaign(
        name=name,
        fleet_root=directory,
        targets=targets,
        manifest_path=manifest,
    )

    saved_path = CampaignStore.save(campaign, file_path=output_file, base_dir=directory)

    if as_json:
        typer.echo(campaign.model_dump_json(indent=2))
        return

    console.print(
        Panel(
            f"[bold]Campaign ID:[/bold] [cyan]{campaign.campaign_id}[/cyan]\n"
            f"[bold]Title:[/bold] [white]{campaign.name}[/white]\n"
            f"[bold]Status:[/bold] [yellow]{campaign.status.value.upper()}[/yellow]\n"
            f"[bold]Participating Repositories:[/bold] [green]{len(campaign.repository_records)}[/green]\n"
            f"[bold]Targets:[/bold] [magenta]{', '.join(f'{k.upper()}: {v}' for k, v in campaign.targets.items())}[/magenta]\n"
            f"[bold]Campaign Saved To:[/bold] [cyan]{saved_path.resolve()}[/cyan]",
            title="Modernization Campaign Initialized (P22) — Engineered by Amal P S",
            border_style="magenta",
        )
    )


@campaign_app.command("run")
def campaign_run_cmd(
    campaign_ref: Annotated[
        Optional[str],
        typer.Option("--campaign", "-c", help="Campaign ID or path to campaign JSON file."),
    ] = None,
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Base directory containing repositories."),
    ] = Path("."),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--execute", help="Simulate campaign modernization vs live execution."),
    ] = True,
    output_file: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Target path to save updated campaign state."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output execution results as raw JSON."),
    ] = False,
):
    """Execute or simulate an organization-wide modernization campaign wave by wave (P22)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.campaign import CampaignEngine, CampaignStore

    if campaign_ref:
        campaign = CampaignStore.load(campaign_ref, base_dir=directory)
    else:
        # Check if an existing campaign is in .amstralift/campaigns/
        existing = CampaignStore.list_campaigns(base_dir=directory)
        if existing:
            campaign = existing[0]
        else:
            campaign = CampaignEngine.init_campaign(
                name="Fleet Modernization Campaign",
                fleet_root=directory,
            )

    updated_campaign = CampaignEngine.run_campaign(campaign, dry_run=dry_run)
    saved_path = CampaignStore.save(updated_campaign, file_path=output_file, base_dir=directory)

    if as_json:
        typer.echo(updated_campaign.model_dump_json(indent=2))
        return

    m = updated_campaign.metrics
    mode_badge = "[bold yellow][DRY-RUN SIMULATION][/bold yellow]" if dry_run else "[bold green][LIVE EXECUTION][/bold green]"

    console.print(
        Panel(
            f"[bold]Campaign:[/bold] [white]{updated_campaign.name}[/white] {mode_badge}\n"
            f"[bold]Campaign ID:[/bold] [cyan]{updated_campaign.campaign_id}[/cyan] | [bold]Status:[/bold] [green]{updated_campaign.status.value.upper()}[/green]\n"
            f"[bold]Total Repositories:[/bold] [cyan]{m.total_repos}[/cyan] | [bold]Modernized:[/bold] [green]{m.modernized_count}[/green] | [bold]Partial:[/bold] [yellow]{m.partial_count}[/yellow] | [bold]Refused:[/bold] [red]{m.refused_count}[/red] | [bold]Failed:[/bold] [bold red]{m.failed_count}[/bold red]\n"
            f"[bold]Upgrade Success Rate:[/bold] [green]{m.upgrade_success_rate}%[/green] | [bold]Refusal Rate:[/bold] [yellow]{m.refusal_rate}%[/yellow]\n"
            f"[bold]CVE Reduction:[/bold] [bold green]{m.cve_reduction_pct}%[/bold green] ({m.total_cves_before} → {m.total_cves_after})\n"
            f"[bold]EOL Reduction:[/bold] [bold green]{m.eol_reduction_pct}%[/bold green] ({m.eol_repos_before} → {m.eol_repos_after})\n"
            f"[bold]Engineering Hours Saved:[/bold] [bold magenta]{m.engineering_hours_saved:,.1f} hrs[/bold magenta]\n"
            f"[bold]Mean Verification Time:[/bold] [cyan]{m.mean_verification_time_display}[/cyan]",
            title="Modernization Campaign Dashboard (P22) — Engineered by Amal P S",
            border_style="green",
        )
    )

    if m.ecosystem_breakdown:
        eco_table = Table(
            title="Ecosystem Breakdown",
            header_style="bold cyan",
            border_style="dim",
        )
        eco_table.add_column("Ecosystem", style="bold magenta")
        eco_table.add_column("Total Repos", style="white")
        eco_table.add_column("Modernized", style="green")
        eco_table.add_column("Partial", style="yellow")
        eco_table.add_column("Refused", style="red")
        eco_table.add_column("Failed", style="bold red")
        eco_table.add_column("CVEs Eliminated", style="cyan")
        eco_table.add_column("Hours Saved", style="bold green")

        for eco_name, em in sorted(m.ecosystem_breakdown.items()):
            eco_table.add_row(
                eco_name.upper(),
                str(em.total_repos),
                str(em.modernized),
                str(em.partial),
                str(em.refused),
                str(em.failed),
                str(em.cves_eliminated),
                f"{em.hours_saved:,.1f} hrs",
            )
        console.print(eco_table)


@campaign_app.command("status")
def campaign_status_cmd(
    campaign_ref: Annotated[
        Optional[str],
        typer.Option("--campaign", "-c", help="Campaign ID or path to campaign JSON file."),
    ] = None,
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Base directory containing campaigns."),
    ] = Path("."),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output campaign status as raw JSON."),
    ] = False,
):
    """View executive status and dashboard KPIs of a modernization campaign (P22)."""
    from rich.panel import Panel
    from rich.table import Table

    from amstralift.campaign import CampaignStore

    if campaign_ref:
        campaign = CampaignStore.load(campaign_ref, base_dir=directory)
    else:
        campaigns = CampaignStore.list_campaigns(base_dir=directory)
        if not campaigns:
            console.print("[yellow]⚠ No active or recorded campaigns found. Run 'amstralift campaign init' first.[/yellow]")
            return
        campaign = campaigns[0]

    if as_json:
        typer.echo(campaign.model_dump_json(indent=2))
        return

    m = campaign.metrics
    console.print(
        Panel(
            f"[bold]Campaign Name:[/bold] [white]{campaign.name}[/white]\n"
            f"[bold]Campaign ID:[/bold] [cyan]{campaign.campaign_id}[/cyan]\n"
            f"[bold]Charter:[/bold] {campaign.description or 'None provided.'}\n"
            f"[bold]Status:[/bold] [green]{campaign.status.value.upper()}[/green]\n"
            f"[bold]Total Fleet Repositories:[/bold] [cyan]{m.total_repos}[/cyan]\n"
            f"[bold]Modernized / In Progress:[/bold] [green]{m.modernized_count + m.partial_count}[/green] / {m.total_repos}\n"
            f"[bold]Upgrade Success Rate:[/bold] [bold green]{m.upgrade_success_rate}%[/bold green]\n"
            f"[bold]CVE Reduction:[/bold] [bold green]{m.cve_reduction_pct}%[/bold green] ({m.total_cves_before} → {m.total_cves_after})\n"
            f"[bold]EOL Reduction:[/bold] [bold green]{m.eol_reduction_pct}%[/bold green] ({m.eol_repos_before} → {m.eol_repos_after})\n"
            f"[bold]Engineering Hours Saved:[/bold] [bold magenta]{m.engineering_hours_saved:,.1f} hrs[/bold magenta]\n"
            f"[bold]Mean Sandbox Verification Time:[/bold] [cyan]{m.mean_verification_time_display}[/cyan]",
            title="Modernization Campaign Status (P22) — Engineered by Amal P S",
            border_style="cyan",
        )
    )

    table = Table(
        title="Repository Progress Overview",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Repository ID", style="bold green", no_wrap=True)
    table.add_column("Ecosystem", style="cyan")
    table.add_column("Starting Ver", style="white")
    table.add_column("Target Ver", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Wave", style="magenta")
    table.add_column("CVEs (Bef → Aft)", style="white")
    table.add_column("Runtime", style="dim")

    for r_id, r in sorted(campaign.repository_records.items()):
        status_color = (
            "green"
            if r.status.value == "modernized"
            else ("yellow" if r.status.value == "partial" else ("red" if r.status.value in ("failed", "refused") else "white"))
        )
        cve_str = f"{r.cves_before} → {r.cves_after}"
        table.add_row(
            r_id,
            r.ecosystem.upper(),
            r.initial_version or "unknown",
            r.target_version or "latest",
            f"[{status_color}]{r.status.value.upper()}[/{status_color}]",
            f"Wave {r.wave_number}",
            cve_str,
            f"{r.verification_time_seconds:.1f}s",
        )
    console.print(table)


@campaign_app.command("report")
def campaign_report_cmd(
    campaign_ref: Annotated[
        Optional[str],
        typer.Option("--campaign", "-c", help="Campaign ID or path to campaign JSON file."),
    ] = None,
    directory: Annotated[
        Path,
        typer.Option("--dir", "-d", help="Base directory containing campaigns."),
    ] = Path("."),
    output_file: Annotated[
        Path,
        typer.Option("--output", "-o", help="Target markdown file path for executive report."),
    ] = Path("campaign-report.md"),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output report metrics as raw JSON."),
    ] = False,
):
    """Export comprehensive executive Markdown report of a campaign (P22)."""
    from amstralift.campaign import CampaignStore

    if campaign_ref:
        campaign = CampaignStore.load(campaign_ref, base_dir=directory)
    else:
        campaigns = CampaignStore.list_campaigns(base_dir=directory)
        if not campaigns:
            console.print("[bold red]✖ Error:[/bold red] No campaigns found to report on.")
            raise typer.Exit(code=1)
        campaign = campaigns[0]

    if as_json:
        typer.echo(campaign.metrics.model_dump_json(indent=2))
        return

    report_path = CampaignStore.export_markdown_report(campaign, output_path=output_file)
    console.print(f"[bold green]✔ Executive campaign report exported successfully to:[/bold green] {report_path.resolve()}")


@app.command()
def version():
    """Display AMstraLift version and author attribution."""
    from amstralift import __author__, __license__, __version__

    console.print(f"AMstraLift v{__version__} — Engineered by {__author__} ({__license__})")


if __name__ == "__main__":
    app()
