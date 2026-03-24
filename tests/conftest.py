"""
pytest 全局配置 —— 测试环境隔离

autouse fixture：将 Agent 共享内存替换为每个测试独立的临时 SQLite DB，
测试结束后自动销毁，避免测试会话记录污染正式持久化存储。
"""
import pytest

import src.agent.agent as _agent_module
from src.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _isolated_agent_memory(tmp_path):
    """
    每个测试使用独立的临时 DB，测试结束后自动销毁。

    通过直接替换模块级 _shared_memory 实现隔离，无需修改任何测试文件。
    集成测试（标记 @pytest.mark.integration）同样受保护。
    """
    _original = _agent_module._shared_memory
    mem = MemoryStore(db_path=str(tmp_path / "agent_test.db"))
    _agent_module._shared_memory = mem
    yield
    _agent_module._shared_memory = _original
    mem.close()
