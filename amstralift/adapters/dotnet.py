""".NET ecosystem adapter (Section 2, 8, 9).

Handles .NET NuGet package discovery via NuGet flatcontainer API,
*.csproj / packages.lock.json updates, dependency tiering, and dotnet build/test gates.
"""

import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from amstralift.adapters.base import BaseAdapter
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
)

NUGET_FLATCONTAINER_BASE = "https://api.nuget.org/v3-flatcontainer"

TIER_3_PATTERNS = [
    re.compile(r".*(identity|auth|security|crypto|payment|stripe|jwt|certificate).*", re.IGNORECASE),
    re.compile(r"^Microsoft\.AspNetCore\.Authentication.*", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(r"^Microsoft\.EntityFrameworkCore.*", re.IGNORECASE),
    re.compile(r".*(database|dapper|orm|mediatr|automapper|routing).*", re.IGNORECASE),
]


def classify_dotnet_tier(package_name: str) -> DependencyTier:
    """Classify .NET NuGet package into Tier 1, 2, or 3."""
    for pattern in TIER_3_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_3_CRITICAL

    for pattern in TIER_2_PATTERNS:
        if pattern.search(package_name):
            return DependencyTier.TIER_2_VERIFY_BEHAVIOR

    return DependencyTier.TIER_1_SAFE


class DotNetAdapter(BaseAdapter):
    """Adapter for .NET repositories using dotnet CLI and NuGet."""

    def __init__(
        self,
        nuget_base: str = NUGET_FLATCONTAINER_BASE,
        timeout_seconds: float = 10.0,
        test_command: str | None = None,
        build_command: str | None = None,
    ):
        self.nuget_base = nuget_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.test_command = test_command
        self.build_command = build_command

    @property
    def name(self) -> str:
        return "dotnet"

    def detect(self, repo_path: Path) -> bool:
        """Detect .NET project via *.csproj, *.fsproj, *.sln, or Directory.Build.props."""
        return (
            any(repo_path.glob("*.csproj"))
            or any(repo_path.glob("*.sln"))
            or any(repo_path.glob("*.fsproj"))
            or any(repo_path.glob("**/*.csproj"))
            or any(repo_path.glob("**/*.sln"))
        )

    def fetch_latest_version(self, package_name: str) -> str | None:
        """Fetch latest stable version from NuGet flatcontainer API."""
        url = f"{self.nuget_base}/{package_name.lower()}/index.json"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    versions = data.get("versions", [])
                    stable = [v for v in versions if "-" not in v]
                    if stable:
                        return stable[-1]
                    if versions:
                        return versions[-1]
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Scan *.csproj files for PackageReference entries and find updates."""
        candidates: list[DependencyChange] = []
        csproj_files = list(repo_path.glob("**/*.csproj"))

        for csproj in csproj_files:
            try:
                tree = ET.parse(csproj)
                root = tree.getroot()
                for pr in root.findall(".//PackageReference"):
                    pkg = pr.get("Include") or pr.get("Update")
                    ver = pr.get("Version")
                    if pkg and ver:
                        latest = self.fetch_latest_version(pkg)
                        if latest and latest != ver:
                            tier = classify_dotnet_tier(pkg)
                            candidates.append(
                                DependencyChange(
                                    package_name=pkg,
                                    from_version=ver,
                                    to_version=latest,
                                    change_type="direct",
                                    tier=tier,
                                    rationale=f"Upgrade .NET NuGet package {pkg} to {latest} via NuGet API",
                                )
                            )
            except Exception:
                continue

        return candidates

    def apply_upgrade(self, repo_path: Path, changes: list[DependencyChange]) -> None:
        """Apply package updates to *.csproj files and packages.lock.json."""
        csproj_files = list(repo_path.glob("**/*.csproj"))
        for csproj in csproj_files:
            content = csproj.read_text(encoding="utf-8")
            for c in changes:
                pattern = rf'(<PackageReference\s+[^>]*Include="{re.escape(c.package_name)}"[^>]*Version=)"[^"]*"'
                replacement = rf'\1"{c.to_version}"'
                content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
            csproj.write_text(content, encoding="utf-8")

        for lock_file in repo_path.glob("**/packages.lock.json"):
            lock_content = lock_file.read_text(encoding="utf-8")
            for c in changes:
                pattern = rf'("{re.escape(c.package_name)}":\s*\{{[^}}]*"resolved":\s*)"[^"]*"'
                replacement = rf'\1"{c.to_version}"'
                lock_content = re.sub(pattern, replacement, lock_content, flags=re.IGNORECASE)
            lock_file.write_text(lock_content, encoding="utf-8")

    def run_build_and_tests(self, repo_path: Path) -> GateSummary:
        """Execute dotnet build and dotnet test gates."""
        results: list[GateResult] = []
        has_dotnet = shutil.which("dotnet") is not None

        default_test = "dotnet test" if has_dotnet else None
        default_build = "dotnet build" if has_dotnet else None

        test_cmd = self.test_command if self.test_command is not None else default_test
        build_cmd = self.build_command if self.build_command is not None else default_build

        gates = [
            ("test", test_cmd, True),
            ("build", build_cmd, True),
        ]

        for gate_name, cmd, is_required in gates:
            if not cmd:
                status = GateStatus.REQUIRED_SKIPPED if is_required else GateStatus.OPTIONAL_PASSED
                results.append(
                    GateResult(
                        name=gate_name,
                        command="N/A",
                        status=status,
                        exit_code=0,
                        stdout="dotnet CLI not detected in environment",
                    )
                )
                continue

            start_t = time.time()
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                )
                duration = time.time() - start_t
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    status = GateStatus.REQUIRED_PASSED if is_required else GateStatus.OPTIONAL_PASSED
                else:
                    status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED

                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=proc.returncode,
                        stdout=stdout,
                        stderr=stderr,
                        duration_seconds=duration,
                    )
                )
            except Exception as e:
                duration = time.time() - start_t
                status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=status,
                        exit_code=1,
                        stderr=str(e),
                        duration_seconds=duration,
                    )
                )

        return GateSummary(results=results)
