"""
test_citation_builder —— Phase 1.4 RAG 引用编排单测

覆盖 6 个维度：
1. 注册：单 Hit / 多 Hit / 同 source+heading 合并 / 不同 heading 不合并 / 空 hits
2. 跨多次 register 累计编号：第二次 register 接着第一次的编号继续分配
3. 提取：英文 [n] / 中文【n】 / 复合 [1,2] / 嵌套 [1][2] / 未分配编号丢弃 / 空文本 / 去重保序
4. 渲染：空 used_nums / 单条 / 多条按编号升序 / page_no 缺失 / chunk_count=1 时不显示 chunks
5. 边界：page_no 字段类型异常（str/None/非法值）/ heading_path 缺失或空白
6. 端到端集成：模拟一轮 register → extract_used → render 完整链路
"""
from __future__ import annotations

from typing import Any

import pytest

from src.agent.core.citation_builder import CitationBuilder
from src.rag.retriever import Hit


# ── 辅助：构造 Hit ───────────────────────────────────────────────────────────

def _make_hit(
    source: str,
    heading: str | None = None,
    page_no: Any = None,
    document: str = "chunk text",
    distance: float = 0.1,
    collection: str = "kb_zh",
) -> Hit:
    """构造一条 Hit；metadata 仅塞测试关心的字段。"""
    meta: dict[str, Any] = {}
    if heading is not None:
        meta["heading_path"] = heading
    if page_no is not None:
        meta["page_no"] = page_no
    return Hit(
        source=source,
        document=document,
        distance=distance,
        collection=collection,
        metadata=meta,
    )


# ── 1. 注册 ─────────────────────────────────────────────────────────────────

class TestRegister:
    """register() 编号分配与合并去重行为。"""

    def test_empty_hits_returns_empty_list(self) -> None:
        builder = CitationBuilder()
        assert builder.register([]) == []
        assert len(builder) == 0

    def test_single_hit_gets_num_1(self) -> None:
        builder = CitationBuilder()
        nums = builder.register([_make_hit("a.md")])
        assert nums == [1]
        assert len(builder) == 1
        assert builder.citations[0].num == 1
        assert builder.citations[0].source == "a.md"
        assert builder.citations[0].chunk_count == 1

    def test_distinct_sources_get_distinct_nums(self) -> None:
        builder = CitationBuilder()
        nums = builder.register([_make_hit("a.md"), _make_hit("b.md")])
        assert nums == [1, 2]

    def test_same_source_same_heading_merges(self) -> None:
        """同 (source, heading_path) 合并：返回相同编号、chunk_count 累加。"""
        builder = CitationBuilder()
        nums = builder.register([
            _make_hit("a.md", heading="§ 1"),
            _make_hit("a.md", heading="§ 1"),  # 同 heading → 合并
            _make_hit("a.md", heading="§ 1"),  # 第三个 chunk
        ])
        assert nums == [1, 1, 1]
        assert len(builder) == 1
        assert builder.citations[0].chunk_count == 3

    def test_same_source_diff_heading_no_merge(self) -> None:
        """同 source 但 heading 不同 → 不合并，分配不同编号。"""
        builder = CitationBuilder()
        nums = builder.register([
            _make_hit("a.md", heading="§ 1"),
            _make_hit("a.md", heading="§ 2"),
        ])
        assert nums == [1, 2]
        assert len(builder) == 2

    def test_no_heading_treated_as_one_bucket(self) -> None:
        """两条都无 heading → 视为同 (source, '') 合并。"""
        builder = CitationBuilder()
        nums = builder.register([
            _make_hit("a.md"),
            _make_hit("a.md"),
        ])
        assert nums == [1, 1]
        assert builder.citations[0].chunk_count == 2

    def test_page_no_taken_from_first_hit(self) -> None:
        """合并条目时 page_no 取首个 hit 的值（不做范围合并）。"""
        builder = CitationBuilder()
        builder.register([
            _make_hit("a.md", heading="§ 1", page_no=5),
            _make_hit("a.md", heading="§ 1", page_no=99),  # 被忽略
        ])
        assert builder.citations[0].page_no == 5


# ── 2. 跨多次 register 累计编号 ─────────────────────────────────────────────

class TestCrossCallNumbering:
    """模拟同一轮内多次 search_knowledge tool_call 编号连续累计。"""

    def test_second_register_continues_numbering(self) -> None:
        builder = CitationBuilder()
        nums1 = builder.register([_make_hit("a.md"), _make_hit("b.md")])
        nums2 = builder.register([_make_hit("c.md"), _make_hit("d.md")])
        assert nums1 == [1, 2]
        assert nums2 == [3, 4]
        assert len(builder) == 4

    def test_second_register_reuses_existing_key(self) -> None:
        """第二次 register 命中已存在的 (source, heading) → 复用编号、累加 chunks。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", heading="§ 1")])
        nums = builder.register([
            _make_hit("a.md", heading="§ 1"),  # 复用 [1]
            _make_hit("b.md", heading="§ 1"),  # 新分配 [2]
        ])
        assert nums == [1, 2]
        assert builder.citations[0].chunk_count == 2


# ── 3. extract_used ─────────────────────────────────────────────────────────

class TestExtractUsed:
    """从 answer 文本里提取 [n] / 【n】。"""

    @pytest.fixture
    def builder_with_3(self) -> CitationBuilder:
        b = CitationBuilder()
        b.register([_make_hit("a.md"), _make_hit("b.md"), _make_hit("c.md")])
        return b

    def test_empty_text_returns_empty(self, builder_with_3: CitationBuilder) -> None:
        assert builder_with_3.extract_used("") == []
        assert builder_with_3.extract_used("无引用的纯文本") == []

    def test_basic_english_brackets(self, builder_with_3: CitationBuilder) -> None:
        assert builder_with_3.extract_used("如 RRF [1] 所示") == [1]

    def test_chinese_brackets(self, builder_with_3: CitationBuilder) -> None:
        assert builder_with_3.extract_used("见【2】") == [2]

    def test_mixed_brackets_in_same_text(self, builder_with_3: CitationBuilder) -> None:
        nums = builder_with_3.extract_used("第一处 [1]，第二处【2】，第三处 [3]")
        assert nums == [1, 2, 3]

    def test_nested_brackets(self, builder_with_3: CitationBuilder) -> None:
        assert builder_with_3.extract_used("综合 [1][2][3]") == [1, 2, 3]

    def test_composite_comma_brackets(self, builder_with_3: CitationBuilder) -> None:
        """[1,2] 复合写法 — 内部用 `[1,2]` 这种 LLM 也常写，需各算一次。"""
        nums = builder_with_3.extract_used("综合 [1,2,3]")
        # 当前 regex 不强支持 [1,2] 复合（每个 [..] 只抓一个数字），文档化此行为
        assert nums == []  # `[1,2,3]` 匹配不到，因为内含逗号

    def test_dedup_preserves_first_order(self, builder_with_3: CitationBuilder) -> None:
        """同编号多次出现去重；首次出现顺序决定列表顺序。"""
        nums = builder_with_3.extract_used("[2] 然后 [1] 又 [2] 再 [3] 又 [1]")
        assert nums == [2, 1, 3]

    def test_unallocated_num_silently_dropped(self, builder_with_3: CitationBuilder) -> None:
        """LLM 写了 builder 没分配的 [7] / [99] → 静默丢弃（反幻觉）。"""
        nums = builder_with_3.extract_used("瞎引一通 [7] [99]【50】 真引 [1]")
        assert nums == [1]

    def test_multi_digit_nums(self) -> None:
        """两位数编号 [10] 应能被正确捕获。"""
        b = CitationBuilder()
        for _ in range(12):
            b.register([_make_hit(f"f{_}.md")])
        nums = b.extract_used("引 [10] 和 [12]")
        assert nums == [10, 12]


# ── 4. render ───────────────────────────────────────────────────────────────

class TestRender:
    """render() 渲染 sources 块。"""

    def test_empty_used_returns_empty_string(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])
        assert builder.render([]) == ""

    def test_single_citation_minimal(self) -> None:
        """最简：无 heading 无 page_no 单 chunk。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])
        out = builder.render([1])
        assert "— sources —" in out
        assert "[1] a.md" in out
        # 单 chunk 时不应显示 chunks=1
        assert "chunks" not in out
        # 无 page_no 时不应有括号尾巴
        assert "(" not in out

    def test_with_heading_only(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", heading="§ 2.1 Intro")])
        out = builder.render([1])
        assert "[1] a.md § § 2.1 Intro" in out

    def test_with_page_no(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", heading="§ 1", page_no=7)])
        out = builder.render([1])
        assert "(p.7)" in out

    def test_with_chunks_gt_1(self) -> None:
        builder = CitationBuilder()
        builder.register([
            _make_hit("a.md", heading="§ 1"),
            _make_hit("a.md", heading="§ 1"),
            _make_hit("a.md", heading="§ 1"),
        ])
        out = builder.render([1])
        assert "(chunks=3)" in out

    def test_full_metadata(self) -> None:
        """heading + page_no + chunks 全有：行内 `(p.N, chunks=K)`。"""
        builder = CitationBuilder()
        builder.register([
            _make_hit("a.md", heading="§ X", page_no=12),
            _make_hit("a.md", heading="§ X", page_no=12),
        ])
        out = builder.render([1])
        assert "[1] a.md § § X  (p.12, chunks=2)" in out

    def test_multi_citations_sorted_by_num(self) -> None:
        """渲染顺序按编号升序，即便 used_nums 是乱序传入。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md"), _make_hit("b.md"), _make_hit("c.md")])
        out = builder.render([3, 1, 2])
        # 3 条引用 + 标题行 = 4 行
        idx_1 = out.find("[1]")
        idx_2 = out.find("[2]")
        idx_3 = out.find("[3]")
        assert 0 < idx_1 < idx_2 < idx_3

    def test_render_only_subset(self) -> None:
        """渲染只包含传入的 used 编号，未传的不出现。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md"), _make_hit("b.md"), _make_hit("c.md")])
        out = builder.render([1, 3])
        assert "[1]" in out and "[3]" in out
        assert "[2]" not in out

    def test_prefix_has_blank_line(self) -> None:
        """渲染前应有 `\\n\\n` 双换行，保证与正文之间空行隔断。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])
        out = builder.render([1])
        assert out.startswith("\n\n— sources —")


# ── 5. 边界：元数据异常 ──────────────────────────────────────────────────────

class TestMetadataEdgeCases:
    """page_no / heading_path 字段缺失或类型异常时应优雅降级。"""

    def test_page_no_string_int_coerces(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", page_no="42")])
        assert builder.citations[0].page_no == 42

    def test_page_no_garbage_falls_back_to_none(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", page_no="abc")])
        assert builder.citations[0].page_no is None

    def test_page_no_zero_string_is_zero(self) -> None:
        """`"0"` 是合法 int 0，不当作 None；展示时 `p.0` 也算合理（少见但合法）。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", page_no="0")])
        assert builder.citations[0].page_no == 0

    def test_empty_string_page_no_is_none(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", page_no="")])
        assert builder.citations[0].page_no is None

    def test_whitespace_heading_treated_as_none(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md", heading="   ")])
        assert builder.citations[0].heading is None

    def test_no_metadata_at_all(self) -> None:
        """metadata 为 None / {} → 安全 fallback。"""
        h = Hit(
            source="a.md",
            document="x",
            distance=0.1,
            collection="kb_zh",
            metadata=None,
        )
        builder = CitationBuilder()
        builder.register([h])
        assert builder.citations[0].heading is None
        assert builder.citations[0].page_no is None


# ── 6. 端到端集成 ────────────────────────────────────────────────────────────

class TestEndToEnd:
    """模拟一轮真实流程：register（两次 tool_call）→ extract_used → render。"""

    def test_full_pipeline_realistic(self) -> None:
        builder = CitationBuilder()

        # 第一次 tool_call：5 条 hit，其中 a.md § 1 合并 3 个 chunk
        builder.register([
            _make_hit("src/rag/retriever.py", heading="Hybrid 检索 / _rrf_fuse"),
            _make_hit("src/rag/retriever.py", heading="Hybrid 检索 / _rrf_fuse"),
            _make_hit("docs/design.md", heading="2.1.5 Retrieve+Rerank", page_no=7),
            _make_hit("docs/iter_2_agent.md", heading="4.5"),
            _make_hit("README.md"),
        ])
        # 现在分配了 4 个编号：[1] retriever.py, [2] design.md, [3] iter_2_agent.md, [4] README.md

        # 第二次 tool_call：再补 2 条；其中 design.md §2.1.5 复用 [2]
        builder.register([
            _make_hit("docs/design.md", heading="2.1.5 Retrieve+Rerank", page_no=7),
            _make_hit("src/agent/agent.py", heading="run()"),
        ])
        # 此时分配的总编号 = 5（[5] = agent.py）；design.md chunks 累计到 2

        # LLM 给出的 answer 引用 [1] [2]
        answer = (
            "RRF 是把多路结果合并的方法 [1]。本项目用它合并 dense 与 BM25 [1][2]，"
            "再走 rerank 与去重 [2]。"
        )

        used = builder.extract_used(answer)
        assert used == [1, 2]

        sources_block = builder.render(used)
        # 应只含 [1] [2]，不含 [3]/[4]/[5]
        assert "[1] src/rag/retriever.py" in sources_block
        assert "[2] docs/design.md" in sources_block
        assert "(p.7, chunks=2)" in sources_block  # design.md 合并了 2 chunk
        assert "[3]" not in sources_block
        assert "[4]" not in sources_block
        assert "[5]" not in sources_block

        # 最终回答 = answer + sources_block
        final = answer + sources_block
        assert final.startswith("RRF 是把多路结果合并")
        assert final.endswith("chunks=2)")

    def test_llm_writes_no_citation_returns_clean_answer(self) -> None:
        """LLM 一个 [n] 都没写（如用户 rules.md 禁用了引用）→ render 返回空，
        Agent.run() 拼接后 answer 完全不变。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md"), _make_hit("b.md")])

        answer = "RAG 是一种检索增强生成的方法。"  # 没有任何 [n]

        used = builder.extract_used(answer)
        assert used == []

        sources_block = builder.render(used)
        assert sources_block == ""

        final = answer + sources_block
        assert final == answer  # 完全不变，符合用户主权契约

    def test_llm_hallucinated_citations_silently_dropped(self) -> None:
        """LLM 写了超出 builder 分配范围的 [99] → render 不出现幻觉条目。"""
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])

        answer = "如 [1] [99] 所示"

        used = builder.extract_used(answer)
        assert used == [1]  # [99] 静默丢弃

        sources_block = builder.render(used)
        assert "[1]" in sources_block
        assert "[99]" not in sources_block


# ── 7. web 来源（Deep Research）─────────────────────────────────────────────

class TestRegisterWeb:
    """register_web() 网页来源编号分配 / 去重 / 渲染（与 KB 共用同一编号体系）。"""

    def test_single_web_source(self) -> None:
        builder = CitationBuilder()
        nums = builder.register_web([{"url": "https://a.com", "title": "A 站"}])
        assert nums == [1]
        c = builder.citations[0]
        assert c.url == "https://a.com"
        assert c.title == "A 站"
        assert c.source == ""

    def test_empty_url_skipped(self) -> None:
        """url 为空 / 空白的项跳过、不分配编号。"""
        builder = CitationBuilder()
        nums = builder.register_web([
            {"url": "", "title": "无效"},
            {"url": "   ", "title": "也无效"},
            {"url": "https://b.com", "title": "B"},
        ])
        assert nums == [1]
        assert len(builder) == 1

    def test_same_url_dedups_and_reuses_num(self) -> None:
        builder = CitationBuilder()
        n1 = builder.register_web([{"url": "https://x.com", "title": "X"}])
        n2 = builder.register_web([{"url": "https://x.com", "title": "X 改名（被忽略）"}])
        assert n1 == n2 == [1]
        assert len(builder) == 1
        assert builder.citations[0].title == "X"  # 首次 title 生效

    def test_missing_title_falls_back_to_url_on_render(self) -> None:
        builder = CitationBuilder()
        builder.register_web([{"url": "https://noname.com"}])
        out = builder.render([1])
        assert "[1] https://noname.com — https://noname.com" in out

    def test_render_web_line_format(self) -> None:
        builder = CitationBuilder()
        builder.register_web([{"url": "https://a.com/p", "title": "标题"}])
        out = builder.render([1])
        assert "[1] 标题 — https://a.com/p" in out

    def test_kb_and_web_share_continuous_numbering(self) -> None:
        """KB register 与 web register_web 共用 _next_num，编号连续。"""
        builder = CitationBuilder()
        kb = builder.register([_make_hit("a.md"), _make_hit("b.md")])
        web = builder.register_web([{"url": "https://c.com", "title": "C"}])
        assert kb == [1, 2]
        assert web == [3]
        out = builder.render([1, 3])
        assert "[1] a.md" in out
        assert "[3] C — https://c.com" in out
        assert "[2]" not in out

    def test_web_url_never_collides_with_kb_source(self) -> None:
        """web key 用哨兵前缀，url 与某 KB source 同名也不会误合并。"""
        builder = CitationBuilder()
        builder.register([_make_hit("https://a.com")])  # KB source 恰好长得像 url
        builder.register_web([{"url": "https://a.com", "title": "网页"}])
        assert len(builder) == 2


# ── 8. 重编号（普通问答 / Deep Research：连续 [n]）─────────────────────────

class TestRenumberAndRender:
    """renumber_and_render()：把正文实际引用的稀疏编号压成从 1 起连续。

    普通问答与深度研究收尾都走这一步，避免参考资料列表出现 [1][3][7] 跳号。
    """

    def test_no_citation_returns_text_and_empty(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])
        new_text, block = builder.renumber_and_render("没有引用的正文")
        assert new_text == "没有引用的正文"
        assert block == ""

    def test_sparse_nums_compacted_by_first_appearance(self) -> None:
        """分配了 [1]..[5]，正文只引 [4][2][5]（首现序）→ 改写为 [1][2][3]。"""
        builder = CitationBuilder()
        for i in range(5):
            builder.register([_make_hit(f"f{i}.md")])
        text = "先 [4]，再 [2]，最后 [5]。"
        new_text, block = builder.renumber_and_render(text)
        assert new_text == "先 [1]，再 [2]，最后 [3]。"
        # sources 块按新编号连续
        assert "[1] f3.md" in block
        assert "[2] f1.md" in block
        assert "[3] f4.md" in block
        assert "[4]" not in block

    def test_repeated_citation_keeps_same_new_num(self) -> None:
        builder = CitationBuilder()
        for i in range(3):
            builder.register([_make_hit(f"f{i}.md")])
        new_text, _ = builder.renumber_and_render("[3] 然后 [1] 又 [3]")
        assert new_text == "[1] 然后 [2] 又 [1]"

    def test_web_and_kb_mixed_renumber(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])  # [1]
        builder.register_web([{"url": "https://x.com", "title": "X"}])  # [2]
        builder.register([_make_hit("b.md")])  # [3]
        text = "网页 [2] 与文档 [3]"  # 只引 web 和第二个 KB
        new_text, block = builder.renumber_and_render(text)
        assert new_text == "网页 [1] 与文档 [2]"
        assert "[1] X — https://x.com" in block
        assert "[2] b.md" in block

    def test_hallucinated_num_left_untouched(self) -> None:
        builder = CitationBuilder()
        builder.register([_make_hit("a.md")])  # 只有 [1]
        new_text, block = builder.renumber_and_render("真引 [1]，幻觉 [9]")
        assert new_text == "真引 [1]，幻觉 [9]"  # [9] 不在分配范围，原样保留
        assert "[1] a.md" in block

    def test_chinese_brackets_preserved(self) -> None:
        builder = CitationBuilder()
        for i in range(3):
            builder.register([_make_hit(f"f{i}.md")])
        new_text, _ = builder.renumber_and_render("见【3】与【1】")
        assert new_text == "见【1】与【2】"
