"""
测试：AutoGPT Agent（Plan → Execute → Review 三阶段循环）

测试内容：
    - 初始化参数与默认值
    - Plan 阶段：JSON 解析成功 / 失败回退
    - Execute 阶段：TASK_COMPLETE 提取、工具调用子循环、工具轮次上限
    - Review 阶段：综合任务结果生成最终回答
    - run() 全流程编排
    - 接口契约：session_id / last_usage / activate_skill / thinking_cfg
    - 历史持久化：只写 user + assistant
    - make_agent() 工厂：三种 IMP_METHOD 返回正确类型
    - 集成测试（@pytest.mark.integration）
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from src.agent.autogpt_agent import AutoGPTAgent
from src.agent.agent import ThinkingConfig, TokenUsage, SYSTEM_PROMPT
from src.agent.tools import ToolResult
from src.agent.core.event_bus import EVENT_PLAN_CREATED, EVENT_TOKEN_CHUNK
from src.memory.chat_history import ChatHistoryStore

# AutoGPT Agent 本期不验证（详见 iter_2_agent.md §4.4.3），整文件默认 deselect
pytestmark = pytest.mark.autogpt


# ── 辅助：构造 mock LLM response ─────────────────────────────────────────────

def _text_resp(content: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> Any:
    """无工具调用的纯文本 response。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    if prompt_tokens or completion_tokens:
        resp.usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    return resp


def _tool_resp(tool_name: str, tool_args: dict, call_id: str = "call_001") -> Any:
    """包含 tool_calls 的 response。"""
    tc = SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )
    message = SimpleNamespace(content="", tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _plan_resp(tasks: list[str], reasoning: str = "合理分解") -> Any:
    """Plan 阶段：返回合法 JSON 的 response。"""
    payload = json.dumps({"tasks": tasks, "reasoning": reasoning}, ensure_ascii=False)
    return _text_resp(payload)


def _make_agent(
    tmp_path,
    *,
    session_id: str = "test-session",
    max_plan_tasks: int = 4,
    max_task_tool_rounds: int = 2,
    verbose: bool = False,
    skills=None,
) -> AutoGPTAgent:
    """工厂：构建一个使用临时 DB 的 AutoGPTAgent（不依赖全局 ChatHistoryStore）。"""
    ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
    ag = AutoGPTAgent(
        verbose=verbose,
        session_id=session_id,
        chat_history=ch,
        max_plan_tasks=max_plan_tasks,
        max_task_tool_rounds=max_task_tool_rounds,
        skills=skills,
    )
    return ag


def _make_skill_info(name: str, desc: str, body: str) -> Any:
    from src.skills.skill_loader import SkillInfo
    return SkillInfo(
        name=name,
        description=desc,
        location=Path(f"/fake/{name}/SKILL.md"),
        body=body,
    )


# ── 初始化 ────────────────────────────────────────────────────────────────────

class TestAutoGPTInit:
    def test_session_id_auto_generated(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert isinstance(ag.session_id, str) and len(ag.session_id) > 0

    def test_custom_session_id(self, tmp_path):
        ag = _make_agent(tmp_path, session_id="my-sess")
        assert ag.session_id == "my-sess"

    def test_last_usage_initially_none(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag.last_usage is None

    def test_verbose_stored(self, tmp_path):
        ag = _make_agent(tmp_path, verbose=True)
        assert ag.verbose is True

    def test_thinking_cfg_defaults(self, tmp_path):
        import src.config as _cfg
        ag = _make_agent(tmp_path)
        assert ag.thinking_cfg.enabled == _cfg.THINKING_ENABLED
        assert ag.thinking_cfg.budget == _cfg.THINKING_BUDGET

    def test_thinking_cfg_explicit(self, tmp_path):
        cfg = ThinkingConfig(enabled=True, budget=8000)
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(thinking_config=cfg, chat_history=ch, verbose=False)
        assert ag.thinking_cfg.enabled is True
        assert ag.thinking_cfg.budget == 8000

    def test_max_plan_tasks_from_constructor(self, tmp_path):
        ag = _make_agent(tmp_path, max_plan_tasks=3)
        assert ag._max_plan_tasks == 3

    def test_max_task_tool_rounds_from_constructor(self, tmp_path):
        ag = _make_agent(tmp_path, max_task_tool_rounds=2)
        assert ag._max_task_tool_rounds == 2

    def test_skills_extracted_to_bodies(self, tmp_path):
        skill = _make_skill_info("writer", "写作助手", "写作规范正文")
        ag = _make_agent(tmp_path, skills={"writer": skill})
        assert "writer" in ag._skill_bodies
        assert ag._skill_bodies["writer"] == "写作规范正文"

    def test_skills_catalog_appended_to_system_prompt(self, tmp_path):
        skill = _make_skill_info("coder", "编程助手", "编程规范正文")
        ag = _make_agent(tmp_path, skills={"coder": skill})
        # build_skill_catalog 会把 skill 描述追加到 system_prompt
        assert "coder" in ag._system_prompt or "编程助手" in ag._system_prompt

    def test_system_prompt_default(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(chat_history=ch, verbose=False)
        assert ag._system_prompt == SYSTEM_PROMPT

    def test_system_prompt_custom(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(system_prompt="自定义提示", chat_history=ch, verbose=False)
        assert ag._system_prompt == "自定义提示"


# ── Plan 阶段 ─────────────────────────────────────────────────────────────────

class TestAutoGPTPlan:
    def test_plan_parses_json_task_list(self, tmp_path):
        ag = _make_agent(tmp_path)
        tasks_expected = ["搜索知识库", "抓取网页"]
        with patch("src.agent.autogpt_agent.chat", return_value=_plan_resp(tasks_expected)):
            result = ag._plan("目标", "")
        assert result == tasks_expected

    def test_plan_parses_fenced_json(self, tmp_path):
        """模型把 JSON 包进 ```json 代码围栏时也能解析（修 iter_99 §5.1）。"""
        ag = _make_agent(tmp_path)
        inner = json.dumps({"tasks": ["搜索", "总结"], "reasoning": "x"}, ensure_ascii=False)
        raw = f"```json\n{inner}\n```"
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp(raw)):
            result = ag._plan("目标", "")
        assert result == ["搜索", "总结"]

    def test_plan_parses_json_with_prose_around(self, tmp_path):
        """JSON 前后夹着说明文字时，靠首个 {…} 兜底也能解析。"""
        ag = _make_agent(tmp_path)
        inner = json.dumps({"tasks": ["a", "b"], "reasoning": "x"}, ensure_ascii=False)
        raw = f"好的，计划如下：\n{inner}\n以上。"
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp(raw)):
            result = ag._plan("目标", "")
        assert result == ["a", "b"]

    def test_plan_strips_empty_tasks(self, tmp_path):
        ag = _make_agent(tmp_path)
        raw = json.dumps({"tasks": ["任务1", "", "  ", "任务2"], "reasoning": "x"})
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp(raw)):
            result = ag._plan("目标", "")
        assert result == ["任务1", "任务2"]

    def test_plan_truncates_to_max_plan_tasks(self, tmp_path):
        ag = _make_agent(tmp_path, max_plan_tasks=2)
        tasks = ["t1", "t2", "t3", "t4"]
        with patch("src.agent.autogpt_agent.chat", return_value=_plan_resp(tasks)):
            result = ag._plan("目标", "")
        assert len(result) == 2
        assert result == ["t1", "t2"]

    def test_plan_fallback_on_invalid_json(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp("这不是JSON")):
            result = ag._plan("用户问题", "")
        # fallback: 返回 [goal]
        assert result == ["用户问题"]

    def test_plan_fallback_on_missing_tasks_key(self, tmp_path):
        ag = _make_agent(tmp_path)
        raw = json.dumps({"reasoning": "没有 tasks 字段"})
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp(raw)):
            result = ag._plan("用户问题", "")
        assert result == ["用户问题"]

    def test_plan_fallback_on_empty_tasks_list(self, tmp_path):
        ag = _make_agent(tmp_path)
        raw = json.dumps({"tasks": [], "reasoning": "空列表"})
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp(raw)):
            result = ag._plan("用户问题", "")
        assert result == ["用户问题"]

    def test_plan_passes_max_tasks_to_prompt(self, tmp_path):
        ag = _make_agent(tmp_path, max_plan_tasks=3)
        captured: list[list] = []

        def fake_chat(messages, **kw):
            captured.append(messages)
            return _plan_resp(["t1"])

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._plan("目标", "")

        system_content = captured[0][0]["content"]
        assert "3" in system_content  # max_tasks 已注入提示词

    def test_plan_includes_history_hint_when_non_empty(self, tmp_path):
        ag = _make_agent(tmp_path)
        captured: list[list] = []

        def fake_chat(messages, **kw):
            captured.append(messages)
            return _plan_resp(["t1"])

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._plan("目标", "历史摘要内容")

        user_content = captured[0][1]["content"]
        assert "历史摘要内容" in user_content


# ── Execute 阶段 ───────────────────────────────────────────────────────────────

class TestAutoGPTExecute:
    def test_execute_returns_task_complete_content(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat",
                   return_value=_text_resp("TASK_COMPLETE: 任务已完成，结果如下")):
            result = ag._execute_task("查找资料", "用户目标", [])
        assert result == "任务已完成，结果如下"

    def test_execute_fallback_when_no_task_complete_marker(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat",
                   return_value=_text_resp("直接返回了正文内容")):
            result = ag._execute_task("查找资料", "用户目标", [])
        assert result == "直接返回了正文内容"

    def test_execute_with_tool_call_then_task_complete(self, tmp_path):
        ag = _make_agent(tmp_path, max_task_tool_rounds=2)
        call_count = 0

        def fake_chat(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_resp("search_knowledge", {"query": "RAG"})
            return _text_resp("TASK_COMPLETE: 找到了 RAG 相关内容")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="RAG 相关文档")):
            result = ag._execute_task("搜索 RAG", "了解 RAG", [])

        assert result == "找到了 RAG 相关内容"
        assert call_count == 2

    def test_execute_tool_call_id_passed_in_message(self, tmp_path):
        ag = _make_agent(tmp_path, max_task_tool_rounds=1)
        collected: list[dict] = []

        def fake_chat(messages, tools=None, **kw):
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "tool":
                    collected.append(m)
            if not collected:
                return _tool_resp("search_knowledge", {"query": "q"}, call_id="cid_42")
            return _text_resp("TASK_COMPLETE: done")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="结果")):
            ag._execute_task("任务", "目标", [])

        assert any(m.get("tool_call_id") == "cid_42" for m in collected)

    def test_execute_tool_rounds_limit_disables_tools(self, tmp_path):
        ag = _make_agent(tmp_path, max_task_tool_rounds=1)
        tools_arg_list: list = []

        def fake_chat(messages, tools=None, **kw):
            tools_arg_list.append(tools)
            if tools is not None:
                return _tool_resp("search_knowledge", {"query": "q"})
            return _text_resp("TASK_COMPLETE: 被迫回答")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="结果")):
            ag._execute_task("任务", "目标", [])

        # 最后一次 chat 调用时 tools 应为 None
        assert tools_arg_list[-1] is None

    def test_execute_timeout_fallback_message(self, tmp_path):
        """所有迭代均调用工具但不输出 TASK_COMPLETE，返回超时兜底文本。"""
        ag = _make_agent(tmp_path, max_task_tool_rounds=1)

        def fake_chat(messages, tools=None, **kw):
            # 无论 tools 是否为 None，都返回 tool_call
            return _tool_resp("search_knowledge", {"query": "q"})

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="结果")):
            result = ag._execute_task("不可完成的任务", "目标", [])

        assert "未能在" in result or "子任务" in result

    def test_execute_prior_results_injected_in_system(self, tmp_path):
        ag = _make_agent(tmp_path, max_task_tool_rounds=1)
        system_msgs: list[str] = []

        def fake_chat(messages, tools=None, **kw):
            system_msgs.append(messages[0]["content"])
            return _text_resp("TASK_COMPLETE: done")

        prior = [("前一个任务", "前一个结果")]
        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._execute_task("当前任务", "目标", prior)

        assert "前一个任务" in system_msgs[0]
        assert "前一个结果" in system_msgs[0]


# ── Review 阶段 ───────────────────────────────────────────────────────────────

class TestAutoGPTReview:
    def test_review_returns_llm_content(self, tmp_path):
        ag = _make_agent(tmp_path)
        task_results = [("搜索知识库", "找到了 RAG 文档"), ("抓取网页", "网页内容 xyz")]
        with patch("src.agent.autogpt_agent.chat",
                   return_value=_text_resp("综合回答：RAG 是检索增强生成技术")):
            result = ag._review("什么是 RAG？", task_results)
        assert result == "综合回答：RAG 是检索增强生成技术"

    def test_review_includes_task_results_in_prompt(self, tmp_path):
        ag = _make_agent(tmp_path)
        captured: list[list] = []

        def fake_chat(messages, **kw):
            captured.append(messages)
            return _text_resp("回答")

        task_results = [("任务A", "结果A"), ("任务B", "结果B")]
        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._review("目标", task_results)

        user_content = captured[0][1]["content"]
        assert "任务A" in user_content and "结果A" in user_content
        assert "任务B" in user_content and "结果B" in user_content

    def test_review_fallback_on_empty_llm_response(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat", return_value=_text_resp("")):
            result = ag._review("目标", [])
        assert "抱歉" in result

    def test_review_system_prompt_used_in_messages(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(
            system_prompt="自定义系统提示",
            chat_history=ch,
            verbose=False,
        )
        captured: list[list] = []

        def fake_chat(messages, **kw):
            captured.append(messages)
            return _text_resp("回答")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._review("目标", [])

        # Review 阶段的 system 消息应源自 agent 的 _system_prompt
        assert captured[0][0]["role"] == "system"
        assert "自定义系统提示" in captured[0][0]["content"]


# ── run() 全流程 ──────────────────────────────────────────────────────────────

class TestAutoGPTRun:
    def test_run_returns_string(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat") as mock_chat:
            mock_chat.side_effect = [
                _plan_resp(["搜索知识库"]),          # Phase 1: plan
                _text_resp("TASK_COMPLETE: 找到了"),  # Phase 2: execute
                _text_resp("最终综合回答"),            # Phase 3: review
            ]
            result = ag.run("什么是 RAG？")
        assert isinstance(result, str)
        assert result == "最终综合回答"

    def test_run_calls_phases_in_order(self, tmp_path):
        ag = _make_agent(tmp_path)
        call_order: list[str] = []

        original_plan = ag._plan
        original_execute = ag._execute_task
        original_review = ag._review

        def fake_plan(*a, **kw):
            call_order.append("plan")
            return ["任务1"]

        def fake_execute(*a, **kw):
            call_order.append("execute")
            return "执行结果"

        def fake_review(*a, **kw):
            call_order.append("review")
            return "最终回答"

        ag._plan = fake_plan
        ag._execute_task = fake_execute
        ag._review = fake_review

        result = ag.run("目标")
        assert call_order == ["plan", "execute", "review"]
        assert result == "最终回答"

    def test_run_executes_all_tasks(self, tmp_path):
        ag = _make_agent(tmp_path)
        executed: list[str] = []

        ag._plan = lambda *a, **kw: ["任务A", "任务B", "任务C"]
        original_execute = ag._execute_task.__func__ if hasattr(ag._execute_task, '__func__') else None

        def fake_execute(task, goal, prior):
            executed.append(task)
            return f"结果_{task}"

        ag._execute_task = fake_execute
        ag._review = lambda goal, results: "最终"

        ag.run("目标")
        assert executed == ["任务A", "任务B", "任务C"]

    def test_run_passes_accumulated_results_to_review(self, tmp_path):
        ag = _make_agent(tmp_path)
        ag._plan = lambda *a, **kw: ["t1", "t2"]
        ag._execute_task = lambda task, goal, prior: f"done_{task}"
        review_args: list = []

        def fake_review(goal, task_results):
            review_args.append(task_results)
            return "最终"

        ag._review = fake_review
        ag.run("目标")

        # review 应收到两个 (task, result) 对
        assert len(review_args[0]) == 2
        assert review_args[0][0] == ("t1", "done_t1")
        assert review_args[0][1] == ("t2", "done_t2")

    def test_run_persists_user_and_assistant_to_db(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(
            session_id="persist-test",
            chat_history=ch,
            verbose=False,
        )
        ag._plan = lambda *a, **kw: ["task1"]
        ag._execute_task = lambda *a, **kw: "exec_result"
        ag._review = lambda *a, **kw: "最终回答"

        ag.run("用户输入")

        msgs = [m for m in ch.load("persist-test") if m["role"] in ("user", "assistant")]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "用户输入"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "最终回答"

    def test_run_does_not_persist_tool_messages(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(session_id="tool-check", chat_history=ch, verbose=False)
        ag._plan = lambda *a, **kw: ["task"]
        ag._execute_task = lambda *a, **kw: "exec"
        ag._review = lambda *a, **kw: "最终"

        ag.run("问题")

        all_msgs = ch.load("tool-check")
        roles = [m["role"] for m in all_msgs]
        assert "tool" not in roles


# ── 公共层接入（B-2 / B-4 / B-6 / B-8）────────────────────────────────────────

class TestAutoGPTCommonLayer:
    """验证 AutoGPT 复用公共层 helper 的接线：ToolCallEngine / 四层 system / 事件 / 流式。"""

    def test_execute_uses_tool_call_engine_no_db_pollution(self, tmp_path):
        """B-2/B-5：Execute 子循环经 ToolCallEngine 跑工具，但中间 tool/assistant
        消息只进内存临时历史，真实 DB 仅留 user + 最终 assistant。"""
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(
            session_id="no-pollute",
            chat_history=ch,
            verbose=False,
            max_plan_tasks=1,
            max_task_tool_rounds=2,
        )
        responses = [
            _plan_resp(["搜索资料"]),                        # Plan
            _tool_resp("search_knowledge", {"query": "q"}),   # Execute iter1: 工具
            _text_resp("TASK_COMPLETE: 已找到"),              # Execute iter2: 完成
            _text_resp("最终综合回答"),                        # Review
        ]
        with patch("src.agent.autogpt_agent.chat", side_effect=responses), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="检索结果")):
            result = ag.run("帮我查资料")

        assert result == "最终综合回答"
        all_msgs = ch.load("no-pollute")
        roles = [m["role"] for m in all_msgs]
        assert "tool" not in roles                       # 中间 tool 消息未落库
        assert roles == ["user", "assistant"]            # 只剩 user + 最终 assistant
        assert all_msgs[-1]["content"] == "最终综合回答"

    def test_inner_make_plan_emits_plan_created_event(self, tmp_path):
        """B-6：子循环内 LLM 调 make_plan 时，经 ToolCallEngine 自动 emit plan_created。"""
        ag = _make_agent(tmp_path, max_plan_tasks=1, max_task_tool_rounds=2)
        events: list = []
        ag.set_event_callback(lambda ev: events.append(ev))

        responses = [
            _plan_resp(["规划并执行"]),                                  # Plan
            _tool_resp("make_plan", {"steps": ["第一步", "第二步"]}),     # Execute: make_plan
            _text_resp("TASK_COMPLETE: 完成"),                          # Execute: 完成
            _text_resp("最终回答"),                                      # Review
        ]
        with patch("src.agent.autogpt_agent.chat", side_effect=responses), \
             patch("src.agent.core.tool_call_engine.execute_tool",
                   return_value=ToolResult(status="ok", content="已记录 plan")):
            ag.run("做个计划")

        assert any(ev.type == EVENT_PLAN_CREATED for ev in events)

    def test_review_four_layer_injects_user_context(self, tmp_path):
        """B-4：Review system 经 MemoryManager 注入 <user_context>。"""
        class _FakeMem:
            def load_for_context(self, _max_chars):
                return "用户偏好：回答尽量简洁"

        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(chat_history=ch, user_memory=_FakeMem(), verbose=False)
        captured: list = []

        def fake_chat(messages, **kw):
            captured.append(messages)
            return _text_resp("回答")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._review("目标", [])

        system_content = captured[0][0]["content"]
        assert "<user_context>" in system_content
        assert "用户偏好：回答尽量简洁" in system_content

    def test_review_passes_token_callback_when_subscriber_present(self, tmp_path):
        """B-8：Review 有 token 订阅者时，向 LLM 传 on_token_chunk（流式）。"""
        ag = _make_agent(tmp_path)
        ag.events.subscribe(EVENT_TOKEN_CHUNK, lambda _p: None)
        captured_kwargs: list = []

        def fake_chat(messages, tools=None, on_token_chunk=None, **kw):
            captured_kwargs.append(on_token_chunk)
            return _text_resp("回答")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._review("目标", [])

        assert captured_kwargs and captured_kwargs[-1] is not None

    def test_review_no_token_callback_without_subscriber(self, tmp_path):
        """无 token 订阅者时不传 on_token_chunk（零副作用，保护 mock 单测）。"""
        ag = _make_agent(tmp_path)
        captured_kwargs: list = []

        def fake_chat(messages, tools=None, on_token_chunk=None, **kw):
            captured_kwargs.append(on_token_chunk)
            return _text_resp("回答")

        with patch("src.agent.autogpt_agent.chat", side_effect=fake_chat):
            ag._review("目标", [])

        assert captured_kwargs and captured_kwargs[-1] is None


# ── last_usage / token 统计 ───────────────────────────────────────────────────

class TestAutoGPTTokenUsage:
    def test_last_usage_set_after_run_with_usage(self, tmp_path):
        ag = _make_agent(tmp_path)
        # 先构建 response 列表并设置 usage，再传给 side_effect（避免提前消耗迭代器）
        responses = [
            _plan_resp(["t1"]),
            _text_resp("TASK_COMPLETE: done"),
            _text_resp("综合回答"),
        ]
        for resp in responses:
            resp.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)

        with patch("src.agent.autogpt_agent.chat") as mock_chat:
            mock_chat.side_effect = responses
            ag.run("测试")

        # 3 次 LLM 调用，各 100 prompt + 20 completion
        assert ag.last_usage is not None
        assert ag.last_usage.prompt_tokens == 300
        assert ag.last_usage.completion_tokens == 60
        assert ag.last_usage.total_tokens == 360

    def test_last_usage_none_when_no_usage_attribute(self, tmp_path):
        ag = _make_agent(tmp_path)
        with patch("src.agent.autogpt_agent.chat") as mock_chat:
            mock_chat.side_effect = [
                _plan_resp(["t1"]),
                _text_resp("TASK_COMPLETE: done"),
                _text_resp("综合回答"),
            ]
            ag.run("测试")
        assert ag.last_usage is None

    def test_last_usage_accumulates_across_phases(self, tmp_path):
        ag = _make_agent(tmp_path)
        responses = [
            _plan_resp(["t1"]),
            _text_resp("TASK_COMPLETE: done"),
            _text_resp("综合回答"),
        ]
        usages = [(50, 10), (30, 5), (20, 8)]
        for resp, (p, c) in zip(responses, usages):
            resp.usage = SimpleNamespace(prompt_tokens=p, completion_tokens=c)

        with patch("src.agent.autogpt_agent.chat", side_effect=responses):
            ag.run("测试")

        assert ag.last_usage.prompt_tokens == 100    # 50+30+20
        assert ag.last_usage.completion_tokens == 23  # 10+5+8
        assert ag.last_usage.total_tokens == 123


# ── activate_skill ────────────────────────────────────────────────────────────

class TestAutoGPTActivateSkill:
    def test_activate_injects_tag_into_system_prompt(self, tmp_path):
        ag = _make_agent(tmp_path)
        result = ag.activate_skill("writer", "写作规范内容")
        assert result is True
        assert '<skill_content name="writer">' in ag._system_prompt
        assert "写作规范内容" in ag._system_prompt

    def test_activate_removes_from_skill_bodies(self, tmp_path):
        skill = _make_skill_info("writer", "写作助手", "正文")
        ag = _make_agent(tmp_path, skills={"writer": skill})
        assert "writer" in ag._skill_bodies
        ag.activate_skill("writer", "正文")
        assert "writer" not in ag._skill_bodies

    def test_activate_idempotent_returns_false(self, tmp_path):
        ag = _make_agent(tmp_path)
        assert ag.activate_skill("sk", "body") is True
        count = ag._system_prompt.count('<skill_content name="sk">')
        assert ag.activate_skill("sk", "body") is False
        assert ag._system_prompt.count('<skill_content name="sk">') == count

    def test_activate_unknown_skill_still_injects(self, tmp_path):
        ag = _make_agent(tmp_path)
        result = ag.activate_skill("unknown_skill", "新内容")
        assert result is True
        assert '<skill_content name="unknown_skill">' in ag._system_prompt


# ── 历史摘要 ──────────────────────────────────────────────────────────────────

class TestAutoGPTHistorySummary:
    def test_empty_history_returns_empty_summary(self, tmp_path):
        ag = _make_agent(tmp_path)
        summary = ag._build_history_summary()
        assert summary == ""

    def test_history_summary_includes_user_and_assistant(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(session_id="hist-test", chat_history=ch, verbose=False)
        ch.append("hist-test", {"role": "user", "content": "你好"})
        ch.append("hist-test", {"role": "assistant", "content": "你好！"})

        summary = ag._build_history_summary()
        assert "你好" in summary
        assert "用户" in summary
        assert "Agent" in summary

    def test_history_summary_excludes_tool_messages(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "ag.db"))
        ag = AutoGPTAgent(session_id="hist-tool", chat_history=ch, verbose=False)
        ch.append("hist-tool", {"role": "user", "content": "问题"})
        ch.append("hist-tool", {"role": "tool", "tool_call_id": "x", "content": "工具结果"})
        ch.append("hist-tool", {"role": "assistant", "content": "回答"})

        summary = ag._build_history_summary()
        assert "工具结果" not in summary


# ── make_agent 工厂：三种实现 ─────────────────────────────────────────────────

class TestMakeAgentFactory:
    """验证 handlers.make_agent() 根据 IMP_METHOD 返回正确的 Agent 类型。"""

    @staticmethod
    def _build_factory_args(tmp_path):
        from src.agent.agent import ThinkingConfig
        ch = ChatHistoryStore(db_path=str(tmp_path / "factory.db"))
        return dict(
            chat_history=ch,
            skills_map={},
            thinking_cfg=ThinkingConfig(),
            system_prompt=SYSTEM_PROMPT,
            session_id="factory-session",
            user_memory=None,
        )

    def test_make_agent_python(self, tmp_path):
        from src.cli.handlers import make_agent
        from src.agent.agent import Agent
        with patch("src.config.IMP_METHOD", "PYTHON"):
            import src.config as cfg
            cfg.IMP_METHOD = "PYTHON"
            ag = make_agent(**self._build_factory_args(tmp_path))
        assert isinstance(ag, Agent)

    def test_make_agent_autogpt(self, tmp_path):
        from src.cli.handlers import make_agent
        import src.config as cfg
        cfg.IMP_METHOD = "AUTOGPT"
        try:
            ag = make_agent(**self._build_factory_args(tmp_path))
            assert isinstance(ag, AutoGPTAgent)
        finally:
            cfg.IMP_METHOD = "PYTHON"

    def test_make_agent_langchain(self, tmp_path):
        import src.agent.langchain_agent  # ensure module is importable before patching
        from src.cli.handlers import make_agent
        from src.agent.langchain_agent import LangChainAgent
        import src.config as cfg
        orig = cfg.IMP_METHOD
        cfg.IMP_METHOD = "LANGCHAIN"
        try:
            with patch("src.agent.langchain_agent.build_chat_model", return_value=MagicMock()), \
                 patch("src.agent.langchain_agent.build_langchain_tools", return_value=[]), \
                 patch("src.agent.langchain_agent.get_shared_chat_history", return_value=MagicMock()):
                ag = make_agent(**self._build_factory_args(tmp_path))
            assert isinstance(ag, LangChainAgent)
        finally:
            cfg.IMP_METHOD = orig

    def test_make_agent_autogpt_interface(self, tmp_path):
        """AutoGPTAgent 应暴露与其他实现相同的 duck-typed 接口属性。"""
        from src.cli.handlers import make_agent
        import src.config as cfg
        cfg.IMP_METHOD = "AUTOGPT"
        try:
            ag = make_agent(**self._build_factory_args(tmp_path))
            assert hasattr(ag, "run")
            assert hasattr(ag, "session_id")
            assert hasattr(ag, "activate_skill")
            assert hasattr(ag, "last_usage")
            assert hasattr(ag, "verbose")
            assert hasattr(ag, "thinking_cfg")
        finally:
            cfg.IMP_METHOD = "PYTHON"

    def test_make_agent_python_interface(self, tmp_path):
        """Agent (PYTHON) 应暴露相同的 duck-typed 接口属性。"""
        from src.cli.handlers import make_agent
        import src.config as cfg
        orig = cfg.IMP_METHOD
        cfg.IMP_METHOD = "PYTHON"
        try:
            ag = make_agent(**self._build_factory_args(tmp_path))
            assert hasattr(ag, "run")
            assert hasattr(ag, "session_id")
            assert hasattr(ag, "activate_skill")
            assert hasattr(ag, "last_usage")
            assert hasattr(ag, "verbose")
            assert hasattr(ag, "thinking_cfg")
        finally:
            cfg.IMP_METHOD = orig

    def test_make_agent_langchain_interface(self, tmp_path):
        """LangChainAgent 应暴露相同的 duck-typed 接口属性。"""
        import src.agent.langchain_agent  # ensure module is importable before patching
        from src.cli.handlers import make_agent
        import src.config as cfg
        orig = cfg.IMP_METHOD
        cfg.IMP_METHOD = "LANGCHAIN"
        try:
            with patch("src.agent.langchain_agent.build_chat_model", return_value=MagicMock()), \
                 patch("src.agent.langchain_agent.build_langchain_tools", return_value=[]), \
                 patch("src.agent.langchain_agent.get_shared_chat_history", return_value=MagicMock()):
                ag = make_agent(**self._build_factory_args(tmp_path))
            assert hasattr(ag, "run")
            assert hasattr(ag, "session_id")
            assert hasattr(ag, "activate_skill")
            assert hasattr(ag, "last_usage")
            assert hasattr(ag, "verbose")
            assert hasattr(ag, "thinking_cfg")
        finally:
            cfg.IMP_METHOD = orig


# ── 集成测试 ──────────────────────────────────────────────────────────────────

class TestAutoGPTIntegration:
    """端到端真实 API 测试（消耗真实 API quota）"""

    @pytest.mark.integration
    def test_run_returns_nonempty_string(self, tmp_path):
        ag = _make_agent(tmp_path, max_plan_tasks=2, max_task_tool_rounds=2)
        result = ag.run("你好，请介绍一下 RAG 技术的核心原理")
        assert isinstance(result, str)
        assert len(result) > 20

    @pytest.mark.integration
    def test_run_persists_to_db(self, tmp_path):
        ch = ChatHistoryStore(db_path=str(tmp_path / "int.db"))
        ag = AutoGPTAgent(
            session_id="int-test",
            chat_history=ch,
            verbose=True,
            max_plan_tasks=1,
            max_task_tool_rounds=2,
        )
        result = ag.run("什么是向量数据库？")
        assert isinstance(result, str)
        msgs = [m for m in ch.load("int-test") if m["role"] in ("user", "assistant")]
        assert len(msgs) == 2

    @pytest.mark.integration
    def test_run_three_phases_all_produce_output(self, tmp_path):
        """验证三阶段均正常工作（通过 verbose 日志间接验证任务被分解和执行）。"""
        ag = _make_agent(
            tmp_path,
            max_plan_tasks=2,
            max_task_tool_rounds=2,
            verbose=True,
        )
        result = ag.run("比较 RAG 和 Fine-tuning 的适用场景")
        assert isinstance(result, str)
        assert len(result) > 10
