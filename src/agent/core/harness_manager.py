"""Harness 自检 manager

提供两路生产路径 critic：

1. `review_grading()` — 单题 quiz 批改自检；复用 [`judge_with_llm`](../../../tools/eval_common/llm_judge.py) helper
2. `filter_chunks()` — RAG 召回 chunks 相关性批量过滤；K 条一次 LLM 调用

所有 critic 调用都用 `ThreadPoolExecutor` 包 timeout（跨平台），超时静默降级保留原始输出。
任何异常软返回，不向上传播，不阻塞主流程（grade_quiz / search_knowledge）。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import src.config as config
from src.llm.provider import chat
from tools.eval_common import judge_with_llm

logger = logging.getLogger(__name__)

# critic prompt 文件目录：tools/agent_eval/harness/
_CRITIC_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "tools" / "agent_eval" / "harness"
_DEFAULT_QUIZ_CRITIC = _CRITIC_PROMPTS_DIR / "quiz_critic.txt"
_DEFAULT_RAG_CRITIC = _CRITIC_PROMPTS_DIR / "rag_critic.txt"

# 召回片段过长会撑爆 prompt context；单条截断到该字符数
_RAG_CHUNK_TRUNCATE_CHARS: int = 800


@dataclass(frozen=True)
class HarnessVerdict:
    """单条自检判决。

    Attributes:
        passed:  True=通过 / 放行；False=不达标，应 flag。
        score:   critic 给的分数（0-5）；critic 失败时为 None。
        reason:  ≤ 80 字简评 / 失败原因。
        raw:     critic 原始返回，便于排查。
        failure: True 表示 critic 自身调用失败（区分"判定通过"vs"没判出来"）；
                 调用方决定"failure 时是放行还是 flag"，本类只忠实表达事实。
    """
    passed: bool
    score: float | None
    reason: str
    raw: str
    failure: bool = False


def _load_prompt(path: Path) -> str:
    """加载 critic prompt 文件；缺失即 fail-fast。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as e:
        raise RuntimeError(
            f"critic prompt 文件缺失：{path}。\n"
            f"请检查 tools/agent_eval/harness/ 目录是否完整"
            f"（应含 quiz_critic.txt + rag_critic.txt）。"
        ) from e
    except OSError as e:
        raise RuntimeError(f"critic prompt 文件读取失败：{path}：{e}") from e


# RAG 批量评判 system prompt：动态填入 criteria + K
_RAG_BATCH_SYS_TEMPLATE = """你是检索结果质量评委。给定用户问题和编号为 1..K 的 K 条候选文档片段，逐条判断该片段是否与问题相关。

{criteria}

输出**严格的 JSON**，格式：

{{"verdicts": [{{"i": 1, "score": <0 或 5>}}, {{"i": 2, "score": <0 或 5>}}, ...]}}

只输出 JSON 一段，verdicts 数组长度必须等于 K（{k}）。不要 markdown 代码块、不要前后说明。"""

_RAG_BATCH_USER_TEMPLATE = """## 用户问题
{query}

## 候选片段（K={k}）
{chunks}"""


class HarnessManager:
    """Harness critic 管理器。

    构造一次后跨整个进程复用；线程安全（内部 ThreadPoolExecutor 自带锁）。
    """

    def __init__(
        self,
        *,
        threshold: float | None = None,
        timeout: float | None = None,
        quiz_critic_path: Path = _DEFAULT_QUIZ_CRITIC,
        rag_critic_path: Path = _DEFAULT_RAG_CRITIC,
    ) -> None:
        self.threshold = threshold if threshold is not None else config.HARNESS_GRADING_THRESHOLD
        self.timeout = timeout if timeout is not None else config.HARNESS_LLM_TIMEOUT_SEC
        self._quiz_criteria = _load_prompt(quiz_critic_path)
        self._rag_criteria = _load_prompt(rag_critic_path)
        # max_workers=2 足够 — Q1 / R1 不会并发，但留 buffer 防万一
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="harness"
        )
        logger.info(
            "[Harness] manager 初始化：threshold=%.2f, timeout=%.1fs",
            self.threshold, self.timeout,
        )

    # ── Q1 单题批改自检 ───────────────────────────────────────────────────────

    def review_grading(
        self,
        *,
        stem: str,
        user_answer: str,
        correct_answer: str,
        agent_score: float,
        agent_feedback: str,
    ) -> HarnessVerdict:
        """对单题批改结果做 critic 自检。

        passed=False & failure=False  → critic 判定批改不达标，调用方应 mark harness_flagged。
        passed=True  & failure=False  → critic 判定批改 OK，放行。
        passed=True  & failure=True   → critic 自身失败（超时 / 解析错），软放行（不 flag，避免误伤）。
        """
        prompt_text = (
            f"## 题目\n{stem}\n\n"
            f"## 标准答案\n{correct_answer}\n\n"
            f"## 用户答案\n{user_answer or '（未作答）'}"
        )
        output_text = (
            f"Agent 给的分数：{agent_score:.2f}/1.0\n"
            f"Agent 给的反馈：{agent_feedback or '（无）'}"
        )

        try:
            future = self._executor.submit(
                judge_with_llm,
                prompt=prompt_text,
                output=output_text,
                criteria=self._quiz_criteria,
                role_intro="你是测验批改质量评委",
            )
            res = future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("[Harness] quiz critic 超时 (%.1fs)，软放行", self.timeout)
            return HarnessVerdict(
                passed=True, score=None,
                reason=f"critic 超时 ({self.timeout}s)",
                raw="", failure=True,
            )
        except Exception as e:  # noqa: BLE001 — critic 异常一律软返回，不阻塞主流程
            logger.warning("[Harness] quiz critic 异常：%s", e)
            return HarnessVerdict(
                passed=True, score=None,
                reason=f"critic 异常：{e}",
                raw="", failure=True,
            )

        if not res.ok or res.score is None:
            logger.warning("[Harness] quiz critic 解析失败：%s", res.reason)
            return HarnessVerdict(
                passed=True, score=None,
                reason=res.reason or "critic 解析失败",
                raw=res.raw, failure=True,
            )

        passed = res.score >= self.threshold
        return HarnessVerdict(
            passed=passed, score=res.score, reason=res.reason,
            raw=res.raw, failure=False,
        )

    # ── R1 召回 chunks 相关性批量过滤 ─────────────────────────────────────────

    def filter_chunks(self, *, query: str, hits: list[Any]) -> list[Any]:
        """对召回的 K 条 chunks 做相关性自检；返回过滤后 hits（保持原顺序）。

        critic 失败 / 超时 → 软返回原始 hits 不过滤（避免空召回坑用户体验）。

        Args:
            query: 用户原始问题。
            hits:  retriever.search() 返回的 Hit 列表；每个对象需有 `.document` 属性
                   （或 str()-able），其它字段不消费。

        Returns:
            保留的 hits 子集（保持入参顺序）；critic 失败时返回原 hits。
        """
        if not hits:
            return hits
        if not isinstance(query, str) or not query.strip():
            return hits

        k = len(hits)
        chunks_text = "\n\n---\n\n".join(
            f"[{i + 1}] {self._truncate_doc(self._extract_doc(h))}"
            for i, h in enumerate(hits)
        )
        sys_prompt = _RAG_BATCH_SYS_TEMPLATE.format(criteria=self._rag_criteria, k=k)
        user_msg = _RAG_BATCH_USER_TEMPLATE.format(
            query=query.strip(), k=k, chunks=chunks_text,
        )

        try:
            future = self._executor.submit(
                self._call_chat_for_rag,
                sys_prompt=sys_prompt,
                user_msg=user_msg,
            )
            raw = future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "[Harness] RAG critic 超时 (%.1fs, K=%d)，软放行原始 %d 条",
                self.timeout, k, k,
            )
            return hits
        except Exception as e:  # noqa: BLE001 — critic 异常一律软返回
            logger.warning("[Harness] RAG critic 异常 (K=%d)：%s", k, e)
            return hits

        verdicts = self._parse_rag_verdicts(raw, k)
        if verdicts is None:
            logger.warning(
                "[Harness] RAG critic 解析失败 (K=%d)，软放行：%r",
                k, raw[:200],
            )
            return hits

        kept = [h for h, score in zip(hits, verdicts) if score >= 3.0]
        logger.info(
            "[Harness] RAG critic: %d → %d (过滤 %d 条 not_relevant)",
            k, len(kept), k - len(kept),
        )
        return kept

    # ── 内部 helper ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_doc(hit: Any) -> str:
        """兼容 Hit dataclass / dict / 纯字符串三种输入。"""
        doc = getattr(hit, "document", None)
        if doc is None and isinstance(hit, dict):
            doc = hit.get("document")
        if doc is None:
            return str(hit)
        return str(doc)

    @staticmethod
    def _truncate_doc(text: str, max_chars: int = _RAG_CHUNK_TRUNCATE_CHARS) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "…"

    @staticmethod
    def _call_chat_for_rag(*, sys_prompt: str, user_msg: str) -> str:
        """给 filter_chunks 用的 chat 调用（独立函数便于线程化）。"""
        resp = chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_msg}],
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def _parse_rag_verdicts(raw: str, k: int) -> list[float] | None:
        """解析 RAG critic 返回，返回长度 K 的 score 列表（0 或 5）；失败 None。

        容错：score 不是 0/5 时按 ≥ 2.5 归 5、否则归 0；数组长度对不上直接判失败。
        """
        m = re.search(r"\{.*\}", raw.replace("\n", " "), re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            verdicts = data.get("verdicts")
            if not isinstance(verdicts, list) or len(verdicts) != k:
                return None
            scores: list[float] = []
            for v in verdicts:
                if not isinstance(v, dict):
                    return None
                s = float(v.get("score", -1))
                if s not in (0.0, 5.0):
                    s = 5.0 if s >= 2.5 else 0.0
                scores.append(s)
            return scores
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


# ── 进程级单例 ────────────────────────────────────────────────────────────────

_singleton: HarnessManager | None = None
_singleton_lock = threading.Lock()


def get_harness_manager() -> HarnessManager:
    """进程级单例；首次调用懒加载（读 critic prompt 文件）。"""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = HarnessManager()
    return _singleton


def reset_for_test() -> None:
    """UT 用：清空单例（让下次 get_harness_manager 重新读 prompt 文件）。"""
    global _singleton
    with _singleton_lock:
        _singleton = None
