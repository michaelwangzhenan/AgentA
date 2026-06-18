"""src/eval_runner.py UT：单任务锁 / 完成回收 / 取消（用临时 sleeper 模块跑真子进程）。"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import src.services.eval_runner as eval_runner


@pytest.fixture(autouse=True)
def _reset_runner():
    """每个用例后杀掉残留进程并清空全局状态。"""
    yield
    try:
        eval_runner.cancel()
    except Exception:
        pass
    eval_runner._job = None  # noqa: SLF001 — 测试复位


@pytest.fixture
def sleeper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """造一个可 `python -m` 的临时模块：sleep argv[1] 秒后退出。注册成 task=sleeper。"""
    mod = tmp_path / "agenta_eval_sleeper.py"
    mod.write_text(
        "import sys, time\n"
        "time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 5)\n",
        encoding="utf-8",
    )
    # 让子进程能 import 到它：PYTHONPATH 注入 tmp_path（start() 会 copy os.environ）
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setitem(eval_runner.EVAL_MODULES, "sleeper", "agenta_eval_sleeper")
    return "sleeper"


def _wait(pred: Callable[[dict], bool], timeout: float = 20.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = eval_runner.status()
        if pred(st):
            return st
        time.sleep(0.2)
    raise AssertionError(f"等待超时，最后状态={eval_runner.status()}")


def test_unknown_task_raises():
    with pytest.raises(ValueError):
        eval_runner.start("nope", [])


def test_run_completes(sleeper: str):
    st = eval_runner.start(sleeper, ["0"])
    assert st["state"] in ("running", "done")
    done = _wait(lambda s: s["state"] == "done")
    assert done["returncode"] == 0
    assert "agenta_eval_sleeper" in done["tail"]  # 日志记了命令行


def test_busy_returns_error(sleeper: str):
    eval_runner.start(sleeper, ["5"])
    _wait(lambda s: s["state"] == "running")
    with pytest.raises(RuntimeError):
        eval_runner.start(sleeper, ["5"])


def test_cancel_kills(sleeper: str):
    eval_runner.start(sleeper, ["20"])
    _wait(lambda s: s["state"] == "running")
    eval_runner.cancel()
    done = _wait(lambda s: s["state"] == "done")
    assert done["returncode"] is not None  # 被杀，退出码非 None


def test_status_idle_initially():
    eval_runner._job = None  # noqa: SLF001
    assert eval_runner.status()["state"] == "idle"


def test_model_injected_as_eval_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """选中模型经 AGENTA_EVAL_ACTIVE_MODEL 注入（非 ACTIVE_MODEL，否则被 .env override 冲掉）。"""
    out = tmp_path / "seen_env.txt"
    mod = tmp_path / "agenta_eval_envdump.py"
    mod.write_text(
        "import os, pathlib\n"
        f"pathlib.Path(r'{out}').write_text(os.environ.get('AGENTA_EVAL_ACTIVE_MODEL', ''), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setitem(eval_runner.EVAL_MODULES, "envdump", "agenta_eval_envdump")

    eval_runner.start("envdump", [], model="kimi-k2.5")
    _wait(lambda s: s["state"] == "done")
    assert out.read_text(encoding="utf-8") == "kimi-k2.5"
