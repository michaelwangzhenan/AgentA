"""DB 秀 /admin/db/* 端点 UT：mock src.db_inspect，只验证 HTTP 封装与 404。

鉴权由 conftest 的 _disable_auth_by_default 兜底为 admin。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.api.routes.db_admin as db_admin
from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_chroma_collections_ok(client, monkeypatch):
    monkeypatch.setattr(
        db_admin.inspect, "chroma_collections",
        lambda: {"root": "/x", "collections": [{"name": "kb_zh", "count": 95, "dim": 512}]},
    )
    r = client.get("/api/admin/db/chroma/collections")
    assert r.status_code == 200
    assert r.json()["collections"][0]["name"] == "kb_zh"


def test_chroma_items_bad_collection_404(client, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("no such collection")

    monkeypatch.setattr(db_admin.inspect, "chroma_items", _raise)
    r = client.get("/api/admin/db/chroma/nope/items")
    assert r.status_code == 404


def test_chroma_item_missing_404(client, monkeypatch):
    monkeypatch.setattr(db_admin.inspect, "chroma_item", lambda *a, **k: None)
    r = client.get("/api/admin/db/chroma/kb_zh/items/zzz")
    assert r.status_code == 404


def test_chroma_items_pagination_params(client, monkeypatch):
    captured = {}

    def _items(name, limit, offset, **kwargs):
        captured["args"] = (name, limit, offset)
        return {"name": name, "total": 0, "items": [], "truncated": False}

    monkeypatch.setattr(db_admin.inspect, "chroma_items", _items)
    r = client.get("/api/admin/db/chroma/kb_zh/items?limit=10&offset=20")
    assert r.status_code == 200
    assert captured["args"] == ("kb_zh", 10, 20)


def test_chroma_items_filter_sort_params_passthrough(client, monkeypatch):
    captured = {}

    def _items(name, limit, offset, **kwargs):
        captured.update(kwargs)
        return {"name": name, "total": 0, "items": [], "truncated": False}

    monkeypatch.setattr(db_admin.inspect, "chroma_items", _items)
    r = client.get(
        "/api/admin/db/chroma/kb_zh/items"
        "?filename_q=alpha&body_q=hello&ts_from=100&ts_to=200&sort_by=ingested_at&desc=true"
    )
    assert r.status_code == 200
    assert captured["filename_q"] == "alpha"
    assert captured["body_q"] == "hello"
    assert captured["ts_from"] == 100
    assert captured["ts_to"] == 200
    assert captured["sort_by"] == "ingested_at"
    assert captured["desc"] is True


def test_chroma_items_limit_out_of_range_422(client):
    # limit 上限 200，超出应被 FastAPI 校验拦下
    r = client.get("/api/admin/db/chroma/kb_zh/items?limit=999")
    assert r.status_code == 422


def test_bm25_docs_missing_index_404(client, monkeypatch):
    monkeypatch.setattr(db_admin.inspect, "bm25_docs", lambda *a, **k: None)
    r = client.get("/api/admin/db/bm25/nope/docs")
    assert r.status_code == 404


def test_bm25_docs_filter_sort_params_passthrough(client, monkeypatch):
    captured = {}

    def _docs(collection, limit, offset, **kwargs):
        captured.update(kwargs)
        return {"collection": collection, "total": 0, "items": []}

    monkeypatch.setattr(db_admin.inspect, "bm25_docs", _docs)
    r = client.get(
        "/api/admin/db/bm25/kb_zh/docs"
        "?filename_q=alpha&body_q=hello&ts_from=100&ts_to=200&sort_by=filename&desc=true"
    )
    assert r.status_code == 200
    assert captured["filename_q"] == "alpha"
    assert captured["body_q"] == "hello"
    assert captured["ts_from"] == 100
    assert captured["ts_to"] == 200
    assert captured["sort_by"] == "filename"
    assert captured["desc"] is True


def test_sqlite_databases_ok(client, monkeypatch):
    monkeypatch.setattr(
        db_admin.inspect, "sqlite_databases",
        lambda: {"databases": [{"key": "auth", "file": "auth.db", "exists": True, "tables": []}]},
    )
    r = client.get("/api/admin/db/sqlite/databases")
    assert r.status_code == 200
    assert r.json()["databases"][0]["key"] == "auth"


def test_sqlite_table_rows_missing_db_404(client, monkeypatch):
    monkeypatch.setattr(db_admin.inspect, "sqlite_table_rows", lambda *a, **k: None)
    r = client.get("/api/admin/db/sqlite/nope/sometable")
    assert r.status_code == 404


def test_sqlite_table_rows_bad_table_404(client, monkeypatch):
    monkeypatch.setattr(
        db_admin.inspect, "sqlite_table_rows",
        lambda *a, **k: {"db_key": "auth", "table": "x", "error": "表不存在"},
    )
    r = client.get("/api/admin/db/sqlite/auth/x")
    assert r.status_code == 404
