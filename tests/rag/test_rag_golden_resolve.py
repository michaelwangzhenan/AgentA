"""golden 出题正文来源解析（resolve_golden_input）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.config as config
import src.rag.ingest as ingest


def test_resolve_golden_input_prefers_web_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload_root = tmp_path / "web_uploads"
    (upload_root / "m3").mkdir(parents=True)
    (upload_root / "m3" / "a.md").write_text("web", encoding="utf-8")
    monkeypatch.setattr(config, "WEB_UPLOAD_DIR", str(upload_root))
    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "datasets"))

    file_path, text = ingest.resolve_golden_input("m3", "a.md", "doc-1")
    assert file_path is not None
    assert text is None
    assert file_path.name == "a.md"


def test_resolve_golden_input_falls_back_to_docs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_dir = tmp_path / "datasets" / "data_en"
    docs_dir.mkdir(parents=True)
    (docs_dir / "cli.md").write_text("cli", encoding="utf-8")
    monkeypatch.setattr(config, "WEB_UPLOAD_DIR", str(tmp_path / "web_uploads"))
    monkeypatch.setattr(config, "DOCS_DIR", str(docs_dir))

    file_path, text = ingest.resolve_golden_input("en", "cli.md", "doc-2")
    assert file_path is not None
    assert text is None


def test_resolve_golden_input_uses_ingest_history_docs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_dir = tmp_path / "custom_3gpp"
    custom_dir.mkdir()
    (custom_dir / "spec.docx").write_text("spec", encoding="utf-8")

    chroma_path = tmp_path / "chroma"
    chroma_path.mkdir()
    hist = {
        "collections": {
            "kb_m3": {
                "docs_dirs": [str(custom_dir)],
            }
        }
    }
    (chroma_path / "ingest_history.json").write_text(
        json.dumps(hist), encoding="utf-8"
    )

    monkeypatch.setattr(config, "WEB_UPLOAD_DIR", str(tmp_path / "web_uploads"))
    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "missing_default"))
    monkeypatch.setattr(config, "CHROMA_DB_PATH", str(chroma_path))

    file_path, text = ingest.resolve_golden_input("m3", "spec.docx", "doc-3")
    assert file_path is not None
    assert file_path.parent == custom_dir.resolve()
