"""API key 运行时配置与持久化（仅 admin 可改）。

跟 `config_overrides` 同思路（JSON 持久化 + 启动合并 + 运行时即时生效），但 API key 是
敏感明文、且藏在 `PROVIDER_CONFIGS`（frozen dataclass）或标量属性里，所以单独用
`.agenta/api_keys.json` + admin-only 接口隔离，不混进对所有登录用户可读的 `/api/config`。

层级（优先级递增）：环境变量（.env） < runtime override（本模块写的 JSON 文件）。
reset 恢复到 .env 值（即刚 import 完时的值，下面叫 "initial"）。
"""

from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path
from typing import Literal

import src.config as _cfg

KEYS_PATH = Path(".agenta/api_keys.json")

_lock = threading.RLock()
_initial: dict[str, str] = {}  # id -> 启动时（.env / 默认）值，reset 的恢复目标
_snapshot_taken = False


@dataclasses.dataclass(frozen=True)
class SecretItem:
    """一项可配 API key 的元信息（UI 展示 + 写入目标）。"""
    id: str                              # 接口 / UI 用的逻辑 id（厂商名或 "serpapi"）
    label: str                           # UI 显示名
    kind: Literal["provider", "scalar"]  # provider=写进 PROVIDER_CONFIGS；scalar=写 _cfg 属性
    target: str                          # provider 时为 PROVIDER_CONFIGS 的 key；scalar 时为属性名
    env: str                             # 对应环境变量名（仅作 UI 提示）


# ollama 用占位 key 不需要配置，故不列入
SECRET_ITEMS: list[SecretItem] = [
    SecretItem("kimi", "Moonshot Kimi", "provider", "kimi", "MOONSHOT_API_KEY"),
    SecretItem("qwen", "通义千问", "provider", "qwen", "QWEN_API_KEY"),
    SecretItem("deepseek", "DeepSeek", "provider", "deepseek", "DEEPSEEK_API_KEY"),
    SecretItem("glm", "智谱 GLM", "provider", "glm", "GLM_API_KEY"),
    SecretItem("minimax", "MiniMax", "provider", "minimax", "MINIMAX_API_KEY"),
    SecretItem("claude", "Anthropic Claude", "provider", "claude", "ANTHROPIC_API_KEY"),
    SecretItem("openai", "OpenAI", "provider", "openai", "OPENAI_API_KEY"),
    SecretItem("grok", "xAI Grok", "provider", "grok", "GROK_API_KEY1"),
    SecretItem("gemini", "Google Gemini", "provider", "gemini", "GEMINI_API_KEY"),
    SecretItem("serpapi", "SerpAPI（web 搜索）", "scalar", "SERPAPI_API_KEY", "SERPAPI_API_KEY"),
    SecretItem("siliconflow", "硅基流动 SiliconFlow（embedding / rerank）", "scalar", "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEY"),
]

_ITEMS_BY_ID: dict[str, SecretItem] = {it.id: it for it in SECRET_ITEMS}


def get_item(key_id: str) -> SecretItem | None:
    return _ITEMS_BY_ID.get(key_id)


def current_value(item: SecretItem) -> str:
    """读取该项当前生效的明文值（仅内部 / 脱敏后才出接口）。"""
    if item.kind == "provider":
        cfg = _cfg.PROVIDER_CONFIGS.get(item.target)
        return cfg.api_key if cfg else ""
    return getattr(_cfg, item.target, "") or ""


def _set_value(item: SecretItem, value: str) -> None:
    # provider 的 ProviderConfig 是 frozen，只能整体重建后替换 dict entry；
    # provider.py 每次调用都现读 PROVIDER_CONFIGS，故替换后下一次 LLM 调用即生效。
    if item.kind == "provider":
        cfg = _cfg.PROVIDER_CONFIGS.get(item.target)
        if cfg is not None:
            _cfg.PROVIDER_CONFIGS[item.target] = dataclasses.replace(cfg, api_key=value)
    else:
        setattr(_cfg, item.target, value)


def _snapshot_initial() -> None:
    """记录所有项的 initial 值（.env / 默认）；必须在首次 apply override 前调用，幂等。"""
    global _snapshot_taken
    if _snapshot_taken:
        return
    for item in SECRET_ITEMS:
        _initial[item.id] = current_value(item)
    _snapshot_taken = True


def _read_file() -> dict[str, str]:
    if not KEYS_PATH.exists():
        return {}
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _write_file(data: dict[str, str]) -> None:
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(KEYS_PATH)


def apply_overrides() -> dict[str, str]:
    """启动时调用：snapshot initial → 读文件 → 覆盖到运行时配置。返回已应用的 override。"""
    with _lock:
        _snapshot_initial()
        applied: dict[str, str] = {}
        for key_id, value in _read_file().items():
            item = _ITEMS_BY_ID.get(key_id)
            if item is not None:
                _set_value(item, value)
                applied[key_id] = value
        return applied


def is_overridden(key_id: str) -> bool:
    with _lock:
        return key_id in _read_file()


def set_key(key_id: str, value: str) -> None:
    """持久化 override + 写进运行时配置。"""
    item = _ITEMS_BY_ID.get(key_id)
    if item is None:
        raise KeyError(key_id)
    with _lock:
        _snapshot_initial()
        data = _read_file()
        data[key_id] = value
        _write_file(data)
        _set_value(item, value)


def clear_key(key_id: str) -> str:
    """删 override，把运行时配置恢复到启动时 .env 值；返回恢复后的值。"""
    item = _ITEMS_BY_ID.get(key_id)
    if item is None:
        raise KeyError(key_id)
    with _lock:
        _snapshot_initial()
        data = _read_file()
        if key_id in data:
            del data[key_id]
            _write_file(data)
        initial = _initial.get(key_id, "")
        _set_value(item, initial)
        return initial


def mask(value: str) -> str:
    """脱敏：只露尾 4 位（如 sk-…3f9a），永不返回完整明文。空值返回空串。"""
    if not value:
        return ""
    if len(value) <= 7:
        return "•" * len(value)
    return f"{value[:3]}…{value[-4:]}"


def reset_for_test() -> None:
    """仅供 UT：删文件 + 清 snapshot 状态。"""
    global _snapshot_taken
    with _lock:
        if KEYS_PATH.exists():
            KEYS_PATH.unlink()
        _initial.clear()
        _snapshot_taken = False
