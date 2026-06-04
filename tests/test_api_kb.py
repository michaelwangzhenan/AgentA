"""KB 端点 UT —— 列表 / 上传 / 删除 / 校验路径

`ingest_one` / `list_kb_documents` / `delete_kb_document` 被 monkeypatch，
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
            "ingested_at": 1700001000.0,
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
            "ingested_at": 1700002000.0,
            "chunks": 12,
            "total_chars": 4567,
        },
        {
            # 老数据无 ingested_at → 后端 fallback 0.0
            "doc_id": "legacy",
            "filename": "old.md",
            "source": "old.md",
            "ext": ".md",
            "lang": "en",
            "mtime": 1690000000.0,
            "chunks": 2,
            "total_chars": 100,
        },
    ]
    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", lambda model: fake_docs)

    r = client.get("/api/kb/documents")
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert len(docs) == 3
    assert docs[0]["doc_id"] == "abc123"
    assert docs[0]["filename"] == "intro.md"
    assert docs[0]["chunks"] == 5
    assert docs[0]["ingested_at"] == 1700001000.0
    assert docs[1]["lang"] == "zh"
    # 老数据缺字段 → 0.0 fallback
    assert docs[2]["ingested_at"] == 0.0


# ─── POST /api/kb/upload ─────────────────────────────────────────────────


def _stub_successful_ingest(monkeypatch: pytest.MonkeyPatch, chunks: int = 3) -> None:
    """安装 fake ingest_one：模拟"单文件入库成功，返回 N chunks"。"""
    from src.rag.ingest import _doc_id_from_relpath

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str
    ) -> dict[str, Any]:
        return {
            "doc_id": _doc_id_from_relpath(Path(file_path).name),
            "chunks": chunks,
            "status": "ingested",
        }

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)


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
    """ingest_one 返回 status='empty'（parse 空内容）→ chunks=0 + message 说明。"""

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str
    ) -> dict[str, Any]:
        return {"doc_id": "empty-doc", "chunks": 0, "status": "empty"}

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == 0
    assert "未入库" in body["message"]


def test_upload_skipped_unchanged_returns_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """ingest_one 返回 status='skipped_unchanged'（内容未变化）→ skipped 标志 + 提示。"""

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str
    ) -> dict[str, Any]:
        return {"doc_id": "same-doc", "chunks": 7, "status": "skipped_unchanged"}

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == 7
    assert body["skipped_unchanged"] is True
    assert "未变化" in body["message"]


def test_upload_ingest_exception_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(file_path: Any, docs_root: Any, model: str) -> dict[str, Any]:
        raise RuntimeError("embedding model offline")

    monkeypatch.setattr("src.api.routes.kb.ingest_one", boom)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 500
    assert "embedding model offline" in r.json()["detail"]


def test_upload_ingest_timeout_returns_504(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """ingest 阻塞超过 WEB_INGEST_TIMEOUT_SEC → 504，避免前端无限等。"""
    import time

    monkeypatch.setattr(config, "WEB_INGEST_TIMEOUT_SEC", 1)

    def slow_ingest(file_path: Any, docs_root: Any, model: str) -> dict[str, Any]:
        time.sleep(3)  # 远超 1s 超时
        return {"doc_id": "x", "chunks": 1, "status": "ingested"}

    monkeypatch.setattr("src.api.routes.kb.ingest_one", slow_ingest)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 504
    assert "超时" in r.json()["detail"]


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


# ─── DELETE /api/kb/documents（清空全部） ────────────────────────────────


def test_clear_all_documents_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清空 KB → 后端聚合返回 docs/chunks/files 三项统计。"""
    monkeypatch.setattr(
        "src.api.routes.kb.delete_all_kb_documents",
        lambda model: {"docs_removed": 3, "chunks_removed": 42, "files_removed": 3},
    )
    r = client.delete("/api/kb/documents")
    assert r.status_code == 200
    assert r.json() == {
        "docs_removed": 3,
        "chunks_removed": 42,
        "files_removed": 3,
    }


def test_clear_all_documents_empty_kb_returns_zeros(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 KB 调用清空 → 幂等，返回全 0，不抛错。"""
    monkeypatch.setattr(
        "src.api.routes.kb.delete_all_kb_documents",
        lambda model: {"docs_removed": 0, "chunks_removed": 0, "files_removed": 0},
    )
    r = client.delete("/api/kb/documents")
    assert r.status_code == 200
    body = r.json()
    assert body == {"docs_removed": 0, "chunks_removed": 0, "files_removed": 0}
