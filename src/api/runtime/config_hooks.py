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


def _on_thinking_changed(_old: Any, _new: Any) -> None:
    """THINKING_* 改后同步到 agent 单例的 thinking_cfg。

    agent 在 `__init__` 时一次性 from_config 快照 thinking_cfg，之后不再读 `_cfg`；
    若不同步，UI 改的档位对运行中的 agent 无效。这里只在 agent 已构造时 mutate
    其 thinking_cfg（dataclass，可变）；未构造则跳过——下次构造会自然读到新值。
    """
    try:
        from src.api.deps import get_agent
        if get_agent.cache_info().currsize == 0:
            return
        cfg = get_agent().thinking_cfg
        cfg.enabled = _cfg.THINKING_ENABLED
        cfg.budget = _cfg.THINKING_BUDGET
        logger.info(
            "[config] thinking 同步到 agent：enabled=%s budget=%d",
            cfg.enabled, cfg.budget,
        )
    except Exception as e:
        logger.warning("[config] 同步 thinking 到 agent 失败: %s", e)


def _on_imp_method_changed(_old: Any, new: Any) -> None:
    """IMP_METHOD 改后清掉 agent 单例缓存，下一次请求按新实现重建。

    agent 单例在 `get_agent()` 里按 `IMP_METHOD` 选实现且 `lru_cache` 缓存，不清缓存
    则一直复用旧实现。清后下一轮对话即用新实现（会重开 sub-store 连接，可接受）。
    """
    try:
        from src.api.deps import get_agent
        get_agent.cache_clear()
        logger.info("[config] IMP_METHOD → %s，agent 单例已失效将重建", str(new).upper())
    except Exception as e:
        logger.warning("[config] 切换 IMP_METHOD 失败: %s", e)


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


def _on_golden_db_path_changed(_old: Any, new: Any) -> None:
    """RAG_GOLDEN_DB_PATH 改后清掉 golden 单例，下次访问按新路径重建。

    GoldenStore 是进程级缓存单例（构造时读 RAG_GOLDEN_DB_PATH），不重置则一直连旧库。
    """
    try:
        from src.memory.golden_store import reset_shared_store
        reset_shared_store()
        logger.info("[config] RAG_GOLDEN_DB_PATH → %s，golden 单例已重置", new)
    except Exception as e:
        logger.warning("[config] 重置 golden 单例失败: %s", e)


_HOOKS: dict[str, Callable[[Any, Any], None]] = {
    "LOG_LEVEL": _on_log_level_changed,
    "RAG_GOLDEN_DB_PATH": _on_golden_db_path_changed,
    "IMP_METHOD": _on_imp_method_changed,
    "MCP_ENABLED": _on_mcp_changed,
    "MCP_CONFIG_FILE": _on_mcp_changed,
    "THINKING_ENABLED": _on_thinking_changed,
    "THINKING_BUDGET": _on_thinking_changed,
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
