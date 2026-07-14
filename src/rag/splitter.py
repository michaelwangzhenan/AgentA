"""
结构化文本分块模块

提供两个层次的分块能力：
    1. split_text(text, size, overlap)
       — 类 LangChain RecursiveCharacterTextSplitter：按 \n\n → \n → 句号 → 空格 → 字符
       逐级回退切分；只把文本切到不超过 size 的"原子单元"，再贪心打包成 chunk，相邻
       chunk 间保留 overlap 字符。无任何分隔符时退化为按字符等步长切分。

    2. split_structured(text, size, overlap)
       — 在 split_text 之上识别两类锚点并保留结构信息：
         · "[[PAGE:N]]" 独占一行（由 parser 在 PDF/PPTX 解析时插入）→ 切分点 + page_no 元数据；
         · Markdown 风格的 "#"~"######" 标题行 → 切分点 + heading_path 路径。
       对每个 section.body 走 split_text；最终 Chunk.text 自动注入"父级标题路径"前缀，
       让 chunk 自带"我在第几章/第几页/讲什么"的语义锚点，显著提升相似度命中率。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

# PAGE 标记必须独占一行：[[PAGE:5]]；行尾允许残留空白
_PAGE_RE = re.compile(r"^\s*\[\[PAGE:(\d+)\]\]\s*$")
# Markdown 标题行：1~6 个 # 后跟空格再跟标题正文
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass
class Chunk:
    """
    结构化分块单元，由 split_structured() 产出，供 ingest 写入向量库。

    Attributes:
        text:         已注入"父级标题路径 + 空行 + 正文"前缀的最终文本，直接用于 embedding。
        heading_path: 当前 chunk 所属的标题层级路径（按文档顺序），如 ["第 5 章", "5.2 物理层"]。
        page_no:      所属页号（PDF/PPTX 解析时通过 [[PAGE:N]] 标记带入），无则为 None。
        line_start:   1-based 起始行号（在原始解析文本中的行号，用于人工溯源）。
        line_end:     1-based 结束行号（含）。
    """

    text: str
    heading_path: list[str] = field(default_factory=list)
    page_no: int | None = None
    line_start: int = 0
    line_end: int = 0


# ── 第 1 层：纯文本递归分块 ────────────────────────────────────────────────────


def _split_by_sep(text: str, sep: str) -> list[str] | None:
    """
    用 sep 切分 text 并保留分隔符；返回切分后的 piece 列表。

    防递归死循环关键：若 sep 仅出现在文本末尾（split 后只有一个非空 part + 末尾空串），
    把 sep 加回 piece 会等于原 text，递归调用栈不会收敛 → RecursionError。
    本函数检测到该情形（有效 piece 数 < 2）时返回 None，让调用方 fall through 到下一级 sep。
    """
    if sep not in text:
        return None
    parts = text.split(sep)
    pieces: list[str] = []
    for i, p in enumerate(parts):
        if not p:
            continue
        # 该 part 后面在原文里跟着 sep 的当且仅当它不是最后一个 part
        piece = p + sep if i < len(parts) - 1 else p
        pieces.append(piece)
    if len(pieces) < 2:
        return None
    return pieces


def _split_into_atoms(text: str, chunk_size: int) -> list[str]:
    """
    按语义边界把文本递归拆成不超过 chunk_size 的"原子单元"。

    优先级：段落（\\n\\n） > 行（\\n） > 中文/英文句号 > 空格 > 字符。
    每一级都保留分隔符，避免拼回时丢失语义边界。最后一级是字符级硬切（兜底）。

    所有分支都走 _split_by_sep，遇到"sep 仅在末尾出现一次"等无法实际切短文本的情形
    会自动跳到下一级 sep，防止递归不收敛。
    """
    if len(text) <= chunk_size:
        return [text]

    # 段落 → 行 → 中英文断句 → 空格，依次尝试；任何一级能真正切分（≥ 2 个有效 piece）就用它。
    for sep in ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", " "):
        pieces = _split_by_sep(text, sep)
        if pieces is None:
            continue
        result: list[str] = []
        for piece in pieces:
            result.extend(_split_into_atoms(piece, chunk_size))
        return result

    # 字符级兜底（无任何分隔符 / 所有 sep 都仅在末尾出现一次）
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _greedy_pack(atoms: list[str], chunk_size: int, overlap: int) -> list[str]:
    """
    将原子单元贪心打包成 ≤ chunk_size 的 chunk；相邻 chunk 间保留 overlap 字符尾巴。

    overlap 的实现方式：每次切出一块后，把 current 的最后 overlap 字符作为下一块开头。
    若 tail + atom 仍 > chunk_size，则放弃 tail（仅保留 atom），保证 chunk 不超长。

    边界用例：原子单元自身已 > chunk_size（不应发生，但防御性处理：直接字符级切）。
    """
    chunks: list[str] = []
    current = ""
    for atom in atoms:
        if not atom:
            continue
        if len(atom) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            step = max(chunk_size - overlap, 1)
            for i in range(0, len(atom), step):
                piece = atom[i : i + chunk_size]
                if piece:
                    chunks.append(piece)
            continue

        if len(current) + len(atom) <= chunk_size:
            current += atom
        else:
            if current:
                chunks.append(current)
                tail = current[-overlap:] if overlap > 0 else ""
                merged = tail + atom
                current = merged if len(merged) <= chunk_size else atom
            else:
                current = atom

    if current:
        chunks.append(current)
    return chunks


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    递归字符分块（无结构识别）入口。

    切分策略：
      - 优先在 \\n\\n / \\n / 句号 / 空格 处切分，避免在词中间断开；
      - 仅当文本无任何分隔符时才退化为按字符等步长切分。
    """
    if not text or not text.strip():
        return []
    atoms = _split_into_atoms(text, chunk_size)
    return _greedy_pack(atoms, chunk_size, overlap)


# ── 第 2 层：识别 PAGE 锚点 + Markdown 标题的结构化分块 ──────────────────────


def _heading_breadcrumb(stack: list[tuple[int, str]]) -> str:
    """把 (level, title) 栈拼成 Markdown 风格的路径前缀。"""
    if not stack:
        return ""
    return "\n".join(f"{'#' * level} {title}" for level, title in stack)


def _split_section(
    stack: list[tuple[int, str]],
    page_no: int | None,
    body: list[str],
    line_start: int,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """切分一个标题区段；调用结束后即可释放该区段正文。"""
    body_text = "\n".join(body).strip()
    if not body_text:
        return []
    breadcrumb = _heading_breadcrumb(stack)
    budget = chunk_size - (len(breadcrumb) + 2 if breadcrumb else 0)
    budget = max(budget, max(chunk_size // 2, 64))

    chunks: list[Chunk] = []
    line_cursor = line_start
    for sub in split_text(body_text, budget, overlap):
        sub_lines = sub.count("\n") + 1
        chunks.append(
            Chunk(
                text=(breadcrumb + "\n\n" + sub) if breadcrumb else sub,
                heading_path=[title for _, title in stack],
                page_no=page_no,
                line_start=line_cursor,
                line_end=line_cursor + sub_lines - 1,
            )
        )
        line_cursor += sub_lines
    return chunks


def iter_structured_lines(
    lines: Iterable[str],
    chunk_size: int,
    overlap: int,
) -> Iterator[Chunk]:
    """逐行读取结构化文本，按标题区段增量产出分块。"""
    stack: list[tuple[int, str]] = []
    current_page: int | None = None
    body: list[str] = []
    section_start: int = 1

    def flush(start: int) -> list[Chunk]:
        chunks = _split_section(
            list(stack), current_page, body, start, chunk_size, overlap
        )
        body.clear()
        return chunks

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r\n")
        m_page = _PAGE_RE.match(line)
        if m_page:
            yield from flush(section_start)
            current_page = int(m_page.group(1))
            section_start = idx + 1
            continue

        m_h = _HEADING_RE.match(line)
        if m_h:
            yield from flush(section_start)
            level = len(m_h.group(1))
            title = m_h.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section_start = idx + 1
            continue

        body.append(line)

    yield from flush(section_start)


def split_structured(text: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """
    识别 [[PAGE:N]] 与 Markdown 标题，把文本切成带结构 metadata 的 Chunk 列表。
    """
    if not text or not text.strip():
        return []
    return list(iter_structured_lines(text.splitlines(), chunk_size, overlap))
