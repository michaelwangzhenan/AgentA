"""
CLI 入口 —— 私有知识库 Agent 对话界面

启动：`python -m src.cli.main`（须在仓库根目录，相对路径 logs/ .agenta/ 按 cwd 解析）

命令：
    输入问题后回车即可对话
    输入 /help  查看帮助
"""

import logging
import os
import sys
import threading
import warnings
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

# 消除 HuggingFace tokenizer 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

# override=True 确保 .env 覆盖系统环境变量
load_dotenv(override=True)

# Windows 控制台默认 cp936 (GBK) encode emoji 会抛 UnicodeEncodeError 让进程崩，
# 统一覆写到 UTF-8；errors="replace" 兜底，终端字体不支持时降级为 ? 替代而不崩。
# 必须在 _Tee 包装 sys.stdout/stderr 之前调，否则 Tee 持有的是未覆写过的原 stream。
for _stream in (sys.stdout, sys.stderr):
    if _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")


class _LogFile:
    """CLI 共享日志文件：stdout / stderr 两个 _Tee 都写它；写满按大小滚动并保留备份。

    单一写入口（带锁）便于在 write 里统一判断体积并滚动，避免两个 _Tee 各持句柄打架。
    """

    def __init__(self, path: Path, mode: str, max_bytes: int, backup_count: int):
        self._path = path
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        # buffering=1 → 行缓冲，进程异常退出也不丢日志
        self._fh = open(path, mode, encoding="utf-8", buffering=1)
        self._size = self._fh.tell()  # append 模式从当前文件尾算起

    def write(self, s: str) -> None:
        with self._lock:
            self._fh.write(s)
            self._size += len(s.encode("utf-8", "replace"))
            if self._max_bytes and self._size >= self._max_bytes:
                self._rollover()

    def flush(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
            except Exception:
                pass

    def _rollover(self) -> None:
        try:
            self._fh.close()
            oldest = Path(f"{self._path}.{self._backup_count}")
            if oldest.exists():
                oldest.unlink()
            for i in range(self._backup_count - 1, 0, -1):
                src = Path(f"{self._path}.{i}")
                if src.exists():
                    src.replace(Path(f"{self._path}.{i + 1}"))
            if self._path.exists():
                self._path.replace(Path(f"{self._path}.1"))
        except OSError:
            pass
        self._fh = open(self._path, "w", encoding="utf-8", buffering=1)
        self._size = 0


class _Tee:
    """把写入同时转发给原 stream 和共享日志文件；其余属性（isatty/fileno/encoding 等）透传给原 stream，避免破坏 prompt_toolkit 的 TTY 检测。"""

    def __init__(self, original, logfile: "_LogFile"):
        self._original = original
        self._logfile = logfile

    def write(self, s):
        n = self._original.write(s)
        try:
            self._logfile.write(s)
        except Exception:
            # 文件突发不可写（磁盘满 / 句柄关闭）不影响终端输出
            pass
        return n

    def flush(self):
        self._original.flush()
        self._logfile.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _prune_multi_logs(log_dir: Path, keep: int) -> None:
    """MULTI 模式启动时清理旧 agenta-*.log，只保留最近 keep 份（不含将新建的当前份）。"""
    try:
        files = sorted(
            log_dir.glob("agenta-*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[keep:]:
            old.unlink()
    except OSError:
        pass


# CLI 终端输出落盘开关 —— 必须在配置 logging 之前完成 stream 包装，
# 否则 StreamHandler 在构造时已绑定到未包装的 sys.stderr，logger 输出进不了文件。
# 三种模式：NONE 不写；SINGLE 固定 ./logs/agenta.log 跨启动追加；MULTI 每次新建带时间戳文件。
_CLI_LOG_FILE: "_LogFile | None" = None
_CLI_LOG_PATH: Path | None = None
_CLI_LOG_MODE = os.getenv("CLI_LOG_MODE", "NONE").upper()
if _CLI_LOG_MODE not in ("NONE", "SINGLE", "MULTI"):
    print(f"[WARN] 未知 CLI_LOG_MODE '{_CLI_LOG_MODE}'，降级使用 NONE（可选值：NONE / SINGLE / MULTI）")
    _CLI_LOG_MODE = "NONE"
if _CLI_LOG_MODE != "NONE":
    try:
        _log_dir = Path("./logs")
        _log_dir.mkdir(parents=True, exist_ok=True)
        _max_bytes = _int_env("LOG_MAX_BYTES", 5 * 1024 * 1024)
        _backup_count = _int_env("LOG_BACKUP_COUNT", 3)
        if _CLI_LOG_MODE == "SINGLE":
            # 跨启动追加（不删旧 = F3）；体积靠按大小滚动控制（F8）
            _CLI_LOG_PATH = _log_dir / "agenta.log"
            _open_mode = "a"
        else:  # MULTI
            _CLI_LOG_PATH = _log_dir / f"agenta-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
            _open_mode = "w"
            _prune_multi_logs(_log_dir, keep=_backup_count)  # 留最近 N 份历史（F3）
        _CLI_LOG_FILE = _LogFile(_CLI_LOG_PATH, _open_mode, _max_bytes, _backup_count)
        sys.stdout = _Tee(sys.stdout, _CLI_LOG_FILE)
        sys.stderr = _Tee(sys.stderr, _CLI_LOG_FILE)
    except OSError as e:
        print(f"[WARN] 无法打开 CLI 日志文件：{e}，本次运行不写文件")
        _CLI_LOG_FILE = None
        _CLI_LOG_PATH = None

# logger 配置（必须在 _Tee 包装之后，handler 才绑定到 tee → 日志进文件）。
# 格式 / 级别 / 上下文注入统一收口到 src.services.log_setup。
from src.services import log_setup  # noqa: E402

log_setup.setup_cli_logging(os.getenv("LOG_LEVEL"))

# src.* 模块必须在 load_dotenv() 之后导入，确保 src.config 读取到 .env 的值
from src.cli.ui import BANNER, HELP_TEXT
from src.cli.tab_complete import make_completer
from src.agent.core.skill_loader import scan_skills, SkillInfo, format_scan_banner
from src.cli import handlers
from src.memory.session_store import SessionStore
import src.config as config

# 如果用户记忆功能开启，提前导入以备 main() 中直接使用
if config.USER_MEMORY_ENABLED:
    from src.memory.user_memory import UserMemoryStore

def _warm_up_rag_models() -> None:
    """启动时预加载 embedding（及可选 reranker），并提示用户勿误以为卡死。"""
    aliases = ", ".join(a for a, _, _ in config.iter_active_embeddings())
    parts = [f"embedding（{aliases or '默认'}）"]
    if config.RERANKER_ENABLED:
        parts.append(f"reranker（{config.RERANKER_MODEL}）")
    targets = "、".join(parts)
    print(
        f"⏳ 正在预加载 {targets}, 可能需数十秒至数分钟，请稍候…",
        flush=True,
    )
    from src.rag.retriever import warm_up as rag_warm_up

    rag_warm_up()
    print(f"✅ {targets} 已就绪。\n", flush=True)


def _bootstrap_mcp() -> None:
    """按 `.agenta/mcp/config.json` 启动 MCP server 子进程；空配 / 失败一律静默不阻塞。

    设计：失败 server 由 MCPManager 内部标 `failed` 并 log warning；本函数只负责
    入口编排（加载 config → start_all → 注册 atexit shutdown），任何异常吞掉。
    """
    if not config.MCP_ENABLED:
        return
    try:
        from src.agent.core.mcp_config import load_mcp_config
        from src.agent.core.mcp_manager import get_shared_manager
        specs = load_mcp_config()
        if not specs:
            return
        manager = get_shared_manager()
        manager.start_all(specs)
        import atexit
        atexit.register(manager.shutdown)
        statuses = manager.status()
        connected = sum(1 for s in statuses if s["status"] == "connected")
        print(f"🔌 MCP server 已加载（{connected}/{len(statuses)} connected）\n")
    except Exception as exc:
        logging.getLogger(__name__).warning("[main] MCP bootstrap 失败：%s", exc)


def main() -> None:
    """CLI 主循环。"""
    print(BANNER)

    if _CLI_LOG_PATH:
        _mode_hint = "追加" if _CLI_LOG_MODE == "SINGLE" else "覆盖"
        print(f"📝 终端输出同步写入（{_CLI_LOG_MODE} / {_mode_hint}）：{_CLI_LOG_PATH}\n")

    # _warm_up_rag_models()

    # 延迟导入 Agent（仍较重，但 banner / RAG 预热提示已先输出）
    print("⏳ 正在初始化 Agent…", flush=True)
    from src.agent.agent import SYSTEM_PROMPT, ThinkingConfig

    # 共享 SessionStore 实例，整个进程生命周期内复用
    session_store = SessionStore()

    # 启动时扫描 Skills 目录，并把"已加载 N 个 / 失败 N 个"显式打到 stdout，
    # 让用户即便没看 log 也能感知 skill 状态（满足 Step 0 验收 ④ "失败可见"）
    scan = scan_skills()
    skills_map: dict[str, SkillInfo] = scan.loaded
    # 供 CLI 匹配的 {/name: body} 字典（tab 补全 + 手动激活）
    skill_cmds: dict[str, str] = {f"/{name}": info.body for name, info in skills_map.items()}
    success_line, failure_block = format_scan_banner(scan)
    print(success_line)
    if failure_block:
        print(failure_block)
    print()

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

    # Phase 3.3：按 .agenta/mcp/config.json 拉起 MCP server 子进程；失败永远不阻塞 Agent
    _bootstrap_mcp()

    agent = handlers.make_agent(session_store, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
    print(f"💬 当前 Session: {agent.session_id}\n")

    prompt_session: PromptSession[str] = PromptSession(
        history=InMemoryHistory(),
        completer=make_completer(session_store, list(skill_cmds.keys())),
        complete_while_typing=False,  # 仅 Tab 触发，不干扰正常输入
    )

    while True:
        try:
            # 每轮刷新补全器，确保新建/删除的 session id 即时更新
            prompt_session.completer = make_completer(session_store, list(skill_cmds.keys()))
            user_input = prompt_session.prompt("你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("👋 再见1！")
            handlers.quit_sys(session_store, user_memory)
            
        if not user_input:
            continue

        # 把当前 session 写进日志上下文，使本轮日志带 s:<session>
        log_setup.set_session_id(agent.session_id)

        # 用户输入只走 TTY 回显，不经 Python stdout —— 若开了文件日志需手动补写一笔，
        # 文件里才能完整还原"我问了啥 → Agent 答了啥"
        if _CLI_LOG_FILE is not None:
            try:
                _CLI_LOG_FILE.write(f"你: {user_input}\n")
                _CLI_LOG_FILE.flush()
            except Exception:
                pass

        # ── 内置命令处理 ──────────────────────────────────────────────────────
        cmd_parts = user_input.split(maxsplit=1) # input 分割为命 令名和参数
        cmd_name = cmd_parts[0].lower() if cmd_parts else ""
        match cmd_name:
            case "/quit" | "/exit":
                print("👋 再见2！")
                handlers.quit_sys(session_store, user_memory)
            case "/help":
                print(HELP_TEXT)
                continue
            case "/clear":
                session_store.clear(agent.session_id)
                agent = handlers.make_agent(session_store, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
                print(f"✅ 对话历史已清空，Agent 已重置。\n💬 新 Session: {agent.session_id}\n")
                continue
            case "/history":
                handlers.show_history(session_store, agent.session_id)
                continue
            case "/sessions":
                query = cmd_parts[1].strip() if len(cmd_parts) > 1 else None
                handlers.list_sessions(
                    session_store,
                    query=query,
                    current_session_id=agent.session_id,
                )
                continue
            case "/session":
                session_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not session_arg:
                    print("⚠️  /session 需要 session id 参数。用 /sessions 查看列表，或 /sessions <关键词> 搜索。\n")
                    continue
                new_agent = handlers.switch_session(
                    session_store, session_arg, SYSTEM_PROMPT, skills_map,
                    thinking_cfg, user_memory=user_memory
                )
                if new_agent is not None:
                    agent = new_agent
                continue
            case "/del-session":
                target_id = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not target_id:
                    print("⚠️  请指定要删除的 session ID，例：/del-session <id>\n")
                elif target_id == agent.session_id:
                    print("⚠️  不能删除当前活跃 session，请先用 /session <id> 切换到其他 session 后再删除。\n")
                else:
                    deleted = session_store.delete_session(target_id)
                    if deleted:
                        print(f"🗑️  Session {target_id} 已彻底删除。\n")
                    else:
                        print(f"❌ Session {target_id} 不存在。\n")
                continue
            case "/clean-session":
                sessions = session_store.list_sessions()
                if not sessions:
                    print("📭 暂无历史 session 记录，无需清空。\n")
                else:
                    confirm = input(f"⚠️  即将清空全部 {len(sessions)} 个 session 记录（不可恢复），确认请输入 yes：").strip().lower()
                    if confirm == "yes":
                        count = session_store.clean_all_sessions()
                        # 当前 Agent 的历史也已被清除，重建一个新 session
                        agent = handlers.make_agent(session_store, skills_map, thinking_cfg, SYSTEM_PROMPT, user_memory=user_memory)
                        print(f"🗑️  已清空全部 {count} 个 session 记录。新 Session: {agent.session_id}\n")
                    else:
                        print("已取消。\n")
                continue
            case "/reload-skills":
                scan = scan_skills()
                skills_map = scan.loaded
                skill_cmds = {f"/{name}": info.body for name, info in skills_map.items()}
                # 重建 Agent，使 system_prompt 中的 catalog 立即刷新
                agent = handlers.make_agent(
                    session_store, skills_map, thinking_cfg,
                    SYSTEM_PROMPT, session_id=agent.session_id,
                    user_memory=user_memory,
                )
                success_line, failure_block = format_scan_banner(scan)
                print(f"🔄 Skills 已重新加载。{success_line}")
                if failure_block:
                    print(failure_block)
                print()
                continue
            case "/save":
                save_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                if not save_arg:
                    print("⚠️  请指定文件名，例：/save my-chat\n")
                else:
                    handlers.save_history(session_store, agent.session_id, save_arg)
                continue
            case "/thinking":
                think_tokens = (
                    cmd_parts[1].strip().lower().split() if len(cmd_parts) > 1 else []
                )
                handlers.handle_thinking_cfg(thinking_cfg, think_tokens)
                continue
            case "/memory":
                if user_memory is None:
                    print("⚠️  跨 session 记忆功能未启用（请在 .env 中设置 USER_MEMORY_ENABLED=true）\n")
                else:
                    handlers.handle_memory(user_memory, cmd_parts)
                continue
            case "/study":
                from src.memory.learning_plan_store import get_shared_store as _get_lp_store
                handlers.handle_study(_get_lp_store(), cmd_parts, session_id=agent.session_id)
                continue
            case "/quiz":
                from src.memory.quiz_store import get_shared_store as _get_quiz_store
                handlers.handle_quiz(_get_quiz_store(), cmd_parts)
                continue
            case "/srs":
                from src.memory.srs_store import get_shared_store as _get_srs_store
                handlers.handle_srs(_get_srs_store(), cmd_parts)
                continue
            case "/mcp":
                from src.agent.core.mcp_manager import get_shared_manager as _get_mcp_mgr
                handlers.handle_mcp(_get_mcp_mgr(), cmd_parts)
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
