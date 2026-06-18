"""
plan_manager —— Plan-Execute 状态封装与 reconstruct

Plan 不在 SessionStore 新建表，完全依赖 messages 历史中的
`make_plan` / `update_step` / `abort_plan` 三类 tool call 自然落库：

    [assistant, tool_calls=[make_plan(steps=[...])]]
    [tool, tool_call_id=A, content="plan 已记录 ..."]
    ... 中间是各 step 的业务 tool 调用 ...
    [assistant, tool_calls=[update_step(step_id=1, status="success")]]
    [tool, tool_call_id=B, content="step 1 updated"]

`reconstruct_from_messages(messages)` 倒序找最新 `make_plan`，正向叠加之后
所有 `update_step` / `abort_plan` 调用 → 当前 plan 状态。

依赖 messages 标准 OpenAI 格式（assistant.tool_calls 为 list），兼容 dict 与
SDK 对象两种 tool_call 形态（OpenAI SDK 返回的是对象，写库后从 SQLite 读回
是 dict）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

StepStatus = Literal["pending", "success", "failed", "skipped"]
_ALL_STEP_STATUS: tuple[str, ...] = ("pending", "success", "failed", "skipped")


@dataclass
class PlanStep:
    """单个 plan 步骤。id 从 1 起，对外可见编号；text 是 LLM 生成的步骤描述。"""

    id: int
    text: str
    status: StepStatus = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanState:
    """plan 当前态：steps 列表 + 是否已 abort。"""

    steps: list[PlanStep] = field(default_factory=list)
    aborted: bool = False

    # ── 构造 ──────────────────────────────────────────────────────────────────

    @classmethod
    def from_step_texts(cls, step_texts: list[str]) -> "PlanState":
        """make_plan(steps: list[str]) 用：step.id 从 1 起按入参顺序分配。"""
        return cls(steps=[PlanStep(id=i + 1, text=t) for i, t in enumerate(step_texts)])

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def next_pending_step(self) -> PlanStep | None:
        """第一个 pending step；plan aborted 或全部 step 已结束返回 None。"""
        if self.aborted:
            return None
        for s in self.steps:
            if s.status == "pending":
                return s
        return None

    def is_complete(self) -> bool:
        """plan 是否到达终态（aborted 或所有 step 非 pending）。"""
        return self.aborted or all(s.status != "pending" for s in self.steps)

    def progress(self) -> tuple[int, int]:
        """(已结束 step 数（非 pending）, 总 step 数)。"""
        done = sum(1 for s in self.steps if s.status != "pending")
        return done, len(self.steps)

    # ── 更新 ──────────────────────────────────────────────────────────────────

    def update(self, step_id: int, status: str, note: str = "") -> bool:
        """
        更新指定 step 的 status / note。

        Returns:
            True — 找到 step_id 且 status 合法、已更新；
            False — step_id 不存在或 status 非法（含 "pending" 反向更新也禁止）。
        """
        if status not in _ALL_STEP_STATUS or status == "pending":
            logger.warning("[plan] update_step status 非法: %r", status)
            return False
        for s in self.steps:
            if s.id == step_id:
                s.status = status  # type: ignore[assignment]
                if note:
                    s.note = note
                return True
        logger.warning("[plan] update_step 找不到 step_id=%s", step_id)
        return False


# ── reconstruct ──────────────────────────────────────────────────────────────


def reconstruct_from_messages(messages: list[dict[str, Any]]) -> PlanState | None:
    """
    从 messages 历史 reconstruct 当前 plan 状态。

    算法：
      1. 倒序找最新的 `assistant.tool_calls[*].function.name == "make_plan"`；
         若没有 → 返回 None
      2. 解析该 tool_call 的 arguments.steps 初始化 PlanState
      3. 正向遍历该 message 之后的所有 assistant.tool_calls：
         - `update_step(step_id, status, note?)` → `state.update(...)`
         - `abort_plan(reason?)`                  → `state.aborted = True`

    Returns:
        PlanState 或 None（messages 中无 make_plan 调用）。
    """
    make_plan_idx: int | None = None
    initial_steps: list[str] = []

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if _tc_name(tc) == "make_plan":
                steps_arg = _tc_args(tc).get("steps", [])
                if isinstance(steps_arg, list) and all(isinstance(s, str) for s in steps_arg):
                    initial_steps = steps_arg
                    make_plan_idx = i
                    break
        if make_plan_idx is not None:
            break

    if make_plan_idx is None:
        return None

    state = PlanState.from_step_texts(initial_steps)

    for j in range(make_plan_idx + 1, len(messages)):
        msg = messages[j]
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            name = _tc_name(tc)
            args = _tc_args(tc)
            if name == "update_step":
                step_id = args.get("step_id")
                status = args.get("status")
                note = args.get("note", "") or ""
                if isinstance(step_id, int) and isinstance(status, str):
                    state.update(step_id, status, note)
            elif name == "abort_plan":
                state.aborted = True

    return state


# ── tool_call 形态兼容（SDK 对象 / SQLite 反序列化 dict） ────────────────────


def _tc_name(tc: Any) -> str:
    """读 tool_call.function.name；兼容 dict 与 SDK 对象。"""
    if isinstance(tc, dict):
        return (tc.get("function") or {}).get("name", "") or ""
    fn = getattr(tc, "function", None)
    return getattr(fn, "name", "") if fn else ""


def _tc_args(tc: Any) -> dict[str, Any]:
    """读 tool_call.function.arguments 并 JSON 解析。"""
    if isinstance(tc, dict):
        raw = (tc.get("function") or {}).get("arguments", "{}")
    else:
        fn = getattr(tc, "function", None)
        raw = getattr(fn, "arguments", "{}") if fn else "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
