"""Interactive desktop GUI for AMstraLift using Tkinter.

Provides a modern, high-contrast desktop interface for selecting repositories,
configuring upgrade/audit parameters, monitoring real-time progress, switching
Dark/Light themes, and safely aborting ongoing operations.
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
        "bg_main": "#1e1e2e",
        "bg_card": "#252538",
        "bg_input": "#181825",
        "fg_text": "#cdd6f4",
        "fg_muted": "#9399b2",
        "border": "#313244",
        "console_bg": "#11111b",
        "console_fg": "#cdd6f4",
        "btn_primary": "#3b82f6",
        "btn_primary_hover": "#2563eb",
        "btn_abort": "#ef4444",
        "btn_abort_hover": "#dc2626",
        "btn_secondary": "#313244",
        "btn_secondary_fg": "#cdd6f4",
        "highlight": "#89b4fa",
        "status_bg": "#181825",
    },
    "light": {
        "bg_main": "#f8fafc",
        "bg_card": "#ffffff",
        "bg_input": "#f1f5f9",
        "fg_text": "#0f172a",
        "fg_muted": "#64748b",
        "border": "#cbd5e1",
        "console_bg": "#0f172a",
        "console_fg": "#f8fafc",
        "btn_primary": "#2563eb",
        "btn_primary_hover": "#1d4ed8",
        "btn_abort": "#dc2626",
        "btn_abort_hover": "#b91c1c",
        "btn_secondary": "#e2e8f0",
        "btn_secondary_fg": "#1e293b",
        "highlight": "#1d4ed8",
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
                self.text_widget.insert(tk.END, clean, (self.tag,))
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
        self.root.geometry("900x740")
        self.root.minsize(780, 600)

        self.config = load_gui_config()
        self.current_theme = self.config.get("theme", "dark")
        if self.current_theme not in THEMES:
            self.current_theme = "dark"

        self.cancellation_token = get_default_cancellation_token()
        self.is_running = False
        self.start_time: float = 0.0
        self.timer_id: Optional[str] = None
        self.last_target_repo: Optional[str] = None

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self._create_widgets()
        self._apply_theme(self.current_theme)
        self._set_defaults()

    def _create_widgets(self):
        # ── Header ──────────────────────────────────────────────────────────
        self.header_frame = tk.Frame(self.root, padx=14, pady=10)
        self.header_frame.pack(fill=tk.X)

        header_left = tk.Frame(self.header_frame)
        header_left.pack(side=tk.LEFT)

        self.title_lbl = tk.Label(
            header_left,
            text="AMstraLift Modernization Engine",
            font=("Segoe UI", 15, "bold"),
        )
        self.title_lbl.pack(side=tk.LEFT)

        self.version_lbl = tk.Label(
            header_left,
            text=f"v{__version__}",
            font=("Segoe UI", 10),
        )
        self.version_lbl.pack(side=tk.LEFT, padx=(8, 0), pady=(3, 0))

        # Header Right: Theme Switcher
        self.theme_btn = tk.Button(
            self.header_frame,
            text="🌙 Dark Mode" if self.current_theme == "light" else "☀️ Light Mode",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            command=self._toggle_theme,
        )
        self.theme_btn.pack(side=tk.RIGHT)

        # ── Main Tabs Notebook ───────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=False, padx=14, pady=(0, 6))

        # Tab 1: Upgrade
        self.upgrade_tab = tk.Frame(self.notebook, padx=12, pady=10)
        self.notebook.add(self.upgrade_tab, text=" 🚀 Framework Upgrade ")

        # Tab 2: Security Audit
        self.audit_tab = tk.Frame(self.notebook, padx=12, pady=10)
        self.notebook.add(self.audit_tab, text=" 🛡️ Security Audit & CVEs ")

        self._build_upgrade_tab()
        self._build_audit_tab()

        # ── Quick Actions Toolbar (active after completion) ──────────────────
        self.quick_actions_frame = tk.Frame(self.root, padx=14, pady=4)
        self.quick_actions_frame.pack(fill=tk.X)

        quick_lbl = tk.Label(
            self.quick_actions_frame,
            text="Quick Actions:",
            font=("Segoe UI", 9, "bold"),
        )
        quick_lbl.pack(side=tk.LEFT, padx=(0, 8))

        self.open_folder_btn = tk.Button(
            self.quick_actions_frame,
            text="📂 Open Folder",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._open_project_folder,
            state="disabled",
        )
        self.open_folder_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.open_code_btn = tk.Button(
            self.quick_actions_frame,
            text="💻 Open in VS Code",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._open_in_vscode,
            state="disabled",
        )
        self.open_code_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.git_status_btn = tk.Button(
            self.quick_actions_frame,
            text="🔀 Git Status",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=2,
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
            padx=8,
            pady=2,
            command=self._copy_log,
        )
        self.copy_log_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.save_log_btn = tk.Button(
            self.quick_actions_frame,
            text="💾 Save Log...",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._save_log_to_file,
        )
        self.save_log_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.clear_log_btn = tk.Button(
            self.quick_actions_frame,
            text="🧹 Clear",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=8,
            pady=2,
            command=self._clear_log,
        )
        self.clear_log_btn.pack(side=tk.RIGHT)

        # ── Live Execution Log Panel ────────────────────────────────────────
        self.log_container = tk.Frame(self.root, padx=14, pady=2)
        self.log_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = ScrolledText(
            self.log_container,
            wrap=tk.WORD,
            font=("Consolas", 9),
            height=13,
            state="disabled",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("stdout", foreground="#cdd6f4")
        self.log_text.tag_configure("stderr", foreground="#f87171")
        self.log_text.tag_configure("success", foreground="#34d399", font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("info", foreground="#60a5fa")
        self.log_text.tag_configure("warning", foreground="#fbbf24")

        # ── Status Bar & Progress ───────────────────────────────────────────
        self.status_frame = tk.Frame(self.root, padx=14, pady=6)
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # Left: Status badge and message
        status_left = tk.Frame(self.status_frame)
        status_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_badge = tk.Label(status_left, text="🟢", font=("Segoe UI", 10))
        self.status_badge.pack(side=tk.LEFT, padx=(0, 6))

        self.status_var = tk.StringVar(value="Ready.")
        self.status_lbl = tk.Label(
            status_left,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            anchor=tk.W,
        )
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X)

        # Right: Timer and Determinate Progress Bar
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
            length=160,
        )
        self.progress_bar.pack(side=tk.RIGHT)

    def _build_upgrade_tab(self):
        f = self.upgrade_tab

        # Repository Folder Row
        repo_frame = tk.Frame(f)
        repo_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(repo_frame, text="Target Repository:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        repo_input_frame = tk.Frame(repo_frame)
        repo_input_frame.pack(fill=tk.X, pady=(3, 0))

        self.upgrade_repo_var = tk.StringVar()
        self.upgrade_repo_combo = ttk.Combobox(
            repo_input_frame,
            textvariable=self.upgrade_repo_var,
            values=self.config.get("recent_repos", []),
        )
        self.upgrade_repo_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.browse_upgrade_btn = tk.Button(
            repo_input_frame,
            text="Browse...",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            command=lambda: self._browse_directory(self.upgrade_repo_var),
        )
        self.browse_upgrade_btn.pack(side=tk.RIGHT)

        # Options Box
        self.opt_box = tk.LabelFrame(f, text=" Upgrade Configuration ", padx=10, pady=6)
        self.opt_box.pack(fill=tk.X, pady=(0, 8))

        # Row 0: Ecosystem & Modernization
        tk.Label(self.opt_box, text="Ecosystem:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.upgrade_eco_var = tk.StringVar(value="auto")
        eco_combo = ttk.Combobox(
            self.opt_box,
            textvariable=self.upgrade_eco_var,
            values=["auto", "angular", "react", "python", "dotnet"],
            state="readonly",
            width=14,
        )
        eco_combo.grid(row=0, column=1, sticky=tk.W, padx=6, pady=3)

        tk.Label(self.opt_box, text="Modernizations:").grid(row=0, column=2, sticky=tk.W, pady=3, padx=(14, 0))
        self.upgrade_mod_var = tk.StringVar(value="all")
        mod_combo = ttk.Combobox(
            self.opt_box,
            textvariable=self.upgrade_mod_var,
            values=["none", "all", "control-flow", "standalone", "style"],
            state="readonly",
            width=14,
        )
        mod_combo.grid(row=0, column=3, sticky=tk.W, padx=6, pady=3)

        # Row 1: Branches
        tk.Label(self.opt_box, text="Base Branch:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.base_branch_var = tk.StringVar(value="")
        base_entry = ttk.Entry(self.opt_box, textvariable=self.base_branch_var, width=16)
        base_entry.grid(row=1, column=1, sticky=tk.W, padx=6, pady=3)

        tk.Label(self.opt_box, text="Output Branch:").grid(row=1, column=2, sticky=tk.W, pady=3, padx=(14, 0))
        self.output_branch_var = tk.StringVar(value="")
        out_branch_entry = ttk.Entry(self.opt_box, textvariable=self.output_branch_var, width=16)
        out_branch_entry.grid(row=1, column=3, sticky=tk.W, padx=6, pady=3)

        # Row 2: Checkboxes
        chk_frame = tk.Frame(self.opt_box)
        chk_frame.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(4, 0))

        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame,
            text="Incremental Upgrade (one major version jump at a time)",
            variable=self.incremental_var,
        ).pack(anchor=tk.W, pady=2)

        self.remediate_cves_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame,
            text="Auto-remediate known CVEs (apply safe scoped dependency overrides)",
            variable=self.remediate_cves_var,
        ).pack(anchor=tk.W, pady=2)

        chk_sub = tk.Frame(chk_frame)
        chk_sub.pack(anchor=tk.W)

        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            chk_sub,
            text="Dry run only",
            variable=self.dry_run_var,
        ).pack(side=tk.LEFT, padx=(0, 14), pady=2)

        self.allow_failed_gates_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            chk_sub,
            text="Allow failed gates (create branch even if test gates time out)",
            variable=self.allow_failed_gates_var,
        ).pack(side=tk.LEFT, pady=2)

        # Action Buttons Row
        action_frame = tk.Frame(f)
        action_frame.pack(fill=tk.X, pady=(2, 0))

        self.run_btn = tk.Button(
            action_frame,
            text="▶ Start Framework Upgrade",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._execute_upgrade,
        )
        self.run_btn.pack(side=tk.LEFT)

        self.abort_upgrade_btn = tk.Button(
            action_frame,
            text="⏹ Abort",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=6,
            state="disabled",
            command=self._abort_execution,
        )
        self.abort_upgrade_btn.pack(side=tk.LEFT, padx=(8, 0))

    def _build_audit_tab(self):
        f = self.audit_tab

        repo_frame = tk.Frame(f)
        repo_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(repo_frame, text="Target Repository:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        repo_input_frame = tk.Frame(repo_frame)
        repo_input_frame.pack(fill=tk.X, pady=(3, 0))

        self.audit_repo_var = tk.StringVar()
        self.audit_repo_combo = ttk.Combobox(
            repo_input_frame,
            textvariable=self.audit_repo_var,
            values=self.config.get("recent_repos", []),
        )
        self.audit_repo_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.browse_audit_btn = tk.Button(
            repo_input_frame,
            text="Browse...",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            command=lambda: self._browse_directory(self.audit_repo_var),
        )
        self.browse_audit_btn.pack(side=tk.RIGHT)

        self.audit_box = tk.LabelFrame(f, text=" Security Scan & Remediation Mode ", padx=10, pady=8)
        self.audit_box.pack(fill=tk.X, pady=(0, 8))

        self.audit_mode_var = tk.StringVar(value="audit")

        ttk.Radiobutton(
            self.audit_box,
            text="Audit Only (scan dependencies and display CVE vulnerability table)",
            variable=self.audit_mode_var,
            value="audit",
        ).pack(anchor=tk.W, pady=3)

        ttk.Radiobutton(
            self.audit_box,
            text="Preview Plan (calculate compatible non-breaking fix versions without modifying files)",
            variable=self.audit_mode_var,
            value="preview",
        ).pack(anchor=tk.W, pady=3)

        ttk.Radiobutton(
            self.audit_box,
            text="Apply & Remediate (apply scoped overrides and execute verification gates)",
            variable=self.audit_mode_var,
            value="apply",
        ).pack(anchor=tk.W, pady=3)

        action_frame = tk.Frame(f)
        action_frame.pack(fill=tk.X, pady=(2, 0))

        self.audit_btn = tk.Button(
            action_frame,
            text="🛡️ Run Security Scan",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._execute_audit,
        )
        self.audit_btn.pack(side=tk.LEFT)

        self.abort_audit_btn = tk.Button(
            action_frame,
            text="⏹ Abort",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=6,
            state="disabled",
            command=self._abort_execution,
        )
        self.abort_audit_btn.pack(side=tk.LEFT, padx=(8, 0))

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

        self.upgrade_repo_combo.configure(values=self.config["recent_repos"])
        self.audit_repo_combo.configure(values=self.config["recent_repos"])
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
        self.title_lbl.configure(bg=colors["bg_main"], fg=colors["highlight"])
        self.version_lbl.configure(bg=colors["bg_main"], fg=colors["fg_muted"])

        self.theme_btn.configure(
            text="☀️ Light Mode" if is_dark else "🌙 Dark Mode",
            bg=colors["btn_secondary"],
            fg=colors["btn_secondary_fg"],
            activebackground=colors["border"],
            activeforeground=colors["fg_text"],
        )

        # Tab Frames
        self.upgrade_tab.configure(bg=colors["bg_card"])
        self.audit_tab.configure(bg=colors["bg_card"])
        for parent in (self.upgrade_tab, self.audit_tab):
            for child in parent.winfo_children():
                if isinstance(child, tk.Frame):
                    child.configure(bg=colors["bg_card"])
                    for grandchild in child.winfo_children():
                        if isinstance(grandchild, (tk.Frame, tk.Label)):
                            grandchild.configure(bg=colors["bg_card"])
                            if isinstance(grandchild, tk.Label):
                                grandchild.configure(fg=colors["fg_text"])

        # LabelFrames
        self.opt_box.configure(bg=colors["bg_card"], fg=colors["fg_text"])
        self.audit_box.configure(bg=colors["bg_card"], fg=colors["fg_text"])
        for box in (self.opt_box, self.audit_box):
            for child in box.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=colors["bg_card"], fg=colors["fg_text"])
                elif isinstance(child, tk.Frame):
                    child.configure(bg=colors["bg_card"])
                    for gc in child.winfo_children():
                        if isinstance(gc, tk.Frame):
                            gc.configure(bg=colors["bg_card"])

        # Buttons
        self.run_btn.configure(
            bg=colors["btn_primary"],
            fg="#ffffff",
            activebackground=colors["btn_primary_hover"],
            activeforeground="#ffffff",
        )
        self.abort_upgrade_btn.configure(
            bg=colors["btn_abort"],
            fg="#ffffff",
            activebackground=colors["btn_abort_hover"],
            activeforeground="#ffffff",
        )
        self.audit_btn.configure(
            bg="#059669",
            fg="#ffffff",
            activebackground="#047857",
            activeforeground="#ffffff",
        )
        self.abort_audit_btn.configure(
            bg=colors["btn_abort"],
            fg="#ffffff",
            activebackground=colors["btn_abort_hover"],
            activeforeground="#ffffff",
        )
        self.browse_upgrade_btn.configure(
            bg=colors["btn_secondary"],
            fg=colors["btn_secondary_fg"],
            activebackground=colors["border"],
        )
        self.browse_audit_btn.configure(
            bg=colors["btn_secondary"],
            fg=colors["btn_secondary_fg"],
            activebackground=colors["border"],
        )

        # Quick Actions Toolbar
        self.quick_actions_frame.configure(bg=colors["bg_main"])
        for child in self.quick_actions_frame.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=colors["bg_main"], fg=colors["fg_text"])
            elif isinstance(child, tk.Button):
                child.configure(
                    bg=colors["btn_secondary"],
                    fg=colors["btn_secondary_fg"],
                    activebackground=colors["border"],
                )

        # Log Panel
        self.log_container.configure(bg=colors["bg_main"])
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
                    "The 'code' executable was not found in your system PATH.",
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
            self.timer_var.set(f"⏱ {mins:02d}:{secs:02d}")
            self.timer_id = self.root.after(1000, self._update_timer)

    def _set_running_state(self, is_running: bool, status_msg: str, progress_pct: int = 0):
        self.is_running = is_running
        self.status_var.set(status_msg)

        if is_running:
            self.cancellation_token.reset()
            self.status_badge.configure(text="🟡")
            self.progress_bar["value"] = progress_pct or 5
            self.run_btn.configure(state="disabled")
            self.audit_btn.configure(state="disabled")
            self.abort_upgrade_btn.configure(state="normal")
            self.abort_audit_btn.configure(state="normal")
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

            self.run_btn.configure(state="normal")
            self.audit_btn.configure(state="normal")
            self.abort_upgrade_btn.configure(state="disabled")
            self.abort_audit_btn.configure(state="disabled")
            self.open_folder_btn.configure(state="normal")
            self.open_code_btn.configure(state="normal")
            self.git_status_btn.configure(state="normal")

    def _abort_execution(self):
        if not self.is_running:
            return
        self.status_var.set("⏹ Aborting operation...")
        self.cancellation_token.cancel()
        print("\n[ABORTED] Operation was aborted by user. Terminating active tasks...\n", file=sys.stderr)

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

                self.root.after(0, lambda: self._set_running_state(False, f"✔ Upgrade Complete: {proposal.branch_name}", 100))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Upgrade Complete",
                    f"AMstraLift completed successfully!\n\nTarget Branch: {proposal.branch_name}\nStatus: {proposal.publish_status}\nChanges: {len(bundle.bundle.changes)}",
                ))
            except OperationCancelledError:
                print("\n[ABORTED] Operation stopped by user.", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, "⏹ Aborted by user.", 0))
            except NoUpgradesAvailableError as ne:
                print(f"\n[Info] {ne}")
                self.root.after(0, lambda: self._set_running_state(False, "Repository already up to date.", 100))
                self.root.after(0, lambda: messagebox.showinfo("Up to Date", str(ne)))
            except Exception as exc:
                print(f"\n[Error] Upgrade failed: {exc}", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, f"Failed: {exc}", 0))
                self.root.after(0, lambda: messagebox.showerror("Upgrade Failed", f"An error occurred during upgrade:\n\n{exc}"))
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
                self.root.after(0, lambda: self.progress_bar.configure(value=30))

                result = orchestrator.run_remediation(
                    repo_path=Path(repo_path_str),
                    mode=mode,
                )
                check_cancelled(self.cancellation_token)

                self.root.after(0, lambda: self.progress_bar.configure(value=70))
                SecurityUI.render_dashboard(result.report, console)
                if mode in ("preview", "apply") and result.plan:
                    SecurityUI.render_preview(result.plan, console)
                if mode == "apply":
                    SecurityUI.render_result(result, console)

                self.root.after(0, lambda: self._set_running_state(False, f"✔ Audit complete: {len(result.report.findings)} CVE(s) found.", 100))
            except OperationCancelledError:
                print("\n[ABORTED] Security scan stopped by user.", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, "⏹ Aborted by user.", 0))
            except Exception as exc:
                print(f"\n[Error] Audit failed: {exc}", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, f"Audit error: {exc}", 0))
                self.root.after(0, lambda: messagebox.showerror("Audit Error", str(exc)))
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
