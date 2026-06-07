"""
MCPManager —— MCP client 生命周期管理

把 [`MCP Python SDK`](https://github.com/modelcontextprotocol/python-sdk)
（async-first）封装成**同步 API**，给 Python Agent 主流程使用。

设计要点：
- **模块级单例**：`get_shared_manager()` 跨 helper 共享同一 manager；agent 启动一次性
  `start_all()`，进程退出 `shutdown()`，避免重复启子进程
- **后台 thread + 长驻 event loop**：MCP SDK 用 `async with`，session 必须持有在
  同一个 event loop 内。主流程同步用 `asyncio.run_coroutine_threadsafe(...).result(timeout)`
  桥接，单线程跑所有 server 协程（loop 锁定在 worker thread）
- **启动失败不阻塞**：单个 server 启动 / handshake 失败 → 标 `status='failed'` + log
  warning + 不阻塞其它 server / Agent 主流程
- **namespace 强制前缀**：tool 对外暴露为 `<server>.<tool>`；call_tool 按 `.` 第一个
  分隔符拆，server 名禁含 `.`（由 mcp_config 守门）

API 表面（同步）：
- `start_all(specs)`：按 ServerSpec 列表启动 server，最长等 `MCP_CONNECT_TIMEOUT_SEC`
- `start_one(spec)`：启动单个 server（已在 _handles 里则跳过）；用于 UI 新建 / toggle on
- `stop_one(name)`：停止单个 server（设 close event；不删 _handles）；用于 UI 删除 / toggle off
- `reload(specs, disabled_names)`：按差异 diff 启停 server，已存在但 spec 改了的先 stop 再 start
- `list_tools()`：返回所有 connected server 的 tool 合流（带 namespace 前缀）
- `call_tool(name, args, timeout)`：name 须含 `<server>.` 前缀；返回字符串（CallToolResult
  内 TextContent 拼接），失败抛 `MCPCallError`
- `status()`：列每个 server 的状态字典（CLI `/mcp list` 用）
- `shutdown()`：触发所有 server 优雅关闭 + 停 loop + join 后台 thread
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import src.config as _cfg
from src.agent.core.mcp_config import ServerSpec

logger = logging.getLogger(__name__)


ServerStatus = Literal["connecting", "connected", "failed", "closed"]


@dataclass
class _ServerHandle:
    """单个 MCP server 的运行时句柄；只在 manager 内部使用。"""
    name: str
    spec: ServerSpec
    status: ServerStatus = "connecting"
    tools: list[Any] = field(default_factory=list)   # mcp.types.Tool 实例
    session: ClientSession | None = None
    error: str | None = None
    # 关闭信号；serve_one 在 wait 后清理 session
    _close_event: asyncio.Event | None = None
    _task: asyncio.Task | None = None


class MCPCallError(RuntimeError):
    """call_tool 转发到 MCP server 时的错误（含未连接 / 超时 / SDK 抛错）。"""


class MCPManager:
    """MCP client 集合的同步外壳；内部用后台 thread + asyncio event loop 跑 async SDK。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._handles: dict[str, _ServerHandle] = {}
        self._started: bool = False
        self._shutdown_flag: bool = False
        self._lock = threading.Lock()

    # ── 公共同步 API ──────────────────────────────────────────────────────────

    def start_all(self, specs: list[ServerSpec]) -> None:
        """按规格列表启动所有 server；阻塞直到全部就绪或超时（每 server 独立判定）。

        重复调用是 idempotent：第二次调用直接返回。
        """
        with self._lock:
            if self._started:
                return
            self._started = True

        if not specs:
            logger.info("[MCPManager] 无 server 规格，跳过启动")
            return

        self._ensure_loop_running()
        assert self._loop is not None

        try:
            fut = asyncio.run_coroutine_threadsafe(self._start_all_coro(specs), self._loop)
            # 给所有 server 串联超时一个宽松上限：N * connect_timeout + 5s buffer
            grace = _cfg.MCP_CONNECT_TIMEOUT_SEC * max(1, len(specs)) + 5
            fut.result(timeout=grace)
        except FutureTimeoutError:
            logger.warning("[MCPManager] start_all 总体超时，未就绪的 server 后续按 failed 处理")
        except Exception as exc:
            logger.warning("[MCPManager] start_all 异常：%s", exc)

        connected = sum(1 for h in self._handles.values() if h.status == "connected")
        failed = sum(1 for h in self._handles.values() if h.status == "failed")
        logger.info(
            "[MCPManager] 启动完成：%d connected / %d failed / %d total",
            connected, failed, len(self._handles),
        )

    def start_one(self, spec: ServerSpec) -> None:
        """启动单个 server，最长等 `MCP_CONNECT_TIMEOUT_SEC + 1s`。

        若同名 handle 已存在且非 closed/failed → 跳过（idempotent）；否则用新 spec
        覆盖旧 handle。本方法用于 UI 新建 / toggle on / 编辑后实时拉起。

        多次 start_all 已设过 `_started` 也无碍：本路径不走 start_all 的批量分支。
        """
        self._ensure_loop_running()
        assert self._loop is not None

        existing = self._handles.get(spec.name)
        if existing is not None and existing.status in ("connecting", "connected"):
            logger.debug("[MCPManager] server %r 已在运行，跳过 start_one", spec.name)
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(self._start_one(spec), self._loop)
            fut.result(timeout=_cfg.MCP_CONNECT_TIMEOUT_SEC + 5)
        except FutureTimeoutError:
            logger.warning("[MCPManager] start_one %r 总体超时", spec.name)
        except Exception as exc:
            logger.warning("[MCPManager] start_one %r 异常：%s", spec.name, exc)

    def stop_one(self, name: str) -> None:
        """停止单个 server：触发其 close event 让 _serve 协程退出，回收 handle。

        非阻塞等待固定 5s；超时仅 log warning（task 已 cancel，loop 关闭时会兜底回收）。
        多次调用安全；server 不存在直接 no-op。
        """
        if self._loop is None:
            return
        handle = self._handles.get(name)
        if handle is None:
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._stop_one_coro(handle), self._loop
            )
            fut.result(timeout=5)
        except FutureTimeoutError:
            logger.warning("[MCPManager] stop_one %r 超时，强行取消 task", name)
        except Exception as exc:
            logger.warning("[MCPManager] stop_one %r 异常：%s", name, exc)
        finally:
            # 用户期望"删了就没了"，从 _handles 移除避免列表残留 closed 项
            self._handles.pop(name, None)

    def reload(
        self,
        specs: list[ServerSpec],
        disabled_names: set[str] | None = None,
    ) -> None:
        """按 diff 把当前运行集合切换到目标集合（启用且 spec 一致的 idempotent）。

        Args:
            specs: 目标 spec 列表（已 env 展开；通常来自 `mcp_config.list_specs()`）
            disabled_names: 禁用 name 集合；这些 server 不会被启动（即便在 specs 里）

        差异处理：
        - 应启用但当前未跑 → start_one
        - 应启用且当前在跑但 spec 改了 → stop_one + start_one
        - 不该启用但当前在跑 → stop_one
        - 一致 → 不动
        """
        disabled = disabled_names or set()
        target_map: dict[str, ServerSpec] = {
            s.name: s for s in specs if s.name not in disabled
        }
        current_names = set(self._handles.keys())

        # 1. 不该启用 / 已删 → stop
        for name in sorted(current_names - set(target_map.keys())):
            self.stop_one(name)

        # 2. 应启用 → start 或重启（spec 变了的 stop+start）
        for name in sorted(target_map.keys()):
            new_spec = target_map[name]
            existing = self._handles.get(name)
            if existing is None or existing.status in ("failed", "closed"):
                self.start_one(new_spec)
            elif existing.spec != new_spec:
                logger.info("[MCPManager] server %r spec 变化，重启", name)
                self.stop_one(name)
                self.start_one(new_spec)
            # else: 一致且在跑 → 跳过

    def list_tools(self) -> list[dict[str, Any]]:
        """合流所有 connected server 的 tool 清单，每条带 `<server>.` 前缀。

        Returns:
            list of dict，每条含 `name`（namespaced） / `description` / `inputSchema` /
            `server`（来源 server 名）。失败 server 不参与合流。
        """
        merged: list[dict[str, Any]] = []
        for handle in self._handles.values():
            if handle.status != "connected":
                continue
            for tool in handle.tools:
                merged.append({
                    "name": f"{handle.name}.{tool.name}",
                    "description": getattr(tool, "description", "") or "",
                    "inputSchema": getattr(tool, "inputSchema", None) or {"type": "object"},
                    "server": handle.name,
                })
        return merged

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> str:
        """转发到对应 server 的 `tools/call`，返回 TextContent 拼接的字符串。

        Args:
            name: 必须含 `.`（如 `filesystem.read_file`）；按第一个 `.` 拆 server / tool。
            arguments: tool 参数 dict（可为 None）。
            timeout: 调用超时（秒）；None 取 `config.MCP_CALL_TIMEOUT_SEC`。

        Raises:
            MCPCallError: name 格式错 / server 未连接 / SDK 抛错 / 超时。
        """
        if "." not in name:
            raise MCPCallError(f"call_tool: name {name!r} 缺 '<server>.' namespace 前缀")
        server_name, tool_name = name.split(".", 1)

        handle = self._handles.get(server_name)
        if handle is None:
            raise MCPCallError(f"call_tool: 未知 server {server_name!r}")
        if handle.status != "connected" or handle.session is None:
            raise MCPCallError(
                f"call_tool: server {server_name!r} 状态 {handle.status!r}，无法调用"
            )

        if self._loop is None:
            raise MCPCallError("call_tool: event loop 未启动")

        wait = timeout if timeout is not None else float(_cfg.MCP_CALL_TIMEOUT_SEC)

        try:
            fut = asyncio.run_coroutine_threadsafe(
                handle.session.call_tool(tool_name, arguments=arguments or {}),
                self._loop,
            )
            result = fut.result(timeout=wait)
        except FutureTimeoutError as exc:
            raise MCPCallError(
                f"call_tool: {name!r} 调用超时（{wait}s）"
            ) from exc
        except Exception as exc:
            raise MCPCallError(f"call_tool: {name!r} SDK 抛错：{exc}") from exc

        return self._stringify_result(result)

    def status(self) -> list[dict[str, Any]]:
        """列每个 server 的状态摘要（CLI `/mcp list` 用）。"""
        return [
            {
                "name": h.name,
                "status": h.status,
                "tool_count": len(h.tools),
                "error": h.error,
                "command": f"{h.spec.command} {' '.join(h.spec.args)}".strip(),
            }
            for h in self._handles.values()
        ]

    def shutdown(self) -> None:
        """触发所有 server 优雅关闭，停 event loop，join 后台 thread。

        多次调用安全；shutdown 后再调 call_tool 会抛 MCPCallError。
        """
        with self._lock:
            if self._shutdown_flag:
                return
            self._shutdown_flag = True

        if self._loop is None or self._loop_thread is None:
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_coro(), self._loop)
            fut.result(timeout=10)
        except Exception as exc:
            logger.warning("[MCPManager] shutdown 协程异常：%s", exc)

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        self._loop = None
        self._loop_thread = None
        logger.info("[MCPManager] 已关闭")

    # ── 内部：event loop 后台线程 ────────────────────────────────────────────

    def _ensure_loop_running(self) -> None:
        if self._loop is not None and self._loop_thread is not None:
            return

        ready = threading.Event()

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                # 取消未完成的 task，关闭异步生成器，然后真关 loop
                try:
                    pending = asyncio.all_tasks(loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        thread = threading.Thread(target=_run_loop, name="mcp-event-loop", daemon=True)
        thread.start()
        ready.wait(timeout=5)
        self._loop_thread = thread

    # ── 内部：协程 ────────────────────────────────────────────────────────────

    async def _start_all_coro(self, specs: list[ServerSpec]) -> None:
        """在 event loop 内并发启动所有 server。"""
        await asyncio.gather(
            *(self._start_one(spec) for spec in specs),
            return_exceptions=True,
        )

    async def _start_one(self, spec: ServerSpec) -> None:
        """启动单个 server：建 client → ClientSession → initialize → list_tools。

        成功后让 task 阻塞在 close_event 上，保持 session 持有；shutdown 时退出 context。
        """
        handle = _ServerHandle(name=spec.name, spec=spec)
        handle._close_event = asyncio.Event()
        self._handles[spec.name] = handle

        ready = asyncio.Event()

        async def _serve() -> None:
            try:
                params = StdioServerParameters(
                    command=spec.command,
                    args=list(spec.args),
                    env=self._build_env(spec),
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(
                            session.initialize(),
                            timeout=_cfg.MCP_CONNECT_TIMEOUT_SEC,
                        )
                        tools_resp = await session.list_tools()
                        handle.session = session
                        handle.tools = list(tools_resp.tools)
                        handle.status = "connected"
                        ready.set()
                        logger.info(
                            "[MCPManager] server %r 已连接（%d tools）",
                            spec.name, len(handle.tools),
                        )
                        assert handle._close_event is not None
                        await handle._close_event.wait()
            except asyncio.CancelledError:
                handle.status = "closed"
                raise
            except Exception as exc:
                handle.status = "failed"
                handle.error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[MCPManager] server %r 启动失败：%s（其它 server 不受影响）",
                    spec.name, handle.error,
                )
                ready.set()
            finally:
                handle.session = None
                handle.status = "closed" if handle.status == "connected" else handle.status

        handle._task = asyncio.create_task(_serve(), name=f"mcp-{spec.name}")

        # 等就绪 / 失败信号；多给 1s buffer 兜底 wait_for 自身误差
        try:
            await asyncio.wait_for(
                ready.wait(),
                timeout=_cfg.MCP_CONNECT_TIMEOUT_SEC + 1,
            )
        except asyncio.TimeoutError:
            handle.status = "failed"
            handle.error = "connect timeout（未等到 initialize 完成）"
            logger.warning("[MCPManager] server %r 等就绪超时", spec.name)

    async def _stop_one_coro(self, handle: _ServerHandle) -> None:
        """触发单个 handle 关闭事件，等 server task 收尾。"""
        if handle._close_event is not None:
            handle._close_event.set()
        task = handle._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                logger.warning("[MCPManager] stop_one_coro 等 task 超时，强行取消")
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _shutdown_coro(self) -> None:
        """触发所有 handle 关闭事件，等 server task 收尾。"""
        tasks = []
        for h in self._handles.values():
            if h._close_event is not None:
                h._close_event.set()
            if h._task is not None and not h._task.done():
                tasks.append(h._task)
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                logger.warning("[MCPManager] shutdown 等 server task 超时，强行取消")
                for t in tasks:
                    t.cancel()

    # ── 内部：纯函数 helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_env(spec: ServerSpec) -> dict[str, str] | None:
        """合并当前进程 env 和 spec.env；spec.env 覆盖同名键。

        SDK 文档建议传当前 PATH 等基础变量，避免子进程找不到 npx / python。
        """
        if not spec.env:
            return None
        merged = dict(os.environ)
        merged.update(spec.env)
        return merged

    @staticmethod
    def _stringify_result(result: Any) -> str:
        """把 `CallToolResult` 的 content 列表拼成 LLM 友好的纯文本。

        - text content：直接拼 .text
        - 非 text content：占位 `[non-text content: <type>]`
        - isError=True：前缀 `[tool reported error] ` 让 LLM 看到错误标记
        """
        parts: list[str] = []
        for item in getattr(result, "content", None) or []:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                parts.append(getattr(item, "text", "") or "")
            else:
                parts.append(f"[non-text content: {item_type}]")
        body = "\n\n".join(p for p in parts if p)
        if getattr(result, "isError", False):
            return f"[tool reported error] {body}".rstrip()
        return body


# ── 模块级单例 ──────────────────────────────────────────────────────────────

_shared_manager: MCPManager | None = None
_shared_lock = threading.Lock()


def get_shared_manager() -> MCPManager:
    """返回进程级共享 `MCPManager` 实例（首次调用 lazy 创建）。"""
    global _shared_manager
    if _shared_manager is None:
        with _shared_lock:
            if _shared_manager is None:
                _shared_manager = MCPManager()
    return _shared_manager


def reset_shared_manager_for_tests() -> None:
    """**仅供 UT 调用**：清掉单例，让下个 case 拿到干净 manager。"""
    global _shared_manager
    with _shared_lock:
        if _shared_manager is not None:
            try:
                _shared_manager.shutdown()
            except Exception:
                pass
        _shared_manager = None
