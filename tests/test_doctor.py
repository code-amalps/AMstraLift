"""Unit tests for AMstraLift doctor, diagnostics, and self-healing engine."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amstralift.core.doctor import CheckStatus, DoctorEngine, DoctorReport


def test_doctor_engine_initialization(tmp_path: Path):
    cfg_dir = tmp_path / ".amstralift"
    engine = DoctorEngine(config_dir=cfg_dir)
    assert engine.config_dir == cfg_dir
    assert engine.config_file == cfg_dir / "gui_config.json"


def test_doctor_diagnose_healthy_config(tmp_path: Path):
    cfg_dir = tmp_path / ".amstralift"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "gui_config.json"
    cfg_file.write_text(json.dumps({"theme": "dark", "recent_repos": []}), encoding="utf-8")

    engine = DoctorEngine(config_dir=cfg_dir)
    report = engine.diagnose_and_heal(auto_fix=False)

    assert isinstance(report, DoctorReport)
    config_items = [i for i in report.items if i.name == "User Configuration"]
    assert len(config_items) == 1
    assert config_items[0].status == CheckStatus.OK


def test_doctor_heals_missing_config_directory(tmp_path: Path):
    cfg_dir = tmp_path / ".amstralift_missing"
    assert not cfg_dir.exists()

    engine = DoctorEngine(config_dir=cfg_dir)

    # 1. Without auto_fix -> returns warning
    report_unfixed = engine.diagnose_and_heal(auto_fix=False)
    unfixed_item = next(i for i in report_unfixed.items if i.name == "User Configuration")
    assert unfixed_item.status == CheckStatus.WARNING
    assert unfixed_item.repairable is True
    assert not cfg_dir.exists()

    # 2. With auto_fix -> creates dir and default config
    report_fixed = engine.diagnose_and_heal(auto_fix=True)
    fixed_item = next(i for i in report_fixed.items if i.name == "User Configuration")
    assert fixed_item.status == CheckStatus.REPAIRED
    assert fixed_item.repaired is True
    assert cfg_dir.exists()
    assert (cfg_dir / "gui_config.json").exists()
    assert len(report_fixed.repairs_applied) >= 1


def test_doctor_heals_corrupted_config_json(tmp_path: Path):
    cfg_dir = tmp_path / ".amstralift_corrupted"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "gui_config.json"
    cfg_file.write_text("{ broken invalid json ...", encoding="utf-8")

    engine = DoctorEngine(config_dir=cfg_dir)

    # 1. Without auto_fix -> returns ERROR
    report_unfixed = engine.diagnose_and_heal(auto_fix=False)
    unfixed_item = next(i for i in report_unfixed.items if i.name == "User Configuration")
    assert unfixed_item.status == CheckStatus.ERROR
    assert unfixed_item.repairable is True

    # 2. With auto_fix -> backs up corrupted file and restores defaults
    report_fixed = engine.diagnose_and_heal(auto_fix=True)
    fixed_item = next(i for i in report_fixed.items if i.name == "User Configuration")
    assert fixed_item.status == CheckStatus.REPAIRED
    assert fixed_item.repaired is True

    # Confirm backup file was created
    bak_file = cfg_dir / "gui_config.json.corrupted_bak"
    assert bak_file.exists()
    assert "{ broken invalid json" in bak_file.read_text(encoding="utf-8")

    # Confirm config file is now valid JSON
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"


def test_doctor_heals_stale_temporary_sandboxes(tmp_path: Path, monkeypatch):
    # Mock tempfile.gettempdir to point to tmp_path
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    # Create stale sandbox directories
    stale1 = tmp_path / "amstralift_stage_a_abc123"
    stale1.mkdir()
    (stale1 / "dummy.txt").write_text("test")
    stale2 = tmp_path / "amstralift_security_sandbox_xyz789"
    stale2.mkdir()

    engine = DoctorEngine(config_dir=tmp_path / "cfg")

    # Diagnosis without fix
    report = engine.diagnose_and_heal(auto_fix=False)
    temp_item = next(i for i in report.items if i.name == "Sandbox Temp Storage")
    assert temp_item.status == CheckStatus.WARNING
    assert "2 orphaned sandbox" in temp_item.message

    # Auto fix
    report_fixed = engine.diagnose_and_heal(auto_fix=True)
    fixed_item = next(i for i in report_fixed.items if i.name == "Sandbox Temp Storage")
    assert fixed_item.status == CheckStatus.REPAIRED
    assert not stale1.exists()
    assert not stale2.exists()


def test_doctor_report_serialization(tmp_path: Path):
    engine = DoctorEngine(config_dir=tmp_path)
    report = engine.diagnose_and_heal(auto_fix=True)
    d = report.to_dict()

    assert "version" in d
    assert "is_healthy" in d
    assert "items" in d
    assert isinstance(d["items"], list)
    assert all("name" in item and "status" in item for item in d["items"])
