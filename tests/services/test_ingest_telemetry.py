"""入库可观测性与并发保护测试。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

import src.config as config
from src.services import ingest_telemetry as telemetry


class TestIngestTelemetry:
    def test_probe_logs_phase_and_flushes(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        flushed: list[bool] = []

        def _fake_flush() -> None:
            flushed.append(True)

        caplog.set_level(logging.INFO)
        path = tmp_path / "sample.txt"
        path.write_text("hello", encoding="utf-8")
        probe = telemetry.IngestProbe(file_path=path, rel_path="sample.txt")

        monkeypatch.setattr(telemetry, "flush_log_handlers", _fake_flush)
        monkeypatch.setattr(telemetry, "process_rss_mb", lambda: 128)
        monkeypatch.setattr(telemetry, "system_avail_mb", lambda: 512)
        with probe.track("parse"):
            pass

        assert any("[ingest] phase=parse event=start" in r.message for r in caplog.records)
        assert any("[ingest] phase=parse event=done" in r.message for r in caplog.records)
        assert any("rss_mb=128" in r.message for r in caplog.records)
        assert flushed

    def test_probe_logs_error_on_exception(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        caplog.set_level(logging.ERROR)
        path = tmp_path / "sample.txt"
        path.write_text("hello", encoding="utf-8")
        probe = telemetry.IngestProbe(file_path=path, rel_path="sample.txt")

        with pytest.raises(RuntimeError, match="boom"):
            with probe.track("embed"):
                raise RuntimeError("boom")

        assert any("[ingest] phase=embed event=error" in r.message for r in caplog.records)

    def test_ingest_slot_limits_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "INGEST_MAX_CONCURRENT", 1)
        telemetry._semaphore = None
        telemetry._sem_limit = 0
        gate = threading.Event()
        holder = threading.Event()

        def _holder() -> None:
            with telemetry.ingest_slot():
                holder.set()
                assert gate.wait(2)

        t = threading.Thread(target=_holder, daemon=True)
        t.start()
        assert holder.wait(2)

        acquired = threading.Event()

        def _waiter() -> None:
            with telemetry.ingest_slot():
                acquired.set()

        waiter = threading.Thread(target=_waiter, daemon=True)
        waiter.start()
        waiter.join(timeout=0.2)
        assert not acquired.is_set()

        gate.set()
        t.join(timeout=2)
        assert acquired.wait(2)
