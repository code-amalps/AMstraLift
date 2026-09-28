"""Remediation engine for applying direct and transitive dependency updates across manifests."""

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from amstralift.adapters.base import get_node_execution_env
from amstralift.security.models import RemediationPlan


class RemediationEngine:
    """Applies minimal, safe direct and transitive dependency changes."""

    @classmethod
    def apply_plan(cls, repo_path: Path, ecosystem: str, plan: RemediationPlan) -> list[str]:
        """Apply plan items using ecosystem-appropriate manifest and lockfile operations.

        Returns list of modified file paths.
        """
        eco = ecosystem.lower().strip()
        if eco in ("angular", "react", "npm"):
            return cls.apply_npm(repo_path, plan)
        elif eco in ("dotnet", "nuget"):
            return cls.apply_dotnet(repo_path, plan)
        elif eco in ("python", "pypi"):
            return cls.apply_python(repo_path, plan)
        return []

    @classmethod
    def apply_npm(cls, repo_path: Path, plan: RemediationPlan) -> list[str]:
        """Apply npm upgrades to package.json (direct & overrides) and regenerate lockfile."""
        pkg_json_path = repo_path / "package.json"
        if not pkg_json_path.exists():
            return []

        modified_files: list[str] = []
        try:
            pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        has_changes = False
        for item in plan.items:
            pkg = item.package_name
            target_v = item.target_version

            if item.is_direct:
                # Update dependencies or devDependencies
                if "dependencies" in pkg_data and pkg in pkg_data["dependencies"]:
                    pkg_data["dependencies"][pkg] = target_v
                    has_changes = True
                elif "devDependencies" in pkg_data and pkg in pkg_data["devDependencies"]:
                    pkg_data["devDependencies"][pkg] = target_v
                    has_changes = True
                else:
                    # Fallback to dependencies
                    pkg_data.setdefault("dependencies", {})[pkg] = target_v
                    has_changes = True
            else:
                # Transitive remediation via npm overrides
                pkg_data.setdefault("overrides", {})[pkg] = target_v
                has_changes = True

        if has_changes:
            pkg_json_path.write_text(json.dumps(pkg_data, indent=2) + "\n", encoding="utf-8")
            modified_files.append("package.json")

            # Regenerate lockfile if npm is available
            if shutil.which("npm"):
                try:
                    subprocess.run(
                        "npm install --package-lock-only --ignore-scripts",
                        shell=True,
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=180,
                        env=get_node_execution_env(),
                    )
                    lock_file = repo_path / "package-lock.json"
                    if lock_file.exists():
                        modified_files.append("package-lock.json")
                except Exception:
                    pass

        return modified_files

    @classmethod
    def apply_dotnet(cls, repo_path: Path, plan: RemediationPlan) -> list[str]:
        """Apply .NET direct PackageReference updates and transitive package pins."""
        modified_files: list[str] = []

        # 1. Check Directory.Packages.props (Central Package Management)
        props_files = list(repo_path.glob("**/Directory.Packages.props"))
        cpm_handled: set[str] = set()

        for props_path in props_files:
            content = props_path.read_text(encoding="utf-8")
            orig_content = content
            for item in plan.items:
                clean_target = item.target_version.lstrip("^~")
                pattern = rf'(<PackageVersion\s+[^>]*Include="{re.escape(item.package_name)}"[^>]*Version=)"[^"]*"'
                if re.search(pattern, content, flags=re.IGNORECASE):
                    content = re.sub(pattern, rf'\1"{clean_target}"', content, flags=re.IGNORECASE)
                    cpm_handled.add(item.package_name)

            if content != orig_content:
                props_path.write_text(content, encoding="utf-8")
                modified_files.append(str(props_path.relative_to(repo_path)))

        # 2. Update .csproj files
        csproj_files = list(repo_path.glob("**/*.csproj"))
        for csproj_path in csproj_files:
            content = csproj_path.read_text(encoding="utf-8")
            orig_content = content

            for item in plan.items:
                if item.package_name in cpm_handled:
                    continue

                clean_target = item.target_version.lstrip("^~")
                pattern = rf'(<PackageReference\s+[^>]*Include="{re.escape(item.package_name)}"[^>]*Version=)"[^"]*"'

                if re.search(pattern, content, flags=re.IGNORECASE):
                    content = re.sub(pattern, rf'\1"{clean_target}"', content, flags=re.IGNORECASE)
                elif not item.is_direct:
                    # Pin transitive dependency with explicit security comment
                    pin_snippet = (
                        f'    <!-- AMstraLift Security Fix: pin transitive {item.cve_id} -->\n'
                        f'    <PackageReference Include="{item.package_name}" Version="{clean_target}" />\n'
                    )
                    if "</ItemGroup>" in content:
                        content = content.replace("</ItemGroup>", f"{pin_snippet}  </ItemGroup>", 1)
                    elif "</Project>" in content:
                        content = content.replace("</Project>", f"  <ItemGroup>\n{pin_snippet}  </ItemGroup>\n</Project>")

            if content != orig_content:
                csproj_path.write_text(content, encoding="utf-8")
                modified_files.append(str(csproj_path.relative_to(repo_path)))

        # Restore to update project.assets.json and lockfiles if dotnet is present
        if shutil.which("dotnet"):
            try:
                subprocess.run(
                    "dotnet restore",
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except Exception:
                pass

        return list(set(modified_files))

    @classmethod
    def apply_python(cls, repo_path: Path, plan: RemediationPlan) -> list[str]:
        """Apply Python dependency changes to requirements.txt, pyproject.toml, or constraints.txt."""
        modified_files: list[str] = []

        # 1. Update requirements.txt
        req_path = repo_path / "requirements.txt"
        if req_path.exists():
            content = req_path.read_text(encoding="utf-8")
            orig_content = content
            for item in plan.items:
                pkg = item.package_name
                target_v = item.target_version.lstrip("^~")
                pattern = rf'(^{re.escape(pkg)}\s*[~>=<]=*)[^\r\n]+'
                if re.search(pattern, content, flags=re.MULTILINE | re.IGNORECASE):
                    content = re.sub(pattern, rf'\g<1>{target_v}', content, flags=re.MULTILINE | re.IGNORECASE)
                elif not item.is_direct:
                    # Append transitive constraint
                    content += f"\n# AMstraLift Security Fix: {item.cve_id}\n{pkg}>={target_v}\n"

            if content != orig_content:
                req_path.write_text(content, encoding="utf-8")
                modified_files.append("requirements.txt")

        # 2. Update pyproject.toml if present
        pyproject_path = repo_path / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8")
            orig_content = content
            for item in plan.items:
                if item.is_direct:
                    pkg = item.package_name
                    target_v = item.target_version.lstrip("^~")
                    pattern = rf'("{re.escape(pkg)}[~>=<]*)[^"]*"'
                    content = re.sub(pattern, rf'"{pkg}>={target_v}"', content, flags=re.IGNORECASE)

            if content != orig_content:
                pyproject_path.write_text(content, encoding="utf-8")
                modified_files.append("pyproject.toml")

        return list(set(modified_files))
