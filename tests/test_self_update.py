"""Unit tests for AMstraLift self-updater and version comparison."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from amstralift.core.self_update import SelfUpdater, UpdateCheckResult


def test_is_newer_comparison():
    assert SelfUpdater._is_newer("0.2.0", "0.1.18") is True
    assert SelfUpdater._is_newer("0.1.19", "0.1.18") is True
    assert SelfUpdater._is_newer("1.0.0", "0.9.9") is True
    assert SelfUpdater._is_newer("0.1.18", "0.1.18") is False
    assert SelfUpdater._is_newer("0.1.17", "0.1.18") is False


def test_check_for_updates_pypi_success():
    fake_pypi_data = {
        "info": {
            "version": "0.2.0",
            "summary": "Major new release with automated self-healing",
        },
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "url": "https://files.pythonhosted.org/amstralift-0.2.0-py3-none-any.whl",
                "digests": {"sha256": "fake_sha256_hash"},
            }
        ],
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(fake_pypi_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = SelfUpdater.check_for_updates()

    assert isinstance(res, UpdateCheckResult)
    assert res.latest_version == "0.2.0"
    assert res.update_available is True
    assert res.source == "pypi"
    assert res.download_url == "https://files.pythonhosted.org/amstralift-0.2.0-py3-none-any.whl"
    assert res.checksum == "fake_sha256_hash"


def test_check_for_updates_offline_fallback():
    with patch("urllib.request.urlopen", side_effect=Exception("Network down")):
        res = SelfUpdater.check_for_updates()

    assert isinstance(res, UpdateCheckResult)
    assert res.update_available is False
    assert res.source == "offline"


def test_check_for_updates_github_success():
    fake_github_data = {
        "tag_name": "v0.2.1",
        "body": "Bugfixes and performance optimizations",
        "published_at": "2026-11-20T10:00:00Z",
        "assets": [
            {
                "name": "amstralift.exe",
                "browser_download_url": "https://github.com/code-amalps/AMstraLift/releases/download/v0.2.1/amstralift.exe",
            }
        ],
    }

    mock_pypi_err = MagicMock(side_effect=Exception("PyPI 404"))
    mock_gh_resp = MagicMock()
    mock_gh_resp.status = 200
    mock_gh_resp.read.return_value = json.dumps(fake_github_data).encode("utf-8")
    mock_gh_resp.__enter__.return_value = mock_gh_resp

    def urlopen_side_effect(req, *args, **kwargs):
        if "pypi.org" in req.full_url:
            raise Exception("PyPI unreachable")
        return mock_gh_resp

    with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), patch.object(
        SelfUpdater, "is_frozen_binary", return_value=True
    ):
        res = SelfUpdater.check_for_updates()

    assert isinstance(res, UpdateCheckResult)
    assert res.latest_version == "0.2.1"
    assert res.update_available is True
    assert res.source == "github"
    assert "amstralift.exe" in (res.download_url or "")


def test_execute_update_already_latest():
    with patch.object(
        SelfUpdater,
        "check_for_updates",
        return_value=UpdateCheckResult(
            current_version="0.1.18",
            latest_version="0.1.18",
            update_available=False,
        ),
    ):
        success, msg = SelfUpdater.execute_update(force=False)
        assert success is True
        assert "already at the latest version" in msg


def test_execute_update_pip_success():
    with patch.object(
        SelfUpdater,
        "check_for_updates",
        return_value=UpdateCheckResult(
            current_version="0.1.18",
            latest_version="0.2.0",
            update_available=True,
        ),
    ), patch.object(SelfUpdater, "is_frozen_binary", return_value=False), patch(
        "subprocess.run"
    ) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Successfully installed amstralift-0.2.0", stderr="")
        success, msg = SelfUpdater.execute_update()
        assert success is True
        assert "Successfully upgraded AMstraLift" in msg
        mock_run.assert_called_once()
