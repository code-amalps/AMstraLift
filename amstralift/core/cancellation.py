"""Cancellation and process termination support for AMstraLift workflows.

Enables safe, responsive aborting of long-running operations (npm install,
test runners, builds) and their underlying child process trees on Windows and POSIX.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


class OperationCancelledError(Exception):
    """Raised when an ongoing upgrade, audit, or subprocess is aborted by the user."""

    pass


class CancellationToken:
    """Thread-safe token for signalling cancellation and terminating child processes."""

    def __init__(self) -> None:
        self._cancelled = False
        self._active_proc: Optional[subprocess.Popen[Any]] = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Signal cancellation and immediately terminate any registered child process tree."""
        with self._lock:
            self._cancelled = True
            proc = self._active_proc

        if proc is not None:
            # Terminate asynchronously so GUI thread / caller is never blocked or frozen
            threading.Thread(target=self._terminate_proc_tree, args=(proc,), daemon=True).start()

    def reset(self) -> None:
        """Reset the token back to normal uncancelled state."""
        with self._lock:
            self._cancelled = False
            self._active_proc = None

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        with self._lock:
            return self._cancelled

    def check_cancelled(self) -> None:
        """Raise OperationCancelledError if cancellation has been requested."""
        if self.is_cancelled:
            raise OperationCancelledError("Operation was cancelled by user.")

    def register_process(self, proc: subprocess.Popen[Any]) -> None:
        """Register an active subprocess for immediate termination upon cancellation."""
        with self._lock:
            if self._cancelled:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise OperationCancelledError("Operation was cancelled by user.")
            self._active_proc = proc

    def unregister_process(self, proc: Optional[subprocess.Popen[Any]] = None) -> None:
        """Unregister a completed subprocess."""
        with self._lock:
            if self._active_proc is proc or proc is None:
                self._active_proc = None

    @staticmethod
    def _terminate_proc_tree(proc: subprocess.Popen[Any]) -> None:
        """Recursively terminate the process tree across Windows and POSIX."""
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                # On Windows, taskkill /F /T kills the entire process tree (node, karma, chrome, etc.)
                flags = 0
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    flags = subprocess.CREATE_NO_WINDOW
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                    creationflags=flags,
                )
            else:
                proc.kill()
        except Exception as exc:
            logger.debug("Failed to terminate process tree for PID %s: %s", getattr(proc, "pid", None), exc)
            try:
                proc.kill()
            except Exception:
                pass


_GLOBAL_TOKEN = CancellationToken()


def get_default_cancellation_token() -> CancellationToken:
    """Return the global default cancellation token."""
    return _GLOBAL_TOKEN


def check_cancelled(token: Optional[CancellationToken] = None) -> None:
    """Convenience helper to check cancellation status."""
    tok = token or _GLOBAL_TOKEN
    tok.check_cancelled()


def run_cancellable_subprocess(
    cmd: Any,
    *,
    token: Optional[CancellationToken] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Execute a subprocess while tracking it against a CancellationToken.

    If the token is cancelled while the process is running, the process tree
    is terminated immediately and OperationCancelledError is raised.
    """
    tok = token or _GLOBAL_TOKEN
    tok.check_cancelled()

    pop_kwargs = dict(kwargs)
    if pop_kwargs.pop("capture_output", False):
        if "stdout" not in pop_kwargs:
            pop_kwargs["stdout"] = subprocess.PIPE
        if "stderr" not in pop_kwargs:
            pop_kwargs["stderr"] = subprocess.PIPE

    proc = subprocess.Popen(cmd, **pop_kwargs)
    tok.register_process(proc)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        tok._terminate_proc_tree(proc)
        proc.kill()
        proc.wait()
        raise
    except BaseException:
        tok._terminate_proc_tree(proc)
        raise
    finally:
        tok.unregister_process(proc)

    if tok.is_cancelled:
        raise OperationCancelledError("Operation was cancelled by user.")

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


class CancellableScope:
    """Context manager that automatically registers any spawned subprocesses with a CancellationToken."""

    def __init__(self, token: Optional[CancellationToken] = None) -> None:
        self.token = token or _GLOBAL_TOKEN
        self._orig_popen_init: Any = None

    def __enter__(self) -> CancellableScope:
        tok = self.token
        orig_init = subprocess.Popen.__init__
        self._orig_popen_init = orig_init

        def _hooked_popen_init(self_proc: subprocess.Popen[Any], *args: Any, **kwargs: Any) -> None:
            orig_init(self_proc, *args, **kwargs)
            # Never register internal cleanup commands like taskkill
            cmd = args[0] if args else kwargs.get("args")
            if cmd:
                first = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd)
                if "taskkill" in str(first).lower():
                    return
            tok.register_process(self_proc)

        subprocess.Popen.__init__ = _hooked_popen_init  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._orig_popen_init is not None:
            subprocess.Popen.__init__ = self._orig_popen_init  # type: ignore[assignment]
            self._orig_popen_init = None
        self.token.unregister_process()

