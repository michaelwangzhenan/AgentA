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
    from src.api.runtime import config_overrides as _ov

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
    - 临时关闭 SEMANTIC_CACHE_ENABLED：语义缓存共用进程级 ChromaDB（不随测试隔离），
      历次跑积累的条目会让 chat 端点测试随机命中缓存、跳过 agent.run 而失败；
      默认关掉保证确定性。需要验证缓存的测试自行 monkeypatch 设回 True。
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

    # ── 语义缓存隔离 ──────────────────────────────────────────────────────────
    _orig_cache = _agent_module._cfg.SEMANTIC_CACHE_ENABLED
    _agent_module._cfg.SEMANTIC_CACHE_ENABLED = False

    # ── 业务 store 单例全局兜底隔离 ─────────────────────────────────────────────
    # 这些 store 的 get_shared_store() 默认指向真实 ./sqlite_db/*.db。不全局兜底的话，
    # 任何走 agent.run / execute_tool / API 而忘了自己隔离的测试会静默读写真实库
    # —— 例如 Agent.run 经 build_active_study_plan_block 只读真实 learning.db。
    # 各测试文件原有的文件内 reset / dependency_overrides 仍在测试体内覆盖本兜底，互不冲突。
    from src.memory import (
        golden_store,
        learning_plan_store,
        quiz_store,
        security_event_store,
        semantic_cache,
        srs_store,
        trace_store,
        usage_store,
        user_store,
    )

    # 用独立 in-memory SQLite（":memory:"）建临时实例：避免每个测试建 8 个磁盘库的 IO 开销。
    # 各调用方最终都读模块全局 _shared_store，reset 后无论 import 方式都拿到临时库 → 覆盖完整。
    _store_specs = [
        (learning_plan_store, learning_plan_store.LearningPlanStore),
        (quiz_store, quiz_store.QuizStore),
        (srs_store, srs_store.SRSStore),
        (usage_store, usage_store.UsageStore),
        (golden_store, golden_store.GoldenStore),
        (trace_store, trace_store.TraceStore),
        (security_event_store, security_event_store.SecurityEventStore),
        (user_store, user_store.UserStore),
    ]
    _tmp_stores = []
    for _mod, _cls in _store_specs:
        _s = _cls(":memory:")
        _mod.reset_shared_store_for_testing(_s)
        _tmp_stores.append((_mod, _s))
    # 语义缓存依赖进程级 ChromaDB，不便建临时实例；置空单例即可（enabled 已关）
    semantic_cache.reset_shared_store_for_testing(None)

    yield

    # ── 恢复原始状态 ──────────────────────────────────────────────────────────
    _commons_module._chat_history = _orig_mem
    _agent_module._shared_user_memory = _orig_user_mem
    _agent_module._cfg.USER_MEMORY_ENABLED = _orig_enabled
    _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT = _orig_auto
    _agent_module._cfg.SEMANTIC_CACHE_ENABLED = _orig_cache
    for _mod, _s in _tmp_stores:
        _mod.reset_shared_store_for_testing(None)
        try:
            _s.close()
        except Exception:
            pass
    semantic_cache.reset_shared_store_for_testing(None)
    mem.close()


@pytest.fixture
def ut_llm_model() -> str:
    """integration（真实 LLM）测试用的 model id。

    UT_LLM_MODEL 配了且合法就用它，否则回落 ACTIVE_MODEL —— 便于把测试统一指到便宜 /
    快的模型，不动用生产默认模型。integration 测试用 `config.use_llm_prefs(ut_llm_model)` 接入。
    """
    import src.config as _cfg

    return _cfg.resolve_ut_llm_model()
