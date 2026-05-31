"""
MCPConfig —— MCP server 清单加载（Phase 3.3）

读取项目根的 `.agenta/mcp/config.json`（路径可由 `config.MCP_CONFIG_FILE` 覆盖），
启动时一次性加载，由 `MCPManager` 按清单拉起各 server 子进程。

设计要点：
- **配置驱动**：用户编辑一份 JSON 就能给 agent 加新 server，**无需改 agent 代码**
- **缺失/空文件 graceful**：返回 `None`，Agent 跳过 MCP 初始化（验收 ⑦ 零侵入）
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
from dataclasses import dataclass, field
from pathlib import Path

import src.config as _cfg

logger = logging.getLogger(__name__)

# ${VAR} 形式的 env 变量引用，按 POSIX shell 风格匹配；命名首字符须为 [_A-Za-z]
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class ServerSpec:
    """单个 MCP server 的启动规格。"""
    name: str                          # server 名（namespace 前缀，如 "filesystem"）
    command: str                       # 可执行命令（如 "npx" / "python"）
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


def _expand_env(value: str) -> str:
    """把 value 内 `${VAR}` 替换为 `os.environ['VAR']`；缺失保留原样。"""
    return _VAR_PATTERN.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )


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

    base = Path(root) if root is not None else Path.cwd()
    rel = file if file is not None else _cfg.MCP_CONFIG_FILE
    path = base / rel

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
