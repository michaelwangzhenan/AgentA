"""
ThinkingPolicy —— Extended Thinking 配置与 budget 估算策略（Helper 层）

职责：
- `ThinkingConfig` 数据类：enabled / budget / adaptive 三档配置，可从全局 config 实例化
- `effective_budget(messages)`：adaptive 模式下基于 messages 长度估算实际 budget；
  非 adaptive 时直接返回固定 budget

被三种 Agent 实现共享：Python（已接） / LangChain / AutoGPT（子任务通常不启用 thinking）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import src.config as _cfg
from src.llm.provider import estimate_thinking_budget

logger = logging.getLogger(__name__)


@dataclass
class ThinkingConfig:
    """Extended Thinking 运行时配置，可被 Agent 与调用方共享同一实例。"""
    enabled: bool = False
    budget: int = 8_000
    adaptive: bool = False

    @classmethod
    def from_config(cls) -> "ThinkingConfig":
        """从全局 config 读取默认值创建实例。"""
        return cls(
            enabled=_cfg.THINKING_ENABLED,
            budget=_cfg.THINKING_BUDGET,
            adaptive=_cfg.THINKING_ADAPTIVE,
        )


class ThinkingPolicy:
    """
    Extended Thinking 调度策略。

    每轮 LLM 调用前由 Agent 调 `effective_budget(messages)` 拿到当前轮的 budget，
    传给 `call_with_thinking(budget_tokens=...)`。
    """

    def __init__(self, config: ThinkingConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def effective_budget(self, messages: list[dict[str, Any]]) -> int:
        """
        计算本轮的 budget：
        - `adaptive=True`  → 用 `estimate_thinking_budget(messages, fixed_budget)` 动态估算
        - `adaptive=False` → 直接返回 `config.budget`
        """
        if not self.config.adaptive:
            return self.config.budget
        budget = estimate_thinking_budget(messages, self.config.budget)
        logger.info("[ThinkingPolicy] Adaptive Thinking: 估算 budget=%d tokens", budget)
        return budget
