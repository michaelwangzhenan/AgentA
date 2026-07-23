"""将 LLM 供应商异常转为用户可读的聊天文案（不暴露原始 JSON / HTTP 细节）。"""

from __future__ import annotations

import json
import re
from typing import Any

# 与输入过滤命中时的统一拒答文案保持一致
CONTENT_BLOCKED_REPLY = "尊敬的用户您好，让我们换个话题再聊聊吧。"

_BALANCE_HINTS = (
    "insufficient",
    "balance",
    "余额",
    "quota",
    "billing",
    "payment required",
    "credit",
    "no funds",
    "exceeded your current",
    "额度",
    "欠费",
)

_AUTH_HINTS = (
    "invalid api key",
    "authentication",
    "unauthorized",
    "api key",
    "invalid_api_key",
)

_RATE_HINTS = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
)

_TIMEOUT_HINTS = (
    "timeout",
    "timed out",
    "deadline exceeded",
)


def _extract_error_message(raw: str) -> str:
    """从 Error code: 400 - {...} 或嵌套 JSON 里抽出 message。"""
    text = raw.strip()
    if not text:
        return text
    # 去掉常见前缀：Error code: 400 -
    text = re.sub(r"^Error code:\s*\d+\s*-\s*", "", text, flags=re.I).strip()
    if text.startswith("{") or text.startswith("'"):
        try:
            # 单引号 JSON（部分 SDK 的 str(dict)）
            normalized = text.replace("'", '"')
            data = json.loads(normalized)
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict) and err.get("message"):
                    return str(err["message"])
                if data.get("message"):
                    return str(data["message"])
        except json.JSONDecodeError:
            pass
    return raw


def friendly_llm_error(exc: Exception | str) -> str:
    """把供应商 / Agent 层异常映射为聊天里展示的友好文案。"""
    raw = str(exc)
    text = _extract_error_message(raw)
    lower = text.lower()
    raw_lower = raw.lower()

    if "content exists risk" in lower or "content_filter" in lower or "content exists risk" in raw_lower:
        return CONTENT_BLOCKED_REPLY

    if any(h in lower or h in raw_lower for h in _BALANCE_HINTS):
        return "当前模型的账户余额或额度不足，请切换其他 LLM，或充值后再试。"

    if any(h in lower for h in _AUTH_HINTS) or " 401" in raw or raw.strip().startswith("401"):
        return "当前模型的 API 密钥无效或未配置，请在设置中检查 API Key，或切换到其他 LLM。"

    if any(h in lower for h in _RATE_HINTS):
        return "当前模型请求过于频繁，请稍后再试，或在设置中切换到其他 LLM。"

    if any(h in lower for h in _TIMEOUT_HINTS):
        return "模型响应超时，请稍后重试，或在设置中切换到其他 LLM。"

    if " 400" in raw or "invalid_request" in lower:
        return CONTENT_BLOCKED_REPLY

    if " 403" in raw or "forbidden" in lower:
        return "当前模型拒绝处理该请求，请换个问法，或在设置中切换到其他 LLM。"

    if " 500" in raw or " 502" in raw or " 503" in raw or "internal server" in lower:
        return "模型服务暂时不可用，请稍后重试，或在设置中切换到其他 LLM。"

    return "模型暂时无法回答，请稍后重试，或在设置中切换到其他 LLM。"


def final_answer_payload(text: str, *, provider_error: bool = True) -> dict[str, Any]:
    return {"text": text, "usage": None, "provider_error": provider_error}
