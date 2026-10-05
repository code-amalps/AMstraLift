"""Rich terminal UI dashboards and views for vulnerability auditing, previewing, and remediation results."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from amstralift.governance.vulnerabilities import VulnerabilitySeverity
from amstralift.security.models import (
    AuditReport,
    RemediationPlan,
    RemediationResult,
)


class SecurityUI:
    """Renders formatted Rich dashboards, preview plans, and remediation outcomes."""

    @classmethod
    def render_dashboard(cls, report: AuditReport, console: Console) -> None:
        """Render the comprehensive Vulnerability Dashboard."""
        if not report.findings:
            if getattr(report, "scan_failed_or_blocked", False):
                console.print(
                    Panel.fit(
                        f"[bold yellow]⚠️ Vulnerability Scan Incomplete (Corporate Proxy / Offline)[/bold yellow]\n\n"
                        f"[yellow]The vulnerability database (api.osv.dev) was unreachable or blocked by corporate proxy.\n"
                        f"Unable to verify vulnerability status for {report.scanned_packages_count} packages.\n"
                        f"Check corporate proxy / network access or use local audit feeds.[/yellow]",
                        title="Security Audit Incomplete",
                        border_style="yellow",
                    )
                )
                return

            console.print(
                Panel.fit(
                    f"[bold green]✔ Zero Vulnerabilities Found![/bold green]\n\n"
                    f"[dim]All {report.scanned_packages_count} scanned dependencies are clean according to vulnerability intelligence.\n"
                    f"No known security advisories affecting this repository.[/dim]",
                    title="Security Audit Passed",
                    border_style="green",
                )
            )
            return

        # Summary Metrics Panel
        sev_counts: dict[str, int] = {}
        for f in report.findings:
            sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1

        summary_text = (
            f"[bold]Total Findings:[/bold] {len(report.findings)} advisories across [cyan]{report.vulnerable_packages_count}[/cyan] packages\n"
            f"[bold]Dependency Breakdown:[/bold] [blue]{len(report.direct_findings)} Direct[/blue] | [magenta]{len(report.transitive_findings)} Transitive[/magenta]\n"
            f"[bold]Severity Breakdown:[/bold] "
            f"[bold red]{sev_counts.get('CRITICAL', 0)} Critical[/bold red] | "
            f"[red]{sev_counts.get('HIGH', 0)} High[/red] | "
            f"[yellow]{sev_counts.get('MEDIUM', 0)} Medium[/yellow] | "
            f"[cyan]{sev_counts.get('LOW', 0)} Low[/cyan]\n"
            f"[bold]Governance Status:[/bold] [green]{len(report.exempted_findings)} Exempted (Waiver Active)[/green] | [red]{len(report.actionable_findings)} Actionable[/red]"
        )
        console.print(
            Panel(
                summary_text,
                title=f"🛡️ AMstraLift Security Audit Dashboard ({report.ecosystem.upper()})",
                border_style="red" if report.has_critical_or_high else "yellow",
            )
        )

        # Table of Findings
        table = Table(title="Detected Vulnerability Advisories", show_header=True, header_style="bold red")
        table.add_column("Advisory / CVE", style="bold")
        table.add_column("Package")
        table.add_column("Installed")
        table.add_column("Type")
        table.add_column("Severity")
        table.add_column("Fixed Version", style="bold green")
        table.add_column("Exemption / Status")
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
            type_str = "[blue]Direct[/blue]" if f.is_direct else f"[magenta]Transitive[/magenta]\n[dim]via {f.display_introduced_by[:25]}[/dim]"
            fix_str = f.fixed_version if f.fixed_version else "[dim red]No patch yet[/dim red]"
            status_str = f"[green]EXEMPT ({f.exemption_id})[/green]" if f.is_exempted else "[red]Actionable[/red]"
            summary_short = f.summary[:55] + "..." if len(f.summary) > 55 else f.summary

            table.add_row(
                f.cve_id,
                f.package_name,
                f.current_version,
                type_str,
                f"[{sev_color}]{f.severity.value}[/{sev_color}]",
                fix_str,
                status_str,
                summary_short,
            )

        console.print(table)

    @classmethod
    def render_preview(cls, plan: RemediationPlan, console: Console) -> None:
        """Render the Remediation Preview View."""
        if not plan.items:
            console.print(
                Panel.fit(
                    "[yellow]No actionable remediation items in plan. All vulnerabilities are either exempted or require upstream fixes.[/yellow]",
                    title="Remediation Preview",
                    border_style="yellow",
                )
            )
            return

        table = Table(
            title=f"📋 Remediation Preview Plan ({plan.total_packages_to_remediate} packages to update)",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Package", style="bold")
        table.add_column("Current")
        table.add_column("Target", style="bold green")
        table.add_column("Type")
        table.add_column("Mechanism")
        table.add_column("Major Leap?", style="bold yellow")
        table.add_column("Expected File Changes")
        table.add_column("Compatibility Rationale")

        for item in plan.items:
            type_str = "Direct" if item.is_direct else f"Transitive\n({item.introduced_by[0] if item.introduced_by else 'indirect'})"
            major_str = "[bold red]YES (Breaking Risk)[/bold red]" if item.is_major_bump else "[green]No (Safe)[/green]"
            files_str = ", ".join(item.expected_file_changes)

            table.add_row(
                item.package_name,
                item.current_version,
                item.target_version,
                type_str,
                item.remediation_mechanism,
                major_str,
                files_str,
                item.compatibility_notes[:60],
            )

        console.print(table)

        # Breaking Risk Warnings
        if plan.has_major_upgrades:
            risk_lines = []
            for item in plan.items:
                if item.is_major_bump:
                    risk_lines.append(f"• [bold]{item.package_name}[/bold]: {'; '.join(item.breaking_change_risks)}")
            console.print(
                Panel(
                    "\n".join(risk_lines),
                    title="⚠️ Breaking Change & Major Upgrade Warnings",
                    border_style="bold red",
                )
            )

        # Advisories for unfixable items
        if plan.advisories:
            adv_lines = [f"• {adv}" for adv in plan.advisories[:5]]
            console.print(
                Panel(
                    "\n".join(adv_lines),
                    title="ℹ️ Security Advisories & Unresolved Findings",
                    border_style="yellow",
                )
            )

    @classmethod
    def render_result(cls, result: RemediationResult, console: Console) -> None:
        """Render the Remediation Result View with gates, uncertainty warning, and PR info."""
        if not result.remediation_successful:
            console.print(
                Panel(
                    f"[bold red]✖ Security Remediation Halted[/bold red]\n\n"
                    f"[red]{result.error_message}[/red]",
                    title="Remediation Failed",
                    border_style="red",
                )
            )
            return

        from amstralift.security.models import VerificationStatus

        if result.verification_status == VerificationStatus.UNVERIFIED_NO_TESTS:
            console.print(
                Panel.fit(
                    "[bold yellow]⚠️ Build Compiled Successfully - Unverified Safety (0 Tests Found)[/bold yellow]\n"
                    "[dim]Remediation candidate compiled cleanly, but NO automated tests exist to verify functional safety.\n"
                    "Prepared for policy-controlled manual review and human approval.[/dim]",
                    title="Remediation Proposal (Unverified)",
                    border_style="yellow",
                )
            )
        elif result.verification_status == VerificationStatus.VERIFICATION_INCOMPLETE:
            console.print(
                Panel.fit(
                    "[bold yellow]⚠️ Build Compiled Successfully - Verification Incomplete (Tests Skipped)[/bold yellow]\n"
                    "[dim]Automated test runner was skipped or unavailable. Policy-controlled manual approval required.[/dim]",
                    title="Remediation Proposal (Incomplete)",
                    border_style="yellow",
                )
            )
        else:
            console.print(
                Panel.fit(
                    "[bold green]✔ Security Remediation Verified Safe![/bold green]\n"
                    "[dim]Build and automated tests passed with verified evidence inside the isolated sandbox.[/dim]",
                    title="Remediation Success (Fully Verified)",
                    border_style="green",
                )
            )

        # Uncertainty Warning Banner
        if result.uncertainty_warning:
            console.print(
                Panel(
                    f"[bold yellow]{result.uncertainty_warning}[/bold yellow]",
                    title="⚠️ Functional Uncertainty Notice",
                    border_style="yellow",
                )
            )

        # Build & Test Gates Table
        if result.gate_summary:
            gate_table = Table(title="5 Verification Gates Evidence", show_header=True, header_style="bold green")
            gate_table.add_column("Gate")
            gate_table.add_column("Command")
            gate_table.add_column("Status")
            gate_table.add_column("Duration")
            for g in result.gate_summary.results:
                icon = "✅" if "PASSED" in g.status.value else "❌"
                gate_table.add_row(g.name, g.command, f"{icon} {g.status.value}", f"{g.duration_seconds:.2f}s")
            console.print(gate_table)

        # PR Proposal Details
        if result.branch_name:
            pr_info = [
                f"[bold]Remediation Branch:[/bold] [cyan]{result.branch_name}[/cyan]",
                f"[dim]Run 'git checkout {result.branch_name}' to inspect the verified security patches.[/dim]",
            ]
            if result.pr_proposal and result.pr_proposal.remote_pr_url:
                pr_info.append(f"[bold]Remote PR URL:[/bold] [cyan]{result.pr_proposal.remote_pr_url}[/cyan]")
            console.print(Panel("\n".join(pr_info), title="Stage B Publisher Output", border_style="cyan"))

        # Advisories and Skipped Items Notice
        if result.plan and result.plan.advisories:
            adv_lines = [f"• {adv}" for adv in result.plan.advisories]
            console.print(
                Panel(
                    "\n".join(adv_lines),
                    title="ℹ️ Security Governance Advisories & Skipped Items",
                    border_style="yellow",
                )
            )
