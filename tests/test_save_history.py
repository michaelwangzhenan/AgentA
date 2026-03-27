"""
测试：main._save_history() 对话导出函数

测试内容：
    - 空 session 不写文件，仅打印提示
    - 文件名规范化：去掉 .txt/.md 后缀，再追加 .md
    - 非法字符替换为 _，防止路径遍历
    - 文件写入正确的 markdown 内容
    - OSError 被捕获，不向上层抛出
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── 辅助：动态导入 main._save_history，避免 main 模块中的副作用 ───────────────────

def _get_save_history():
    """从 main 模块中直接取出 _save_history 函数引用。"""
    import main as m
    return m._save_history


# ── 辅助：构造 mock MemoryStore ──────────────────────────────────────────────

def _mock_memory(messages: list[dict]) -> MagicMock:
    """返回一个 load() 会返回 messages 的 mock MemoryStore。"""
    mem = MagicMock()
    mem.load.return_value = messages
    return mem


# ── 测试类 ────────────────────────────────────────────────────────────────────

class TestSaveHistoryEmpty:
    """空 session 时的行为"""

    def test_empty_session_prints_message(self, capsys) -> None:
        """load 返回空列表时，应打印提示，不创建任何文件。"""
        _save_history = _get_save_history()
        mem = _mock_memory([])
        _save_history(mem, "sess-1", "myfile")
        out = capsys.readouterr().out
        assert "暂无对话历史" in out

    def test_empty_session_no_file_created(self, tmp_path) -> None:
        """空 session 不应创建 history/ 目录下的任何 .md 文件。"""
        _save_history = _get_save_history()
        mem = _mock_memory([])
        with patch("main.Path", wraps=Path):
            _save_history(mem, "sess-1", "somefilename")
        # 确保当前工作目录中没有意外写入
        assert not list(tmp_path.glob("**/*.md"))


class TestSaveHistoryFilename:
    """文件名规范化规则"""

    def _run(self, filename: str, messages: list[dict] | None = None) -> str:
        """执行 _save_history，返回实际写入的文件路径（stem 部分）。"""
        _save_history = _get_save_history()
        if messages is None:
            messages = [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"},
            ]
        mem = _mock_memory(messages)
        captured_paths: list[Path] = []

        original_write_text = Path.write_text

        def mock_write_text(self, *args, **kwargs):
            captured_paths.append(self)

        with patch.object(Path, "write_text", mock_write_text), \
             patch.object(Path, "mkdir"):
            _save_history(mem, "sess-1", filename)

        return captured_paths[0].name if captured_paths else ""

    def test_md_suffix_stripped_and_readded(self) -> None:
        """传入 foo.md 应写入 foo.md（不双重追加）。"""
        name = self._run("foo.md")
        assert name == "foo.md"

    def test_txt_suffix_stripped_then_md_added(self) -> None:
        """传入 foo.txt 应写入 foo.md。"""
        name = self._run("foo.txt")
        assert name == "foo.md"

    def test_no_suffix_adds_md(self) -> None:
        """无后缀的文件名应自动加 .md。"""
        name = self._run("mysession")
        assert name == "mysession.md"

    def test_special_chars_replaced(self) -> None:
        """含特殊字符的文件名非法字符应被替换为 _。"""
        name = self._run("my session/2024")
        # 空格和 / 都应被 _ 替换
        assert " " not in name
        assert "/" not in name

    def test_path_traversal_blocked(self) -> None:
        """包含 ../ 的路径遍历名称应被清洗，不包含 .. 或 /。"""
        name = self._run("../../etc/passwd")
        assert ".." not in name
        assert "/" not in name

    def test_invalid_name_prints_error(self, capsys) -> None:
        """空名称或以 . 开头的名称应打印错误，不写文件。"""
        _save_history = _get_save_history()
        mem = _mock_memory([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "ok"}])
        # 传入纯点（清洗后 safe_name 为空或以 . 开头）
        _save_history(mem, "sess-1", ".hidden")
        out = capsys.readouterr().out
        assert "无效文件名" in out


class TestSaveHistoryContent:
    """写入文件的内容格式"""

    def test_markdown_contains_session_id(self, tmp_path) -> None:
        """导出的 markdown 应包含 session id。"""
        _save_history = _get_save_history()
        msgs = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        mem = _mock_memory(msgs)
        history_dir = tmp_path / "history"
        history_dir.mkdir()

        written: list[str] = []

        def fake_write_text(self2, text, encoding=None):
            written.append(text)

        with patch.object(Path, "write_text", fake_write_text), \
             patch.object(Path, "mkdir"):
            _save_history(mem, "my-session-id", "output")

        assert written, "write_text 应被调用一次"
        assert "my-session-id" in written[0]

    def test_markdown_contains_user_and_agent_labels(self) -> None:
        """导出的 markdown 应含 '你' 和 'Agent' 标签。"""
        _save_history = _get_save_history()
        msgs = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        mem = _mock_memory(msgs)
        written: list[str] = []

        def fake_write_text(self2, text, encoding=None):
            written.append(text)

        with patch.object(Path, "write_text", fake_write_text), \
             patch.object(Path, "mkdir"):
            _save_history(mem, "sess", "test_output")

        assert "## 你" in written[0]
        assert "## Agent" in written[0]

    def test_system_messages_excluded(self) -> None:
        """system 角色的消息不应出现在导出文件中。"""
        _save_history = _get_save_history()
        msgs = [
            {"role": "system", "content": "系统提示，不应导出"},
            {"role": "user", "content": "用户问题"},
            {"role": "assistant", "content": "回答"},
        ]
        mem = _mock_memory(msgs)
        written: list[str] = []

        def fake_write_text(self2, text, encoding=None):
            written.append(text)

        with patch.object(Path, "write_text", fake_write_text), \
             patch.object(Path, "mkdir"):
            _save_history(mem, "sess", "out")

        assert "系统提示，不应导出" not in written[0]


class TestSaveHistoryOSError:
    """OSError 异常处理"""

    def test_oserror_does_not_propagate(self, capsys) -> None:
        """write_text 抛出 OSError 时，函数应捕获并打印错误，不向外抛出。"""
        _save_history = _get_save_history()
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
        ]
        mem = _mock_memory(msgs)

        def raise_oserror(self2, *args, **kwargs):
            raise OSError("磁盘已满")

        with patch.object(Path, "write_text", raise_oserror), \
             patch.object(Path, "mkdir"):
            # 不应抛出异常
            _save_history(mem, "sess", "out")

        out = capsys.readouterr().out
        assert "导出失败" in out or "磁盘已满" in out
