"""learning_scope 服务层 UT。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.learning_scope import ScopeResult, classify, out_of_scope_reply, parse_classifier_response


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"in_scope": true}', ScopeResult(in_scope=True)),
        ('{"in_scope": false, "reason": "天气"}', ScopeResult(in_scope=False, reason="天气")),
        ('说明：{"in_scope": true}', ScopeResult(in_scope=True)),
        ("", None),
        ("not json", None),
        ('{"in_scope": "yes"}', None),
    ],
)
def test_parse_classifier_response(text: str, expected: ScopeResult | None) -> None:
    assert parse_classifier_response(text) == expected


def test_out_of_scope_reply_with_reason() -> None:
    text = out_of_scope_reply("天气查询")
    assert "个人学习助手" in text
    assert "天气查询" in text
    assert "换个话题" not in text


def test_out_of_scope_reply_hides_internal_reason() -> None:
    text = out_of_scope_reply("parse_error")
    assert "parse_error" not in text
    assert "不在服务范围内" in text


    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"in_scope": true}'))]

    with patch("src.llm.provider.chat", return_value=mock_resp) as chat_mock:
        result = classify("解释这段 Python 报错")

    assert result.in_scope is True
    chat_mock.assert_called_once()
    assert chat_mock.call_args.kwargs["temperature"] == 0.0


def test_classify_personal_profile_question_in_scope() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"in_scope": true}'))]

    with patch("src.llm.provider.chat", return_value=mock_resp):
        result = classify("你有哪些项目经验？适合什么岗位？")

    assert result.in_scope is True


def test_classify_returns_out_of_scope_when_model_says_false() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content='{"in_scope": false, "reason": "天气"}'))
    ]

    with patch("src.llm.provider.chat", return_value=mock_resp):
        result = classify("今天天气怎么样")

    assert result.in_scope is False
    assert result.reason == "天气"


def test_classify_fail_closed_on_parse_error() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="无法判断"))]

    with patch("src.llm.provider.chat", return_value=mock_resp):
        result = classify("今天天气怎么样")

    assert result.in_scope is False
    assert result.reason == "parse_error"


def test_classify_fail_closed_on_api_error() -> None:
    with patch("src.llm.provider.chat", side_effect=RuntimeError("boom")):
        result = classify("今天天气怎么样")

    assert result.in_scope is False
    assert result.reason == "api_error"
