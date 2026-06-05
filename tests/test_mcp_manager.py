"""
test_mcp_manager —— Phase 3.3 MCPManager 行为单测（D15 SDK 层 mock）

测试策略（D15）：
- 不真启子进程；patch `mcp_manager.stdio_client` + `mcp_manager.ClientSession`
- 通过 `FakeSession`（既扮演 `async with stdio_client(...)` 出口 (read, write)，
  也扮演 `async with ClientSession(read, write)` 出口 session）
- 后台 thread + event loop 真实启动，验证跨线程同步桥接 OK

覆盖维度：
1. 空 specs → 不抛 / 不启线程；status 空
2. 单 server 成功 → status='connected'，list_tools 含 namespace 前缀
3. 单 server initialize 抛错 → status='failed' + error 记录 + 不阻塞 manager
4. 单 server connect 超时 → status='failed' + error 含 'timeout'
5. 多 server 一成一败 → 互不影响
6. call_tool 缺 '.' namespace → MCPCallError
7. call_tool 未知 server → MCPCallError
8. call_tool failed server → MCPCallError
9. call_tool 转发到 session 并返回 TextContent 拼接
10. call_tool isError=True → 返回带错误标记的字符串
11. call_tool 非 text content → 占位 '[non-text content: ...]'
12. shutdown idempotent + 关闭后再调 call_tool → 抛错
13. start_all idempotent（多次只启一次）
14. get_shared_manager 单例
"""
from __future__ import annotations

from typing import Any

import pytest

import src.agent.core.mcp_manager as mcp_manager_mod
from src.agent.core.mcp_config import ServerSpec
from src.agent.core.mcp_manager import (
    MCPCallError,
    MCPManager,
    get_shared_manager,
    reset_shared_manager_for_tests,
)


# ── 测试用 fake 对象（轻量手写，避免 AsyncMock 噪声） ───────────────────────


class FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object"}


class FakeListToolsResult:
    def __init__(self, tools: list[FakeTool]) -> None:
        self.tools = tools


class FakeTextContent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeImageContent:
    def __init__(self) -> None:
        self.type = "image"


class FakeCallToolResult:
    def __init__(self, content: list[Any], is_error: bool = False) -> None:
        self.content = content
        self.isError = is_error


class FakeSession:
    """既扮演 stdio_client 出口元组，也扮演 ClientSession async context manager。"""

    def __init__(
        self,
        tools: list[FakeTool] | None = None,
        call_result: FakeCallToolResult | None = None,
        init_raises: Exception | None = None,
        init_delay: float = 0.0,
        call_raises: Exception | None = None,
        call_delay: float = 0.0,
    ) -> None:
        self.tools = tools or []
        self._call_result = call_result
        self._init_raises = init_raises
        self._init_delay = init_delay
        self._call_raises = call_raises
        self._call_delay = call_delay
        self.initialize_calls = 0
        self.call_tool_calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self._init_delay > 0:
            import asyncio
            await asyncio.sleep(self._init_delay)
        if self._init_raises is not None:
            raise self._init_raises

    async def list_tools(self) -> FakeListToolsResult:
        return FakeListToolsResult(self.tools)

    async def call_tool(self, name: str, arguments: dict | None = None) -> FakeCallToolResult:
        self.call_tool_calls.append((name, arguments))
        if self._call_delay > 0:
            import asyncio
            await asyncio.sleep(self._call_delay)
        if self._call_raises is not None:
            raise self._call_raises
        return self._call_result or FakeCallToolResult([FakeTextContent("ok")])


def _install_patches(
    monkeypatch: pytest.MonkeyPatch,
    name_to_session: dict[str, FakeSession],
) -> None:
    """patch `stdio_client` + `ClientSession`，按 spec.command 路由到对应 FakeSession。"""

    class _StdioCM:
        def __init__(self, params: Any) -> None:
            self._params = params

        async def __aenter__(self) -> tuple[Any, Any]:
            return (self._params, None)

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def fake_stdio_client(params: Any) -> _StdioCM:
        return _StdioCM(params)

    def fake_client_session(read: Any, write: Any) -> FakeSession:
        params = read
        # spec.command 当 key；UT 内 spec.name == spec.command 时刚好对齐
        session = name_to_session.get(params.command)
        if session is None:
            raise RuntimeError(f"no fake session for command {params.command!r}")
        return session

    monkeypatch.setattr(mcp_manager_mod, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp_manager_mod, "ClientSession", fake_client_session)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    reset_shared_manager_for_tests()
    yield
    reset_shared_manager_for_tests()


@pytest.fixture
def short_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接超时压到 2s，让 timeout case 跑得快。"""
    import src.config as _cfg
    monkeypatch.setattr(_cfg, "MCP_CONNECT_TIMEOUT_SEC", 2)


# ── Cases ─────────────────────────────────────────────────────────────────


class TestStartAll:

    def test_empty_specs_no_error(self) -> None:
        mgr = MCPManager()
        mgr.start_all([])
        assert mgr.status() == []
        mgr.shutdown()

    def test_single_server_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("read_file"), FakeTool("write_file")])
        _install_patches(monkeypatch, {"fs": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="fs", command="fs")])
            st = mgr.status()
            assert len(st) == 1
            assert st[0]["name"] == "fs"
            assert st[0]["status"] == "connected"
            assert st[0]["tool_count"] == 2
            assert sess.initialize_calls == 1
        finally:
            mgr.shutdown()

    def test_initialize_failure_marks_server_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(init_raises=RuntimeError("handshake bad"))
        _install_patches(monkeypatch, {"bad": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="bad", command="bad")])
            st = mgr.status()
            assert st[0]["status"] == "failed"
            assert "handshake bad" in st[0]["error"]
        finally:
            mgr.shutdown()

    def test_connect_timeout_marks_server_failed(
        self, monkeypatch: pytest.MonkeyPatch, short_connect_timeout: None,
    ) -> None:
        sess = FakeSession(init_delay=10.0)  # 远大于 2s timeout
        _install_patches(monkeypatch, {"slow": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="slow", command="slow")])
            st = mgr.status()
            assert st[0]["status"] == "failed"
            assert st[0]["error"] is not None
        finally:
            mgr.shutdown()

    def test_one_failure_does_not_block_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        good = FakeSession(tools=[FakeTool("ok_tool")])
        bad = FakeSession(init_raises=RuntimeError("nope"))
        _install_patches(monkeypatch, {"good": good, "bad": bad})

        mgr = MCPManager()
        try:
            mgr.start_all([
                ServerSpec(name="good", command="good"),
                ServerSpec(name="bad", command="bad"),
            ])
            st = {s["name"]: s for s in mgr.status()}
            assert st["good"]["status"] == "connected"
            assert st["bad"]["status"] == "failed"
        finally:
            mgr.shutdown()

    def test_start_all_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"x": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="x", command="x")])
            mgr.start_all([ServerSpec(name="x", command="x")])
            assert sess.initialize_calls == 1
        finally:
            mgr.shutdown()


class TestListTools:

    def test_list_tools_with_namespace_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[
            FakeTool("read_file", description="读文件", input_schema={"type": "object", "properties": {"path": {"type": "string"}}}),
            FakeTool("write_file", description="写文件"),
        ])
        _install_patches(monkeypatch, {"fs": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="fs", command="fs")])
            tools = mgr.list_tools()
            names = {t["name"] for t in tools}
            assert names == {"fs.read_file", "fs.write_file"}
            read = next(t for t in tools if t["name"] == "fs.read_file")
            assert read["description"] == "读文件"
            assert read["server"] == "fs"
            assert read["inputSchema"]["type"] == "object"
        finally:
            mgr.shutdown()

    def test_failed_server_not_in_list_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        good = FakeSession(tools=[FakeTool("ok")])
        bad = FakeSession(init_raises=RuntimeError("nope"))
        _install_patches(monkeypatch, {"good": good, "bad": bad})

        mgr = MCPManager()
        try:
            mgr.start_all([
                ServerSpec(name="good", command="good"),
                ServerSpec(name="bad", command="bad"),
            ])
            tools = mgr.list_tools()
            assert [t["name"] for t in tools] == ["good.ok"]
        finally:
            mgr.shutdown()


class TestCallTool:

    def _bootstrap(self, monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> MCPManager:
        _install_patches(monkeypatch, {"fs": session})
        mgr = MCPManager()
        mgr.start_all([ServerSpec(name="fs", command="fs")])
        return mgr

    def test_missing_namespace_raises(self) -> None:
        mgr = MCPManager()
        with pytest.raises(MCPCallError, match="namespace"):
            mgr.call_tool("read_file", {})
        mgr.shutdown()

    def test_unknown_server_raises(self) -> None:
        mgr = MCPManager()
        with pytest.raises(MCPCallError, match="未知 server"):
            mgr.call_tool("ghost.read_file", {})
        mgr.shutdown()

    def test_failed_server_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(init_raises=RuntimeError("nope"))
        _install_patches(monkeypatch, {"bad": sess})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="bad", command="bad")])
            with pytest.raises(MCPCallError, match="状态"):
                mgr.call_tool("bad.read_file", {})
        finally:
            mgr.shutdown()

    def test_forwards_to_session_and_returns_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("read_file")],
            call_result=FakeCallToolResult([FakeTextContent("file body line")]),
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            result = mgr.call_tool("fs.read_file", {"path": "/tmp/x"})
            assert result == "file body line"
            assert sess.call_tool_calls == [("read_file", {"path": "/tmp/x"})]
        finally:
            mgr.shutdown()

    def test_multi_text_content_concatenated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("read_file")],
            call_result=FakeCallToolResult([FakeTextContent("a"), FakeTextContent("b")]),
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            result = mgr.call_tool("fs.read_file", {})
            assert result == "a\n\nb"
        finally:
            mgr.shutdown()

    def test_is_error_flagged_in_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("read_file")],
            call_result=FakeCallToolResult([FakeTextContent("permission denied")], is_error=True),
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            result = mgr.call_tool("fs.read_file", {})
            assert result.startswith("[tool reported error]")
            assert "permission denied" in result
        finally:
            mgr.shutdown()

    def test_non_text_content_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("read_file")],
            call_result=FakeCallToolResult([FakeImageContent()]),
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            result = mgr.call_tool("fs.read_file", {})
            assert "[non-text content: image]" in result
        finally:
            mgr.shutdown()

    def test_call_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("slow_tool")],
            call_delay=5.0,
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            with pytest.raises(MCPCallError, match="超时"):
                mgr.call_tool("fs.slow_tool", {}, timeout=0.5)
        finally:
            mgr.shutdown()

    def test_sdk_error_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(
            tools=[FakeTool("fail_tool")],
            call_raises=RuntimeError("internal sdk boom"),
        )
        mgr = self._bootstrap(monkeypatch, sess)
        try:
            with pytest.raises(MCPCallError, match="SDK 抛错"):
                mgr.call_tool("fs.fail_tool", {})
        finally:
            mgr.shutdown()


class TestShutdown:

    def test_shutdown_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"x": sess})

        mgr = MCPManager()
        mgr.start_all([ServerSpec(name="x", command="x")])
        mgr.shutdown()
        mgr.shutdown()  # 第二次不抛

    def test_call_tool_after_shutdown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"x": sess})

        mgr = MCPManager()
        mgr.start_all([ServerSpec(name="x", command="x")])
        mgr.shutdown()
        with pytest.raises(MCPCallError):
            mgr.call_tool("x.t", {})


class TestSingleton:

    def test_get_shared_manager_returns_same_instance(self) -> None:
        a = get_shared_manager()
        b = get_shared_manager()
        assert a is b

    def test_reset_clears_singleton(self) -> None:
        a = get_shared_manager()
        reset_shared_manager_for_tests()
        b = get_shared_manager()
        assert a is not b


class TestStartOneStopOneReload:
    """单 server 启停 / reload diff —— UI 实时生效路径"""

    def test_start_one_then_stop_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"fs": sess})

        mgr = MCPManager()
        try:
            mgr.start_one(ServerSpec(name="fs", command="fs"))
            st = {row["name"]: row for row in mgr.status()}
            assert st["fs"]["status"] == "connected"

            mgr.stop_one("fs")
            assert "fs" not in {row["name"] for row in mgr.status()}
        finally:
            mgr.shutdown()

    def test_start_one_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"fs": sess})

        mgr = MCPManager()
        try:
            spec = ServerSpec(name="fs", command="fs")
            mgr.start_one(spec)
            mgr.start_one(spec)  # 第二次跳过，不会重复 initialize
            assert sess.initialize_calls == 1
        finally:
            mgr.shutdown()

    def test_stop_one_unknown_is_noop(self) -> None:
        mgr = MCPManager()
        try:
            mgr.stop_one("ghost")  # 不抛
        finally:
            mgr.shutdown()

    def test_reload_starts_new_and_stops_removed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = FakeSession(tools=[FakeTool("ta")])
        b = FakeSession(tools=[FakeTool("tb")])
        _install_patches(monkeypatch, {"a": a, "b": b})

        mgr = MCPManager()
        try:
            mgr.start_all([ServerSpec(name="a", command="a")])
            assert {row["name"] for row in mgr.status()} == {"a"}

            mgr.reload([ServerSpec(name="b", command="b")], disabled_names=set())
            names = {row["name"] for row in mgr.status()}
            assert names == {"b"}
        finally:
            mgr.shutdown()

    def test_reload_skips_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sess = FakeSession(tools=[FakeTool("t")])
        _install_patches(monkeypatch, {"fs": sess})

        mgr = MCPManager()
        try:
            mgr.reload(
                [ServerSpec(name="fs", command="fs")],
                disabled_names={"fs"},
            )
            assert mgr.status() == []
            assert sess.initialize_calls == 0
        finally:
            mgr.shutdown()
