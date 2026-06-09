"""
跨 session 用户记忆模块

持久化关于用户的长期信息（自然语言陈述句），支持新 session 自动加载注入。

设计：ChatGPT 式扁平自然语言列表 —— 一条记忆就是一句自洽的自然语言（如
"用户是后端工程师，常用 Python"），不分类别。新信息进来时由一次 LLM 调用
完成"提取 + 合并"：对现有列表给出 ADD / UPDATE / DELETE 操作，天然去重去矛盾。

职责：
    1. 存储：一行一句自然语言记忆
    2. 检索：加载并格式化为可注入 system prompt 的文本块
    3. 提取合并：调 LLM 从一轮对话维护记忆列表（输出操作）
    4. 安全：写入前 _sanitize 防 prompt injection
    5. 控制：用户可查询、增、改、删、清空

表结构：
    user_memories(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL DEFAULT 1,
        text        TEXT    NOT NULL,                 -- 自然语言整句
        source      TEXT    NOT NULL DEFAULT 'auto',  -- auto/explicit/manual
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL                  -- 最后改写时间
    )

source 字段来源：
    - auto      MemoryManager.try_extract 在自动模式下提取（USER_MEMORY_AUTO_EXTRACT）
    - explicit  用户敲"请记住"/"remember" 等触发词后由 LLM 提取
    - manual    用户用 /memory add / /memory edit 显式写入
"""

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# prompt injection 风险模式：物理位置统一在 src/agent/core/security_filter，本模块 import 复用
from src.agent.core.security_filter import _INJECTION_PATTERNS
from src.core.user_context import current_user_id

logger = logging.getLogger(__name__)


# ── LLM 接口 Protocol ──────────────────────────────────────────────────────────

@runtime_checkable
class LlmChatFn(Protocol):
    """LLM chat 函数的最小接口（与 provider.LLMProvider.chat 签名兼容）。"""

    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
    ) -> Any: ...

# ── 常量 ──────────────────────────────────────────────────────────────────────

# 写入来源
MEMORY_SOURCES: frozenset[str] = frozenset({"auto", "explicit", "manual"})
SOURCE_LABELS: dict[str, str] = {
    "auto":     "自动",
    "explicit": "请记住",
    "manual":   "手工",
}

# 用户显式触发记忆提取的关键词
_REMEMBER_TRIGGERS: frozenset[str] = frozenset({
    "记住这个", "记住这一点", "记住我说的", "请记住", "帮我记住",
    "记住以下", "以后记住", "永久记住",
    "remember this", "remember that", "please remember", "keep in mind",
})

# 单条记忆存储硬上限（字符）。prompt 另会提示 LLM 更短，这里只是兜底防超长。
_MAX_TEXT_CHARS: int = 500
# 单条记忆建议长度（提示给 LLM，保持精炼）
_TEXT_HINT_CHARS: int = 120
# 单次合并调用最多应用的操作数（防 LLM 异常输出搅乱整库）
_MAX_OPS_PER_CALL: int = 10


# ── 安全校验 ──────────────────────────────────────────────────────────────────

def _sanitize(text: str) -> str:
    """
    清洗记忆内容，防止 prompt injection。

    遍历全部风险模式，取所有匹配位置的最小值作为截断点，
    消除因模式检索顺序不同导致的绕过风险。
    """
    matches = [m.start() for p in _INJECTION_PATTERNS if (m := p.search(text))]
    if matches:
        cut = min(matches)
        logger.warning("[UserMemory] 检测到潜在注入模式，内容已截断至位置 %d", cut)
        text = text[:cut].strip()
    # 移除控制字符（保留 \t \n）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:_MAX_TEXT_CHARS]


# ── 触发检测 ──────────────────────────────────────────────────────────────────

def should_extract_immediately(user_text: str) -> bool:
    """检测用户输入是否含有显式记忆触发词（用于立即提取）。"""
    lower = user_text.lower()
    return any(trigger in lower for trigger in _REMEMBER_TRIGGERS)


# ── LLM 提取 + 合并 ───────────────────────────────────────────────────────────

# 不同触发模式下的策略行（拼进 system prompt）
_MODE_AUTO = (
    "当前是自动维护：把对话里体现的用户**长期信息**记下来——兴趣爱好、好恶、技能与背景、"
    "学习 / 工作目标、持久偏好、需纠正的错误，都值得 ADD / UPDATE；只忽略一次性、"
    "与用户本人无关的内容（当天天气、纯知识问答、临时任务），没有就返回 []。"
)
_MODE_EXPLICIT = (
    "用户明确要求记住当前对话，请**积极**维护：讨论结论、关注的技术方向、待办、"
    "重要决策、工作背景都值得 ADD 或 UPDATE。"
)


def _build_merge_system_prompt(max_entries: int, mode_line: str) -> str:
    """构造"提取 + 合并"system prompt。

    JSON 示例里有花括号，故用拼接而非 str.format，避免转义负担。
    """
    return (
        "你是用户记忆管理助手。基于最新对话，维护一份关于用户的长期记忆列表"
        "（每条是一句自洽的自然语言陈述）。\n\n"
        f"{mode_line}\n\n"
        "你会看到【当前记忆列表】（带编号）和【最新对话】。请输出对列表的操作，"
        "让它保持准确、精简、无重复、无矛盾：\n"
        "- 出现值得长期记住的新信息 → ADD\n"
        "- 新信息与某条已有记忆是同一主题（更新 / 纠正 / 补充）→ UPDATE 那条\n"
        "- 已有记忆被新信息推翻、作废 → DELETE 那条\n"
        "- 没有值得改动的 → 返回空数组 []\n\n"
        "要求：\n"
        f"- 每条记忆一句自然语言陈述（如\"用户是后端工程师，常用 Python\"），不超过 {_TEXT_HINT_CHARS} 字\n"
        "- 只记明确事实，不推断不编造\n"
        f"- 列表总数控制在 {max_entries} 条以内，超了就把最不重要 / 最旧的合并或 DELETE\n"
        "- id 必须用【当前记忆列表】里真实存在的编号\n\n"
        "输出 JSON 数组，每项是下列三种之一：\n"
        '{"op": "ADD", "text": "..."}\n'
        '{"op": "UPDATE", "id": 3, "text": "..."}\n'
        '{"op": "DELETE", "id": 5}\n'
        "只输出 JSON，不要任何解释文字。"
    )


def _format_memory_list(existing: list[dict[str, Any]]) -> str:
    """把已有记忆排成带编号的列表供 LLM 判断；空列表给出明确占位。"""
    if not existing:
        return "（当前没有任何记忆）"
    return "\n".join(
        f"{m['id']}. {m['text']}" for m in existing if m.get("id") and m.get("text")
    )


def extract_memory_ops(
    user_input: str,
    agent_reply: str,
    llm_chat_fn: LlmChatFn,
    *,
    existing: list[dict[str, Any]] | None = None,
    context_history: str = "",
    is_explicit: bool = False,
    max_entries: int = 30,
) -> list[dict[str, Any]]:
    """
    调一次 LLM，基于本轮对话 + 现有记忆，产出对记忆列表的操作（提取 + 合并一步到位）。

    Args:
        user_input:       用户输入。
        agent_reply:      Agent 回答。
        llm_chat_fn:      LLM chat 函数（签名同 provider.chat）。
        existing:         该用户现有记忆（含 id / text），供 LLM 去重去矛盾。
        context_history:  最近若干轮历史；非空就拼进对话。auto / explicit 都会带，
                          仅用来给 LLM 更多上下文，不再决定提取模式。
        is_explicit:      True=显式触发（"请记住"）更积极；False=自动维护（收长期信息、丢一次性内容）。
        max_entries:      记忆总条数软上限，提示 LLM 合并时控制规模。

    Returns:
        操作列表，每项形如 {"op": "ADD", "text": ...} / {"op": "UPDATE", "id": n, "text": ...}
        / {"op": "DELETE", "id": n}。最多 _MAX_OPS_PER_CALL 条；失败或无改动返回 []。
    """
    existing = existing or []
    mode_line = _MODE_EXPLICIT if is_explicit else _MODE_AUTO
    system_prompt = _build_merge_system_prompt(max_entries, mode_line)

    if context_history:
        conversation = (
            f"【近期对话上下文】\n{context_history}\n\n"
            f"【最新对话】\n用户：{user_input}\n\nAgent：{agent_reply}"
        )
    else:
        conversation = f"【最新对话】\n用户：{user_input}\n\nAgent：{agent_reply}"
    conversation += "\n\n【当前记忆列表】\n" + _format_memory_list(existing)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": conversation},
    ]
    try:
        response = llm_chat_fn(messages, temperature=0.1)
        raw: str = response.choices[0].message.content or ""
        # 提取第一个 JSON 数组（防止 LLM 附加解释文字）
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return []
        entries: list[Any] = json.loads(json_match.group())
        return _normalize_ops(entries)
    except Exception as exc:
        logger.warning("[UserMemory] LLM 提取合并失败: %s", exc)
        return []


def _normalize_ops(entries: list[Any]) -> list[dict[str, Any]]:
    """校验并规整 LLM 输出的操作；丢弃非法项，截到 _MAX_OPS_PER_CALL 条。"""
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("op", "")).strip().upper()
        if action == "ADD":
            text = str(entry.get("text", "")).strip()
            if text:
                result.append({"op": "ADD", "text": text})
        elif action == "UPDATE":
            text = str(entry.get("text", "")).strip()
            try:
                oid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if text:
                result.append({"op": "UPDATE", "id": oid, "text": text})
        elif action == "DELETE":
            try:
                oid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            result.append({"op": "DELETE", "id": oid})
    return result[:_MAX_OPS_PER_CALL]


# ── UserMemoryStore ───────────────────────────────────────────────────────────

class UserMemoryStore:
    """
    跨 session 用户记忆存储（扁平自然语言列表）。

    独立于对话历史 ChatHistoryStore，使用单独的 SQLite 文件。
    同一进程建议复用单个实例；内置 threading.Lock，可被多线程安全读写。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + _lock 组合保证多线程安全
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()
        logger.info("[UserMemory] 初始化完成: %s", self._db_path)

    # ── 表结构 ────────────────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        """创建 user_memories 表（幂等）+ fail-fast 检测旧 schema。

        不做向后兼容迁移：旧的结构化 schema（category/key/value）请手动删除
        `./sqlite_db/user_memory.db` 重建。建表后做一次 PRAGMA 自检，缺 text 列
        （= 旧结构化库）时抛带操作指引的 RuntimeError，而非裸 OperationalError。
        """
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL DEFAULT 1,
                    text        TEXT    NOT NULL,
                    source      TEXT    NOT NULL DEFAULT 'auto',
                    created_at  TEXT    NOT NULL,
                    updated_at  TEXT    NOT NULL
                )
            """)
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(user_memories)")}
            if "text" not in cols:
                raise RuntimeError(
                    "user_memory.db schema 已过期（旧版结构化记忆），"
                    "请删除 ./sqlite_db/user_memory.db 后重启。"
                )
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_memories_user
                    ON user_memories(user_id)
            """)

    # ── 核心 CRUD ─────────────────────────────────────────────────────────────

    def add(self, text: str, source: str = "auto", user_id: int | None = None) -> int | None:
        """
        新增一条记忆。

        Args:
            text:   自然语言整句（自动 _sanitize + 截断）。
            source: 写入来源，未知值降级为 'auto'。

        Returns:
            新行 id；text 清洗后为空则跳过、返回 None。
        """
        uid = user_id if user_id is not None else current_user_id()
        if source not in MEMORY_SOURCES:
            logger.warning("[UserMemory] 未知 source %r，降级为 'auto'", source)
            source = "auto"
        clean = _sanitize(text)
        if not clean:
            logger.warning("[UserMemory] text 清洗后为空，跳过写入")
            return None
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT INTO user_memories(user_id, text, source, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?)""",
                (uid, clean, source, now, now),
            )
            row_id = int(cursor.lastrowid or 0)
        logger.info("[UserMemory] 已新增 (source=%s, id=%d): %s", source, row_id, clean[:50])
        return row_id

    def update_text(self, memory_id: int, new_text: str, user_id: int | None = None) -> bool:
        """按 id 改写记忆内容（限本人，刷新 updated_at）；text 清洗后为空或 id 不存在返回 False。"""
        clean = _sanitize(new_text)
        if not clean:
            logger.warning("[UserMemory] update_text: 清洗后为空，跳过 id=%d", memory_id)
            return False
        uid = user_id if user_id is not None else current_user_id()
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE user_memories SET text = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (clean, now, memory_id, uid),
            )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("[UserMemory] 已改写 id=%d: %s", memory_id, clean[:50])
        return updated

    def apply_ops(
        self, ops: list[dict[str, Any]], source: str = "auto", user_id: int | None = None
    ) -> dict[str, int]:
        """应用一组 LLM 产出的操作（ADD / UPDATE / DELETE），返回各操作的成功计数。

        非法 id（不存在 / 非本人）的 UPDATE / DELETE 被静默忽略；操作数截到上限。
        """
        uid = user_id if user_id is not None else current_user_id()
        added = updated = deleted = 0
        for op in ops[:_MAX_OPS_PER_CALL]:
            action = op.get("op")
            if action == "ADD":
                if self.add(op.get("text", ""), source=source, user_id=uid) is not None:
                    added += 1
            elif action == "UPDATE":
                if self.update_text(int(op["id"]), op.get("text", ""), user_id=uid):
                    updated += 1
            elif action == "DELETE":
                if self.delete(int(op["id"]), user_id=uid):
                    deleted += 1
        if added or updated or deleted:
            logger.info(
                "[UserMemory] 应用操作 (source=%s): +%d ~%d -%d", source, added, updated, deleted
            )
        return {"added": added, "updated": updated, "deleted": deleted}

    def load_all(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """加载某用户全部记忆，按 id 升序（= 写入顺序，便于 CLI 编号稳定）。"""
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, text, source, created_at, updated_at
                   FROM user_memories
                   WHERE user_id = ?
                   ORDER BY id ASC""",
                (uid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_for_context(self, max_chars: int = 1500, user_id: int | None = None) -> str:
        """
        加载记忆并格式化为可注入 system prompt 的文本块（扁平自然语言 bullet）。

        排序：manual / explicit（用户手写）优先于 auto，同级按 updated_at 倒序；
        超出 max_chars 时截断。被动注入不刷新时间戳（避免门内条目永久占位、门外饥饿）。

        Returns:
            每行 `- {text}` 的文本，无记忆时返回空字符串。
        """
        uid = user_id if user_id is not None else current_user_id()
        with self._lock:
            rows = self._conn.execute(
                """SELECT text
                   FROM user_memories
                   WHERE user_id = ?
                   ORDER BY CASE source
                                WHEN 'manual'   THEN 0
                                WHEN 'explicit' THEN 1
                                ELSE 2
                            END,
                            updated_at DESC""",
                (uid,),
            ).fetchall()
        if not rows:
            return ""

        lines: list[str] = []
        for row in rows:
            line = f"- {row['text']}"
            candidate = "\n".join(lines + [line])
            if len(candidate) > max_chars:
                break
            lines.append(line)

        return "\n".join(lines)

    def delete(self, memory_id: int, user_id: int | None = None) -> bool:
        """删除指定 id 的记忆条目（限本人），返回是否实际删除。"""
        uid = user_id if user_id is not None else current_user_id()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM user_memories WHERE id = ? AND user_id = ?", (memory_id, uid)
            )
        return cursor.rowcount > 0

    def clear(self, user_id: int | None = None) -> int:
        """清空某用户的全部记忆，返回被删除的条目数。"""
        uid = user_id if user_id is not None else current_user_id()
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM user_memories WHERE user_id = ?", (uid,))
        count = cursor.rowcount
        logger.info("[UserMemory] 已清空用户 %d 的全部 %d 条记忆", uid, count)
        return count

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "UserMemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
