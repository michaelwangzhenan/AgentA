"""
Phase 3 测试：RAG 向量化入库 & 检索

测试内容：
    - rag/ingest.py：chunk_text() 分块逻辑
    - config.py：resolve_embedding() 多模型解析
    - rag/retriever.py：search() 检索返回格式（需要已完成入库）
"""

import pytest
import src.config as config
from src.rag.ingest import chunk_text
from src.rag.retriever import search


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
        # 第一块末尾 100 字符应与第二块开头 100 字符相同
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
    def test_search_result_contains_source(self) -> None:
        """检索结果应包含来源文件名"""
        result = search("ChromaDB 向量数据库", top_k=2)
        assert "来源:" in result

    @pytest.mark.integration
    def test_search_result_contains_similarity(self) -> None:
        """检索结果应包含相似度分数"""
        result = search("LLM 大语言模型", top_k=2)
        assert "相似度:" in result

    @pytest.mark.integration
    def test_search_result_contains_collection_name(self) -> None:
        """检索结果应包含 collection 名称（库: kb_xx）"""
        result = search("LLM 大语言模型", top_k=2)
        assert "库:" in result

    @pytest.mark.integration
    def test_search_top_k_limits_results(self) -> None:
        """返回的文档片段数量不应超过 top_k"""
        result = search("测试", top_k=2)
        # 每个结果以 [N] 开头
        count = result.count("\n[")  # 第 2 个及之后的结果
        assert count <= 1  # top_k=2 时最多有 1 个 "\n[" 分隔符

    @pytest.mark.integration
    def test_search_irrelevant_query_still_returns_result(self) -> None:
        """即使问题不相关，也应返回最相近的结果（不返回空）"""
        result = search("火星上有生命吗", top_k=1)
        assert isinstance(result, str)
        assert len(result) > 0
