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
    输入 /quit 或 /exit 或 Ctrl+C 退出
"""

import logging
import sys
import warnings

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

from src.cli.tab_complete import make_completer

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

from src.agent.agent import Agent
from src.memory.store import MemoryStore
import src.config as config


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
  /quit                      退出程序
  /exit                      退出程序（同 /quit）

模型别名：
  en  →  all-MiniLM-L6-v2   英文/多语言
  zh  →  BAAI/bge-small-zh   中文优化

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
    for s in sessions:
        created = s["created_at"][:19].replace("T", " ")
        first_msg = (s["first_user_msg"] or "（无用户消息）")[:40]
        print(f"  {s['session_id']}  [{created}]  {s['msg_count']} 条  首问: {first_msg}")
    print()


def main() -> None:
    """CLI 主循环。"""
    print(BANNER)

    # 共享 MemoryStore 实例，整个进程生命周期内复用
    memory = MemoryStore()
    agent = Agent(verbose=True, memory=memory)
    print(f"💬 当前 Session: {agent.session_id}\n")

    prompt_session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=make_completer(memory),
        complete_while_typing=False,  # 仅 Tab 触发，不干扰正常输入
    )

    while True:
        try:
            # 每轮刷新补全器，确保新建/删除的 session id 即时出现
            prompt_session.completer = make_completer(memory)
            user_input = prompt_session.prompt("你: ").strip()
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
                agent = Agent(verbose=True, memory=memory)
                print(f"✅ 对话历史已清空，Agent 已重置。\n💬 新 Session: {agent.session_id}\n")
                continue
            case "/history":
                _show_history(memory, agent.session_id)
                continue
            case "/session":
                session_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if session_arg:
                    # 切换到指定 session
                    agent = Agent(verbose=True, session_id=session_arg, memory=memory)
                    history = memory.load(session_arg)
                    msg_count = len([m for m in history if m["role"] != "system"])
                    print(f"✅ 已切换到 Session: {session_arg}（共 {msg_count} 条历史消息）\n")
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
                        agent = Agent(verbose=True, memory=memory)
                        print(f"🗑️  已清空全部 {count} 个 session 记录。新 Session: {agent.session_id}\n")
                    else:
                        print("已取消。\n")
                continue

        # ── 正常问答 ──────────────────────────────────────────────────────────
        print()
        try:
            reply = agent.run(user_input)
            print(f"Agent: {reply}\n")
        except KeyboardInterrupt:
            print("\n⚠️  已中断当前回答。\n")
        except Exception as e:
            print(f"❌ 出错了: {e}\n")


if __name__ == "__main__":
    main()
