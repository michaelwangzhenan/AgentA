"""
pytest 全局配置 —— 测试环境隔离

autouse fixture：将 Agent 共享内存替换为每个测试独立的临时 SQLite DB，
测试结束后自动销毁，避免测试会话记录污染正式持久化存储。

同样隔离 UserMemoryStore：测试期间关闭用户记忆功能，避免额外 LLM 调用
干扰 mock call count，也避免向真实 DB 写入测试数据。
"""
import pytest

import src.agent.agent as _agent_module


@pytest.fixture(autouse=True)
def _isolated_agent_memory(tmp_path):
    """
    每个测试使用独立的临时 DB，测试结束后自动销毁。

    - 替换 _shared_memory：隔离对话历史 DB
    - 替换 _shared_user_memory 为 None，并临时关闭 USER_MEMORY_ENABLED：
      防止 _try_extract_memories 向真实 DB 写入、或额外调用 LLM 干扰 mock 计数
    """
    from src.memory.chat_history import ChatHistory

    # ── 对话历史隔离 ──────────────────────────────────────────────────────────
    _orig_mem = _agent_module._shared_memory
    mem = ChatHistory(db_path=str(tmp_path / "agent_test.db"))
    _agent_module._shared_memory = mem

    # ── 用户记忆隔离 ──────────────────────────────────────────────────────────
    _orig_user_mem = _agent_module._shared_user_memory
    _orig_enabled = _agent_module._cfg.USER_MEMORY_ENABLED
    _orig_auto = _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT
    _agent_module._shared_user_memory = None
    _agent_module._cfg.USER_MEMORY_ENABLED = False
    _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT = False

    yield

    # ── 恢复原始状态 ──────────────────────────────────────────────────────────
    _agent_module._shared_memory = _orig_mem
    _agent_module._shared_user_memory = _orig_user_mem
    _agent_module._cfg.USER_MEMORY_ENABLED = _orig_enabled
    _agent_module._cfg.USER_MEMORY_AUTO_EXTRACT = _orig_auto
    mem.close()
