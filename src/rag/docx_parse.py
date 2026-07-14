"""DOCX 流式解析：大文件逐段读 word/document.xml，避免整包载入内存。"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_NAME_RE = re.compile(r"heading\s*(\d)", re.I)
_HEADING_STYLE_ID_RE = re.compile(r"(?i)heading\s*(\d)")


def _attr(elem: ET.Element, name: str) -> str | None:
    return elem.get(_W_NS + name) or elem.get(name)


def _load_heading_style_map(archive: zipfile.ZipFile) -> dict[str, int]:
    """从 styles.xml 建立 styleId → 标题级别映射。"""
    mapping: dict[str, int] = {}
    try:
        with archive.open("word/styles.xml") as raw:
            for _event, elem in ET.iterparse(raw, events=("end",)):
                if elem.tag != _W_NS + "style":
                    continue
                style_id = _attr(elem, "styleId")
                name_el = elem.find(_W_NS + "name")
                name = _attr(name_el, "val") if name_el is not None else ""
                if style_id and name:
                    m = _HEADING_NAME_RE.search(name)
                    if m:
                        mapping[style_id] = int(m.group(1))
                elem.clear()
    except KeyError:
        pass
    return mapping


def _paragraph_style_id(paragraph: ET.Element) -> str | None:
    p_pr = paragraph.find(_W_NS + "pPr")
    if p_pr is None:
        return None
    p_style = p_pr.find(_W_NS + "pStyle")
    if p_style is None:
        return None
    return _attr(p_style, "val")


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter(_W_NS + "t"):
        if node.text:
            parts.append(node.text)
    return "".join(parts).strip()


def _heading_level(style_id: str | None, heading_map: dict[str, int]) -> int | None:
    if not style_id:
        return None
    if style_id in heading_map:
        return heading_map[style_id]
    m = _HEADING_STYLE_ID_RE.search(style_id)
    if m:
        return int(m.group(1))
    return None


def iter_docx_paragraphs(path: Path) -> Iterator[tuple[int | None, str]]:
    """逐段产出 (heading_level, text)；大文件友好，峰值内存与单段规模相关。"""
    with zipfile.ZipFile(path) as archive:
        heading_map = _load_heading_style_map(archive)
        with archive.open("word/document.xml") as raw:
            for _event, elem in ET.iterparse(raw, events=("end",)):
                if elem.tag != _W_NS + "p":
                    continue
                text = _paragraph_text(elem)
                if text:
                    yield _heading_level(_paragraph_style_id(elem), heading_map), text
                elem.clear()


def stream_docx_to_stdout(path: Path) -> None:
    """流式写出与 _parse_docx 相同格式的 Markdown 文本。"""
    from src.rag.parser import _is_noise_line

    for level, text in iter_docx_paragraphs(path):
        if _is_noise_line(text):
            continue
        if level:
            sys.stdout.write(f"{'#' * level} {text}\n\n")
        else:
            sys.stdout.write(f"{text}\n\n")
    sys.stdout.flush()


def parse_docx_streaming(path: Path) -> str:
    """内存版流式解析（测试与小工具用）。"""
    lines: list[str] = []
    from src.rag.parser import _is_noise_line

    for level, text in iter_docx_paragraphs(path):
        if _is_noise_line(text):
            continue
        if level:
            lines.append(f"{'#' * level} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines).strip()
