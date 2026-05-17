"""
结构化文本分块模块

提供两个层次的分块能力：
    1. split_text(text, size, overlap)
       — 类 LangChain RecursiveCharacterTextSplitter：按 \n\n → \n → 句号 → 空格 → 字符
       逐级回退切分；只把文本切到不超过 size 的"原子单元"，再贪心打包成 chunk，相邻
       chunk 间保留 overlap 字符。无任何分隔符时退化为字符级切分（与 ingest.chunk_text
       的旧字符切语义一致，保留对老测试的兼容）。

    2. split_structured(text, size, overlap)
       — 在 split_text 之上识别两类锚点并保留结构信息：
         · "[[PAGE:N]]" 独占一行（由 parser 在 PDF/PPTX 解析时插入）→ 切分点 + page_no 元数据；
         · Markdown 风格的 "#"~"######" 标题行 → 切分点 + heading_path 面包屑。
       对每个 section.body 走 split_text；最终 Chunk.text 自动注入"父级标题面包屑"前缀，
       让 chunk 自带"我在第几章/第几页/讲什么"的语义锚点，显著提升相似度命中率。
"""

from __future__ import annotations

import re
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
        text:         已注入"父级标题面包屑 + 空行 + 正文"前缀的最终文本，直接用于 embedding。
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


def _split_into_atoms(text: str, chunk_size: int) -> list[str]:
    """
    按语义边界把文本递归拆成不超过 chunk_size 的"原子单元"。

    优先级：段落（\\n\\n） > 行（\\n） > 中文/英文句号 > 空格 > 字符。
    每一级都保留分隔符，避免拼回时丢失语义边界。最后一级是字符级硬切（兜底）。
    """
    if len(text) <= chunk_size:
        return [text]

    # 段落
    if "\n\n" in text:
        parts = text.split("\n\n")
        result: list[str] = []
        for i, p in enumerate(parts):
            piece = p + "\n\n" if i < len(parts) - 1 else p
            result.extend(_split_into_atoms(piece, chunk_size))
        return result

    # 行
    if "\n" in text:
        parts = text.split("\n")
        result = []
        for i, p in enumerate(parts):
            piece = p + "\n" if i < len(parts) - 1 else p
            result.extend(_split_into_atoms(piece, chunk_size))
        return result

    # 中英文断句标点（保留分隔符）
    for sep in ("。", "！", "？", "；", ". ", "! ", "? "):
        if sep in text:
            parts = text.split(sep)
            result = []
            for i, p in enumerate(parts):
                piece = p + sep if i < len(parts) - 1 else p
                result.extend(_split_into_atoms(piece, chunk_size))
            return result

    # 空格
    if " " in text:
        parts = text.split(" ")
        result = []
        for i, p in enumerate(parts):
            piece = p + " " if i < len(parts) - 1 else p
            result.extend(_split_into_atoms(piece, chunk_size))
        return result

    # 字符级兜底（无任何分隔符）
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

    与旧 chunk_text 的"按字符等步长滑动"相比：
      - 优先在 \\n\\n / \\n / 句号 / 空格 处切分，避免在词中间断开；
      - 仅当文本无任何分隔符时才退化为字符级切分（与旧实现一致，保留单测兼容）。
    """
    if not text or not text.strip():
        return []
    atoms = _split_into_atoms(text, chunk_size)
    return _greedy_pack(atoms, chunk_size, overlap)


# ── 第 2 层：识别 PAGE 锚点 + Markdown 标题的结构化分块 ──────────────────────


def _heading_breadcrumb(stack: list[tuple[int, str]]) -> str:
    """把 (level, title) 栈拼成 Markdown 风格的面包屑前缀。"""
    if not stack:
        return ""
    return "\n".join(f"{'#' * level} {title}" for level, title in stack)


def split_structured(text: str, chunk_size: int, overlap: int) -> list[Chunk]:
    """
    识别 [[PAGE:N]] 与 # / ## / ... 标题行，把文本切成带结构 metadata 的 Chunk 列表。

    流程：
      1. 逐行扫描，维护 (current_page, heading_stack) 状态。
      2. 标题行与 PAGE 行作为"分段点"：碰到时 flush 当前 body 为一个 section。
      3. 标题行同时更新 heading_stack（新 level >= 栈顶 level 的旧标题被弹出）。
      4. 对每个 section.body 调用 split_text 切成多块；每块前缀注入面包屑。
    """
    if not text or not text.strip():
        return []

    lines = text.splitlines()
    # 每个 section: (heading_stack_snapshot, page_no, body_lines, line_start)
    sections: list[tuple[list[tuple[int, str]], int | None, list[str], int]] = []

    stack: list[tuple[int, str]] = []
    current_page: int | None = None
    body: list[str] = []
    section_start: int = 1

    def flush(start: int) -> None:
        if any(line.strip() for line in body):
            sections.append((list(stack), current_page, list(body), start))
        body.clear()

    for idx, line in enumerate(lines, start=1):
        m_page = _PAGE_RE.match(line)
        if m_page:
            flush(section_start)
            current_page = int(m_page.group(1))
            section_start = idx + 1
            continue

        m_h = _HEADING_RE.match(line)
        if m_h:
            flush(section_start)
            level = len(m_h.group(1))
            title = m_h.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section_start = idx + 1
            continue

        body.append(line)

    flush(section_start)

    chunks: list[Chunk] = []
    for snap_stack, page_no, body_lines, line_start in sections:
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            continue
        breadcrumb = _heading_breadcrumb(snap_stack)
        # 给正文留出 chunk 预算：减去面包屑及空行长度，但下限 chunk_size/2 防止退化
        budget = chunk_size - (len(breadcrumb) + 2 if breadcrumb else 0)
        budget = max(budget, max(chunk_size // 2, 64))

        sub_chunks = split_text(body_text, budget, overlap)
        line_cursor = line_start
        for sub in sub_chunks:
            sub_lines = sub.count("\n") + 1
            heading_path = [title for (_, title) in snap_stack]
            final_text = (breadcrumb + "\n\n" + sub) if breadcrumb else sub
            chunks.append(
                Chunk(
                    text=final_text,
                    heading_path=heading_path,
                    page_no=page_no,
                    line_start=line_cursor,
                    line_end=line_cursor + sub_lines - 1,
                )
            )
            line_cursor += sub_lines

    return chunks
