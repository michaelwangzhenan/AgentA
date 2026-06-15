"""KB 端点 UT —— 列表 / 上传 / 删除 / 校验路径

`ingest_one` / `list_kb_documents` / `delete_kb_document` 被 monkeypatch，
不触发真 chromadb / embedding；纯粹测 routes 层的请求 → 响应 + 校验逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import src.config as config
from src.api.main import app


def _sse_events(text: str) -> list[dict[str, Any]]:
    """解析 SSE 文本为事件 dict 列表（只取 data: 行）。"""
    events: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def _final_event(text: str) -> dict[str, Any]:
    """取流里最后的 done / error 事件。"""
    for ev in reversed(_sse_events(text)):
        if ev.get("type") in ("done", "error"):
            return ev
    raise AssertionError(f"SSE 流里没有 done/error 事件：{text!r}")


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


# ─── GET /api/kb/collections（库列表 L1） ─────────────────────────────────


def test_list_collections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """列出全部已定义库，每库带 doc/chunk 计数，默认库 is_default=True。"""
    monkeypatch.setattr(
        "src.api.routes.kb.count_kb_documents",
        lambda model, use_cache=True: (2, 11),
    )
    r = client.get("/api/kb/collections")
    assert r.status_code == 200
    cols = r.json()["collections"]
    aliases = {c["alias"] for c in cols}
    assert aliases == set(config.EMBEDDING_MODELS)
    # 恰好一个被标为默认，且 = config.DEFAULT_EMBEDDING_ALIAS
    defaults = [c["alias"] for c in cols if c["is_default"]]
    assert defaults == [config.DEFAULT_EMBEDDING_ALIAS]
    one = cols[0]
    assert one["doc_count"] == 2
    assert one["chunk_count"] == 11
    assert one["collection"]  # 非空


# ─── GET /api/kb/documents ───────────────────────────────────────────────


def test_list_documents_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", lambda model: [])
    r = client.get("/api/kb/documents")
    assert r.status_code == 200
    assert r.json() == {"documents": []}


def test_list_documents_passes_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?model=zh 应原样透传给 list_kb_documents。"""
    seen: dict[str, str] = {}

    def fake_list(model: str) -> list[dict[str, Any]]:
        seen["model"] = model
        return []

    monkeypatch.setattr("src.api.routes.kb.list_kb_documents", fake_list)
    r = client.get("/api/kb/documents?model=zh")
    assert r.status_code == 200
    assert seen["model"] == "zh"


def test_unknown_model_alias_returns_400(client: TestClient) -> None:
    r = client.get("/api/kb/documents?model=nope")
    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


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
    """安装 fake ingest_one：模拟"单文件入库成功，返回 N chunks"（含进度回调）。"""
    from src.rag.ingest import _doc_id_from_relpath

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str, progress_cb: Any = None
    ) -> dict[str, Any]:
        if progress_cb:
            progress_cb("parse", 0, 0)
            progress_cb("embed", chunks, chunks)
        return {
            "doc_id": _doc_id_from_relpath(Path(file_path).name),
            "chunks": chunks,
            "status": "ingested",
        }

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)
    # 出题改为入库流程内同步执行：测试里 stub 掉，避免真打 LLM（默认返回 2 条）
    monkeypatch.setattr(
        "src.api.routes.kb._generate_golden_sync",
        lambda target_path, safe_name, doc_id: 2,
    )


def test_upload_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    _stub_successful_ingest(monkeypatch, chunks=5)

    files = {"file": ("hello.md", b"# Hello\n\nThis is a test.", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)

    assert r.status_code == 200
    # 流式：含 progress 事件 + 最终 done 事件
    events = _sse_events(r.text)
    assert any(e["type"] == "progress" for e in events)
    # 含 golden 出题相位
    assert any(e["type"] == "progress" and e.get("phase") == "golden" for e in events)
    done = _final_event(r.text)
    assert done["type"] == "done"
    assert done["filename"] == "hello.md"
    assert done["chunks"] == 5
    assert done["doc_id"]  # 非空
    assert done["golden_generated"] == 2
    # 物理文件落到 web_uploads/<alias>/ 子目录下
    saved = _tmp_upload_dir / config.DEFAULT_EMBEDDING_ALIAS / "hello.md"
    assert saved.exists()
    assert saved.read_bytes() == b"# Hello\n\nThis is a test."


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
    # 未传 relpath → 只取 basename，落到 web_uploads/<alias>/inner.md
    alias_root = _tmp_upload_dir / config.DEFAULT_EMBEDDING_ALIAS
    assert (alias_root / "inner.md").exists()
    assert not (alias_root / "subdir").exists()


def test_upload_relpath_preserves_subdir(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """传 relpath 时按相对路径建子目录，落到 web_uploads/<alias>/<relpath>。"""
    _stub_successful_ingest(monkeypatch, chunks=1)

    files = {"file": ("inner.md", b"content", "text/markdown")}
    r = client.post(
        "/api/kb/upload",
        files=files,
        data={"model": "zh", "relpath": "docs/sub/inner.md"},
    )
    assert r.status_code == 200
    assert (_tmp_upload_dir / "zh" / "docs" / "sub" / "inner.md").exists()


def test_upload_relpath_traversal_falls_back_to_basename(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """relpath 含 .. 时回退到 basename，不会逃出 web_uploads/<alias>/。"""
    _stub_successful_ingest(monkeypatch, chunks=1)

    files = {"file": ("inner.md", b"content", "text/markdown")}
    r = client.post(
        "/api/kb/upload",
        files=files,
        data={"relpath": "../../etc/inner.md"},
    )
    assert r.status_code == 200
    alias_root = _tmp_upload_dir / config.DEFAULT_EMBEDDING_ALIAS
    assert (alias_root / "inner.md").exists()
    # 没有逃到上层目录
    assert not (_tmp_upload_dir.parent / "etc" / "inner.md").exists()


def test_upload_parse_returns_empty_chunks_zero(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ingest_one 返回 status='empty'（parse 空内容）→ chunks=0 + message 说明。"""

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str, progress_cb: Any = None
    ) -> dict[str, Any]:
        return {"doc_id": "empty-doc", "chunks": 0, "status": "empty"}

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    done = _final_event(r.text)
    assert done["type"] == "done"
    assert done["chunks"] == 0
    assert "未入库" in done["message"]


def test_upload_skipped_unchanged_returns_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _tmp_upload_dir: Path,
) -> None:
    """ingest_one 返回 status='skipped_unchanged'（内容未变化）→ skipped 标志 + 提示。"""

    def fake_ingest_one(
        file_path: Any, docs_root: Any, model: str, progress_cb: Any = None
    ) -> dict[str, Any]:
        return {"doc_id": "same-doc", "chunks": 7, "status": "skipped_unchanged"}

    monkeypatch.setattr("src.api.routes.kb.ingest_one", fake_ingest_one)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    done = _final_event(r.text)
    assert done["chunks"] == 7
    assert done["skipped_unchanged"] is True
    assert "未变化" in done["message"]


def test_upload_ingest_exception_emits_error_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流已开始后入库抛错 → 作为 SSE error 事件回传（HTTP 仍 200）。"""

    def boom(
        file_path: Any, docs_root: Any, model: str, progress_cb: Any = None
    ) -> dict[str, Any]:
        raise RuntimeError("embedding model offline")

    monkeypatch.setattr("src.api.routes.kb.ingest_one", boom)

    files = {"file": ("hello.md", b"# Hello", "text/markdown")}
    r = client.post("/api/kb/upload", files=files)
    assert r.status_code == 200
    err = _final_event(r.text)
    assert err["type"] == "error"
    assert "embedding model offline" in err["message"]


# ─── DELETE /api/kb/documents/{doc_id} ───────────────────────────────────


def test_delete_document_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.api.routes.kb.delete_kb_document",
        lambda doc_id, model, web_upload_dir=None: (True, 7),
    )
    r = client.delete("/api/kb/documents/abc123")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "chunks_removed": 7}


def test_delete_document_not_found_returns_deleted_false(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.api.routes.kb.delete_kb_document",
        lambda doc_id, model, web_upload_dir=None: (False, 0),
    )
    r = client.delete("/api/kb/documents/not-exist")
    assert r.status_code == 200
    assert r.json() == {"deleted": False, "chunks_removed": 0}


def test_delete_document_passes_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?model=m3 应透传给 delete_kb_document，操作落在所选库。"""
    seen: dict[str, str] = {}

    def fake_delete(
        doc_id: str, model: str, web_upload_dir: Any = None
    ) -> tuple[bool, int]:
        seen["model"] = model
        return True, 3

    monkeypatch.setattr("src.api.routes.kb.delete_kb_document", fake_delete)
    r = client.delete("/api/kb/documents/abc?model=m3")
    assert r.status_code == 200
    assert seen["model"] == "m3"


# ─── DELETE /api/kb/documents（清空全部） ────────────────────────────────


def test_clear_all_documents_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清空 KB → 后端聚合返回 docs/chunks/files 三项统计。"""
    monkeypatch.setattr(
        "src.api.routes.kb.delete_all_kb_documents",
        lambda model, web_upload_dir=None: {
            "docs_removed": 3,
            "chunks_removed": 42,
            "files_removed": 3,
        },
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
        lambda model, web_upload_dir=None: {
            "docs_removed": 0,
            "chunks_removed": 0,
            "files_removed": 0,
        },
    )
    r = client.delete("/api/kb/documents")
    assert r.status_code == 200
    body = r.json()
    assert body == {"docs_removed": 0, "chunks_removed": 0, "files_removed": 0}
