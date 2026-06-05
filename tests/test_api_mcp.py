"""MCP servers / tools 端点 UT。

Mock `MCPManager` 不真起子进程；用 monkeypatch 把 routes 模块本地引用的
`list_specs` / `read_disabled_list` / `add_server` 等替换成内存版本，
避免读到真实 `.agenta/mcp/config.json`。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import src.api.routes.mcp as mcp_routes
from src.agent.core.mcp_config import MCPConfigError, ServerSpec
from src.api.deps import get_mcp_manager
from src.api.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _make_mock_manager(
    status_rows: list[dict[str, Any]] | None = None,
    tool_rows: list[dict[str, Any]] | None = None,
) -> MagicMock:
    m = MagicMock()
    m.status.return_value = status_rows or []
    m.list_tools.return_value = tool_rows or []
    return m


def _patch_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    specs: list[ServerSpec] | None = None,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    """把 routes 模块里的 list_specs / read_disabled_list 替换成内存桩，
    并返回一个共享 state dict 供测试观察 add/update/delete/rename/toggle 的写入效果。
    """
    state: dict[str, Any] = {
        "specs": list(specs or []),
        "disabled": set(disabled or set()),
        "calls": [],
    }

    def _list_specs() -> list[ServerSpec]:
        return list(state["specs"])

    def _read_disabled_list() -> set[str]:
        return set(state["disabled"])

    def _add_server(name: str, command: str, args: list[str], env: dict[str, str]) -> ServerSpec:
        if any(s.name == name for s in state["specs"]):
            raise MCPConfigError("already_exists", f"server '{name}' 已存在")
        spec = ServerSpec(name=name, command=command, args=list(args), env=dict(env))
        state["specs"].append(spec)
        state["calls"].append(("add", name))
        return spec

    def _update_server(name: str, command: str, args: list[str], env: dict[str, str]) -> ServerSpec:
        for i, s in enumerate(state["specs"]):
            if s.name == name:
                spec = ServerSpec(name=name, command=command, args=list(args), env=dict(env))
                state["specs"][i] = spec
                state["calls"].append(("update", name))
                return spec
        raise MCPConfigError("not_found", f"server '{name}' 不存在")

    def _delete_server(name: str) -> None:
        for i, s in enumerate(state["specs"]):
            if s.name == name:
                state["specs"].pop(i)
                state["calls"].append(("delete", name))
                return
        raise MCPConfigError("not_found", f"server '{name}' 不存在")

    def _rename_server(old_name: str, new_name: str) -> ServerSpec:
        for i, s in enumerate(state["specs"]):
            if s.name == old_name:
                if any(x.name == new_name for x in state["specs"]):
                    raise MCPConfigError("already_exists", f"server '{new_name}' 已存在")
                spec = ServerSpec(name=new_name, command=s.command, args=list(s.args), env=dict(s.env))
                state["specs"][i] = spec
                if old_name in state["disabled"]:
                    state["disabled"].discard(old_name)
                    state["disabled"].add(new_name)
                state["calls"].append(("rename", old_name, new_name))
                return spec
        raise MCPConfigError("not_found", f"server '{old_name}' 不存在")

    def _toggle_server(name: str, enabled: bool, *, valid_names: set[str] | None = None) -> bool:
        if valid_names is not None and name not in valid_names:
            raise MCPConfigError("not_found", f"server '{name}' 不存在")
        if enabled:
            state["disabled"].discard(name)
        else:
            state["disabled"].add(name)
        state["calls"].append(("toggle", name, enabled))
        return enabled

    def _cleanup_disabled_orphans() -> set[str]:
        names = {s.name for s in state["specs"]}
        orphans = state["disabled"] - names
        state["disabled"] -= orphans
        return orphans

    monkeypatch.setattr(mcp_routes, "list_specs", _list_specs)
    monkeypatch.setattr(mcp_routes, "read_disabled_list", _read_disabled_list)
    monkeypatch.setattr(mcp_routes, "add_server", _add_server)
    monkeypatch.setattr(mcp_routes, "update_server", _update_server)
    monkeypatch.setattr(mcp_routes, "delete_server", _delete_server)
    monkeypatch.setattr(mcp_routes, "rename_server", _rename_server)
    monkeypatch.setattr(mcp_routes, "toggle_server", _toggle_server)
    monkeypatch.setattr(mcp_routes, "cleanup_disabled_orphans", _cleanup_disabled_orphans)
    return state


# ── GET 端点 ─────────────────────────────────────────────────────────────────


def test_list_servers_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    app.dependency_overrides[get_mcp_manager] = _make_mock_manager
    client = TestClient(app)
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    assert r.json() == {"servers": []}


def test_list_servers_merges_config_disabled_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config 里两个 server，disabled 标了一个，manager 跑了一个 → 输出应合并三源。"""
    _patch_config(
        monkeypatch,
        specs=[
            ServerSpec(name="fs", command="npx", args=["-y", "filesystem"], env={}),
            ServerSpec(name="off", command="bash", args=["-c", "echo"], env={"K": "V"}),
        ],
        disabled={"off"},
    )
    runtime = [
        {"name": "fs", "status": "connected", "tool_count": 3, "error": None, "command": "npx -y filesystem"},
    ]
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager(runtime, [])
    client = TestClient(app)
    r = client.get("/api/mcp/servers")
    assert r.status_code == 200
    body = r.json()
    by_name = {s["name"]: s for s in body["servers"]}
    assert by_name["fs"]["enabled"] is True
    assert by_name["fs"]["status"] == "connected"
    assert by_name["fs"]["tool_count"] == 3
    assert by_name["fs"]["args"] == ["-y", "filesystem"]
    assert by_name["off"]["enabled"] is False
    assert by_name["off"]["status"] == "closed"
    assert by_name["off"]["env"] == {"K": "V"}


def test_list_tools_with_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    rows = [
        {
            "name": "fs.read_file",
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "server": "fs",
        },
    ]
    app.dependency_overrides[get_mcp_manager] = lambda: _make_mock_manager([], rows)
    client = TestClient(app)
    r = client.get("/api/mcp/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["tools"][0]["name"] == "fs.read_file"


# ── POST / PUT / DELETE / RENAME ────────────────────────────────────────────


def test_create_server_writes_and_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(monkeypatch)
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post(
        "/api/mcp/servers",
        json={"name": "fs", "command": "npx", "args": ["-y", "filesystem"], "env": {}},
    )
    assert r.status_code == 201
    assert r.json()["name"] == "fs"
    assert r.json()["enabled"] is True
    assert ("add", "fs") in state["calls"]
    manager.start_one.assert_called_once()


def test_update_server_restarts_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="fs", command="old", args=[], env={})],
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.put(
        "/api/mcp/servers/fs",
        json={"command": "npx", "args": ["-y", "filesystem"], "env": {}},
    )
    assert r.status_code == 200
    assert ("update", "fs") in state["calls"]
    manager.stop_one.assert_called_once_with("fs")
    manager.start_one.assert_called_once()


def test_update_server_skips_restart_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="fs", command="old", args=[], env={})],
        disabled={"fs"},
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.put(
        "/api/mcp/servers/fs",
        json={"command": "npx", "args": [], "env": {}},
    )
    assert r.status_code == 200
    manager.stop_one.assert_not_called()
    manager.start_one.assert_not_called()


def test_update_server_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.put(
        "/api/mcp/servers/ghost",
        json={"command": "x", "args": [], "env": {}},
    )
    assert r.status_code == 404


def test_delete_server(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="fs", command="x", args=[], env={})],
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.delete("/api/mcp/servers/fs")
    assert r.status_code == 204
    assert ("delete", "fs") in state["calls"]
    manager.stop_one.assert_called_once_with("fs")


def test_rename_server_migrates_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="old", command="x", args=[], env={})],
        disabled={"old"},
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/servers/old/rename", json={"new_name": "neo"})
    assert r.status_code == 200
    assert r.json()["name"] == "neo"
    assert ("rename", "old", "neo") in state["calls"]
    assert "neo" in state["disabled"]
    # 改名后新 name 在 disabled 集合里 → 不应启动
    manager.start_one.assert_not_called()
    manager.stop_one.assert_called_once_with("old")


def test_rename_409_when_target_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(
        monkeypatch,
        specs=[
            ServerSpec(name="a", command="x", args=[], env={}),
            ServerSpec(name="b", command="y", args=[], env={}),
        ],
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/servers/a/rename", json={"new_name": "b"})
    assert r.status_code == 409


# ── TOGGLE ─────────────────────────────────────────────────────────────────


def test_toggle_enable_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="fs", command="x", args=[], env={})],
        disabled={"fs"},
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/servers/fs/toggle", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"name": "fs", "enabled": True}
    assert "fs" not in state["disabled"]
    manager.start_one.assert_called_once()


def test_toggle_disable_stops_server(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_config(
        monkeypatch,
        specs=[ServerSpec(name="fs", command="x", args=[], env={})],
    )
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/servers/fs/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert "fs" in state["disabled"]
    manager.stop_one.assert_called_once_with("fs")


def test_toggle_404_for_unknown_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    manager = _make_mock_manager()
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/servers/ghost/toggle", json={"enabled": True})
    assert r.status_code == 404


# ── RELOAD ─────────────────────────────────────────────────────────────────


def test_reload_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(
        monkeypatch,
        specs=[
            ServerSpec(name="a", command="x", args=[], env={}),
            ServerSpec(name="b", command="y", args=[], env={}),
        ],
        disabled={"b"},
    )
    runtime = [
        {"name": "a", "status": "connected", "tool_count": 2, "error": None, "command": "x"},
    ]
    manager = _make_mock_manager(runtime, [])
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    client = TestClient(app)
    r = client.post("/api/mcp/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["enabled"] == 1
    assert body["connected"] == 1
    assert body["failed"] == 0
    manager.reload.assert_called_once()
