"""
Phase 4 测试：工具层

测试内容：
    - TOOLS 列表结构是否符合 OpenAI Function Calling 格式
    - execute_tool() 路由逻辑
    - _tool_search_knowledge() 调用 RAG 检索
    - _tool_fetch_url() 网页抓取（含错误处理）
"""

import pytest
from agent.tools import TOOLS, execute_tool


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

    def test_unknown_tool_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="未知工具"):
            execute_tool("nonexistent_tool", {})

    @pytest.mark.integration
    def test_search_knowledge_returns_string(self) -> None:
        result = execute_tool("search_knowledge", {"query": "RAG 技术", "top_k": 2})
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.integration
    def test_search_knowledge_default_top_k(self) -> None:
        """top_k 未传时应使用默认值 5，不抛出异常"""
        result = execute_tool("search_knowledge", {"query": "向量数据库"})
        assert isinstance(result, str)


class TestFetchUrl:
    """测试 fetch_url 工具"""

    def test_invalid_url_scheme_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "ftp://example.com"})
        assert "错误" in result
        assert "http" in result

    def test_invalid_url_no_scheme_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "example.com"})
        assert "错误" in result

    @pytest.mark.integration
    def test_fetch_valid_url_returns_text(self) -> None:
        """抓取一个稳定的公开页面，验证返回非空文本"""
        result = execute_tool("fetch_url", {"url": "https://httpbin.org/html", "max_chars": 500})
        assert isinstance(result, str)
        assert len(result) > 0
        # httpbin 返回 Herman Melville 的段落，不含 script 标签
        assert "<script>" not in result

    @pytest.mark.integration
    def test_fetch_url_respects_max_chars(self) -> None:
        """返回内容不应超过 max_chars + 截断提示的长度"""
        max_chars = 200
        result = execute_tool("fetch_url", {"url": "https://httpbin.org/html", "max_chars": max_chars})
        # 截断后的内容 ≤ max_chars + 截断提示（约 30 字符）
        assert len(result) <= max_chars + 60

    @pytest.mark.integration
    def test_fetch_nonexistent_url_returns_error(self) -> None:
        result = execute_tool("fetch_url", {"url": "https://this-domain-does-not-exist-xyz123.com"})
        assert "错误" in result
