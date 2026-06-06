"""
ThinkingPolicy —— Extended Thinking 配置（Helper 层）

职责：
- `ThinkingConfig` 数据类：enabled / budget 配置，可从全局 config 实例化
- `effective_budget()`：返回固定 budget（档位由 UI 手动选）

被三种 Agent 实现共享：Python（已接） / LangChain / AutoGPT（子任务通常不启用 thinking）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import src.config as _cfg

logger = logging.getLogger(__name__)


@dataclass
class ThinkingConfig:
    """Extended Thinking 运行时配置，可被 Agent 与调用方共享同一实例。"""
    enabled: bool = False
    budget: int = 8_000

    @classmethod
    def from_config(cls) -> "ThinkingConfig":
        """从全局 config 读取默认值创建实例。"""
        return cls(
            enabled=_cfg.THINKING_ENABLED,
            budget=_cfg.THINKING_BUDGET,
        )


class ThinkingPolicy:
    """
    Extended Thinking 调度策略。

    每轮 LLM 调用前由 Agent 调 `effective_budget()` 拿到当前轮的 budget，
    传给 `call_with_thinking(budget_tokens=...)`。
    """

    def __init__(self, config: ThinkingConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def effective_budget(self) -> int:
        """返回本轮 budget（固定值，UI 档位决定）。"""
        return self.config.budget
