"""thinking（推理）参数翻译 —— provider 直连与 langchain 两条路径共用的单一真相源。

把「ModelConfig + ThinkingSpec + budget」翻译成各 SDK 需要的请求参数。抽出来共享，
避免 `provider` / `claude_provider` / `langchain_provider` 各写一份、悄悄对不上
（历史上 Claude budget cap 就漂移过：一处用 max_output_tokens、一处用 CLAUDE_MAX_TOKENS）。
"""

from __future__ import annotations

from typing import Any

import src.config as config

# claude 单次最大输出 tokens 的兜底上限（模型未声明 max_output_tokens 时用）
_CLAUDE_OUTPUT_CAP: int = 64_000
# thinking 时给正文预留的 tokens：max_tokens 必须 > budget_tokens，Anthropic 强制
_THINKING_HEADROOM: int = 4096
# Anthropic 允许的最小 budget
_MIN_BUDGET: int = 1024


def claude_thinking_budget(
    model: "config.ModelConfig", budget_tokens: int
) -> tuple[int, int]:
    """算 Claude Extended Thinking 的 ``(budget, max_tokens)``。

    cap 取模型 ``max_output_tokens``（未声明则兜底 64000）；budget 夹在
    ``[1024, cap-4096]``；``max_tokens = budget + 4096`` 保证严格大于 budget。
    """
    cap = model.max_output_tokens or _CLAUDE_OUTPUT_CAP
    budget = max(_MIN_BUDGET, min(budget_tokens, cap - _THINKING_HEADROOM))
    return budget, budget + _THINKING_HEADROOM


def openai_thinking_extra_body(
    model: "config.ModelConfig", spec: "config.ThinkingSpec", budget: int
) -> tuple[str, dict[str, Any]]:
    """算 OpenAI 兼容协议开 thinking 时的 ``(model_id, extra_body)``。

    在模型基础 ``extra_body`` 上叠加 ``spec.enable_extra_body``（翻 thinking 开关）、
    按 ``spec.budget_key`` 透传预算、按 ``spec.thinking_model`` 切专用思考模型。
    """
    extra_body = dict(model.extra_body or {})
    extra_body.update(spec.enable_extra_body or {})
    if spec.budget_key:
        extra_body[spec.budget_key] = budget
    model_id = spec.thinking_model or model.model_id
    return model_id, extra_body
