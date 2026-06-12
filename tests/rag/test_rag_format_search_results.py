"""
测试：`format_search_results(hits)` —— RetrieverAPI 的 LLM-facing 格式化层

把"Hit 列表 → LLM 可读字符串"的格式契约固化下来。
这是 Agent core ↔ RAG 之间最薄但最容易被无意改坏的接口（任何对输出
schema 的改动会同时影响 prompt 与提取逻辑）。

覆盖：
- 空 hits → 引导文本（提示运行 ingest）
- 单 hit 含 `score`（精排后）→ 用 score 显示，4 位小数
- 单 hit 仅 `distance`（未精排）→ 用 (1 - distance) 显示
- 多 hits → 段间以 `\\n\\n---\\n\\n` 分隔
- `retrievers` 出现时含 "召回=dense+bm25" 形态
- metadata 的 `heading_path` / `page_no` 被纳入 loc_bits
- 关键字段（source / collection / document 内容）必现于输出
"""
from __future__ import annotations

from src.rag.retriever import Hit, format_search_results


# ── helpers ─────────────────────────────────────────────────────────────────

def _mk_hit(
    source: str = "docs/a.md",
    document: str = "chunk body",
    distance: float = 0.2,
    collection: str = "kb_m3",
    score: float | None = None,
    hid: str = "id1",
    retrievers: list[str] | None = None,
    metadata: dict | None = None,
) -> Hit:
    return Hit(
        source=source,
        document=document,
        distance=distance,
        collection=collection,
        score=score,
        id=hid,
        retrievers=retrievers or [],
        metadata=metadata,
    )


# ── 空 hits ─────────────────────────────────────────────────────────────────

class TestEmptyHits:

    def test_returns_guide_text(self) -> None:
        out = format_search_results([])
        assert "知识库为空" in out or "尚未初始化" in out
        # 引导用户运行 ingest
        assert "ingest" in out


# ── 分数显示：score vs distance ─────────────────────────────────────────────

class TestScoreFormatting:

    def test_score_takes_precedence_over_distance(self) -> None:
        """score 非 None 时，用 score 显示，distance 应被忽略。"""
        h = _mk_hit(distance=0.5, score=0.9876)
        out = format_search_results([h])
        assert "0.9876" in out
        # 不应错把 (1 - distance)=0.5 拿来显示
        assert "0.5000" not in out

    def test_distance_used_when_score_none(self) -> None:
        """score=None 时，用 round(1 - distance, 4) 显示。"""
        h = _mk_hit(distance=0.2, score=None)
        out = format_search_results([h])
        # 1 - 0.2 = 0.8
        assert "0.8" in out

    def test_score_formatted_to_four_decimals(self) -> None:
        """score 字段保留 4 位小数。"""
        h = _mk_hit(score=0.123456789)
        out = format_search_results([h])
        assert "0.1235" in out  # round-half-even 在第 4 位


# ── 多 hit 分隔与顺序 ───────────────────────────────────────────────────────

class TestMultipleHits:

    def test_segments_joined_by_separator(self) -> None:
        hits = [_mk_hit(source=f"f{i}.md", document=f"body-{i}") for i in range(3)]
        out = format_search_results(hits)
        assert out.count("\n\n---\n\n") == 2  # n-1 个分隔符

    def test_hits_preserve_input_order_with_index(self) -> None:
        """编号 [1] [2] [3] 与输入顺序一致，不重新排序。"""
        hits = [
            _mk_hit(source="A.md", document="first"),
            _mk_hit(source="B.md", document="second"),
            _mk_hit(source="C.md", document="third"),
        ]
        out = format_search_results(hits)
        # 顺序断言：A 在 B 前，B 在 C 前
        assert out.index("A.md") < out.index("B.md") < out.index("C.md")
        # 编号格式
        assert "[1]" in out and "[2]" in out and "[3]" in out


# ── retrievers / heading_path / page_no 附加位 ─────────────────────────────

class TestLocationBits:

    def test_retrievers_rendered(self) -> None:
        h = _mk_hit(retrievers=["dense", "bm25"])
        out = format_search_results([h])
        assert "召回=dense+bm25" in out

    def test_no_retrievers_no_recall_label(self) -> None:
        h = _mk_hit(retrievers=[])
        out = format_search_results([h])
        assert "召回=" not in out

    def test_heading_path_rendered(self) -> None:
        h = _mk_hit(metadata={"heading_path": "第1章 > 第2节"})
        out = format_search_results([h])
        assert "章节=第1章 > 第2节" in out

    def test_page_no_rendered(self) -> None:
        h = _mk_hit(metadata={"page_no": 42})
        out = format_search_results([h])
        assert "页=42" in out

    def test_metadata_none_does_not_crash(self) -> None:
        """metadata=None 时不应抛 AttributeError，只是不显示 heading/page。"""
        h = _mk_hit(metadata=None)
        out = format_search_results([h])
        assert "章节=" not in out
        assert "页=" not in out


# ── 关键字段全部出现 ────────────────────────────────────────────────────────

class TestCoreFieldsPresent:

    def test_source_and_collection_and_document_all_visible(self) -> None:
        h = _mk_hit(
            source="docs/resume.md",
            document="工作经验：5G 协议栈",
            collection="kb_zh",
            score=0.81,
        )
        out = format_search_results([h])
        assert "docs/resume.md" in out
        assert "kb_zh" in out
        assert "工作经验：5G 协议栈" in out
        assert "0.8100" in out
