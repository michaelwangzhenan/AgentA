"""fetch_url 响应体字节上限。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.config as config
from src.agent import tools


def _mock_response(
    *,
    content: bytes = b"<html><body>hello</body></html>",
    content_length: str | None = None,
    content_type: str = "text/html",
) -> MagicMock:
    resp = MagicMock()
    resp.headers = {"Content-Type": content_type}
    if content_length is not None:
        resp.headers["Content-Length"] = content_length
    resp.url = "https://example.com/page"
    resp.iter_content.return_value = [content]
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_rejects_content_length_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_FETCH_BYTES", 100)
    with patch("src.agent.tools.requests.get", return_value=_mock_response(content_length="200")):
        result = tools._fetch_raw_response("https://example.com/page")
    assert isinstance(result, tools.ToolResult)
    assert result.status == "error"
    assert "Content-Length" in result.content


def test_fetch_aborts_stream_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_FETCH_BYTES", 50)

    def _iter():
        yield b"x" * 40
        yield b"y" * 20

    resp = _mock_response(content=b"")
    resp.iter_content.return_value = _iter()
    with patch("src.agent.tools.requests.get", return_value=resp):
        result = tools._fetch_raw_response("https://example.com/page")
    assert isinstance(result, tools.ToolResult)
    assert result.status == "error"
    assert "下载上限" in result.content


def test_jina_fetch_uses_same_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_FETCH_BYTES", 10)
    resp = _mock_response(content=b"01234567890", content_type="text/plain")
    with patch("src.agent.tools.requests.get", return_value=resp):
        result = tools._fetch_via_jina("https://example.com", max_chars=100)
    assert isinstance(result, tools.ToolResult)
    assert result.status == "error"
