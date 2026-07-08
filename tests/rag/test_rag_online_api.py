"""测试：online API backend（SiliconFlow）—— embedding / rerank 云端分发。

覆盖核心路径（纯单元测试，mock requests / 不发真实请求、不加载本地模型）：
    - embedding 云端判定：仅 bge-m3 且默认 embedding=api-m3 走云端，表外回落本地
    - 精排单开关派生：RERANKER_MODEL 的 disable / api: 前缀解析（rerank_enabled/is_api/model_name）
    - HTTP：重试（网络异常 / 429 / 5xx）、4xx 立即抛、耗尽抛 OnlineApiError
    - embed_texts / rerank_scores：请求体、按 index 对齐、异常
    - ApiEmbeddingFunction：name 同名复用、未托管模型报错
    - retriever._get_embedding_fn 分发 + _embed_query_cached 云端/本地键
    - reranker.rerank api 路径：不二次 sigmoid、失败降级不精排
"""

from unittest.mock import MagicMock, patch

import pytest

import src.config as config
from src.rag import online_api


def _resp(status: int, payload: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = text
    return r


@pytest.fixture
def _api_on(monkeypatch):
    """把默认 embedding 切到 api-m3（m3 走云端），并给一个假 key / 基址。"""
    monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "api-m3")
    monkeypatch.setattr(config, "SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(config, "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(config, "SILICONFLOW_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "SILICONFLOW_TIMEOUT_SEC", 5)


class TestEmbeddingIsApi:
    """embedding 云端判定：只有 bge-m3 且默认 embedding=api-m3 才走云端，其余一律本地。"""

    def test_default_embedding_is_api(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "api-m3")
        assert config.default_embedding_is_api() is True
        monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "m3")
        assert config.default_embedding_is_api() is False

    def test_resolve_api_m3_shares_m3(self) -> None:
        # api-m3 与本地 m3 解析到同一 (模型, collection)，共用 kb_m3、免重灌
        assert config.resolve_embedding("api-m3") == config.resolve_embedding("m3")

    def test_mapped_model_api_when_default_api(self, _api_on) -> None:
        assert online_api.embedding_is_api("BAAI/bge-m3") is True

    def test_unmapped_model_stays_local(self, _api_on) -> None:
        # MiniLM / bge-small-zh 不在映射表 → 即便默认 api-m3 也本地
        assert online_api.embedding_is_api("all-MiniLM-L6-v2") is False
        assert online_api.embedding_is_api("BAAI/bge-small-zh") is False

    def test_default_not_api_is_local(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "m3")
        assert online_api.embedding_is_api("BAAI/bge-m3") is False


class TestRerankModelHelpers:
    """精排单开关派生：RERANKER_MODEL 一个值同时表达 开不开 / 云端还是本地 / 哪个模型。"""

    def test_disable(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "RERANKER_MODEL", "disable")
        assert config.rerank_enabled() is False
        assert config.rerank_is_api() is False

    def test_api_value(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "RERANKER_MODEL", "api:BAAI/bge-reranker-v2-m3")
        assert config.rerank_enabled() is True
        assert config.rerank_is_api() is True
        # 去前缀后的真实模型名：api 打分模型 id / 阈值查表都用它
        assert config.rerank_model_name() == "BAAI/bge-reranker-v2-m3"

    def test_local_values(self, monkeypatch) -> None:
        for name in (
            "BAAI/bge-reranker-base",
            "BAAI/bge-reranker-v2-m3",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        ):
            monkeypatch.setattr(config, "RERANKER_MODEL", name)
            assert config.rerank_enabled() is True
            assert config.rerank_is_api() is False
            assert config.rerank_model_name() == name

    def test_api_and_local_v2m3_share_threshold(self, monkeypatch) -> None:
        # api: 与本地同款 v2-m3 去前缀后同名 → 命中同一条 per-model 阈值（0.0）
        monkeypatch.setattr(config, "RERANKER_MODEL", "api:BAAI/bge-reranker-v2-m3")
        assert config.min_rerank_score_for_model(config.rerank_model_name()) == 0.0
        monkeypatch.setattr(config, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        assert config.min_rerank_score_for_model(config.rerank_model_name()) == 0.0


class TestHttpRetry:
    """_post 重试与错误分类。"""

    def test_retry_on_5xx_then_success(self, _api_on) -> None:
        seq = [_resp(503, text="boom"), _resp(200, {"ok": 1})]
        with patch("src.rag.online_api.requests.post", side_effect=seq) as mock_post:
            data, _ms = online_api._post("/embeddings", {"x": 1})
        assert data == {"ok": 1}
        assert mock_post.call_count == 2

    def test_4xx_raises_without_retry(self, _api_on) -> None:
        with patch("src.rag.online_api.requests.post", return_value=_resp(401, text="bad key")) as mock_post:
            with pytest.raises(online_api.OnlineApiError):
                online_api._post("/embeddings", {"x": 1})
        assert mock_post.call_count == 1  # 4xx 不重试

    def test_raises_after_retries_exhausted(self, _api_on) -> None:
        with patch("src.rag.online_api.requests.post", return_value=_resp(500)) as mock_post:
            with pytest.raises(online_api.OnlineApiError):
                online_api._post("/embeddings", {"x": 1})
        assert mock_post.call_count == 3  # MAX_RETRIES=2 → 共 3 次

    def test_network_error_is_retryable(self, _api_on) -> None:
        import requests as _rq
        seq = [_rq.ConnectionError("net"), _resp(200, {"ok": 1})]
        with patch("src.rag.online_api.requests.post", side_effect=seq) as mock_post:
            data, _ms = online_api._post("/embeddings", {"x": 1})
        assert data == {"ok": 1}
        assert mock_post.call_count == 2


class TestEmbedTexts:
    def test_request_body_and_index_alignment(self, _api_on) -> None:
        # 故意乱序返回，验证按 index 排序对齐
        payload = {"data": [
            {"index": 1, "embedding": [0.2, 0.2]},
            {"index": 0, "embedding": [0.1, 0.1]},
        ]}
        with patch("src.rag.online_api.requests.post", return_value=_resp(200, payload)) as mock_post:
            vecs = online_api.embed_texts(["a", "b"], "BAAI/bge-m3")
        body = mock_post.call_args.kwargs["json"]
        assert body["model"] == "BAAI/bge-m3"
        assert body["input"] == ["a", "b"]
        assert vecs == [[0.1, 0.1], [0.2, 0.2]]

    def test_empty_returns_empty(self, _api_on) -> None:
        with patch("src.rag.online_api.requests.post") as mock_post:
            assert online_api.embed_texts([], "BAAI/bge-m3") == []
        mock_post.assert_not_called()

    def test_count_mismatch_raises(self, _api_on) -> None:
        payload = {"data": [{"index": 0, "embedding": [0.1]}]}
        with patch("src.rag.online_api.requests.post", return_value=_resp(200, payload)):
            with pytest.raises(online_api.OnlineApiError):
                online_api.embed_texts(["a", "b"], "BAAI/bge-m3")


class TestRerankScores:
    def test_scores_aligned_by_index(self, _api_on) -> None:
        payload = {"results": [
            {"index": 2, "relevance_score": 0.10},
            {"index": 0, "relevance_score": 0.99},
            {"index": 1, "relevance_score": 0.00},
        ]}
        with patch("src.rag.online_api.requests.post", return_value=_resp(200, payload)) as mock_post:
            scores = online_api.rerank_scores("q", ["d0", "d1", "d2"], "BAAI/bge-reranker-v2-m3")
        body = mock_post.call_args.kwargs["json"]
        assert body["query"] == "q"
        assert body["documents"] == ["d0", "d1", "d2"]
        assert scores == [0.99, 0.00, 0.10]


class TestApiEmbeddingFunction:
    def test_name_is_sentence_transformer(self) -> None:
        # 同名以复用既有 kb_m3（EF 冲突校验按 name 判定）
        assert online_api.ApiEmbeddingFunction.name() == "sentence_transformer"

    def test_call_delegates_to_embed_texts(self, _api_on) -> None:
        ef = online_api.ApiEmbeddingFunction("BAAI/bge-m3")
        with patch("src.rag.online_api.embed_texts", return_value=[[0.1], [0.2]]) as m:
            out = ef(["a", "b"])
        m.assert_called_once_with(["a", "b"], "BAAI/bge-m3")
        # Chroma 的 EmbeddingFunction 基类会把结果 normalize 成 numpy float32，转回比对值
        out_vals = [[float(x) for x in v] for v in out]
        assert out_vals == [pytest.approx([0.1]), pytest.approx([0.2])]

    def test_unmapped_model_raises(self) -> None:
        with pytest.raises(online_api.OnlineApiError):
            online_api.ApiEmbeddingFunction("all-MiniLM-L6-v2")


class TestRetrieverDispatch:
    """retriever._get_embedding_fn / _embed_query_cached 按云端 / 本地分发。"""

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        from src.rag import retriever
        retriever._embedding_fn_cache.clear()
        retriever._embed_query_cached.cache_clear()
        yield
        retriever._embedding_fn_cache.clear()
        retriever._embed_query_cached.cache_clear()

    def test_api_mapped_returns_api_ef(self, _api_on) -> None:
        from src.rag import retriever
        fn = retriever._get_embedding_fn("BAAI/bge-m3")
        assert isinstance(fn, online_api.ApiEmbeddingFunction)

    def test_api_unmapped_returns_local(self, _api_on) -> None:
        from src.rag import retriever
        with patch("src.rag.retriever.SentenceTransformerEmbeddingFunction") as mock_st:
            retriever._get_embedding_fn("all-MiniLM-L6-v2")
        mock_st.assert_called_once()  # 表外模型走本地

    def test_embed_query_cached_api_branch(self, _api_on) -> None:
        from src.rag import retriever
        with patch("src.rag.online_api.embed_texts", return_value=[[0.5, 0.6]]) as m:
            vec = retriever._embed_query_cached("BAAI/bge-m3", "hello", True)
        m.assert_called_once_with(["hello"], "BAAI/bge-m3")
        assert vec == (0.5, 0.6)

    def test_get_embedding_fn_use_api_override_decouples_global(self, monkeypatch) -> None:
        # 全局本地（default 非 api-m3），但入库显式 use_api=True → 仍走云端 ApiEmbeddingFunction
        from src.rag import retriever
        monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "m3")
        fn = retriever._get_embedding_fn("BAAI/bge-m3", use_api=True)
        assert isinstance(fn, online_api.ApiEmbeddingFunction)

    def test_get_embedding_fn_use_api_false_forces_local(self, monkeypatch) -> None:
        # 全局云端（api-m3），但入库显式 use_api=False → 强制本地，不看全局
        from src.rag import retriever
        monkeypatch.setattr(config, "DEFAULT_EMBEDDING_ALIAS", "api-m3")
        with patch("src.rag.retriever.SentenceTransformerEmbeddingFunction") as mock_st:
            retriever._get_embedding_fn("BAAI/bge-m3", use_api=False)
        mock_st.assert_called_once()

    def test_get_embedding_fn_use_api_true_unmapped_falls_back_local(self, monkeypatch) -> None:
        # 防御：显式 use_api=True 但该模型无云端版 → 回落本地，不炸
        from src.rag import retriever
        with patch("src.rag.retriever.SentenceTransformerEmbeddingFunction") as mock_st:
            retriever._get_embedding_fn("all-MiniLM-L6-v2", use_api=True)
        mock_st.assert_called_once()

    def test_alias_is_api(self) -> None:
        assert config.alias_is_api("api-m3") is True
        assert config.alias_is_api("m3") is False
        assert config.alias_is_api("en") is False


class TestRerankApiPath:
    """reranker.rerank api 路径：不二次 sigmoid、失败降级不精排。"""

    def _hits(self, n: int):
        from src.rag.retriever import Hit
        return [Hit(source=f"d{i}.txt", document=f"doc {i}", distance=0.1 * i, collection="kb_m3") for i in range(n)]

    def test_api_scores_used_without_sigmoid(self, monkeypatch) -> None:
        from src.rag import reranker
        monkeypatch.setattr(config, "RERANKER_MODEL", "api:BAAI/bge-reranker-v2-m3")
        hits = self._hits(3)
        with patch("src.rag.online_api.rerank_scores", return_value=[0.99, 0.00, 0.50]) as m:
            result = reranker.rerank("q", hits, top_k=3)
        # 走 api 时把去前缀的真实模型名作为打分 model id 传下去
        assert m.call_args[0][2] == "BAAI/bge-reranker-v2-m3"
        # 分数原样保留（若二次 sigmoid，0.99 会被压到 ~0.729）
        assert result[0].score == pytest.approx(0.99)
        assert result[0].document == "doc 0"
        assert result[-1].score == pytest.approx(0.00)

    def test_api_failure_degrades_to_no_rerank(self, monkeypatch) -> None:
        from src.rag import reranker
        monkeypatch.setattr(config, "RERANKER_MODEL", "api:BAAI/bge-reranker-v2-m3")
        hits = self._hits(5)
        with patch("src.rag.online_api.rerank_scores", side_effect=online_api.OnlineApiError("boom")):
            result = reranker.rerank("q", hits, top_k=3)
        assert result == hits[:3]  # 降级：返回召回前 top_k，不动分数

    def test_local_value_still_uses_cross_encoder(self, monkeypatch) -> None:
        from src.rag import reranker
        monkeypatch.setattr(config, "RERANKER_MODEL", "BAAI/bge-reranker-base")
        hits = self._hits(3)
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.8, 0.7]
        with patch("src.rag.reranker._get_cross_encoder", return_value=mock_model):
            result = reranker.rerank("q", hits, top_k=3)
        mock_model.predict.assert_called_once()
        assert len(result) == 3


class TestRerankMinScorePerModel:
    """精排阈值 per-model：v2-m3 用专用低阈值，其余回退全局。"""

    def test_v2_m3_uses_dedicated_threshold(self, monkeypatch) -> None:
        # 全局设高阈值，v2-m3 仍应拿到映射表里的专用值（0.0），不受全局影响
        monkeypatch.setattr(config, "RAG_RERANK_MIN_SCORE", 0.3)
        assert config.min_rerank_score_for_model("BAAI/bge-reranker-v2-m3") == 0.0

    def test_unmapped_model_falls_back_to_global(self, monkeypatch) -> None:
        monkeypatch.setattr(config, "RAG_RERANK_MIN_SCORE", 0.3)
        assert config.min_rerank_score_for_model("BAAI/bge-reranker-base") == 0.3
