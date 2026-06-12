"""/api/api-keys 端点 UT。

覆盖：
1. GET 返回脱敏视图（configured / masked / source），永不出现完整明文
2. PUT provider key → PROVIDER_CONFIGS 立即生效 + 持久化 + GET source=override
3. PUT serpapi → _cfg.SERPAPI_API_KEY 立即生效
4. PUT 空串 / DELETE → 恢复到启动时（.env）值
5. 重启模拟：apply_overrides 仍能恢复值
6. 未知 key → 404
7. admin 门禁：开认证 + 普通用户 → 403
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.runtime import api_keys as _store
from src.api.deps import get_user_store
from src.api.main import app
from src.memory.user_store import UserStore


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """独立 keys 文件 + 还原 PROVIDER_CONFIGS / SERPAPI_API_KEY / 模块级 snapshot。

    把 _initial 重定格到当前（测试启动时）值，使 reset / 空文件 apply 对 UT 行为确定。
    """
    keys_path = tmp_path / "api_keys.json"
    monkeypatch.setattr(_store, "KEYS_PATH", keys_path)

    providers_snapshot = dict(_cfg.PROVIDER_CONFIGS)
    serpapi_snapshot = _cfg.SERPAPI_API_KEY
    initial_snapshot = dict(_store._initial)
    snapshot_taken_orig = _store._snapshot_taken

    _store._initial.clear()
    for item in _store.SECRET_ITEMS:
        _store._initial[item.id] = _store.current_value(item)
    _store._snapshot_taken = True

    try:
        yield TestClient(app)
    finally:
        _cfg.PROVIDER_CONFIGS.clear()
        _cfg.PROVIDER_CONFIGS.update(providers_snapshot)
        _cfg.SERPAPI_API_KEY = serpapi_snapshot
        _store._initial.clear()
        _store._initial.update(initial_snapshot)
        _store._snapshot_taken = snapshot_taken_orig


# ─── GET ──────────────────────────────────────────────────────────────────

def test_get_lists_all_items(client: TestClient) -> None:
    body = client.get("/api/api-keys").json()
    ids = {it["id"] for it in body["items"]}
    assert {"openai", "kimi", "claude", "serpapi"} <= ids
    assert "ollama" not in ids  # 占位 key 不纳入管理
    for it in body["items"]:
        for required in ("id", "label", "env", "configured", "masked", "source"):
            assert required in it
        assert it["source"] in ("env", "override")


def test_get_never_leaks_full_plaintext(client: TestClient) -> None:
    """即便某 provider 配了真实 key，GET 响应也不能出现完整明文。"""
    _store.set_key("openai", "sk-secret-1234567890-abcdef")
    text = json.dumps(client.get("/api/api-keys").json(), ensure_ascii=False)
    assert "sk-secret-1234567890-abcdef" not in text
    assert "sk-…cdef" in text  # 脱敏：头 3 + 尾 4


# ─── PUT ──────────────────────────────────────────────────────────────────

def test_put_provider_key_takes_effect_and_persists(client: TestClient) -> None:
    r = client.put("/api/api-keys/openai", json={"value": "sk-new-openai-key-9999"})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["source"] == "override"
    # 运行时即时生效：PROVIDER_CONFIGS 已替换
    assert _cfg.PROVIDER_CONFIGS["openai"].api_key == "sk-new-openai-key-9999"
    # 持久化到文件
    data = json.loads(_store.KEYS_PATH.read_text(encoding="utf-8"))
    assert data["openai"] == "sk-new-openai-key-9999"


def test_put_serpapi_scalar_takes_effect(client: TestClient) -> None:
    r = client.put("/api/api-keys/serpapi", json={"value": "serp-abc-12345678"})
    assert r.status_code == 200
    assert _cfg.SERPAPI_API_KEY == "serp-abc-12345678"
    assert r.json()["source"] == "override"


def test_put_empty_value_clears_override(client: TestClient) -> None:
    initial = _cfg.PROVIDER_CONFIGS["openai"].api_key
    client.put("/api/api-keys/openai", json={"value": "sk-temporary-123456"})
    assert _cfg.PROVIDER_CONFIGS["openai"].api_key == "sk-temporary-123456"

    r = client.put("/api/api-keys/openai", json={"value": ""})
    assert r.status_code == 200
    assert _cfg.PROVIDER_CONFIGS["openai"].api_key == initial
    assert r.json()["source"] == "env"


def test_put_unknown_key_404(client: TestClient) -> None:
    assert client.put("/api/api-keys/nope", json={"value": "x"}).status_code == 404


# ─── DELETE / reset ─────────────────────────────────────────────────────────

def test_delete_resets_to_initial(client: TestClient) -> None:
    initial = _cfg.PROVIDER_CONFIGS["kimi"].api_key
    client.put("/api/api-keys/kimi", json={"value": "sk-kimi-override-1234"})
    assert _cfg.PROVIDER_CONFIGS["kimi"].api_key == "sk-kimi-override-1234"

    r = client.delete("/api/api-keys/kimi")
    assert r.status_code == 200
    assert r.json()["source"] == "env"
    assert _cfg.PROVIDER_CONFIGS["kimi"].api_key == initial
    # 文件里已删 key
    if _store.KEYS_PATH.exists():
        assert "kimi" not in json.loads(_store.KEYS_PATH.read_text(encoding="utf-8"))


def test_delete_unknown_key_404(client: TestClient) -> None:
    assert client.delete("/api/api-keys/nope").status_code == 404


# ─── 重启模拟 ────────────────────────────────────────────────────────────────

def test_override_survives_apply_on_restart(client: TestClient) -> None:
    client.put("/api/api-keys/deepseek", json={"value": "sk-deepseek-restart-77"})
    # 模拟新进程：把 PROVIDER_CONFIGS 改回 initial，apply 应再次覆盖
    import dataclasses
    cfg = _cfg.PROVIDER_CONFIGS["deepseek"]
    _cfg.PROVIDER_CONFIGS["deepseek"] = dataclasses.replace(
        cfg, api_key=_store._initial["deepseek"]
    )
    _store.apply_overrides()
    assert _cfg.PROVIDER_CONFIGS["deepseek"].api_key == "sk-deepseek-restart-77"


# ─── admin 门禁 ──────────────────────────────────────────────────────────────

def test_endpoints_require_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_store, "KEYS_PATH", tmp_path / "api_keys.json")
    user_store = UserStore(str(tmp_path / "auth.db"))
    user_store.create_user("admin", "pw", role="admin")
    user_store.create_user("bob", "pw", role="user")
    app.dependency_overrides[get_user_store] = lambda: user_store
    orig = _cfg.AUTH_ENABLED
    _cfg.AUTH_ENABLED = True
    try:
        c = TestClient(app)
        login = c.post("/api/auth/login", json={"username": "bob", "password": "pw"})
        assert login.status_code == 200
        assert c.get("/api/api-keys").status_code == 403
        assert c.put("/api/api-keys/openai", json={"value": "x"}).status_code == 403
        assert c.delete("/api/api-keys/openai").status_code == 403
    finally:
        _cfg.AUTH_ENABLED = orig
        app.dependency_overrides.clear()
        user_store.close()
