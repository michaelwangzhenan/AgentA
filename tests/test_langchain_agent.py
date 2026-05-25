import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from src.memory.langchain_history import SQLiteChatMessageHistory
from src.agent.langchain_tools import build_langchain_tools, SearchKnowledgeInput, FetchUrlInput

# LangChain Agent 用 langchain 0.3 的 create_tool_calling_agent + AgentExecutor 实现。
# 默认套件按需 deselect（import langchain 较慢，~7s 左右），用 `bash tools/ut.sh -lc` 单独跑。
pytestmark = pytest.mark.langchain

def test_sqlite_history_add_and_load(tmp_path):
    db = str(tmp_path) + '/test.db'
    h = SQLiteChatMessageHistory('s1', db)
    h.add_message(HumanMessage(content='hello'))
    h.add_message(AIMessage(content='hi'))
    assert len(h.messages) == 2
    assert h.messages[0].content == 'hello'

def test_sqlite_history_clear(tmp_path):
    db = str(tmp_path) + '/test.db'
    h = SQLiteChatMessageHistory('s2', db)
    h.add_message(HumanMessage(content='x'))
    h.clear()
    assert h.messages == []

def test_build_langchain_tools_default():
    tools = build_langchain_tools()
    names = [x.name for x in tools]
    assert 'search_knowledge' in names
    assert 'fetch_url' in names

def test_build_langchain_tools_with_skills():
    tools = build_langchain_tools({'demo': 'body'})
    assert any(x.name == 'load_skill' for x in tools)

def test_search_input_defaults():
    assert SearchKnowledgeInput(query='q').top_k == 5

def test_fetch_input_defaults():
    assert FetchUrlInput(url='http://x.com').max_chars == 3000

def _mk(sess='sid', prompt='p', tc=None):
    """构造 LangChainAgent + 返回 (agent, mock_executor)。

    完整 mock 掉 LLM / tools / SQLite / agent / executor，避免真实 langchain 调用。
    """
    with patch('src.agent.langchain_agent.build_chat_model') as p_llm, \
         patch('src.agent.langchain_agent.build_langchain_tools') as p_tools, \
         patch('src.agent.langchain_agent.SQLiteChatMessageHistory') as p_hist, \
         patch('src.agent.langchain_agent.create_tool_calling_agent') as p_create, \
         patch('src.agent.langchain_agent.AgentExecutor') as p_exec:
        p_llm.return_value = MagicMock()
        p_tools.return_value = []
        hi = MagicMock()
        hi.messages = []
        p_hist.return_value = hi
        p_create.return_value = MagicMock()
        mx = MagicMock()
        mx.invoke.return_value = {'output': 'ok'}
        p_exec.return_value = mx
        from src.agent.langchain_agent import LangChainAgent
        ag = LangChainAgent(session_id=sess, system_prompt=prompt, thinking_config=tc)
    return ag, mx

def test_langchain_agent_session_id():
    ag, _ = _mk(sess='my-id')
    assert ag.session_id == 'my-id'

def test_langchain_agent_session_id_auto():
    ag, _ = _mk(sess=None)
    assert ag.session_id

def test_langchain_agent_last_usage_none():
    ag, _ = _mk()
    assert ag.last_usage is None

def test_langchain_agent_thinking_cfg_stored():
    cfg = MagicMock()
    ag, _ = _mk(tc=cfg)
    assert ag.thinking_cfg is cfg

def test_langchain_agent_run():
    ag, _ = _mk()
    r = ag.run('hello')
    assert r == 'ok'

def test_langchain_agent_activate_skill_first_true():
    ag, _ = _mk()
    assert ag.activate_skill('sk', 'body') is True
    assert 'sk' in ag._system_prompt

def test_langchain_agent_activate_skill_duplicate_false():
    ag, _ = _mk()
    ag.activate_skill('sk', 'body')
    assert ag.activate_skill('sk', 'body') is False
