"""Config 运行时 override 持久化。

UI 改的值持久化到 `.agenta/config_overrides.json`；启动时把文件内容覆盖到
`src.config` 模块属性，作为 `os.getenv` 默认值之上的第三层。

层级（优先级递增）：
  1. 代码硬编码默认值（`os.getenv("X", "default_value")` 中的 default）
  2. 环境变量（.env / 系统 env）
  3. runtime override（本模块持久化的 JSON 文件，UI 改的值）

reset 操作恢复到第 2 层（即模块刚 import 完时的值，下面叫 "initial"）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import src.config as _cfg

OVERRIDES_PATH = Path(".agenta/config_overrides.json")

_lock = threading.RLock()
_initial_values: dict[str, Any] = {}
_snapshot_taken = False


def _snapshot_initial() -> None:
    """记录所有 registry 项的 initial 值（来自 .env 或硬编码默认）。

    必须在第一次 apply override 之前调用；幂等。
    """
    global _snapshot_taken
    if _snapshot_taken:
        return
    from src.api.config_meta import REGISTRY
    for item in REGISTRY:
        if item.editable:
            _initial_values[item.key] = getattr(_cfg, item.key, None)
    _snapshot_taken = True


def _read_file() -> dict[str, Any]:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(data: dict[str, Any]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OVERRIDES_PATH)


def apply_overrides() -> dict[str, Any]:
    """启动时调用：snapshot initial → 加载文件 → 覆盖 _cfg。返回已应用的 overrides。"""
    with _lock:
        _snapshot_initial()
        overrides = _read_file()
        applied: dict[str, Any] = {}
        for key, value in overrides.items():
            if key in _initial_values:
                setattr(_cfg, key, value)
                applied[key] = value
        return applied


def get_overrides() -> dict[str, Any]:
    with _lock:
        return _read_file()


def is_overridden(key: str) -> bool:
    with _lock:
        return key in _read_file()


def get_initial_value(key: str) -> Any:
    """返回启动时 snapshot 的值（reset 恢复的目标）。"""
    _snapshot_initial()
    return _initial_values.get(key)


def set_override(key: str, value: Any) -> Any:
    """持久化 override 到文件 + setattr 到 _cfg；返回新值。"""
    with _lock:
        _snapshot_initial()
        data = _read_file()
        data[key] = value
        _write_file(data)
        setattr(_cfg, key, value)
        return value


def clear_override(key: str) -> Any:
    """从 overrides 删 key + 把 _cfg 恢复到 initial 值；返回 initial 值。"""
    with _lock:
        _snapshot_initial()
        data = _read_file()
        if key in data:
            del data[key]
            _write_file(data)
        initial = _initial_values.get(key)
        setattr(_cfg, key, initial)
        return initial


def reload_from_file() -> dict[str, Any]:
    """重新读 overrides 文件 → 同步到 _cfg → 对所有发生变化的 key 触发副作用 hook。

    使用场景：用户在编辑器里手动改了 `.agenta/config_overrides.json`，UI 点 "重新加载"
    把磁盘内容拉回内存（无需重启 uvicorn）。

    返回 `{key: (old_value, new_value)}` 描述本次实际变化的 key。
    """
    from src.api import config_hooks
    from src.api.config_meta import REGISTRY

    changed: dict[str, tuple[Any, Any]] = {}
    with _lock:
        _snapshot_initial()
        data = _read_file()
        for item in REGISTRY:
            if not item.editable:
                continue
            old_value = getattr(_cfg, item.key, None)
            new_value = data.get(item.key, _initial_values.get(item.key))
            if old_value != new_value:
                setattr(_cfg, item.key, new_value)
                changed[item.key] = (old_value, new_value)
    # hook 在锁外触发，避免 hook 内部回头读 config 死锁
    for key, (old, new) in changed.items():
        config_hooks.run_post_change_hook(key, old, new)
    return changed


def reset_for_test() -> None:
    """仅供 UT：清掉文件 + snapshot 状态，下次 import 重置。"""
    global _snapshot_taken
    with _lock:
        if OVERRIDES_PATH.exists():
            OVERRIDES_PATH.unlink()
        _initial_values.clear()
        _snapshot_taken = False
