"""
测试：工具层

测试内容：
    - TOOLS 列表结构是否符合 OpenAI Function Calling 格式
    - execute_tool() 路由逻辑
    - _tool_search_knowledge() 调用 RAG 检索
    - _tool_fetch_url() 网页抓取（含错误处理）
"""

import json

import pytest
from src.agent.tools import TOOLS, ToolResult, execute_tool, get_tools


class TestToolsSchema:
    """测试 TOOLS 列表的 JSON Schema 格式是否合法"""

    def test_tools_is_nonempty_list(self) -> None:
        assert isinstance(TOOLS, list)
        assert len(TOOLS) >= 2

    def test_each_tool_has_required_fields(self) -> None:
        for tool in TOOLS:
            assert tool.get("type") == "function", f"工具缺少 type=function: {tool}"
            func = tool.get("function", {})
            assert "name" in func, f"工具缺少 name: {tool}"
            assert "description" in func, f"工具缺少 description: {tool}"
            assert "parameters" in func, f"工具缺少 parameters: {tool}"

    def test_tool_names_are_expected(self) -> None:
        names = {t["function"]["name"] for t in TOOLS}
        assert "search_knowledge" in names
        assert "fetch_url" in names

    def test_search_knowledge_required_params(self) -> None:
        tool = next(t for t in TOOLS if t["function"]["name"] == "search_knowledge")
        params = tool["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params["required"]

    def test_fetch_url_required_params(self) -> None:
        tool = next(t for t in TOOLS if t["function"]["name"] == "fetch_url")
        params = tool["function"]["parameters"]
        assert "url" in params["properties"]
        assert "url" in params["required"]


class TestExecuteToolRouting:
    """测试 execute_tool() 路由逻辑"""

    def test_unknown_tool_returns_error_result(self) -> None:
        """未知工具名应返回 status='error' 的 ToolResult，不抛出异常"""
        result = execute_tool("nonexistent_tool", {})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "未知工具" in result.content

    @pytest.mark.integration
    def test_search_knowledge_returns_tool_result(self) -> None:
        result = execute_tool("search_knowledge", {"query": "RAG 技术", "top_k": 2})
        assert isinstance(result, ToolResult)
        assert result.status in ("ok", "empty")
        assert len(result.content) > 0

    @pytest.mark.integration
    def test_search_knowledge_default_top_k(self) -> None:
        """top_k 未传时应使用默认值 5，不抛出异常"""
        result = execute_tool("search_knowledge", {"query": "向量数据库"})
        assert isinstance(result, ToolResult)


class TestFetchUrl:
    """测试 fetch_url 工具"""

    def test_invalid_url_scheme_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "ftp://example.com"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "http" in result.content

    def test_invalid_url_no_scheme_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "example.com"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    @pytest.mark.integration
    def test_fetch_valid_url_returns_ok(self) -> None:
        """抓取一个稳定的公开页面，验证返回非空文本"""
        result = execute_tool("fetch_url", {"url": "https://httpbin.org/html", "max_chars": 500})
        assert isinstance(result, ToolResult)
        assert result.status == "ok"
        assert len(result.content) > 0
        assert "<script>" not in result.content

    @pytest.mark.integration
    def test_fetch_url_respects_max_chars(self) -> None:
        """返回内容不应超过 max_chars + 截断提示的长度"""
        max_chars = 200
        result = execute_tool("fetch_url", {"url": "https://httpbin.org/html", "max_chars": max_chars})
        assert len(result.content) <= max_chars + 60

    @pytest.mark.integration
    def test_fetch_nonexistent_url_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "https://this-domain-does-not-exist-xyz123.com"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"


# TestFetchUrlDescriptionGuidance 已删除：原断言 fetch_url description 内必须含
# "国内 / 国外 / baidu / zhihu / csdn ..." 等字串，属于脆性 string-match 测试，
# description 已精简，相关引导策略改由 SYSTEM_PROMPT + Agent 行为测覆盖。


# ── Phase 2.1 — Plan-Execute 三 tool ─────────────────────────────────────────


def _mk_assistant_tc(name: str, args: dict, call_id: str = "c1") -> dict:
    """构造一个 assistant 带单 tool_call 的 dict 形态 message（与 ToolCallEngine 写库格式一致）。"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


class TestPlanToolsSchema:
    """make_plan / update_step / abort_plan 三 tool 的 OpenAI Function Calling schema 合规。"""

    def test_get_tools_includes_three_plan_tools(self) -> None:
        names = {t["function"]["name"] for t in get_tools()}
        assert {"make_plan", "update_step", "abort_plan"} <= names

    def test_make_plan_steps_required(self) -> None:
        tool = next(t for t in get_tools() if t["function"]["name"] == "make_plan")
        params = tool["function"]["parameters"]
        assert "steps" in params["properties"]
        assert "steps" in params["required"]
        assert params["properties"]["steps"]["type"] == "array"

    def test_update_step_status_enum_locked(self) -> None:
        tool = next(t for t in get_tools() if t["function"]["name"] == "update_step")
        params = tool["function"]["parameters"]
        assert set(params["properties"]["status"]["enum"]) == {"success", "failed", "skipped"}
        assert set(params["required"]) == {"step_id", "status"}

    def test_abort_plan_no_required_args(self) -> None:
        tool = next(t for t in get_tools() if t["function"]["name"] == "abort_plan")
        params = tool["function"]["parameters"]
        assert params["required"] == []


class TestMakePlanExecute:
    def test_make_plan_valid_returns_ack_with_first_step(self) -> None:
        result = execute_tool("make_plan", {"steps": ["列项目", "对比", "总结"]})
        assert result.status == "ok"
        assert "已记录 plan" in result.content
        assert "共 3 步" in result.content
        assert "下一步：第 1 步" in result.content
        assert "列项目" in result.content

    def test_make_plan_empty_steps_returns_error(self) -> None:
        result = execute_tool("make_plan", {"steps": []})
        assert result.status == "error"
        assert "非空" in result.content

    def test_make_plan_non_list_returns_error(self) -> None:
        result = execute_tool("make_plan", {"steps": "not-a-list"})
        assert result.status == "error"

    def test_make_plan_non_str_element_returns_error(self) -> None:
        result = execute_tool("make_plan", {"steps": [1, 2, 3]})
        assert result.status == "error"

    def test_make_plan_whitespace_element_returns_error(self) -> None:
        result = execute_tool("make_plan", {"steps": ["", "   "]})
        assert result.status == "error"


class TestUpdateStepExecute:
    """update_step 需要 active plan；测试要构造含 make_plan 的 messages 历史透传。"""

    def _msgs_with_plan(self, steps: list[str]) -> list[dict]:
        return [
            {"role": "user", "content": "do something"},
            _mk_assistant_tc("make_plan", {"steps": steps}, call_id="mp1"),
            {"role": "tool", "tool_call_id": "mp1", "content": "plan 已记录..."},
        ]

    def test_update_step_success_returns_next_step_hint(self) -> None:
        msgs = self._msgs_with_plan(["a", "b", "c"])
        # 模拟本轮 LLM 调 update_step：messages 末尾追加本次 assistant tool_calls
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 1, "status": "success"}, call_id="u1"))
        result = execute_tool(
            "update_step",
            {"step_id": 1, "status": "success", "note": "找到 3 个"},
            messages=msgs,
        )
        assert result.status == "ok"
        assert "已创建" in result.content or "[完成]" in result.content
        assert "step 1" in result.content
        assert "找到 3 个" in result.content
        assert "1/3" in result.content
        assert "下一步：第 2 步" in result.content
        assert "b" in result.content

    def test_update_step_completes_plan(self) -> None:
        msgs = self._msgs_with_plan(["a", "b"])
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 1, "status": "success"}, call_id="u1"))
        msgs.append({"role": "tool", "tool_call_id": "u1", "content": "..."})
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 2, "status": "success"}, call_id="u2"))
        result = execute_tool(
            "update_step", {"step_id": 2, "status": "success"}, messages=msgs,
        )
        assert result.status == "ok"
        assert "plan 已完成" in result.content
        assert "2/2" in result.content
        assert "总结最终答案" in result.content

    def test_update_step_failed_with_note(self) -> None:
        msgs = self._msgs_with_plan(["a", "b"])
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 1, "status": "failed"}, call_id="u1"))
        result = execute_tool(
            "update_step",
            {"step_id": 1, "status": "failed", "note": "503 错误"},
            messages=msgs,
        )
        assert result.status == "ok"
        assert "[失败]" in result.content
        assert "503 错误" in result.content

    def test_update_step_skipped(self) -> None:
        msgs = self._msgs_with_plan(["a", "b"])
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 1, "status": "skipped"}, call_id="u1"))
        result = execute_tool(
            "update_step", {"step_id": 1, "status": "skipped"}, messages=msgs,
        )
        assert result.status == "ok"
        assert "[跳过]" in result.content
        assert "skipped" in result.content

    def test_update_step_no_active_plan_returns_error(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        result = execute_tool(
            "update_step", {"step_id": 1, "status": "success"}, messages=msgs,
        )
        assert result.status == "error"
        assert "active plan" in result.content
        assert "make_plan" in result.content

    def test_update_step_out_of_range_returns_error(self) -> None:
        msgs = self._msgs_with_plan(["a", "b"])
        msgs.append(_mk_assistant_tc("update_step", {"step_id": 99, "status": "success"}, call_id="u1"))
        result = execute_tool(
            "update_step", {"step_id": 99, "status": "success"}, messages=msgs,
        )
        assert result.status == "error"
        assert "不在当前 plan 范围内" in result.content

    def test_update_step_invalid_status_returns_error(self) -> None:
        msgs = self._msgs_with_plan(["a"])
        result = execute_tool(
            "update_step", {"step_id": 1, "status": "weird"}, messages=msgs,
        )
        assert result.status == "error"
        assert "success / failed / skipped" in result.content

    def test_update_step_non_int_step_id_returns_error(self) -> None:
        msgs = self._msgs_with_plan(["a"])
        result = execute_tool(
            "update_step", {"step_id": "1", "status": "success"}, messages=msgs,
        )
        assert result.status == "error"
        assert "≥1 整数" in result.content


class TestAbortPlanExecute:
    def test_abort_plan_with_reason(self) -> None:
        result = execute_tool("abort_plan", {"reason": "多次失败"})
        assert result.status == "ok"
        assert "plan 已中止" in result.content
        assert "多次失败" in result.content
        assert "总结" in result.content

    def test_abort_plan_no_reason(self) -> None:
        result = execute_tool("abort_plan", {})
        assert result.status == "ok"
        assert "plan 已中止" in result.content
        assert "总结" in result.content
