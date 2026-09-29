"""Tests for the AMstraLift REST API endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from amstralift.adapters.angular import AngularAdapter
from amstralift.api.app import create_app
from tests.test_end_to_end_slice import init_mock_angular_repo


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_api_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_api_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data
    assert "angular" in data["available_adapters"]
    assert "react" in data["available_adapters"]
    assert "dotnet" in data["available_adapters"]
    assert "python" in data["available_adapters"]


def test_api_policy_default(client):
    response = client.get("/api/v1/policy")
    assert response.status_code == 200
    data = response.json()
    assert "slas" in data
    assert "CRITICAL" in data["slas"]
    assert "HIGH" in data["slas"]


def test_api_audit_invalid_directory(client):
    response = client.post(
        "/api/v1/audit",
        json={"repo_path": "/nonexistent/path/here"},
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_api_audit_valid_repo(tmp_path: Path, client):
    repo_path = tmp_path / "audit-test-repo"
    init_mock_angular_repo(repo_path, test_exit_code=0)

    response = client.post(
        "/api/v1/audit",
        json={
            "repo_path": str(repo_path),
            "ecosystem": "angular",
            "mode": "audit",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ecosystem"] == "angular"
    assert "findings_count" in data
    assert "report" in data


def test_api_upgrade_invalid_directory(client):
    response = client.post(
        "/api/v1/upgrade",
        json={"repo_path": "/nonexistent/path/here"},
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_api_upgrade_valid_repo(tmp_path: Path, client):
    repo_path = tmp_path / "upgrade-test-repo"
    init_mock_angular_repo(repo_path, test_exit_code=0)

    with patch.object(AngularAdapter, "fetch_latest_version", return_value="18.2.0"):
        response = client.post(
            "/api/v1/upgrade",
            json={
                "repo_path": str(repo_path),
                "ecosystem": "angular",
                "dry_run": True,
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "changes" in data
    assert "gate_results" in data
    assert data["all_required_passed"] is True
