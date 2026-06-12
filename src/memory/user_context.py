"""请求级当前用户上下文。

多用户下，独享数据（会话 / 记忆 / 学习计划 / quiz / SRS / rules）按 user_id 隔离。
难点是 Agent 是进程级单例，tools 调 store 时拿不到 HTTP 请求里的用户。这里用
`contextvars` 维护"当前请求是哪个用户"：

- API 路由 / chat 入口在处理请求前 `set_current_user(uid)`；
- store 方法不显式传 user_id 时回落到 `current_user_id()`；
- CLI / 测试 / `AUTH_ENABLED=false` 时回落到 `config.DEFAULT_USER_ID`。

contextvars 在同一线程内有效；`run_in_executor` 不会自动复制上下文，所以流式
chat 在 executor 线程入口要再 set 一次（见 routes/chat.py）。

本模块放在 `src/memory/`：与「按用户隔离的持久化」同属一层，供各 `*Store` 与
API / Agent 依赖，避免与 `src/agent/core` 的「core」混名。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

import src.config as _cfg

# 不设默认值，未 set 时由 current_user_id() 回落到 config.DEFAULT_USER_ID，
# 这样改 .env 的 DEFAULT_USER_ID 立即生效，不被模块导入期的快照锁死。
_current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def current_user_id() -> int:
    """返回当前请求的用户 id；未设置时回落到 config.DEFAULT_USER_ID。"""
    uid = _current_user_id.get()
    return uid if uid is not None else _cfg.DEFAULT_USER_ID


def set_current_user(user_id: int | None) -> None:
    """设置当前请求的用户 id（传 None 清空，回落到默认用户）。"""
    _current_user_id.set(user_id)


@contextmanager
def use_user(user_id: int) -> Iterator[None]:
    """临时把当前用户切到 user_id，退出作用域自动还原。"""
    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)
