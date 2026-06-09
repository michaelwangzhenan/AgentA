"""
CitationBuilder —— RAG 引用展示编排

职责：
- 在 RAG `search_knowledge` tool 调用时，把 Retriever 命中的 `Hit` 列表按
  (source, heading_path) 合并去重，并分配跨 tool_call 累计的全局编号 `[n]`。
- LLM 生成 final_answer 后，扫出正文实际出现的 `[n]` / `【n】` 编号（支持中英
  文方括号、嵌套 `[1][2]`、`[1,2]` 等多种写法），仅保留 builder 已分配过的
  编号（防止 LLM 幻觉超出范围的引用）。
- 把这些命中编号渲染成附加在 answer 末尾的 `— sources —` 块。

设计约定：
- 每轮 `Agent.run()` 实例化一次；不跨轮持有状态，编号从 `[1]` 起。
- 跨同一轮内的多次 `search_knowledge` tool_call，编号**连续累计**（第一次
  tool_call 分到 [1][2]，第二次接着 [3][4]）。
- 同 `(source, heading_path)` 视为同一引用条目；多 chunk 合并为一条，`chunks=N`
  在渲染时附注。
- 用户写 rules.md 关掉引用（让 LLM 不写 `[n]`）时，本类不会产生任何 sources
  块输出 —— 符合用户主权约定。

不做：
- 不校验 LLM 是否真的引到了相关条目（程序后置生成 + 编号只从 builder 来，
  结构上规避假引用）。
- 不跨轮累计编号（每轮独立从 [1] 起最直观）。
- 不为 page_no 做范围合并（合并条目时取首个 hit 的 page_no，简单稳定）。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from src.rag.retriever import Hit


# 正文里识别引用编号的正则；同时支持英文 `[n]` 与中文【n】方括号。
# 复合写法 `[1,2]` 也会被 `findall` 抓两次（每次一个数字），靠 set 去重。
_CITATION_RE = re.compile(r"[\[【](\d+)[\]】]")

# 渲染 sources 块时的标题分隔条
_SOURCES_HEADER = "— sources —"


@dataclass
class Citation:
    """一条引用条目（按 source+heading 合并后的逻辑单位）。

    Attributes:
        num:         分配的全局编号（[n] 的 n）。
        source:      文件相对路径（如 `src/rag/retriever.py`）。
        heading:     标题路径（如 `## 2.1.3 检索融合`）；None 表示无 heading。
        page_no:     页号；None 表示 markdown 等无页号来源。
        chunk_count: 该 (source, heading) 下合并了几个 chunk。
    """

    num: int
    source: str
    heading: str | None
    page_no: int | None
    chunk_count: int = 1


class CitationBuilder:
    """RAG 引用编排器：注册 → 提取 → 渲染。"""

    def __init__(self) -> None:
        # 已分配的引用条目，按 num 升序对应
        self._citations: list[Citation] = []
        # (source, heading) → num，用于合并去重
        self._key_to_num: dict[tuple[str, str], int] = {}
        # 下一个可分配的编号（[n] 的 n）；从 1 起
        self._next_num: int = 1
        # 同一轮多个 search_knowledge 可并行执行（tool_call_engine 并行路径），
        # register 会改 _citations / _next_num，故加锁保证编号分配不竞争。
        self._lock = threading.Lock()

    # ── 注册 ──────────────────────────────────────────────────────────────

    def register(self, hits: list[Hit]) -> list[int]:
        """注册一批 hits，分配（或复用）编号。

        合并逻辑：同 `(source, heading_path)` 的多个 hit 共享同一编号，
        `chunk_count` 累加，但 `page_no` **只取首次出现的值**（多 chunk
        通常落在相同或相邻页号，简单稳定胜于精细范围）。

        Args:
            hits: Retriever 返回的命中列表，可能为空。

        Returns:
            `list[int]`，长度等于 `hits`，第 i 项是 `hits[i]` 对应的编号。
            空列表入参 → 空列表返回。
        """
        nums: list[int] = []
        with self._lock:
            for hit in hits:
                heading = self._extract_heading(hit)
                key = (hit.source, heading or "")
                if key in self._key_to_num:
                    # 同 (source, heading) 复用编号，chunk_count 累加
                    num = self._key_to_num[key]
                    citation = next(c for c in self._citations if c.num == num)
                    citation.chunk_count += 1
                else:
                    num = self._next_num
                    self._next_num += 1
                    self._key_to_num[key] = num
                    self._citations.append(Citation(
                        num=num,
                        source=hit.source,
                        heading=heading,
                        page_no=self._extract_page_no(hit),
                    ))
                nums.append(num)
        return nums

    @staticmethod
    def _extract_heading(hit: Hit) -> str | None:
        """从 Hit.metadata 取 heading_path；缺失 / 空串 → None。"""
        meta = hit.metadata or {}
        h = meta.get("heading_path")
        if not h:
            return None
        return str(h).strip() or None

    @staticmethod
    def _extract_page_no(hit: Hit) -> int | None:
        """从 Hit.metadata 取 page_no，宽松转 int；非法值 → None。"""
        meta = hit.metadata or {}
        p = meta.get("page_no")
        if p is None or p == "":
            return None
        try:
            return int(p)
        except (TypeError, ValueError):
            return None

    # ── 提取 ──────────────────────────────────────────────────────────────

    def extract_used(self, text: str) -> list[int]:
        """从 LLM answer 正文里扫出实际出现的引用编号。

        - 同时支持英文 `[n]` 与中文【n】方括号；
        - 复合 `[1,2]` / `[1][2]` 都能被抓；
        - 只保留 builder 已分配过的编号（LLM 写了未分配的 `[7]` 静默丢弃，
          这是反幻觉的核心防线）；
        - 去重并按**首次出现顺序**返回，保证渲染时编号上下顺序与正文一致。

        Args:
            text: LLM 输出的最终回答正文（不含 sources 块）。

        Returns:
            已分配编号的去重有序列表；无引用时返回空列表。
        """
        if not text:
            return []
        valid_nums = {c.num for c in self._citations}
        seen: set[int] = set()
        ordered: list[int] = []
        for m in _CITATION_RE.finditer(text):
            num = int(m.group(1))
            if num in valid_nums and num not in seen:
                seen.add(num)
                ordered.append(num)
        return ordered

    # ── 渲染 ──────────────────────────────────────────────────────────────

    def render(self, used_nums: list[int]) -> str:
        """把命中的引用编号渲染为附加在 answer 末尾的 sources 块文本。

        - `used_nums` 为空 → 返回空串（不追加任何内容）；
        - 渲染顺序按编号升序，而非传入顺序（更易于人工 diff / 视觉扫描）；
        - 每条形如 `[n] source § heading  (p.N, chunks=K)`，缺省字段省略；
        - 输出前缀为 `\\n\\n— sources —\\n`，保证与正文之间有空行隔断。

        Args:
            used_nums: `extract_used()` 的返回值，或自行筛选过的子集。

        Returns:
            可直接 `answer + render(...)` 拼接的文本块；空时为空串。
        """
        if not used_nums:
            return ""
        wanted = set(used_nums)
        chosen = sorted(
            (c for c in self._citations if c.num in wanted),
            key=lambda c: c.num,
        )
        lines = [_SOURCES_HEADER]
        for c in chosen:
            lines.append(self._render_one(c))
        return "\n\n" + "\n".join(lines)

    @staticmethod
    def _render_one(c: Citation) -> str:
        """单条引用的展示行。"""
        head = f"§ {c.heading}" if c.heading else ""
        tail_bits: list[str] = []
        if c.page_no is not None:
            tail_bits.append(f"p.{c.page_no}")
        if c.chunk_count > 1:
            tail_bits.append(f"chunks={c.chunk_count}")
        tail = f"  ({', '.join(tail_bits)})" if tail_bits else ""
        body = f"{c.source}"
        if head:
            body = f"{body} {head}"
        return f"[{c.num}] {body}{tail}"

    # ── 调试 / 内省 ────────────────────────────────────────────────────────

    @property
    def citations(self) -> list[Citation]:
        """已注册的所有引用条目（按分配顺序）；返回浅拷贝避免外部改动。"""
        return list(self._citations)

    def __len__(self) -> int:
        return len(self._citations)
