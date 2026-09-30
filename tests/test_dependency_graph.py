"""Unit tests for DependencyGraphAnalyzer across npm, .NET, and Python ecosystems."""

import json
from pathlib import Path

from amstralift.security.dependency_graph import DependencyGraphAnalyzer


def test_npm_direct_and_transitive_graph(tmp_path: Path):
    """Verify npm direct vs transitive discovery and ancestor tracking."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps({
            "name": "sample-app",
            "dependencies": {"express": "^4.17.1"},
            "devDependencies": {"karma": "^6.0.0"},
        }),
        encoding="utf-8",
    )

    lock_json = tmp_path / "package-lock.json"
    lock_json.write_text(
        json.dumps({
            "name": "sample-app",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "dependencies": {"express": "^4.17.1"},
                    "devDependencies": {"karma": "^6.0.0"},
                },
                "node_modules/express": {
                    "version": "4.17.1",
                    "dependencies": {"qs": "6.7.0"},
                },
                "node_modules/karma": {
                    "version": "6.0.0",
                    "dependencies": {"socket.io": "3.1.0"},
                },
                "node_modules/qs": {
                    "version": "6.7.0",
                },
                "node_modules/socket.io": {
                    "version": "3.1.0",
                },
            },
        }),
        encoding="utf-8",
    )

    deps = DependencyGraphAnalyzer.analyze_npm(tmp_path)
    dep_map = {d.package_name: d for d in deps}

    assert "express" in dep_map
    assert dep_map["express"].is_direct
    assert dep_map["express"].version == "^4.17.1"

    assert "qs" in dep_map
    assert not dep_map["qs"].is_direct
    assert "express" in dep_map["qs"].introduced_by
    assert dep_map["qs"].display_introduced_by == "express"


def test_dotnet_cpm_and_transitive_graph(tmp_path: Path):
    """Verify .NET Central Package Management (Directory.Packages.props) and .csproj discovery."""
    props_file = tmp_path / "Directory.Packages.props"
    props_file.write_text(
        """<Project>
  <ItemGroup>
    <PackageVersion Include="Newtonsoft.Json" Version="13.0.1" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    csproj_file = tmp_path / "App.csproj"
    csproj_file.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" />
    <PackageReference Include="Serilog" Version="3.1.0" />
  </ItemGroup>
</Project>""",
        encoding="utf-8",
    )

    deps = DependencyGraphAnalyzer.analyze_dotnet(tmp_path)
    dep_map = {d.package_name: d for d in deps}

    assert "Newtonsoft.Json" in dep_map
    assert dep_map["Newtonsoft.Json"].is_direct
    assert dep_map["Newtonsoft.Json"].version == "13.0.1"

    assert "Serilog" in dep_map
    assert dep_map["Serilog"].is_direct
    assert dep_map["Serilog"].version == "3.1.0"
    assert dep_map["Serilog"].target_framework == "net9.0"


def test_python_requirements_graph(tmp_path: Path):
    """Verify Python direct dependency parsing from requirements.txt."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        """# Production dependencies
requests>=2.25.0
flask==2.0.1
urllib3~=1.26.5
""",
        encoding="utf-8",
    )

    deps = DependencyGraphAnalyzer.analyze_python(tmp_path)
    dep_map = {d.package_name: d for d in deps}

    assert "requests" in dep_map
    assert dep_map["requests"].is_direct
    assert "flask" in dep_map
    assert "urllib3" in dep_map


def test_npm_multiversion_transitive_graph(tmp_path: Path):
    """Verify that multiple versions of the same package in lockfile are all discovered."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps({
            "name": "sample-app",
            "dependencies": {},
            "devDependencies": {"@commitlint/cli": "^11.0.0"},
        }),
        encoding="utf-8",
    )

    lock_json = tmp_path / "package-lock.json"
    lock_json.write_text(
        json.dumps({
            "name": "sample-app",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "devDependencies": {"@commitlint/cli": "^11.0.0"},
                },
                "node_modules/@commitlint/cli": {
                    "version": "11.0.0",
                    "dependencies": {"semver": "7.3.2"},
                },
                "node_modules/semver": {
                    "version": "5.7.2",
                },
                "node_modules/@commitlint/cli/node_modules/semver": {
                    "version": "7.3.2",
                },
            },
        }),
        encoding="utf-8",
    )

    deps = DependencyGraphAnalyzer.analyze_npm(tmp_path)
    semver_deps = [d for d in deps if d.package_name == "semver"]
    assert len(semver_deps) == 2
    versions = {d.version for d in semver_deps}
    assert versions == {"5.7.2", "7.3.2"}
    nested_semver = next(d for d in semver_deps if d.version == "7.3.2")
    assert not nested_semver.is_direct
    assert "@commitlint/cli" in nested_semver.introduced_by
