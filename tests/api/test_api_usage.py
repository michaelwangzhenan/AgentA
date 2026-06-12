"""Token 用量统计端点 UT（iter_11）。

覆盖：本人 vs 全员视角隔离、admin 门禁、单价读写、CSV 导出，以及"公共采集点"
（/api/chat 经 final_answer 事件落库，对 Agent 实现零侵入）。

用 reset_shared_store_for_testing 把临时 UsageStore 注入进程单例：路由读
（get_usage_store → get_shared_store）与写（record_usage → get_shared_store）
都会命中它，省去 dependency_overrides。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import src.config as _cfg
from src.api.deps import get_agent, get_user_store
from src.api.main import app
from src.memory.usage_store import UsageStore, reset_shared_store_for_testing
from src.memory.user_store import UserStore


class _Usage(NamedTuple):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@pytest.fixture
def usage_store(tmp_path: Path) -> Iterator[UsageStore]:
    s = UsageStore(str(tmp_path / "usage.db"))
    reset_shared_store_for_testing(s)
    yield s
    reset_shared_store_for_testing(None)
    s.close()


@pytest.fixture
def user_store(tmp_path: Path) -> Iterator[UserStore]:
    s = UserStore(str(tmp_path / "auth.db"))
    # id=1 (admin-ish), id=2 普通；用量 seed 用这两个 id
    s.create_user("alice", "pw", role="admin")
    s.create_user("bob", "pw", role="user")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(usage_store: UsageStore, user_store: UserStore) -> TestClient:
    app.dependency_overrides[get_user_store] = lambda: user_store
    return TestClient(app)


def _seed(s: UsageStore, **kw) -> None:
    base = dict(user_id=1, model_id="kimi-k2.5", thinking=False,
                prompt_tokens=100, completion_tokens=50)
    base.update(kw)
    s.record(**base)


# ── 本人视角 ──────────────────────────────────────────────────────────────────


def test_my_summary_only_current_user(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1, prompt_tokens=100, completion_tokens=50)
    _seed(usage_store, user_id=2, prompt_tokens=999, completion_tokens=999)
    r = client.get("/api/usage/summary?range=30d")
    assert r.status_code == 200
    body = r.json()
    # 默认用户 id=1（认证关闭）；只统计本人
    assert body["count"] == 1
    assert body["prompt_tokens"] == 100
    assert body["total_tokens"] == 150


def test_my_series_returns_rows(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1, model_id="kimi-k2.5")
    r = client.get("/api/usage/series?range=30d&group_by=model")
    assert r.status_code == 200
    body = r.json()
    assert body["group_by"] == "model"
    assert any(row["key"] == "kimi-k2.5" for row in body["rows"])


def test_my_events_excludes_others(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1)
    _seed(usage_store, user_id=2)
    r = client.get("/api/usage/events?range=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    # 本人明细不带 user 字段
    assert body["events"][0]["username"] is None


def test_my_events_csv(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1)
    r = client.get("/api/usage/events.csv?range=30d")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "total_tokens" in r.text


# ── 全员视角（admin） ──────────────────────────────────────────────────────────


def test_admin_summary_includes_all(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1, prompt_tokens=100, completion_tokens=50)
    _seed(usage_store, user_id=2, prompt_tokens=200, completion_tokens=100)
    r = client.get("/api/usage/admin/summary?range=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["total_tokens"] == 450


def test_admin_users_ranking(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1, prompt_tokens=10, completion_tokens=10)
    _seed(usage_store, user_id=2, prompt_tokens=500, completion_tokens=500)
    r = client.get("/api/usage/admin/users?range=30d")
    assert r.status_code == 200
    users = r.json()["users"]
    # 按 token 降序，bob(id=2) 在前
    assert users[0]["username"] == "bob"
    assert users[0]["total_tokens"] == 1000


def test_admin_events_have_username(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=2)
    r = client.get("/api/usage/admin/events?range=30d")
    assert r.status_code == 200
    ev = r.json()["events"][0]
    assert ev["username"] == "bob"
    assert ev["user_id"] == 2


def test_admin_series_group_by_user(client: TestClient, usage_store: UsageStore) -> None:
    _seed(usage_store, user_id=1)
    _seed(usage_store, user_id=2)
    r = client.get("/api/usage/admin/series?range=30d&group_by=user")
    assert r.status_code == 200
    labels = {row["key_label"] for row in r.json()["rows"]}
    assert {"alice", "bob"} <= labels


# ── 单价配置 ──────────────────────────────────────────────────────────────────


def test_get_pricing_lists_models_with_defaults(client: TestClient) -> None:
    r = client.get("/api/usage/pricing")
    assert r.status_code == 200
    body = r.json()
    by_id = {it["model_id"]: it for it in body["items"]}
    assert "kimi-k2.5" in by_id
    assert by_id["kimi-k2.5"]["input_price"] == _cfg.MODEL_PRICING_DEFAULTS["kimi-k2.5"][0]
    assert by_id["kimi-k2.5"]["is_override"] is False


def test_put_pricing_overrides_and_affects_cost(
    client: TestClient, usage_store: UsageStore
) -> None:
    # 自定义 gpt-4o 单价为 $10/$20 每 1M
    r = client.put("/api/usage/pricing", json={
        "items": [{"model_id": "gpt-4o", "input_price": 10.0, "output_price": 20.0}]
    })
    assert r.status_code == 200
    by_id = {it["model_id"]: it for it in r.json()["items"]}
    assert by_id["gpt-4o"]["is_override"] is True
    assert by_id["gpt-4o"]["input_price"] == 10.0

    # 记 1M 输入 + 1M 输出 → 成本应 = 10 + 20 = 30
    usage_store.record(user_id=1, model_id="gpt-4o", thinking=False,
                       prompt_tokens=1_000_000, completion_tokens=1_000_000)
    s = client.get("/api/usage/summary?range=30d").json()
    assert s["cost"] == pytest.approx(30.0)


def test_put_pricing_ignores_unknown_model(client: TestClient) -> None:
    r = client.put("/api/usage/pricing", json={
        "items": [{"model_id": "no-such-model", "input_price": 1.0, "output_price": 1.0}]
    })
    assert r.status_code == 200
    assert all(it["model_id"] != "no-such-model" for it in r.json()["items"])


# ── admin 门禁（开认证 + 普通用户） ────────────────────────────────────────────


def test_admin_endpoints_require_admin(usage_store: UsageStore, user_store: UserStore) -> None:
    app.dependency_overrides[get_user_store] = lambda: user_store
    orig = _cfg.AUTH_ENABLED
    _cfg.AUTH_ENABLED = True
    try:
        c = TestClient(app)
        # 以普通用户 bob 登录
        login = c.post("/api/auth/login", json={"username": "bob", "password": "pw"})
        assert login.status_code == 200
        # 本人端点放行
        assert c.get("/api/usage/summary?range=30d").status_code == 200
        # 全员端点 403
        assert c.get("/api/usage/admin/summary?range=30d").status_code == 403
        # 写单价 403
        wr = c.put("/api/usage/pricing", json={"items": []})
        assert wr.status_code == 403
    finally:
        _cfg.AUTH_ENABLED = orig


def test_pricing_isolation_between_users(usage_store: UsageStore, user_store: UserStore) -> None:
    """本人用量按登录用户隔离（开认证）。"""
    app.dependency_overrides[get_user_store] = lambda: user_store
    orig = _cfg.AUTH_ENABLED
    _cfg.AUTH_ENABLED = True
    try:
        _seed(usage_store, user_id=1)  # alice
        _seed(usage_store, user_id=2)  # bob
        c = TestClient(app)
        c.post("/api/auth/login", json={"username": "bob", "password": "pw"})
        body = c.get("/api/usage/summary?range=30d").json()
        assert body["count"] == 1  # 只看到自己（bob）
    finally:
        _cfg.AUTH_ENABLED = orig


# ── 公共采集点：/api/chat 经 final_answer 落库（实现无关） ──────────────────────


def test_chat_records_usage_via_final_answer(
    client: TestClient, usage_store: UsageStore
) -> None:
    def _run(message, *, session_id=None, event_callback=None):
        if event_callback is not None:
            event_callback(SimpleNamespace(
                type="final_answer",
                payload={"text": "hi", "usage": _Usage(30, 12, 42)},
            ))
        return "hi"

    mock = MagicMock()
    mock.run.side_effect = _run
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200

    now = int(time.time())
    rows, total = usage_store.list_events(now - 60, now + 60, user_id=_cfg.DEFAULT_USER_ID)
    assert total == 1
    assert rows[0]["prompt_tokens"] == 30
    assert rows[0]["completion_tokens"] == 12
    assert rows[0]["total_tokens"] == 42


def test_chat_no_usage_records_nothing(client: TestClient, usage_store: UsageStore) -> None:
    mock = MagicMock()
    mock.run.return_value = "hi"  # 不触发 event_callback → 无 usage
    app.dependency_overrides[get_agent] = lambda: mock

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    now = int(time.time())
    _, total = usage_store.list_events(now - 60, now + 60)
    assert total == 0
