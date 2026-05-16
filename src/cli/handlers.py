"""
CLI 命令处理器 —— 各 /command 的具体逻辑

将纯业务处理函数从 main.py 中解耦，便于独立测试和复用。
"""

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import src.config as config
from src.memory.chat_history import ChatHistory
from src.memory.user_memory import UserMemoryStore

# 历史记录预览截断长度
_HISTORY_PREVIEW_LEN: int = 60

if TYPE_CHECKING:
    from src.agent.agent import Agent, ThinkingConfig
    from src.cli.skill_loader import SkillInfo


def quit_sys(chat_history: ChatHistory, user_memory: UserMemoryStore | None) -> None:
    import sys
    chat_history.close()
    if user_memory is not None:
        user_memory.close()
    sys.exit(0)

def run_ingest(docs_dir: str | None = None, model: str | None = None) -> None:
    """在 CLI 中触发文档入库，可指定文档目录和 embedding 模型。"""
    import os
    target_dir = docs_dir or config.DOCS_DIR
    target_model = model or config.DEFAULT_EMBEDDING_ALIAS
    if not os.path.exists(target_dir):
        print(f"❌ 目录不存在: {target_dir}\n")
        return
    if not os.path.isdir(target_dir):
        print(f"❌ 路径不是目录: {target_dir}\n")
        return
    model_name, collection_name = config.resolve_embedding(target_model)
    print(f"\n⏳ 正在扫描 {target_dir}")
    print(f"   Embedding 模型: {model_name}  →  collection: {collection_name}\n")
    try:
        from src.rag.ingest import ingest_all
        ingest_all(docs_dir=target_dir, model=target_model)
        print()
    except Exception as e:
        print(f"❌ 入库失败: {e}\n")


def save_history(chat_history: ChatHistory, session_id: str, filename: str) -> None:
    """将当前 session 的 user/assistant 对话导出到 history/<filename>.md。"""
    msgs = [m for m in chat_history.load(session_id) if m["role"] in ("user", "assistant")]
    if not msgs:
        print("📭 当前 session 暂无对话历史，无可导出内容。\n")
        return

    stem = re.sub(r'\.(md|txt)$', '', filename, flags=re.IGNORECASE)
    safe_name = re.sub(r'[^\w\-.]', '_', stem)
    if not safe_name or safe_name.startswith('.'):
        print(f"❌ 无效文件名：{filename!r}\n")
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
        print(f"💾 对话已导出到 {out_path}（共 {len(msgs)} 条）\n")
    except OSError as e:
        print(f"❌ 导出失败: {e}\n")


def show_history(chat_history: ChatHistory, session_id: str) -> None:
    """展示当前 session 的历史对话摘要（角色 + 内容前 60 字）。"""
    msgs = [m for m in chat_history.load(session_id) if m["role"] in ("user", "assistant")]
    if not msgs:
        print("📭 当前 session 暂无对话历史。\n")
        return
    print(f"\n📋 Session {session_id} 历史摘要（共 {len(msgs)} 条）：")
    for i, msg in enumerate(msgs, 1):
        role_label = "你" if msg["role"] == "user" else "Agent"
        content = (msg.get("content") or "").replace("\n", " ")
        preview = content[:_HISTORY_PREVIEW_LEN] + ("…" if len(content) > _HISTORY_PREVIEW_LEN else "")
        print(f"  [{i:02d}] {role_label}: {preview}")
    print()


def list_sessions(chat_history: ChatHistory) -> None:
    """列出所有历史 session。"""
    sessions = chat_history.list_sessions()
    if not sessions:
        print("📭 暂无历史 session 记录。\n")
        return
    print(f"\n📚 历史 Session 列表（共 {len(sessions)} 个）：")
    print(f"  {'ID':<10}  {'Create On':<19}  {'messages':<12}  {'Prompt':<16}  {'1st Question':<40}")
    print(f"  {'-'*8:<10}  {'-'*19:<19}  {'-'*12:<12}  {'-'*16:<16}  {'-'*40}")
    for s in sessions:
        created = s["created_at"][:19].replace("T", " ")
        sid_short = s["session_id"][:8]
        prompt_label = s["prompt_name"] or "默认"
        first_msg = (s["first_user_msg"] or "（无用户消息）")[:40]
        print(f"  {sid_short:<10}  {created:<19}  {s['msg_count']:<12}  {prompt_label:<16}  {first_msg:<40}")
    print()


def make_agent(
    chat_history: ChatHistory,
    skills_map: "dict[str, SkillInfo]",
    thinking_cfg: "ThinkingConfig",
    system_prompt: str,
    prompt_name: str = "",
    session_id: str | None = None,
    user_memory: "UserMemoryStore | None" = None,
) -> "Agent":
    imp = config.IMP_METHOD
    if imp == "AUTOGPT":
        from src.agent.autogpt_agent import AutoGPTAgent
        return AutoGPTAgent(
            verbose=True,
            chat_history=chat_history,
            session_id=session_id,
            system_prompt=system_prompt,
            prompt_name=prompt_name,
            skills=skills_map or None,
            thinking_config=thinking_cfg,
            user_memory=user_memory,
        )
    if imp == "LANGCHAIN":
        from src.agent.lc_agent import LangChainAgent
        return LangChainAgent(
            verbose=True,
            chat_history=chat_history,
            session_id=session_id,
            system_prompt=system_prompt,
            prompt_name=prompt_name,
            skills=skills_map or None,
            thinking_config=thinking_cfg,
            user_memory=user_memory,
        )
    from src.agent.agent import Agent
    return Agent(
        verbose=True,
        chat_history=chat_history,
        session_id=session_id,
        system_prompt=system_prompt,
        prompt_name=prompt_name,
        skills=skills_map or None,
        thinking_config=thinking_cfg,
        user_memory=user_memory,
    )


def _print_token_usage(agent: "Agent") -> None:
    """若本次对话有 token 统计则打印，无统计时静默跳过。"""
    if agent.last_usage:
        u = agent.last_usage
        print(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")


def run_query(agent: "Agent", question: str) -> None:
    """执行一次问答并打印结果，捕获中断和运行时异常。"""
    print()
    try:
        reply = agent.run(question)
        print(f"Agent: {reply}\n")
        _print_token_usage(agent)
    except KeyboardInterrupt:
        print("\n⚠️  已中断当前回答。\n")
    except Exception as e:
        print(f"❌ 出错了: {e}\n")


def handle_thinking_cfg(thinking_cfg: "ThinkingConfig", think_tokens: list[str]) -> None:
    """处理 /thinking 子命令，直接修改 thinking_cfg 状态并打印结果。"""
    match think_tokens[0] if think_tokens else "":
        case "on":
            thinking_cfg.enabled = True
            adaptive_hint = "，自动 budget 已开启" if thinking_cfg.adaptive else ""
            print(f"💭 Extended Thinking 已开启（budget={thinking_cfg.budget} tokens{adaptive_hint}）。\n")
        case "off":
            thinking_cfg.enabled = False
            print("💭 Extended Thinking 已关闭\n")
        case "adaptive":
            thinking_cfg.enabled = True
            thinking_cfg.adaptive = True
            print(
                f"🧠 Adaptive Thinking 已开启：将按问题复杂度自动估算 budget（上限 {thinking_cfg.budget} tokens）。\n"
                f"   三档：LOW 1 500 / MEDIUM 8 000 / HIGH 32 000\n"
            )
        case "budget" if len(think_tokens) >= 2:
            try:
                thinking_cfg.budget = int(think_tokens[1])
                print(f"💭 Thinking budget 已设置为 {thinking_cfg.budget} tokens\n")
            except ValueError:
                print(f"❌ 无效数字：{think_tokens[1]!r}，用法: /thinking budget <整数>\n")
        case _:
            status = "开启" if thinking_cfg.enabled else "关闭"
            adaptive_status = "✅ 开启" if thinking_cfg.adaptive else "❌ 关闭"
            print(
                f"💭 Extended Thinking: {status}，budget={thinking_cfg.budget} tokens\n"
                f"🧠 Adaptive Thinking: {adaptive_status}\n"
                f"用法: /thinking on | off | adaptive | budget <N>\n"
            )


def switch_session(
    chat_history: ChatHistory,
    session_arg: str,
    custom_prompts: dict[str, str],
    default_system_prompt: str,
    skills_map: "dict[str, SkillInfo]",
    thinking_cfg: "ThinkingConfig",
    user_memory: "UserMemoryStore | None" = None,
) -> "tuple[Agent, str | None] | None":
    """切换到指定 session 并恢复对应 Prompt 上下文。

    Returns:
        (新 Agent, active_prompt_name)；若无 session_arg 则列出列表并返回 None。
    """
    if not session_arg:
        list_sessions(chat_history)
        return None

    sessions_info = {s["session_id"]: s for s in chat_history.list_sessions()}
    saved_prompt = sessions_info.get(session_arg, {}).get("prompt_name", "")
    active_prompt_name: str | None = saved_prompt or None
    restored_prompt = (
        custom_prompts.get(f"/{saved_prompt}")
        if saved_prompt and f"/{saved_prompt}" in custom_prompts
        else None
    )
    agent = make_agent(
        chat_history=chat_history,
        skills_map=skills_map,
        thinking_cfg=thinking_cfg,
        system_prompt=restored_prompt or default_system_prompt,
        prompt_name=saved_prompt or "",
        session_id=session_arg,
        user_memory=user_memory,
    )
    history = chat_history.load(session_arg)
    msg_count = len([m for m in history if m["role"] != "system"])
    prompt_hint = f"  Prompt: {active_prompt_name}" if active_prompt_name else ""
    print(f"✅ 已切换到 Session: {session_arg}（共 {msg_count} 条历史消息）{prompt_hint}\n")
    return agent, active_prompt_name


def handle_memory(user_memory: "UserMemoryStore", cmd_parts: list[str]) -> None:
    """
    处理 /memory 子命令：
        /memory            — 展示全部记忆条目
        /memory del <id>   — 删除指定 id 的记忆
        /memory clear      — 清空全部记忆
    """
    from src.memory.user_memory import CATEGORY_LABELS

    sub_tokens = cmd_parts[1].strip().lower().split() if len(cmd_parts) > 1 else []
    sub_cmd = sub_tokens[0] if sub_tokens else ""

    match sub_cmd:
        case "":   # /memory — 展示全部
            entries = user_memory.load_all()
            if not entries:
                print("📭 当前没有任何记忆条目。\n")
                return
            print(f"\n🧠 用户记忆（共 {len(entries)} 条）：\n")
            for e in entries:
                label = CATEGORY_LABELS.get(e["category"], e["category"])
                ts = e["created_at"][:16].replace("T", " ")
                print(f"  [{e['id']:3d}] [{label}] {e['key']}：{e['value']}")
                print(f"         记录于 {ts}")
            print()

        case "del":   # /memory del <id>
            if len(sub_tokens) < 2:
                print("⚠️  请指定记忆 ID，例：/memory del 3\n")
                return
            try:
                mid = int(sub_tokens[1])
                deleted = user_memory.delete(mid)
                if deleted:
                    print(f"🗑️  记忆 {mid} 已删除。\n")
                else:
                    print(f"❌ 记忆 ID {mid} 不存在。\n")
            except ValueError:
                print(f"❌ 无效 ID：{sub_tokens[1]!r}，应为整数。\n")

        case "clear":   # /memory clear
            count = user_memory.clear()
            print(f"🗑️  已清空全部 {count} 条记忆。\n")

        case _:
            print("⚠️  未知子命令。用法: /memory | /memory del <id> | /memory clear\n")
