"""Config 改动后的副作用 hook。

每个 hook 在对应 key 改完之后被调用；幂等、失败不抛异常（只 log warning）。
未注册 hook 的 key 走 noop —— 多数 key 的下游消费者都是每次调用读 `_cfg.X`，
setattr 一次后下一次调用即生效，无需任何额外动作。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import src.config as _cfg

logger = logging.getLogger(__name__)


def _on_log_level_changed(_old: Any, new: Any) -> None:
    """LOG_LEVEL 改后立即应用到 root logger。"""
    try:
        logging.getLogger().setLevel(str(new).upper())
        logger.info("[config] LOG_LEVEL → %s", new)
    except Exception as e:
        logger.warning("[config] 应用 LOG_LEVEL 失败: %s", e)


def _on_mcp_changed(_old: Any, _new: Any) -> None:
    """MCP_ENABLED / MCP_CONFIG_FILE 改后重载 MCP manager。"""
    try:
        from src.agent.core.mcp_config import load_mcp_config
        from src.agent.core.mcp_manager import get_shared_manager
        manager = get_shared_manager()
        if not _cfg.MCP_ENABLED:
            manager.shutdown()
            logger.info("[config] MCP 已禁用，已停所有 server")
            return
        specs = load_mcp_config()
        manager.reload(specs)
        logger.info("[config] MCP 已重载，specs=%d", len(specs))
    except Exception as e:
        logger.warning("[config] MCP 重载失败: %s", e)


_HOOKS: dict[str, Callable[[Any, Any], None]] = {
    "LOG_LEVEL": _on_log_level_changed,
    "MCP_ENABLED": _on_mcp_changed,
    "MCP_CONFIG_FILE": _on_mcp_changed,
}


def run_post_change_hook(key: str, old_value: Any, new_value: Any) -> None:
    """根据 key 派发对应 hook；未注册则 noop。"""
    hook = _HOOKS.get(key)
    if hook is None:
        return
    try:
        hook(old_value, new_value)
    except Exception as e:
        logger.warning("[config] hook %s 异常: %s", key, e)


def has_hook(key: str) -> bool:
    return key in _HOOKS
