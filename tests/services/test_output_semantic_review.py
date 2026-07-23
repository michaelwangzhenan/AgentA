"""output_semantic_review 服务层 UT。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.output_semantic_review import ReviewResult, parse_review_response, review


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"safe": true}', ReviewResult(safe=True)),
        (
            '{"safe": false, "category": "illegal", "reason": "危害"}',
            ReviewResult(safe=False, category="illegal", reason="危害"),
        ),
        ('说明：{"safe": true}', ReviewResult(safe=True)),
        ("", None),
        ("not json", None),
        ('{"safe": "yes"}', None),
    ],
)
def test_parse_review_response(text: str, expected: ReviewResult | None) -> None:
    assert parse_review_response(text) == expected


def test_review_always_calls_model_not_keyword_filter() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"safe": true}'))]

    with patch("src.llm.provider.chat", return_value=mock_resp) as chat_mock:
        result = review("你好", "包含任意措辞的回复")

    assert result.safe is True
    chat_mock.assert_called_once()
    system_prompt = chat_mock.call_args.args[0][0]["content"]
    assert "敏感词" in system_prompt
    assert "premise_error" in system_prompt


def test_review_returns_safe_when_model_says_true() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"safe": true}'))]

    with patch("src.llm.provider.chat", return_value=mock_resp) as chat_mock:
        result = review("解释 Python", "这是学习回答")

    assert result.safe is True
    chat_mock.assert_called_once()
    assert chat_mock.call_args.kwargs["temperature"] == 0.0


def test_review_fail_closed_on_parse_error() -> None:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="无法判断"))]

    with patch("src.llm.provider.chat", return_value=mock_resp):
        result = review("问题", "回答")

    assert result.safe is False
    assert result.reason == "parse_error"


def test_review_fail_closed_on_api_error() -> None:
    with patch("src.llm.provider.chat", side_effect=RuntimeError("boom")):
        result = review("问题", "回答")

    assert result.safe is False
    assert result.reason == "api_error"
