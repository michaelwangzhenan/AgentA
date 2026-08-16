import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from src.stores.langchain_history import SQLiteChatMessageHistory
from src.agent.langchain_tools import (
    build_langchain_tools,
    SearchKnowledgeInput,
    FetchUrlInput,
    _schema_to_model,
)

# LangChain Agent 用 langchain 1.x 的 create_agent（LangGraph）实现。
# 默认套件按需 deselect（import langchain 较慢），用 `bash tools/ut.sh -lc` 单独跑。
pytestmark = pytest.mark.langchain


# ── SQLite history ──────────────────────────────────────────────────────────
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


# ── 工具动态包装 ──────────────────────────────────────────────────────────────
def test_build_langchain_tools_default():
    tools = build_langchain_tools()
    names = [x.name for x in tools]
    assert 'search_knowledge' in names
    assert 'fetch_url' in names

def test_build_langchain_tools_full_coverage():
    """全业务工具应被动态包装（plan / study / quiz / srs）。"""
    names = {x.name for x in build_langchain_tools()}
    for expected in (
        'make_plan', 'update_step', 'abort_plan',
        'create_study_plan', 'update_study_progress', 'query_study_status',
        'create_quiz', 'grade_quiz', 'query_quiz_history',
        'add_to_srs', 'query_srs_due', 'review_srs_card', 'query_srs_stats',
    ):
        assert expected in names, f'缺少工具 {expected}'

def test_build_langchain_tools_with_skills():
    tools = build_langchain_tools({'demo': 'body'})
    assert any(x.name == 'load_skill' for x in tools)

def test_tools_route_through_execute_tool():
    """StructuredTool 调用应路由到 execute_tool，并透传 citation_builder。"""
    sentinel = object()
    with patch('src.agent.langchain_tools.execute_tool') as p_exec:
        res = MagicMock()
        res.status = 'ok'
        res.to_llm_str.return_value = 'RESULT'
        p_exec.return_value = res
        tools = build_langchain_tools(citation_getter=lambda: sentinel)
        search = next(t for t in tools if t.name == 'search_knowledge')
        out = search.func(query='q', top_k=3)
    assert out == 'RESULT'
    _, kwargs = p_exec.call_args
    assert kwargs['citation_builder'] is sentinel
    assert p_exec.call_args[0][0] == 'search_knowledge'

def test_tools_empty_hint_appended():
    with patch('src.agent.langchain_tools.execute_tool') as p_exec:
        res = MagicMock()
        res.status = 'empty'
        res.to_llm_str.return_value = '无结果'
        p_exec.return_value = res
        search = next(t for t in build_langchain_tools() if t.name == 'search_knowledge')
        out = search.func(query='q')
    assert 'web_search' in out  # TOOL_EMPTY_HINT 被追加


# ── schema → pydantic ─────────────────────────────────────────────────────────
def test_schema_to_model_required_and_optional():
    model = _schema_to_model('demo', {
        'type': 'object',
        'properties': {
            'q': {'type': 'string', 'description': 'query'},
            'n': {'type': 'integer', 'default': 5},
        },
        'required': ['q'],
    })
    inst = model(q='hi')
    assert inst.q == 'hi'
    assert inst.n == 5

def test_search_input_defaults():
    assert SearchKnowledgeInput(query='q').top_k == 5

def test_fetch_input_defaults():
    assert FetchUrlInput(url='http://x.com').max_chars == 3000


# ── LangChainAgent ────────────────────────────────────────────────────────────
def _mk(sess='sid', prompt='p', tc=None):
    """构造 LangChainAgent + 返回 (agent, None)。

    mock 掉 LLM / tools / 共享 ChatHistory，避免真实 langchain 调用与磁盘读写。
    create_agent 在 run() 内每轮重建，故其 mock 放在 _run() 里（见下）。
    第二个返回值保留为 None 以兼容历史调用 `ag, _ = _mk()`。
    """
    with patch('src.agent.langchain_agent.build_chat_model') as p_llm, \
         patch('src.agent.langchain_agent.build_langchain_tools') as p_tools, \
         patch('src.agent.langchain_agent.get_shared_session_store') as p_ch:
        p_llm.return_value = MagicMock()
        p_tools.return_value = []
        p_ch.return_value = MagicMock()
        from src.agent.langchain_agent import LangChainAgent
        ag = LangChainAgent(session_id=sess, system_prompt=prompt, thinking_config=tc)
    return ag, None


def _run(ag, *args, output='ok', messages=None, **kwargs):
    """调用 ag.run，期间桩掉 create_agent + 四层 prompt helper + 历史加载（避免真实 langchain/DB）。

    output：单条 AIMessage 正文（messages 为 None 时用它包一条）；
    messages：自定义 invoke 返回的消息列表（覆盖 output，用于 usage 等场景）。
    """
    out_msgs = messages if messages is not None else [AIMessage(content=output)]
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {'messages': out_msgs}
    mem = MagicMock()
    ctxs = [
        patch('src.agent.langchain_agent.create_agent', return_value=mock_agent),
        patch('src.agent.langchain_agent.build_layered_system_prompt', return_value=('sys', mem)),
        patch('src.agent.langchain_agent.load_truncated_lc_messages', return_value=[]),
    ]
    for c in ctxs:
        c.start()
    try:
        return ag.run(*args, **kwargs)
    finally:
        for c in ctxs:
            c.stop()


def test_langchain_agent_satisfies_agent_api():
    """LangChainAgent 必须满足 AgentAPI Protocol（duck-typed 契约）。"""
    from src.agent.agent_api import AgentAPI
    ag, _ = _mk()
    assert isinstance(ag, AgentAPI)

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
    r = _run(ag, 'hello')
    assert r == 'ok'

def test_langchain_agent_run_emits_final_answer():
    ag, _ = _mk()
    seen = []
    ag.set_event_callback(lambda ev: seen.append(ev.type))
    _run(ag, 'hello')
    assert 'final_answer' in seen

def test_langchain_agent_run_appends_citation():
    """citation render 非空时应拼接到回答末尾。"""
    ag, _ = _mk()
    with patch('src.agent.langchain_agent.CitationBuilder') as p_cb:
        cb = MagicMock()
        cb.renumber_and_render.return_value = ('答案 [1]', '\n\n— sources —\n[1] doc')
        p_cb.return_value = cb
        out = _run(ag, 'q', output='答案 [1]')
    assert out.endswith('[1] doc')

def test_langchain_agent_activate_skill_first_true():
    ag, _ = _mk()
    assert ag.activate_skill('sk', 'body') is True
    assert 'sk' in ag._system_prompt

def test_langchain_agent_activate_skill_duplicate_false():
    ag, _ = _mk()
    ag.activate_skill('sk', 'body')
    assert ag.activate_skill('sk', 'body') is False


# ── 事件桥接 handler ──────────────────────────────────────────────────────────
def test_event_bridge_tool_and_plan_events():
    from src.agent.core.event_bus import EventBus, AgentEvent
    from src.agent.langchain_agent import _EventBridgeHandler
    seen = []
    bus = EventBus()
    for t in ('tool_call_start', 'tool_call_end', 'plan_created', 'plan_step_start', 'plan_step_end'):
        bus.subscribe(t, lambda p, _t=t: seen.append(_t))
    h = _EventBridgeHandler(bus)
    h.on_tool_start({'name': 'make_plan'}, '{"steps": ["a", "b"]}', run_id='r1')
    h.on_tool_start({'name': 'update_step'}, '{"step_id": 1, "status": "success"}', run_id='r2')
    h.on_tool_end('some output', run_id='r1')
    assert 'tool_call_start' in seen
    assert 'tool_call_end' in seen
    assert 'plan_created' in seen
    assert 'plan_step_start' in seen
    assert 'plan_step_end' in seen

def test_event_bridge_token_chunk():
    from src.agent.core.event_bus import EventBus
    from src.agent.langchain_agent import _EventBridgeHandler
    seen = []
    bus = EventBus()
    bus.subscribe('token_chunk', lambda p: seen.append(p['text']))
    h = _EventBridgeHandler(bus)
    h.on_llm_new_token('hello')
    h.on_llm_new_token('')  # 空 token 不发
    assert seen == ['hello']


# ── token 统计（P-C2）────────────────────────────────────────────────────────
def test_langchain_agent_usage_extracted():
    """从响应 AIMessage.usage_metadata 累加出 last_usage。"""
    ag, _ = _mk()
    msg = AIMessage(content='ok', usage_metadata={
        'input_tokens': 3, 'output_tokens': 5, 'total_tokens': 8,
    })
    _run(ag, 'hi', messages=[msg])
    assert ag.last_usage is not None
    assert ag.last_usage.prompt_tokens == 3
    assert ag.last_usage.completion_tokens == 5
    assert ag.last_usage.total_tokens == 8


def test_langchain_agent_usage_none_when_absent():
    """无 usage_metadata 时 last_usage 保持 None。"""
    ag, _ = _mk()
    _run(ag, 'hi', output='no-usage')
    assert ag.last_usage is None


# ── 历史截断（P-C1）──────────────────────────────────────────────────────────
def test_langchain_agent_run_uses_truncated_history():
    """run() 应经 load_truncated_lc_messages 拉历史并拼到本轮 user 前。"""
    ag, _ = _mk()
    mem = MagicMock()
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {'messages': [AIMessage(content='ok')]}
    hist = [HumanMessage(content='prev-q'), AIMessage(content='prev-a')]
    with patch('src.agent.langchain_agent.create_agent', return_value=mock_agent), \
         patch('src.agent.langchain_agent.build_layered_system_prompt', return_value=('sys', mem)), \
         patch('src.agent.langchain_agent.load_truncated_lc_messages', return_value=hist) as p_load:
        ag.run('new-q')
    p_load.assert_called_once()
    sent = mock_agent.invoke.call_args[0][0]['messages']
    assert [m.content for m in sent] == ['prev-q', 'prev-a', 'new-q']


# ── plan 审批门（P-C3）───────────────────────────────────────────────────────
def test_plan_approval_reject_raises_and_flags():
    from src.agent.core.agent_commons import PlanAbortedByUser
    ag, _ = _mk()
    ag.approval_callback = lambda payload: 'no'
    with patch('src.agent.core.agent_commons._cfg.PLAN_PERMISSION_MODE', True):
        with pytest.raises(PlanAbortedByUser):
            ag._approve_plan(['a', 'b'])
    assert ag._plan_aborted is True


def test_plan_approval_yes_no_abort():
    ag, _ = _mk()
    ag.approval_callback = lambda payload: 'yes'
    with patch('src.agent.core.agent_commons._cfg.PLAN_PERMISSION_MODE', True):
        ag._approve_plan(['a'])  # 不抛
    assert ag._plan_aborted is False


def test_plan_approval_off_returns_yes():
    """PLAN_PERMISSION_MODE=false 时即使 callback 返 no 也放行（不抛、不置 flag）。"""
    ag, _ = _mk()
    ag.approval_callback = lambda payload: 'no'
    with patch('src.agent.core.agent_commons._cfg.PLAN_PERMISSION_MODE', False):
        ag._approve_plan(['a'])  # 不抛
    assert ag._plan_aborted is False


def test_make_plan_tool_invokes_approval_fn():
    """make_plan 工具成功后应调用 approval_fn 并把 steps 透传。"""
    calls = []
    with patch('src.agent.langchain_tools.execute_tool') as p_exec:
        res = MagicMock()
        res.status = 'ok'
        res.to_llm_str.return_value = 'PLAN'
        p_exec.return_value = res
        tools = build_langchain_tools(approval_fn=lambda steps: calls.append(steps))
        mp = next(t for t in tools if t.name == 'make_plan')
        out = mp.func(steps=['a', 'b'])
    assert out == 'PLAN'
    assert calls == [['a', 'b']]
