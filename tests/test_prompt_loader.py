"""
tests/test_prompt_loader.py — 测试 src/cli/prompt_loader.py

覆盖：
  - scan_prompts() 正常加载、空目录、不存在目录
  - 非法名称跳过、保留命令冲突跳过
  - 非 .prompt.md 文件忽略
  - load_prompt_file() 内容读取与首尾空白去除
"""

import logging
from pathlib import Path

import pytest

from src.cli.prompt_loader import (
    RESERVED_COMMANDS,
    load_prompt_file,
    scan_prompts,
)


class TestLoadPromptFile:
    """测试 load_prompt_file() 基本功能。"""

    def test_reads_file_content(self, tmp_path: Path) -> None:
        f = tmp_path / "test.prompt.md"
        f.write_text("你是专家助手。", encoding="utf-8")
        assert load_prompt_file(f) == "你是专家助手。"

    def test_strips_leading_trailing_whitespace(self, tmp_path: Path) -> None:
        f = tmp_path / "test.prompt.md"
        f.write_text("\n\n  内容  \n\n", encoding="utf-8")
        assert load_prompt_file(f) == "内容"

    def test_accepts_path_or_str(self, tmp_path: Path) -> None:
        f = tmp_path / "test.prompt.md"
        f.write_text("hello", encoding="utf-8")
        assert load_prompt_file(str(f)) == "hello"
        assert load_prompt_file(f) == "hello"


class TestScanPrompts:
    """测试 scan_prompts() 目录扫描逻辑。"""

    def test_returns_empty_when_dir_not_exists(self, tmp_path: Path) -> None:
        result = scan_prompts(tmp_path / "nonexistent")
        assert result == {}

    def test_returns_empty_when_dir_is_empty(self, tmp_path: Path) -> None:
        result = scan_prompts(tmp_path)
        assert result == {}

    def test_loads_prompt_files(self, tmp_path: Path) -> None:
        (tmp_path / "expert.prompt.md").write_text("你是专家。", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert "/expert" in result
        assert result["/expert"] == "你是专家。"

    def test_key_has_slash_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "mybot.prompt.md").write_text("content", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert list(result.keys()) == ["/mybot"]

    def test_loads_multiple_files_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "zzz.prompt.md").write_text("z", encoding="utf-8")
        (tmp_path / "aaa.prompt.md").write_text("a", encoding="utf-8")
        result = scan_prompts(tmp_path)
        keys = list(result.keys())
        assert keys == ["/aaa", "/zzz"]

    def test_ignores_non_prompt_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("不是 prompt", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("不是 prompt", encoding="utf-8")
        (tmp_path / "valid.prompt.md").write_text("有效", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert list(result.keys()) == ["/valid"]

    def test_ignores_gitkeep(self, tmp_path: Path) -> None:
        (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
        (tmp_path / "bot.prompt.md").write_text("内容", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert list(result.keys()) == ["/bot"]

    def test_skips_invalid_name_with_spaces(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        (tmp_path / "my bot.prompt.md").write_text("内容", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="src.cli.prompt_loader"):
            result = scan_prompts(tmp_path)
        assert result == {}
        assert "非法字符" in caplog.text

    def test_skips_invalid_name_with_special_chars(self, tmp_path: Path) -> None:
        (tmp_path / "my@bot.prompt.md").write_text("内容", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert result == {}

    def test_skips_reserved_command_names(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        (tmp_path / "help.prompt.md").write_text("内容", encoding="utf-8")
        (tmp_path / "clear.prompt.md").write_text("内容", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="src.cli.prompt_loader"):
            result = scan_prompts(tmp_path)
        assert result == {}
        assert "内置命令冲突" in caplog.text

    def test_reserved_commands_covers_all_builtins(self) -> None:
        """保留命令集合应包含所有已知内置命令。"""
        expected = {"help", "clear", "history", "session", "ingest",
                    "quit", "exit", "del-session", "clean-session", "reload-prompts"}
        assert expected.issubset(RESERVED_COMMANDS)

    def test_result_content_is_stripped(self, tmp_path: Path) -> None:
        (tmp_path / "bot.prompt.md").write_text("\n  内容  \n", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert result["/bot"] == "内容"

    def test_valid_names_with_hyphen_and_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "5g-expert.prompt.md").write_text("5G专家", encoding="utf-8")
        (tmp_path / "code_assistant.prompt.md").write_text("代码助手", encoding="utf-8")
        result = scan_prompts(tmp_path)
        assert "/5g-expert" in result
        assert "/code_assistant" in result
