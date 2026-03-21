"""
测试：RAG 向量化入库 & 检索

测试内容：
    - rag/ingest.py：chunk_text() 分块逻辑
    - config.py：resolve_embedding() 多模型解析
    - rag/reranker.py：rerank() 精排逻辑（单元测试，不依赖向量数据库）
    - rag/retriever.py：search() 检索返回格式（集成，需要已完成入库）
"""

import pytest
from unittest.mock import MagicMock, patch
import src.config as config
from src.rag.ingest import chunk_text
from src.rag.retriever import _Hit, search


class TestChunkText:
    """测试文本分块逻辑"""

    def test_empty_text_returns_empty_list(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_short_text_returns_single_chunk(self) -> None:
        result = chunk_text("短文本", size=600, overlap=100)
        assert len(result) == 1
        assert result[0] == "短文本"

    def test_long_text_is_split_into_multiple_chunks(self) -> None:
        text = "A" * 1500
        result = chunk_text(text, size=600, overlap=100)
        assert len(result) > 1

    def test_chunk_size_not_exceeded(self) -> None:
        text = "B" * 2000
        chunks = chunk_text(text, size=600, overlap=100)
        for chunk in chunks:
            assert len(chunk) <= 600

    def test_overlap_creates_shared_content(self) -> None:
        """相邻块应有重叠内容"""
        text = "X" * 700
        chunks = chunk_text(text, size=600, overlap=100)
        assert len(chunks) == 2
        assert chunks[0][-100:] == chunks[1][:100]

    def test_custom_size_and_overlap(self) -> None:
        text = "C" * 300
        chunks = chunk_text(text, size=100, overlap=20)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 100


class TestResolveEmbedding:
    """测试 config.resolve_embedding() 多模型解析"""

    def test_en_alias_resolves_to_minilm(self) -> None:
        model_name, collection = config.resolve_embedding("en")
        assert model_name == "all-MiniLM-L6-v2"
        assert collection == "kb_en"

    def test_zh_alias_resolves_to_bge(self) -> None:
        model_name, collection = config.resolve_embedding("zh")
        assert model_name == "BAAI/bge-small-zh"
        assert collection == "kb_zh"

    def test_custom_model_name_generates_collection(self) -> None:
        """直接传入模型名应自动生成 collection 名称"""
        model_name, collection = config.resolve_embedding("sentence-transformers/all-mpnet-base-v2")
        assert model_name == "sentence-transformers/all-mpnet-base-v2"
        assert collection.startswith("kb_")
        assert "mpnet" in collection

    def test_embedding_models_dict_has_en_and_zh(self) -> None:
        assert "en" in config.EMBEDDING_MODELS
        assert "zh" in config.EMBEDDING_MODELS

    def test_default_embedding_model_is_resolved(self) -> None:
        """DEFAULT_EMBEDDING_MODEL 和 DEFAULT_COLLECTION 应有值"""
        assert config.DEFAULT_EMBEDDING_MODEL
        assert config.DEFAULT_COLLECTION
        # 向后兼容别名
        assert config.EMBEDDING_MODEL == config.DEFAULT_EMBEDDING_MODEL
        assert config.CHROMA_COLLECTION == config.DEFAULT_COLLECTION


class TestSearch:
    """测试向量检索（需要已完成入库，标记为 integration）"""

    @pytest.mark.integration
    def test_search_returns_string(self) -> None:
        result = search("RAG 技术", top_k=3)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.integration
    def test_search_result_format(self) -> None:
        """检索结果应包含来源、相似度、库名称"""
        result = search("ChromaDB 向量数据库", top_k=2)
        assert "来源:" in result
        assert "相似度:" in result
        assert "库:" in result

    @pytest.mark.integration
    def test_search_top_k_limits_results(self) -> None:
        """返回的文档片段数量不应超过 top_k"""
        result = search("测试", top_k=2)
        # 每个结果以 [N] 开头，top_k=2 时最多有 1 个 "\n[" 分隔符
        count = result.count("\n[")
        assert count <= 1

    @pytest.mark.integration
    def test_search_irrelevant_query_still_returns_result(self) -> None:
        """即使问题不相关，也应返回最相近的结果（不返回空）"""
        result = search("火星上有生命吗", top_k=1)
        assert isinstance(result, str)
        assert len(result) > 0


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _make_hits(n: int) -> list[_Hit]:
    """构造 n 条虚拟 _Hit，source/document 带编号以便区分。"""
    return [
        _Hit(
            source=f"doc{i}.txt",
            document=f"这是第 {i} 条候选文档片段，内容与查询的相关性各不相同。",
            distance=0.1 * i,
            collection="kb_test",
        )
        for i in range(1, n + 1)
    ]


class TestReranker:
    """测试 src/rag/reranker.rerank() 精排逻辑（纯单元测试，不依赖向量数据库）"""

    def test_rerank_passthrough_when_candidates_le_top_k(self) -> None:
        """候选数量 ≤ top_k 时直接原样返回，不加载 CrossEncoder。"""
        from src.rag import reranker

        hits = _make_hits(3)
        with patch("src.rag.reranker._get_cross_encoder") as mock_ce:
            result = reranker.rerank(query="测试", hits=hits, top_k=5)
        mock_ce.assert_not_called()
        assert result is hits  # 直接返回同一对象（passthrough）

    def test_rerank_returns_top_k_count(self) -> None:
        """精排后返回的条数恰好等于 top_k。"""
        from src.rag import reranker

        hits = _make_hits(9)
        mock_model = MagicMock()
        mock_model.predict.return_value = [float(9 - i) for i in range(9)]

        with patch("src.rag.reranker._get_cross_encoder", return_value=mock_model):
            result = reranker.rerank(query="测试", hits=hits, top_k=3)

        assert len(result) == 3

    def test_rerank_results_are_sorted_by_score_descending(self) -> None:
        """精排结果应按 CrossEncoder 分数从高到低排列。"""
        from src.rag import reranker

        hits = _make_hits(5)
        # 第 3 条（index 2）得分最高，第 1 条（index 0）得分最低
        scores = [0.1, 0.3, 0.9, 0.5, 0.2]
        mock_model = MagicMock()
        mock_model.predict.return_value = scores

        with patch("src.rag.reranker._get_cross_encoder", return_value=mock_model):
            result = reranker.rerank(query="测试", hits=hits, top_k=3)

        # top-3 应按 score 降序：0.9 → 0.5 → 0.3，对应 hits[2] → hits[3] → hits[1]
        assert result[0].source == "doc3.txt"
        assert result[1].source == "doc4.txt"
        assert result[2].source == "doc2.txt"

    def test_rerank_passes_correct_pairs_to_model(self) -> None:
        """predict() 应收到 [(query, doc), ...] 形式的输入对。"""
        from src.rag import reranker

        hits = _make_hits(3)
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.4, 0.3]

        with patch("src.rag.reranker._get_cross_encoder", return_value=mock_model):
            reranker.rerank(query="关键词检索", hits=hits, top_k=2)

        call_args = mock_model.predict.call_args[0][0]
        assert len(call_args) == 3
        for pair, hit in zip(call_args, hits):
            assert pair == ("关键词检索", hit.document)

    def test_search_skips_reranker_when_disabled(self) -> None:
        """RERANKER_ENABLED=False 时，search() 不应调用 rerank()。"""
        mock_hit = _Hit(
            source="test.txt",
            document="测试内容",
            distance=0.2,
            collection="kb_test",
        )
        with (
            patch("src.rag.retriever.config.RERANKER_ENABLED", False),
            patch("src.rag.retriever._query_collection", return_value=[mock_hit]),
            patch("src.rag.retriever.chromadb.PersistentClient"),
            patch("src.rag.reranker.rerank") as mock_rerank,
        ):
            result = search("任意问题", top_k=1)

        mock_rerank.assert_not_called()
        assert "来源:" in result

    @pytest.mark.integration
    def test_reranker_real_model_improves_ordering(self) -> None:
        """集成测试：真实 CrossEncoder 精排后最相关文档应排在前面。"""
        from src.rag.reranker import rerank

        query = "ChromaDB 向量数据库的存储路径"
        hits = [
            _Hit(source="a.txt", document="今天天气很好，适合出门散步。", distance=0.1, collection="kb_test"),
            _Hit(source="b.txt", document="ChromaDB 默认将数据持久化到本地磁盘路径。", distance=0.3, collection="kb_test"),
            _Hit(source="c.txt", document="Python 列表推导式可以简化循环代码。", distance=0.2, collection="kb_test"),
        ]
        result = rerank(query=query, hits=hits, top_k=2)
        assert result[0].source == "b.txt"
