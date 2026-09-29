"""Tests for DockerfileUpdater — covers all four ecosystems, multi-stage,
docker-compose, edge cases, and zero-regression assertions.

All tests use temporary directories — no real filesystem is modified.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from amstralift.adapters.docker_updater import (
    DockerfileUpdater,
    _resolve_dotnet_tag,
    _resolve_node_tag,
    _resolve_python_tag,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Unit: tag resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveDotnetTag:
    def test_bare_tag(self):
        result = _resolve_dotnet_tag("mcr.microsoft.com/dotnet/aspnet", "8.0", "9")
        assert result == "9.0"

    def test_alpine_suffix_preserved(self):
        result = _resolve_dotnet_tag("mcr.microsoft.com/dotnet/sdk", "8.0-alpine", "9")
        assert result == "9.0-alpine"

    def test_jammy_suffix_preserved(self):
        result = _resolve_dotnet_tag("mcr.microsoft.com/dotnet/runtime", "8.0-jammy", "10")
        assert result == "10.0-jammy"

    def test_sdk_image(self):
        result = _resolve_dotnet_tag("mcr.microsoft.com/dotnet/sdk", "8.0", "net9.0")
        assert result == "9.0"

    def test_non_dotnet_image_returns_none(self):
        result = _resolve_dotnet_tag("nginx", "1.25", "9")
        assert result is None

    def test_malformed_tag_returns_none(self):
        result = _resolve_dotnet_tag("mcr.microsoft.com/dotnet/aspnet", "latest", "9")
        assert result is None


class TestResolveNodeTag:
    def test_bare_major(self):
        result = _resolve_node_tag("node", "18", 20)
        assert result == "20"

    def test_alpine_suffix(self):
        result = _resolve_node_tag("node", "18-alpine", 20)
        assert result == "20-alpine"

    def test_slim_suffix(self):
        result = _resolve_node_tag("node", "16-slim", 20)
        assert result == "20-slim"

    def test_non_node_image_returns_none(self):
        result = _resolve_node_tag("nginx", "1.25-alpine", 20)
        assert result is None

    def test_malformed_tag_returns_none(self):
        result = _resolve_node_tag("node", "lts-alpine", 20)
        assert result is None


class TestResolvePythonTag:
    def test_bare_version(self):
        result = _resolve_python_tag("python", "3.11", "3.12")
        assert result == "3.12"

    def test_slim_suffix(self):
        result = _resolve_python_tag("python", "3.11-slim", "3.12")
        assert result == "3.12-slim"

    def test_alpine_suffix(self):
        result = _resolve_python_tag("python", "3.9-alpine", "3.12")
        assert result == "3.12-alpine"

    def test_non_python_image_returns_none(self):
        result = _resolve_python_tag("ubuntu", "22.04", "3.12")
        assert result is None

    def test_malformed_tag_returns_none(self):
        result = _resolve_python_tag("python", "latest", "3.12")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Integration: .NET Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

class TestDotnetDockerfile:
    def test_aspnet_tag_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", (
                "FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build\n"
                "WORKDIR /app\n"
                "FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            content = (repo / "Dockerfile").read_text()
            assert "sdk:9.0" in content
            assert "aspnet:9.0" in content
            assert "sdk:8.0" not in content
            assert "aspnet:8.0" not in content
            assert result.total_changes == 2

    def test_alpine_variant_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", (
                "FROM mcr.microsoft.com/dotnet/sdk:8.0-alpine AS build\n"
                "FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine AS runtime\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="net9.0")

            content = (repo / "Dockerfile").read_text()
            assert "sdk:9.0-alpine" in content
            assert "aspnet:9.0-alpine" in content
            assert result.total_changes == 2

    def test_no_dockerfile_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")
            assert result.total_changes == 0
            assert result.dockerfiles_found == []

    def test_non_dotnet_image_not_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            original = "FROM nginx:1.25-alpine\n"
            _write(repo / "Dockerfile", original)

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            content = (repo / "Dockerfile").read_text()
            assert content == original
            assert result.total_changes == 0


# ─────────────────────────────────────────────────────────────────────────────
# Integration: Angular / React (Node) Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

class TestAngularDockerfile:
    def test_node_tag_updated_for_angular_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", (
                "FROM node:18-alpine AS build\n"
                "WORKDIR /app\n"
                "FROM nginx:1.25-alpine\n"
            ))

            # Angular 18 → 19, Node maps 19→ node:20
            result = DockerfileUpdater.update(repo, ecosystem="angular", target_version="19")

            content = (repo / "Dockerfile").read_text()
            assert "node:20-alpine" in content
            assert "node:18-alpine" not in content
            # nginx should NOT be touched
            assert "nginx:1.25-alpine" in content
            assert result.total_changes == 1

    def test_react_node_tag_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", "FROM node:16-slim AS build\n")

            result = DockerfileUpdater.update(repo, ecosystem="react", target_version="18")

            content = (repo / "Dockerfile").read_text()
            # react 18 → NODE_LTS_VERSIONS[18] = 20
            assert "node:20-slim" in content
            assert result.total_changes == 1


# ─────────────────────────────────────────────────────────────────────────────
# Integration: Python Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

class TestPythonDockerfile:
    def test_python_tag_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", "FROM python:3.11-slim\n")

            result = DockerfileUpdater.update(repo, ecosystem="python", target_version=">=3.12")

            content = (repo / "Dockerfile").read_text()
            assert "python:3.12-slim" in content
            assert "python:3.11-slim" not in content
            assert result.total_changes == 1

    def test_alpine_variant_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", "FROM python:3.9-alpine\n")

            DockerfileUpdater.update(repo, ecosystem="python", target_version="3.12")

            content = (repo / "Dockerfile").read_text()
            assert "python:3.12-alpine" in content


# ─────────────────────────────────────────────────────────────────────────────
# Integration: docker-compose.yml
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerCompose:
    def test_compose_image_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "docker-compose.yml", (
                "services:\n"
                "  api:\n"
                "    image: mcr.microsoft.com/dotnet/aspnet:8.0\n"
                "  db:\n"
                "    image: postgres:15\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            content = (repo / "docker-compose.yml").read_text()
            assert "mcr.microsoft.com/dotnet/aspnet:9.0" in content
            # postgres should NOT be touched
            assert "postgres:15" in content
            assert result.total_changes == 1

    def test_compose_yaml_variant_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "docker-compose.yaml", (
                "services:\n"
                "  app:\n"
                "    image: python:3.11-slim\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="python", target_version="3.12")

            content = (repo / "docker-compose.yaml").read_text()
            assert "python:3.12-slim" in content
            assert result.total_changes == 1


# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiStageDockerfile:
    def test_both_build_and_runtime_stages_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", (
                "# Build stage\n"
                "FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build\n"
                "WORKDIR /src\n"
                "COPY . .\n"
                "RUN dotnet publish -o /out\n"
                "\n"
                "# Runtime stage\n"
                "FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime\n"
                "COPY --from=build /out /app\n"
                "ENTRYPOINT [\"dotnet\", \"App.dll\"]\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            content = (repo / "Dockerfile").read_text()
            assert "sdk:9.0 AS build" in content
            assert "aspnet:9.0 AS runtime" in content
            assert "sdk:8.0" not in content
            assert "aspnet:8.0" not in content
            assert result.total_changes == 2

    def test_alias_preserved_in_from_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", "FROM mcr.microsoft.com/dotnet/sdk:8.0 AS builder\n")

            DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            content = (repo / "Dockerfile").read_text()
            assert "AS builder" in content


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases & safety
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_already_on_target_version_no_change(self):
        """If the Dockerfile already uses the target tag, nothing changes."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", "FROM mcr.microsoft.com/dotnet/aspnet:9.0\n")

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            assert result.total_changes == 0

    def test_dockerfile_in_subdirectory_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "src" / "api" / "Dockerfile",
                   "FROM mcr.microsoft.com/dotnet/aspnet:8.0\n")

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            assert result.total_changes == 1
            content = (repo / "src" / "api" / "Dockerfile").read_text()
            assert "aspnet:9.0" in content

    def test_node_modules_dockerfile_ignored(self):
        """Dockerfiles inside node_modules should never be touched."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "node_modules" / "some-pkg" / "Dockerfile",
                   "FROM node:18-alpine\n")

            result = DockerfileUpdater.update(repo, ecosystem="angular", target_version="19")

            # Should be ignored — node_modules is excluded
            assert result.total_changes == 0

    def test_docker_result_audit_log(self):
        """DockerUpdateResult records correct file, line_number, original, updated."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "Dockerfile", (
                "FROM ubuntu:22.04\n"
                "FROM mcr.microsoft.com/dotnet/aspnet:8.0\n"
            ))

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            assert len(result.changes) == 1
            change = result.changes[0]
            assert change.line_number == 2
            assert "8.0" in change.original
            assert "9.0" in change.updated
            assert "dotnet" in change.reason.lower()

    def test_no_tag_from_line_not_touched(self):
        """FROM ubuntu (no :tag) should not be modified."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            original = "FROM ubuntu\n"
            _write(repo / "Dockerfile", original)

            result = DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            assert (repo / "Dockerfile").read_text() == original
            assert result.total_changes == 0

    def test_comments_in_dockerfile_preserved(self):
        """Comment lines should be left exactly as-is."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            content = (
                "# syntax=docker/dockerfile:1\n"
                "FROM mcr.microsoft.com/dotnet/sdk:8.0\n"
                "# Build the app\n"
            )
            _write(repo / "Dockerfile", content)

            DockerfileUpdater.update(repo, ecosystem="dotnet", target_version="9")

            result_content = (repo / "Dockerfile").read_text()
            assert "# syntax=docker/dockerfile:1" in result_content
            assert "# Build the app" in result_content
