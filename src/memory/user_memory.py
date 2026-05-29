"""
跨 session 用户记忆模块

持久化用户偏好、背景和明确指令，支持新 session 自动加载。

职责：
    1. 存储：结构化 key-value 条目，按类别组织，同 (category, key) 自动去重
    2. 检索：加载相关条目并格式化为可注入 system prompt 的文本块
    3. 提取：调用 LLM 从一轮对话中提取值得记住的用户信息
    4. 安全：注入前校验，防止 prompt injection
    5. 控制：用户可查询、删除、清空

表结构：
    user_memories(
        id          INTEGER  PRIMARY KEY AUTOINCREMENT,
        category    TEXT     NOT NULL,  -- preference/background/instruction/task/correction
        key         TEXT     NOT NULL,  -- 短标识（≤ 30 字符）
        value       TEXT     NOT NULL,  -- 实际内容（≤ 500 字符）
        source      TEXT     NOT NULL DEFAULT 'auto',  -- auto/explicit/manual
        created_at  TEXT     NOT NULL,
        accessed_at TEXT     NOT NULL,
        UNIQUE(category, key)           -- 同类同 key 自动覆盖旧值
    )

source 字段来源（C 混合范式，详 iter_2_agent.md §4.9.2）：
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

# 支持的记忆类别
MEMORY_CATEGORIES: frozenset[str] = frozenset({
    "preference",   # 用户偏好（代码风格、语言、格式）
    "background",   # 用户背景（职业、技术栈、项目上下文）
    "instruction",  # 明确指令（"不要用 bullet points"）
    "task",         # 任务进度（未完成工作、决策记录）
    "correction",   # 纠错（agent 犯过的错，避免重犯）
})

CATEGORY_LABELS: dict[str, str] = {
    "preference":  "偏好",
    "background":  "背景",
    "instruction": "指令",
    "task":        "任务",
    "correction":  "纠错",
}

# 写入来源（详 iter_2_agent.md §4.9.2 C 混合范式）
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

# prompt injection 风险模式（value 和 key 写入前均做校验）
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'ignore\s+(?:all\s+)?previous\s+instructions?', re.IGNORECASE),
    re.compile(r'you\s+are\s+now\s+(?:a\s+)?', re.IGNORECASE),
    re.compile(r'new\s+(?:system\s+)?instructions?\s*:', re.IGNORECASE),
    re.compile(r'忽略.{0,10}指令', re.IGNORECASE),
    re.compile(r'你现在是', re.IGNORECASE),
    re.compile(r'新的.{0,6}指令', re.IGNORECASE),
    re.compile(r'system\s*:\s', re.IGNORECASE),
    re.compile(r'<\|(?:im_start|im_end|endoftext)\|>', re.IGNORECASE),
]

# 单条记忆 value 最大字符数
_MAX_VALUE_CHARS: int = 500
# 记忆 key 最大字符数
_MAX_KEY_CHARS: int = 30


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
    return text[:_MAX_VALUE_CHARS]


# ── 触发检测 ──────────────────────────────────────────────────────────────────

def should_extract_immediately(user_text: str) -> bool:
    """检测用户输入是否含有显式记忆触发词（用于立即提取）。"""
    lower = user_text.lower()
    return any(trigger in lower for trigger in _REMEMBER_TRIGGERS)


# ── LLM 提取 ─────────────────────────────────────────────────────────────────

# 自动提取模式：严格过滤，只保留用户个人信息相关内容
_EXTRACT_SYSTEM_PROMPT = """\
你是一个记忆提取助手。分析对话片段，从中提取**值得长期记住的用户信息**。

规则：
- 只提取明确出现的具体事实，不推断或编造
- 忽略泛泛而谈、一次性的内容
- 优先提取：用户偏好、职业背景、持久化指令、agent错误纠正
- key 不超过 15 字，value 不超过 100 字

输出 JSON 数组，每项格式：
{"category": "<类别>", "key": "<简短标识>", "value": "<具体内容>"}

支持的类别：
- preference  用户偏好（代码风格、语言、格式要求）
- background  用户背景（职业、技术栈、项目上下文）
- instruction 明确指令（"不要用 bullet points" 等持久化要求）
- task        任务进度（未完成的工作、待记录的决策）
- correction  纠错（agent 犯过的错误，避免重复）

若无值得记住的内容，返回空数组 []。只输出 JSON，不要有任何解释文字。\
"""

# 显式触发模式：用户主动说"记住这个"，使用更宽松的策略，积极保存对话结论
_EXTRACT_SYSTEM_PROMPT_EXPLICIT = """\
你是一个记忆提取助手。用户明确要求记住当前对话内容，请**积极提取**对话中值得保存的信息。

规则：
- 用户主动触发，应尽量记录，不要因"内容不够明确"而丢弃
- 可以提取：讨论的主题结论、用户关注的技术方向、待办任务、重要决策、用户的工作背景
- 对话中没有明显的用户个人信息时，将核心讨论内容归入 task 类别
- key 不超过 15 字，value 不超过 100 字（提炼核心，不要原文照抄）

输出 JSON 数组，每项格式：
{"category": "<类别>", "key": "<简短标识>", "value": "<具体内容>"}

支持的类别：
- preference  用户偏好（代码风格、语言、格式要求）
- background  用户背景（职业、技术栈、项目上下文）
- instruction 明确指令（持久化要求，如"不要用 bullet points"）
- task        任务进度 / 讨论结论（未完成工作、重要知识点、决策记录）
- correction  纠错（agent 犯过的错误，避免重复）

只输出 JSON，不要有任何解释文字。\
"""


def extract_memories(
    user_input: str,
    agent_reply: str,
    llm_chat_fn: LlmChatFn,
    context_history: str = "",
) -> list[dict[str, str]]:
    """
    调用 LLM 从一轮对话中提取值得记忆的用户信息。

    Args:
        user_input:       用户的原始输入。
        agent_reply:      Agent 的回答。
        llm_chat_fn:      可调用的 LLM chat 函数（签名与 provider.chat 相同）。
        context_history:  可选，最近几轮对话的文本（用于"记住这个"等指代性触发词的场景）。

    Returns:
        list of {category, key, value}，可直接传给 UserMemoryStore.upsert()。
        提取失败或无内容时返回空列表。
    """
    # 有历史上下文 = 用户显式触发（"记住这个"）→ 宽松策略，积极保存对话结论
    # 无历史上下文 = AUTO_EXTRACT 自动触发       → 严格策略，只保存用户个人信息
    if context_history:
        system_prompt = _EXTRACT_SYSTEM_PROMPT_EXPLICIT
        conversation = (
            f"【近期对话上下文】\n{context_history}\n\n"
            f"【触发记忆的当前轮】\n用户：{user_input}\n\nAgent：{agent_reply}"
        )
    else:
        system_prompt = _EXTRACT_SYSTEM_PROMPT
        conversation = f"用户：{user_input}\n\nAgent：{agent_reply}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": conversation},
    ]
    try:
        response = llm_chat_fn(messages, temperature=0.1)
        raw: str = response.choices[0].message.content or ""
        # 提取第一个 JSON 数组（防止 LLM 附加解释文字）
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if not json_match:
            return []
        entries: list[Any] = json.loads(json_match.group())
        result: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cat = str(entry.get("category", ""))
            key = str(entry.get("key", "")).strip()[:_MAX_KEY_CHARS]
            value = str(entry.get("value", "")).strip()
            if cat in MEMORY_CATEGORIES and key and value:
                result.append({"category": cat, "key": key, "value": value})
        return result
    except Exception as exc:
        logger.warning("[UserMemory] LLM 提取失败: %s", exc)
        return []


# ── UserMemoryStore ───────────────────────────────────────────────────────────

class UserMemoryStore:
    """
    跨 session 用户记忆存储。

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

        不做向后兼容 schema 迁移：从 pre-Phase 1.2 升级时请手动删除
        `sqlite_db/user_memory.db` 重建（单用户场景损失可接受，避免引入迁移代码）。
        但裸的 `sqlite3.OperationalError` 对用户不友好，所以在表创建后做一次
        PRAGMA 自检，缺列时抛带操作指引的 RuntimeError。
        """
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category    TEXT    NOT NULL,
                    key         TEXT    NOT NULL,
                    value       TEXT    NOT NULL,
                    source      TEXT    NOT NULL DEFAULT 'auto',
                    created_at  TEXT    NOT NULL,
                    accessed_at TEXT    NOT NULL,
                    UNIQUE(category, key)
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_memories_category
                    ON user_memories(category)
            """)
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(user_memories)")}
            if "source" not in cols:
                raise RuntimeError(
                    f"user_memory.db schema 已过期，请删除后重启。"
                )

    # ── 核心 CRUD ─────────────────────────────────────────────────────────────

    def upsert(self, category: str, key: str, value: str, source: str = "auto") -> None:
        """
        插入或更新一条记忆。同 (category, key) 的旧值被新值覆盖（去重）。

        Args:
            category: 记忆类别，必须在 MEMORY_CATEGORIES 内。
            key: 简短标识（≤ 30 字符，同样经过注入过滤）。
            value: 具体内容（自动清洗、截断）。
            source: 写入来源，必须在 MEMORY_SOURCES 内；未知值降级为 'auto'。
                    冲突 upsert 时也会更新 source，反映"最近一次来源"。
        """
        if category not in MEMORY_CATEGORIES:
            logger.warning("[UserMemory] 未知类别 %r，跳过写入", category)
            return
        if source not in MEMORY_SOURCES:
            logger.warning("[UserMemory] 未知 source %r，降级为 'auto'", source)
            source = "auto"
        clean_key = _sanitize(key.strip())[:_MAX_KEY_CHARS]
        clean_value = _sanitize(value)
        if not clean_key:
            logger.warning("[UserMemory] key 清洗后为空，跳过写入 [%s]", category)
            return
        if not clean_value:
            logger.warning("[UserMemory] value 清洗后为空，跳过写入 [%s] %s", category, key)
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO user_memories(category, key, value, source, created_at, accessed_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(category, key) DO UPDATE SET
                       value = excluded.value,
                       source = excluded.source,
                       accessed_at = excluded.accessed_at""",
                (category, clean_key, clean_value, source, now, now),
            )
        logger.info("[UserMemory] 已写入 [%s] %s (source=%s)", category, key, source)

    def update_value(self, memory_id: int, new_value: str) -> bool:
        """
        按 id 更新单条记忆的 value（保持 category/key/source 不变；accessed_at 同步刷新）。

        用途：CLI `/memory edit <id> <new_value>` 让用户直接修正 LLM 误提取的 value，
        无需重敲完整 (category, key) 元组。

        Args:
            memory_id: `/memory` 列表中显示的 id。
            new_value: 新内容，自动 _sanitize + 截断。

        Returns:
            True 表示 id 存在且已更新；False 表示 id 不存在。
        """
        clean_value = _sanitize(new_value)
        if not clean_value:
            logger.warning("[UserMemory] update_value: 清洗后为空，跳过 id=%d", memory_id)
            return False
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE user_memories SET value = ?, accessed_at = ? WHERE id = ?",
                (clean_value, now, memory_id),
            )
        updated = cursor.rowcount > 0
        if updated:
            logger.info("[UserMemory] 已更新 id=%d", memory_id)
        return updated

    def load_all(self) -> list[dict[str, Any]]:
        """加载全部记忆条目，按类别和创建时间升序排序。返回字段含 source。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, category, key, value, source, created_at, accessed_at
                   FROM user_memories
                   ORDER BY category, created_at ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def load_for_context(self, max_chars: int = 1500) -> str:
        """
        加载记忆并格式化为可注入 system prompt 的文本块。

        按 accessed_at 倒序（最近访问的优先），超出 max_chars 时截断。
        同时更新所有已加载条目的 accessed_at（标记为"已使用"）。

        Returns:
            格式化后的记忆文本，无记忆时返回空字符串。
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, category, key, value
                   FROM user_memories
                   ORDER BY accessed_at DESC, created_at DESC"""
            ).fetchall()
        if not rows:
            return ""

        now = datetime.now().isoformat(timespec="seconds")
        lines: list[str] = []
        ids_to_update: list[int] = []

        for row in rows:
            label = CATEGORY_LABELS.get(row["category"], row["category"])
            line = f"- [{label}] {row['key']}：{row['value']}"
            candidate = "\n".join(lines + [line])
            if len(candidate) > max_chars:
                break
            lines.append(line)
            ids_to_update.append(row["id"])

        if not lines:
            return ""

        # 批量更新访问时间
        placeholders = ",".join("?" * len(ids_to_update))
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE user_memories SET accessed_at = ? WHERE id IN ({placeholders})",
                [now, *ids_to_update],
            )

        return "\n".join(lines)

    def delete(self, memory_id: int) -> bool:
        """删除指定 id 的记忆条目，返回是否实际删除。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM user_memories WHERE id = ?", (memory_id,)
            )
        return cursor.rowcount > 0

    def clear(self) -> int:
        """清空全部记忆，返回被删除的条目数。"""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM user_memories")
        count = cursor.rowcount
        logger.info("[UserMemory] 已清空全部 %d 条记忆", count)
        return count

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> "UserMemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
