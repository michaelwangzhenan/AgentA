"""
Phase 3.2 tool 名单门集成 UT —— 锁定 get_tools 与 execute_tool 在 normal/strict
模式下的过滤行为。详 docs/iter_2_agent.md §4.9.12 D4 / D9。
"""
from unittest.mock import patch

from src.agent import tools


def _names(tool_list):
    return {t.get("function", {}).get("name") for t in tool_list}


class TestGetToolsBlocklist:
    """normal 模式下 get_tools() 按 TOOL_BLOCKLIST 过滤。"""

    def test_no_blocklist_returns_full_set(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", ""):
            names = _names(tools.get_tools())
        # 至少包含基础 3 tool + plan 3 tool
        assert {"search_knowledge", "web_search", "fetch_url"}.issubset(names)
        assert {"make_plan", "update_step", "abort_plan"}.issubset(names)

    def test_blocklist_filters_named_tools(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", "fetch_url,web_search"):
            names = _names(tools.get_tools())
        assert "fetch_url" not in names
        assert "web_search" not in names
        assert "search_knowledge" in names

    def test_blocklist_can_filter_plan_tools(self):
        """业务 tool（make_plan / update_step）也可被屏蔽 — 测试名单门覆盖范围。"""
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", "make_plan"):
            names = _names(tools.get_tools())
        assert "make_plan" not in names
        assert "search_knowledge" in names


class TestGetToolsAllowlist:
    """strict 模式下 get_tools() 按 TOOL_ALLOWLIST 仅放行白名单。"""

    def test_strict_empty_allowlist_returns_empty(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "strict"), \
             patch("src.agent.core.security_filter._cfg.TOOL_ALLOWLIST", ""):
            assert tools.get_tools() == []

    def test_strict_allowlist_keeps_only_listed(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "strict"), \
             patch("src.agent.core.security_filter._cfg.TOOL_ALLOWLIST", "search_knowledge,make_plan"):
            names = _names(tools.get_tools())
        assert names == {"search_knowledge", "make_plan"}


class TestGetToolsLoadSkill:
    """load_skill 与名单门的相互作用：默认 load_skill 走 BLOCKLIST 也能屏蔽。"""

    def test_load_skill_appended_normally(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", ""):
            names = _names(tools.get_tools(skill_bodies={"foo": "body"}))
        assert "load_skill" in names

    def test_load_skill_blocklisted(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", "load_skill"):
            names = _names(tools.get_tools(skill_bodies={"foo": "body"}))
        assert "load_skill" not in names


class TestExecuteToolDoubleCheck:
    """双层保险：execute_tool 入口对被屏蔽 tool 直接返 status=error 拒绝。"""

    def test_blocked_tool_returns_error(self):
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", "fetch_url"):
            result = tools.execute_tool("fetch_url", {"url": "https://example.com"})
        assert result.status == "error"
        assert "名单门" in result.content

    def test_allowed_tool_passes_double_check(self):
        """放行的 tool 不被 double-check 拦下（实际逻辑由具体 _tool_* 处理）。"""
        with patch("src.agent.core.security_filter._cfg.SECURITY_MODE", "normal"), \
             patch("src.agent.core.security_filter._cfg.TOOL_BLOCKLIST", ""):
            result = tools.execute_tool("make_plan", {"steps": ["a", "b"]})
        # make_plan 是无副作用工具，应顺利返回 status=ok
        assert result.status == "ok"
