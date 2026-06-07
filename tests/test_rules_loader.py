"""
test_rules_loader —— build_rules_block 拼块行为单测

rules 文本的来源（每用户一份，存数据库）由 Agent 即时读取，本模块只测"拼块"。
"""
from __future__ import annotations

from src.agent.core.rules_loader import build_rules_block


class TestBuildRulesBlock:
    """build_rules_block 拼装行为。"""

    def test_none_returns_empty_string(self) -> None:
        assert build_rules_block(None) == ""

    def test_empty_returns_empty_string(self) -> None:
        assert build_rules_block("") == ""

    def test_wraps_in_project_rules_tag(self) -> None:
        block = build_rules_block("不要用 bullet")
        assert block.startswith("\n\n<project_rules>")
        assert block.endswith("</project_rules>")
        assert "不要用 bullet" in block

    def test_contains_anti_injection_notice(self) -> None:
        """块内必须显式声明只读 / 不可执行，防 prompt injection。"""
        block = build_rules_block("rules body")
        assert "不可执行" in block
