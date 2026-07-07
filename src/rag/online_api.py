"""SiliconFlow 云端 API 客户端 —— embedding / rerank 的云端实现。

- embedding 是否走云端由 `config.EMBEDDING_MODEL=api-m3` 决定（见 `embedding_is_api`），
  只对 `config.ONLINE_API_MODELS` 表内的模型生效，表外模型仍走本地。
- rerank 是否走云端由 `config.RERANKER_MODEL` 的 `api:` 前缀自解释（见 `src/config.py`），
  本模块只提供 `rerank_scores()` HTTP 调用。

用法：
    from src.rag import online_api
    online_api.embedding_is_api("BAAI/bge-m3")        # -> True | False
    online_api.embed_texts(["hi"], "BAAI/bge-m3")     # -> [[...]]
    online_api.rerank_scores(q, docs, "BAAI/bge-reranker-v2-m3")  # -> [0.99, ...]
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

import src.config as config

logger = logging.getLogger(__name__)


class OnlineApiError(RuntimeError):
    """SiliconFlow 调用失败（重试耗尽 / 不可重试的 4xx / 响应解析失败）。"""


# ── 云端判定 ─────────────────────────────────────────────────────────────────
def embedding_is_api(model_name: str) -> bool:
    """该 embedding 模型是否走云端 API：仅 bge-m3 可，且默认 embedding 选了 api-m3。"""
    return config.online_api_model(model_name) is not None and config.default_embedding_is_api()


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _post(path: str, payload: dict) -> tuple[dict, float]:
    """POST 到 SiliconFlow 并返回 (json, 耗时ms)。

    重试仅针对可恢复错误（网络异常 / 429 / 5xx）；4xx（如 key 错误）立即抛出不重试。
    """
    url = config.SILICONFLOW_BASE_URL.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }
    attempts = max(1, config.SILICONFLOW_MAX_RETRIES + 1)
    last_err: Exception | None = None
    for i in range(attempts):
        t0 = time.time()
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=config.SILICONFLOW_TIMEOUT_SEC)
        except requests.RequestException as e:
            last_err = e
            logger.warning("[OnlineAPI] %s 网络异常 %d/%d: %s", path, i + 1, attempts, e)
            continue
        if resp.ok:
            return resp.json(), (time.time() - t0) * 1000
        detail = resp.text[:200]
        if resp.status_code != 429 and resp.status_code < 500:
            raise OnlineApiError(f"SiliconFlow {path} HTTP {resp.status_code}: {detail}")
        last_err = OnlineApiError(f"HTTP {resp.status_code}: {detail}")
        logger.warning("[OnlineAPI] %s 可重试错误 %d/%d: %s", path, i + 1, attempts, last_err)
    raise OnlineApiError(f"SiliconFlow {path} 调用失败（已重试 {attempts} 次）: {last_err}") from last_err


def embed_texts(texts: list[str], api_model_id: str) -> list[list[float]]:
    """批量编码文本为向量（顺序与入参对齐）。"""
    if not texts:
        return []
    data, ms = _post("/embeddings", {"model": api_model_id, "input": texts})
    try:
        items = sorted(data["data"], key=lambda x: x["index"])
        vecs = [[float(x) for x in it["embedding"]] for it in items]
    except (KeyError, TypeError) as e:
        raise OnlineApiError(f"embeddings 响应解析失败: {e}") from e
    if len(vecs) != len(texts):
        raise OnlineApiError(f"embeddings 返回条数 {len(vecs)} != 入参 {len(texts)}")
    logger.info("[OnlineAPI] embeddings model=%s n=%d %.0fms", api_model_id, len(texts), ms)
    return vecs


def rerank_scores(query: str, documents: list[str], api_model_id: str) -> list[float]:
    """对 (query, documents) 打相关度分，返回与 documents 顺序对齐的 [0,1] 分数。"""
    if not documents:
        return []
    data, ms = _post("/rerank", {"model": api_model_id, "query": query, "documents": documents})
    try:
        scores = [0.0] * len(documents)
        for r in data["results"]:
            scores[int(r["index"])] = float(r["relevance_score"])
    except (KeyError, TypeError, IndexError) as e:
        raise OnlineApiError(f"rerank 响应解析失败: {e}") from e
    logger.info("[OnlineAPI] rerank model=%s n=%d %.0fms", api_model_id, len(documents), ms)
    return scores


# ── Chroma 兼容 embedding function ───────────────────────────────────────────
class ApiEmbeddingFunction(EmbeddingFunction):
    """把编码转发到 SiliconFlow 的 Chroma embedding function。

    `name()` 故意返回 "sentence_transformer"：api 与本地跑的是同款 bge-m3、向量空间
    对齐（已验证 cosine ≥ 0.9994），Chroma 的 EF 冲突校验按 name 判定，报同名即可让
    api 直接读写既有 `kb_m3`（免重灌），也让本地/api 之间来回切共用同一 collection。
    """

    def __init__(self, model_name: str) -> None:
        vendor_model = config.online_api_model(model_name)
        if vendor_model is None:
            raise OnlineApiError(f"模型 {model_name} 无 API 版，不能用 ApiEmbeddingFunction")
        self._model_name = model_name
        self._api_model_id = vendor_model[1]

    def __call__(self, input: Documents) -> Embeddings:
        return embed_texts(list(input), self._api_model_id)  # type: ignore[return-value]

    @staticmethod
    def name() -> str:
        return "sentence_transformer"

    def get_config(self) -> dict[str, Any]:
        return {"model_name": self._model_name}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "ApiEmbeddingFunction":  # noqa: A002
        return ApiEmbeddingFunction(config["model_name"])

    def default_space(self) -> str:
        return "cosine"
