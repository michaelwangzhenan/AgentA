"""
test_mcp_config —— Phase 3.3 MCP server 清单加载行为单测

覆盖维度（与验收标准 ① + ⑦ 对应）：
1. 文件缺失 / 空 / 全空白 → None（验收 ⑦ 零侵入）
2. MCP_ENABLED=false 即便文件存在也跳过
3. JSON 解析失败 → None（不抛）
4. schema 错（顶层非 object / `servers` 缺失或非 object）→ None
5. 单个 server 字段不合法（缺 command / args 非数组 / env 非 string map）→ 跳过该 server，其余正常加载
6. server 名含 "." → 跳过（避免 namespace 拆分歧义）
7. 正常配置 → ServerSpec 列表，含 env 变量展开（${VAR} 替换 / 缺失保留）
8. 显式 file= 覆盖 config.MCP_CONFIG_FILE

测试通过临时目录 + monkeypatch 隔离外部环境。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.config as _cfg
from src.agent.core.mcp_config import (
    MCPConfigError,
    ServerSpec,
    add_server,
    cleanup_disabled_orphans,
    delete_server,
    list_specs,
    load_mcp_config,
    read_disabled_list,
    rename_server,
    toggle_server,
    update_server,
    write_disabled_list,
)


def _write_config(tmp_path: Path, content: str | dict) -> Path:
    cfg_dir = tmp_path / ".agenta" / "mcp"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content), encoding="utf-8")
    return path


class TestGracefulMissingOrEmpty:
    """缺失 / 空 / disable 三条路径，全部 None 且不抛。"""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_mcp_config(root=tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "")
        assert load_mcp_config(root=tmp_path) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "   \n\t  \n")
        assert load_mcp_config(root=tmp_path) is None

    def test_disabled_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_config(tmp_path, {"servers": {"x": {"command": "echo"}}})
        monkeypatch.setattr(_cfg, "MCP_ENABLED", False)
        assert load_mcp_config(root=tmp_path) is None


class TestSchemaErrorsAreGraceful:
    """JSON / schema 错统一降级为 None，不抛。"""

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "{not valid json}")
        assert load_mcp_config(root=tmp_path) is None

    def test_non_object_top_level_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '["servers"]')
        assert load_mcp_config(root=tmp_path) is None

    def test_missing_servers_field_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"other": {}})
        assert load_mcp_config(root=tmp_path) is None

    def test_servers_non_object_returns_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": ["x", "y"]})
        assert load_mcp_config(root=tmp_path) is None


class TestPerServerValidation:
    """单 server 不合法跳过该条，其余继续加载。"""

    def test_skip_server_without_command(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {
            "good": {"command": "echo", "args": ["hi"]},
            "bad": {"args": ["hi"]},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert [s.name for s in result] == ["good"]

    def test_skip_server_with_non_string_args(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {
            "bad": {"command": "echo", "args": [1, 2]},
            "good": {"command": "echo"},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert [s.name for s in result] == ["good"]

    def test_skip_server_with_non_string_env(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {
            "bad": {"command": "echo", "env": {"K": 123}},
            "good": {"command": "echo"},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert [s.name for s in result] == ["good"]

    def test_skip_server_name_with_dot(self, tmp_path: Path) -> None:
        """server 名含 '.' 会让 namespace 拆分歧义，必须拒。"""
        _write_config(tmp_path, {"servers": {
            "bad.name": {"command": "echo"},
            "good": {"command": "echo"},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert [s.name for s in result] == ["good"]

    def test_empty_servers_dict_returns_empty_list(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {}})
        assert load_mcp_config(root=tmp_path) == []


class TestNormalLoad:
    """合法配置 → 完整 ServerSpec 列表。"""

    def test_minimal_server_loads_with_defaults(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {
            "fetch": {"command": "python", "args": ["-m", "mcp_server_fetch"]},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result == [ServerSpec(
            name="fetch",
            command="python",
            args=["-m", "mcp_server_fetch"],
            env={},
        )]

    def test_preserves_server_order(self, tmp_path: Path) -> None:
        _write_config(tmp_path, {"servers": {
            "a": {"command": "echo"},
            "b": {"command": "echo"},
            "c": {"command": "echo"},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert [s.name for s in result] == ["a", "b", "c"]


class TestEnvVarExpansion:
    """${VAR} 在 command / args / env value 中均展开。"""

    def test_expand_in_args(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_WORKDIR", "/tmp/work")
        _write_config(tmp_path, {"servers": {
            "fs": {"command": "npx", "args": ["-y", "fs-server", "${MY_WORKDIR}"]},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert result[0].args == ["-y", "fs-server", "/tmp/work"]

    def test_expand_in_env_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret123")
        _write_config(tmp_path, {"servers": {
            "x": {"command": "echo", "env": {"API_TOKEN": "${MY_TOKEN}"}},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert result[0].env == {"API_TOKEN": "secret123"}

    def test_missing_var_preserves_literal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺失 ${VAR} 不抛，保留原字面（让 server 启动时自己决定是否报错）。"""
        monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
        _write_config(tmp_path, {"servers": {
            "x": {"command": "echo", "args": ["${DEFINITELY_NOT_SET}"]},
        }})
        result = load_mcp_config(root=tmp_path)
        assert result is not None
        assert result[0].args == ["${DEFINITELY_NOT_SET}"]


class TestExplicitFileOverride:
    """显式 file= 覆盖 config.MCP_CONFIG_FILE。"""

    def test_custom_file_path(self, tmp_path: Path) -> None:
        alt = tmp_path / "my_mcp.json"
        alt.write_text(json.dumps({"servers": {"x": {"command": "echo"}}}), encoding="utf-8")

        result = load_mcp_config(root=tmp_path, file="my_mcp.json")
        assert result is not None
        assert [s.name for s in result] == ["x"]


# ── CRUD 用例：UI 编辑路径 ─────────────────────────────────────────────────────


def _disabled_path(tmp_path: Path) -> Path:
    return tmp_path / ".agenta" / "mcp" / "disabled.json"


class TestAddServer:
    def test_add_creates_file_and_returns_spec(self, tmp_path: Path) -> None:
        spec = add_server(
            "fs",
            "npx",
            args=["-y", "filesystem"],
            env={"K": "V"},
            root=tmp_path,
        )
        assert spec == ServerSpec(name="fs", command="npx", args=["-y", "filesystem"], env={"K": "V"})
        cfg = json.loads((tmp_path / ".agenta/mcp/config.json").read_text())
        assert cfg["servers"]["fs"]["command"] == "npx"
        assert cfg["servers"]["fs"]["args"] == ["-y", "filesystem"]
        assert cfg["servers"]["fs"]["env"] == {"K": "V"}

    def test_add_rejects_invalid_name(self, tmp_path: Path) -> None:
        with pytest.raises(MCPConfigError) as ei:
            add_server("bad.name", "echo", root=tmp_path)
        assert ei.value.code == "invalid_name"

    def test_add_rejects_duplicate(self, tmp_path: Path) -> None:
        add_server("fs", "echo", root=tmp_path)
        with pytest.raises(MCPConfigError) as ei:
            add_server("fs", "echo", root=tmp_path)
        assert ei.value.code == "already_exists"

    def test_add_rejects_empty_command(self, tmp_path: Path) -> None:
        with pytest.raises(MCPConfigError) as ei:
            add_server("fs", "", root=tmp_path)
        assert ei.value.code == "invalid_field"


class TestUpdateServer:
    def test_update_replaces_fields(self, tmp_path: Path) -> None:
        add_server("fs", "old", args=["a"], env={"X": "1"}, root=tmp_path)
        spec = update_server("fs", "new", args=["b"], env={}, root=tmp_path)
        assert spec.command == "new"
        assert spec.args == ["b"]
        assert spec.env == {}
        cfg = json.loads((tmp_path / ".agenta/mcp/config.json").read_text())
        assert cfg["servers"]["fs"]["args"] == ["b"]

    def test_update_404(self, tmp_path: Path) -> None:
        with pytest.raises(MCPConfigError) as ei:
            update_server("ghost", "echo", root=tmp_path)
        assert ei.value.code == "not_found"


class TestDeleteServer:
    def test_delete_removes_entry(self, tmp_path: Path) -> None:
        add_server("a", "x", root=tmp_path)
        add_server("b", "y", root=tmp_path)
        delete_server("a", root=tmp_path)
        cfg = json.loads((tmp_path / ".agenta/mcp/config.json").read_text())
        assert list(cfg["servers"].keys()) == ["b"]

    def test_delete_404(self, tmp_path: Path) -> None:
        with pytest.raises(MCPConfigError) as ei:
            delete_server("ghost", root=tmp_path)
        assert ei.value.code == "not_found"


class TestRenameServer:
    def test_rename_updates_key(self, tmp_path: Path) -> None:
        add_server("old", "echo", args=["x"], root=tmp_path)
        spec = rename_server("old", "neo", root=tmp_path, disabled_file=_disabled_path(tmp_path))
        assert spec.name == "neo"
        cfg = json.loads((tmp_path / ".agenta/mcp/config.json").read_text())
        assert "old" not in cfg["servers"]
        assert cfg["servers"]["neo"]["command"] == "echo"

    def test_rename_migrates_disabled_status(self, tmp_path: Path) -> None:
        add_server("old", "echo", root=tmp_path)
        disabled_file = _disabled_path(tmp_path)
        write_disabled_list({"old"}, disabled_file)
        rename_server("old", "neo", root=tmp_path, disabled_file=disabled_file)
        assert read_disabled_list(disabled_file) == {"neo"}

    def test_rename_409_when_target_exists(self, tmp_path: Path) -> None:
        add_server("a", "x", root=tmp_path)
        add_server("b", "y", root=tmp_path)
        with pytest.raises(MCPConfigError) as ei:
            rename_server("a", "b", root=tmp_path, disabled_file=_disabled_path(tmp_path))
        assert ei.value.code == "already_exists"

    def test_rename_same_name_is_noop(self, tmp_path: Path) -> None:
        add_server("a", "x", root=tmp_path)
        spec = rename_server("a", "a", root=tmp_path, disabled_file=_disabled_path(tmp_path))
        assert spec.name == "a"


class TestToggleServer:
    def test_toggle_writes_disabled_list(self, tmp_path: Path) -> None:
        df = _disabled_path(tmp_path)
        toggle_server("fs", False, valid_names={"fs"}, disabled_file=df)
        assert read_disabled_list(df) == {"fs"}
        toggle_server("fs", True, valid_names={"fs"}, disabled_file=df)
        assert read_disabled_list(df) == set()

    def test_toggle_404_when_not_in_valid(self, tmp_path: Path) -> None:
        with pytest.raises(MCPConfigError) as ei:
            toggle_server("ghost", False, valid_names={"fs"}, disabled_file=_disabled_path(tmp_path))
        assert ei.value.code == "not_found"


class TestEnvVarPassthroughInRawConfig:
    """raw 配置读写保留 ${VAR} 字面量；只有运行时 ServerSpec 才展开。"""

    def test_raw_preserves_literal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PATH", "/tmp/x")
        add_server("fs", "npx", args=["${MY_PATH}"], env={"P": "${MY_PATH}"}, root=tmp_path)
        cfg = json.loads((tmp_path / ".agenta/mcp/config.json").read_text())
        # raw JSON 必须保留 ${MY_PATH} 字面量，便于 UI 编辑后仍可见原始变量名
        assert cfg["servers"]["fs"]["args"] == ["${MY_PATH}"]
        assert cfg["servers"]["fs"]["env"] == {"P": "${MY_PATH}"}

    def test_list_specs_returns_expanded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PATH", "/tmp/x")
        add_server("fs", "${MY_PATH}/bin", args=["${MY_PATH}"], root=tmp_path)
        specs = list_specs(root=tmp_path)
        assert specs[0].command == "/tmp/x/bin"
        assert specs[0].args == ["/tmp/x"]


class TestCleanupOrphans:
    def test_cleanup_removes_orphan_names(self, tmp_path: Path) -> None:
        add_server("alive", "x", root=tmp_path)
        df = _disabled_path(tmp_path)
        write_disabled_list({"alive", "ghost"}, df)
        orphans = cleanup_disabled_orphans(root=tmp_path, disabled_file=df)
        assert orphans == {"ghost"}
        assert read_disabled_list(df) == {"alive"}
