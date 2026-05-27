"""
test_rules_loader —— Phase 1.3 项目级 rules 加载行为单测

覆盖 5 条 case：
1. 文件缺失 → None（不抛异常）
2. 空文件 / 全空白 → None（视同未配置）
3. 正常内容 → strip 后返回，含 build_rules_block 拼装
4. 超长截断 → 末尾带 `…(rules truncated)` 注脚
5. 含 BOM + 首尾空白 → strip 后干净返回

测试通过临时目录 + monkeypatch _cfg 隔离外部环境，不依赖工作目录。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.config as _cfg
from src.agent.core.rules_loader import (
    _TRUNCATE_NOTICE,
    build_rules_block,
    load_project_rules,
)


class TestLoadProjectRules:
    """load_project_rules 各路径行为。"""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = load_project_rules(root=tmp_path)
        assert result is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("", encoding="utf-8")

        assert load_project_rules(root=tmp_path) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("   \n\t  \n", encoding="utf-8")

        assert load_project_rules(root=tmp_path) is None

    def test_normal_content_returned_stripped(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        body = "始终用中文回答。\n引用要带页码。"
        (rules_dir / "rules.md").write_text("\n\n" + body + "\n\n", encoding="utf-8")

        result = load_project_rules(root=tmp_path)
        assert result == body  # 首尾空白被 strip

    def test_bom_is_stripped(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("\ufeff不要用 bullet", encoding="utf-8")

        result = load_project_rules(root=tmp_path)
        assert result == "不要用 bullet"
        assert not result.startswith("\ufeff")

    def test_oversize_is_truncated_with_notice(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        long_text = "X" * 200
        (rules_dir / "rules.md").write_text(long_text, encoding="utf-8")

        result = load_project_rules(root=tmp_path, max_chars=50)
        assert result is not None
        assert len(result) <= 50
        assert result.endswith(_TRUNCATE_NOTICE)

    def test_disabled_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """USER_RULES_ENABLED=False 时即便文件存在也跳过。"""
        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("有内容", encoding="utf-8")

        monkeypatch.setattr(_cfg, "USER_RULES_ENABLED", False)
        assert load_project_rules(root=tmp_path) is None

    def test_custom_file_arg_overrides_config(self, tmp_path: Path) -> None:
        """显式传 file= 不走 _cfg.USER_RULES_FILE，便于测试与场景化使用。"""
        (tmp_path / "my_rules.md").write_text("alt rules", encoding="utf-8")

        result = load_project_rules(root=tmp_path, file="my_rules.md")
        assert result == "alt rules"


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
