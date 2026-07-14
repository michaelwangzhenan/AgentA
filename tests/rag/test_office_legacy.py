"""旧版 Office 解析测试。"""

from pathlib import Path
import zipfile

import pytest

from src.rag.office_legacy import (
    LegacyOfficeParseError,
    parse_legacy_doc,
    parse_legacy_ppt,
    parse_legacy_xls,
    sniff_office_container,
    sniff_office_word_kind,
)
from src.rag.parser import SUPPORTED_EXTENSIONS, parse_file

_OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
_DOC_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


class TestSniffOfficeContainer:
    def test_detects_ole(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.doc"
        path.write_bytes(_OLE_HEADER)
        assert sniff_office_container(path) == "ole"
        assert sniff_office_word_kind(path) == "ole-doc"

    def test_detects_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "misnamed.doc"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", b"<w:document/>")
        assert sniff_office_container(path) == "zip"
        assert sniff_office_word_kind(path) == "docx"


class TestParseLegacyDoc:
    def test_uses_antiword_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "sample.doc"
        path.write_bytes(_OLE_HEADER)

        def _fake_run(cmd, **kwargs):
            class _Result:
                returncode = 0
                stdout = b"Legacy text"
                stderr = b""
            return _Result()

        monkeypatch.setattr("src.rag.office_legacy._find_antiword", lambda: "/usr/bin/antiword")
        monkeypatch.setattr("src.rag.office_legacy._find_soffice", lambda: None)
        monkeypatch.setattr("src.rag.office_legacy.subprocess.run", _fake_run)
        assert parse_legacy_doc(path) == "Legacy text"

    def test_raises_when_no_converter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "sample.doc"
        path.write_bytes(_OLE_HEADER)
        monkeypatch.setattr("src.rag.office_legacy._find_antiword", lambda: None)
        monkeypatch.setattr("src.rag.office_legacy._find_soffice", lambda: None)
        with pytest.raises(LegacyOfficeParseError, match="antiword"):
            parse_legacy_doc(path)


class TestParseLegacyPptXls:
    def test_parse_legacy_ppt_converts_then_uses_pptx_parser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "deck.ppt"
        path.write_bytes(_OLE_HEADER)

        def _fake_convert(src, binary, convert_to, outdir, timeout):
            assert convert_to == "pptx"
            (outdir / "deck.pptx").write_bytes(b"pptx")
            return True

        monkeypatch.setattr("src.rag.office_legacy._require_soffice", lambda: "/usr/bin/soffice")
        monkeypatch.setattr("src.rag.office_legacy._run_libreoffice_convert", _fake_convert)
        monkeypatch.setattr("src.rag.parser._parse_pptx", lambda _: "[Slide 1]\nTitle")

        assert parse_legacy_ppt(path) == "[Slide 1]\nTitle"

    def test_parse_legacy_xls_converts_then_uses_xlsx_parser(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "book.xls"
        path.write_bytes(_OLE_HEADER)

        def _fake_convert(src, binary, convert_to, outdir, timeout):
            assert convert_to == "xlsx"
            (outdir / "book.xlsx").write_bytes(b"xlsx")
            return True

        monkeypatch.setattr("src.rag.office_legacy._require_soffice", lambda: "/usr/bin/soffice")
        monkeypatch.setattr("src.rag.office_legacy._run_libreoffice_convert", _fake_convert)
        monkeypatch.setattr("src.rag.parser._parse_xlsx", lambda _: "[Sheet: A]\n1 | 2")

        assert parse_legacy_xls(path) == "[Sheet: A]\n1 | 2"


class TestParseLegacyEntry:
    def test_supported_extensions_include_legacy_office(self) -> None:
        assert {".doc", ".ppt", ".xls"}.issubset(SUPPORTED_EXTENSIONS)

    def test_misnamed_docx_uses_docx_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "legacy.doc"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                (
                    f'<?xml version="1.0" encoding="UTF-8"?>'
                    f'<w:document {_DOC_NS}><w:body>'
                    f'<w:p><w:r><w:t>from docx</w:t></w:r></w:p>'
                    f"</w:body></w:document>"
                ).encode(),
            )

        parsed = tmp_path / "parsed.txt"
        parsed.write_text("from docx", encoding="utf-8")

        from contextlib import contextmanager

        @contextmanager
        def _fake_parsed_docx_temp(_: Path):
            yield parsed

        monkeypatch.setattr("src.rag.parser.parsed_docx_temp", _fake_parsed_docx_temp)
        assert "from docx" in parse_file(path)

    def test_misnamed_pptx_uses_pptx_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "legacy.ppt"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("ppt/presentation.xml", b"<presentation/>")
        monkeypatch.setattr("src.rag.parser._parse_pptx", lambda _: "slide text")
        assert parse_file(path) == "slide text"

    def test_misnamed_xlsx_uses_xlsx_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "legacy.xls"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", b"<workbook/>")
        monkeypatch.setattr("src.rag.parser._parse_xlsx", lambda _: "sheet text")
        assert parse_file(path) == "sheet text"
