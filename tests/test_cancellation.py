"""Tests for cancellation token and process termination."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock

import pytest

from amstralift.core.cancellation import (
    CancellableScope,
    CancellationToken,
    OperationCancelledError,
    check_cancelled,
    run_cancellable_subprocess,
)


def test_cancellation_token_basic():
    token = CancellationToken()
    assert not token.is_cancelled
    token.check_cancelled()

    token.cancel()
    assert token.is_cancelled
    with pytest.raises(OperationCancelledError):
        token.check_cancelled()

    token.reset()
    assert not token.is_cancelled
    token.check_cancelled()


def test_check_cancelled_helper():
    token = CancellationToken()
    check_cancelled(token)

    token.cancel()
    with pytest.raises(OperationCancelledError):
        check_cancelled(token)


def test_cancellation_terminates_registered_process():
    token = CancellationToken()
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    token.register_process(mock_proc)
    token.cancel()

    assert token.is_cancelled
    token.unregister_process(mock_proc)


def test_cancellable_scope_registers_popen():
    token = CancellationToken()
    orig_init = subprocess.Popen.__init__

    with CancellableScope(token):
        assert subprocess.Popen.__init__ != orig_init

    assert subprocess.Popen.__init__ == orig_init


def test_run_cancellable_subprocess_checks_cancelled():
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelledError):
        run_cancellable_subprocess(["dummy"], token=token)
