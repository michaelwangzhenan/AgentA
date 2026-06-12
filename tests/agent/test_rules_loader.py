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

    def test_wraps_in_user_rules_tag(self) -> None:
        block = build_rules_block("不要用 bullet")
        assert block.startswith("\n\n<user_rules>")
        assert block.endswith("</user_rules>")
        assert "不要用 bullet" in block

    def test_framed_as_user_preference_to_obey(self) -> None:
        """块内必须把 rules 框定为用户偏好并要求遵守（rules 是可信内容，不做防注入清洗）。"""
        block = build_rules_block("rules body")
        assert "偏好" in block
        assert "遵守" in block
