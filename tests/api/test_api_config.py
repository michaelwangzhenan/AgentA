"""/api/config 编辑面板端点 UT。

覆盖：
1. GET 返回新 shape（groups + items + metadata + source）
2. GET 不出现任何 API key 字段或值
3. PATCH 改 bool / int / enum / multi_enum 各 1 项 → 立即生效 + 持久化
4. PATCH 校验失败：未知 key / 类型错 / 范围越界 / 非法 enum
5. DELETE reset 单项：override 文件清掉 + _cfg 恢复 initial
6. LOG_LEVEL hook 触发：root logger 真切到新 level
7. overrides.json 持久化文件落盘 + 跨 client 重建仍生效
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.runtime import config_overrides
from src.api.runtime.config_meta import REGISTRY
from src.api.main import app
from src.memory import golden_store


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """每个测试用独立的 overrides 文件 + 自动还原 _cfg / _initial_values 状态。

    注意 `_initial_values` 是模块级缓存：它在第一次 `apply_overrides()` 时定格为
    "_cfg 在真实 overrides 文件应用之前的值"，而我们 monkey-patch OVERRIDES_PATH 后再
    跑 reload，会把这些 key "回滚" 到那个旧 initial → 在测试视角下属于伪变化。

    所以这里同时 snapshot 当前 _initial_values + _snapshot_taken，并把 initial 重新
    定格在 "本次测试启动时的 _cfg 状态"，保证 reload 行为对 UT 是确定的。
    """
    overrides_path = tmp_path / "config_overrides.json"
    monkeypatch.setattr(config_overrides, "OVERRIDES_PATH", overrides_path)

    cfg_snapshot: dict[str, object] = {item.key: getattr(_cfg, item.key, None) for item in REGISTRY}
    initial_snapshot = dict(config_overrides._initial_values)
    snapshot_taken_orig = config_overrides._snapshot_taken

    # 把 initial 重定格到当前 _cfg：让 reload(空文件) 等价于 "no-op"
    config_overrides._initial_values.clear()
    for item in REGISTRY:
        if item.editable:
            config_overrides._initial_values[item.key] = getattr(_cfg, item.key, None)
    config_overrides._snapshot_taken = True

    try:
        yield TestClient(app)
    finally:
        for k, v in cfg_snapshot.items():
            setattr(_cfg, k, v)
        config_overrides._initial_values.clear()
        config_overrides._initial_values.update(initial_snapshot)
        config_overrides._snapshot_taken = snapshot_taken_orig


# ─── GET ──────────────────────────────────────────────────────────────────

def test_get_returns_groups_with_items(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert "groups" in body
    group_names = {g["name"] for g in body["groups"]}
    assert {"llm", "rag", "memory", "rules", "mcp", "security", "web", "log"} <= group_names

    # 每组至少 1 项；每项含完整 metadata
    for group in body["groups"]:
        assert group["items"], f"组 {group['name']} 应至少有 1 项"
        for it in group["items"]:
            for required in ("key", "type", "value", "default", "source", "brief", "detail", "editable"):
                assert required in it, f"{it.get('key')} 缺字段 {required}"
            assert it["source"] in ("default", "override")


def test_get_active_model_options_match_registry(client: TestClient) -> None:
    body = client.get("/api/config").json()
    llm = next(g for g in body["groups"] if g["name"] == "llm")
    model = next(it for it in llm["items"] if it["key"] == "ACTIVE_MODEL")
    assert model["type"] == "enum_str"
    assert model["options"] == sorted(_cfg.MODEL_CONFIGS.keys())


def test_routing_and_cache_split_into_two_groups(client: TestClient) -> None:
    body = client.get("/api/config").json()
    by_name = {g["name"]: g for g in body["groups"]}

    routing = by_name["model_routing"]
    assert routing["label"] == "模型路由"
    assert {it["key"] for it in routing["items"]} == {
        "MODEL_ROUTING_ENABLED",
        "MODEL_ROUTING_MODE",
        "MODEL_ROUTING_CLASSIFIER_MODEL",
    }

    cache = by_name["semantic_cache"]
    assert cache["label"] == "语义缓存"
    cache_keys = {it["key"] for it in cache["items"]}
    assert {
        "SEMANTIC_CACHE_ENABLED",
        "SEMANTIC_CACHE_THRESHOLD",
        "SEMANTIC_CACHE_TTL_DAYS",
    } <= cache_keys
    # COLLECTION 属内部项，隐藏不展示
    coll = next(it for it in cache["items"] if it["key"] == "SEMANTIC_CACHE_COLLECTION")
    assert coll["hidden"] is True

    # 旧的合并组已不存在
    assert "jiangben" not in by_name


def test_models_catalog_endpoint(client: TestClient) -> None:
    body = client.get("/api/config/models").json()
    assert body["active"] == _cfg.ACTIVE_MODEL
    # 目录里的模型总数应与 MODEL_CONFIGS 对齐，且每个模型挂在已知厂商下
    listed = {m["id"] for p in body["providers"] for m in p["models"]}
    assert listed == set(_cfg.MODEL_CONFIGS.keys())
    for p in body["providers"]:
        assert p["name"] in _cfg.PROVIDER_CONFIGS


def test_get_no_api_keys_in_response(client: TestClient) -> None:
    """响应里不能出现任何 API key 字段或值。"""
    body = client.get("/api/config").json()
    text = json.dumps(body, ensure_ascii=False).lower()

    forbidden_keys = [
        "api_key",
        "moonshot_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "deepseek_api_key",
        "qwen_api_key",
        "glm_api_key",
        "grok_api_key",
        "minimax_api_key",
        "serpapi_api_key",
    ]
    for k in forbidden_keys:
        assert k not in text, f"响应不应包含 {k!r}"

    for provider_name, provider_cfg in _cfg.PROVIDER_CONFIGS.items():
        if provider_cfg.api_key and len(provider_cfg.api_key) > 10:
            assert provider_cfg.api_key not in text, (
                f"{provider_name} 真实 api_key 泄漏到响应里"
            )


def test_get_value_reflects_runtime_setattr(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cfg, "RAG_TOP_K", 42)
    body = client.get("/api/config").json()
    rag = next(g for g in body["groups"] if g["name"] == "rag")
    top_k = next(it for it in rag["items"] if it["key"] == "RAG_TOP_K")
    assert top_k["value"] == 42


# ─── PATCH 成功路径 ───────────────────────────────────────────────────────

def test_patch_bool_takes_effect_immediately(client: TestClient) -> None:
    r = client.patch("/api/config/RERANKER_ENABLED", json={"value": False})
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["value"] is False
    assert body["item"]["source"] == "override"
    assert _cfg.RERANKER_ENABLED is False


def test_patch_int_within_range(client: TestClient) -> None:
    r = client.patch("/api/config/RAG_TOP_K", json={"value": 12})
    assert r.status_code == 200
    assert r.json()["item"]["value"] == 12
    assert _cfg.RAG_TOP_K == 12


def test_patch_enum_str(client: TestClient) -> None:
    r = client.patch("/api/config/SECURITY_MODE", json={"value": "strict"})
    assert r.status_code == 200
    assert r.json()["item"]["value"] == "strict"
    assert _cfg.SECURITY_MODE == "strict"


def test_patch_multi_enum_str(client: TestClient) -> None:
    r = client.patch(
        "/api/config/RAG_ACTIVE_EMBEDDINGS",
        json={"value": ["en", "m3"]},
    )
    assert r.status_code == 200
    assert r.json()["item"]["value"] == ["en", "m3"]
    assert _cfg.RAG_ACTIVE_EMBEDDINGS == ["en", "m3"]


def test_patch_persists_to_overrides_file(client: TestClient) -> None:
    client.patch("/api/config/RAG_TOP_K", json={"value": 7})
    data = json.loads(config_overrides.OVERRIDES_PATH.read_text(encoding="utf-8"))
    assert data["RAG_TOP_K"] == 7


def test_patch_survives_client_recreate(client: TestClient) -> None:
    """模拟 uvicorn 重启：PATCH 改完后 apply_overrides 仍能恢复值。"""
    client.patch("/api/config/RAG_TOP_K", json={"value": 19})
    # 把 _cfg 重置回 initial，模拟新进程；apply_overrides 应再次覆盖
    _cfg.RAG_TOP_K = config_overrides.get_initial_value("RAG_TOP_K")
    config_overrides.apply_overrides()
    assert _cfg.RAG_TOP_K == 19


# ─── PATCH 校验失败路径 ──────────────────────────────────────────────────

def test_patch_unknown_key_404(client: TestClient) -> None:
    r = client.patch("/api/config/NOT_A_REAL_KEY", json={"value": 1})
    assert r.status_code == 404


def test_patch_type_mismatch_400(client: TestClient) -> None:
    r = client.patch("/api/config/RAG_TOP_K", json={"value": "twelve"})
    assert r.status_code == 400


def test_patch_out_of_range_400(client: TestClient) -> None:
    r = client.patch("/api/config/RAG_TOP_K", json={"value": 9999})
    assert r.status_code == 400
    r2 = client.patch("/api/config/RAG_TOP_K", json={"value": 0})
    assert r2.status_code == 400


def test_patch_invalid_enum_400(client: TestClient) -> None:
    r = client.patch("/api/config/SECURITY_MODE", json={"value": "yolo"})
    assert r.status_code == 400


def test_patch_invalid_multi_enum_400(client: TestClient) -> None:
    r = client.patch(
        "/api/config/RAG_ACTIVE_EMBEDDINGS",
        json={"value": ["en", "wat"]},
    )
    assert r.status_code == 400


# ─── DELETE / reset ───────────────────────────────────────────────────────

def test_delete_resets_to_initial(client: TestClient) -> None:
    initial = config_overrides.get_initial_value("RAG_TOP_K")
    client.patch("/api/config/RAG_TOP_K", json={"value": 11})
    assert _cfg.RAG_TOP_K == 11

    r = client.delete("/api/config/RAG_TOP_K")
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["value"] == initial
    assert body["item"]["source"] == "default"
    assert _cfg.RAG_TOP_K == initial

    # overrides 文件里也应已删 key
    if config_overrides.OVERRIDES_PATH.exists():
        data = json.loads(config_overrides.OVERRIDES_PATH.read_text(encoding="utf-8"))
        assert "RAG_TOP_K" not in data


def test_delete_unknown_key_404(client: TestClient) -> None:
    r = client.delete("/api/config/NOT_A_REAL_KEY")
    assert r.status_code == 404


# ─── 副作用 hook ──────────────────────────────────────────────────────────

def test_log_level_hook_applies_to_root_logger(client: TestClient) -> None:
    original = logging.getLogger().level
    try:
        client.patch("/api/config/LOG_LEVEL", json={"value": "WARNING"})
        assert logging.getLogger().level == logging.WARNING

        client.patch("/api/config/LOG_LEVEL", json={"value": "DEBUG"})
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(original)


# ─── reload from file ────────────────────────────────────────────────────

def test_reload_picks_up_manual_file_edits(client: TestClient) -> None:
    """手动改 overrides 文件 → POST /reload → _cfg 同步到磁盘最新值。"""
    initial = config_overrides.get_initial_value("RAG_TOP_K")
    assert _cfg.RAG_TOP_K == initial

    # 模拟用户在编辑器里手写文件
    config_overrides.OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_overrides.OVERRIDES_PATH.write_text(
        json.dumps({"RAG_TOP_K": 23}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 还没 reload 之前 _cfg 仍是旧值
    assert _cfg.RAG_TOP_K == initial

    r = client.post("/api/config/reload")
    assert r.status_code == 200
    body = r.json()
    assert "RAG_TOP_K" in body["changed_keys"]
    assert _cfg.RAG_TOP_K == 23

    # 同一份响应里返回的 config 也应是最新值 + source=override
    rag = next(g for g in body["config"]["groups"] if g["name"] == "rag")
    top_k = next(it for it in rag["items"] if it["key"] == "RAG_TOP_K")
    assert top_k["value"] == 23
    assert top_k["source"] == "override"


def test_reload_reverts_when_key_removed_from_file(client: TestClient) -> None:
    """文件里删掉某 key → reload → _cfg 恢复到 initial 值。"""
    initial = config_overrides.get_initial_value("RAG_TOP_K")
    client.patch("/api/config/RAG_TOP_K", json={"value": 31})
    assert _cfg.RAG_TOP_K == 31

    # 用户把 overrides 文件清空（或手动删掉这个 key）
    config_overrides.OVERRIDES_PATH.write_text("{}", encoding="utf-8")

    r = client.post("/api/config/reload")
    assert r.status_code == 200
    assert "RAG_TOP_K" in r.json()["changed_keys"]
    assert _cfg.RAG_TOP_K == initial


def test_reload_no_change_returns_empty_changed_keys(client: TestClient) -> None:
    r = client.post("/api/config/reload")
    assert r.status_code == 200
    assert r.json()["changed_keys"] == []


def test_reload_triggers_log_level_hook(client: TestClient) -> None:
    """文件里手改 LOG_LEVEL → reload → 真切到新 level（验证 hook 被触发）。"""
    original = logging.getLogger().level
    try:
        config_overrides.OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config_overrides.OVERRIDES_PATH.write_text(
            json.dumps({"LOG_LEVEL": "WARNING"}, ensure_ascii=False),
            encoding="utf-8",
        )
        client.post("/api/config/reload")
        assert logging.getLogger().level == logging.WARNING
    finally:
        logging.getLogger().setLevel(original)


# ─── registry self-check ─────────────────────────────────────────────────

def test_registry_keys_all_exist_on_cfg() -> None:
    """registry 里每个 key 都必须是 src.config 真实属性，否则 GET / PATCH 会摸空。"""
    missing = [item.key for item in REGISTRY if not hasattr(_cfg, item.key)]
    assert not missing, f"registry 引用了不存在的 _cfg 属性: {missing}"


# ─── 评估配置进 UI ────────────────────────────────────────────────────────

def test_eval_group_present_with_items(client: TestClient) -> None:
    body = client.get("/api/config").json()
    names = {g["name"] for g in body["groups"]}
    assert "eval" in names
    eval_g = next(g for g in body["groups"] if g["name"] == "eval")
    keys = {it["key"] for it in eval_g["items"]}
    assert {
        "TRACE_ENABLED", "RAG_GOLDEN_DB_PATH", "EVAL_AUTO_GOLDEN_ENABLED",
        "EVAL_AUTO_GOLDEN_MAX_Q", "EVAL_GOLDEN_USE_PENDING", "EVAL_JUDGE_MODEL",
    } <= keys


def test_judge_model_options_follow_routing_pool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 评委可选项 = 空（跟随回答模型）+「模型选择」页的可用候选池
    pool = sorted(_cfg.MODEL_CONFIGS.keys())[:2]
    monkeypatch.setattr("src.llm.model_router.effective_pool", lambda: pool)

    body = client.get("/api/config").json()
    eval_g = next(g for g in body["groups"] if g["name"] == "eval")
    jm = next(it for it in eval_g["items"] if it["key"] == "EVAL_JUDGE_MODEL")
    assert jm["type"] == "enum_str"
    assert jm["options"] == [""] + sorted(pool)


def test_judge_model_patch_empty_and_valid(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = sorted(_cfg.MODEL_CONFIGS.keys())[:2]
    monkeypatch.setattr("src.llm.model_router.effective_pool", lambda: pool)

    r = client.patch("/api/config/EVAL_JUDGE_MODEL", json={"value": ""})
    assert r.status_code == 200
    assert _cfg.EVAL_JUDGE_MODEL == ""

    r2 = client.patch("/api/config/EVAL_JUDGE_MODEL", json={"value": pool[0]})
    assert r2.status_code == 200
    assert _cfg.EVAL_JUDGE_MODEL == pool[0]


def test_judge_model_patch_outside_pool_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    all_models = sorted(_cfg.MODEL_CONFIGS.keys())
    pool = all_models[:1]
    monkeypatch.setattr("src.llm.model_router.effective_pool", lambda: pool)

    # 未知模型一律 400
    r = client.patch("/api/config/EVAL_JUDGE_MODEL", json={"value": "__not_a_model__"})
    assert r.status_code == 400

    # 已知但不在候选池内的模型也拒绝
    outside = next((m for m in all_models if m not in pool), None)
    if outside is not None:
        r2 = client.patch("/api/config/EVAL_JUDGE_MODEL", json={"value": outside})
        assert r2.status_code == 400


def test_golden_db_path_hook_resets_shared_store(client: TestClient) -> None:
    """改 RAG_GOLDEN_DB_PATH 触发 hook 清掉 golden 单例（下次按新路径重建）。"""
    sentinel = object()
    golden_store._shared_store = sentinel  # type: ignore[assignment]
    try:
        r = client.patch(
            "/api/config/RAG_GOLDEN_DB_PATH",
            json={"value": "./sqlite_db/rag_golden_test.db"},
        )
        assert r.status_code == 200
        assert golden_store._shared_store is None
    finally:
        golden_store._shared_store = None
