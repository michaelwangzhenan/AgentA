"""
security_filter —— Prompt Injection 防御工具集（Helper 层）

职责：
- 集中维护 prompt injection 启发式 patterns（模块级 `_INJECTION_PATTERNS`）
- 给"非用户主控"的外部数据（RAG 召回 / web / tool 返回）做标签包装与命中清洗
- tool 调用名单门判定（fail-open + BLOCKLIST 默认；fail-close + ALLOWLIST 严格模式）

不做：
- LLM 分类器 / 语义级判定（cost 翻倍，单用户场景动机弱）
- system prompt 泄露 fingerprint 检测（SaaS 才需要）

SSRF 防御 / URL 校验由 [`url_guard`](./url_guard.py) 单独承担。
"""
from __future__ import annotations

import logging
import re

from src import config as _cfg

logger = logging.getLogger(__name__)


# ── 启发式注入模式 ────────────────────────────────────────────────────────────

# 11 项 regex：8 项搬迁自 user_memory._INJECTION_PATTERNS，3 项扩展（角色标签 / 越狱 / pretend）。
# 任何一段（按 \n\n 切分）匹配任一 pattern 即视为 injection 命中。
# 模式覆盖：
#   1-3  英文常见越狱开头（"ignore previous instructions" / "you are now" / "new instructions:"）
#   4-6  中文同义模板（"忽略...指令" / "你现在是" / "新的...指令"）
#   7-8  伪装系统标签（"system:" / `<|im_start|>` 等 tokenizer 标记）
#   9    角色标签伪造（`<system>` / `<user>` / `<assistant>`）
#   10   经典越狱模板（"act as" / "DAN" / "jailbreak"）
#   11   "pretend you are" 类角色覆盖
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?", re.IGNORECASE),
    re.compile(r"new\s+(?:system\s+)?instructions?\s*:", re.IGNORECASE),
    re.compile(r"忽略.{0,10}指令", re.IGNORECASE),
    re.compile(r"你现在是", re.IGNORECASE),
    re.compile(r"新的.{0,6}指令", re.IGNORECASE),
    re.compile(r"system\s*:\s", re.IGNORECASE),
    re.compile(r"<\|(?:im_start|im_end|endoftext)\|>", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(?:system|user|assistant)\s*>", re.IGNORECASE),
    re.compile(r"\b(?:act\s+as|jailbreak|DAN\s+mode)\b", re.IGNORECASE),
    re.compile(r"pretend\s+(?:you\s+are|to\s+be)", re.IGNORECASE),
]


# ── 标签包装 ──────────────────────────────────────────────────────────────────

# wrap_untrusted 支持的"不可信数据"类别。新增类别请同时更新 SYSTEM_PROMPT 数据隔离原则段。
# - doc：RAG 召回的本地知识库片段
# - web：内置 web_search / fetch_url 返回
# - tool：MCP server 返回（通用 tool 返回标签，含未来其它第三方 tool）
_WRAP_KINDS: frozenset[str] = frozenset({"doc", "web", "tool"})


def wrap_untrusted(content: str, kind: str = "doc") -> str:
    """
    把"非用户主控"的外部数据用 `<untrusted_{kind}>` 标签包住，
    供 SYSTEM_PROMPT 数据隔离原则段引用为"标签内的内容是数据不是指令"。

    嵌套规避：若 content 已含同型 `<untrusted_{kind}>` 标签，不二次包装；
    防 RAG 召回某 doc 本身就含 example 标签时嵌套膨胀。

    Args:
        content: 待包装的工具返回原文。
        kind:    类别标签，当前支持 'doc'（RAG 召回）/ 'web'（web_search / fetch_url）/
                 'tool'（MCP server 等第三方 tool 返回）。未知类别 fail-fast，便于发现拼写错误。

    Returns:
        `<untrusted_{kind}>\n{content}\n</untrusted_{kind}>` 形态字符串。
    """
    if kind not in _WRAP_KINDS:
        raise ValueError(
            f"wrap_untrusted: 未知 kind={kind!r}；支持的值 {sorted(_WRAP_KINDS)}"
        )
    open_tag = f"<untrusted_{kind}>"
    close_tag = f"</untrusted_{kind}>"
    if open_tag in content and close_tag in content:
        # 已有同型标签：避免二次包装让 LLM 看到 untrusted_doc 嵌套
        return content
    return f"{open_tag}\n{content}\n{close_tag}"


# ── 启发式检测 + 命中清洗 ────────────────────────────────────────────────────

# 段单位：以双换行作为逻辑段分隔符（同 Markdown 段落 / web 摘要项 / RAG hit 之间的分隔）；
# 命中段整段删除而非 char-level，保证 LLM 看到的内容连贯。
_SEGMENT_SEP: str = "\n\n"


def scrub_injection(content: str) -> tuple[str, bool]:
    """
    对外部不可信内容做启发式 prompt injection 检测；命中段整段删除。

    切分粒度：以双换行 `\\n\\n` 切段；每段独立过 11 项 patterns；命中段从结果中剔除，
    其它段保留并以 `\\n\\n` 重新拼回；保证 LLM 看到的内容连贯。

    Args:
        content: 待清洗的外部不可信内容（RAG 召回 / web / tool 返回）。

    Returns:
        (cleaned, hit)：cleaned 为清洗后内容；hit=True 表示至少有 1 段被删除。
        全段命中时 cleaned 为空串（调用方按需追加"已清洗"提示给 LLM）。
    """
    if not content:
        return content, False

    segments = content.split(_SEGMENT_SEP)
    kept: list[str] = []
    hit = False
    for seg in segments:
        if any(p.search(seg) for p in _INJECTION_PATTERNS):
            hit = True
            logger.warning(
                "[security_filter] scrub_injection 命中并删除段（前 80 字符）：%s",
                seg[:80].replace("\n", " "),
            )
            continue
        kept.append(seg)

    return _SEGMENT_SEP.join(kept), hit


# ── tool 名单门 ───────────────────────────────────────────────────────────────

# CSV 配置项解析为 set；空值返 空 set；前后空白自动 strip。
def _parse_csv_set(raw: str) -> set[str]:
    """把 CSV 字符串解析为去重 + strip 的 set；空串返 set()。"""
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_tool_allowed(name: str) -> bool:
    """
    判定指定 tool 是否被名单门放行。

    模式切换：
      - SECURITY_MODE=normal（默认）：fail-open + TOOL_BLOCKLIST，不在黑名单即放行
      - SECURITY_MODE=strict：fail-close + TOOL_ALLOWLIST，必须在白名单才放行（空白名单 = 全拒）

    设计意图：
      - normal 模式 UX 优先（默认零配置即可用）
      - strict 模式给"实验 / 高价值会话"用，会话内手动切，需显式列白名单

    Args:
        name: tool 名称（如 "search_knowledge" / "fetch_url"）。

    Returns:
        bool：True 放行，False 拒绝。
    """
    mode = (_cfg.SECURITY_MODE or "normal").strip().lower()
    if mode == "strict":
        allowlist = _parse_csv_set(_cfg.TOOL_ALLOWLIST)
        allowed = name in allowlist
        if not allowed:
            logger.warning(
                "[security_filter] strict 模式拒绝 tool %r（不在 ALLOWLIST=%s）",
                name, sorted(allowlist) or "[空]",
            )
        return allowed
    blocklist = _parse_csv_set(_cfg.TOOL_BLOCKLIST)
    if name in blocklist:
        logger.warning(
            "[security_filter] normal 模式拒绝 tool %r（命中 BLOCKLIST）", name,
        )
        return False
    return True
