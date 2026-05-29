"""
测试 CLI `/srs` 命令组（[`src/cli/handlers.py::handle_srs`](../src/cli/handlers.py) G6 / D5）。

覆盖：
    - 无参 / `list` 子命令：空 / 多卡 / 状态过滤（active / suspended / archived）
    - `due`：空 / 多卡 due 时按时间升序
    - `show`：存在 / 不存在 / 含完整 front + back + 调度字段
    - `stats`：空队列 / 有卡
    - `del`：confirm 流程（yes / no）+ 不存在
    - 错误处理：非整数 / 负数 / 非法状态过滤
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cli.handlers import handle_srs
from src.memory.srs_store import SRSStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SRSStore]:
    s = SRSStore(str(tmp_path / "srs.db"))
    yield s
    s.close()


def _make_collector() -> tuple[list[str], "callable"]:
    lines: list[str] = []
    def out(msg: str) -> None:
        lines.append(msg)
    return lines, out


def _seed_cards(store: SRSStore) -> tuple[int, int, int]:
    """种 3 张卡：c1 active manual / c2 suspended manual / c3 active quiz_question。"""
    c1 = store.add_card("manual", "Python 装饰器原理", "闭包 + __call__")
    c2 = store.add_card("manual", "RAG 全称", "Retrieval-Augmented Generation")
    store.suspend(c2)
    c3 = store.add_card("quiz_question", "1+1=?", "2", source_ref=42)
    return c1, c2, c3


# ── /srs / /srs list ────────────────────────────────────────────────────────


class TestList:

    def test_list_empty(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs"], out=out)
        joined = "\n".join(lines)
        assert "暂无 SRS 卡片" in joined

    def test_list_default_excludes_archived(self, store: SRSStore) -> None:
        c1, c2, c3 = _seed_cards(store)
        store.archive(c3)
        lines, out = _make_collector()
        handle_srs(store, ["/srs"], out=out)
        joined = "\n".join(lines)
        assert "SRS 卡片列表" in joined
        assert f"[{c1:>3d}]" in joined
        assert f"[{c2:>3d}]" in joined
        assert f"[{c3:>3d}]" not in joined

    def test_list_active_filter(self, store: SRSStore) -> None:
        c1, c2, c3 = _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "list active"], out=out)
        joined = "\n".join(lines)
        assert f"[{c1:>3d}]" in joined
        assert f"[{c3:>3d}]" in joined
        assert f"[{c2:>3d}]" not in joined

    def test_list_suspended_filter(self, store: SRSStore) -> None:
        c1, c2, c3 = _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "list suspended"], out=out)
        joined = "\n".join(lines)
        assert f"[{c2:>3d}]" in joined
        assert f"[{c1:>3d}]" not in joined

    def test_list_invalid_status(self, store: SRSStore) -> None:
        _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "list bogus"], out=out)
        joined = "\n".join(lines)
        assert "非法状态过滤" in joined

    def test_list_renders_source_suffix(self, store: SRSStore) -> None:
        _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "list active"], out=out)
        joined = "\n".join(lines)
        assert "← manual" in joined
        assert "← quiz_q#42" in joined

    def test_list_strips_multiline_front_options(self, store: SRSStore) -> None:
        """MCQ 卡的 front 是多行（题干 + ABCD 选项）— `/srs list` 摘要必须只取题干第一行，
        否则表格被多行选项撑乱（详情靠 `/srs show <id>` 看完整）。"""
        store.add_card(
            "quiz_question",
            front="[单选] 简历最重要的部分？\n\nA. 工作经历\nB. 技术技能\nC. 定位句\nD. 教育背景",
            back="C — 定位句",
            source_ref=99,
        )
        lines, out = _make_collector()
        handle_srs(store, ["/srs"], out=out)
        joined = "\n".join(lines)
        # 题干应在摘要里
        assert "简历最重要的部分？" in joined
        # 但 ABCD 多行选项不该出现在 `/srs list` 摘要
        for opt in ("A. 工作经历", "B. 技术技能", "C. 定位句", "D. 教育背景"):
            assert opt not in joined, f"`/srs list` 摘要不应含选项 {opt!r}"


# ── /srs due ────────────────────────────────────────────────────────────────


class TestDue:

    def test_due_empty(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "due"], out=out)
        joined = "\n".join(lines)
        assert "没有 due 卡片" in joined

    def test_due_lists_active_only(self, store: SRSStore) -> None:
        c1, c2, c3 = _seed_cards(store)  # c2 suspended → 不算
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "due"], out=out)
        joined = "\n".join(lines)
        assert f"[{c1:>3d}]" in joined
        assert f"[{c3:>3d}]" in joined
        assert f"[{c2:>3d}]" not in joined
        assert "当前 due 卡片（2 张）" in joined


# ── /srs show ───────────────────────────────────────────────────────────────


class TestShow:

    def test_show_basic(self, store: SRSStore) -> None:
        c1, _, _ = _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", f"show {c1}"], out=out)
        joined = "\n".join(lines)
        assert "Python 装饰器原理" in joined
        assert "闭包 + __call__" in joined
        assert "ease_factor" in joined
        assert "next_review_at" in joined

    def test_show_nonexistent(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "show 999"], out=out)
        joined = "\n".join(lines)
        assert "card_id=999 不存在" in joined

    def test_show_invalid_id_non_integer(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "show abc"], out=out)
        joined = "\n".join(lines)
        assert "无效 card_id" in joined

    def test_show_negative_id(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "show 0"], out=out)
        joined = "\n".join(lines)
        assert "card_id 必须 ≥ 1" in joined

    def test_show_missing_id(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "show"], out=out)
        joined = "\n".join(lines)
        assert "请提供 card_id" in joined


# ── /srs stats ──────────────────────────────────────────────────────────────


class TestStats:

    def test_stats_empty(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "stats"], out=out)
        joined = "\n".join(lines)
        assert "SRS 队列为空" in joined

    def test_stats_with_cards(self, store: SRSStore) -> None:
        _seed_cards(store)
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "stats"], out=out)
        joined = "\n".join(lines)
        assert "总 active:" in joined
        assert "当前 due:" in joined
        assert "平均 ease:" in joined


# ── /srs del ───────────────────────────────────────────────────────────────


class TestDel:

    def test_del_confirmed(self, store: SRSStore) -> None:
        c1, _, _ = _seed_cards(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="yes"):
            handle_srs(store, ["/srs", f"del {c1}"], out=out)
        joined = "\n".join(lines)
        assert "已删除" in joined
        assert store.get_card(c1) is None

    def test_del_cancelled(self, store: SRSStore) -> None:
        c1, _, _ = _seed_cards(store)
        lines, out = _make_collector()
        with patch("builtins.input", return_value="no"):
            handle_srs(store, ["/srs", f"del {c1}"], out=out)
        joined = "\n".join(lines)
        assert "已取消" in joined
        assert store.get_card(c1) is not None

    def test_del_nonexistent(self, store: SRSStore) -> None:
        lines, out = _make_collector()
        handle_srs(store, ["/srs", "del 999"], out=out)
        joined = "\n".join(lines)
        assert "card_id=999 不存在" in joined


# ── 未知子命令 ──────────────────────────────────────────────────────────────


def test_unknown_subcommand(store: SRSStore) -> None:
    lines, out = _make_collector()
    handle_srs(store, ["/srs", "review 1 good"], out=out)
    joined = "\n".join(lines)
    assert "未知子命令" in joined
    assert "/srs list" in joined
