"""
test_cli_handlers_mcp —— Phase 3.3 `/mcp` 命令组 UT

覆盖（验收 ④）：
- 无参 / `list` 子命令：空 manager / 多 server 状态 / failed server 显示 error
- `tools` 子命令：空 list / 多 tool 含 namespace / 来源 server / description 截断
- 非法子命令 → 显示用法
- manager=None（MCP 禁用）→ 友好提示而非崩
"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from src.cli.handlers import handle_mcp


def _make_collector() -> tuple[list[str], "callable"]:
    lines: list[str] = []

    def out(msg: str) -> None:
        lines.append(msg)

    return lines, out


@pytest.fixture
def healthy_manager() -> Iterator[MagicMock]:
    """两个 server：一个 connected（2 tools），一个 failed。"""
    mgr = MagicMock()
    mgr.status.return_value = [
        {
            "name": "filesystem",
            "status": "connected",
            "tool_count": 2,
            "error": None,
            "command": "npx -y @modelcontextprotocol/server-filesystem ./",
        },
        {
            "name": "fetch",
            "status": "failed",
            "tool_count": 0,
            "error": "RuntimeError: handshake bad",
            "command": "python -m mcp_server_fetch",
        },
    ]
    mgr.list_tools.return_value = [
        {
            "name": "filesystem.read_file",
            "description": "读取文件内容并返回字符串",
            "inputSchema": {"type": "object"},
            "server": "filesystem",
        },
        {
            "name": "filesystem.write_file",
            "description": "写入文件",
            "inputSchema": {"type": "object"},
            "server": "filesystem",
        },
    ]
    yield mgr


class TestMcpList:

    def test_no_args_lists_servers(self, healthy_manager: MagicMock) -> None:
        lines, out = _make_collector()
        handle_mcp(healthy_manager, ["/mcp"], out=out)
        joined = "".join(lines)
        assert "MCP server 列表" in joined
        assert "filesystem" in joined
        assert "fetch" in joined
        assert "connected" in joined
        assert "failed" in joined
        # tool 数和命令片段都要展示
        assert "tools=2" in joined
        # failed server 应附 error
        assert "handshake bad" in joined

    def test_list_subcommand_same_as_no_args(self, healthy_manager: MagicMock) -> None:
        lines_a, out_a = _make_collector()
        lines_b, out_b = _make_collector()
        handle_mcp(healthy_manager, ["/mcp"], out=out_a)
        handle_mcp(healthy_manager, ["/mcp", "list"], out=out_b)
        assert "".join(lines_a) == "".join(lines_b)

    def test_empty_manager(self) -> None:
        mgr = MagicMock()
        mgr.status.return_value = []
        lines, out = _make_collector()
        handle_mcp(mgr, ["/mcp"], out=out)
        assert "当前无 MCP server" in "".join(lines)

    def test_none_manager_does_not_crash(self) -> None:
        lines, out = _make_collector()
        handle_mcp(None, ["/mcp"], out=out)
        assert "当前无 MCP server" in "".join(lines)


class TestMcpTools:

    def test_tools_lists_namespaced_tools(self, healthy_manager: MagicMock) -> None:
        lines, out = _make_collector()
        handle_mcp(healthy_manager, ["/mcp", "tools"], out=out)
        joined = "".join(lines)
        assert "MCP tool 列表" in joined
        assert "filesystem.read_file" in joined
        assert "filesystem.write_file" in joined
        assert "[filesystem]" in joined

    def test_tools_empty(self) -> None:
        mgr = MagicMock()
        mgr.list_tools.return_value = []
        lines, out = _make_collector()
        handle_mcp(mgr, ["/mcp", "tools"], out=out)
        assert "当前无可用 MCP tool" in "".join(lines)

    def test_long_description_truncated(self) -> None:
        mgr = MagicMock()
        mgr.list_tools.return_value = [{
            "name": "x.t",
            "description": "x" * 200,
            "inputSchema": {"type": "object"},
            "server": "x",
        }]
        lines, out = _make_collector()
        handle_mcp(mgr, ["/mcp", "tools"], out=out)
        joined = "".join(lines)
        assert "…" in joined  # 截断符
        # 截断后不应包含全部 200 字符
        assert "x" * 200 not in joined


class TestMcpUnknownSubcommand:

    def test_unknown_subcommand_shows_usage(self, healthy_manager: MagicMock) -> None:
        lines, out = _make_collector()
        handle_mcp(healthy_manager, ["/mcp", "bogus"], out=out)
        joined = "".join(lines)
        assert "未知子命令" in joined
        assert "/mcp list" in joined
        assert "/mcp tools" in joined
