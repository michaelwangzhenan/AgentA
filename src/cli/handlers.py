"""
CLI 命令处理器 —— 各 /command 的具体逻辑

将纯业务处理函数从 main.py 中解耦，便于独立测试和复用。
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import src.config as config
from src.memory.chat_history import ChatHistoryStore
from src.memory.user_memory import UserMemoryStore

# 历史记录预览截断长度
_HISTORY_PREVIEW_LEN: int = 60
# 切换 session 后展示的最近消息条数
_SWITCH_PREVIEW_COUNT: int = 2
# 切换预览每条消息正文截断长度
_SWITCH_PREVIEW_LEN: int = 80
# /memory 列表 key 列宽（对齐）
_MEMORY_KEY_COL_WIDTH: int = 16

# /memory 列表分组顺序（与 CATEGORY_LABELS 一致；以稳定输出便于人眼扫描）
MEMORY_CATEGORY_ORDER: tuple[str, ...] = (
    "preference", "background", "instruction", "task", "correction",
)

if TYPE_CHECKING:
    from src.agent.agent import Agent, ThinkingConfig
    from src.cli.skill_loader import SkillInfo


OutputFn = Callable[[str], None]


def _stdout(msg: str) -> None:
    """默认输出适配器：写到 CLI stdout。"""
    print(msg)


def _sanitize_cli_text(text: str) -> str:
    """将 \\r 规范为 \\n，避免终端回车覆盖行首导致回答开头被「吃掉」。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _is_visible_assistant_message(msg: dict[str, Any]) -> bool:
    """仅 tool_calls、无正文的 assistant 行不展示给用户（ReAct 中间态）。"""
    if msg.get("role") != "assistant":
        return True
    if (msg.get("content") or "").strip():
        return True
    return not msg.get("tool_calls")


def _conversation_messages(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导出/摘要用的 user + assistant 列表，跳过无正文的 tool-call assistant。"""
    return [
        m for m in msgs
        if m["role"] in ("user", "assistant") and _is_visible_assistant_message(m)
    ]


def quit_sys(chat_history: ChatHistoryStore, user_memory: UserMemoryStore | None) -> None:
    chat_history.close()
    if user_memory is not None:
        user_memory.close()
    sys.exit(0)

def save_history(
    chat_history: ChatHistoryStore,
    session_id: str,
    filename: str,
    out: OutputFn = _stdout,
) -> None:
    """将当前 session 的 user/assistant 对话导出到 history/<filename>.md。"""
    msgs = _conversation_messages(chat_history.load(session_id))
    if not msgs:
        out("📭 当前 session 暂无对话历史，无可导出内容。\n")
        return

    stem = re.sub(r'\.(md|txt)$', '', filename, flags=re.IGNORECASE)
    safe_name = re.sub(r'[^\w\-.]', '_', stem)
    if not safe_name or safe_name.startswith('.'):
        out(f"❌ 无效文件名：{filename!r}\n")
        return

    history_dir = Path("history")
    history_dir.mkdir(exist_ok=True)
    out_path = history_dir / f"{safe_name}.md"

    lines: list[str] = [
        f"# {safe_name}",
        "",
        f"- **Session**: `{session_id}`",
        f"- **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **消息数**: {len(msgs)}",
        "",
        "---",
        "",
    ]
    for msg in msgs:
        role_label = "你" if msg["role"] == "user" else "Agent"
        content = (msg.get("content") or "").strip()
        lines.append(f"## {role_label}")
        lines.append("")
        lines.append(content)
        lines.append("")

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        out(f"💾 对话已导出到 {out_path}（共 {len(msgs)} 条）\n")
    except OSError as e:
        out(f"❌ 导出失败: {e}\n")


def show_history(
    chat_history: ChatHistoryStore,
    session_id: str,
    out: OutputFn = _stdout,
) -> None:
    """展示当前 session 的历史对话摘要（角色 + 内容前 60 字）。"""
    msgs = _conversation_messages(chat_history.load(session_id))
    if not msgs:
        out("📭 当前 session 暂无对话历史。\n")
        return
    out(f"\n📋 Session {session_id} 历史摘要（共 {len(msgs)} 条）：")
    for i, msg in enumerate(msgs, 1):
        role_label = "你" if msg["role"] == "user" else "Agent"
        content = (msg.get("content") or "").replace("\n", " ")
        preview = content[:_HISTORY_PREVIEW_LEN] + ("…" if len(content) > _HISTORY_PREVIEW_LEN else "")
        out(f"  [{i:02d}] {role_label}: {preview}")
    out("")


def _format_relative_time(iso_ts: str) -> str:
    """把 ISO timestamp 格式化为人性化时间字符串。

    - 当天 → "今天 HH:MM"
    - 昨天 → "昨天 HH:MM"
    - 2-7 天 → "N 天前"
    - 更早 → "YYYY-MM-DD"

    解析失败时降级为原 ISO 串截断到秒。
    """
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts[:19].replace("T", " ")
    days = (datetime.now().date() - ts.date()).days
    if days == 0:
        return f"今天 {ts.strftime('%H:%M')}"
    if days == 1:
        return f"昨天 {ts.strftime('%H:%M')}"
    if 2 <= days <= 7:
        return f"{days} 天前"
    return ts.strftime("%Y-%m-%d")


def list_sessions(
    chat_history: ChatHistoryStore,
    query: str | None = None,
    current_session_id: str | None = None,
    out: OutputFn = _stdout,
) -> None:
    """列出历史 session，可选关键词过滤与当前 session 高亮。

    Args:
        chat_history: 存储依赖。
        query: 可选搜索词，按 session_id 前缀 OR first_user_msg LIKE 过滤。
        current_session_id: 若提供，在列表中用 "▶" 标记当前活跃 session。
        out: 输出适配器，便于测试注入。
    """
    sessions = chat_history.list_sessions(query=query)
    if not sessions:
        if query:
            out(f"📭 没有匹配 {query!r} 的 session。\n")
        else:
            out("📭 暂无历史 session 记录。\n")
        return

    title_suffix = f"（共 {len(sessions)} 个，过滤 {query!r}）" if query else f"（共 {len(sessions)} 个）"
    out(f"\n📚 历史 Session 列表{title_suffix}：")
    out(f"  {'':<2}{'ID':<10}  {'Create On':<14}  {'msgs':<6}  {'1st Question':<40}")
    out(f"  {'':<2}{'-'*8:<10}  {'-'*14:<14}  {'-'*6:<6}  {'-'*40}")
    for s in sessions:
        sid = s["session_id"]
        marker = "▶ " if current_session_id and sid == current_session_id else "  "
        sid_short = sid[:8]
        created = _format_relative_time(s["created_at"])
        first_msg = (s["first_user_msg"] or "（无用户消息）")[:40]
        out(f"  {marker}{sid_short:<10}  {created:<14}  {s['msg_count']:<6}  {first_msg:<40}")
    out("")


def make_agent(
    chat_history: ChatHistoryStore,
    skills_map: "dict[str, SkillInfo]",
    thinking_cfg: "ThinkingConfig",
    system_prompt: str,
    session_id: str | None = None,
    user_memory: "UserMemoryStore | None" = None,
    verbose: bool = True,
) -> "Agent":
    imp = config.IMP_METHOD
    if imp == "AUTOGPT":
        from src.agent.autogpt_agent import AutoGPTAgent
        return AutoGPTAgent(
            verbose=verbose,
            chat_history=chat_history,
            session_id=session_id,
            system_prompt=system_prompt,
            skills=skills_map or None,
            thinking_config=thinking_cfg,
            user_memory=user_memory,
        )
    if imp == "LANGCHAIN":
        from src.agent.langchain_agent import LangChainAgent
        return LangChainAgent(
            verbose=verbose,
            chat_history=chat_history,
            session_id=session_id,
            system_prompt=system_prompt,
            skills=skills_map or None,
            thinking_config=thinking_cfg,
            user_memory=user_memory,
        )
    from src.agent.agent import Agent
    return Agent(
        verbose=verbose,
        chat_history=chat_history,
        session_id=session_id,
        system_prompt=system_prompt,
        skills=skills_map or None,
        thinking_config=thinking_cfg,
        user_memory=user_memory,
    )


def _print_token_usage(agent: "Agent", out: OutputFn = _stdout) -> None:
    """若本次对话有 token 统计则打印，无统计时静默跳过。"""
    if agent.last_usage:
        u = agent.last_usage
        out(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")


# Phase 2.1 — plan step 状态对应的 CLI 渲染图标
_PLAN_STATUS_ICONS: dict[str, str] = {"success": "✓", "failed": "✗", "skipped": "⏭"}


def _render_plan_created(payload: dict[str, Any]) -> None:
    """CLI 渲染 plan_created：📋 + 所有未勾选 step checkbox 一次性打印。"""
    steps = payload.get("steps") or []
    if not steps:
        return
    sys.stdout.write("\n📋 Plan：\n")
    for s in steps:
        sys.stdout.write(f"  ☐ {s.get('id')}. {s.get('text', '')}\n")
    sys.stdout.flush()


def _render_plan_step_end(payload: dict[str, Any]) -> None:
    """CLI 渲染 plan_step_end：✓/✗/⏭ + step_id + 可选 note。"""
    icon = _PLAN_STATUS_ICONS.get(str(payload.get("status", "")), "•")
    note = (payload.get("note") or "").strip()
    suffix = f"（{note}）" if note else ""
    sys.stdout.write(f"  {icon} 第 {payload.get('step_id')} 步{suffix}\n")
    sys.stdout.flush()


def run_query(agent: "Agent", question: str, out: OutputFn = _stdout) -> None:
    """执行一次问答并打印结果，捕获中断和运行时异常。"""
    out("")
    streamed = False
    header_printed = False

    def _on_token_chunk(chunk: str) -> None:
        nonlocal streamed, header_printed
        if not chunk:
            return
        chunk = chunk.replace("\r", "")
        if not header_printed:
            sys.stdout.write("\nAgent: ")
            header_printed = True
            streamed = True
        sys.stdout.write(chunk)
        sys.stdout.flush()

    # 用 AgentAPI 的统一事件入口：按 event.type 派发：
    # - token_chunk:    流式正文
    # - plan_created:   📋 plan checkbox 整块
    # - plan_step_end:  ✓/✗/⏭ 单步状态行
    # - plan_step_start: CLI 不渲染（GUI 端用于高亮当前步，CLI 静默）
    # - 其它事件:        CLI 这里不渲染
    set_event_cb = getattr(agent, "set_event_callback", None)

    def _event_router(event) -> None:
        if event.type == "token_chunk":
            _on_token_chunk(event.payload.get("text", ""))
        elif event.type == "plan_created":
            _render_plan_created(event.payload)
        elif event.type == "plan_step_end":
            _render_plan_step_end(event.payload)

    if set_event_cb is not None:
        set_event_cb(_event_router)
    try:
        reply = agent.run(question)
        safe = _sanitize_cli_text(reply).strip()
        if streamed:
            if header_printed:
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif not safe:
                out("Agent: （无文本输出）\n")
        elif safe:
            out(f"Agent: {safe}\n")
        else:
            out("Agent: （无文本输出）\n")
        _print_token_usage(agent, out)
    except KeyboardInterrupt:
        out("\n⚠️  已中断当前回答。\n")
    except Exception as e:
        out(f"❌ 出错了: {e}\n")
    finally:
        if set_event_cb is not None:
            set_event_cb(None)


def handle_thinking_cfg(
    thinking_cfg: "ThinkingConfig",
    think_tokens: list[str],
    out: OutputFn = _stdout,
) -> None:
    """处理 /thinking 子命令，直接修改 thinking_cfg 状态并打印结果。"""
    match think_tokens[0] if think_tokens else "":
        case "on":
            thinking_cfg.enabled = True
            adaptive_hint = "，自动 budget 已开启" if thinking_cfg.adaptive else ""
            out(f"💭 Extended Thinking 已开启（budget={thinking_cfg.budget} tokens{adaptive_hint}）。\n")
        case "off":
            thinking_cfg.enabled = False
            out("💭 Extended Thinking 已关闭\n")
        case "adaptive":
            thinking_cfg.enabled = True
            thinking_cfg.adaptive = True
            out(
                f"🧠 Adaptive Thinking 已开启：将按问题复杂度自动估算 budget（上限 {thinking_cfg.budget} tokens）。\n"
                f"   三档：LOW 1 500 / MEDIUM 8 000 / HIGH 32 000\n"
            )
        case "budget" if len(think_tokens) >= 2:
            try:
                thinking_cfg.budget = int(think_tokens[1])
                out(f"💭 Thinking budget 已设置为 {thinking_cfg.budget} tokens\n")
            except ValueError:
                out(f"❌ 无效数字：{think_tokens[1]!r}，用法: /thinking budget <整数>\n")
        case _:
            status = "开启" if thinking_cfg.enabled else "关闭"
            adaptive_status = "✅ 开启" if thinking_cfg.adaptive else "❌ 关闭"
            out(
                f"💭 Extended Thinking: {status}，budget={thinking_cfg.budget} tokens\n"
                f"🧠 Adaptive Thinking: {adaptive_status}\n"
                f"用法: /thinking on | off | adaptive | budget <N>\n"
            )


def switch_session(
    chat_history: ChatHistoryStore,
    session_arg: str,
    default_system_prompt: str,
    skills_map: "dict[str, SkillInfo]",
    thinking_cfg: "ThinkingConfig",
    user_memory: "UserMemoryStore | None" = None,
    out: OutputFn = _stdout,
    verbose: bool = True,
) -> "Agent | None":
    """切换到指定 session 并恢复上下文。

    无参兜底 `/session` → list 已废弃；list 由 main.py 路由到独立的 `/sessions` 命令
    （见 [iter_2.md §4.9.1](../../docs/iter_2.md#491-session-列表搜索恢复phase-11)），
    本函数保留对空 session_arg 的防御性返回 None，但不再回退到 list。

    Returns:
        新 Agent；session_arg 为空时返回 None。
    """
    if not session_arg:
        out("⚠️  /session 需要 session id。用 /sessions 查看列表。\n")
        return None

    agent = make_agent(
        chat_history=chat_history,
        skills_map=skills_map,
        thinking_cfg=thinking_cfg,
        system_prompt=default_system_prompt,
        session_id=session_arg,
        user_memory=user_memory,
        verbose=verbose,
    )
    history = chat_history.load(session_arg)
    msg_count = len([m for m in history if m["role"] != "system"])
    out(f"✅ 已切换到 Session: {session_arg}（共 {msg_count} 条历史消息）")

    preview_msgs = [
        m for m in history
        if m["role"] in ("user", "assistant") and _is_visible_assistant_message(m)
    ][-_SWITCH_PREVIEW_COUNT:]
    if preview_msgs:
        out("   最近对话预览：")
        for m in preview_msgs:
            role_label = "你" if m["role"] == "user" else "Agent"
            content = (m.get("content") or "").replace("\n", " ").strip()
            preview = content[:_SWITCH_PREVIEW_LEN] + ("…" if len(content) > _SWITCH_PREVIEW_LEN else "")
            out(f"     {role_label}: {preview}")
    out("")
    return agent


_MEMORY_USAGE = (
    "⚠️  未知子命令。用法：\n"
    "    /memory                              展示全部记忆（按类别分组）\n"
    "    /memory add <类别> <key> <value...>  手动追加一条（类别：preference/background/instruction/task/correction）\n"
    "    /memory edit <id> <新内容...>        修正指定 id 的 value\n"
    "    /memory del <id>                     删除指定 id\n"
    "    /memory clear                        清空全部\n"
)


def _print_memory_list(
    user_memory: "UserMemoryStore", out: OutputFn = _stdout
) -> None:
    """`/memory` 列出全部记忆：按 category 分组 + 人性化时间 + source 标签。

    输出示例：

        🧠 用户记忆（共 7 条）

        ── 偏好（3）─────────────────────────────
          [ 1] 语言        中文                   自动     · 今天 10:23
          [ 5] 长度        ≤ 200 字               请记住   · 昨天 18:05
          [ 9] 引用风格    APA 7th                手工     · 3 天前

        ── 背景（2）────────────────────────────
          ...
    """
    from src.memory.user_memory import CATEGORY_LABELS, SOURCE_LABELS

    entries = user_memory.load_all()
    if not entries:
        out("📭 当前没有任何记忆条目。\n")
        return

    out(f"\n🧠 用户记忆（共 {len(entries)} 条）\n")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        grouped.setdefault(e["category"], []).append(e)

    for cat in MEMORY_CATEGORY_ORDER:
        items = grouped.get(cat)
        if not items:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        out(f"── {label}（{len(items)}）" + "─" * 30)
        for e in items:
            src_label = SOURCE_LABELS.get(e.get("source", "auto"), e.get("source", "auto"))
            ts = _format_relative_time(e["created_at"])
            key = e["key"][:_MEMORY_KEY_COL_WIDTH]
            value = e["value"]
            out(f"  [{e['id']:>3d}] {key:<{_MEMORY_KEY_COL_WIDTH}}  {value}")
            out(f"        {src_label:<6}  · {ts}")
        out("")
    out("")


def handle_memory(
    user_memory: "UserMemoryStore",
    cmd_parts: list[str],
    out: OutputFn = _stdout,
) -> None:
    """处理 /memory 子命令（list / add / edit / del / clear），详 _MEMORY_USAGE。

    保留 value 中的原始大小写与空格：只对子命令名（add/edit/del/clear）做 lower。
    """
    from src.memory.user_memory import MEMORY_CATEGORIES

    raw = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
    if not raw:
        _print_memory_list(user_memory, out)
        return

    head, _, rest = raw.partition(" ")
    sub_cmd = head.lower()
    rest = rest.strip()

    match sub_cmd:
        case "del":
            if not rest:
                out("⚠️  请指定记忆 ID，例：/memory del 3\n")
                return
            try:
                mid = int(rest.split()[0])
            except ValueError:
                out(f"❌ 无效 ID：{rest!r}，应为整数。\n")
                return
            deleted = user_memory.delete(mid)
            out(f"🗑️  记忆 {mid} 已删除。\n" if deleted else f"❌ 记忆 ID {mid} 不存在。\n")

        case "clear":
            count = user_memory.clear()
            out(f"🗑️  已清空全部 {count} 条记忆。\n")

        case "add":
            # add <category> <key> <value...>；value 允许含空格
            toks = rest.split(maxsplit=2)
            if len(toks) < 3:
                out(
                    "⚠️  用法：/memory add <类别> <key> <value...>\n"
                    f"    类别可选：{'/'.join(sorted(MEMORY_CATEGORIES))}\n"
                )
                return
            category, key, value = toks[0].lower(), toks[1], toks[2]
            if category not in MEMORY_CATEGORIES:
                out(
                    f"❌ 未知类别：{category!r}。\n"
                    f"    可选：{'/'.join(sorted(MEMORY_CATEGORIES))}\n"
                )
                return
            user_memory.upsert(category, key, value, source="manual")
            out(f"✍️  已记录 [{category}] {key}：{value}\n")

        case "edit":
            # edit <id> <new value...>；new value 允许含空格
            toks = rest.split(maxsplit=1)
            if len(toks) < 2:
                out("⚠️  用法：/memory edit <id> <新内容>\n")
                return
            try:
                mid = int(toks[0])
            except ValueError:
                out(f"❌ 无效 ID：{toks[0]!r}，应为整数。\n")
                return
            new_value = toks[1]
            updated = user_memory.update_value(mid, new_value)
            if updated:
                out(f"✏️  记忆 {mid} 已更新为：{new_value}\n")
            else:
                out(f"❌ 记忆 ID {mid} 不存在或新内容清洗后为空。\n")

        case _:
            out(_MEMORY_USAGE)
