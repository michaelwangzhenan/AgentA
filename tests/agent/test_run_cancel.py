"""run_cancel 协作式取消。"""

from __future__ import annotations

import threading

from src.agent.core import run_cancel


def test_cancel_scope_lifecycle() -> None:
    ev = threading.Event()
    assert run_cancel.is_cancelled() is False
    with run_cancel.cancel_scope(ev):
        assert run_cancel.is_cancelled() is False
        ev.set()
        assert run_cancel.is_cancelled() is True
    assert run_cancel.is_cancelled() is False
