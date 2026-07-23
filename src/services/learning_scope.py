"""学习范围分类：判断用户问题是否属于「学习相关」话题。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import src.config as config

logger = logging.getLogger(__name__)

CLASSIFIER_MODEL = "kimi-k2.5"

_SYSTEM_PROMPT = """你是问答范围分类器。判断用户消息是否属于「学习相关」话题。

范围内：知识获取、资料整理、编程练习、考试复习、技能训练、作业辅导、错题讲解、技术概念解释、学习方法、读书笔记等。
范围外：闲聊、娱乐、时政评论、医疗实操、违法咨询、旅游攻略、情感陪伴、天气、八卦、购物推荐等与学习无关的内容。

只输出一个 JSON 对象，不要输出其他任何文字：
{"in_scope": true} 或 {"in_scope": false, "reason": "简短原因"}"""

_INTERNAL_REASONS = frozenset({
    "empty_message",
    "model_unavailable",
    "parse_error",
    "api_error",
})

_OUT_OF_SCOPE_REPLY_BASE = (
    "我是个人学习助手，目前只回答与学习相关的问题，"
    "例如知识获取、编程练习、考试复习、资料整理、技能训练等。"
)


def out_of_scope_reply(reason: str | None = None) -> str:
    """范围外请求的友好拒答文案（与敏感词拦截区分）。"""
    if reason and reason not in _INTERNAL_REASONS:
        return (
            f"{_OUT_OF_SCOPE_REPLY_BASE}"
            f"您刚才的问题（{reason}）不在服务范围内，可以换个学习相关的问题试试。"
        )
    return (
        f"{_OUT_OF_SCOPE_REPLY_BASE}"
        "您刚才的问题不在服务范围内，可以换个学习相关的问题试试。"
    )


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class ScopeResult:
    in_scope: bool
    reason: str | None = None


def parse_classifier_response(text: str) -> ScopeResult | None:
    """从模型回复提取 in_scope；解析失败返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None

    candidates: list[str] = []
    if raw.startswith("{"):
        candidates.append(raw)
    candidates.extend(m.group(0) for m in _JSON_OBJECT_RE.finditer(raw))

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        in_scope = obj.get("in_scope")
        if not isinstance(in_scope, bool):
            continue
        reason = obj.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)
        return ScopeResult(in_scope=in_scope, reason=reason)
    return None


def classify(message: str) -> ScopeResult:
    """调用固定模型二分类；异常时 fail-closed（in_scope=false）。"""
    if not (message or "").strip():
        return ScopeResult(in_scope=False, reason="empty_message")

    if CLASSIFIER_MODEL not in config.MODEL_CONFIGS:
        logger.warning("[learning_scope] 分类模型 %s 未配置", CLASSIFIER_MODEL)
        return ScopeResult(in_scope=False, reason="model_unavailable")

    try:
        from src.llm.provider import chat

        with config.use_llm_prefs(CLASSIFIER_MODEL, False, 0):
            resp = chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": (message or "")[:2000]},
                ],
                temperature=0.0,
            )
        text = (resp.choices[0].message.content or "").strip()
        parsed = parse_classifier_response(text)
        if parsed is None:
            logger.warning("[learning_scope] JSON 解析失败: %r", text[:200])
            return ScopeResult(in_scope=False, reason="parse_error")
        return parsed
    except Exception:
        logger.warning("[learning_scope] 分类调用失败", exc_info=True)
        return ScopeResult(in_scope=False, reason="api_error")
