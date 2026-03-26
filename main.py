"""
CLI 入口 —— 私有知识库 Agent 对话界面

使用方式：
    python main.py

命令：
    输入问题后回车即可对话
    输入 /help              查看帮助
    输入 /ingest            重新扫描默认 docs/ 目录并入库（默认模型）
    输入 /ingest <目录> [-m en|zh]  扫描指定目录，可选指定模型
    输入 /clear             清空当前 session 的对话历史并重置 Agent
    输入 /history           查看当前 session 的对话摘要
    输入 /session           列出所有历史 session
    输入 /session <id>      切换到指定 session 并恢复历史
    输入 /del-session <id>   彻底删除指定历史 session 的所有记录
    输入 /clean-session       清空所有历史 session 的记录
    输入 /reload-prompts      重新扫描 advanced/prompts/ 目录，刷新自定义 Prompt 命令
    输入 /<prompt_name> [问题] 切换到指定自定义 Prompt 并重置 Agent，可附带首个问题
    输入 /save <文件名>    导出当前 session 完整对话到 history/<文件名>.md
    输入 /quit 或 /exit 或 Ctrl+C 退出
"""

import logging
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

from src.cli.tab_complete import make_completer
from src.cli.prompt_loader import scan_prompts
from src.cli.skill_loader import scan_skills, SkillInfo

# 消除 HuggingFace tokenizer 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

load_dotenv(override=True)  # override=True 确保 .env 覆盖系统环境变量

# 设置日志：INFO 级别，显示文件名、行号和时间
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    datefmt="%H:%M:%S",
)
# 关闭第三方库的冗余日志
for _noisy in ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from src.memory.store import MemoryStore
import src.config as config


# 自定义 Prompt 配置目录
PROMPTS_DIR: str = "advanced/prompts"
# Skills 目录
SKILLS_DIR: str = "advanced/skills"

BANNER = """
╔══════════════════════════════════════════════╗
║         私有知识库 Agent  v0.1               ║
║  LLM: {provider:<38} ║
║  输入 /help 查看命令列表                     ║
╚══════════════════════════════════════════════╝
""".format(provider=config.ACTIVE_PROVIDER)

HELP_TEXT = """
可用命令：
  /help                      显示本帮助信息
  /ingest                    扫描默认 docs/ 目录并入库（模型: .env EMBEDDING_MODEL）
  /ingest <目录>             扫描指定目录，例：/ingest D:/mydata
  /ingest <目录> -m zh       指定目录 + 中文模型（BAAI/bge-small-zh）
  /ingest <目录> -m en       指定目录 + 英文模型（all-MiniLM-L6-v2）
  /ingest -m zh              默认目录 + 中文模型
  /clear                     清空当前 session 的对话历史并重置 Agent
  /history                   查看当前 session 的历史对话摘要
  /session                   列出所有历史 session
  /session <id>              切换到指定 session 并恢复历史
  /del-session <id>          彻底删除指定历史 session 的所有记录（不可恢复）
  /clean-session             清空所有历史 session 的记录（不可恢复）
  /reload-prompts            重新扫描 advanced/prompts/ 目录，刷新自定义 Prompt 命令
  /reload-skills             重新扫描 advanced/skills/ 目录，刷新 Skill 列表
  /<prompt_name> [问题]      切换到指定自定义 Prompt 并重置 Agent，可附带首个问题
  /<skill_name> [问题]       激活指定 Skill（注入 Skill 指令到当前会话），可附带首个问题
  /save <文件名>             导出当前 session 完整对话到 history/<文件名>.md
  /quit                      退出程序
  /exit                      退出程序（同 /quit）

模型别名：
  en  →  all-MiniLM-L6-v2   英文/多语言
  zh  →  BAAI/bge-small-zh   中文优化

自定义 Prompt：
  在 advanced/prompts/ 目录下放置 <名称>.prompt.md 文件即可。
  文件名即命令名（如 5g-expert.prompt.md → /5g-expert），名称只允许字母、数字、- 和 _。

Skills：
  在 advanced/skills/<名称>/SKILL.md 放置符合 agentskills.io 规范的 Skill。
  Agent 会自动发现并在合适时调用；也可用 /<skill_name> [问题] 手动激活。

直接输入问题即可开始对话。
"""


def _run_ingest(docs_dir: str | None = None, model: str | None = None) -> None:
    """在 CLI 中触发文档入库，可指定文档目录和 embedding 模型。"""
    import os
    target_dir = docs_dir or config.DOCS_DIR
    target_model = model or config.DEFAULT_EMBEDDING_ALIAS
    # 路径校验
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
        from rag.ingest import ingest_all
        ingest_all(docs_dir=target_dir, model=target_model)
        print()
    except Exception as e:
        print(f"❌ 入库失败: {e}\n")


def _save_history(memory: MemoryStore, session_id: str, filename: str) -> None:
    """将当前 session 的 user/assistant 对话导出到 history/<filename>.md。"""
    msgs = [m for m in memory.load(session_id) if m["role"] in ("user", "assistant")]
    if not msgs:
        print("📭 当前 session 暂无对话历史，无可导出内容。\n")
        return

    # 去掉用户传入的扩展名后再校验，统一追加 .md
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


def _show_history(memory: MemoryStore, session_id: str) -> None:
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


def _list_sessions(memory: MemoryStore) -> None:
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


def main() -> None:
    """CLI 主循环。"""
    print(BANNER)

    # 延迟导入重型依赖（chromadb / sentence-transformers），使 banner 能即时显示
    print("⏳ 正在初始化...", end="\r", flush=True)
    from src.agent.agent import Agent, SYSTEM_PROMPT
    print(" " * 30, end="\r", flush=True)

    # 共享 MemoryStore 实例，整个进程生命周期内复用
    memory = MemoryStore()

    # 启动时扫描自定义 Prompt 目录
    custom_prompts: dict[str, str] = scan_prompts(PROMPTS_DIR)
    if custom_prompts:
        print(f"🎭 已加载自定义 Prompt：{', '.join(custom_prompts)}\n")

    # 启动时扫描 Skills 目录
    skills_map: dict[str, SkillInfo] = scan_skills(SKILLS_DIR)
    # 供 CLI 匹配的 {/name: body} 字典（tab 补全 + 手动激活）
    skill_cmds: dict[str, str] = {f"/{name}": info.body for name, info in skills_map.items()}
    if skills_map:
        print(f"🔧 已加载 Skills：{', '.join(skill_cmds)}\n")

    agent = Agent(verbose=True, memory=memory, skills=skills_map or None)
    print(f"💬 当前 Session: {agent.session_id}\n")

    # 当前激活的 prompt 名称（None 表示使用默认提示符 "你"）
    active_prompt_name: str | None = None

    prompt_session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=make_completer(memory, custom_prompts, list(skill_cmds.keys())),
        complete_while_typing=False,  # 仅 Tab 触发，不干扰正常输入
    )

    # Windows：清空控制台输入缓冲区，防止 activate.bat 等激活脚本产生的残留
    # 输入事件被 prompt_toolkit 误读为用户首条输入
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.FlushConsoleInputBuffer(
                ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            )
        except Exception:
            pass

    while True:
        try:
            # 每轮刷新补全器，确保新建/删除的 session id 即时出现
            prompt_session.completer = make_completer(memory, custom_prompts, list(skill_cmds.keys()))
            input_label = f"{active_prompt_name}: " if active_prompt_name else "你: "
            user_input = prompt_session.prompt(input_label).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 再见！")
            memory.close()
            sys.exit(0)

        if not user_input:
            continue

        # ── 内置命令处理 ──────────────────────────────────────────────────────
        cmd_lower = user_input.lower()
        cmd_parts = user_input.split(maxsplit=1)  # 保留原始大小写用于路径
        match cmd_lower.split()[0] if cmd_lower.split() else "":
            case "/quit" | "/exit":
                print("👋 再见！")
                memory.close()
                sys.exit(0)
            case "/help":
                print(HELP_TEXT)
                continue
            case "/ingest":
                # 解析 /ingest [<目录>] [-m <模型>]
                # 支持: /ingest  /ingest ./docs_zh  /ingest ./docs_zh -m zh  /ingest -m zh
                raw_args = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                docs_dir: str | None = None
                model_alias: str | None = None

                # 简单手动解析，避免引入 argparse
                tokens = raw_args.split()
                i = 0
                while i < len(tokens):
                    if tokens[i] in ("-m", "--model") and i + 1 < len(tokens):
                        model_alias = tokens[i + 1]
                        i += 2
                    elif not tokens[i].startswith("-"):
                        docs_dir = tokens[i]
                        i += 1
                    else:
                        i += 1  # 忽略未知标志

                _run_ingest(docs_dir=docs_dir, model=model_alias)
                continue
            case "/clear":
                memory.clear(agent.session_id)
                agent = Agent(verbose=True, memory=memory, skills=skills_map or None)
                active_prompt_name = None
                print(f"✅ 对话历史已清空，Agent 已重置。\n💬 新 Session: {agent.session_id}\n")
                continue
            case "/history":
                _show_history(memory, agent.session_id)
                continue
            case "/session":
                session_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if session_arg:
                    # 先查 session 的 prompt_name，再恢复对应的 system_prompt
                    sessions_info = {s["session_id"]: s for s in memory.list_sessions()}
                    saved_prompt = sessions_info.get(session_arg, {}).get("prompt_name", "")
                    active_prompt_name = saved_prompt or None
                    restored_prompt = (
                        custom_prompts.get(f"/{saved_prompt}")
                        if saved_prompt and f"/{saved_prompt}" in custom_prompts
                        else None
                    )
                    agent = Agent(
                        verbose=True,
                        session_id=session_arg,
                        memory=memory,
                        skills=skills_map or None,
                        system_prompt=restored_prompt or SYSTEM_PROMPT,
                        prompt_name=saved_prompt or "",
                    )
                    history = memory.load(session_arg)
                    msg_count = len([m for m in history if m["role"] != "system"])
                    prompt_hint = f"  Prompt: {active_prompt_name}" if active_prompt_name else ""
                    print(f"✅ 已切换到 Session: {session_arg}（共 {msg_count} 条历史消息）{prompt_hint}\n")
                else:
                    # 列出所有历史 session
                    _list_sessions(memory)
                continue
            case "/del-session":
                target_id = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not target_id:
                    print("⚠️  请指定要删除的 session ID，例：/del-session <id>\n")
                elif target_id == agent.session_id:
                    print("⚠️  不能删除当前活跃 session，请先用 /session <id> 切换到其他 session 后再删除。\n")
                else:
                    deleted = memory.delete_session(target_id)
                    if deleted:
                        print(f"🗑️  Session {target_id} 已彻底删除。\n")
                    else:
                        print(f"❌ Session {target_id} 不存在。\n")
                continue
            case "/clean-session":
                sessions = memory.list_sessions()
                if not sessions:
                    print("📭 暂无历史 session 记录，无需清空。\n")
                else:
                    confirm = input(f"⚠️  即将清空全部 {len(sessions)} 个 session 记录（不可恢复），确认请输入 yes：").strip().lower()
                    if confirm == "yes":
                        count = memory.clean_all_sessions()
                        # 当前 Agent 的历史也已被清除，重建一个新 session
                        agent = Agent(verbose=True, memory=memory, skills=skills_map or None)
                        active_prompt_name = None
                        print(f"🗑️  已清空全部 {count} 个 session 记录。新 Session: {agent.session_id}\n")
                    else:
                        print("已取消。\n")
                continue
            case "/reload-prompts":
                custom_prompts = scan_prompts(PROMPTS_DIR)
                cmds_str = ', '.join(custom_prompts) if custom_prompts else '（无）'
                print(f"🔄 Prompt 已重新加载，共 {len(custom_prompts)} 个：{cmds_str}\n")
                continue
            case "/reload-skills":
                skills_map = scan_skills(SKILLS_DIR)
                skill_cmds = {f"/{name}": info.body for name, info in skills_map.items()}
                # 重建 Agent，使 system_prompt 中的 catalog 立即刷新
                _base_prompt = (
                    custom_prompts.get(f"/{active_prompt_name}", SYSTEM_PROMPT)
                    if active_prompt_name
                    else SYSTEM_PROMPT
                )
                agent = Agent(
                    verbose=True,
                    memory=memory,
                    session_id=agent.session_id,
                    system_prompt=_base_prompt,
                    prompt_name=active_prompt_name or "",
                    skills=skills_map or None,
                )
                cmds_str = ', '.join(skill_cmds) if skill_cmds else '（无）'
                print(f"🔄 Skills 已重新加载，共 {len(skill_cmds)} 个：{cmds_str}\n")
                continue
            case "/save":
                save_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not save_arg:
                    print("⚠️  请指定文件名，例：/save my-chat\n")
                else:
                    _save_history(memory, agent.session_id, save_arg)
                continue
        cmd_name = cmd_lower.split()[0] if cmd_lower.split() else ""
        if cmd_name in custom_prompts:
            question = user_input[len(cmd_name):].strip()
            active_prompt_name = cmd_name[1:]  # 去掉 / 前缀，如 "5g-expert"
            agent = Agent(
                verbose=True,
                memory=memory,
                system_prompt=custom_prompts[cmd_name],
                prompt_name=active_prompt_name,
                skills=skills_map or None,
            )
            print(f"🎭 已切换到 Prompt：{active_prompt_name}  (新 Session: {agent.session_id})\n")
            if question:
                print()
                try:
                    reply = agent.run(question)
                    print(f"Agent: {reply}\n")
                    if agent.last_usage:
                        u = agent.last_usage
                        print(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")
                except KeyboardInterrupt:
                    print("\n⚠️  已中断当前回答。\n")
                except Exception as e:
                    print(f"❌ 出错了: {e}\n")
            continue

        # ── 用户显式 Skill 激活 ──────────────────────────────────────────────
        if cmd_name in skill_cmds:
            question = user_input[len(cmd_name):].strip()
            skill_name = cmd_name[1:]  # 去掉 / 前缀
            skill_body = skill_cmds[cmd_name]
            activated = agent.activate_skill(skill_name, skill_body)
            if activated:
                print(f"🔧 Skill [{skill_name}] 已激活（注入当前会话）\n")
            else:
                print(f"🔧 Skill [{skill_name}] 已处于激活状态\n")
            if question:
                print()
                try:
                    reply = agent.run(question)
                    print(f"Agent: {reply}\n")
                    if agent.last_usage:
                        u = agent.last_usage
                        print(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")
                except KeyboardInterrupt:
                    print("\n⚠️  已中断当前回答。\n")
                except Exception as e:
                    print(f"❌ 出错了: {e}\n")
            continue

        # ── 正常问答 ──────────────────────────────────────────────────────────
        print()
        try:
            reply = agent.run(user_input)
            print(f"Agent: {reply}\n")
            if agent.last_usage:
                u = agent.last_usage
                print(f"  📊 Token：输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 合计 {u.total_tokens}\n")
        except KeyboardInterrupt:
            print("\n⚠️  已中断当前回答。\n")
        except Exception as e:
            print(f"❌ 出错了: {e}\n")


if __name__ == "__main__":
    main()
