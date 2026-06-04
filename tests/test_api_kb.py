"""KB 端点 UT —— 列表 / 上传 / 删除 / 校验路径

`ingest_all` / `list_kb_documents` / `delete_kb_document` 被 monkeypatch，
不触发真 chromadb / embedding；纯粹测 routes 层的请求 → 响应 + 校验逻辑。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.api.main import app


@pytest.fixture(autouse=True)
def _tmp_upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """每个测试用独立临时 upload dir，避免污染真磁盘。"""
    target = tmp_path / "web_uploads"
    monkeypatch.setattr(config, "WEB_UPLOAD_DIR", str(target))
    # routes/kb.py 内部用 config.WEB_UPLOAD_DIR / config.WEB_MAX_UPLOAD_MB
    # 也防止 ingest.py 内部读到旧值
    yield target


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ─── GET /api/kb/documents ───────────────────────────────────────────────


def test_list_documents_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", lambda model: [])
    r = client.get("/api/kb/documents")
    assert r.status_code == 200
    assert r.json() == {"documents": []}


def test_list_documents_aggregated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_docs: list[dict[str, Any]] = [
        {
            "doc_id": "abc123",
            "filename": "intro.md",
            "source": "intro.md",
            "ext": ".md",
            "lang": "en",
            "mtime": 1700000000.0,
            "chunks": 5,
            "total_chars": 1234,
        },
        {
            "doc_id": "def456",
            "filename": "guide.pdf",
            "source": "guide.pdf",
            "ext": ".pdf",
            "lang": "zh",
            "mtime": 1700000100.0,
            "chunks": 12,
            "total_chars": 4567,
        },
    ]
    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", lambda model: fake_docs)

    r = client.get("/api/kb/documents")
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert len(docs) == 2
    assert docs[0]["doc_id"] == "abc123"
    assert docs[0]["filename"] == "intro.md"
    assert docs[0]["chunks"] == 5
    assert docs[1]["lang"] == "zh"


# ─── POST /api/kb/upload ─────────────────────────────────────────────────


def _stub_successful_ingest(monkeypatch: pytest.MonkeyPatch, chunks: int = 3) -> None:
    """安装 fake ingest_all + list_kb_documents：模拟"上传后查到 doc_id 有 N chunks"。"""
    monkeypatch.setattr(
        "src.api.routes.kb.ingest_all", lambda docs_dir, model: None
    )

    def fake_list(model: str) -> list[dict[str, Any]]:
        from src.api.routes.kb import _doc_id_from_relpath

        return [
            {
                "doc_id": _doc_id_from_relpath("hello.md"),
                "filename": "hello.md",
                "source": "hello.md",
                "ext": ".md",
                "lang": "en",
                "mtime": 1700000000.0,
                "chunks": chunks,
                "total_chars": 100,
            }
        ]

    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", fake_list)


def test_upload_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    _stub_successful_ingest(monkeypatch, chunks=5)

    files = {"file": ("hello.md", b"# Hello\n\nThis is a test.", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)

    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "hello.md"
    assert body["chunks"] == 5
    assert body["doc_id"]  # 非空
    # 物理文件已落盘
    assert (_tmp_upload_dir / "hello.md").exists()
    assert (_tmp_upload_dir / "hello.md").read_bytes() == b"# Hello\n\nThis is a test."


def test_upload_unsupported_extension_returns_415(client: TestClient) -> None:
    files = {"file": ("payload.exe", b"\x4d\x5a\x90", "application/octet-stream")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 415
    assert ".exe" in r.json()["detail"]


def test_upload_empty_file_returns_422(client: TestClient) -> None:
    files = {"file": ("empty.md", b"", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 422


def test_upload_too_large_returns_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "WEB_MAX_UPLOAD_MB", 1)  # 1 MB 上限
    big = b"x" * (1024 * 1024 + 100)  # 1 MB + 一点点
    files = {"file": ("big.md", big, "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 413


def test_upload_path_traversal_filename_is_stripped(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """传 `../../etc/passwd` 这类文件名时只取 basename，不能跳出 upload dir。"""
    _stub_successful_ingest(monkeypatch, chunks=1)

    files = {"file": ("../../../etc/passwd", b"sensitive", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    # 应该被 415 拦掉（.passwd 不在 SUPPORTED_EXTENSIONS）
    assert r.status_code == 415


def test_upload_filename_with_subdir_only_keeps_basename(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """`subdir/file.md` 这种文件名应只取 basename `file.md`，落到 upload_dir 根下。"""
    _stub_successful_ingest(monkeypatch, chunks=1)

    files = {"file": ("subdir/inner.md", b"content", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    # 只在根目录有 inner.md，subdir 不该被创建
    assert (_tmp_upload_dir / "inner.md").exists()
    assert not (_tmp_upload_dir / "subdir").exists()


def test_upload_parse_returns_empty_chunks_zero(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ingest 后查不到 doc_id（parse 空内容 / 跳过）→ chunks=0 + message 说明。"""
    monkeypatch.setattr("src.api.routes.kb.ingest_all", lambda docs_dir, model: None)
    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", lambda model: [])

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == 0
    assert "未入库" in body["message"]


def test_upload_ingest_exception_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(docs_dir: str, model: str) -> None:
        raise RuntimeError("embedding model offline")

    monkeypatch.setattr("src.api.routes.kb.ingest_all", boom)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 500
    assert "embedding model offline" in r.json()["detail"]


# ─── DELETE /api/kb/documents/{doc_id} ───────────────────────────────────


def test_delete_document_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.api.routes.kb.delete_kb_document",
        lambda doc_id, model: (True, 7),
    )
    r = client.delete("/api/kb/documents/abc123")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "chunks_removed": 7}


def test_delete_document_not_found_returns_deleted_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.api.routes.kb.delete_kb_document",
        lambda doc_id, model: (False, 0),
    )
    r = client.delete("/api/kb/documents/not-exist")
    assert r.status_code == 200
    assert r.json() == {"deleted": False, "chunks_removed": 0}
