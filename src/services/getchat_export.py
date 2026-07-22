"""按用户名导出对话记录为 Markdown（与聊天页 UI 展示对齐）。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import src.config as config

TEXT_ATT_RE = re.compile(r"\n\n附件 `([^`]+)`：\n```\n([\s\S]*?)\n```")
OTHER_ATT_RE = re.compile(r"\n\n\[附件 ([^（]+)（(图片|二进制)）未随消息发送[^\]]*\]")

_DEFAULT_SESSION_TITLE = "New Chat"


class UserNotFoundError(LookupError):
    """用户名在 auth.db 中不存在。"""


@dataclass
class AttachmentInfo:
    name: str
    kind: str  # text | image | other
    lines: int | None = None
    sent: bool = True


@dataclass
class ToolCallExport:
    call_id: str
    name: str
    args: dict[str, Any]
    preview: str | None = None


@dataclass
class UserBubble:
    timestamp: str
    text: str
    attachments: list[AttachmentInfo] = field(default_factory=list)


@dataclass
class AssistantBubble:
    timestamp: str
    tools: list[ToolCallExport] = field(default_factory=list)
    content: str = ""


@dataclass
class SessionExport:
    session_id: str
    title: str
    created_at: str
    bubbles: list[UserBubble | AssistantBubble]


def parse_user_message(content: str) -> tuple[str, list[AttachmentInfo]]:
    """与前端 parseUserMessage 对齐：拆展示文本与附件元信息。"""
    attachments: list[AttachmentInfo] = []
    first_idx = len(content)

    for m in TEXT_ATT_RE.finditer(content):
        first_idx = min(first_idx, m.start())
        body = m.group(2)
        attachments.append(
            AttachmentInfo(
                name=m.group(1),
                kind="text",
                lines=len(body.split("\n")) if body else 0,
                sent=True,
            )
        )

    for m in OTHER_ATT_RE.finditer(content):
        first_idx = min(first_idx, m.start())
        kind_label = m.group(2)
        attachments.append(
            AttachmentInfo(
                name=m.group(1),
                kind="image" if kind_label == "图片" else "other",
                sent=False,
            )
        )

    text = (content[:first_idx] if first_idx < len(content) else content).strip()
    return text, attachments


def _tool_label(name: str, args: dict[str, Any], preview: str | None) -> str:
    """与 ToolBlock.describe 对齐的人类可读标签。"""
    q = args.get("query") if isinstance(args.get("query"), str) else ""
    if name == "web_search":
        return f'联网搜索 "{q}"' if q else "联网搜索"
    if name == "search_knowledge":
        return f'检索知识库 "{q}"' if q else "检索知识库"
    if name == "fetch_url":
        return "抓取网页"
    if name == "update_step":
        m = re.search(r"(\d+)\s*/\s*(\d+)", preview or "")
        step_id = args.get("step_id") if isinstance(args.get("step_id"), int) else None
        if step_id is None and m:
            step_id = int(m.group(1))
        total = m.group(2) if m else None
        suffix = f" {step_id}/{total or '?'}" if step_id is not None else (f" ?/{total}" if total else "")
        return f"update_step{suffix}"
    return name


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


def messages_to_bubbles(rows: list[sqlite3.Row]) -> list[UserBubble | AssistantBubble]:
    """将 DB 消息行合并为 UI 气泡（对齐 backendMessagesToFrontend）。"""
    out: list[UserBubble | AssistantBubble] = []
    pending: AssistantBubble | None = None
    tool_index: dict[str, ToolCallExport] = {}

    def finalize() -> None:
        nonlocal pending, tool_index
        if pending is not None:
            out.append(pending)
            pending = None
            tool_index = {}

    for row in rows:
        role = row["role"]
        if role == "user":
            finalize()
            text, attachments = parse_user_message(row["content"] or "")
            out.append(
                UserBubble(
                    timestamp=row["timestamp"],
                    text=text,
                    attachments=attachments,
                )
            )
            continue

        if role == "assistant":
            if pending is None:
                pending = AssistantBubble(timestamp=row["timestamp"])
            tool_calls = json.loads(row["tool_calls"] or "[]")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    call_id = str(tc.get("id") or "")
                    name = str(fn.get("name") or "")
                    state = ToolCallExport(
                        call_id=call_id,
                        name=name,
                        args=_parse_args(fn.get("arguments")),
                    )
                    pending.tools.append(state)
                    if call_id:
                        tool_index[call_id] = state
            content = row["content"] or ""
            if content:
                pending.content = (
                    f"{pending.content}\n\n{content}" if pending.content else content
                )
            continue

        if role == "tool":
            if pending is None:
                continue
            tid = str(row["tool_call_id"] or "")
            state = tool_index.get(tid)
            if state is not None:
                state.preview = row["content"] or ""
            continue

        # system 等角色不展示

    finalize()
    return out


def _lookup_user_id(username: str, auth_db: str) -> int:
    conn = sqlite3.connect(auth_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise UserNotFoundError(f"用户不存在: {username}")
    return int(row["id"])


def _load_sessions(user_id: int, session_db: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(session_db)
    conn.row_factory = sqlite3.Row
    try:
        return list(
            conn.execute(
                """
                SELECT session_id, created_at, first_user_msg
                FROM sessions
                WHERE user_id = ?
                ORDER BY created_at ASC
                """,
                (user_id,),
            ).fetchall()
        )
    finally:
        conn.close()


def _load_messages(session_id: str, session_db: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(session_db)
    conn.row_factory = sqlite3.Row
    try:
        return list(
            conn.execute(
                """
                SELECT role, content, tool_calls, tool_call_id, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        )
    finally:
        conn.close()


def _session_title(first_user_msg: str | None) -> str:
    raw = (first_user_msg or "").strip()
    return raw or _DEFAULT_SESSION_TITLE


def _format_attachment(att: AttachmentInfo) -> str:
    if att.kind == "text":
        lines = att.lines if att.lines is not None else 0
        return f"- 附件 `{att.name}`（文本，{lines} 行）"
    if att.kind == "image":
        return f"- 附件 `{att.name}`（图片，未发送）"
    return f"- 附件 `{att.name}`（二进制，未发送）"


def _format_bubble(bubble: UserBubble | AssistantBubble) -> list[str]:
    lines = [f"### [{bubble.timestamp}] {'用户' if isinstance(bubble, UserBubble) else '助手'}", ""]
    if isinstance(bubble, UserBubble):
        if bubble.attachments:
            lines.extend(_format_attachment(a) for a in bubble.attachments)
            lines.append("")
        if bubble.text:
            lines.append(bubble.text)
        elif not bubble.attachments:
            lines.append("（空消息）")
        return lines

    for tool in bubble.tools:
        label = _tool_label(tool.name, tool.args, tool.preview)
        lines.append(f"**{label}**")
        if tool.args:
            args_json = json.dumps(tool.args, ensure_ascii=False, indent=2)
            lines.append(f"```json\n{args_json}\n```")
        if tool.preview:
            lines.append(tool.preview)
        lines.append("")

    if bubble.content:
        lines.append(bubble.content)
    elif not bubble.tools:
        lines.append("（空回答）")
    return lines


def format_markdown(username: str, sessions: list[SessionExport]) -> str:
    parts = [f"# 用户 {username} 对话导出", ""]
    if not sessions:
        parts.append("（无对话记录）")
        return "\n".join(parts).rstrip() + "\n"

    for i, sess in enumerate(sessions):
        if i > 0:
            parts.append("---")
            parts.append("")
        parts.append(f"## {sess.title}")
        parts.append("")
        parts.append(f"- Session ID: `{sess.session_id}`")
        parts.append(f"- 创建时间: {sess.created_at}")
        parts.append("")
        if not sess.bubbles:
            parts.append("（无消息）")
            parts.append("")
            continue
        for bubble in sess.bubbles:
            parts.extend(_format_bubble(bubble))
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def export_user_chat(
    username: str,
    *,
    auth_db: str | None = None,
    session_db: str | None = None,
) -> str:
    """导出指定用户全部 session，返回 Markdown 文本。"""
    auth_path = str(auth_db or config.AUTH_DB_PATH)
    session_path = str(session_db or config.MEMORY_DB_PATH)
    user_id = _lookup_user_id(username, auth_path)

    exports: list[SessionExport] = []
    for row in _load_sessions(user_id, session_path):
        sid = row["session_id"]
        messages = _load_messages(sid, session_path)
        exports.append(
            SessionExport(
                session_id=sid,
                title=_session_title(row["first_user_msg"]),
                created_at=row["created_at"],
                bubbles=messages_to_bubbles(messages),
            )
        )
    return format_markdown(username.strip(), exports)


def output_path_for_user(username: str, base: Path | None = None) -> Path:
    """默认输出路径：history/<用户名>_chat.md（相对 base 或当前工作目录）。"""
    root = base if base is not None else Path.cwd()
    return root / "history" / f"{username.strip()}_chat.md"


def write_user_chat(
    username: str,
    output_path: str | Path | None = None,
    *,
    auth_db: str | None = None,
    session_db: str | None = None,
) -> Path:
    """导出并写入文件；用户不存在时抛 UserNotFoundError，不写文件。"""
    text = export_user_chat(username, auth_db=auth_db, session_db=session_db)
    path = Path(output_path) if output_path is not None else output_path_for_user(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
