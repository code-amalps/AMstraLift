"""Smart diagnostics for failed verification gates and build/test pipelines.

Parses compiler, linter, and test outputs across Angular, React, .NET, and Python
ecosystems to extract the exact failing file, line number, error code, and actionable
remediation guidance for developers.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from pydantic import BaseModel, Field
from rich.panel import Panel
from rich.text import Text


class KnownBreakingChange(NamedTuple):
    pattern: re.Pattern
    title: str
    explanation: str
    remediation_tip: str
    docs_url: str | None = None


# Curated knowledge base of frequent framework breaking changes
KNOWN_BREAKING_CHANGES: list[KnownBreakingChange] = [
    # ── Angular ──────────────────────────────────────────────────────────────
    KnownBreakingChange(
        pattern=re.compile(r"entryComponents|Property 'entryComponents' does not exist", re.IGNORECASE),
        title="Angular 13+ Ivy: entryComponents Removed",
        explanation="Angular 13 removed the legacy View Engine. All components are now compiled with Ivy by default.",
        remediation_tip="Remove 'entryComponents: [...]' from your @NgModule declarations; Ivy compiles components automatically.",
        docs_url="https://update.angular.io/?v=12.0-13.0",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"loadChildren.*string|loadChildren.*#", re.IGNORECASE),
        title="Angular Deprecated String-based Lazy Loading",
        explanation="String-based loadChildren syntax ('path/to/module#Module') was removed.",
        remediation_tip="Update to dynamic imports: loadChildren: () => import('./path').then(m => m.Module).",
        docs_url="https://angular.dev/guide/routing/common-router-tasks#lazy-loading",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"CanActivate|CanDeactivate.*deprecated|not assignable to type 'CanActivate'", re.IGNORECASE),
        title="Angular 15+ Functional Route Guards",
        explanation="Class-based route guard interfaces (CanActivate, CanDeactivate) are deprecated in favor of functional guards.",
        remediation_tip="Migrate to functional guards: canActivate: [() => inject(AuthService).canActivate()].",
        docs_url="https://angular.dev/guide/routing/common-router-tasks#preventing-unauthorized-access",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"@angular/material.*legacy|legacy-.*not found|mdc-migration", re.IGNORECASE),
        title="Angular Material 15+ MDC Migration",
        explanation="Angular 15 migrated Material components to Material Design Components (MDC) web standards.",
        remediation_tip="Run 'ng generate @angular/material:mdc-migration' or temporarily import from '@angular/material/legacy-*'.",
        docs_url="https://material.angular.io/guide/mdc-migration",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"ChromeHeadless.*not found|Cannot start Chrome|No binary for Chrome", re.IGNORECASE),
        title="Chrome / Chromium Headless Not Found",
        explanation="The test runner (Karma/Angular CLI) requires Chrome or Chromium to run headless tests.",
        remediation_tip="Install Chrome or Chromium, or set the CHROME_BIN environment variable.",
        docs_url="https://angular.dev/reference/cli/ng-test",
    ),
    # ── .NET ─────────────────────────────────────────────────────────────────
    KnownBreakingChange(
        pattern=re.compile(r"BinaryFormatter.*is obsolete|SYSLIB0011|SYSLIB0050", re.IGNORECASE),
        title=".NET 8+ BinaryFormatter Disabled",
        explanation="BinaryFormatter is obsolete and disabled by default in .NET 8 due to critical deserialization security risks.",
        remediation_tip="Migrate object serialization to System.Text.Json, MessagePack, or Protobuf.",
        docs_url="https://learn.microsoft.com/en-us/dotnet/standard/serialization/binaryformatter-migration-guide",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"IHostingEnvironment.*is obsolete|CS0618.*IHostingEnvironment", re.IGNORECASE),
        title="ASP.NET Core: IHostingEnvironment Obsolete",
        explanation="IHostingEnvironment was replaced by IWebHostEnvironment and IHostEnvironment.",
        remediation_tip="Replace parameter injection of IHostingEnvironment with Microsoft.AspNetCore.Hosting.IWebHostEnvironment.",
        docs_url="https://learn.microsoft.com/en-us/dotnet/core/compatibility/aspnet-core/3.0/hosting-ihostingenvironment-obsolete",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"CS8600|CS8602|CS8603|CS8604|CS8618|CS8625", re.IGNORECASE),
        title=".NET Nullable Reference Types Warning",
        explanation="The project has Nullable Reference Types (<Nullable>enable</Nullable>) enabled and detected potential null dereference.",
        remediation_tip="Annotate nullable variables with '?' or use null-coalescing ('??') / null-forgiving ('!') operators where validated.",
        docs_url="https://learn.microsoft.com/en-us/dotnet/csharp/nullable-references",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"NETSDK1045", re.IGNORECASE),
        title=".NET SDK Version Incompatibility",
        explanation="The current .NET SDK does not support targeting the specified framework version.",
        remediation_tip="Install the required .NET SDK version or update your global.json SDK pin.",
        docs_url="https://dotnet.microsoft.com/download",
    ),
    # ── Python ───────────────────────────────────────────────────────────────
    KnownBreakingChange(
        pattern=re.compile(r"distutils.*not found|No module named 'distutils'|pkg_resources.*deprecated", re.IGNORECASE),
        title="Python 3.12+: distutils Removed",
        explanation="Python 3.12 completely removed the deprecated distutils standard library module.",
        remediation_tip="Replace distutils imports with 'setuptools' or 'importlib.metadata'.",
        docs_url="https://docs.python.org/3/whatsnew/3.12.html#removed",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"PydanticDeprecatedSince20|PydanticUserError.*dict\(\)", re.IGNORECASE),
        title="Pydantic V1 -> V2 Migration",
        explanation="Pydantic v2 deprecated BaseModel.dict() in favor of model_dump() and parse_obj() in favor of model_validate().",
        remediation_tip="Use 'model.model_dump()' instead of 'model.dict()', and 'Model.model_validate(data)' instead of 'parse_obj'.",
        docs_url="https://docs.pydantic.dev/latest/migration/",
    ),
    KnownBreakingChange(
        pattern=re.compile(r"There is no current event loop in thread|DeprecationWarning.*get_event_loop", re.IGNORECASE),
        title="Python 3.10+: asyncio.get_event_loop() Deprecated",
        explanation="asyncio.get_event_loop() without an active running loop is deprecated in Python 3.10 and later.",
        remediation_tip="Use 'asyncio.run(main())' or 'asyncio.new_event_loop()' to initialize the loop.",
        docs_url="https://docs.python.org/3/whatsnew/3.10.html",
    ),
]


class GateDiagnostic(BaseModel):
    """Structured diagnostic representation of a gate failure."""

    gate_name: str
    status: str = "FAILED"
    failing_file: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    error_code: str | None = None
    error_message: str
    context_lines: list[str] = Field(default_factory=list)
    remediation_tip: str | None = None
    docs_url: str | None = None

    @property
    def location_str(self) -> str:
        if not self.failing_file:
            return "Unknown location"
        loc = self.failing_file
        if self.line_number is not None:
            loc += f":{self.line_number}"
            if self.column_number is not None:
                loc += f":{self.column_number}"
        return loc


def diagnose_gate_failure(
    gate_name: str,
    command: str,
    stdout: str | None = None,
    stderr: str | None = None,
    ecosystem: str | None = None,
) -> GateDiagnostic:
    """Analyze stdout/stderr from a failed gate and extract actionable diagnostics."""
    combined = "\n".join(filter(None, [stdout, stderr]))
    lines = [line.strip() for line in combined.splitlines() if line.strip()]

    failing_file: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    error_code: str | None = None
    error_message: str = ""
    context_lines: list[str] = []

    # 1. Regex patterns for compiler & test runner outputs
    # TypeScript / Angular / .NET / ESLint:
    # src/app/app.ts(12,5): error TS2339: ...
    # Services/DataSerializer.cs(45,18): error CS0618: ...
    compiler_match = re.search(
        r"(?P<file>[a-zA-Z0-9_\./\\-]+\.[a-zA-Z0-9]+)(?:\((?P<line>\d+),(?P<col>\d+)\)|:(?P<line2>\d+):(?P<col2>\d+))\s*[-:]?\s*error\s*(?P<code>(?:TS|NG|CS|NETSDK|[A-Z]+)\d+)?:?\s*(?P<msg>.+)",
        combined,
    )
    # Python pytest / mypy: tests/test_app.py:42: error: ... or FAILED tests/test_app.py::test_fn - AssertionError: ...
    python_match = re.search(
        r"(?P<file>[a-zA-Z0-9_\./\\-]+\.py):(?P<line>\d+):\s*(?:error|FAILED):\s*(?P<msg>.+)",
        combined,
    )
    pytest_summary_match = re.search(
        r"FAILED\s+(?P<file>[a-zA-Z0-9_\./\\-]+\.py)::(?P<fn>[a-zA-Z0-9_]+)\s*-\s*(?P<msg>.+)",
        combined,
    )

    matched = compiler_match or python_match or pytest_summary_match

    if matched:
        gd = matched.groupdict()
        failing_file = gd.get("file")
        line_str = gd.get("line") or gd.get("line2")
        if line_str:
            line_number = int(line_str)
        col_str = gd.get("col") or gd.get("col2")
        if col_str:
            column_number = int(col_str)
        error_code = gd.get("code")
        error_message = (gd.get("msg") or "").strip()

    # Fallback if no structured regex matched: grab the most descriptive error line from bottom up
    if not error_message:
        for line in reversed(lines):
            lower = line.lower()
            if any(k in lower for k in ("error:", "error ", "failed", "exception:", "ts", "cs")):
                error_message = line
                break
        if not error_message:
            error_message = lines[-1] if lines else f"Command '{command}' exited with failure."

    # 2. Match against Known Breaking Changes
    remediation_tip: str | None = None
    docs_url: str | None = None

    for kbc in KNOWN_BREAKING_CHANGES:
        if kbc.pattern.search(combined) or kbc.pattern.search(error_message):
            remediation_tip = f"{kbc.title}: {kbc.remediation_tip}"
            docs_url = kbc.docs_url
            break

    # If no specific breaking change matched, provide general ecosystem guidance
    if not remediation_tip:
        if ecosystem == "angular" or (failing_file and failing_file.endswith((".ts", ".html"))):
            remediation_tip = "Check the official Angular update guide at https://update.angular.io for breaking changes."
            docs_url = "https://update.angular.io"
        elif ecosystem == "dotnet" or (failing_file and failing_file.endswith(".cs")):
            remediation_tip = "Check Microsoft's .NET breaking changes guide at https://learn.microsoft.com/en-us/dotnet/core/compatibility/."
            docs_url = "https://learn.microsoft.com/en-us/dotnet/core/compatibility/"
        elif ecosystem == "python" or (failing_file and failing_file.endswith(".py")):
            remediation_tip = "Check the package release notes or Python what's new guide for deprecations."

    return GateDiagnostic(
        gate_name=gate_name,
        status="FAILED",
        failing_file=failing_file,
        line_number=line_number,
        column_number=column_number,
        error_code=error_code,
        error_message=error_message,
        context_lines=context_lines,
        remediation_tip=remediation_tip,
        docs_url=docs_url,
    )


def format_diagnostic_panel(diagnostic: GateDiagnostic) -> Panel:
    """Format a GateDiagnostic as a Rich terminal panel."""
    lines: list[str] = [
        f"[bold red]❌ Verification Gate Failed:[/bold red] [yellow]{diagnostic.gate_name}[/yellow]",
    ]

    if diagnostic.failing_file:
        lines.append(f"[bold cyan]📁 File:[/bold cyan] {diagnostic.location_str}")

    err_text = diagnostic.error_message
    if diagnostic.error_code:
        err_text = f"[{diagnostic.error_code}] {err_text}"
    lines.append(f"[bold red]🔍 Error Details:[/bold red] {err_text}")

    if diagnostic.remediation_tip:
        lines.append(f"\n[bold green]💡 Remediation Tip:[/bold green] {diagnostic.remediation_tip}")

    if diagnostic.docs_url:
        lines.append(f"[dim]🌐 Documentation:[/dim] [link={diagnostic.docs_url}]{diagnostic.docs_url}[/link]")

    return Panel(
        "\n".join(lines),
        title="AMstraLift Smart Diagnostic",
        border_style="red",
    )
