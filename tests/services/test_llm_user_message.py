"""llm_user_message 单元测试。"""

from __future__ import annotations

from src.services.llm_user_message import friendly_llm_error


def test_content_exists_risk() -> None:
    raw = (
        "Error code: 400 - {'error': {'message': 'Content Exists Risk', "
        "'type': 'invalid_request_error', 'param': None, 'code': 'invalid_request_error'}}"
    )
    assert friendly_llm_error(raw) == "尊敬的用户您好，让我们换个话题再聊聊吧。"


def test_insufficient_balance() -> None:
    assert "余额" in friendly_llm_error("Insufficient balance")
    assert "切换" in friendly_llm_error("账户余额不足")


def test_generic_does_not_leak_json() -> None:
    msg = friendly_llm_error(RuntimeError("something weird happened"))
    assert "{" not in msg
    assert "Error code" not in msg
