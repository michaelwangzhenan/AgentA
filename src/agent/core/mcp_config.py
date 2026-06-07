"""
MCPConfig —— MCP server 清单加载

读取项目根的 `.agenta/mcp/config.json`（路径可由 `config.MCP_CONFIG_FILE` 覆盖），
启动时一次性加载，由 `MCPManager` 按清单拉起各 server 子进程。

设计要点：
- **配置驱动**：用户编辑一份 JSON 就能给 agent 加新 server，**无需改 agent 代码**
- **缺失/空文件 graceful**：返回 `None`，Agent 跳过 MCP 初始化（零侵入）
- **env 变量展开**：value 内 `${VAR}` 替换为 `os.environ['VAR']`；缺失保留原样
- **schema 错误 fail-fast**：JSON 格式错 / 字段缺失 / 类型错 → log warning + 返回 `None`
  （不抛异常向上传播，避免阻塞 Agent 启动）

配置文件 schema（参考 Claude Desktop / Cursor 习惯）：

```json
{
  "servers": {
    "<server_name>": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./"],
      "env": {"FOO": "bar"}
    }
  }
}
```
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import src.config as _cfg

logger = logging.getLogger(__name__)

# ${VAR} 形式的 env 变量引用，按 POSIX shell 风格匹配；命名首字符须为 [_A-Za-z]
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# server 名校验：与 OpenAI tool naming 对齐，禁含 '.'（namespace 拆分歧义）
_SERVER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# disabled 列表 fallback（实际默认从 config.MCP_DISABLED_FILE 读，可 .env 覆盖）
DEFAULT_DISABLED_FILE = Path(".agenta/mcp/disabled.json")


@dataclass(frozen=True)
class ServerSpec:
    """单个 MCP server 的启动规格。"""
    name: str                          # server 名（namespace 前缀，如 "filesystem"）
    command: str                       # 可执行命令（如 "npx" / "python"）
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class MCPConfigError(Exception):
    """MCP 配置 CRUD 操作的统一异常类，API 层捕获后映射到 4xx 状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_server_name(name: str) -> None:
    """校验 server 名合法性（同 skill name，禁含 '.'，长度 1-64）。"""
    if not isinstance(name, str) or not name.strip():
        raise MCPConfigError("invalid_name", "server 名不能为空")
    if len(name) > 64:
        raise MCPConfigError("invalid_name", "server 名不能超过 64 字符")
    if not _SERVER_NAME_PATTERN.match(name):
        raise MCPConfigError(
            "invalid_name",
            "server 名只能含字母 / 数字 / 下划线 / 连字符（^[a-zA-Z0-9_-]+$）",
        )


def _expand_env(value: str) -> str:
    """把 value 内 `${VAR}` 替换为 `os.environ['VAR']`；缺失保留原样。"""
    return _VAR_PATTERN.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )


def _resolve_config_path(root: Path | str | None, file: str | None) -> Path:
    """组装绝对配置文件路径：root / file（root 缺省 cwd，file 缺省读 config.MCP_CONFIG_FILE）。"""
    base = Path(root) if root is not None else Path.cwd()
    rel = file if file is not None else _cfg.MCP_CONFIG_FILE
    return base / rel


def _resolve_disabled_path(disabled_file: str | Path | None) -> Path:
    """显式传入优先；否则读 config.MCP_DISABLED_FILE；config 缺失时 fallback 兜底常量。"""
    if disabled_file is not None:
        return Path(disabled_file)
    try:
        return Path(_cfg.MCP_DISABLED_FILE)
    except AttributeError:
        return DEFAULT_DISABLED_FILE


def load_mcp_config(
    root: Path | str | None = None,
    *,
    file: str | None = None,
) -> list[ServerSpec] | None:
    """加载 MCP server 配置文件，返回 ServerSpec 列表。

    Args:
        root: 项目根目录；`None` 表示当前工作目录 `Path.cwd()`。
        file: 相对 `root` 的文件路径；`None` 取 `config.MCP_CONFIG_FILE`。

    Returns:
        - `MCP_ENABLED=false` / 文件不存在 / 文件为空 / schema 错 → `None`
        - 合法配置但 `servers` 为空 dict → 空列表 `[]`
        - 否则 → `ServerSpec` 列表（按 JSON 中出现顺序）

    不抛异常：所有 IO / JSON / schema 错误统一降级为 `None` + log warning。
    """
    if not _cfg.MCP_ENABLED:
        logger.debug("[MCPConfig] MCP_ENABLED=false，跳过")
        return None

    path = _resolve_config_path(root, file)

    if not path.exists() or not path.is_file():
        logger.debug("[MCPConfig] %s 不存在，跳过", path)
        return None

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[MCPConfig] 读取 %s 失败：%s", path, exc)
        return None

    raw_text = raw_text.lstrip("\ufeff").strip()
    if not raw_text:
        logger.debug("[MCPConfig] %s 为空，跳过", path)
        return None

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("[MCPConfig] %s 解析失败：%s（修复 JSON 或删除文件以跳过）", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("[MCPConfig] %s 顶层须是 object（实际 %s），跳过", path, type(data).__name__)
        return None

    servers_raw = data.get("servers")
    if servers_raw is None:
        logger.warning("[MCPConfig] %s 缺 `servers` 字段，跳过", path)
        return None
    if not isinstance(servers_raw, dict):
        logger.warning("[MCPConfig] %s `servers` 须是 object（实际 %s），跳过", path, type(servers_raw).__name__)
        return None

    specs: list[ServerSpec] = []
    for name, item in servers_raw.items():
        spec = _parse_server(name, item, source=path)
        if spec is not None:
            specs.append(spec)

    logger.info("[MCPConfig] 已加载 %s（%d server）", path, len(specs))
    return specs


def _parse_server(name: str, item: object, *, source: Path) -> ServerSpec | None:
    """校验单个 server 条目；任一字段类型 / 必填项不合法返 None（跳过该 server）。"""
    if not isinstance(name, str) or not name.strip():
        logger.warning("[MCPConfig] %s server 名非空字符串，跳过", source)
        return None
    if "." in name:
        # tool 合流时按 "<server>.<tool>" 前缀拆分，server 名含 "." 会让拆分歧义
        logger.warning("[MCPConfig] %s server 名 '%s' 含 '.'，跳过", source, name)
        return None
    if not isinstance(item, dict):
        logger.warning("[MCPConfig] %s server '%s' 须是 object，跳过", source, name)
        return None

    command = item.get("command")
    if not isinstance(command, str) or not command.strip():
        logger.warning("[MCPConfig] %s server '%s' 缺 `command` 或非字符串，跳过", source, name)
        return None

    args_raw = item.get("args", [])
    if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
        logger.warning("[MCPConfig] %s server '%s' `args` 须是 string 数组，跳过", source, name)
        return None

    env_raw = item.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()
    ):
        logger.warning("[MCPConfig] %s server '%s' `env` 须是 string→string map，跳过", source, name)
        return None

    expanded_args = [_expand_env(a) for a in args_raw]
    expanded_env = {k: _expand_env(v) for k, v in env_raw.items()}

    return ServerSpec(
        name=name,
        command=_expand_env(command),
        args=expanded_args,
        env=expanded_env,
    )


# ---------------------------------------------------------------------------
# 原始 (raw, 未做 env 展开) 配置管理 —— 给 UI CRUD 复用
# ---------------------------------------------------------------------------

# UI 编辑时操作的是"未展开"的字段（保留 ${VAR} 字面量），存盘也按原样写回。
# 已加载到 MCPManager 的 ServerSpec 才是展开后的运行时副本。


def _read_raw_config(path: Path) -> dict:
    """读原始 JSON 配置；文件不存在返回 `{"servers": {}}` 骨架。

    Raises:
        MCPConfigError(code="parse_failed"): JSON 格式错 / 顶层非 object
    """
    if not path.exists():
        return {"servers": {}}
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    except OSError as e:
        raise MCPConfigError("read_failed", f"读 {path} 失败：{e}") from e
    if not text:
        return {"servers": {}}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MCPConfigError("parse_failed", f"{path} JSON 解析失败：{e}") from e
    if not isinstance(data, dict):
        raise MCPConfigError("parse_failed", f"{path} 顶层须是 object")
    if "servers" not in data:
        data["servers"] = {}
    elif not isinstance(data["servers"], dict):
        raise MCPConfigError("parse_failed", f"{path} `servers` 须是 object")
    return data


def _atomic_write(path: Path, text: str) -> None:
    """原子写：tempfile + os.replace；保证半写状态不被读到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".mcp.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_raw_servers(
    root: Path | str | None = None,
    *,
    file: str | None = None,
) -> dict[str, dict]:
    """读未做 env 展开的原始 server dict（UI 编辑形态）：`{name: {command, args, env}}`。

    缺文件 / 空文件返回空 dict；解析错抛 MCPConfigError。
    """
    path = _resolve_config_path(root, file)
    data = _read_raw_config(path)
    return dict(data.get("servers") or {})


def write_raw_servers(
    servers: dict[str, dict],
    root: Path | str | None = None,
    *,
    file: str | None = None,
) -> None:
    """把原始 server dict 整体写回配置文件，保留 `servers` 之外的顶层字段（透传）。

    Raises:
        MCPConfigError(code="write_failed"): 文件系统错误
    """
    path = _resolve_config_path(root, file)
    try:
        data = _read_raw_config(path)
    except MCPConfigError:
        # 旧文件解析不动也要让 CRUD 能继续 — 重置成 servers-only 骨架
        data = {"servers": {}}
    data["servers"] = servers
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        _atomic_write(path, text)
    except OSError as e:
        raise MCPConfigError("write_failed", f"写 {path} 失败：{e}") from e


def _normalize_raw(item: object, *, name: str) -> dict:
    """把 UI 传来的 raw server dict 规范化（校验字段类型 + 给可选项填默认值）。

    Raises:
        MCPConfigError(code="invalid_field"): 字段类型 / 必填项不合法
    """
    if not isinstance(item, dict):
        raise MCPConfigError("invalid_field", f"server '{name}' 配置须是 object")
    command = item.get("command")
    if not isinstance(command, str) or not command.strip():
        raise MCPConfigError("invalid_field", f"server '{name}' command 必须是非空字符串")
    args = item.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise MCPConfigError("invalid_field", f"server '{name}' args 必须是字符串数组")
    env = item.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise MCPConfigError("invalid_field", f"server '{name}' env 必须是 string→string map")
    out: dict = {"command": command.strip(), "args": list(args)}
    if env:
        out["env"] = dict(env)
    return out


# ---------------------------------------------------------------------------
# CRUD 辅助 —— 文件级原子操作；运行时启停由 MCPManager 接管
# ---------------------------------------------------------------------------


def add_server(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    root: Path | str | None = None,
    file: str | None = None,
) -> ServerSpec:
    """新增 server 条目并写回 config.json，返回 env 展开后的 ServerSpec（给 manager 用）。

    Raises:
        MCPConfigError(code="invalid_name"): name 不合法
        MCPConfigError(code="invalid_field"): command / args / env 不合法
        MCPConfigError(code="already_exists"): name 已存在
        MCPConfigError(code="parse_failed" / "write_failed"): IO 错误
    """
    validate_server_name(name)
    raw = _normalize_raw(
        {"command": command, "args": list(args or []), "env": dict(env or {})},
        name=name,
    )
    servers = read_raw_servers(root, file=file)
    if name in servers:
        raise MCPConfigError("already_exists", f"server '{name}' 已存在")
    servers[name] = raw
    write_raw_servers(servers, root, file=file)
    return _to_spec(name, raw)


def update_server(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    root: Path | str | None = None,
    file: str | None = None,
) -> ServerSpec:
    """整体替换某 server 的 `command / args / env`（name 不变；改名走 rename_server）。

    Raises:
        MCPConfigError(code="invalid_name" / "invalid_field"): 入参不合法
        MCPConfigError(code="not_found"): name 不存在
    """
    validate_server_name(name)
    raw = _normalize_raw(
        {"command": command, "args": list(args or []), "env": dict(env or {})},
        name=name,
    )
    servers = read_raw_servers(root, file=file)
    if name not in servers:
        raise MCPConfigError("not_found", f"server '{name}' 不存在")
    servers[name] = raw
    write_raw_servers(servers, root, file=file)
    return _to_spec(name, raw)


def delete_server(
    name: str,
    *,
    root: Path | str | None = None,
    file: str | None = None,
) -> None:
    """从 config.json 移除某 server 条目。

    Raises:
        MCPConfigError(code="not_found"): name 不存在
    """
    validate_server_name(name)
    servers = read_raw_servers(root, file=file)
    if name not in servers:
        raise MCPConfigError("not_found", f"server '{name}' 不存在")
    del servers[name]
    write_raw_servers(servers, root, file=file)


def rename_server(
    old_name: str,
    new_name: str,
    *,
    root: Path | str | None = None,
    file: str | None = None,
    disabled_file: str | Path | None = None,
) -> ServerSpec:
    """改名：JSON key 从 `old_name` 改为 `new_name`，同时把 disabled.json 里的状态迁移过去。

    Raises:
        MCPConfigError(code="invalid_name"): new_name 不合法
        MCPConfigError(code="not_found"): old_name 不存在
        MCPConfigError(code="already_exists"): new_name 已存在
    """
    validate_server_name(new_name)
    if old_name == new_name:
        servers = read_raw_servers(root, file=file)
        if old_name not in servers:
            raise MCPConfigError("not_found", f"server '{old_name}' 不存在")
        return _to_spec(old_name, servers[old_name])

    servers = read_raw_servers(root, file=file)
    if old_name not in servers:
        raise MCPConfigError("not_found", f"server '{old_name}' 不存在")
    if new_name in servers:
        raise MCPConfigError("already_exists", f"server '{new_name}' 已存在")

    raw = servers.pop(old_name)
    # 保留原始字段顺序：先把 new_name 放尾部足够，UI 编辑场景对顺序不敏感
    servers[new_name] = raw
    write_raw_servers(servers, root, file=file)

    # 迁移 disabled list 状态（保持禁用语义跟随 name）
    try:
        disabled = read_disabled_list(disabled_file)
        if old_name in disabled:
            new_disabled = (disabled - {old_name}) | {new_name}
            write_disabled_list(new_disabled, disabled_file)
    except OSError as e:
        logger.warning("[MCPConfig] rename 后迁移 disabled 列表失败：%s", e)

    return _to_spec(new_name, raw)


def _to_spec(name: str, raw: dict) -> ServerSpec:
    """把原始 raw dict（含 `${VAR}` 字面量）展开成运行时 ServerSpec。"""
    command = _expand_env(str(raw.get("command", "")))
    args = [_expand_env(a) for a in raw.get("args", []) or []]
    env = {k: _expand_env(v) for k, v in (raw.get("env") or {}).items()}
    return ServerSpec(name=name, command=command, args=args, env=env)


def list_specs(
    root: Path | str | None = None,
    *,
    file: str | None = None,
) -> list[ServerSpec]:
    """列出 config.json 里所有 server 的运行时 ServerSpec（已 env 展开）。

    跟 `load_mcp_config` 不同：本函数**不受 `MCP_ENABLED` 控制**，永远读文件，
    用于 UI / API 层枚举（关 MCP 时 UI 仍能看到 / 编辑配置）。
    """
    servers = read_raw_servers(root, file=file)
    return [_to_spec(n, raw) for n, raw in servers.items()]


# ---------------------------------------------------------------------------
# disabled list 管理 —— 与 SkillsLoader 同款 .agenta/mcp/disabled.json
# ---------------------------------------------------------------------------


def read_disabled_list(disabled_file: str | Path | None = None) -> set[str]:
    """读 disabled 列表（JSON 数组）→ set。文件缺失 / 解析错 → 空 set + warning。"""
    path = _resolve_disabled_path(disabled_file)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.warning("[MCPConfig] %s 内容不是 JSON 数组，忽略", path)
            return set()
        return {str(name) for name in data if isinstance(name, str)}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[MCPConfig] 读 %s 失败: %s，按空 disabled 处理", path, e)
        return set()


def write_disabled_list(
    names: set[str],
    disabled_file: str | Path | None = None,
) -> None:
    """原子写 disabled 列表（排序后写入，git diff 友好）。"""
    path = _resolve_disabled_path(disabled_file)
    sorted_names = sorted(names)
    text = json.dumps(sorted_names, ensure_ascii=False, indent=2) + "\n"
    _atomic_write(path, text)


def toggle_server(
    name: str,
    enabled: bool,
    *,
    valid_names: set[str] | None = None,
    disabled_file: str | Path | None = None,
) -> bool:
    """启用 / 禁用某 server：仅修改 disabled.json，不动 config.json。

    Returns:
        新的 enabled 状态（原样返回，便于 API 回显）。

    Raises:
        MCPConfigError(code="not_found"): valid_names 传入且 name 不在其中
        MCPConfigError(code="write_failed"): 文件系统错误
    """
    if valid_names is not None and name not in valid_names:
        raise MCPConfigError("not_found", f"server '{name}' 不存在")
    current = read_disabled_list(disabled_file)
    new_set = (current - {name}) if enabled else (current | {name})
    if new_set != current:
        try:
            write_disabled_list(new_set, disabled_file)
        except OSError as e:
            raise MCPConfigError("write_failed", f"写 disabled 列表失败：{e}") from e
    return enabled


def cleanup_disabled_orphans(
    *,
    root: Path | str | None = None,
    file: str | None = None,
    disabled_file: str | Path | None = None,
) -> set[str]:
    """清理 disabled.json 里 config.json 中已不存在的 server name。

    返回被清掉的 orphan 集合（可能为空 set）。
    """
    disabled = read_disabled_list(disabled_file)
    if not disabled:
        return set()
    servers = read_raw_servers(root, file=file)
    orphans = disabled - set(servers.keys())
    if orphans:
        try:
            write_disabled_list(disabled - orphans, disabled_file)
        except OSError as e:
            logger.warning("[MCPConfig] 清理 disabled 孤儿失败：%s", e)
    return orphans
