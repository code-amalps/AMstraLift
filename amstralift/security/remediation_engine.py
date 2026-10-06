"""Remediation engine for applying direct and transitive dependency updates across manifests."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from amstralift.adapters.base import get_node_execution_env
from amstralift.core.workspace import safe_rglob
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

        # Clean up problematic native binary & incompatible multi-major routing overrides if present
        if "overrides" in pkg_data and isinstance(pkg_data["overrides"], dict):
            for bad_key in list(pkg_data["overrides"].keys()):
                if bad_key in ("esbuild", "path-to-regexp") or bad_key.startswith(("@esbuild/", "@swc/", "@rollup/")):
                    del pkg_data["overrides"][bad_key]
                    has_changes = True
            for parent_k, parent_v in list(pkg_data["overrides"].items()):
                if isinstance(parent_v, dict):
                    for bad_k in list(parent_v.keys()):
                        if bad_k in ("esbuild", "path-to-regexp") or bad_k.startswith(("@esbuild/", "@swc/", "@rollup/")):
                            del parent_v[bad_k]
                            has_changes = True
                    if not parent_v:
                        del pkg_data["overrides"][parent_k]
                        has_changes = True
            if not pkg_data["overrides"]:
                del pkg_data["overrides"]
                has_changes = True

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
                # Native binary packages (like esbuild) and multi-major routing packages (like path-to-regexp)
                # break if globally overridden because different sub-dependencies require conflicting major branches.
                if (
                    pkg in ("esbuild", "path-to-regexp")
                    or pkg.startswith(("@esbuild/", "@swc/", "@rollup/"))
                ):
                    continue
                pkg_data.setdefault("overrides", {})[pkg] = target_v
                has_changes = True

        angular_core_upgrade = next(
            (item for item in plan.items if item.package_name == "@angular/core" and item.is_direct),
            None,
        )
        if angular_core_upgrade:
            target_version = re.sub(
                r"^[~^=<>v]+", "", angular_core_upgrade.target_version
            )
            target_major_match = re.match(r"\d+", target_version)
            if target_major_match:
                target_major = int(target_major_match.group())
                angular_tooling_packages = {
                    "@angular/build",
                    "@angular/cli",
                    "@angular/compiler-cli",
                    "@angular-devkit/architect",
                    "@angular-devkit/build-angular",
                    "@angular-devkit/core",
                    "@angular-devkit/schematics",
                    "@angular-devkit/schematics-cli",
                }
                for section in ("dependencies", "devDependencies"):
                    for package_name, current_version in pkg_data.get(section, {}).items():
                        if package_name not in angular_tooling_packages:
                            continue
                        current_version = str(current_version)
                        current_major_match = re.match(
                            r"\D*(\d+)", current_version
                        )
                        if (
                            current_major_match
                            and int(current_major_match.group(1)) < target_major
                        ):
                            prefix_match = re.match(r"^[~^]", current_version)
                            prefix = prefix_match.group() if prefix_match else ""
                            pkg_data[section][package_name] = f"{prefix}{target_version}"
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

        # If Angular project, modernize scripts and workspace JSON (orders libraries first for multi-project builds)
        if (repo_path / "angular.json").exists():
            try:
                from amstralift.adapters.angular import (
                    _modernize_angular_workspace_json,
                    _modernize_angular_scripts,
                )
                _modernize_angular_workspace_json(repo_path)
                modernized_scripts = _modernize_angular_scripts(repo_path)
                if modernized_scripts and "package.json" not in modified_files:
                    modified_files.append("package.json")
            except Exception:
                pass

        return modified_files

    @classmethod
    def apply_dotnet(cls, repo_path: Path, plan: RemediationPlan) -> list[str]:
        """Apply .NET direct PackageReference updates and transitive package pins."""
        modified_files: list[str] = []

        # 1. Check Directory.Packages.props (Central Package Management)
        props_files = safe_rglob(repo_path, "Directory.Packages.props")
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
        csproj_files = safe_rglob(repo_path, "*.csproj")
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
