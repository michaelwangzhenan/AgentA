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
from src.memory.learning_plan_store import LearningPlanStore
from src.memory.quiz_store import QuizStore
from src.memory.srs_store import SRSStore
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
    （见 [iter_2_agent.md §4.9.1](../../docs/iter_2_agent.md#491-session-列表搜索恢复phase-11)），
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


# ── Phase 2.2 — /study 命令组 ────────────────────────────────────────────────

_STUDY_USAGE = (
    "⚠️  未知子命令。用法：\n"
    "    /study                            列出全部学习计划（不含 abandoned）\n"
    "    /study list                       同上\n"
    "    /study show [plan_id]             查看 active plan / 指定 plan 全貌（含全部任务）\n"
    "    /study switch <plan_id>           切换 active plan（改 DB is_active）\n"
    "    /study load [plan_id]             把指定 plan（不传则当前 active）加载进当前\n"
    "                                      会话的 system prompt；切 session 后失效，\n"
    "                                      需重新 load（类比 skill 的 load_skill）\n"
    "    /study abandon <plan_id>          放弃指定 plan（标 abandoned，不删除数据）\n"
)

_TASK_STATUS_ICON: dict[str, str] = {"pending": "☐", "success": "✓", "skipped": "⏭"}


def _format_plan_brief(plan: dict[str, Any]) -> str:
    """一行 plan 摘要：active 标记 + id + 进度 + goal 前 40 字。"""
    marker = "▶ " if plan.get("is_active") else "  "
    pid = plan["id"]
    status_tag = "" if plan["status"] == "active" else f"[{plan['status']}]"
    total = plan.get("task_count", 0)
    done = plan.get("done_count", 0)
    goal = (plan["goal"] or "").replace("\n", " ")
    goal_short = goal[:40] + ("…" if len(goal) > 40 else "")
    weeks_suffix = f" · {plan['weeks']}周" if plan.get("weeks") else ""
    return f"  {marker}[{pid:>3d}]{status_tag} {done}/{total}{weeks_suffix}  {goal_short}"


def _print_plan_list(store: LearningPlanStore, out: OutputFn = _stdout) -> None:
    """`/study` / `/study list`：按 active 优先 + 创建时间倒序列出全部 plan。"""
    plans = store.list_plans(include_abandoned=False)
    if not plans:
        out("📭 暂无学习计划。可在对话中说\"我想 8 周准备 ML 面试\"等让 Agent 帮你新建。\n")
        return
    out(f"\n📚 学习计划列表（共 {len(plans)} 个）：")
    out(f"  {'':<2}{'ID':<6}  {'进度':<8}  {'目标':<40}")
    out(f"  {'':<2}{'-'*4:<6}  {'-'*6:<8}  {'-'*40}")
    for p in plans:
        out(_format_plan_brief(p))
    out("")


def _print_plan_detail(plan: dict[str, Any], out: OutputFn = _stdout) -> None:
    """`/study show`：plan 元信息 + 按 stage 分组的全部任务清单。"""
    active_tag = " [active]" if plan.get("is_active") else ""
    status_tag = f" [{plan['status']}]" if plan["status"] != "active" else ""
    weeks_suffix = f"，共 {plan['weeks']} 周" if plan.get("weeks") else ""
    out(f"\n📖 plan_id={plan['id']}{active_tag}{status_tag}")
    out(f"   目标：{plan['goal']}{weeks_suffix}")
    tasks = plan.get("tasks", [])
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] == "success")
    skipped = sum(1 for t in tasks if t["status"] == "skipped")
    out(f"   进度：{done}/{total} 完成（跳过 {skipped}）")
    out(f"   创建：{_format_relative_time(plan['created_at'])}    更新：{_format_relative_time(plan['updated_at'])}")
    if not tasks:
        out("   （暂无任务）\n")
        return
    out("")
    current_stage = None
    for t in tasks:
        if t["stage_idx"] != current_stage:
            current_stage = t["stage_idx"]
            out(f"   ── Stage {current_stage} ──")
        icon = _TASK_STATUS_ICON.get(t["status"], "?")
        note_suffix = f"  ({t['note']})" if t["note"] else ""
        out(f"     {icon} [id={t['id']:>3d}] {t['title']}{note_suffix}")
    out("")


def _parse_plan_id(rest: str, out: OutputFn) -> int | None:
    """从命令参数串解析正整数 plan_id；失败时打印提示并返回 None。"""
    if not rest:
        out("⚠️  请提供 plan_id。\n")
        return None
    try:
        pid = int(rest.split()[0])
    except ValueError:
        out(f"❌ 无效 plan_id：{rest!r}，应为整数。\n")
        return None
    if pid < 1:
        out(f"❌ plan_id 必须 ≥ 1，收到 {pid}。\n")
        return None
    return pid


def handle_study(
    store: LearningPlanStore,
    cmd_parts: list[str],
    session_id: str = "",
    out: OutputFn = _stdout,
) -> None:
    """
    处理 /study 子命令组（list / show / switch / load / abandon）。

    Args:
        store: LearningPlanStore 实例（main.py 传入共享单例）。
        cmd_parts: prompt_toolkit `input.split(maxsplit=1)` 的结果 — `["/study"]`
                   或 `["/study", "show 3"]`。
        session_id: 当前会话 id；用于 `load` 子命令记录 session 级激活映射。
    """
    raw = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
    if not raw:
        _print_plan_list(store, out)
        return

    head, _, rest = raw.partition(" ")
    sub_cmd = head.lower()
    rest = rest.strip()

    match sub_cmd:
        case "list":
            _print_plan_list(store, out)

        case "show":
            if not rest:
                plan = store.get_active()
                if plan is None:
                    out("📭 当前没有 active 学习计划。用 /study list 查看全部，或新建一个。\n")
                    return
                _print_plan_detail(plan, out)
                return
            pid = _parse_plan_id(rest, out)
            if pid is None:
                return
            plan = store.get_plan_with_tasks(pid)
            if plan is None:
                out(f"❌ plan_id={pid} 不存在。\n")
                return
            _print_plan_detail(plan, out)

        case "switch":
            pid = _parse_plan_id(rest, out)
            if pid is None:
                return
            if store.switch_active(pid):
                out(f"✅ 已切换 active plan → plan_id={pid}\n")
            else:
                out(f"❌ 切换失败：plan_id={pid} 不存在或已 abandoned。\n")

        case "load":
            # /study load [plan_id]：把指定 plan（不传则 active）注入当前 session prompt
            if not rest:
                active = store.get_active()
                if active is None:
                    out("📭 没有 active 学习计划可加载。用 /study list 查看全部，或新建一个；"
                        "或用 /study load <plan_id> 加载指定 plan。\n")
                    return
                pid = active["id"]
            else:
                parsed = _parse_plan_id(rest, out)
                if parsed is None:
                    return
                pid = parsed
            if store.mark_loaded(session_id, pid):
                out(f"📌 已加载 plan_id={pid} 到本会话 system prompt；"
                    "切 session 后会失效，需重新 load。\n")
            else:
                out(f"❌ 加载失败：plan_id={pid} 不存在或已 abandoned。\n")

        case "abandon":
            pid = _parse_plan_id(rest, out)
            if pid is None:
                return
            plan = store.get_plan(pid)
            if plan is None:
                out(f"❌ plan_id={pid} 不存在。\n")
                return
            if plan["status"] == "abandoned":
                out(f"⚠️  plan_id={pid} 已是 abandoned 状态。\n")
                return
            confirm = input(f"⚠️  即将放弃 plan_id={pid} \"{plan['goal']}\"（数据保留可后续 show 查看），确认请输入 yes：").strip().lower()
            if confirm == "yes":
                store.abandon_plan(pid)
                out(f"🗑️  plan_id={pid} 已标记 abandoned。\n")
            else:
                out("已取消。\n")

        case _:
            out(_STUDY_USAGE)


# ── Phase 2.3 — /quiz 命令组 ─────────────────────────────────────────────────

_QUIZ_USAGE = (
    "⚠️  未知子命令。用法：\n"
    "    /quiz                            列出最近的 quiz（不含 archived）\n"
    "    /quiz list [plan <plan_id>]      同上 / 过滤某 plan 的 quiz\n"
    "    /quiz show <quiz_set_id>         查看单个 quiz 详情（含题目 + 批改细节）\n"
    "    /quiz del <quiz_set_id>          删除指定 quiz（含全部题目；不可恢复）\n"
)


def _format_quiz_brief(quiz_set: dict[str, Any]) -> str:
    """一行 quiz 摘要：id + 状态 + 题数 + 总分 + topic 前 40 字。"""
    qid = quiz_set["id"]
    status = quiz_set["status"]
    n = quiz_set["num_questions"]
    topic = (quiz_set["topic"] or "")[:40]
    score = quiz_set.get("total_score")
    score_part = f"{score:5.1f}/100" if isinstance(score, (int, float)) else "    —    "
    plan_suffix = ""
    if quiz_set.get("plan_id"):
        plan_suffix = f"  plan {quiz_set['plan_id']}"
        if quiz_set.get("stage_idx"):
            plan_suffix += f".S{quiz_set['stage_idx']}"
    return f"  [{qid:>3d}] [{status:<8}] {n:>2}题  {score_part}  {topic}{plan_suffix}"


def _print_quiz_list(
    store: QuizStore,
    plan_id: int | None = None,
    limit: int | None = None,
    out: OutputFn = _stdout,
) -> None:
    """`/quiz` / `/quiz list`：按创建时间倒序列出 quiz；可选 plan_id 过滤。"""
    eff_limit = limit if isinstance(limit, int) and limit > 0 else config.QUIZ_HISTORY_LIST_LIMIT
    quizzes = store.list_quiz_sets(plan_id=plan_id, limit=eff_limit)
    if not quizzes:
        if plan_id is not None:
            out(f"📭 plan_id={plan_id} 暂无关联 quiz。\n")
        else:
            out("📭 暂无 quiz 历史。可在对话中说\"考考我 X\"等让 Agent 帮你新建。\n")
        return
    title_suffix = f"（plan_id={plan_id}）" if plan_id is not None else ""
    out(f"\n📝 Quiz 列表{title_suffix}（共 {len(quizzes)} 个）：")
    out(f"  {'ID':<6}  {'状态':<10}  {'题数':<6}  {'总分':<10}  主题")
    out(f"  {'-'*4:<6}  {'-'*8:<10}  {'-'*4:<6}  {'-'*8:<10}  {'-'*40}")
    for q in quizzes:
        out(_format_quiz_brief(q))
    out("")


def _print_quiz_detail(quiz: dict[str, Any], out: OutputFn = _stdout) -> None:
    """`/quiz show`：单个 quiz 完整细节（题目 + 选项 + 标答 + 批改反馈）。"""
    status_tag = f" [{quiz['status']}]"
    plan_suffix = ""
    if quiz.get("plan_id"):
        plan_suffix = f"，plan_id={quiz['plan_id']}"
        if quiz.get("stage_idx"):
            plan_suffix += f" Stage {quiz['stage_idx']}"
    out(f"\n📝 quiz_set_id={quiz['id']}{status_tag}")
    out(f"   主题：{quiz['topic']}{plan_suffix}")
    out(f"   题数：{quiz['num_questions']}")
    if quiz.get("total_score") is not None:
        out(f"   总分：{quiz['total_score']:.1f}/100")
    out(f"   创建：{_format_relative_time(quiz['created_at'])}")
    if quiz.get("graded_at"):
        out(f"   批改：{_format_relative_time(quiz['graded_at'])}")
    questions = quiz.get("questions", [])
    if not questions:
        out("   （暂无题目）\n")
        return
    out("")
    for q in questions:
        flag = " ⚠️ 自检：批改可能有偏，建议复核" if q.get("harness_flagged") else ""
        out(f"   ── 第 {q['order_idx']} 题（{q['q_type']}）──{flag}")
        out(f"   {q['stem']}")
        if q["q_type"] in ("mcq_single", "mcq_multi") and q.get("options"):
            for i, opt in enumerate(q["options"]):
                letter = chr(ord("A") + i)
                out(f"     {letter}. {opt}")
        out(f"   标答：{q['correct_answer']}")
        if q.get("user_answer"):
            out(f"   你的答案：{q['user_answer']}")
        if q.get("score") is not None:
            out(f"   得分：{q['score']:.1f}/1.0  反馈：{q.get('feedback', '')}")
        if q.get("explanation"):
            out(f"   考点：{q['explanation']}")
        out("")


def _parse_quiz_id(rest: str, out: OutputFn) -> int | None:
    """从命令参数串解析正整数 quiz_set_id；失败时打印提示并返回 None。"""
    if not rest:
        out("⚠️  请提供 quiz_set_id。\n")
        return None
    try:
        qid = int(rest.split()[0])
    except ValueError:
        out(f"❌ 无效 quiz_set_id：{rest!r}，应为整数。\n")
        return None
    if qid < 1:
        out(f"❌ quiz_set_id 必须 ≥ 1，收到 {qid}。\n")
        return None
    return qid


def handle_quiz(
    store: QuizStore,
    cmd_parts: list[str],
    out: OutputFn = _stdout,
) -> None:
    """
    处理 /quiz 子命令组（list / show / del）。

    Args:
        store: QuizStore 实例（main.py 传入共享单例）。
        cmd_parts: prompt_toolkit `input.split(maxsplit=1)` 的结果。
    """
    raw = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
    if not raw:
        _print_quiz_list(store, out=out)
        return

    head, _, rest = raw.partition(" ")
    sub_cmd = head.lower()
    rest = rest.strip()

    match sub_cmd:
        case "list":
            # 支持 `/quiz list plan <plan_id>` 过滤
            plan_id: int | None = None
            if rest.lower().startswith("plan"):
                _, _, plan_rest = rest.partition(" ")
                plan_id = _parse_plan_id(plan_rest.strip(), out)
                if plan_id is None:
                    return
            _print_quiz_list(store, plan_id=plan_id, out=out)

        case "show":
            qid = _parse_quiz_id(rest, out)
            if qid is None:
                return
            quiz = store.get_quiz_with_questions(qid)
            if quiz is None:
                out(f"❌ quiz_set_id={qid} 不存在。\n")
                return
            _print_quiz_detail(quiz, out)

        case "del":
            qid = _parse_quiz_id(rest, out)
            if qid is None:
                return
            quiz = store.get_quiz_set(qid)
            if quiz is None:
                out(f"❌ quiz_set_id={qid} 不存在。\n")
                return
            confirm = input(
                f"⚠️  即将删除 quiz_set_id={qid} \"{quiz['topic']}\""
                f"（含 {quiz['num_questions']} 题；不可恢复），确认请输入 yes："
            ).strip().lower()
            if confirm == "yes":
                store.delete_quiz_set(qid)
                out(f"🗑️  quiz_set_id={qid} 已删除。\n")
            else:
                out("已取消。\n")

        case _:
            out(_QUIZ_USAGE)


# ── /srs 命令组（Phase 2.4 §4.9.9）─────────────────────────────────────────

_SRS_USAGE = (
    "⚠️  未知子命令。用法：\n"
    "    /srs                              列出 active + suspended 卡片（默认 limit 20）\n"
    "    /srs list [active|suspended]      按状态过滤\n"
    "    /srs due                          列今天 due 的卡片（next_review_at <= now）\n"
    "    /srs show <card_id>               查看单卡完整详情（front + back + SM-2 字段）\n"
    "    /srs stats                        SRS 队列统计（总数 / due / 平均 ease / mature）\n"
    "    /srs del <card_id>                删除指定卡（不可恢复；常规用 archive 软删）\n"
)


def _format_card_brief(card: dict[str, Any]) -> str:
    """一行 SRS 卡摘要：id + 状态 + ease + interval + 来源 + 题干前 50 字。

    MCQ 卡的 front 含 ABCD 多行选项 — 摘要只取第一行（题干）+ 截断，避免选项把
    `/srs list` 表格撑成多行（详情看 `/srs show <id>`）。
    """
    cid = card["id"]
    status = card["status"]
    ef = card["ease_factor"]
    iv = card["interval_days"]
    reps = card["repetitions"]
    first_line = (card["front"] or "").split("\n", 1)[0].strip()
    front_short = first_line[:50] + ("…" if len(first_line) > 50 else "")
    src = card["source_type"]
    if src == "quiz_question" and card.get("source_ref"):
        src_suffix = f"  ← quiz_q#{card['source_ref']}"
    else:
        src_suffix = "  ← manual"
    return f"  [{cid:>3d}] [{status:<8}] ef={ef:.2f} iv={iv:>3}d reps={reps}  {front_short}{src_suffix}"


def _print_card_list(
    store: SRSStore,
    status: str | None = None,
    limit: int | None = None,
    out: OutputFn = _stdout,
) -> None:
    """`/srs` / `/srs list [active|suspended]`：按状态过滤 + 创建时间倒序列卡片。"""
    eff_limit = limit if isinstance(limit, int) and limit > 0 else config.SRS_DEFAULT_DUE_QUERY_LIMIT
    cards = store.list_cards(status=status, limit=eff_limit)
    if not cards:
        filter_suffix = f"（状态={status}）" if status else ""
        out(f"📭 暂无 SRS 卡片{filter_suffix}。可在对话中说\"把错题进 SRS / 帮我加一张卡\"等让 Agent 入队。\n")
        return
    title_suffix = f"（状态={status}）" if status else ""
    out(f"\n📚 SRS 卡片列表{title_suffix}（共 {len(cards)} 张）：")
    out(f"  {'ID':<6}  {'状态':<10}  {'ease':<7}  {'iv':<6}  {'reps':<6}  正面 + 来源")
    out(f"  {'-'*4:<6}  {'-'*8:<10}  {'-'*5:<7}  {'-'*4:<6}  {'-'*4:<6}  {'-'*50}")
    for c in cards:
        out(_format_card_brief(c))
    out("")


def _print_due_list(store: SRSStore, out: OutputFn = _stdout) -> None:
    """`/srs due`：列今天 due 的卡片（next_review_at <= now 且 status=active）。"""
    cards = store.list_due()
    if not cards:
        out("🎉 当前没有 due 卡片。明天再来 / 或新建卡片进队列。\n")
        return
    out(f"\n📅 当前 due 卡片（{len(cards)} 张）：")
    out(f"  {'ID':<6}  {'状态':<10}  {'ease':<7}  {'iv':<6}  {'reps':<6}  正面 + 来源")
    out(f"  {'-'*4:<6}  {'-'*8:<10}  {'-'*5:<7}  {'-'*4:<6}  {'-'*4:<6}  {'-'*50}")
    for c in cards:
        out(_format_card_brief(c))
    out("\n→ 在对话里说\"开始复习\"等让 Agent 带你一张张过；自评 again/hard/good/easy 4 档更新调度。\n")


def _print_card_detail(card: dict[str, Any], out: OutputFn = _stdout) -> None:
    """`/srs show <id>`：单卡完整详情。"""
    src = card["source_type"]
    src_suffix = f" ← quiz_question#{card['source_ref']}" if (src == "quiz_question" and card.get("source_ref")) else " ← manual"
    out(f"\n📚 card_id={card['id']} [{card['status']}]{src_suffix}")
    out(f"   ──── 正面 ────")
    out(f"   {card['front']}")
    out(f"   ──── 背面 ────")
    out(f"   {card['back']}")
    if card.get("note"):
        out(f"   备注：{card['note']}")
    out("")
    out(f"   ease_factor:      {card['ease_factor']:.2f}")
    out(f"   interval_days:    {card['interval_days']}")
    out(f"   repetitions:      {card['repetitions']}")
    out(f"   lapses:           {card['lapses']}")
    out(f"   next_review_at:   {card['next_review_at']}")
    out(f"   last_reviewed_at: {card['last_reviewed_at'] or '（未 review）'}")
    out(f"   created_at:       {_format_relative_time(card['created_at'])}")
    out("")


def _print_srs_stats(store: SRSStore, out: OutputFn = _stdout) -> None:
    """`/srs stats`：队列摘要统计。"""
    stats = store.stats()
    total = stats["total_active"] + stats["total_suspended"] + stats["total_archived"]
    if total == 0:
        out("📭 SRS 队列为空。\n")
        return
    out("\n📊 SRS 队列统计：")
    out(f"   总 active:    {stats['total_active']:>4} 张")
    out(f"   总 suspended: {stats['total_suspended']:>4} 张")
    out(f"   总 archived:  {stats['total_archived']:>4} 张")
    out(f"   当前 due:     {stats['due_count']:>4} 张")
    out(f"   平均 ease:    {stats['avg_ease']:.2f}（标准 ~2.5；< 2.0 偏难，> 2.6 偏易）")
    out(f"   mature 卡:    {stats['mature_count']:>4} 张（interval ≥ 21d）")
    out("")


def _parse_card_id(rest: str, out: OutputFn) -> int | None:
    """从命令参数串解析正整数 card_id；失败时打印提示并返回 None。"""
    if not rest:
        out("⚠️  请提供 card_id。\n")
        return None
    try:
        cid = int(rest.split()[0])
    except ValueError:
        out(f"❌ 无效 card_id：{rest!r}，应为整数。\n")
        return None
    if cid < 1:
        out(f"❌ card_id 必须 ≥ 1，收到 {cid}。\n")
        return None
    return cid


def handle_srs(
    store: SRSStore,
    cmd_parts: list[str],
    out: OutputFn = _stdout,
) -> None:
    """
    处理 /srs 子命令组（list / due / show / stats / del）。

    Args:
        store: SRSStore 实例（main.py 传入共享单例）。
        cmd_parts: prompt_toolkit `input.split(maxsplit=1)` 的结果。
    """
    raw = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
    if not raw:
        _print_card_list(store, out=out)
        return

    head, _, rest = raw.partition(" ")
    sub_cmd = head.lower()
    rest = rest.strip()

    match sub_cmd:
        case "list":
            status_filter: str | None = None
            if rest:
                rest_lower = rest.lower()
                if rest_lower in ("active", "suspended", "archived"):
                    status_filter = rest_lower
                else:
                    out(f"⚠️  非法状态过滤：{rest!r}，应为 active / suspended / archived 之一。\n")
                    return
            _print_card_list(store, status=status_filter, out=out)

        case "due":
            _print_due_list(store, out=out)

        case "show":
            cid = _parse_card_id(rest, out)
            if cid is None:
                return
            card = store.get_card(cid)
            if card is None:
                out(f"❌ card_id={cid} 不存在。\n")
                return
            _print_card_detail(card, out)

        case "stats":
            _print_srs_stats(store, out=out)

        case "del":
            cid = _parse_card_id(rest, out)
            if cid is None:
                return
            card = store.get_card(cid)
            if card is None:
                out(f"❌ card_id={cid} 不存在。\n")
                return
            front_short = (card["front"] or "")[:40] + ("…" if len(card["front"]) > 40 else "")
            confirm = input(
                f"⚠️  即将删除 card_id={cid}：\"{front_short}\"（硬删不可恢复；推荐用 archive 软删），"
                f"确认请输入 yes："
            ).strip().lower()
            if confirm == "yes":
                store.delete_card(cid)
                out(f"🗑️  card_id={cid} 已删除。\n")
            else:
                out("已取消。\n")

        case _:
            out(_SRS_USAGE)
