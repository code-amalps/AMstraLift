"""Unit tests for AMstraLift Desktop GUI self-healing and update button."""

import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

from amstralift.core.self_update import UpdateCheckResult
from amstralift.gui import AMstraLiftGUI


@pytest.fixture
def tk_root():
    """Provide a Tkinter root instance, or skip if no graphical display is available."""
    try:
        root = tk.Tk()
    except Exception as exc:
        pytest.skip(f"Tkinter display unavailable in this environment: {exc}")
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_gui_update_and_doctor_widgets_present(tk_root):
    app = AMstraLiftGUI(tk_root)
    tk_root.update()

    assert hasattr(app, "update_btn")
    assert hasattr(app, "doctor_btn")
    # Update button starts unmapped
    assert not app.update_btn.winfo_ismapped()
    # Doctor button is in quick actions toolbar
    assert app.doctor_btn.winfo_ismapped()


def test_gui_reveals_update_button_when_update_available(tk_root):
    app = AMstraLiftGUI(tk_root)
    tk_root.update()

    fake_update = UpdateCheckResult(
        current_version="0.1.18",
        latest_version="0.2.0",
        update_available=True,
        release_notes="Automated self-healing update",
    )
    app._on_background_scan_complete(fake_update, has_repairable=False, report=None)
    tk_root.update()

    assert app.update_btn.winfo_ismapped()
    assert "Update to v0.2.0" in app.update_btn.cget("text")


def test_gui_reveals_self_heal_pill_when_maintenance_needed(tk_root):
    app = AMstraLiftGUI(tk_root)
    tk_root.update()

    app._on_background_scan_complete(None, has_repairable=True, report=None)
    tk_root.update()

    assert app.update_btn.winfo_ismapped()
    assert "Self-Heal Ready" in app.update_btn.cget("text")
