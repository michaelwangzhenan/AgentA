"""
CLI 入口 —— 私有知识库 Agent 对话界面

命令：
    输入问题后回车即可对话
    输入 /help  查看帮助
"""

import logging
import sys
import warnings
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

# 消除 HuggingFace tokenizer 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

# override=True 确保 .env 覆盖系统环境变量
load_dotenv(override=True) 

# 设置日志：INFO 级别，显示文件名、行号和时间
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    datefmt="%H:%M:%S",
)

# 关闭第三方库的冗余日志
for _noisy in ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# src.* 模块必须在 load_dotenv() 之后导入，确保 src.config 读取到 .env 的值
from src.cli.ui import BANNER, HELP_TEXT
from src.cli.tab_complete import make_completer
from src.cli.prompt_loader import scan_prompts
from src.cli.skill_loader import scan_skills, SkillInfo
from src.cli import handlers
from src.memory.chat_history import ChatHistory
import src.config as config

# 如果用户记忆功能开启，提前导入以备 main() 中直接使用
if config.USER_MEMORY_ENABLED:
    from src.memory.user_memory import UserMemoryStore

# 自定义 Prompt 配置目录 / Skills 目录（从 config 统一管理）
PROMPTS_DIR: str = config.PROMPTS_DIR
SKILLS_DIR: str = config.SKILLS_DIR


def main() -> None:
    """CLI 主循环。"""
    print(BANNER)

    # 延迟导入重型依赖（chromadb / sentence-transformers），使 banner 能即时显示
    print("⏳ 正在初始化...", end="\r", flush=True) 
    from src.agent.agent import SYSTEM_PROMPT, ThinkingConfig

    # 共享 ChatHistory 实例，整个进程生命周期内复用
    chat_history = ChatHistory()

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

    # Extended Thinking 运行时配置（初始值从 config 读取）
    thinking_cfg = ThinkingConfig.from_config()

    # 跨 session 用户记忆（USER_MEMORY_ENABLED=false 时为 None，功能完全禁用）
    user_memory = (
        UserMemoryStore(config.USER_MEMORY_DB_PATH)
        if config.USER_MEMORY_ENABLED
        else None
    )
    if user_memory:
        cnt = len(user_memory.load_all())
        print(f"🧠 跨 session 记忆已加载（共 {cnt} 条）\n")

    agent = handlers.make_agent(chat_history, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
    print(f"💬 当前 Session: {agent.session_id}\n")

    # 当前激活的 prompt 名称（None 表示使用默认提示符 "你"）
    active_prompt_name: str | None = None

    prompt_session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=make_completer(chat_history, custom_prompts, list(skill_cmds.keys())),
        complete_while_typing=False,  # 仅 Tab 触发，不干扰正常输入
    )

    # Windows：清空控制台输入缓冲区，防止 VS Code 伪终端（ConPTY）把启动命令
    # 注入 stdin，被 prompt_toolkit 误读为用户首条输入。
    # ConPTY 下 stdin 是命名管道，msvcrt.kbhit 无效，需用 PeekNamedPipe 排空。
    # if sys.platform == "win32":
    #     import time
    #     time.sleep(0.2)  # 等待启动脚本字符全部进入缓冲区
    #     try:
    #         import os
    #         import ctypes
    #         kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    #         h = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    #         # 真实 Win32 控制台：清空输入事件队列
    #         kernel32.FlushConsoleInputBuffer(h)
    #         # ConPTY 管道：逐段 peek + read 丢弃已到达字节
    #         avail = ctypes.c_ulong(0)
    #         while (
    #             kernel32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None)
    #             and avail.value > 0
    #         ):
    #             os.read(sys.stdin.fileno(), avail.value)
    #     except Exception:
    #         pass

    while True:
        try:
            # 每轮刷新补全器，确保新建/删除的 session id 即时出现
            prompt_session.completer = make_completer(chat_history, custom_prompts, list(skill_cmds.keys()))
            input_label = f"{active_prompt_name}: " if active_prompt_name else "你: "
            user_input = prompt_session.prompt(input_label).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 再见！")
            chat_history.close()
            sys.exit(0)

        if not user_input:
            continue

        # ── 内置命令处理 ──────────────────────────────────────────────────────
        cmd_lower = user_input.lower()
        cmd_parts = user_input.split(maxsplit=1)  # 保留原始大小写用于路径
        cmd_tokens = cmd_lower.split()
        match cmd_tokens[0] if cmd_tokens else "":
            case "/quit" | "/exit":
                print("👋 再见！")
                chat_history.close()
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

                handlers.run_ingest(docs_dir=docs_dir, model=model_alias)
                continue
            case "/clear":
                chat_history.clear(agent.session_id)
                agent = handlers.make_agent(chat_history, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
                active_prompt_name = None
                print(f"✅ 对话历史已清空，Agent 已重置。\n💬 新 Session: {agent.session_id}\n")
                continue
            case "/history":
                handlers.show_history(chat_history, agent.session_id)
                continue
            case "/session":
                session_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                result = handlers.switch_session(
                    chat_history, session_arg, custom_prompts, SYSTEM_PROMPT, skills_map,
                    thinking_cfg, user_memory=user_memory
                )
                if result:
                    agent, active_prompt_name = result
                continue
            case "/del-session":
                target_id = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not target_id:
                    print("⚠️  请指定要删除的 session ID，例：/del-session <id>\n")
                elif target_id == agent.session_id:
                    print("⚠️  不能删除当前活跃 session，请先用 /session <id> 切换到其他 session 后再删除。\n")
                else:
                    deleted = chat_history.delete_session(target_id)
                    if deleted:
                        print(f"🗑️  Session {target_id} 已彻底删除。\n")
                    else:
                        print(f"❌ Session {target_id} 不存在。\n")
                continue
            case "/clean-session":
                sessions = chat_history.list_sessions()
                if not sessions:
                    print("📭 暂无历史 session 记录，无需清空。\n")
                else:
                    confirm = input(f"⚠️  即将清空全部 {len(sessions)} 个 session 记录（不可恢复），确认请输入 yes：").strip().lower()
                    if confirm == "yes":
                        count = chat_history.clean_all_sessions()
                        # 当前 Agent 的历史也已被清除，重建一个新 session
                        agent = handlers.make_agent(chat_history, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
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
                agent = handlers.make_agent(
                    chat_history, skills_map, thinking_cfg,
                    _base_prompt, active_prompt_name or "", agent.session_id,
                    user_memory=user_memory,
                )
                cmds_str = ', '.join(skill_cmds) if skill_cmds else '（无）'
                print(f"🔄 Skills 已重新加载，共 {len(skill_cmds)} 个：{cmds_str}\n")
                continue
            case "/save":
                save_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not save_arg:
                    print("⚠️  请指定文件名，例：/save my-chat\n")
                else:
                    handlers.save_history(chat_history, agent.session_id, save_arg)
                continue
            case "/thinking":
                think_tokens = (
                    cmd_parts[1].strip().lower().split() if len(cmd_parts) > 1 else []
                )
                handlers.handle_thinking(thinking_cfg, think_tokens)
                continue
            case "/memory":
                if user_memory is None:
                    print("⚠️  跨 session 记忆功能未启用（请在 .env 中设置 USER_MEMORY_ENABLED=true）\n")
                else:
                    handlers.handle_memory(user_memory, cmd_parts)
                continue
        cmd_name = cmd_tokens[0] if cmd_tokens else ""
        if cmd_name in custom_prompts:
            question = user_input[len(cmd_name):].strip()
            active_prompt_name = cmd_name[1:]  # 去掉 / 前缀，如 "5g-expert"
            agent = handlers.make_agent(
                chat_history, skills_map, thinking_cfg,
                custom_prompts[cmd_name], active_prompt_name,
                user_memory=user_memory,
            )
            print(f"🎭 已切换到 Prompt：{active_prompt_name}  (新 Session: {agent.session_id})\n")
            if question:
                handlers.run_query(agent, question)
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
                handlers.run_query(agent, question)
            continue

        # ── 正常问答 ──────────────────────────────────────────────────────────
        handlers.run_query(agent, user_input)


if __name__ == "__main__":
    main()
