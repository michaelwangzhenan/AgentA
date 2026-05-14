import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from src.memory.lc_history import SQLiteChatMessageHistory
from src.agent.lc_tools import build_lc_tools, SearchKnowledgeInput, FetchUrlInput

def test_sqlite_history_add_and_load(tmp_path):
    db = str(tmp_path) + '/test.db'
    h = SQLiteChatMessageHistory('s1', db)
    h.add_message(HumanMessage(content='hello'))
    h.add_message(AIMessage(content='hi'))
    msgs = h.messages
    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert msgs[0].content == 'hello'

def test_sqlite_history_clear(tmp_path):
    db = str(tmp_path) + '/test.db'
    h = SQLiteChatMessageHistory('s2', db)
    h.add_message(HumanMessage(content='x'))
    h.clear()
    assert h.messages == []

def test_build_lc_tools_returns_two_base_tools():
    tools = build_lc_tools()
    names = [x.name for x in tools]
    assert 'search_knowledge' in names
    assert 'fetch_url' in names

def test_build_lc_tools_with_skills():
    bodies = {'demo': 'skill body'}
    tools = build_lc_tools(bodies)
    assert len(tools) == 3
    names = [x.name for x in tools]
    assert 'load_skill' in names

def test_search_input_defaults():
    inp = SearchKnowledgeInput(query='test')
    assert inp.top_k == 5

def test_fetch_input_defaults():
    inp = FetchUrlInput(url='http://x.com')
    assert inp.max_chars == 3000

def test_lc_agent_chat_returns_string(tmp_path):
    import src.agent.lc_agent
    db = str(tmp_path) + '/a.db'
    with patch('src.agent.lc_agent.build_chat_model') as mock_llm, \
         patch('src.agent.lc_agent.build_lc_tools') as mock_tools:
        mock_llm.return_value = MagicMock()
        mock_tools.return_value = []
        from src.agent.lc_agent import LangChainAgent
        with patch('src.agent.lc_agent.create_agent') as mock_ag:
            mock_exec = MagicMock()
            pong = AIMessage(content='pong')
            mock_exec.invoke.return_value = {'messages': [pong]}
            mock_ag.return_value = mock_exec
            agent = LangChainAgent('sess1', db_path=db)
            reply = agent.chat('ping')
            assert reply == 'pong'
            assert len(agent._history.messages) == 2
