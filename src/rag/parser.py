"""
文档解析模块 —— 离线预处理阶段使用

将各种格式的本地文档转换为纯文本字符串，供 ingest.py 后续分块和向量化使用。
支持格式：.md / .txt / .html / .pdf / .docx / .pptx / .xlsx

注意：网页 URL 的实时抓取由 agent/tools.py 中的 fetch_url 工具负责，
      本模块只处理本地文件。
"""

import logging
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


# 支持的文件扩展名集合
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx"}
)


# ── 解析后内容清洗 ────────────────────────────────────────────────────────────
# 目的：去除 PDF/Word/PPT 等长文档常见的"模板噪声"——页眉页脚、版权声明、纯页码、
# PPT 母版水印等。这些片段会大量重复进入向量库，污染相似度计算并稀释命中率。

# 满足以下任一条件即被视为"模板/页脚噪声"：
#   1. 整行只有数字、日期、版权字符（©/® 等）
#   2. 整行匹配典型页码模式（"1 / 32" 或 "Page 5 of 10"）
#   3. 整行匹配典型版权/保留声明（不限语种）
_NOISE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\d+\s*$"),                                # 纯页码
    re.compile(r"^\s*[-–—•·]+\s*\d+\s*[-–—•·]+\s*$"),          # "- 12 -" / "— 12 —"
    re.compile(r"^\s*page\s+\d+\s*(of\s+\d+)?\s*$", re.I),      # Page 5 / Page 5 of 10
    re.compile(r"^\s*\d+\s*[/／]\s*\d+\s*$"),                    # 1/32 / 1／32
    re.compile(r"©|®|™"),                                       # 含版权符号
    re.compile(r"copyright|all\s+rights\s+reserved", re.I),     # 英文版权
    re.compile(r"版权所有|保留所有权利|未经.*授权.*禁止"),      # 中文版权
)

# 短行重复阈值：一份文档中重复出现 ≥ N 次的短行（≤ MAX_LEN 字符）视为页眉页脚/水印
_NOISE_REPEAT_THRESHOLD: int = 5
_NOISE_MAX_LEN: int = 80


def _is_noise_line(line: str) -> bool:
    """单行级别的噪声检测（不需要全文上下文，纯规则）。"""
    s = line.strip()
    if not s:
        return False
    return any(p.search(s) for p in _NOISE_LINE_PATTERNS)


def _clean_extracted_text(text: str) -> str:
    """
    解析层最后一步：去除模板/页脚类噪声，避免污染向量库。

    清洗策略：
      1. 整行规则匹配：纯页码、页码模式、版权声明 → 直接丢弃。
      2. 跨页重复：在长文档中重复出现 ≥ _NOISE_REPEAT_THRESHOLD 次的"短行"
         （length ≤ _NOISE_MAX_LEN）视为页眉页脚/水印 → 丢弃。
      3. 多余空行折叠为最多一个空行（避免 chunk 中出现大段空白）。
    """
    if not text or not text.strip():
        return ""

    raw_lines = text.splitlines()

    # Pass 1: 统计短行重复次数
    short_line_counts: Counter[str] = Counter()
    for line in raw_lines:
        s = line.strip()
        if 0 < len(s) <= _NOISE_MAX_LEN:
            short_line_counts[s] += 1

    repeated_noise: set[str] = {
        s for s, n in short_line_counts.items() if n >= _NOISE_REPEAT_THRESHOLD
    }

    # Pass 2: 应用规则 + 重复短行 + 空行折叠
    cleaned: list[str] = []
    prev_blank = False
    dropped = 0
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            if not prev_blank:
                cleaned.append("")
                prev_blank = True
            continue
        if _is_noise_line(stripped) or stripped in repeated_noise:
            dropped += 1
            continue
        cleaned.append(stripped)
        prev_blank = False

    if dropped > 0:
        logger.debug("[parser] 清洗丢弃噪声行 %d 条", dropped)

    return "\n".join(cleaned).strip()


def parse_file(file_path: str | Path) -> str:
    """
    解析单个本地文件，返回纯文本字符串。

    Args:
        file_path: 文件路径（支持字符串或 Path 对象）。

    Returns:
        文件的纯文本内容，末尾不带多余空白。

    Raises:
        ValueError: 文件格式不受支持。
        FileNotFoundError: 文件不存在。
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件格式: '{suffix}'，"
            f"支持的格式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    match suffix:
        case ".md" | ".txt":
            raw = _parse_text(path)
        case ".html" | ".htm":
            raw = _parse_html(path)
        case ".pdf":
            raw = _parse_pdf(path)
        case ".docx":
            raw = _parse_docx(path)
        case ".pptx":
            raw = _parse_pptx(path)
        case ".xlsx":
            raw = _parse_xlsx(path)
        case _:
            # 理论上不会到这里，frozenset 已经过滤
            raise ValueError(f"未处理的格式: '{suffix}'")

    # 统一过一遍模板/页脚噪声清洗，避免污染下游 chunk → embedding
    return _clean_extracted_text(raw)


# ── 各格式解析实现 ────────────────────────────────────────────────────────────


def _parse_text(path: Path) -> str:
    """解析 .md / .txt：直接读取文本，自动检测编码。"""
    # 优先 utf-8，失败则回退 gbk（兼容 Windows 中文环境）
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding).strip()
        except UnicodeDecodeError:
            continue
    # latin-1 是超集，理论上不会失败，此处保险起见
    return path.read_text(errors="replace").strip()


def _parse_html(path: Path) -> str:
    """解析 .html / .htm：用 BeautifulSoup 提取正文，去除脚本和样式标签。"""
    from bs4 import BeautifulSoup

    html = _parse_text(path)
    soup = BeautifulSoup(html, "lxml")

    # 移除不需要的标签
    for tag in soup(["script", "style", "head", "nav", "footer", "aside"]):
        tag.decompose()

    # 提取纯文本，保留段落间空行
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    # 过滤空行连续出现（最多保留一个空行）
    result: list[str] = []
    prev_blank = False
    for line in lines:
        if line:
            result.append(line)
            prev_blank = False
        elif not prev_blank:
            result.append("")
            prev_blank = True

    return "\n".join(result).strip()


def _parse_pdf(path: Path) -> str:
    """解析 .pdf：用 pypdf 逐页提取文本，页间用换行分隔。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages.append(text)

    return "\n\n".join(pages).strip()


def _parse_docx(path: Path) -> str:
    """解析 .docx：提取所有段落文本，保留段落换行。"""
    from docx import Document

    doc = Document(str(path))
    paragraphs = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    return "\n\n".join(paragraphs).strip()


def _parse_pptx(path: Path) -> str:
    """解析 .pptx：遍历所有 slide 的所有 shape，提取文本框内容。"""
    from pptx import Presentation

    prs = Presentation(str(path))
    slides_text: list[str] = []

    for slide_idx, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:  # type: ignore[union-attr]
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        texts.append(line)
        if texts:
            slides_text.append(f"[Slide {slide_idx}]\n" + "\n".join(texts))

    return "\n\n".join(slides_text).strip()


def _parse_xlsx(path: Path) -> str:
    """
    解析 .xlsx：遍历所有 sheet 的所有行，
    每行转为 '列1 | 列2 | 列3 ...' 格式，便于语义检索。
    """
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    sheets_text: list[str] = []

    for sheet in wb.worksheets:
        rows_text: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell).strip() if cell is not None else "" for cell in row]
            # 跳过全空行
            if any(cells):
                rows_text.append(" | ".join(cells))
        if rows_text:
            sheets_text.append(f"[Sheet: {sheet.title}]\n" + "\n".join(rows_text))

    wb.close()
    return "\n\n".join(sheets_text).strip()
