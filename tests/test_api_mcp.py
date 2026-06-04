"""MCP server / tools 列表端点 UT。

mock MCPManager 返回固定 status / list_tools，不真起 server。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_mcp_manager
from src.api.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _make_mock_manager(
    status_rows: list[dict[str, Any]],
    tool_rows: list[dict[str, Any]],
) -> MagicMock:
    m = MagicMock()
    m.status.return_value = status_rows
    m.list_tools.return_value = tool_rows
    return m


def test_list_servers_empty() -> None:
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager([], [])
    client = TestClient(app)
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    assert r.json() == {"servers": []}


def test_list_servers_with_data() -> None:
    rows = [
        {
            "name": "fs",
            "status": "connected",
            "tool_count": 3,
            "error": None,
            "command": "npx @modelcontextprotocol/server-filesystem",
        },
        {
            "name": "broken",
            "status": "failed",
            "tool_count": 0,
            "error": "ENOENT",
            "command": "/missing/bin",
        },
    ]
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager(rows, [])
    client = TestClient(app)
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    body = r.json()
    assert len(body["servers"]) == 2
    assert body["servers"][0]["status"] == "connected"
    assert body["servers"][1]["error"] == "ENOENT"


def test_list_tools_empty() -> None:
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager([], [])
    client = TestClient(app)
    r = client.get("/api/mcp/tools")
    assert r.status_code == 200
    assert r.json() == {"tools": []}


def test_list_tools_with_data() -> None:
    rows = [
        {
            "name": "fs.read_file",
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "server": "fs",
        },
        {
            "name": "fs.list_dir",
            "description": "List a directory",
            "inputSchema": {"type": "object"},
            "server": "fs",
        },
    ]
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager([], rows)
    client = TestClient(app)
    r = client.get("/api/mcp/tools")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tools"]) == 2
    assert body["tools"][0]["name"] == "fs.read_file"
    assert body["tools"][0]["server"] == "fs"
