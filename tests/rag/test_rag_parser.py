"""
测试：文档解析层

测试内容：
    - rag/parser.py：各格式文档解析（md/txt/html/docx/pptx/xlsx）
    - 边界情况：不支持的格式、不存在的文件
"""

from pathlib import Path
import zipfile

import pytest

from src.rag.parser import (
    DocxParseError,
    SUPPORTED_EXTENSIONS,
    inspect_docx_uncompressed_size,
    parse_file,
)

# 测试文档目录（test_sample.* 实际存放在 datasets/data_en/test/ 下）
DOCS_DIR = Path(__file__).resolve().parents[2] / "datasets" / "data_en" / "test"


class TestSupportedExtensions:
    """测试支持的文件格式集合"""

    def test_all_expected_formats_supported(self) -> None:
        expected = {".md", ".txt", ".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx"}
        assert expected == SUPPORTED_EXTENSIONS

    def test_unsupported_format_raises_value_error(self, tmp_path: Path) -> None:
        f = tmp_path / "test.csv"
        f.write_text("a,b,c")
        with pytest.raises(ValueError, match="不支持的文件格式"):
            parse_file(f)

    def test_nonexistent_file_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_file("nonexistent_file_xyz.md")

    def test_office_temp_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "~$draft.docx"
        path.write_bytes(b"placeholder")
        with pytest.raises(ValueError, match="Office 临时文件"):
            parse_file(path)

    def test_docx_hard_limit_is_checked(self, tmp_path: Path) -> None:
        path = tmp_path / "large.docx"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"x" * 4096)
        with pytest.raises(DocxParseError, match="解压后过大"):
            inspect_docx_uncompressed_size(path, max_bytes=1024)


class TestParseMarkdown:
    def test_parse_md_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.md")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_md_contains_expected_content(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.md")
        assert "RAG" in result or "知识库" in result


class TestParseTxt:
    def test_parse_txt_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.txt")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_txt_contains_expected_content(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.txt")
        assert "RAG" in result


class TestParseHtml:
    def test_parse_html_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.html")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_html_filters_script_and_nav(self) -> None:
        """script、nav、footer 标签内容应被过滤"""
        result = parse_file(DOCS_DIR / "test_sample.html")
        assert "console.log" not in result
        assert "导航栏（应被过滤）" not in result
        assert "页脚（应被过滤）" not in result

    def test_parse_html_preserves_body_content(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.html")
        assert "ChromaDB" in result


@pytest.mark.slow  # 真实加载 python-docx 解析 Office 文件，慢
class TestParseDocx:
    def test_parse_docx_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.docx")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_docx_contains_expected_content(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.docx")
        assert "LLM" in result


@pytest.mark.slow  # 真实加载 python-pptx 解析 Office 文件，慢
class TestParsePptx:
    def test_parse_pptx_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.pptx")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_pptx_contains_slide_markers(self) -> None:
        """每张 slide 应有 [Slide N] 标记"""
        result = parse_file(DOCS_DIR / "test_sample.pptx")
        assert "[Slide 1]" in result
        assert "[Slide 2]" in result

    def test_parse_pptx_contains_expected_content(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.pptx")
        assert "Agent" in result


@pytest.mark.slow  # 真实加载 openpyxl 解析 Office 文件，慢
class TestParseXlsx:
    def test_parse_xlsx_returns_nonempty_string(self) -> None:
        result = parse_file(DOCS_DIR / "test_sample.xlsx")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_xlsx_contains_sheet_marker(self) -> None:
        """每个 sheet 应有 [Sheet: ...] 标记"""
        result = parse_file(DOCS_DIR / "test_sample.xlsx")
        assert "[Sheet:" in result

    def test_parse_xlsx_uses_pipe_separator(self) -> None:
        """列之间应使用 | 分隔"""
        result = parse_file(DOCS_DIR / "test_sample.xlsx")
        assert " | " in result
