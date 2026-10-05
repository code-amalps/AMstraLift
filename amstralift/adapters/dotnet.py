""".NET ecosystem adapter (Section 2, 8, 9).

Handles .NET NuGet package discovery via NuGet flatcontainer API,
*.csproj / packages.lock.json updates, dependency tiering, and dotnet build/test gates.
"""

import logging
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger("amstralift.adapters.dotnet")

from amstralift.adapters.base import BaseAdapter
from amstralift.core.workspace import safe_rglob
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
)
from amstralift.governance.dotnet_lifecycle import DotNetLifecycleGovernance

NUGET_FLATCONTAINER_BASE = "https://api.nuget.org/v3-flatcontainer"

TIER_3_PATTERNS = [
    re.compile(r".*(identity|auth|security|crypto|payment|stripe|jwt|certificate).*", re.IGNORECASE),
    re.compile(r"^Microsoft\.AspNetCore\.Authentication.*", re.IGNORECASE),
]

TIER_2_PATTERNS = [
    re.compile(r"^Microsoft\.EntityFrameworkCore.*", re.IGNORECASE),
    re.compile(r".*(database|dapper|orm|mediatr|automapper|routing).*", re.IGNORECASE),
]


def get_installed_dotnet_sdk_majors() -> list[int]:
    """Detect installed .NET SDK major versions via 'dotnet --list-sdks'."""
    dotnet_bin = shutil.which("dotnet")
    if not dotnet_bin:
        return []
    try:
        res = subprocess.run([dotnet_bin, "--list-sdks"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return []
        majors: set[int] = set()
        for line in res.stdout.splitlines():
            m = re.match(r"^\s*(\d+)\.\d+", line.strip())
            if m:
                majors.add(int(m.group(1)))
        return sorted(list(majors))
    except Exception:
        return []


def extract_major_version(ver_str: str) -> int | None:
    """Extract integer major version from a version string."""
    clean = ver_str.strip().lstrip("^~>=<")
    match = re.match(r"^(\d+)", clean)
    if match:
        return int(match.group(1))
    return None


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
        incremental: bool = True,
        prefer_lts: bool = True,
    ):
        self.nuget_base = nuget_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.test_command = test_command
        self.build_command = build_command
        self.incremental = incremental
        self.prefer_lts = prefer_lts

    @property
    def name(self) -> str:
        return "dotnet"

    def detect(self, repo_path: Path) -> bool:
        """Detect .NET project via *.csproj, *.fsproj, *.sln, Directory.Build.props, or Directory.Packages.props."""
        return (
            any(repo_path.glob("*.csproj"))
            or any(repo_path.glob("*.sln"))
            or any(repo_path.glob("*.fsproj"))
            or (repo_path / "Directory.Build.props").exists()
            or (repo_path / "Directory.Packages.props").exists()
            or bool(safe_rglob(repo_path, "*.csproj"))
            or bool(safe_rglob(repo_path, "*.sln"))
            or bool(safe_rglob(repo_path, "Directory.Packages.props"))
        )

    def fetch_latest_version(self, package_name: str, target_major: int | None = None) -> str | None:
        """Fetch latest stable GA version from NuGet flatcontainer API (never pre-release).

        If target_major is provided, returns latest stable GA version matching that major version.
        """
        url = f"{self.nuget_base}/{package_name.lower()}/index.json"
        try:
            from amstralift.security.osv_client import create_resilient_httpx_client
            with create_resilient_httpx_client(timeout=self.timeout_seconds) as client:
                res = client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    versions = data.get("versions", [])
                    stable = [v for v in versions if "-" not in v]
                    if target_major is not None:
                        matching = []
                        for v in stable:
                            m = re.match(r"^(\d+)\.", v)
                            if m and int(m.group(1)) == target_major:
                                matching.append(v)
                        if matching:
                            return matching[-1]
                        return None
                    if stable:
                        return stable[-1]
        except Exception:
            return None
        return None

    def discover_candidates(self, repo_path: Path) -> list[DependencyChange]:
        """Scan *.csproj, Directory.Build.props, and Directory.Packages.props for TargetFramework and PackageReference entries."""
        candidates: list[DependencyChange] = []
        csproj_files = safe_rglob(repo_path, ["*.csproj", "*.fsproj"])
        props_files = safe_rglob(repo_path, ["Directory.Build.props", "Directory.Build.targets"])
        cpm_files = safe_rglob(repo_path, "Directory.Packages.props")

        # 1. Discover TargetFramework upgrades, constrained by installed host .NET SDK
        installed_sdks = get_installed_dotnet_sdk_majors()
        max_sdk_major = max(installed_sdks) if installed_sdks else None

        discovered_tfms: set[str] = set()
        for fpath in (*props_files, *csproj_files):
            try:
                tree = ET.parse(fpath)
                root = tree.getroot()
                for tag in ("TargetFramework", "TargetFrameworks"):
                    for node in root.findall(f".//{tag}"):
                        if node is not None and node.text:
                            for raw_tfm in node.text.split(";"):
                                tfm = raw_tfm.strip()
                                if tfm and tfm not in discovered_tfms:
                                    discovered_tfms.add(tfm)
                                    decision = DotNetLifecycleGovernance.evaluate_tfm(
                                        tfm,
                                        prefer_lts=self.prefer_lts,
                                        incremental=self.incremental,
                                        max_supported_major=max_sdk_major,
                                    )
                                    if decision and decision.should_upgrade:
                                        candidates.append(
                                            DependencyChange(
                                                package_name="Microsoft.NET.TargetFramework",
                                                from_version=decision.current_tfm,
                                                to_version=decision.target_tfm,
                                                change_type="direct",
                                                tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
                                                rationale=decision.reason,
                                            )
                                        )
            except Exception:
                continue

        # 2. Discover PackageReference upgrades (supporting Central Package Management)
        declared_pkgs: dict[str, str] = {}

        # 2a. Inspect Central Package Management (Directory.Packages.props)
        for cpm in cpm_files:
            try:
                tree = ET.parse(cpm)
                root = tree.getroot()
                for pv in root.findall(".//PackageVersion") + root.findall(".//GlobalPackageReference"):
                    pkg = pv.get("Include") or pv.get("Update")
                    ver = pv.get("Version") or pv.findtext("Version")
                    if pkg and ver and not ver.startswith("$"):
                        declared_pkgs[pkg] = ver
            except Exception:
                continue

        # 2b. Inspect *.csproj and *.fsproj files
        for csproj in csproj_files:
            try:
                tree = ET.parse(csproj)
                root = tree.getroot()
                for pr in root.findall(".//PackageReference"):
                    pkg = pr.get("Include") or pr.get("Update")
                    ver = pr.get("Version") or pr.findtext("Version")
                    if pkg and ver and not ver.startswith("$"):
                        declared_pkgs.setdefault(pkg, ver)
            except Exception:
                continue

        # Determine target dotnet major if TargetFramework upgrade was scheduled
        target_dotnet_major: int | None = None
        for c in candidates:
            if c.package_name == "Microsoft.NET.TargetFramework":
                m = re.match(r"^net(\d+)\.0$", c.to_version)
                if m:
                    target_dotnet_major = int(m.group(1))
                    break

        # Check for ecosystem incompatibilities
        has_graph_auth = "Microsoft.Graph.Auth" in declared_pkgs

        # 2c. Query NuGet for package updates
        for pkg, ver in declared_pkgs.items():
            try:
                cur_pkg_major = extract_major_version(ver)
                target_pkg_major: int | None = None

                # Special compatibility guards for known breaking ecosystem rewrites:
                if pkg == "Microsoft.Graph" and (has_graph_auth or cur_pkg_major == 4):
                    # Microsoft Graph v5 is a major rewrite incompatible with Microsoft.Graph.Auth and v4 syntax (.Request(), IAuthenticationProvider).
                    # Constrain to highest v4 release (4.54.0) to prevent compilation errors.
                    target_pkg_major = 4
                elif pkg == "Microsoft.Graph.Auth":
                    # Deprecated preview package with no GA; do not attempt upgrade
                    continue
                else:
                    # Framework packages (align with target .NET runtime major)
                    is_framework_pkg = any(
                        pkg.startswith(prefix) for prefix in (
                            "Microsoft.AspNetCore.",
                            "Microsoft.Extensions.",
                            "Microsoft.EntityFrameworkCore.",
                            "System.Text.Json",
                            "System.Net.Http.Json",
                            "Microsoft.NET.Test.Sdk",
                        )
                    )

                    if is_framework_pkg and target_dotnet_major is not None:
                        target_pkg_major = target_dotnet_major
                    elif self.incremental and cur_pkg_major is not None:
                        # In incremental mode, third-party packages must NOT cross major versions
                        target_pkg_major = cur_pkg_major

                try:
                    latest = self.fetch_latest_version(pkg, target_major=target_pkg_major)
                except TypeError:
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
        """Apply package and TargetFramework updates to *.csproj, Directory.Build.props, Directory.Packages.props, and packages.lock.json."""
        csproj_files = safe_rglob(repo_path, ["*.csproj", "*.fsproj"])
        props_files = safe_rglob(repo_path, ["Directory.Build.props", "Directory.Build.targets"])
        cpm_files = safe_rglob(repo_path, "Directory.Packages.props")

        # 1. Update TargetFramework across *.csproj and Directory.Build.props
        for fpath in (*props_files, *csproj_files):
            try:
                content = fpath.read_text(encoding="utf-8")
                orig = content
                for c in changes:
                    if c.package_name == "Microsoft.NET.TargetFramework":
                        pattern = rf'(<TargetFramework(?:s)?\s*>\s*){re.escape(c.from_version)}(\s*</TargetFramework(?:s)?>)'
                        content = re.sub(pattern, rf'\g<1>{c.to_version}\g<2>', content, flags=re.IGNORECASE)
                if content != orig:
                    fpath.write_text(content, encoding="utf-8")
            except Exception:
                pass

        # 2. Update PackageReference in *.csproj
        for csproj in csproj_files:
            try:
                content = csproj.read_text(encoding="utf-8")
                orig = content
                for c in changes:
                    if c.package_name != "Microsoft.NET.TargetFramework":
                        clean_to = c.to_version.lstrip("^~")
                        pattern_attr = rf'(<PackageReference\s+[^>]*Include="{re.escape(c.package_name)}"[^>]*Version=)"[^"]*"'
                        content = re.sub(pattern_attr, rf'\1"{clean_to}"', content, flags=re.IGNORECASE)
                        pattern_child = rf'(<PackageReference\s+[^>]*Include="{re.escape(c.package_name)}"[^>]*>\s*<Version>)[^<]*(</Version>)'
                        content = re.sub(pattern_child, rf'\g<1>{clean_to}\g<2>', content, flags=re.IGNORECASE)
                if content != orig:
                    csproj.write_text(content, encoding="utf-8")
            except Exception:
                pass

        # 3. Update Directory.Packages.props (Central Package Management)
        for cpm in cpm_files:
            try:
                content = cpm.read_text(encoding="utf-8")
                orig = content
                for c in changes:
                    if c.package_name != "Microsoft.NET.TargetFramework":
                        clean_to = c.to_version.lstrip("^~")
                        pattern_attr = rf'(<(?:PackageVersion|GlobalPackageReference)\s+[^>]*Include="{re.escape(c.package_name)}"[^>]*Version=)"[^"]*"'
                        content = re.sub(pattern_attr, rf'\1"{clean_to}"', content, flags=re.IGNORECASE)
                        pattern_child = rf'(<(?:PackageVersion|GlobalPackageReference)\s+[^>]*Include="{re.escape(c.package_name)}"[^>]*>\s*<Version>)[^<]*(</Version>)'
                        content = re.sub(pattern_child, rf'\g<1>{clean_to}\g<2>', content, flags=re.IGNORECASE)
                if content != orig:
                    cpm.write_text(content, encoding="utf-8")
            except Exception:
                pass

        # 4. Update packages.lock.json
        for lock_file in safe_rglob(repo_path, "packages.lock.json"):
            try:
                lock_content = lock_file.read_text(encoding="utf-8")
                orig = lock_content
                for c in changes:
                    if c.package_name != "Microsoft.NET.TargetFramework":
                        pattern = rf'("{re.escape(c.package_name)}":\s*\{{[^}}]*"resolved":\s*)"[^"]*"'
                        replacement = rf'\1"{c.to_version}"'
                        lock_content = re.sub(pattern, replacement, lock_content, flags=re.IGNORECASE)
                if lock_content != orig:
                    lock_file.write_text(lock_content, encoding="utf-8")
            except Exception:
                pass

        # ── Docker: update FROM / image: tags in Dockerfiles & docker-compose ──
        tfm_change = next(
            (c for c in changes if c.package_name == "Microsoft.NET.TargetFramework"),
            None,
        )
        if tfm_change:
            # to_version is like "net9.0" — pass the major version number
            from amstralift.adapters.docker_updater import DockerfileUpdater
            DockerfileUpdater.update(repo_path, ecosystem="dotnet", target_version=tfm_change.to_version)

    def run_build_and_tests(
        self,
        repo_path: Path,
        timeout_seconds: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> GateSummary:
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

        for i, (gate_name, cmd, is_required) in enumerate(gates):
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

            if progress_callback:
                progress_callback(f"↳ [Gate {i+1}/{len(gates)}] Executing {gate_name} ('{cmd}')...")

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
                    timeout=timeout_seconds,
                )
                duration = time.time() - start_t
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                if proc.returncode == 0:
                    status = GateStatus.REQUIRED_PASSED if is_required else GateStatus.OPTIONAL_PASSED
                    if progress_callback:
                        progress_callback(f"  ✔ Gate '{gate_name}' passed ({duration:.1f}s)")
                else:
                    status = GateStatus.REQUIRED_FAILED if is_required else GateStatus.OPTIONAL_FAILED
                    if progress_callback:
                        progress_callback(f"  ✖ Gate '{gate_name}' failed with exit code {proc.returncode} ({duration:.1f}s)")

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
            except subprocess.TimeoutExpired:
                duration = time.time() - start_t
                if progress_callback:
                    progress_callback(f"  ⚠ Gate '{gate_name}' timed out after {duration:.0f}s")
                timeout_note = (
                    f".NET {gate_name} timed out after {duration:.0f}s. "
                    "Test result is UNCERTAIN — run with --skip-tests / --allow-failed-gates to proceed."
                )
                results.append(
                    GateResult(
                        name=gate_name,
                        command=cmd,
                        status=GateStatus.REQUIRED_TIMEOUT,
                        exit_code=124,
                        stdout=timeout_note,
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

    def get_declared_dependencies(self, repo_path: Path) -> dict[str, str]:
        deps: dict[str, str] = {}
        # 1. Inspect Central Package Management (CPM) files: Directory.Packages.props, etc.
        for prop_pattern in ("Directory.Packages.props", "Directory.Build.props", "Directory.Build.targets", "Packages.props"):
            for prop_file in safe_rglob(repo_path, prop_pattern):
                try:
                    tree = ET.parse(prop_file)
                    for tag in (".//PackageVersion", ".//GlobalPackageReference", ".//PackageReference"):
                        for elem in tree.getroot().findall(tag):
                            pkg = elem.get("Include") or elem.get("Update")
                            ver = elem.get("Version")
                            if not ver:
                                ver_elem = elem.find("Version")
                                if ver_elem is not None and ver_elem.text:
                                    ver = ver_elem.text.strip()
                            if pkg and ver:
                                deps[pkg] = ver
                except Exception:
                    continue

        # 2. Inspect *.csproj files
        for csproj in safe_rglob(repo_path, "*.csproj"):
            try:
                tree = ET.parse(csproj)
                for pr in tree.getroot().findall(".//PackageReference"):
                    pkg = pr.get("Include") or pr.get("Update")
                    ver = pr.get("Version")
                    if not ver:
                        ver_elem = pr.find("Version")
                        if ver_elem is not None and ver_elem.text:
                            ver = ver_elem.text.strip()
                    if pkg and ver:
                        deps[pkg] = ver
            except Exception:
                continue
        return deps

    def apply_modernizations(self, repo_path: Path, modernize_flags: list[str]) -> list[str]:
        """Apply modern .NET code fixes, style rules, and analyzers via dotnet format."""
        if not modernize_flags:
            return []

        applied = []
        normalized = [f.strip().lower() for f in modernize_flags]

        if not shutil.which("dotnet"):
            return ["Skipped .NET modernizations: dotnet CLI not found in environment"]

        def _run_format(cmd: list[str], success_msg: str, timeout_secs: int) -> str:
            cmd_display = " ".join(cmd)
            try:
                res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout_secs)
                if res.returncode == 0:
                    return success_msg
                else:
                    return f"{cmd_display} returned exit code {res.returncode}"
            except subprocess.TimeoutExpired:
                logger.warning("'%s' timed out after %ds; skipping non-critical modernization", cmd_display, timeout_secs)
                return f"Skipped '{cmd_display}' (timed out after {timeout_secs}s; upgrade preserved)"
            except Exception as e:
                logger.warning("'%s' failed: %s; skipping non-critical modernization", cmd_display, e)
                return f"Skipped '{cmd_display}' ({e})"

        # 1. Code Style / Modern C# Syntax
        if any(f in ("style", "format", "all") for f in normalized):
            msg = _run_format(
                ["dotnet", "format", "style", "--severity", "warn", "--no-restore"],
                "Applied modern C# code style fixes via 'dotnet format style'",
                45,
            )
            applied.append(msg)

        # 2. Whitespace and Layout Modernization
        if any(f in ("whitespace", "all") for f in normalized):
            msg = _run_format(
                ["dotnet", "format", "whitespace", "--no-restore"],
                "Formatted whitespace layout via 'dotnet format whitespace'",
                30,
            )
            applied.append(msg)

        # 3. Roslyn Analyzers and Deprecation Fixes
        if any(f in ("analyzers", "fixes", "all") for f in normalized):
            msg = _run_format(
                ["dotnet", "format", "analyzers", "--severity", "warn", "--no-restore"],
                "Applied Roslyn analyzer deprecation code fixes via 'dotnet format analyzers'",
                60,
            )
            applied.append(msg)

        return applied


