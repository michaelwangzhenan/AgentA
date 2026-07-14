"""DOCX 流式解析单元测试。"""

from pathlib import Path
import zipfile

import pytest

from src.rag.docx_parse import iter_docx_paragraphs, parse_docx_streaming
from src.rag.parser import (
    DocxParseError,
    assert_docx_hard_limit,
    docx_needs_streaming,
    measure_docx_uncompressed_size,
)

_DOC_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _write_docx(path: Path, document_xml: bytes, *, extra: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        if extra:
            for name, content in extra.items():
                archive.writestr(name, content)


def _paragraph(text: str, style: str | None = None) -> str:
    style_xml = f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""
    return (
        f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _document(*paragraphs: str) -> bytes:
    body = "".join(paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_DOC_NS}><w:body>{body}</w:body></w:document>'
    ).encode()


class TestDocxStreamingParse:
    def test_iter_paragraphs_plain_text(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.docx"
        _write_docx(path, _document(_paragraph("Hello"), _paragraph("World")))
        items = list(iter_docx_paragraphs(path))
        assert items == [(None, "Hello"), (None, "World")]

    def test_iter_paragraphs_heading_style_id(self, tmp_path: Path) -> None:
        path = tmp_path / "heading.docx"
        _write_docx(path, _document(_paragraph("Chapter", "Heading1"), _paragraph("Body")))
        items = list(iter_docx_paragraphs(path))
        assert items[0] == (1, "Chapter")
        assert items[1] == (None, "Body")

    def test_parse_streaming_formats_markdown(self, tmp_path: Path) -> None:
        path = tmp_path / "md.docx"
        _write_docx(path, _document(_paragraph("Title", "Heading2"), _paragraph("Content")))
        text = parse_docx_streaming(path)
        assert "## Title" in text
        assert "Content" in text

    def test_docx_needs_streaming_above_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.config as config

        monkeypatch.setattr(config, "DOCX_MAX_UNZIP_MB", 1)
        path = tmp_path / "big.docx"
        _write_docx(path, b"x" * (2 * 1024 * 1024))
        assert docx_needs_streaming(path) is True

    def test_docx_needs_streaming_below_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.config as config

        monkeypatch.setattr(config, "DOCX_MAX_UNZIP_MB", 64)
        path = tmp_path / "small.docx"
        _write_docx(path, _document(_paragraph("ok")))
        assert docx_needs_streaming(path) is False

    def test_hard_limit_rejects_zip_bomb(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.config as config

        monkeypatch.setattr(config, "DOCX_HARD_MAX_UNZIP_MB", 1)
        path = tmp_path / "huge.docx"
        _write_docx(path, b"x" * (2 * 1024 * 1024))
        with pytest.raises(DocxParseError, match="硬上限"):
            assert_docx_hard_limit(path)

    def test_measure_size_without_reject(self, tmp_path: Path) -> None:
        path = tmp_path / "measure.docx"
        payload = b"x" * 4096
        _write_docx(path, payload)
        assert measure_docx_uncompressed_size(path) == len(payload)
