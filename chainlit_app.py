"""
Chainlit Web UI 入口（与现有 CLI 并行）。

运行：
    chainlit run chainlit_app.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from typing import Any

import chainlit as cl
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from dotenv import load_dotenv

try:
    from chainlit.input_widget import Select, Slider, Switch
except Exception:  # pragma: no cover - 兼容旧版 chainlit
    Select = Slider = Switch = None  # type: ignore[assignment]

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
from src.cli.skill_loader import SkillInfo, format_scan_banner, scan_skills
from src.cli.ui import HELP_TEXT
from src.memory.chat_history import ChatHistoryStore
import src.config as config

if config.USER_MEMORY_ENABLED:
    from src.memory.user_memory import UserMemoryStore
else:  # pragma: no cover - 类型占位
    UserMemoryStore = None  # type: ignore[assignment]

from src.agent.agent import SYSTEM_PROMPT, ThinkingConfig

logger = logging.getLogger(__name__)


# ── Intercept /api/agenta/* before Chainlit's SPA catch-all ───────────────
# Middleware runs before route dispatch, so it bypasses the SPA wildcard route.

class _AgentAApiMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/api/agenta/sessions":
            ch = ChatHistoryStore()
            try:
                rows = await asyncio.to_thread(ch.list_sessions)
                return Response(
                    content=json.dumps(rows, ensure_ascii=False),
                    media_type="application/json",
                )
            except Exception as exc:
                return Response(
                    content=json.dumps({"error": str(exc)}),
                    status_code=500,
                    media_type="application/json",
                )
            finally:
                await asyncio.to_thread(ch.close)
        return await call_next(request)


from chainlit.server import app as _cl_server  # noqa: E402
_cl_server.add_middleware(_AgentAApiMiddleware)


class AppState:
    """Chainlit 会话运行时状态（避免 dataclass 在部分运行时触发反射异常）。"""

    def __init__(
        self,
        *,
        chat_history: ChatHistoryStore,
        skills_map: dict[str, SkillInfo],
        skill_cmds: dict[str, str],
        thinking_cfg: ThinkingConfig,
        user_memory: UserMemoryStore | None,
        agent: Any,
    ) -> None:
        self.chat_history = chat_history
        self.skills_map = skills_map
        self.skill_cmds = skill_cmds
        self.thinking_cfg = thinking_cfg
        self.user_memory = user_memory
        self.agent = agent


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


def _make_agent(state: AppState, session_id: str | None = None) -> Any:
    return handlers.make_agent(
        chat_history=state.chat_history,
        skills_map=state.skills_map,
        thinking_cfg=state.thinking_cfg,
        system_prompt=SYSTEM_PROMPT,
        session_id=session_id,
        user_memory=state.user_memory,
        verbose=False,
    )


def _get_actions() -> list[cl.Action]:
    return []


def _runtime_settings_widgets(state: AppState) -> list[Any]:
    if Select is None or Switch is None or Slider is None:
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



async def _stream_agent_reply(state: AppState, user_input: str) -> tuple[str, cl.Message]:
    """
    线程桥接运行 agent.run，将 thinking chunk 和正文 token 均流式推送到 Chainlit。

    Returns:
        (answer, answer_msg): 最终回答字符串 和 已发送的流式回答消息对象。
        调用方可在 answer_msg 上附加 actions 后 update()。
    """
    loop = asyncio.get_running_loop()
    thinking_queue: asyncio.Queue[str | None] = asyncio.Queue()
    token_queue: asyncio.Queue[str | None] = asyncio.Queue()
    # Phase 2.1: plan 事件桥接到 Chainlit 主消息流（plan_created/plan_step_end）；
    # plan_step_start 仅用于 GUI step UI 高亮，本期不渲染（[iter_2.md §4.13.1 #11](../docs/iter_2.md#4131-deferred-backlog)）。
    plan_queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    def thinking_callback(chunk: str) -> None:
        loop.call_soon_threadsafe(thinking_queue.put_nowait, chunk)

    def token_callback(chunk: str) -> None:
        loop.call_soon_threadsafe(token_queue.put_nowait, chunk)

    def plan_callback(event_type: str, payload: dict) -> None:
        loop.call_soon_threadsafe(plan_queue.put_nowait, (event_type, payload))

    async def consume_thinking() -> None:
        msg: cl.Message | None = None
        while True:
            token = await thinking_queue.get()
            if token is None:
                break
            if msg is None:
                msg = cl.Message(content="💭 Thinking...\n")
                await msg.send()
            await msg.stream_token(token)
        if msg is not None:
            await msg.update()

    async def consume_plan() -> None:
        """plan_created → 推送 📋 整块 checkbox；plan_step_end → 推送单步完成消息。"""
        while True:
            item = await plan_queue.get()
            if item is None:
                break
            event_type, payload = item
            if event_type == "plan_created":
                steps = payload.get("steps") or []
                if not steps:
                    continue
                lines = ["📋 **Plan**", ""]
                for s in steps:
                    lines.append(f"- [ ] {s.get('id')}. {s.get('text', '')}")
                await cl.Message(content="\n".join(lines)).send()
            elif event_type == "plan_step_end":
                icon = {"success": "✅", "failed": "❌", "skipped": "⏭️"}.get(
                    str(payload.get("status", "")), "•",
                )
                note = (payload.get("note") or "").strip()
                suffix = f" — {note}" if note else ""
                await cl.Message(
                    content=f"{icon} **Step {payload.get('step_id')}**{suffix}"
                ).send()

    # 预建答案消息，流式写入 token
    answer_msg = cl.Message(content="")
    await answer_msg.send()
    token_received = False

    async def consume_tokens() -> None:
        nonlocal token_received
        while True:
            token = await token_queue.get()
            if token is None:
                break
            token_received = True
            await answer_msg.stream_token(token)

    # 走统一 set_event_callback 入口：单回调按 event.type 分流到对应 queue。
    # thinking_callback/token_callback 仍是 Callable[[str], None] 签名，沿用旧的 chunk 文本即可。
    def _event_router(event):
        if event.type == "thinking_chunk":
            thinking_callback(event.payload.get("text", ""))
        elif event.type == "token_chunk":
            token_callback(event.payload.get("text", ""))
        elif event.type in ("plan_created", "plan_step_end"):
            plan_callback(event.type, event.payload)

    if hasattr(state.agent, "set_event_callback"):
        state.agent.set_event_callback(_event_router)

    thinking_task = asyncio.create_task(consume_thinking())
    token_task = asyncio.create_task(consume_tokens())
    plan_task = asyncio.create_task(consume_plan())
    try:
        answer = await asyncio.to_thread(state.agent.run, user_input)
    finally:
        if hasattr(state.agent, "set_event_callback"):
            state.agent.set_event_callback(None)
        loop.call_soon_threadsafe(thinking_queue.put_nowait, None)
        loop.call_soon_threadsafe(token_queue.put_nowait, None)
        loop.call_soon_threadsafe(plan_queue.put_nowait, None)
        await thinking_task
        await token_task
        await plan_task

    # 若 provider 不支持流式（token_received=False），直接填充完整答案
    if not token_received:
        answer_msg.content = answer
    return answer, answer_msg


async def _handle_command(state: AppState, user_input: str) -> bool:
    cmd_parts = user_input.split(maxsplit=1)
    cmd_name = cmd_parts[0].lower() if cmd_parts else ""
    collector = _OutputCollector()

    match cmd_name:
        case "/help":
            await cl.Message(content=f"```text\n{HELP_TEXT}\n```", actions=_get_actions()).send()
            return True
        case "/clear":
            await asyncio.to_thread(state.chat_history.clear, state.agent.session_id)
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
            new_agent = await asyncio.to_thread(
                handlers.switch_session,
                state.chat_history,
                session_arg,
                SYSTEM_PROMPT,
                state.skills_map,
                state.thinking_cfg,
                state.user_memory,
                collector.write,
                False,
            )
            if new_agent is not None:
                state.agent = new_agent
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
            state.agent = _make_agent(state)
            _set_state(state)
            await cl.Message(
                content=f"🗑️  已清空全部 {count} 个 session。新 Session: `{state.agent.session_id}`",
                actions=_get_actions(),
            ).send()
            return True
        case "/reload-skills":
            scan = await asyncio.to_thread(scan_skills)
            state.skills_map = scan.loaded
            state.skill_cmds = {f"/{name}": info.body for name, info in state.skills_map.items()}
            state.agent = _make_agent(state, session_id=state.agent.session_id)
            _set_state(state)
            success_line, failure_block = format_scan_banner(scan)
            parts = [f"🔄 Skills 已重新加载。{success_line}"]
            if failure_block:
                parts.append(f"```\n{failure_block}\n```")
            await cl.Message(content="\n\n".join(parts), actions=_get_actions()).send()
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

    # Skill 手动激活命令
    if cmd_name in state.skill_cmds:
        question = user_input[len(cmd_name):].strip()
        skill_name = cmd_name[1:]
        activated = state.agent.activate_skill(skill_name, state.skill_cmds[cmd_name])
        text = f"🔧 Skill [{skill_name}] 已激活（注入当前会话）" if activated else f"🔧 Skill [{skill_name}] 已处于激活状态"
        await cl.Message(content=text, actions=_get_actions()).send()
        if question:
            answer, answer_msg = await _stream_agent_reply(state, question)
            await answer_msg.update()
        return True

    return False


@cl.on_chat_start
async def on_chat_start() -> None:
    chat_history = ChatHistoryStore()
    scan = scan_skills()
    skills_map = scan.loaded
    skill_cmds = {f"/{name}": info.body for name, info in skills_map.items()}
    thinking_cfg = ThinkingConfig.from_config()
    user_memory = UserMemoryStore(config.USER_MEMORY_DB_PATH) if config.USER_MEMORY_ENABLED else None

    state = AppState(
        chat_history=chat_history,
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

    # Step 0 验收 ④"失败可见"：启动消息显式列出已加载 + 失败明细（与 CLI 同文案）
    success_line, failure_block = format_scan_banner(scan)
    parts = [
        "AgentA Chainlit UI 已启动。",
        f"当前 Session: `{state.agent.session_id}`",
        success_line,
    ]
    if failure_block:
        parts.append(f"```\n{failure_block}\n```")
    parts.append("输入 `/help` 查看命令列表。")
    await cl.Message(content="\n\n".join(parts), actions=_get_actions()).send()
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
    answer, answer_msg = await _stream_agent_reply(state, user_input)
    answer_msg.actions = _get_actions()
    await answer_msg.update()

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
