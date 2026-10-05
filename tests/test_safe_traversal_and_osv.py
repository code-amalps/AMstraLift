"""Tests for safe_rglob traversal and optimized OSV vulnerability scanning."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amstralift.core.workspace import safe_rglob
from amstralift.security.models import DiscoveredDependency
from amstralift.security.osv_client import OSVClient


def test_safe_rglob_matches_patterns_and_skips_excluded(tmp_path: Path):
    # Setup test file tree
    src_dir = tmp_path / "src" / "app"
    src_dir.mkdir(parents=True)
    (src_dir / "app.component.ts").write_text("export class AppComponent {}", encoding="utf-8")
    (src_dir / "app.component.html").write_text("<div>App</div>", encoding="utf-8")
    (src_dir / "styles.scss").write_text("body { margin: 0; }", encoding="utf-8")

    # Excluded directories
    node_modules = tmp_path / "node_modules" / "some-lib"
    node_modules.mkdir(parents=True)
    (node_modules / "index.ts").write_text("export const x = 1;", encoding="utf-8")
    (node_modules / "Dockerfile").write_text("FROM node:20", encoding="utf-8")

    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (git_dir / "pre-commit.ts").write_text("exit 0", encoding="utf-8")

    dist_dir = tmp_path / "dist" / "output"
    dist_dir.mkdir(parents=True)
    (dist_dir / "bundle.ts").write_text("export const y = 2;", encoding="utf-8")

    # Root Dockerfile
    (tmp_path / "Dockerfile").write_text("FROM node:20", encoding="utf-8")

    # 1. safe_rglob for *.ts
    ts_files = safe_rglob(tmp_path, "*.ts")
    assert len(ts_files) == 1
    assert ts_files[0].name == "app.component.ts"

    # 2. safe_rglob for Dockerfile*
    dockerfiles = safe_rglob(tmp_path, "Dockerfile*")
    assert len(dockerfiles) == 1
    assert dockerfiles[0] == tmp_path / "Dockerfile"

    # 3. safe_rglob with multiple patterns
    multi = safe_rglob(tmp_path, ["*.html", "*.scss"])
    assert len(multi) == 2
    names = {f.name for f in multi}
    assert names == {"app.component.html", "styles.scss"}


def test_safe_rglob_resilient_to_inaccessible_paths(tmp_path: Path, monkeypatch):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("export const a = 1;", encoding="utf-8")

    # Simulate an OSError during traversal
    real_walk = os.walk

    def faulty_walk(top, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror:
            onerror(FileNotFoundError(3, "The system cannot find the path specified", "fake_junction"))
        return real_walk(top, **kwargs)

    monkeypatch.setattr(os, "walk", faulty_walk)

    # Must complete safely without raising FileNotFoundError
    results = safe_rglob(tmp_path, "*.ts")
    assert len(results) == 1
    assert results[0].name == "index.ts"


def test_osv_scan_deduplicates_and_caches():
    client = OSVClient(timeout_seconds=5.0)

    # 5 dependencies, but only 2 unique packages
    deps = [
        DiscoveredDependency(package_name="lodash", version="4.17.20", is_direct=True, introduced_by=[]),
        DiscoveredDependency(package_name="lodash", version="4.17.20", is_direct=False, introduced_by=["parent-a"]),
        DiscoveredDependency(package_name="lodash", version="4.17.20", is_direct=False, introduced_by=["parent-b"]),
        DiscoveredDependency(package_name="axios", version="0.21.1", is_direct=True, introduced_by=[]),
        DiscoveredDependency(package_name="axios", version="0.21.1", is_direct=False, introduced_by=["parent-c"]),
    ]

    mock_batch_response = MagicMock()
    mock_batch_response.status_code = 200
    mock_batch_response.json.return_value = {
        "results": [
            {"vulns": [{"id": "GHSA-jf85-cpcp-j695", "summary": "Prototype pollution in lodash"}]},
            {"vulns": [{"id": "GHSA-42xw-2xvc-rh8d", "summary": "SSRF in axios"}]},
        ]
    }

    mock_client_instance = MagicMock()
    mock_client_instance.post.return_value = mock_batch_response

    with patch("httpx.Client") as mock_client_cls, \
         patch.object(client, "get_vuln_details") as mock_details:
        mock_client_cls.return_value.__enter__.return_value = mock_client_instance
        mock_details.side_effect = lambda vid, client=None: {
            "id": vid,
            "summary": "Mock vuln details",
            "affected": [{"package": {"name": "lodash" if "jf85" in vid else "axios"}, "ranges": [{"events": [{"fixed": "4.17.21" if "jf85" in vid else "0.21.2"}]}]}],
        }

        report = client.scan_discovered_dependencies(deps, ecosystem="npm")

        # Must query only 1 batch containing 2 unique packages (not 5 queries!)
        assert mock_client_instance.post.call_count == 1
        call_args = mock_client_instance.post.call_args[1]
        queries = call_args["json"]["queries"]
        assert len(queries) == 2
        assert {q["package"]["name"] for q in queries} == {"lodash", "axios"}

        # Total findings should be 5 (one for each of the 5 dependent locations)
        assert len(report.findings) == 5

        # Verify caching: second run with same deps makes 0 HTTP queries
        mock_client_instance.post.reset_mock()
        second_report = client.scan_discovered_dependencies(deps, ecosystem="npm")
        assert mock_client_instance.post.call_count == 0
        assert len(second_report.findings) == 5


def test_osv_scan_handles_batch_failure_gracefully():
    client = OSVClient(timeout_seconds=5.0)
    deps = [
        DiscoveredDependency(package_name="pkg-a", version="1.0.0", is_direct=True, introduced_by=[]),
        DiscoveredDependency(package_name="pkg-b", version="2.0.0", is_direct=True, introduced_by=[]),
    ]

    mock_client_instance = MagicMock()
    mock_client_instance.post.side_effect = Exception("OSV API connection timed out")

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value = mock_client_instance

        # Should NOT raise and should NOT do sequential fallback loops
        report = client.scan_discovered_dependencies(deps, ecosystem="npm")
        assert report.findings == []
        assert report.scanned_packages_count == 2
