"""Skill 注入治理：截断、会话引用、历史还原。"""

from __future__ import annotations

import pytest

import src.config as config
from src.agent.core.skill_loader import (
    format_skill_content,
    hydrate_skill_refs,
    skill_ref_stub,
    truncate_skill_body,
)


def test_truncate_skill_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SKILL_INJECT_MAX_CHARS", 20)
    out = truncate_skill_body("x" * 100)
    assert len(out) > 20
    assert "截断" in out


def test_format_skill_content_has_hash() -> None:
    block = format_skill_content("demo", "hello")
    assert 'name="demo"' in block
    assert 'hash="' in block
    assert "hello" in block


def test_hydrate_skill_refs_expands_stub() -> None:
    bodies = {"demo": "full body text"}
    msgs = [{"role": "tool", "content": skill_ref_stub("demo", bodies["demo"])}]
    out = hydrate_skill_refs(msgs, bodies)
    assert "<skill_content" in out[0]["content"]
    assert "full body text" in out[0]["content"]


def test_load_skill_db_stub_live_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_skill：落库 skill_ref，同轮 messages 仍含完整正文。"""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.agent.core.tool_call_engine import ToolCallEngine
    from src.agent.tools import ToolResult

    store = MagicMock()
    engine = ToolCallEngine(store, "s1", {"demo": "FULL_SKILL_BODY"})
    messages: list[dict] = []
    tool_call = SimpleNamespace(id="c1")
    result = ToolResult(
        status="ok",
        content='<skill_content name="demo">\nFULL_SKILL_BODY\n</skill_content>',
    )

    engine._consume_result(tool_call, "load_skill", {"name": "demo"}, result, messages)

    db_msg = store.append.call_args[0][1]
    assert "<skill_ref" in db_msg["content"]
    assert "FULL_SKILL_BODY" not in db_msg["content"]
    assert "<skill_content" in messages[0]["content"]
    assert "FULL_SKILL_BODY" in messages[0]["content"]
