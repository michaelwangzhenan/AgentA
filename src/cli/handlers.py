"""
CLI 命令处理器 —— 各 /command 的具体逻辑

将纯业务处理函数从 main.py 中解耦，便于独立测试和复用。
"""

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import src.config as config
from src.memory.store import MemoryStore

if TYPE_CHECKING:
    from src.agent.agent import Agent, ThinkingConfig


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


def save_history(memory: MemoryStore, session_id: str, filename: str) -> None:
    """将当前 session 的 user/assistant 对话导出到 history/<filename>.md。"""
    msgs = [m for m in memory.load(session_id) if m["role"] in ("user", "assistant")]
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


def show_history(memory: MemoryStore, session_id: str) -> None:
    """展示当前 session 的历史对话摘要（角色 + 内容前 60 字）。"""
    msgs = [m for m in memory.load(session_id) if m["role"] in ("user", "assistant")]
    if not msgs:
        print("📭 当前 session 暂无对话历史。\n")
        return
    print(f"\n📋 Session {session_id} 历史摘要（共 {len(msgs)} 条）：")
    for i, msg in enumerate(msgs, 1):
        role_label = "你" if msg["role"] == "user" else "Agent"
        content = (msg.get("content") or "").replace("\n", " ")
        preview = content[:60] + ("…" if len(content) > 60 else "")
        print(f"  [{i:02d}] {role_label}: {preview}")
    print()


def list_sessions(memory: MemoryStore) -> None:
    """列出所有历史 session。"""
    sessions = memory.list_sessions()
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


def print_token_usage(agent: "Agent") -> None:
    """若本次对话有 token 统计则打印，无统计时静默跳过。"""
    if agent.last_usage:
        u = agent.last_usage
        print(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")


def make_agent(
    memory: MemoryStore,
    skills_map: dict,
    thinking_cfg: "ThinkingConfig",
    system_prompt: str,
    prompt_name: str = "",
    session_id: str | None = None,
) -> "Agent":
    """创建 Agent 实例，封装 CLI 层所需的标准参数。"""
    from src.agent.agent import Agent
    return Agent(
        verbose=True,
        memory=memory,
        session_id=session_id,
        system_prompt=system_prompt,
        prompt_name=prompt_name,
        skills=skills_map or None,
        thinking_config=thinking_cfg,
    )


def run_query(agent: "Agent", question: str) -> None:
    """执行一次问答并打印结果，捕获中断和运行时异常。"""
    print()
    try:
        reply = agent.run(question)
        print(f"Agent: {reply}\n")
        print_token_usage(agent)
    except KeyboardInterrupt:
        print("\n⚠️  已中断当前回答。\n")
    except Exception as e:
        print(f"❌ 出错了: {e}\n")


def handle_thinking(thinking_cfg: "ThinkingConfig", think_tokens: list[str]) -> None:
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
    memory: MemoryStore,
    session_arg: str,
    custom_prompts: dict[str, str],
    default_system_prompt: str,
    skills_map: dict,
    thinking_cfg: "ThinkingConfig",
) -> "tuple[Agent, str | None] | None":
    """切换到指定 session 并恢复对应 Prompt 上下文。

    Returns:
        (新 Agent, active_prompt_name)；若无 session_arg 则列出列表并返回 None。
    """
    if not session_arg:
        list_sessions(memory)
        return None

    sessions_info = {s["session_id"]: s for s in memory.list_sessions()}
    saved_prompt = sessions_info.get(session_arg, {}).get("prompt_name", "")
    active_prompt_name: str | None = saved_prompt or None
    restored_prompt = (
        custom_prompts.get(f"/{saved_prompt}")
        if saved_prompt and f"/{saved_prompt}" in custom_prompts
        else None
    )
    agent = make_agent(
        memory=memory,
        skills_map=skills_map,
        thinking_cfg=thinking_cfg,
        system_prompt=restored_prompt or default_system_prompt,
        prompt_name=saved_prompt or "",
        session_id=session_arg,
    )
    history = memory.load(session_arg)
    msg_count = len([m for m in history if m["role"] != "system"])
    prompt_hint = f"  Prompt: {active_prompt_name}" if active_prompt_name else ""
    print(f"✅ 已切换到 Session: {session_arg}（共 {msg_count} 条历史消息）{prompt_hint}\n")
    return agent, active_prompt_name
