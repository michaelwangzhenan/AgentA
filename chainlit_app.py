"""
Chainlit Web UI 入口（与现有 CLI 并行）。

运行：
    chainlit run chainlit_app.py
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

import chainlit as cl
from dotenv import load_dotenv

try:
    from chainlit.input_widget import Select, Slider, Switch, TextInput
except Exception:  # pragma: no cover - 兼容旧版 chainlit
    Select = Slider = Switch = TextInput = None  # type: ignore[assignment]

# 消除 HuggingFace tokenizer 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

# override=True 确保 .env 覆盖系统环境变量
load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(message)s",
    datefmt="%H:%M:%S",
)
for _noisy in ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from src.cli import handlers
from src.cli.prompt_loader import scan_prompts
from src.cli.skill_loader import SkillInfo, scan_skills
from src.cli.ui import HELP_TEXT
from src.memory.chat_history import ChatHistory
import src.config as config

if config.USER_MEMORY_ENABLED:
    from src.memory.user_memory import UserMemoryStore
else:  # pragma: no cover - 类型占位
    UserMemoryStore = None  # type: ignore[assignment]

from src.agent.agent import SYSTEM_PROMPT, ThinkingConfig

logger = logging.getLogger(__name__)

PROMPTS_DIR: str = config.PROMPTS_DIR
SKILLS_DIR: str = config.SKILLS_DIR


class AppState:
    """Chainlit 会话运行时状态（避免 dataclass 在部分运行时触发反射异常）。"""

    def __init__(
        self,
        *,
        chat_history: ChatHistory,
        custom_prompts: dict[str, str],
        skills_map: dict[str, SkillInfo],
        skill_cmds: dict[str, str],
        thinking_cfg: ThinkingConfig,
        user_memory: UserMemoryStore | None,
        agent: Any,
        active_prompt_name: str | None = None,
        ingest_docs_dir: str = config.DOCS_DIR,
        ingest_model_alias: str = config.DEFAULT_EMBEDDING_ALIAS,
    ) -> None:
        self.chat_history = chat_history
        self.custom_prompts = custom_prompts
        self.skills_map = skills_map
        self.skill_cmds = skill_cmds
        self.thinking_cfg = thinking_cfg
        self.user_memory = user_memory
        self.agent = agent
        self.active_prompt_name = active_prompt_name
        self.ingest_docs_dir = ingest_docs_dir
        self.ingest_model_alias = ingest_model_alias


class _OutputCollector:
    """收集 handlers 输出，统一回传到 Chainlit。"""

    def __init__(self) -> None:
        self._chunks: list[str] = []

    def write(self, msg: str) -> None:
        self._chunks.append(msg.rstrip("\n"))

    def text(self) -> str:
        return "\n".join(chunk for chunk in self._chunks if chunk is not None).strip()


def _set_state(state: AppState) -> None:
    cl.user_session.set("state", state)


def _get_state() -> AppState:
    state = cl.user_session.get("state")
    if state is None:
        raise RuntimeError("AppState 未初始化，请先开始会话。")
    return state


def _build_base_prompt(state: AppState) -> str:
    if state.active_prompt_name:
        cmd = f"/{state.active_prompt_name}"
        return state.custom_prompts.get(cmd, SYSTEM_PROMPT)
    return SYSTEM_PROMPT


def _make_agent(state: AppState, session_id: str | None = None) -> Any:
    return handlers.make_agent(
        chat_history=state.chat_history,
        skills_map=state.skills_map,
        thinking_cfg=state.thinking_cfg,
        system_prompt=_build_base_prompt(state),
        prompt_name=state.active_prompt_name or "",
        session_id=session_id,
        user_memory=state.user_memory,
        verbose=False,
    )


def _get_actions() -> list[cl.Action]:
    return []


def _parse_ingest_args(raw_args: str) -> tuple[str | None, str | None]:
    docs_dir: str | None = None
    model_alias: str | None = None
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
            i += 1
    return docs_dir, model_alias


def _runtime_settings_widgets(state: AppState) -> list[Any]:
    if Select is None or Switch is None or Slider is None or TextInput is None:
        return []
    providers = sorted(config.PROVIDER_CONFIGS.keys())
    widgets: list[Any] = [
        Select(
            id="provider",
            label="LLM Provider",
            values=providers,
            initial_value=config.ACTIVE_PROVIDER if config.ACTIVE_PROVIDER in providers else providers[0],
        ),
        Switch(id="thinking_enabled", label="Extended Thinking", initial=state.thinking_cfg.enabled),
        Switch(id="thinking_adaptive", label="Adaptive Thinking", initial=state.thinking_cfg.adaptive),
        Slider(
            id="thinking_budget",
            label="Thinking Budget",
            min=1024,
            max=32000,
            step=256,
            initial=state.thinking_cfg.budget,
        ),
        Slider(
            id="rag_top_k",
            label="RAG Top K",
            min=1,
            max=10,
            step=1,
            initial=int(config.RAG_TOP_K),
        ),
        TextInput(id="ingest_docs_dir", label="Ingest Docs Dir", initial=state.ingest_docs_dir),
        Select(
            id="ingest_model_alias",
            label="Ingest Embedding Alias",
            values=list(config.EMBEDDING_MODELS.keys()),
            initial_value=state.ingest_model_alias if state.ingest_model_alias in config.EMBEDDING_MODELS else config.DEFAULT_EMBEDDING_ALIAS,
        ),
    ]
    return widgets


async def _send_collected(title: str, collector: _OutputCollector, actions: list[cl.Action] | None = None) -> None:
    text = collector.text() or "（无输出）"
    await cl.Message(content=f"**{title}**\n\n```text\n{text}\n```", actions=actions or []).send()


async def _send_settings(state: AppState) -> None:
    widgets = _runtime_settings_widgets(state)
    if not widgets:
        await cl.Message(content="当前 Chainlit 版本不支持 ChatSettings 控件，已跳过设置面板。").send()
        return
    await cl.ChatSettings(widgets).send()



async def _stream_agent_reply(state: AppState, user_input: str) -> str:
    """
    线程桥接运行 agent.run，并将 thinking chunk 流式输出到 Chainlit。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def thinking_callback(chunk: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, chunk)

    async def consume_thinking() -> None:
        msg: cl.Message | None = None
        while True:
            token = await queue.get()
            if token is None:
                break
            if msg is None:
                msg = cl.Message(content="💭 Thinking...\n")
                await msg.send()
            await msg.stream_token(token)
        if msg is not None:
            await msg.update()

    if hasattr(state.agent, "set_thinking_callback"):
        state.agent.set_thinking_callback(thinking_callback)

    consumer_task = asyncio.create_task(consume_thinking())
    try:
        answer = await asyncio.to_thread(state.agent.run, user_input)
    finally:
        if hasattr(state.agent, "set_thinking_callback"):
            state.agent.set_thinking_callback(None)
        loop.call_soon_threadsafe(queue.put_nowait, None)
        await consumer_task
    return answer


async def _handle_command(state: AppState, user_input: str) -> bool:
    cmd_parts = user_input.split(maxsplit=1)
    cmd_name = cmd_parts[0].lower() if cmd_parts else ""
    collector = _OutputCollector()

    match cmd_name:
        case "/help":
            await cl.Message(content=f"```text\n{HELP_TEXT}\n```", actions=_get_actions()).send()
            return True
        case "/ingest":
            raw_args = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
            docs_dir, model_alias = _parse_ingest_args(raw_args)
            docs_dir = docs_dir or state.ingest_docs_dir
            model_alias = model_alias or state.ingest_model_alias
            await asyncio.to_thread(handlers.run_ingest, docs_dir, model_alias, collector.write)
            await _send_collected("入库结果", collector, actions=_get_actions())
            return True
        case "/clear":
            await asyncio.to_thread(state.chat_history.clear, state.agent.session_id)
            state.active_prompt_name = None
            state.agent = _make_agent(state)
            _set_state(state)
            await cl.Message(
                content=f"✅ 对话历史已清空，Agent 已重置。\n\n新 Session: `{state.agent.session_id}`",
                actions=_get_actions(),
            ).send()
            return True
        case "/history":
            await asyncio.to_thread(handlers.show_history, state.chat_history, state.agent.session_id, collector.write)
            await _send_collected("会话历史摘要", collector, actions=_get_actions())
            return True
        case "/session":
            session_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
            result = await asyncio.to_thread(
                handlers.switch_session,
                state.chat_history,
                session_arg,
                state.custom_prompts,
                SYSTEM_PROMPT,
                state.skills_map,
                state.thinking_cfg,
                state.user_memory,
                collector.write,
                False,
            )
            if result:
                state.agent, state.active_prompt_name = result
                _set_state(state)
            await _send_collected("Session 操作结果", collector, actions=_get_actions())
            return True
        case "/del-session":
            target_id = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
            if not target_id:
                collector.write("⚠️  请指定要删除的 session ID，例：/del-session <id>\n")
            elif target_id == state.agent.session_id:
                collector.write("⚠️  不能删除当前活跃 session，请先切换到其他 session 后再删除。\n")
            else:
                deleted = await asyncio.to_thread(state.chat_history.delete_session, target_id)
                collector.write(f"🗑️  Session {target_id} 已彻底删除。\n" if deleted else f"❌ Session {target_id} 不存在。\n")
            await _send_collected("删除 Session", collector, actions=_get_actions())
            return True
        case "/clean-session":
            # Web UI 中避免交互式 input，要求显式确认参数 yes
            confirm = cmd_parts[1].strip().lower() if len(cmd_parts) > 1 else ""
            if confirm != "yes":
                await cl.Message(content="⚠️  该操作会清空全部会话（不可恢复）。请使用 `/clean-session yes` 确认执行。").send()
                return True
            count = await asyncio.to_thread(state.chat_history.clean_all_sessions)
            state.active_prompt_name = None
            state.agent = _make_agent(state)
            _set_state(state)
            await cl.Message(
                content=f"🗑️  已清空全部 {count} 个 session。新 Session: `{state.agent.session_id}`",
                actions=_get_actions(),
            ).send()
            return True
        case "/reload-prompts":
            state.custom_prompts = await asyncio.to_thread(scan_prompts, PROMPTS_DIR)
            _set_state(state)
            cmds_str = ", ".join(state.custom_prompts) if state.custom_prompts else "（无）"
            await cl.Message(content=f"🔄 Prompt 已重新加载，共 {len(state.custom_prompts)} 个：{cmds_str}", actions=_get_actions()).send()
            return True
        case "/reload-skills":
            state.skills_map = await asyncio.to_thread(scan_skills, SKILLS_DIR)
            state.skill_cmds = {f"/{name}": info.body for name, info in state.skills_map.items()}
            state.agent = _make_agent(state, session_id=state.agent.session_id)
            _set_state(state)
            cmds_str = ", ".join(state.skill_cmds) if state.skill_cmds else "（无）"
            await cl.Message(content=f"🔄 Skills 已重新加载，共 {len(state.skill_cmds)} 个：{cmds_str}", actions=_get_actions()).send()
            return True
        case "/save":
            save_arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
            if not save_arg:
                await cl.Message(content="⚠️  请指定文件名，例：`/save my-chat`").send()
                return True
            await asyncio.to_thread(handlers.save_history, state.chat_history, state.agent.session_id, save_arg, collector.write)
            await _send_collected("导出会话", collector, actions=_get_actions())
            return True
        case "/thinking":
            think_tokens = cmd_parts[1].strip().lower().split() if len(cmd_parts) > 1 else []
            await asyncio.to_thread(handlers.handle_thinking_cfg, state.thinking_cfg, think_tokens, collector.write)
            _set_state(state)
            await _send_collected("Thinking 配置", collector, actions=_get_actions())
            return True
        case "/memory":
            if state.user_memory is None:
                await cl.Message(content="⚠️  跨 session 记忆未启用（请在 .env 设置 USER_MEMORY_ENABLED=true）").send()
                return True
            await asyncio.to_thread(handlers.handle_memory, state.user_memory, cmd_parts, collector.write)
            await _send_collected("用户记忆", collector, actions=_get_actions())
            return True

    # Prompt 切换命令
    if cmd_name in state.custom_prompts:
        question = user_input[len(cmd_name):].strip()
        state.active_prompt_name = cmd_name[1:]
        state.agent = _make_agent(state)
        _set_state(state)
        await cl.Message(
            content=f"🎭 已切换到 Prompt：`{state.active_prompt_name}`（新 Session: `{state.agent.session_id}`）",
            actions=_get_actions(),
        ).send()
        if question:
            answer = await _stream_agent_reply(state, question)
            await cl.Message(content=answer).send()
        return True

    # Skill 手动激活命令
    if cmd_name in state.skill_cmds:
        question = user_input[len(cmd_name):].strip()
        skill_name = cmd_name[1:]
        activated = state.agent.activate_skill(skill_name, state.skill_cmds[cmd_name])
        text = f"🔧 Skill [{skill_name}] 已激活（注入当前会话）" if activated else f"🔧 Skill [{skill_name}] 已处于激活状态"
        await cl.Message(content=text, actions=_get_actions()).send()
        if question:
            answer = await _stream_agent_reply(state, question)
            await cl.Message(content=answer).send()
        return True

    return False


@cl.on_chat_start
async def on_chat_start() -> None:
    chat_history = ChatHistory()
    custom_prompts = scan_prompts(PROMPTS_DIR)
    skills_map = scan_skills(SKILLS_DIR)
    skill_cmds = {f"/{name}": info.body for name, info in skills_map.items()}
    thinking_cfg = ThinkingConfig.from_config()
    user_memory = UserMemoryStore(config.USER_MEMORY_DB_PATH) if config.USER_MEMORY_ENABLED else None

    state = AppState(
        chat_history=chat_history,
        custom_prompts=custom_prompts,
        skills_map=skills_map,
        skill_cmds=skill_cmds,
        thinking_cfg=thinking_cfg,
        user_memory=user_memory,
        agent=handlers.make_agent(
            chat_history=chat_history,
            skills_map=skills_map,
            thinking_cfg=thinking_cfg,
            system_prompt=SYSTEM_PROMPT,
            user_memory=user_memory,
            verbose=False,
        ),
    )
    _set_state(state)

    prompt_hint = f"已加载 Prompts: {', '.join(custom_prompts)}\n" if custom_prompts else ""
    skill_hint = f"已加载 Skills: {', '.join(skill_cmds)}\n" if skill_cmds else ""
    await cl.Message(
        content=(
            "AgentA Chainlit UI 已启动。\n\n"
            f"当前 Session: `{state.agent.session_id}`\n"
            f"{prompt_hint}{skill_hint}"
            "输入 `/help` 查看命令列表。"
        ),
        actions=_get_actions(),
    ).send()
    await _send_settings(state)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    state = _get_state()
    user_input = (message.content or "").strip()
    if not user_input:
        return

    if user_input.startswith("/"):
        handled = await _handle_command(state, user_input)
        if not handled:
            await cl.Message(content=f"未知命令：`{user_input}`，输入 `/help` 查看可用命令。").send()
        return

    before_tools = [m for m in state.chat_history.load(state.agent.session_id) if m.get("role") == "tool"]
    answer = await _stream_agent_reply(state, user_input)
    await cl.Message(content=answer, actions=_get_actions()).send()

    after_tools = [m for m in state.chat_history.load(state.agent.session_id) if m.get("role") == "tool"]
    new_tools = after_tools[len(before_tools):]
    if new_tools:
        previews = []
        for idx, tool_msg in enumerate(new_tools, start=1):
            content = (tool_msg.get("content") or "").replace("\n", " ")
            previews.append(f"{idx}. {content[:120]}{'...' if len(content) > 120 else ''}")
        await cl.Message(content="🛠️ 本轮工具输出：\n" + "\n".join(previews)).send()

    usage = getattr(state.agent, "last_usage", None)
    if usage:
        await cl.Message(
            content=f"📊 Token：输入 {usage.prompt_tokens} + 输出 {usage.completion_tokens} = 合计 {usage.total_tokens}"
        ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict[str, Any]) -> None:
    state = _get_state()
    provider = settings.get("provider")
    if isinstance(provider, str) and provider in config.PROVIDER_CONFIGS and provider != config.ACTIVE_PROVIDER:
        config.ACTIVE_PROVIDER = provider
        state.agent = _make_agent(state, session_id=state.agent.session_id)

    state.thinking_cfg.enabled = bool(settings.get("thinking_enabled", state.thinking_cfg.enabled))
    state.thinking_cfg.adaptive = bool(settings.get("thinking_adaptive", state.thinking_cfg.adaptive))
    try:
        state.thinking_cfg.budget = int(settings.get("thinking_budget", state.thinking_cfg.budget))
    except (TypeError, ValueError):
        pass
    try:
        config.RAG_TOP_K = int(settings.get("rag_top_k", config.RAG_TOP_K))
    except (TypeError, ValueError):
        pass

    docs_dir = settings.get("ingest_docs_dir")
    if isinstance(docs_dir, str) and docs_dir.strip():
        state.ingest_docs_dir = docs_dir.strip()
    model_alias = settings.get("ingest_model_alias")
    if isinstance(model_alias, str) and model_alias in config.EMBEDDING_MODELS:
        state.ingest_model_alias = model_alias

    _set_state(state)
    await cl.Message(
        content=(
            "⚙️ 设置已更新："
            f"\n- provider: `{config.ACTIVE_PROVIDER}`"
            f"\n- thinking: `{state.thinking_cfg.enabled}`"
            f"\n- adaptive: `{state.thinking_cfg.adaptive}`"
            f"\n- budget: `{state.thinking_cfg.budget}`"
            f"\n- rag_top_k: `{config.RAG_TOP_K}`"
        ),
    ).send()


@cl.action_callback("clear_chat")
async def action_clear(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/clear")


@cl.action_callback("show_history")
async def action_history(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/history")


@cl.action_callback("clean_sessions_prompt")
async def action_clean_sessions_prompt(_: cl.Action) -> None:
    await cl.Message(
        content="⚠️ 确认要清空全部会话吗？该操作不可恢复。",
        actions=[
            cl.Action(name="clean_sessions_confirm", label="确认清空", payload={}),
            cl.Action(name="clean_sessions_cancel", label="取消", payload={}),
        ],
    ).send()


@cl.action_callback("clean_sessions_confirm")
async def action_clean_sessions_confirm(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/clean-session yes")


@cl.action_callback("clean_sessions_cancel")
async def action_clean_sessions_cancel(_: cl.Action) -> None:
    await cl.Message(content="已取消清空全部会话。", actions=_get_actions()).send()


@cl.action_callback("list_sessions")
async def action_sessions(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/session")


@cl.action_callback("reload_prompts")
async def action_reload_prompts(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/reload-prompts")


@cl.action_callback("reload_skills")
async def action_reload_skills(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/reload-skills")


@cl.action_callback("show_memory")
async def action_memory(_: cl.Action) -> None:
    state = _get_state()
    await _handle_command(state, "/memory")


@cl.on_chat_end
async def on_chat_end() -> None:
    state = cl.user_session.get("state")
    if not state:
        return
    try:
        state.chat_history.close()
        if state.user_memory is not None:
            state.user_memory.close()
    except Exception as exc:  # pragma: no cover - 资源回收保护
        logger.warning("会话结束资源释放失败: %s", exc)
