"""Interactive desktop GUI for AMstraLift.

A modern, high-contrast, responsive desktop interface with segmented navigation,
card-based layout, dark/light theme switching, live progress streaming, and
one-click cancellation.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Optional

from amstralift import __version__
from amstralift.core.cancellation import (
    CancellationToken,
    OperationCancelledError,
    get_default_cancellation_token,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".amstralift"
CONFIG_FILE = CONFIG_DIR / "gui_config.json"

THEMES = {
    "dark": {
        "bg_main": "#0f172a",          # Deep slate background
        "bg_card": "#1e293b",          # Card / container background
        "bg_input": "#090d16",         # Text input background
        "fg_text": "#f8fafc",          # Primary crisp white text
        "fg_muted": "#94a3b8",         # Secondary muted text
        "border": "#334155",           # Border outline
        "border_focus": "#3b82f6",     # Active focus border
        "console_bg": "#020617",       # Pure terminal black/slate
        "console_fg": "#f8fafc",       # Console text
        "btn_primary": "#2563eb",      # Bright Blue primary button
        "btn_primary_hover": "#1d4ed8",
        "btn_primary_disabled": "#334155",
        "btn_primary_disabled_fg": "#64748b",
        "btn_abort": "#ef4444",        # Red abort button
        "btn_abort_hover": "#dc2626",
        "btn_abort_disabled": "#334155",
        "btn_secondary": "#334155",    # Subtle secondary button
        "btn_secondary_fg": "#f8fafc",
        "btn_secondary_hover": "#475569",
        "tab_active_bg": "#2563eb",
        "tab_active_fg": "#ffffff",
        "tab_inactive_bg": "#1e293b",
        "tab_inactive_fg": "#94a3b8",
        "status_bg": "#090d16",
    },
    "light": {
        "bg_main": "#f1f5f9",          # Light cool slate
        "bg_card": "#ffffff",          # Pure white card
        "bg_input": "#f8fafc",         # Very light input
        "fg_text": "#0f172a",          # Deep dark text
        "fg_muted": "#64748b",         # Secondary gray text
        "border": "#cbd5e1",           # Border outline
        "border_focus": "#2563eb",
        "console_bg": "#0f172a",       # Professional dark navy terminal
        "console_fg": "#f8fafc",
        "btn_primary": "#2563eb",
        "btn_primary_hover": "#1d4ed8",
        "btn_primary_disabled": "#e2e8f0",
        "btn_primary_disabled_fg": "#94a3b8",
        "btn_abort": "#dc2626",
        "btn_abort_hover": "#b91c1c",
        "btn_abort_disabled": "#e2e8f0",
        "btn_secondary": "#e2e8f0",
        "btn_secondary_fg": "#0f172a",
        "btn_secondary_hover": "#cbd5e1",
        "tab_active_bg": "#2563eb",
        "tab_active_fg": "#ffffff",
        "tab_inactive_bg": "#ffffff",
        "tab_inactive_fg": "#64748b",
        "status_bg": "#e2e8f0",
    },
}


def load_gui_config() -> dict[str, Any]:
    """Load persistent desktop GUI preferences."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"theme": "dark", "recent_repos": []}


def save_gui_config(config: dict[str, Any]) -> None:
    """Save persistent desktop preferences."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to write gui_config: %s", exc)


class TextRedirector(io.StringIO):
    """Redirects stdout/stderr writes to a Tkinter ScrolledText widget with tag styling."""

    def __init__(self, text_widget: ScrolledText, tag: str = "stdout"):
        super().__init__()
        self.text_widget = text_widget
        self.tag = tag

    def write(self, s: str) -> int:
        if not s:
            return 0

        def _append():
            try:
                self.text_widget.configure(state="normal")
                clean = re.sub(r"\x1b\[[0-9;]*[mK]", "", s)

                # Determine tag based on content
                chosen_tag = self.tag
                if "[error]" in clean.lower() or "failed" in clean.lower() or "exception" in clean.lower():
                    chosen_tag = "stderr"
                elif "[1/5]" in clean or "[2/5]" in clean or "[3/5]" in clean or "[4/5]" in clean or "[5/5]" in clean:
                    chosen_tag = "step"
                elif "✔" in clean or "success" in clean.lower() or "complete" in clean.lower():
                    chosen_tag = "success"
                elif "↳" in clean:
                    chosen_tag = "info"

                self.text_widget.insert(tk.END, clean, (chosen_tag,))
                self.text_widget.see(tk.END)
                self.text_widget.configure(state="disabled")
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            try:
                self.text_widget.after(0, _append)
            except Exception:
                pass
        return len(s)

    def flush(self):
        pass


class AMstraLiftGUI:
    """Modern Desktop GUI for AMstraLift Upgrade & Security Engine."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"AMstraLift v{__version__} - Upgrade & Security Engine")
        self.root.geometry("920x760")
        self.root.minsize(800, 620)

        self.config = load_gui_config()
        self.current_theme = self.config.get("theme", "dark")
        if self.current_theme not in THEMES:
            self.current_theme = "dark"

        self.active_tab = "upgrade"  # "upgrade" or "audit"
        self.cancellation_token = get_default_cancellation_token()
        self.is_running = False
        self.start_time: float = 0.0
        self.timer_id: Optional[str] = None
        self.last_target_repo: Optional[str] = None

        self._create_widgets()
        self._set_defaults()
        self._apply_theme(self.current_theme)

    def _create_widgets(self):
        # ── Header ──────────────────────────────────────────────────────────
        self.header_frame = tk.Frame(self.root, padx=16, pady=12)
        self.header_frame.pack(fill=tk.X)

        header_left = tk.Frame(self.header_frame)
        header_left.pack(side=tk.LEFT)

        self.title_lbl = tk.Label(
            header_left,
            text="AMstraLift Modernization Engine",
            font=("Segoe UI", 15, "bold"),
        )
        self.title_lbl.pack(side=tk.LEFT)

        self.version_badge = tk.Label(
            header_left,
            text=f" v{__version__} ",
            font=("Segoe UI", 9, "bold"),
            padx=6,
            pady=1,
            relief=tk.FLAT,
        )
        self.version_badge.pack(side=tk.LEFT, padx=(8, 0))

        # Header Right: Theme Switcher
        self.theme_btn = tk.Button(
            self.header_frame,
            text="☀️ Light Mode" if self.current_theme == "dark" else "🌙 Dark Mode",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
            command=self._toggle_theme,
        )
        self.theme_btn.pack(side=tk.RIGHT)

        # ── Segmented Navigation Bar ─────────────────────────────────────────
        self.nav_frame = tk.Frame(self.root, padx=16, pady=4)
        self.nav_frame.pack(fill=tk.X, pady=(0, 8))

        self.tab_upgrade_btn = tk.Button(
            self.nav_frame,
            text=" 🚀 Framework Upgrade ",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._show_upgrade_tab,
        )
        self.tab_upgrade_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.tab_audit_btn = tk.Button(
            self.nav_frame,
            text=" 🛡️ Security Audit & CVEs ",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._show_audit_tab,
        )
        self.tab_audit_btn.pack(side=tk.LEFT)

        # ── Main Content Area ───────────────────────────────────────────────
        self.content_container = tk.Frame(self.root, padx=16, pady=0)
        self.content_container.pack(fill=tk.X)

        self._build_upgrade_card()
        self._build_audit_card()

        # Show upgrade tab by default
        self._show_upgrade_tab()

        # ── Quick Actions Toolbar ────────────────────────────────────────────
        self.quick_actions_frame = tk.Frame(self.root, padx=16, pady=4)
        self.quick_actions_frame.pack(fill=tk.X)

        self.quick_lbl = tk.Label(
            self.quick_actions_frame,
            text="Quick Actions:",
            font=("Segoe UI", 9, "bold"),
        )
        self.quick_lbl.pack(side=tk.LEFT, padx=(0, 8))

        self.open_folder_btn = tk.Button(
            self.quick_actions_frame,
            text="📂 Open Folder",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_project_folder,
            state="disabled",
        )
        self.open_folder_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.open_code_btn = tk.Button(
            self.quick_actions_frame,
            text="💻 Open in VS Code",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_in_vscode,
            state="disabled",
        )
        self.open_code_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.git_status_btn = tk.Button(
            self.quick_actions_frame,
            text="🔀 Git Status",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._show_git_status,
            state="disabled",
        )
        self.git_status_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Log toolbar buttons on right
        self.copy_log_btn = tk.Button(
            self.quick_actions_frame,
            text="📋 Copy Log",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._copy_log,
        )
        self.copy_log_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.save_log_btn = tk.Button(
            self.quick_actions_frame,
            text="💾 Save Log...",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._save_log_to_file,
        )
        self.save_log_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.clear_log_btn = tk.Button(
            self.quick_actions_frame,
            text="🧹 Clear",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._clear_log,
        )
        self.clear_log_btn.pack(side=tk.RIGHT)

        # ── Live Execution Terminal Panel ───────────────────────────────────
        self.log_container = tk.Frame(self.root, padx=16, pady=2)
        self.log_container.pack(fill=tk.BOTH, expand=True)

        self.log_border = tk.Frame(self.log_container, bd=1, relief=tk.SOLID)
        self.log_border.pack(fill=tk.BOTH, expand=True)

        self.log_text = ScrolledText(
            self.log_border,
            wrap=tk.WORD,
            font=("Consolas", 10),
            height=13,
            bd=0,
            state="disabled",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("stdout", foreground="#f8fafc")
        self.log_text.tag_configure("stderr", foreground="#f87171", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("success", foreground="#4ade80", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("step", foreground="#38bdf8", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("info", foreground="#818cf8")
        self.log_text.tag_configure("warning", foreground="#facc15")

        # ── Status Bar & Progress ───────────────────────────────────────────
        self.status_frame = tk.Frame(self.root, padx=16, pady=8)
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        status_left = tk.Frame(self.status_frame)
        status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_badge = tk.Label(status_left, text="🟢", font=("Segoe UI", 10))
        self.status_badge.pack(side=tk.LEFT, padx=(0, 6))

        self.status_var = tk.StringVar(value="Ready.")
        self.status_lbl = tk.Label(
            status_left,
            textvariable=self.status_var,
            font=("Segoe UI", 9, "bold"),
            anchor=tk.W,
        )
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X)

        status_right = tk.Frame(self.status_frame)
        status_right.pack(side=tk.RIGHT)

        self.timer_var = tk.StringVar(value="")
        self.timer_lbl = tk.Label(
            status_right,
            textvariable=self.timer_var,
            font=("Segoe UI", 9, "bold"),
        )
        self.timer_lbl.pack(side=tk.LEFT, padx=(0, 10))

        self.progress_bar = ttk.Progressbar(
            status_right,
            orient="horizontal",
            mode="determinate",
            length=180,
        )
        self.progress_bar.pack(side=tk.RIGHT)

    def _build_upgrade_card(self):
        self.upgrade_card = tk.Frame(self.content_container, padx=14, pady=12, bd=1, relief=tk.SOLID)

        # Target Repository Row
        repo_lbl = tk.Label(self.upgrade_card, text="Target Repository:", font=("Segoe UI", 9, "bold"))
        repo_lbl.pack(anchor=tk.W, pady=(0, 4))

        repo_row = tk.Frame(self.upgrade_card)
        repo_row.pack(fill=tk.X, pady=(0, 8))

        self.upgrade_repo_var = tk.StringVar()
        self.upgrade_repo_entry = tk.Entry(
            repo_row,
            textvariable=self.upgrade_repo_var,
            font=("Segoe UI", 9),
            bd=1,
            relief=tk.SOLID,
        )
        self.upgrade_repo_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=4)

        self.browse_upgrade_btn = tk.Button(
            repo_row,
            text="Browse...",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=3,
            cursor="hand2",
            command=lambda: self._browse_directory(self.upgrade_repo_var),
        )
        self.browse_upgrade_btn.pack(side=tk.RIGHT)

        # Configuration Grid
        config_grid = tk.Frame(self.upgrade_card)
        config_grid.pack(fill=tk.X, pady=(0, 8))

        # Row 0: Ecosystem & Modernizations
        tk.Label(config_grid, text="Ecosystem:", font=("Segoe UI", 9)).grid(row=0, column=0, sticky=tk.W, pady=3)
        self.upgrade_eco_var = tk.StringVar(value="auto")
        eco_combo = ttk.Combobox(
            config_grid,
            textvariable=self.upgrade_eco_var,
            values=["auto", "angular", "react", "python", "dotnet"],
            state="readonly",
            width=15,
        )
        eco_combo.grid(row=0, column=1, sticky=tk.W, padx=8, pady=3)

        tk.Label(config_grid, text="Modernizations:", font=("Segoe UI", 9)).grid(row=0, column=2, sticky=tk.W, pady=3, padx=(16, 0))
        self.upgrade_mod_var = tk.StringVar(value="all")
        mod_combo = ttk.Combobox(
            config_grid,
            textvariable=self.upgrade_mod_var,
            values=["none", "all", "control-flow", "standalone", "style"],
            state="readonly",
            width=15,
        )
        mod_combo.grid(row=0, column=3, sticky=tk.W, padx=8, pady=3)

        # Row 1: Branches
        tk.Label(config_grid, text="Base Branch:", font=("Segoe UI", 9)).grid(row=1, column=0, sticky=tk.W, pady=3)
        self.base_branch_var = tk.StringVar(value="")
        self.base_entry = tk.Entry(config_grid, textvariable=self.base_branch_var, width=17, font=("Segoe UI", 9), bd=1, relief=tk.SOLID)
        self.base_entry.grid(row=1, column=1, sticky=tk.W, padx=8, pady=3, ipady=2)

        tk.Label(config_grid, text="Output Branch:", font=("Segoe UI", 9)).grid(row=1, column=2, sticky=tk.W, pady=3, padx=(16, 0))
        self.output_branch_var = tk.StringVar(value="")
        self.output_entry = tk.Entry(config_grid, textvariable=self.output_branch_var, width=17, font=("Segoe UI", 9), bd=1, relief=tk.SOLID)
        self.output_entry.grid(row=1, column=3, sticky=tk.W, padx=8, pady=3, ipady=2)

        # Checkboxes
        chk_frame = tk.Frame(self.upgrade_card)
        chk_frame.pack(fill=tk.X, pady=(2, 8))

        self.incremental_var = tk.BooleanVar(value=True)
        self.chk_inc = tk.Checkbutton(
            chk_frame,
            text="Incremental Upgrade (one major version jump at a time, e.g. 12 → 13)",
            variable=self.incremental_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_inc.pack(anchor=tk.W, pady=1)

        self.remediate_cves_var = tk.BooleanVar(value=True)
        self.chk_cve = tk.Checkbutton(
            chk_frame,
            text="Auto-remediate known CVEs (apply safe scoped dependency overrides)",
            variable=self.remediate_cves_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_cve.pack(anchor=tk.W, pady=1)

        chk_sub = tk.Frame(chk_frame)
        chk_sub.pack(anchor=tk.W, pady=1)

        self.dry_run_var = tk.BooleanVar(value=False)
        self.chk_dry = tk.Checkbutton(
            chk_sub,
            text="Dry run only",
            variable=self.dry_run_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_dry.pack(side=tk.LEFT, padx=(0, 16))

        self.allow_failed_gates_var = tk.BooleanVar(value=False)
        self.chk_allow_fail = tk.Checkbutton(
            chk_sub,
            text="Allow failed gates (create branch even if test gates time out)",
            variable=self.allow_failed_gates_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_allow_fail.pack(side=tk.LEFT)

        # Action Buttons
        action_frame = tk.Frame(self.upgrade_card)
        action_frame.pack(fill=tk.X, pady=(4, 0))

        self.run_btn = tk.Button(
            action_frame,
            text="▶ Start Framework Upgrade",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=18,
            pady=7,
            cursor="hand2",
            command=self._execute_upgrade,
        )
        self.run_btn.pack(side=tk.LEFT)

        self.abort_upgrade_btn = tk.Button(
            action_frame,
            text="⏹ Abort",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=7,
            cursor="hand2",
            state="disabled",
            command=self._abort_execution,
        )
        self.abort_upgrade_btn.pack(side=tk.LEFT, padx=(10, 0))

    def _build_audit_card(self):
        self.audit_card = tk.Frame(self.content_container, padx=14, pady=12, bd=1, relief=tk.SOLID)

        # Target Repository Row
        repo_lbl = tk.Label(self.audit_card, text="Target Repository:", font=("Segoe UI", 9, "bold"))
        repo_lbl.pack(anchor=tk.W, pady=(0, 4))

        repo_row = tk.Frame(self.audit_card)
        repo_row.pack(fill=tk.X, pady=(0, 8))

        self.audit_repo_var = tk.StringVar()
        self.audit_repo_entry = tk.Entry(
            repo_row,
            textvariable=self.audit_repo_var,
            font=("Segoe UI", 9),
            bd=1,
            relief=tk.SOLID,
        )
        self.audit_repo_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), ipady=4)

        self.browse_audit_btn = tk.Button(
            repo_row,
            text="Browse...",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=3,
            cursor="hand2",
            command=lambda: self._browse_directory(self.audit_repo_var),
        )
        self.browse_audit_btn.pack(side=tk.RIGHT)

        # Audit Mode Radios
        mode_box = tk.Frame(self.audit_card)
        mode_box.pack(fill=tk.X, pady=(0, 10))

        tk.Label(mode_box, text="Audit & Remediation Mode:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(0, 4))

        self.audit_mode_var = tk.StringVar(value="audit")

        self.radio_audit = tk.Radiobutton(
            mode_box,
            text="Audit Only (scan dependencies and display CVE vulnerability table)",
            variable=self.audit_mode_var,
            value="audit",
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.radio_audit.pack(anchor=tk.W, pady=2)

        self.radio_preview = tk.Radiobutton(
            mode_box,
            text="Preview Plan (calculate compatible non-breaking fix versions without modifying files)",
            variable=self.audit_mode_var,
            value="preview",
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.radio_preview.pack(anchor=tk.W, pady=2)

        self.radio_apply = tk.Radiobutton(
            mode_box,
            text="Apply & Remediate (apply scoped overrides and execute verification gates)",
            variable=self.audit_mode_var,
            value="apply",
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.radio_apply.pack(anchor=tk.W, pady=2)

        # Remediation Policy Options
        self.audit_policy_frame = tk.Frame(self.audit_card)
        self.audit_policy_frame.pack(fill=tk.X, pady=(0, 10))

        self.audit_safe_only_var = tk.BooleanVar(value=True)
        self.chk_audit_safe_only = tk.Checkbutton(
            self.audit_policy_frame,
            text="Apply safe in-major fixes only (skip breaking major leaps like uuid)",
            variable=self.audit_safe_only_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_audit_safe_only.pack(anchor=tk.W, pady=1)

        self.audit_allow_major_var = tk.BooleanVar(value=False)
        self.chk_audit_allow_major = tk.Checkbutton(
            self.audit_policy_frame,
            text="Allow major version upgrades (accept breaking change risks)",
            variable=self.audit_allow_major_var,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self.chk_audit_allow_major.pack(anchor=tk.W, pady=1)

        def _on_allow_major_toggle(*_):
            if self.audit_allow_major_var.get():
                self.audit_safe_only_var.set(False)

        def _on_safe_only_toggle(*_):
            if self.audit_safe_only_var.get():
                self.audit_allow_major_var.set(False)

        self.audit_allow_major_var.trace_add("write", _on_allow_major_toggle)
        self.audit_safe_only_var.trace_add("write", _on_safe_only_toggle)

        # Dynamic button label based on selected mode
        def _on_audit_mode_change(*_):
            m = self.audit_mode_var.get()
            if m == "apply":
                self.audit_btn.configure(text="🛡️ Apply & Verify Remediation")
            elif m == "preview":
                self.audit_btn.configure(text="📋 Preview Remediation Plan")
            else:
                self.audit_btn.configure(text="🔍 Run Security Audit")

        self.audit_mode_var.trace_add("write", _on_audit_mode_change)

        # Action Buttons
        action_frame = tk.Frame(self.audit_card)
        action_frame.pack(fill=tk.X, pady=(4, 0))

        self.audit_btn = tk.Button(
            action_frame,
            text="🔍 Run Security Audit",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=18,
            pady=7,
            cursor="hand2",
            command=self._execute_audit,
        )
        self.audit_btn.pack(side=tk.LEFT)

        self.abort_audit_btn = tk.Button(
            action_frame,
            text="⏹ Abort",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=7,
            cursor="hand2",
            state="disabled",
            command=self._abort_execution,
        )
        self.abort_audit_btn.pack(side=tk.LEFT, padx=(10, 0))

    def _show_upgrade_tab(self):
        self.active_tab = "upgrade"
        self.audit_card.pack_forget()
        self.upgrade_card.pack(fill=tk.X)
        self._update_tab_buttons()

    def _show_audit_tab(self):
        self.active_tab = "audit"
        self.upgrade_card.pack_forget()
        self.audit_card.pack(fill=tk.X)
        self._update_tab_buttons()

    def _update_tab_buttons(self):
        colors = THEMES[self.current_theme]
        if self.active_tab == "upgrade":
            self.tab_upgrade_btn.configure(
                bg=colors["tab_active_bg"],
                fg=colors["tab_active_fg"],
                activebackground=colors["btn_primary_hover"],
                activeforeground=colors["tab_active_fg"],
            )
            self.tab_audit_btn.configure(
                bg=colors["tab_inactive_bg"],
                fg=colors["tab_inactive_fg"],
                activebackground=colors["border"],
                activeforeground=colors["fg_text"],
            )
        else:
            self.tab_upgrade_btn.configure(
                bg=colors["tab_inactive_bg"],
                fg=colors["tab_inactive_fg"],
                activebackground=colors["border"],
                activeforeground=colors["fg_text"],
            )
            self.tab_audit_btn.configure(
                bg=colors["tab_active_bg"],
                fg=colors["tab_active_fg"],
                activebackground=colors["btn_primary_hover"],
                activeforeground=colors["tab_active_fg"],
            )

    def _set_defaults(self):
        cwd = str(Path.cwd().resolve())
        recent = self.config.get("recent_repos", [])
        initial_repo = recent[0] if recent else cwd

        self.upgrade_repo_var.set(initial_repo)
        self.audit_repo_var.set(initial_repo)
        self.last_target_repo = initial_repo

    def _record_recent_repo(self, path: str):
        norm = str(Path(path).resolve())
        recent = self.config.get("recent_repos", [])
        if norm in recent:
            recent.remove(norm)
        recent.insert(0, norm)
        self.config["recent_repos"] = recent[:8]
        save_gui_config(self.config)
        self.last_target_repo = norm

    def _browse_directory(self, target_var: tk.StringVar):
        selected = filedialog.askdirectory(initialdir=target_var.get() or ".")
        if selected:
            norm = str(Path(selected).resolve())
            target_var.set(norm)
            self._record_recent_repo(norm)
            self.upgrade_repo_var.set(norm)
            self.audit_repo_var.set(norm)

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.config["theme"] = self.current_theme
        save_gui_config(self.config)
        self._apply_theme(self.current_theme)

    def _apply_theme(self, theme_name: str):
        colors = THEMES[theme_name]
        is_dark = theme_name == "dark"

        self.root.configure(bg=colors["bg_main"])
        self.header_frame.configure(bg=colors["bg_main"])
        self.header_frame.winfo_children()[0].configure(bg=colors["bg_main"])
        self.title_lbl.configure(bg=colors["bg_main"], fg=colors["fg_text"])
        self.version_badge.configure(bg=colors["btn_primary"], fg="#ffffff")

        self.theme_btn.configure(
            text="☀️ Light Mode" if is_dark else "🌙 Dark Mode",
            bg=colors["btn_secondary"],
            fg=colors["btn_secondary_fg"],
            activebackground=colors["btn_secondary_hover"],
            activeforeground=colors["btn_secondary_fg"],
        )

        self.nav_frame.configure(bg=colors["bg_main"])
        self.content_container.configure(bg=colors["bg_main"])
        self._update_tab_buttons()

        # Cards
        for card in (self.upgrade_card, self.audit_card):
            card.configure(bg=colors["bg_card"], highlightbackground=colors["border"])
            for child in card.winfo_children():
                self._recursively_style_widget(child, colors)

        # Checkboxes and Radios (No white boxes!)
        for chk in (
            self.chk_inc,
            self.chk_cve,
            self.chk_dry,
            self.chk_allow_fail,
            self.chk_audit_safe_only,
            self.chk_audit_allow_major,
        ):
            chk.configure(
                bg=colors["bg_card"],
                fg=colors["fg_text"],
                selectcolor=colors["bg_input"],
                activebackground=colors["bg_card"],
                activeforeground=colors["fg_text"],
                highlightthickness=0,
                bd=0,
            )

        for radio in (self.radio_audit, self.radio_preview, self.radio_apply):
            radio.configure(
                bg=colors["bg_card"],
                fg=colors["fg_text"],
                selectcolor=colors["bg_input"],
                activebackground=colors["bg_card"],
                activeforeground=colors["fg_text"],
                highlightthickness=0,
                bd=0,
            )

        # Entry fields
        for entry in (self.upgrade_repo_entry, self.audit_repo_entry, self.base_entry, self.output_entry):
            entry.configure(
                bg=colors["bg_input"],
                fg=colors["fg_text"],
                insertbackground=colors["fg_text"],
                highlightbackground=colors["border"],
                highlightcolor=colors["border_focus"],
            )

        # Primary Buttons
        self._update_button_visuals(colors)

        # Quick Actions Toolbar
        self.quick_actions_frame.configure(bg=colors["bg_main"])
        self.quick_lbl.configure(bg=colors["bg_main"], fg=colors["fg_text"])
        for btn in (
            self.open_folder_btn,
            self.open_code_btn,
            self.git_status_btn,
            self.copy_log_btn,
            self.save_log_btn,
            self.clear_log_btn,
        ):
            btn.configure(
                bg=colors["btn_secondary"],
                fg=colors["btn_secondary_fg"],
                activebackground=colors["btn_secondary_hover"],
            )

        # Log Panel
        self.log_container.configure(bg=colors["bg_main"])
        self.log_border.configure(bg=colors["border"])
        self.log_text.configure(
            bg=colors["console_bg"],
            fg=colors["console_fg"],
            insertbackground=colors["console_fg"],
        )

        # Status Bar
        self.status_frame.configure(bg=colors["status_bg"])
        for child in self.status_frame.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=colors["status_bg"])
                for gc in child.winfo_children():
                    if isinstance(gc, tk.Label):
                        gc.configure(bg=colors["status_bg"], fg=colors["fg_text"])

    def _recursively_style_widget(self, widget: tk.Widget, colors: dict[str, str]):
        if isinstance(widget, tk.Frame):
            widget.configure(bg=colors["bg_card"])
            for child in widget.winfo_children():
                self._recursively_style_widget(child, colors)
        elif isinstance(widget, tk.Label):
            widget.configure(bg=colors["bg_card"], fg=colors["fg_text"])
        elif isinstance(widget, tk.Button):
            if widget in (self.browse_upgrade_btn, self.browse_audit_btn):
                widget.configure(
                    bg=colors["btn_secondary"],
                    fg=colors["btn_secondary_fg"],
                    activebackground=colors["btn_secondary_hover"],
                )

    def _update_button_visuals(self, colors: Optional[dict[str, str]] = None):
        c = colors or THEMES[self.current_theme]
        if self.is_running:
            self.run_btn.configure(
                bg=c["btn_primary_disabled"],
                fg=c["btn_primary_disabled_fg"],
                state="disabled",
            )
            self.audit_btn.configure(
                bg=c["btn_primary_disabled"],
                fg=c["btn_primary_disabled_fg"],
                state="disabled",
            )
            self.abort_upgrade_btn.configure(
                bg=c["btn_abort"],
                fg="#ffffff",
                state="normal",
                activebackground=c["btn_abort_hover"],
            )
            self.abort_audit_btn.configure(
                bg=c["btn_abort"],
                fg="#ffffff",
                state="normal",
                activebackground=c["btn_abort_hover"],
            )
        else:
            self.run_btn.configure(
                bg=c["btn_primary"],
                fg="#ffffff",
                state="normal",
                activebackground=c["btn_primary_hover"],
            )
            self.audit_btn.configure(
                bg="#059669",
                fg="#ffffff",
                state="normal",
                activebackground="#047857",
            )
            self.abort_upgrade_btn.configure(
                bg=c["btn_abort_disabled"],
                fg=c["btn_primary_disabled_fg"],
                state="disabled",
            )
            self.abort_audit_btn.configure(
                bg=c["btn_abort_disabled"],
                fg=c["btn_primary_disabled_fg"],
                state="disabled",
            )

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        content = self.log_text.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set("📋 Log copied to clipboard.")

    def _save_log_to_file(self):
        target = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
            title="Save AMstraLift Execution Log",
        )
        if target:
            try:
                Path(target).write_text(self.log_text.get("1.0", tk.END), encoding="utf-8")
                self.status_var.set(f"💾 Log saved to {Path(target).name}")
            except Exception as exc:
                messagebox.showerror("Save Failed", f"Could not save log file: {exc}")

    def _open_project_folder(self):
        repo = self.last_target_repo
        if repo and Path(repo).exists():
            try:
                if sys.platform == "win32":
                    os.startfile(repo)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", repo])
                else:
                    subprocess.Popen(["xdg-open", repo])
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to open directory: {exc}")

    def _open_in_vscode(self):
        repo = self.last_target_repo
        if repo and Path(repo).exists():
            code_cmd = shutil.which("code.cmd") or shutil.which("code")
            if code_cmd:
                try:
                    subprocess.Popen([code_cmd, repo], shell=sys.platform == "win32")
                    self.status_var.set("💻 Opened repository in VS Code.")
                except Exception as exc:
                    messagebox.showerror("Error", f"Failed to open VS Code: {exc}")
            else:
                messagebox.showinfo(
                    "VS Code Not Found",
                    "The 'code' command was not found in your system PATH.",
                )

    def _show_git_status(self):
        repo = self.last_target_repo
        if repo and Path(repo).exists():
            try:
                res = subprocess.run(
                    ["git", "status"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                print(f"\n--- Git Status ({Path(repo).name}) ---\n" + (res.stdout or res.stderr or "").strip() + "\n")
            except Exception as exc:
                print(f"\n[Git Error] {exc}\n", file=sys.stderr)

    def _update_timer(self):
        if self.is_running:
            elapsed = int(time.time() - self.start_time)
            mins, secs = divmod(elapsed, 60)
            spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            self.spinner_idx = getattr(self, "spinner_idx", 0) + 1
            s_glyph = spinners[self.spinner_idx % len(spinners)]
            self.timer_var.set(f"{s_glyph} {mins:02d}:{secs:02d}")
            self.timer_id = self.root.after(1000, self._update_timer)

    def _set_running_state(self, is_running: bool, status_msg: str, progress_pct: int = 0):
        self.is_running = is_running
        self.status_var.set(status_msg)
        self._update_button_visuals()

        if is_running:
            self.cancellation_token.reset()
            self.status_badge.configure(text="🟡")
            self.progress_bar["value"] = progress_pct or 5
            self.open_folder_btn.configure(state="disabled")
            self.open_code_btn.configure(state="disabled")
            self.git_status_btn.configure(state="disabled")

            self.start_time = time.time()
            self.timer_var.set("⏱ 00:00")
            if self.timer_id:
                self.root.after_cancel(self.timer_id)
            self._update_timer()
        else:
            if self.timer_id:
                self.root.after_cancel(self.timer_id)
                self.timer_id = None

            self.progress_bar["value"] = progress_pct or (100 if "Complete" in status_msg or "✔" in status_msg else 0)
            if "Complete" in status_msg or "✔" in status_msg:
                self.status_badge.configure(text="🟢")
            elif "Abort" in status_msg:
                self.status_badge.configure(text="🔴")
            elif "Failed" in status_msg or "Error" in status_msg:
                self.status_badge.configure(text="🔴")
            else:
                self.status_badge.configure(text="🟢")

            self.open_folder_btn.configure(state="normal")
            self.open_code_btn.configure(state="normal")
            self.git_status_btn.configure(state="normal")

    def _abort_execution(self):
        if not self.is_running:
            return
        self.abort_upgrade_btn.configure(state="disabled")
        self.abort_audit_btn.configure(state="disabled")
        self.status_var.set("⏹ Aborting... Please wait")
        self.status_badge.configure(text="🟡")
        self.cancellation_token.cancel()
        print("\n[ABORTED] Operation was aborted by user. Terminating active tasks...\n", file=sys.stderr)
        self.root.update_idletasks()

    def _execute_upgrade(self):
        repo_path_str = self.upgrade_repo_var.get().strip()
        if not repo_path_str or not Path(repo_path_str).exists():
            messagebox.showerror("Invalid Directory", "Please select a valid local repository directory.")
            return

        self._record_recent_repo(repo_path_str)

        eco = self.upgrade_eco_var.get().strip()
        eco_param = None if eco == "auto" else eco
        mod_val = self.upgrade_mod_var.get().strip()
        mod_param = None if mod_val == "none" else mod_val
        inc = self.incremental_var.get()
        cve = self.remediate_cves_var.get()
        dry = self.dry_run_var.get()
        allow_fail = self.allow_failed_gates_var.get()

        self._clear_log()
        self._set_running_state(True, "Initializing upgrade workflow...", 5)

        def _worker():
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            redirector = TextRedirector(self.log_text, "stdout")
            sys.stdout = redirector
            sys.stderr = redirector

            try:
                from amstralift.execution.stage_a import NoUpgradesAvailableError
                from amstralift.service import UpgradeOrchestrator

                orchestrator = UpgradeOrchestrator(incremental=inc)
                print(f"Starting AMstraLift upgrade on: {repo_path_str}")
                print(f"Incremental: {inc} | Remediate CVEs: {cve} | Modernizations: {mod_param}\n")

                base_b = self.base_branch_var.get().strip() or None
                out_b = self.output_branch_var.get().strip() or None

                effective_branch = base_b
                if not effective_branch:
                    from amstralift.core.workspace import get_active_branch, run_git
                    active = get_active_branch(Path(repo_path_str))
                    for candidate in ("Dev", "main", "master"):
                        if candidate != active and run_git(["rev-parse", "--verify", candidate], cwd=Path(repo_path_str)).returncode == 0:
                            active = candidate
                            break
                    effective_branch = active or "main"

                def _progress_cb(pct: int, msg: str):
                    self.root.after(0, lambda: self.status_var.set(msg))
                    self.root.after(0, lambda: self.progress_bar.configure(value=pct))

                bundle, proposal = orchestrator.run_upgrade(
                    repo_path=Path(repo_path_str),
                    ecosystem=eco_param,
                    target_branch=effective_branch,
                    output_branch=out_b,
                    dry_run=dry,
                    allow_failed_gates=allow_fail,
                    draft_on_fail=True,
                    modernize=mod_param.split(",") if mod_param else None,
                    remediate_cves=cve,
                    progress_callback=_progress_cb,
                    cancellation_token=self.cancellation_token,
                )

                print("\n=======================================================")
                print(f"✔ Upgrade workflow complete!")
                print(f"Branch: {proposal.branch_name}")
                print(f"Status: {proposal.publish_status}")
                print(f"Changes applied: {len(bundle.bundle.changes)}")
                for chg in bundle.bundle.changes:
                    print(f" - {chg.package_name}: {chg.from_version} -> {chg.to_version} ({chg.change_type})")
                print("=======================================================\n")

                branch_msg = proposal.branch_name
                status_msg = proposal.publish_status
                changes_count = len(bundle.bundle.changes)
                self.root.after(0, lambda b=branch_msg: self._set_running_state(False, f"✔ Upgrade Complete: {b}", 100))
                self.root.after(0, lambda b=branch_msg, s=status_msg, c=changes_count: messagebox.showinfo(
                    "Upgrade Complete",
                    f"AMstraLift completed successfully!\n\nTarget Branch: {b}\nStatus: {s}\nChanges: {c}",
                ))
            except OperationCancelledError:
                print("\n[ABORTED] Operation stopped by user.", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, "⏹ Aborted by user.", 0))
            except NoUpgradesAvailableError as ne:
                info_msg = str(ne)
                print(f"\n[Info] {info_msg}")
                self.root.after(0, lambda: self._set_running_state(False, "Repository already up to date.", 100))
                self.root.after(0, lambda m=info_msg: messagebox.showinfo("Up to Date", m))
            except Exception as exc:
                err_msg = str(exc)
                print(f"\n[Error] Upgrade failed: {err_msg}", file=sys.stderr)
                self.root.after(0, lambda m=err_msg: self._set_running_state(False, f"Failed: {m}", 0))
                self.root.after(0, lambda m=err_msg: messagebox.showerror("Upgrade Failed", f"An error occurred during upgrade:\n\n{m}"))
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

        threading.Thread(target=_worker, daemon=True).start()

    def _execute_audit(self):
        repo_path_str = self.audit_repo_var.get().strip()
        if not repo_path_str or not Path(repo_path_str).exists():
            messagebox.showerror("Invalid Directory", "Please select a valid local repository directory.")
            return

        self._record_recent_repo(repo_path_str)

        mode = self.audit_mode_var.get()
        safe_only = self.audit_safe_only_var.get()
        allow_major = self.audit_allow_major_var.get()

        self._clear_log()
        self._set_running_state(True, f"Running security {mode}...", 10)

        def _worker():
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            redirector = TextRedirector(self.log_text, "stdout")
            sys.stdout = redirector
            sys.stderr = redirector

            try:
                from amstralift.core.cancellation import check_cancelled
                from amstralift.security.orchestrator import SecurityOrchestrator
                from amstralift.security.ui import SecurityUI
                from rich.console import Console

                console = Console(file=redirector, force_terminal=True, color_system=None)
                orchestrator = SecurityOrchestrator()

                check_cancelled(self.cancellation_token)
                print(f"Scanning dependencies in: {repo_path_str} (Mode: {mode})...\n")
                def _audit_progress_cb(pct: int, msg: str):
                    self.root.after(0, lambda: self.status_var.set(msg))
                    self.root.after(0, lambda: self.progress_bar.configure(value=pct))

                result = orchestrator.run_remediation(
                    repo_path=Path(repo_path_str),
                    mode=mode,
                    allow_major=allow_major,
                    safe_only=safe_only,
                    cancellation_token=self.cancellation_token,
                    progress_callback=_audit_progress_cb,
                )
                check_cancelled(self.cancellation_token)

                self.root.after(0, lambda: self.progress_bar.configure(value=70))
                SecurityUI.render_dashboard(result.report, console)
                if mode in ("preview", "apply") and result.plan:
                    SecurityUI.render_preview(result.plan, console)
                if mode == "apply":
                    SecurityUI.render_result(result, console)
                    if result.remediation_successful:
                        msg = (
                            f"Remediation successfully applied and verified!\n\n"
                            f"Branch: {result.branch_name}\n"
                            f"Targeted fixes: {len(result.plan.items if result.plan else [])}"
                        )
                        if result.plan and result.plan.advisories:
                            msg += f"\n\nAdvisories ({len(result.plan.advisories)}):\n" + "\n".join(f"• {a}" for a in result.plan.advisories[:3])
                        self.root.after(0, lambda: messagebox.showinfo("Remediation Complete", msg))
                    else:
                        self.root.after(0, lambda: messagebox.showwarning("Remediation Halted", f"Security remediation halted:\n\n{result.error_message}"))

                status_msg = f"✔ Remediation complete: {result.branch_name}" if (mode == "apply" and result.remediation_successful) else f"✔ Audit complete: {len(result.report.findings)} CVE(s) found."
                self.root.after(0, lambda s=status_msg: self._set_running_state(False, s, 100))
            except OperationCancelledError:
                print("\n[ABORTED] Security scan stopped by user.", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, "⏹ Aborted by user.", 0))
            except Exception as exc:
                err_msg = str(exc)
                print(f"\n[Error] Audit failed: {err_msg}", file=sys.stderr)
                self.root.after(0, lambda m=err_msg: self._set_running_state(False, f"Audit error: {m}", 0))
                self.root.after(0, lambda m=err_msg: messagebox.showerror("Audit Error", m))
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

        threading.Thread(target=_worker, daemon=True).start()


def launch_gui():
    """Entry point to launch the AMstraLift Tkinter GUI."""
    root = tk.Tk()
    app = AMstraLiftGUI(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
