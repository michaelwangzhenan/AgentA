"""离线评估的单任务运行器：同时只跑一个 eval 子进程，输出落日志，可查状态 / 取消。

设计要点：
- **单任务全局锁**：同时只允许一个 eval 在跑（多数耗 CPU / token，重）。
- **后台子进程**：`python -m tools.<module> <args>`，stdout/stderr 落 `logs/eval_runs/`；
  与请求解耦——前端轮询 status 看进度，跨页面存活、重连即恢复。
- **模型注入**：选中的测试模型经子进程 `AGENTA_EVAL_ACTIVE_MODEL` env 传入（该键不在 `.env`，
  扛得住各 eval 入口的 `load_dotenv(override=True)`；`config.ACTIVE_MODEL` 会优先读它）。不写任何持久配置。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # src/services/x.py → 仓库根
_LOG_DIR = _PROJECT_ROOT / "logs" / "eval_runs"
_IS_WIN = os.name == "nt"

# task_key -> 模块路径。后续 eval 逐个加。
EVAL_MODULES: dict[str, str] = {
    "security": "tools.agent_eval.security.adversarial",
    "rag": "tools.rag_eval.runner",
    "memory": "tools.agent_eval.memory.recall_golden",
    "skills": "tools.agent_eval.skills.recall_skill",
    "mcp": "tools.agent_eval.mcp.eval_mcp",
    "perf": "tools.agent_eval.perf_eval",
    "plan": "tools.agent_eval.plan.eval_plan",
    "critic": "tools.agent_eval.critic.eval_critic",
    "learning_plan": "tools.agent_eval.plan_business.eval_learning_plan",
    "quiz": "tools.agent_eval.quiz.eval_quiz",
    "srs": "tools.agent_eval.srs.eval_srs",
}

_lock = threading.Lock()
# 当前（唯一）任务状态；None = 从未跑过 / 已清空
_job: dict | None = None


def _tail(path: Path, n: int = 200) -> str:
    """读日志末尾 n 行；读失败返回空串（子进程正写入时也尽量容错）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _running(job: dict | None) -> bool:
    return bool(job and job["proc"].poll() is None)


def _status_locked() -> dict:
    """在持锁状态下组装当前状态；顺便回收已结束进程的退出码。"""
    if _job is None:
        return {"state": "idle"}
    rc = _job["proc"].poll()
    if rc is not None and _job["returncode"] is None:
        _job["returncode"] = rc
        _job["finished_at"] = time.time()
    state = "running" if rc is None else "done"
    return {
        "state": state,
        "task": _job["task"],
        "model": _job["model"],
        "args": _job["args"],
        "started_at": _job["started_at"],
        "finished_at": _job["finished_at"],
        "returncode": _job["returncode"],
        "tail": _tail(_job["log_path"]),
    }


def start(task: str, args: list[str], model: str | None = None) -> dict:
    """启动一个 eval 子进程。task 未注册 → ValueError；已有任务在跑 → RuntimeError。"""
    global _job
    with _lock:
        module = EVAL_MODULES.get(task)
        if module is None:
            raise ValueError(f"未知评估任务：{task}")
        if _running(_job):
            raise RuntimeError("已有评估在运行，请等它结束或先取消")

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        log_path = _LOG_DIR / f"{task}-{ts}.log"

        env = os.environ.copy()
        if model:
            # 用 AGENTA_EVAL_ACTIVE_MODEL（不在 .env 里）而非直接设 ACTIVE_MODEL：各 eval 入口
            # 的 load_dotenv(override=True) 会用 .env 的 ACTIVE_MODEL 覆盖普通注入，唯独这个
            # 专用键不会被覆盖；config.ACTIVE_MODEL 会优先读它。
            env["AGENTA_EVAL_ACTIVE_MODEL"] = model

        cmd = [sys.executable, "-u", "-m", module, *args]
        logf = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — 进程存活期间持有，结束时关
        logf.write(f"$ {' '.join(cmd)}\n" + (f"# ACTIVE_MODEL={model}\n" if model else "") + "\n")
        logf.flush()
        # 独立进程组，便于 cancel 时连子孙一起杀
        popen_kw: dict = {"cwd": str(_PROJECT_ROOT), "env": env, "stdout": logf, "stderr": subprocess.STDOUT}
        if _IS_WIN:
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kw)

        _job = {
            "task": task,
            "args": args,
            "model": model,
            "started_at": time.time(),
            "finished_at": None,
            "returncode": None,
            "log_path": log_path,
            "proc": proc,
            "logf": logf,
        }
        return _status_locked()


def status() -> dict:
    with _lock:
        st = _status_locked()
        # 进程结束后关闭日志文件句柄（只关一次）
        if _job and _job["returncode"] is not None and not _job["logf"].closed:
            _job["logf"].close()
        return st


def cancel() -> dict:
    """杀掉当前在跑的进程树；没有在跑则原样返回状态。"""
    with _lock:
        if not _running(_job):
            return _status_locked()
        proc = _job["proc"]
        try:
            if _IS_WIN:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            else:
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return _status_locked()
