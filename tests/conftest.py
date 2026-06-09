"""
pytest 全局配置 —— 测试环境隔离

autouse fixture：将 Agent 共享内存替换为每个测试独立的临时 SQLite DB，
测试结束后自动销毁，避免测试会话记录污染正式持久化存储。

同样隔离 UserMemoryStore：测试期间关闭用户记忆功能，避免额外 LLM 调用
干扰 mock call count，也避免向真实 DB 写入测试数据。

模块 import 顺序：
    必须先 load_dotenv() 再 import src.* —— src.config 在 import 时即读取
    os.getenv()，否则 PROVIDER_CONFIGS 中的 api_key 全为空字符串，会让
    test_provider_api_key_not_empty / test_get_active_model_returns_provider_and_model
    等"配置存在性"测试统一 fail。与 main.py 同源行为。
"""
from dotenv import load_dotenv
load_dotenv(override=True)

import pytest

import src.agent.agent as _agent_module
# _chat_history 单例已抽到公共层 agent_commons（三实现共享）；隔离需 patch 这里。
import src.agent.core.agent_commons as _commons_module


@pytest.fixture(autouse=True)
def _disable_auth_by_default():
    """默认关认证：所有 API 测试落到 DEFAULT_USER_ID（且为 admin），

    保持既有 API 测试无需登录即可跑通。需要验证认证 / 隔离的测试自行
    把 `_cfg.AUTH_ENABLED` 设回 True。
    """
    import src.config as _cfg

    orig = _cfg.AUTH_ENABLED
    _cfg.AUTH_ENABLED = False
    yield
    _cfg.AUTH_ENABLED = orig


@pytest.fixture(autouse=True)
def _neutralize_runtime_overrides():
    """中和运行时 override 对全局 config 的污染。

    `src.api.main` 在 import 时就调 `apply_overrides()`，把 `.agenta/config_overrides.json`
    （开发者用 UI 存的运行时配置，如 `THINKING_ENABLED=true` / `ACTIVE_MODEL=qwen3.5-flash`）灌进
    全局 `_cfg`。全量 pytest 收集阶段一旦 import 到 main，这些 override 会泄漏到不 mock LLM 的
    测试里（agent 误走 thinking 分支发起真·LLM 调用，导致挂起 / 断言失败）。

    这里每个测试前把被 snapshot 的 editable key 复位到 import 时的 env 基线（`_initial_values`），
    不删 override 文件（`reset_for_test` 会 unlink 文件，会清掉开发者的真实配置，故不用它）。
    snapshot 未建立（没 import 过 main）时为 no-op，此时本来也无污染。
    """
    from src.api import config_overrides as _ov

    if _ov._snapshot_taken:
        for key, val in _ov._initial_values.items():
            setattr(_ov._cfg, key, val)
    yield


@pytest.fixture(autouse=True)
def _isolated_agent_memory(tmp_path, _neutralize_runtime_overrides):
    """
    每个测试使用独立的临时 DB，测试结束后自动销毁。

    依赖 `_neutralize_runtime_overrides` 先复位 config 到 env 基线，确保本 fixture 对
    USER_MEMORY_ENABLED 等的关闭发生在复位之后、不被复位覆盖回 env 值。

    - 替换 _chat_history：隔离对话历史 DB
    - 替换 _shared_user_memory 为 None，并临时关闭 USER_MEMORY_ENABLED：
      防止 MemoryManager.try_extract 向真实 DB 写入、或额外调用 LLM 干扰 mock 计数
    """
    from src.memory.chat_history import ChatHistoryStore

    # ── 对话历史隔离 ──────────────────────────────────────────────────────────
    _orig_mem = _commons_module._chat_history
    mem = ChatHistoryStore(db_path=str(tmp_path / "agent_test.db"))
    _commons_module._chat_history = mem

    # ── 用户记忆隔离 ──────────────────────────────────────────────────────────
    _orig_user_mem = _agent_module._shared_user_memory
    _orig_enabled = _agent_module._cfg.USER_MEMORY_ENABLED
    _orig_auto = _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT
    _agent_module._shared_user_memory = None
    _agent_module._cfg.USER_MEMORY_ENABLED = False
    _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT = False

    yield

    # ── 恢复原始状态 ──────────────────────────────────────────────────────────
    _commons_module._chat_history = _orig_mem
    _agent_module._shared_user_memory = _orig_user_mem
    _agent_module._cfg.USER_MEMORY_ENABLED = _orig_enabled
    _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT = _orig_auto
    mem.close()
