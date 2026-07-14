"""
ResearchEngine —— Deep Research（深度研究）四阶段编排

一次深度研究是一条四阶段流水线：
    ① 规划：把研究问题拆成 3~MAX_SUBQUESTIONS 个子问题
    ② 并行检索：每个子问题派一个独立上下文的受限子代理，并行查 KB + web
    ③ 反思：汇总发现，判断是否有缺口 / 矛盾，按需补查 ≤2 个子问题（最多 1 轮）
    ④ 综述：跨子代理结果去重、分章节，流式产出带 `[n]` 引用的 Markdown 报告

设计要点（详见 docs/iter_14_enh.md §3.2）：
- 独立路径：普通 chat 完全不走本引擎；普通 `Agent.run` 行为零改动。
- 受限子代理：引擎内精简 ReAct loop，只给 3 个检索 tool、独立 in-memory 上下文，
  **不读不写** SessionStore —— 研究中间过程不污染用户会话历史。
- 共享引用器：所有子代理共用一个 `CitationBuilder`（线程安全），KB + web 统一 `[n]`。
- 软失败：单子代理异常 / 全空 → 标记失败、记 note，不中断整体，报告里照常说明缺口。
- 进度可视化：发一组 `research_*` 事件给前端研究面板（不发 `plan_*`）。
- 收尾对齐 `Agent.run`：流式 token_chunk 推正文、`final_answer` 带聚合 usage，
  最终"用户问题 + 报告"写入 SessionStore（仅此一条，中间过程不落库）。
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import src.config as _cfg
from src.agent.core.agent_commons import TokenUsage
from src.agent.core.citation_builder import CitationBuilder
from src.agent.core.run_cancel import is_cancelled
from src.agent.core.event_bus import (
    ALL_EVENT_TYPES,
    EVENT_ERROR,
    EVENT_FINAL_ANSWER,
    EVENT_INFO,
    EVENT_RESEARCH_PLAN,
    EVENT_RESEARCH_REFLECT,
    EVENT_RESEARCH_STARTED,
    EVENT_RESEARCH_SUBAGENT_END,
    EVENT_RESEARCH_SUBAGENT_PROGRESS,
    EVENT_RESEARCH_SUBAGENT_START,
    EVENT_RESEARCH_SYNTHESIZING,
    EVENT_TOKEN_CHUNK,
    AgentEvent,
    EventBus,
)
from src.agent.tools import execute_tool, get_research_tools
from src.llm.provider import chat
from src.stores.session_store import SessionStore

logger = logging.getLogger(__name__)

# 从 JSON 文本里抠出第一个 {...} 或 [...]（LLM 偶尔在 JSON 外包裹解释文字）
_JSON_BLOCK_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)

# 子问题数量硬下限（裁剪区间 [_MIN_SUBQUESTIONS, MAX_SUBQUESTIONS]）
_MIN_SUBQUESTIONS: int = 3
# 反思补查最多派几个子问题
_MAX_FOLLOWUPS: int = 2

# tool 名 → 子代理进度 stage / label（面板按子代理分组显示）
_TOOL_STAGE: dict[str, tuple[str, str]] = {
    "search_knowledge": ("retrieving_kb", "检索知识库"),
    "web_search": ("searching_web", "联网搜索"),
    "fetch_url": ("reading_page", "读取网页"),
}
# 视为"采纳了一条来源"的检索 tool（status=ok 时计数）
_RETRIEVAL_TOOLS: frozenset[str] = frozenset(_TOOL_STAGE)

_PLAN_SYSTEM = """你是研究规划助手。把用户的研究问题拆成若干个彼此独立、可并行检索的子问题。
要求：
- 子问题数量 3~{max_q} 个，覆盖问题的关键侧面，彼此不重复。
- 每个子问题是一句可直接拿去检索的具体问题。
- 只输出严格 JSON：{{"subquestions": ["...", "..."]}}，不要 markdown、不要多余解释。"""

_SUBAGENT_SYSTEM = """你是研究子代理，负责就【一个】子问题查清事实。
你只有 3 个检索工具：search_knowledge（查私有知识库）、web_search（联网搜索）、fetch_url（抓取网页正文）。
工作方式：
- 先 search_knowledge 查知识库；不足再 web_search 找网页，并对关键链接 fetch_url 读正文。
- 多源交叉验证，去重，聚焦本子问题，不要发散到其它问题。
- 工具返回的来源都带 [n] 编号；你在小结里引用事实时务必标注对应 [n]。
查够后，直接输出该子问题的发现小结（纯文本，带 [n] 引用），不要再调用工具。"""

_SUBAGENT_FORCE_SUMMARY = (
    "检索轮次或来源已达上限。请立即基于以上已检索到的内容，就该子问题给出发现小结"
    "（纯文本，引用事实标注 [n]），不要再调用任何工具。"
)

_REFLECT_SYSTEM = """你是研究质检助手。给定研究问题与各子问题已收集的发现，判断信息是否足以写出可靠报告。
只输出严格 JSON：{{"sufficient": true/false, "gap": "缺口或矛盾的一句话说明", "followups": ["补查子问题", "..."]}}。
- sufficient=true 时 followups 给空数组。
- followups 最多 {max_f} 条，必须是能补上缺口的、新的具体子问题。
- 不要 markdown、不要多余解释。"""

_SYNTH_SYSTEM = """你是研究报告撰写助手。基于各子问题的发现，写一篇结构化的中文调研报告。
格式要求：
- 开头一段「摘要」概述核心结论。
- 中间按主题分章节（## 小标题）展开，跨子问题去重、综合，不要简单罗列。
- 正文引用事实处标注 [n]（n 为发现里给出的来源编号），不要自己编造编号。
- 不要在正文末尾手写"参考来源"列表（系统会自动追加）。
- 若某些子问题未能查到资料，在相应位置如实说明信息缺口。"""


def _bind_callback(bus: EventBus, callback: Callable[[AgentEvent], None] | None) -> None:
    """把统一事件回调绑定到 bus：为每种事件类型注册 wrapper，把 payload 包成 AgentEvent。

    与 `Agent._bind_callback` 同语义，复制一份避免依赖 Agent 重型模块。
    """
    bus.clear()
    if callback is None:
        return
    for evt_type in ALL_EVENT_TYPES:
        def _wrapper(payload: Any, _t: str = evt_type) -> None:
            callback(AgentEvent(type=_t, payload=payload))
        bus.subscribe(evt_type, _wrapper)


class _Usage:
    """跨线程累计 token 用量（子代理在线程池里跑，累加需加锁）。"""

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self._lock = threading.Lock()

    def add(self, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        with self._lock:
            self.prompt += int(getattr(u, "prompt_tokens", 0) or 0)
            self.completion += int(getattr(u, "completion_tokens", 0) or 0)

    def to_token_usage(self) -> TokenUsage | None:
        if not (self.prompt or self.completion):
            return None
        return TokenUsage(self.prompt, self.completion, self.prompt + self.completion)


class ResearchEngine:
    """Deep Research 四阶段编排引擎。

    一次 `run()` = 一个 chat 请求；内部用线程池派子代理（占用同一信号量名额）。
    依赖向下：llm.provider / tools / citation_builder / event_bus / session_store。
    """

    def __init__(self, session_store: SessionStore, user_id: int | None = None) -> None:
        self._session_store = session_store
        self._user_id = user_id
        # 整次研究的总来源计数（跨子代理线程共享，加锁）；run() 每次重置
        self._total_sources = 0
        self._sources_lock = threading.Lock()

    # ── 入口 ────────────────────────────────────────────────────────────────

    def run(
        self,
        query: str,
        *,
        session_id: str,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        """执行完整四阶段流水线，返回最终报告文本（含参考来源块）。"""
        bus = EventBus()
        _bind_callback(bus, event_callback)

        def _on_token(chunk: str) -> None:
            bus.publish(AgentEvent(type=EVENT_TOKEN_CHUNK, payload={"text": chunk}))

        usage = _Usage()
        citation_builder = CitationBuilder()
        self._total_sources = 0
        self._sources_lock = threading.Lock()

        # 用户问题先落库（首次自动建 session 并归属 user）
        self._session_store.append(
            session_id, {"role": "user", "content": query}, user_id=self._user_id,
        )
        bus.publish(AgentEvent(type=EVENT_INFO, payload={
            "message": "research.run.start", "session_id": session_id,
        }))
        bus.publish(AgentEvent(type=EVENT_RESEARCH_STARTED, payload={"query": query}))

        if (early := self._finish_if_cancelled(session_id, usage, bus)) is not None:
            return early

        # ① 规划
        subquestions = self._plan(query, usage)
        bus.publish(AgentEvent(type=EVENT_RESEARCH_PLAN, payload={
            "subquestions": [{"id": i, "text": q} for i, q in enumerate(subquestions)],
        }))

        if (early := self._finish_if_cancelled(session_id, usage, bus)) is not None:
            return early

        # ② 并行子代理检索
        results = self._run_subagents(subquestions, citation_builder, bus, usage, start_id=0)

        if (early := self._finish_if_cancelled(session_id, usage, bus)) is not None:
            return early

        # ③ 反思补查（可选，最多 1 轮）
        if _cfg.DEEP_RESEARCH_REFLECT_ENABLED:
            followups = self._reflect(query, results, bus, usage)
            if followups:
                extra = self._run_subagents(
                    followups, citation_builder, bus, usage, start_id=len(results),
                )
                results.extend(extra)

        if (early := self._finish_if_cancelled(session_id, usage, bus)) is not None:
            return early

        # ④ 综述成稿（流式）
        bus.publish(AgentEvent(type=EVENT_RESEARCH_SYNTHESIZING, payload={}))
        report = self._synthesize(query, results, usage, _on_token)

        return self._finalize(report, citation_builder, session_id, usage, bus)

    # ── ① 规划 ──────────────────────────────────────────────────────────────

    def _plan(self, query: str, usage: _Usage) -> list[str]:
        """一次 LLM 调用把研究问题拆成子问题；解析失败 / 越界则裁剪或降级单条。"""
        max_q = _cfg.DEEP_RESEARCH_MAX_SUBQUESTIONS
        try:
            resp = chat(
                [
                    {"role": "system", "content": _PLAN_SYSTEM.format(max_q=max_q)},
                    {"role": "user", "content": f"研究问题：{query}"},
                ],
                temperature=0.3,
            )
            usage.add(resp)
            raw = resp.choices[0].message.content or ""
            data = _parse_json(raw)
            subs = data.get("subquestions") if isinstance(data, dict) else None
            cleaned = [str(s).strip() for s in subs if str(s).strip()] if isinstance(subs, list) else []
        except Exception as exc:  # noqa: BLE001 — 规划失败软降级为原问题单条
            logger.warning("[ResearchEngine] 规划失败，降级为单子问题：%s", exc)
            cleaned = []

        if not cleaned:
            return [query]
        # 裁剪到 [_MIN_SUBQUESTIONS, max_q]：多砍尾、少则保留原样（不硬凑）
        if len(cleaned) > max_q:
            cleaned = cleaned[:max_q]
        return cleaned

    # ── ② 子代理并行检索 ──────────────────────────────────────────────────────

    def _run_subagents(
        self,
        questions: list[str],
        citation_builder: CitationBuilder,
        bus: EventBus,
        usage: _Usage,
        *,
        start_id: int,
    ) -> list[dict[str, Any]]:
        """线程池并行跑子代理；子线程 copy_context 传 user / llm_prefs / session 等 contextvar。"""
        if not questions:
            return []
        if is_cancelled():
            return []
        max_parallel = max(1, _cfg.DEEP_RESEARCH_MAX_PARALLEL_SUBAGENTS)
        tasks = list(enumerate(questions, start=start_id))

        def work(sub_id: int, question: str) -> dict[str, Any]:
            return self._run_subagent(sub_id, question, citation_builder, bus, usage)

        with ThreadPoolExecutor(max_workers=min(len(tasks), max_parallel)) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, work, sid, q)
                for sid, q in tasks
            ]
            return [f.result() for f in futures]

    def _run_subagent(
        self,
        sub_id: int,
        question: str,
        citation_builder: CitationBuilder,
        bus: EventBus,
        usage: _Usage,
    ) -> dict[str, Any]:
        """单子问题的受限 bounded ReAct：仅 3 检索 tool + 独立 in-memory 上下文，不写 DB。"""
        bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_START, payload={
            "sub_id": sub_id, "question": question,
        }))
        max_rounds = max(1, _cfg.DEEP_RESEARCH_SUBAGENT_MAX_ROUNDS)
        per_cap = max(1, _cfg.DEEP_RESEARCH_MAX_SOURCES_PER_SUBAGENT)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SUBAGENT_SYSTEM},
            {"role": "user", "content": f"子问题：{question}"},
        ]
        tools = get_research_tools()
        sources = 0

        try:
            for rnd in range(1, max_rounds + 1):
                if is_cancelled():
                    bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_END, payload={
                        "sub_id": sub_id, "status": "failed", "sources": sources,
                        "note": "客户端已断开",
                    }))
                    return {"sub_id": sub_id, "question": question, "status": "failed",
                            "findings": "", "sources": sources}
                # 来源达上限（本子代理或全局）→ 本轮不给 tools，逼出小结
                budget_left = sources < per_cap and not self._total_cap_reached()
                resp = chat(messages, tools=tools if budget_left else None)
                usage.add(resp)
                message = resp.choices[0].message

                if message.tool_calls and budget_left:
                    self._append_assistant(messages, message)
                    for tc in message.tool_calls:
                        sources += self._exec_subagent_tool(
                            tc, sub_id, sources, citation_builder, bus, messages,
                        )
                    continue

                findings = (message.content or "").strip()
                if findings:
                    bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_END, payload={
                        "sub_id": sub_id, "status": "ok", "sources": sources, "note": "",
                    }))
                    return {"sub_id": sub_id, "question": question, "status": "ok",
                            "findings": findings, "sources": sources}
                # 空内容且仍有预算：再逼一轮无 tools 小结
                if budget_left:
                    messages.append({"role": "user", "content": _SUBAGENT_FORCE_SUMMARY})

            # 轮次耗尽仍未产出 → 强制一次无 tools 小结
            messages.append({"role": "user", "content": _SUBAGENT_FORCE_SUMMARY})
            resp = chat(messages)
            usage.add(resp)
            findings = (resp.choices[0].message.content or "").strip()
            status = "ok" if findings else "failed"
            note = "" if findings else "未能产出有效发现"
            bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_END, payload={
                "sub_id": sub_id, "status": status, "sources": sources, "note": note,
            }))
            return {"sub_id": sub_id, "question": question, "status": status,
                    "findings": findings, "sources": sources}
        except Exception as exc:  # noqa: BLE001 — 单子代理失败软降级，不中断整体
            logger.warning("[ResearchEngine] 子代理 %d 失败：%s", sub_id, exc)
            bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_END, payload={
                "sub_id": sub_id, "status": "failed", "sources": sources, "note": str(exc),
            }))
            return {"sub_id": sub_id, "question": question, "status": "failed",
                    "findings": "", "sources": sources}

    def _exec_subagent_tool(
        self,
        tool_call: Any,
        sub_id: int,
        sources: int,
        citation_builder: CitationBuilder,
        bus: EventBus,
        messages: list[dict[str, Any]],
    ) -> int:
        """执行子代理的单个工具调用，回填 tool 结果到 messages，发进度事件；返回新增来源数。"""
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        stage, label = _TOOL_STAGE.get(name, ("retrieving", "检索中"))
        # 工具入参里的检索词 / URL，作为过程展示的细节（面板显示"联网搜索：xxx"）
        detail = str(args.get("query") or args.get("url") or "").strip()
        # action=True 让前端面板就本次工具调用新增一行过程，detail 为这次查的内容
        bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_PROGRESS, payload={
            "sub_id": sub_id, "stage": stage, "label": label,
            "detail": detail, "sources": sources, "action": True,
        }))

        result = execute_tool(
            name, args, citation_builder=citation_builder, cite_web=True,
        )
        messages.append({
            "role": "tool", "tool_call_id": tool_call.id, "content": result.to_llm_str(),
        })

        gained = 1 if (name in _RETRIEVAL_TOOLS and result.status == "ok") else 0
        if gained:
            with self._sources_lock:
                self._total_sources += 1
        # 工具结束：刷新来源计数 + 本次结果状态（action 省略，仅更新最近这行）
        bus.publish(AgentEvent(type=EVENT_RESEARCH_SUBAGENT_PROGRESS, payload={
            "sub_id": sub_id, "stage": stage, "label": label,
            "sources": sources + gained, "status": result.status,
        }))
        return gained

    def _total_cap_reached(self) -> bool:
        with self._sources_lock:
            return self._total_sources >= max(1, _cfg.DEEP_RESEARCH_MAX_TOTAL_SOURCES)

    # ── ③ 反思 ──────────────────────────────────────────────────────────────

    def _reflect(
        self,
        query: str,
        results: list[dict[str, Any]],
        bus: EventBus,
        usage: _Usage,
    ) -> list[str]:
        """评估发现是否充分；有缺口且未超总来源预算 → 返回 ≤2 个补查子问题。"""
        if self._total_cap_reached():
            return []
        digest = _findings_digest(results)
        try:
            resp = chat(
                [
                    {"role": "system", "content": _REFLECT_SYSTEM.format(max_f=_MAX_FOLLOWUPS)},
                    {"role": "user", "content": f"研究问题：{query}\n\n已有发现：\n{digest}"},
                ],
                temperature=0.2,
            )
            usage.add(resp)
            data = _parse_json(resp.choices[0].message.content or "")
        except Exception as exc:  # noqa: BLE001 — 反思失败视作"已充分"，照常综述
            logger.warning("[ResearchEngine] 反思失败，跳过补查：%s", exc)
            data = {}

        sufficient = bool(data.get("sufficient", True)) if isinstance(data, dict) else True
        gap = str(data.get("gap", "")).strip() if isinstance(data, dict) else ""
        raw_followups = data.get("followups") if isinstance(data, dict) else None
        followups: list[str] = []
        if not sufficient and isinstance(raw_followups, list):
            followups = [str(f).strip() for f in raw_followups if str(f).strip()][:_MAX_FOLLOWUPS]

        bus.publish(AgentEvent(type=EVENT_RESEARCH_REFLECT, payload={
            "note": "信息已充分" if sufficient or not followups else "发现缺口，补查中",
            "gap": gap,
            "followups": followups,
        }))
        return followups

    # ── ④ 综述 ──────────────────────────────────────────────────────────────

    def _synthesize(
        self,
        query: str,
        results: list[dict[str, Any]],
        usage: _Usage,
        on_token: Callable[[str], None],
    ) -> str:
        """一次流式 LLM 调用产出分章节报告；失败则降级为发现摘要兜底。"""
        digest = _findings_digest(results)
        try:
            resp = chat(
                [
                    {"role": "system", "content": _SYNTH_SYSTEM},
                    {"role": "user", "content": (
                        f"研究问题：{query}\n\n各子问题的发现（含来源 [n] 编号）：\n{digest}\n\n"
                        "请据此写出结构化报告。"
                    )},
                ],
                temperature=0.5,
                on_token_chunk=on_token,
            )
            usage.add(resp)
            report = (resp.choices[0].message.content or "").strip()
            if report:
                return report
        except Exception as exc:  # noqa: BLE001 — 综述失败兜底
            logger.warning("[ResearchEngine] 综述失败，返回发现摘要兜底：%s", exc)

        fallback = f"# 研究报告（综述失败兜底）\n\n研究问题：{query}\n\n以下为各子问题原始发现：\n\n{digest}"
        on_token(fallback)
        return fallback

    # ── 收尾 ────────────────────────────────────────────────────────────────

    def _finish_if_cancelled(
        self,
        session_id: str,
        usage: _Usage,
        bus: EventBus,
    ) -> str | None:
        if not is_cancelled():
            return None
        logger.info("[ResearchEngine] 客户端已断开，中止研究 session=%s", session_id)
        msg = "深度研究已中断。"
        self._session_store.append(
            session_id, {"role": "assistant", "content": msg}, user_id=self._user_id,
        )
        bus.publish(AgentEvent(type=EVENT_FINAL_ANSWER, payload={
            "text": msg,
            "usage": usage.to_token_usage(),
            "used_tools": True,
            "personalized": False,
            "client_disconnected": True,
        }))
        return msg

    def _finalize(
        self,
        report: str,
        citation_builder: CitationBuilder,
        session_id: str,
        usage: _Usage,
        bus: EventBus,
    ) -> str:
        """把报告里的 [n] 压缩成连续编号 + 追加 sources 块、落库、发 final_answer。

        正文在综述阶段已按"被检索顺序"的原始编号流式推给前端；这里重编号后由
        final_answer 携带完整文本，前端深度研究消息以 final_answer 文本为准覆盖显示，
        所以不再单独 stream sources 块（避免新旧编号在流式途中打架）。
        """
        report = report.strip()
        report, sources_block = citation_builder.renumber_and_render(report)
        report = report + sources_block

        self._session_store.append(
            session_id, {"role": "assistant", "content": report}, user_id=self._user_id,
        )
        bus.publish(AgentEvent(type=EVENT_FINAL_ANSWER, payload={
            "text": report,
            "usage": usage.to_token_usage(),
            # 研究永不可缓存、用了工具；不注入个性化（子代理纯净上下文）
            "used_tools": True,
            "personalized": False,
        }))
        return report

    # ── helper ──────────────────────────────────────────────────────────────

    @staticmethod
    def _append_assistant(messages: list[dict[str, Any]], message: Any) -> None:
        """把 LLM 的 assistant(tool_calls) 回填进 in-memory messages（含 reasoning_content 兼容）。"""
        from src.agent.core.tool_call_engine import ToolCallEngine
        assistant_msg = ToolCallEngine.assistant_message(message)
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            messages.append({**assistant_msg, "reasoning_content": reasoning})
        else:
            messages.append(assistant_msg)


def _parse_json(raw: str) -> Any:
    """宽松解析 LLM 输出里的 JSON（容忍前后包裹的解释文字）；失败返回 {}。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _findings_digest(results: list[dict[str, Any]]) -> str:
    """把各子问题发现拼成给反思 / 综述看的摘要文本（失败子问题如实标注）。"""
    blocks: list[str] = []
    for r in results:
        head = f"### 子问题：{r['question']}"
        if r["status"] == "ok" and r.get("findings"):
            blocks.append(f"{head}\n{r['findings']}")
        else:
            note = r.get("note") or "未查到有效资料"
            blocks.append(f"{head}\n（信息缺口：{note}）")
    return "\n\n".join(blocks) if blocks else "（无任何发现）"
