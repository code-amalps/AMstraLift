"""Interactive desktop GUI for AMstraLift using Tkinter.

Provides an intuitive Windows/desktop interface for selecting repositories,
configuring upgrade/audit parameters, and watching real-time progress.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from amstralift import __version__


class TextRedirector(io.StringIO):
    """Redirects stdout/stderr writes to a Tkinter ScrolledText widget."""

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
                # Strip ANSI escape codes if present for clean display
                import re
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
    """AMstraLift Desktop GUI Application."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"AMstraLift v{__version__} - Upgrade & Security Engine")
        self.root.geometry("860x680")
        self.root.minsize(740, 560)

        # Style configuration
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista" if "vista" in self.style.theme_names() else "clam")
        except Exception:
            pass

        self._create_widgets()
        self._set_defaults()

    def _create_widgets(self):
        # ── Header ──────────────────────────────────────────────────────────
        header_frame = ttk.Frame(self.root, padding="12 10 12 10")
        header_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(
            header_frame,
            text="AMstraLift Upgrade Engine",
            font=("Segoe UI", 16, "bold"),
            foreground="#1e40af",
        )
        title_lbl.pack(side=tk.LEFT)

        version_lbl = ttk.Label(
            header_frame,
            text=f"v{__version__}",
            font=("Segoe UI", 10),
            foreground="#6b7280",
        )
        version_lbl.pack(side=tk.LEFT, padx=(6, 0), pady=(4, 0))

        # ── Main Content Notebook / Tabs ────────────────────────────────────
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        # Tab 1: Upgrade
        self.upgrade_tab = ttk.Frame(notebook, padding="12")
        notebook.add(self.upgrade_tab, text=" 🚀 Framework Upgrade ")

        # Tab 2: Security Audit
        self.audit_tab = ttk.Frame(notebook, padding="12")
        notebook.add(self.audit_tab, text=" 🛡️ Security Audit & CVEs ")

        self._build_upgrade_tab()
        self._build_audit_tab()

        # ── Live Execution Log Panel ────────────────────────────────────────
        log_frame = ttk.LabelFrame(self.root, text="Execution Output & Verification Gates", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        self.log_text = ScrolledText(
            log_frame,
            wrap=tk.WORD,
            bg="#111827",
            fg="#f3f4f6",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            height=12,
            state="disabled",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("stdout", foreground="#f3f4f6")
        self.log_text.tag_configure("stderr", foreground="#f87171")
        self.log_text.tag_configure("success", foreground="#34d399", font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("info", foreground="#60a5fa")

        # ── Status Bar & Progress ───────────────────────────────────────────
        status_frame = ttk.Frame(self.root, padding="12 4 12 8")
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.progress_bar = ttk.Progressbar(status_frame, mode="indeterminate")
        self.progress_bar.pack(side=tk.RIGHT, padx=(8, 0))

        self.status_var = tk.StringVar(value="Ready.")
        self.status_lbl = ttk.Label(status_frame, textvariable=self.status_var, font=("Segoe UI", 9))
        self.status_lbl.pack(side=tk.LEFT)

    def _build_upgrade_tab(self):
        f = self.upgrade_tab

        # Repository Folder Row
        repo_frame = ttk.Frame(f)
        repo_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(repo_frame, text="Target Repository:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        repo_input_frame = ttk.Frame(repo_frame)
        repo_input_frame.pack(fill=tk.X, pady=(4, 0))

        self.upgrade_repo_var = tk.StringVar()
        self.upgrade_repo_entry = ttk.Entry(repo_input_frame, textvariable=self.upgrade_repo_var)
        self.upgrade_repo_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(
            repo_input_frame,
            text="Browse...",
            command=lambda: self._browse_directory(self.upgrade_repo_var),
        ).pack(side=tk.RIGHT)

        # Config Grid
        grid_frame = ttk.LabelFrame(f, text="Upgrade Options", padding="10")
        grid_frame.pack(fill=tk.X, pady=(0, 10))

        # Ecosystem
        ttk.Label(grid_frame, text="Ecosystem:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.upgrade_eco_var = tk.StringVar(value="auto")
        eco_combo = ttk.Combobox(
            grid_frame,
            textvariable=self.upgrade_eco_var,
            values=["auto", "angular", "react", "python", "dotnet"],
            state="readonly",
            width=15,
        )
        eco_combo.grid(row=0, column=1, sticky=tk.W, padx=8, pady=4)

        # Modernization Options
        ttk.Label(grid_frame, text="Modernizations:").grid(row=0, column=2, sticky=tk.W, pady=4, padx=(16, 0))
        self.upgrade_mod_var = tk.StringVar(value="all")
        mod_combo = ttk.Combobox(
            grid_frame,
            textvariable=self.upgrade_mod_var,
            values=["none", "all", "control-flow", "standalone", "style"],
            state="readonly",
            width=15,
        )
        mod_combo.grid(row=0, column=3, sticky=tk.W, padx=8, pady=4)

        # Checkboxes
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grid_frame,
            text="Incremental Upgrade (one major step at a time, e.g. 12 → 13)",
            variable=self.incremental_var,
        ).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=3)

        self.remediate_cves_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grid_frame,
            text="Auto-remediate known CVEs (apply safe scoped overrides)",
            variable=self.remediate_cves_var,
        ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=3)

        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid_frame,
            text="Dry run only (simulate migration without modifying Git branch)",
            variable=self.dry_run_var,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=3)

        self.allow_failed_gates_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            grid_frame,
            text="Allow failed gates (create branch even if test gates time out)",
            variable=self.allow_failed_gates_var,
        ).grid(row=3, column=2, columnspan=2, sticky=tk.W, pady=3)

        # Action Buttons Row
        action_frame = ttk.Frame(f)
        action_frame.pack(fill=tk.X, pady=(4, 0))

        self.run_btn = tk.Button(
            action_frame,
            text="▶ Start Framework Upgrade",
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._execute_upgrade,
        )
        self.run_btn.pack(side=tk.LEFT)

        ttk.Button(
            action_frame,
            text="Clear Log",
            command=self._clear_log,
        ).pack(side=tk.RIGHT)

    def _build_audit_tab(self):
        f = self.audit_tab

        repo_frame = ttk.Frame(f)
        repo_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(repo_frame, text="Target Repository:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        repo_input_frame = ttk.Frame(repo_frame)
        repo_input_frame.pack(fill=tk.X, pady=(4, 0))

        self.audit_repo_var = tk.StringVar()
        self.audit_repo_entry = ttk.Entry(repo_input_frame, textvariable=self.audit_repo_var)
        self.audit_repo_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(
            repo_input_frame,
            text="Browse...",
            command=lambda: self._browse_directory(self.audit_repo_var),
        ).pack(side=tk.RIGHT)

        grid_frame = ttk.LabelFrame(f, text="Audit & Remediation Mode", padding="10")
        grid_frame.pack(fill=tk.X, pady=(0, 10))

        self.audit_mode_var = tk.StringVar(value="audit")

        ttk.Radiobutton(
            grid_frame,
            text="Audit Only (scan dependencies and display CVE vulnerability table)",
            variable=self.audit_mode_var,
            value="audit",
        ).pack(anchor=tk.W, pady=3)

        ttk.Radiobutton(
            grid_frame,
            text="Preview Plan (calculate compatible fixed versions without modifying files)",
            variable=self.audit_mode_var,
            value="preview",
        ).pack(anchor=tk.W, pady=3)

        ttk.Radiobutton(
            grid_frame,
            text="Apply & Remediate (apply scoped overrides and run test verification gates)",
            variable=self.audit_mode_var,
            value="apply",
        ).pack(anchor=tk.W, pady=3)

        action_frame = ttk.Frame(f)
        action_frame.pack(fill=tk.X, pady=(4, 0))

        self.audit_btn = tk.Button(
            action_frame,
            text="🛡️ Run Security Scan",
            bg="#059669",
            fg="#ffffff",
            activebackground="#047857",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=6,
            command=self._execute_audit,
        )
        self.audit_btn.pack(side=tk.LEFT)

    def _set_defaults(self):
        cwd = str(Path.cwd().resolve())
        self.upgrade_repo_var.set(cwd)
        self.audit_repo_var.set(cwd)

    def _browse_directory(self, target_var: tk.StringVar):
        selected = filedialog.askdirectory(initialdir=target_var.get() or ".")
        if selected:
            norm = str(Path(selected).resolve())
            self.upgrade_repo_var.set(norm)
            self.audit_repo_var.set(norm)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _set_running_state(self, is_running: bool, status_msg: str):
        if is_running:
            self.progress_bar.start(10)
            self.run_btn.configure(state="disabled")
            self.audit_btn.configure(state="disabled")
            self.status_var.set(status_msg)
        else:
            self.progress_bar.stop()
            self.run_btn.configure(state="normal")
            self.audit_btn.configure(state="normal")
            self.status_var.set(status_msg)

    def _execute_upgrade(self):
        repo_path_str = self.upgrade_repo_var.get().strip()
        if not repo_path_str or not Path(repo_path_str).exists():
            messagebox.showerror("Invalid Directory", "Please select a valid local repository directory.")
            return

        eco = self.upgrade_eco_var.get().strip()
        eco_param = None if eco == "auto" else eco
        mod_val = self.upgrade_mod_var.get().strip()
        mod_param = None if mod_val == "none" else mod_val
        inc = self.incremental_var.get()
        cve = self.remediate_cves_var.get()
        dry = self.dry_run_var.get()
        allow_fail = self.allow_failed_gates_var.get()

        self._clear_log()
        self._set_running_state(True, "Executing upgrade workflow...")

        def _worker():
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            redirector = TextRedirector(self.log_text, "stdout")
            sys.stdout = redirector
            sys.stderr = redirector

            try:
                from amstralift.service import UpgradeOrchestrator
                from amstralift.execution.stage_a import NoUpgradesAvailableError

                orchestrator = UpgradeOrchestrator()
                print(f"Starting AMstraLift upgrade on: {repo_path_str}")
                print(f"Incremental: {inc} | Remediate CVEs: {cve} | Modernizations: {mod_param}\n")

                bundle, proposal = orchestrator.run_upgrade(
                    repo_path=Path(repo_path_str),
                    ecosystem=eco_param,
                    dry_run=dry,
                    incremental=inc,
                    allow_failed_gates=allow_fail,
                    modernize=mod_param.split(",") if mod_param else None,
                    remediate_cves=cve,
                )

                print("\n=======================================================")
                print(f"✔ Upgrade workflow complete!")
                print(f"Branch: {proposal.branch_name}")
                print(f"Status: {proposal.publish_status}")
                print(f"Changes applied: {len(bundle.bundle.changes)}")
                for chg in bundle.bundle.changes:
                    print(f" - {chg.package_name}: {chg.from_version} -> {chg.to_version} ({chg.change_type})")
                print("=======================================================\n")
                self.root.after(0, lambda: self._set_running_state(False, f"Completed: {proposal.branch_name}"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Upgrade Complete",
                    f"AMstraLift completed successfully!\n\nTarget Branch: {proposal.branch_name}\nStatus: {proposal.publish_status}",
                ))
            except NoUpgradesAvailableError as ne:
                print(f"\n[Info] {ne}")
                self.root.after(0, lambda: self._set_running_state(False, "Repository already up to date."))
                self.root.after(0, lambda: messagebox.showinfo("Up to Date", str(ne)))
            except Exception as exc:
                print(f"\n[Error] Upgrade failed: {exc}", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, f"Failed: {exc}"))
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

        mode = self.audit_mode_var.get()
        self._clear_log()
        self._set_running_state(True, f"Running security {mode}...")

        def _worker():
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            redirector = TextRedirector(self.log_text, "stdout")
            sys.stdout = redirector
            sys.stderr = redirector

            try:
                from amstralift.security.orchestrator import SecurityOrchestrator
                from amstralift.security.ui import SecurityUI
                from rich.console import Console

                console = Console(file=redirector, force_terminal=True, color_system=None)
                orchestrator = SecurityOrchestrator()

                print(f"Scanning dependencies in: {repo_path_str} (Mode: {mode})...\n")
                result = orchestrator.run_remediation(
                    repo_path=Path(repo_path_str),
                    mode=mode,
                )

                SecurityUI.render_dashboard(result.report, console)
                if mode in ("preview", "apply") and result.plan:
                    SecurityUI.render_preview(result.plan, console)
                if mode == "apply":
                    SecurityUI.render_result(result, console)

                self.root.after(0, lambda: self._set_running_state(False, f"Audit complete: {len(result.report.findings)} CVE(s) found."))
            except Exception as exc:
                print(f"\n[Error] Audit failed: {exc}", file=sys.stderr)
                self.root.after(0, lambda: self._set_running_state(False, f"Audit error: {exc}"))
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
