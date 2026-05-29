"""
测试 [srs_scheduler](../src/agent/core/srs_scheduler.py)（Phase 2.4 G2 / D1 / D4 / D11）。

SM-2 算法核心 + Anki 4 档 mapping 锁定测试，覆盖：
    - parse_rating：4 档大小写宽容 / 非法输入 raise
    - schedule_review：
        * again：重置 reps=0, interval=1, lapses+1, ease 大幅下调
        * hard：reps+1, ease 小幅下调（-0.15），interval × 0.8 penalty
        * good：reps+1, ease 不变（按 SM-2 公式 q=4），interval 走主公式
        * easy：reps+1, ease 上调（+0.15），interval × 1.3 bonus
    - SM-2 阶段公式（D11）：
        * reps=1 → interval = SRS_FIRST_INTERVAL_DAYS（默认 1）
        * reps=2 → interval = SRS_SECOND_INTERVAL_DAYS（默认 6）
        * reps≥3 → interval = round(prev × ease)
    - 边界保护：ease ≥ 1.3，interval ≥ 1
    - card_state_from_dict：从 SRSStore dict 构造
    - 时间计算：next_review_at = now + interval
"""

from datetime import datetime, timedelta

import pytest

from src.agent.core.srs_scheduler import (
    EASE_FACTOR_INIT,
    EASE_FACTOR_MIN,
    INTERVAL_MIN_DAYS,
    CardState,
    Rating,
    card_state_from_dict,
    parse_rating,
    schedule_review,
)


def _new_state(
    ease: float = EASE_FACTOR_INIT,
    interval: int = 0,
    reps: int = 0,
    lapses: int = 0,
) -> CardState:
    return CardState(ease_factor=ease, interval_days=interval, repetitions=reps, lapses=lapses)


# ── parse_rating ───────────────────────────────────────────────────────────


class TestParseRating:
    @pytest.mark.parametrize("raw,expected", [
        ("again", Rating.AGAIN),
        ("AGAIN", Rating.AGAIN),
        (" Again ", Rating.AGAIN),
        ("hard", Rating.HARD),
        ("HARD", Rating.HARD),
        ("good", Rating.GOOD),
        ("easy", Rating.EASY),
        ("EASY", Rating.EASY),
    ])
    def test_valid(self, raw: str, expected: Rating) -> None:
        assert parse_rating(raw) is expected

    @pytest.mark.parametrize("raw", ["", "1", "ok", "great", "fail", "good?"])
    def test_invalid_raises(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_rating(raw)

    def test_non_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_rating(3)  # type: ignore[arg-type]


# ── again 路径 ─────────────────────────────────────────────────────────────


class TestAgain:
    def test_resets_reps_and_interval(self) -> None:
        state = _new_state(ease=2.5, interval=10, reps=4, lapses=1)
        result = schedule_review(state, Rating.AGAIN)
        assert result.repetitions == 0
        assert result.interval_days == INTERVAL_MIN_DAYS
        assert result.lapses == 2

    def test_ease_drops(self) -> None:
        state = _new_state(ease=2.5)
        result = schedule_review(state, Rating.AGAIN)
        # SM-2: q=1 → delta = 0.1 - 4 * (0.08 + 4*0.02) = 0.1 - 4*0.16 = -0.54
        assert result.ease_factor == pytest.approx(2.5 - 0.54, abs=0.01)

    def test_ease_clipped_to_min(self) -> None:
        state = _new_state(ease=1.5)
        result = schedule_review(state, Rating.AGAIN)
        # 1.5 - 0.54 = 0.96 → 夹紧到 1.3
        assert result.ease_factor == EASE_FACTOR_MIN

    def test_string_rating_accepted(self) -> None:
        state = _new_state()
        result = schedule_review(state, "again")
        assert result.rating is Rating.AGAIN


# ── hard 路径 ──────────────────────────────────────────────────────────────


class TestHard:
    def test_reps_increment(self) -> None:
        state = _new_state(ease=2.5, interval=6, reps=2)
        result = schedule_review(state, Rating.HARD)
        assert result.repetitions == 3
        assert result.lapses == 0

    def test_ease_drops_slightly(self) -> None:
        state = _new_state(ease=2.5)
        result = schedule_review(state, Rating.HARD)
        # SM-2: q=3 → delta = 0.1 - 2*(0.08+2*0.02) = 0.1 - 0.24 = -0.14
        assert result.ease_factor == pytest.approx(2.5 - 0.14, abs=0.01)

    def test_interval_hard_penalty(self) -> None:
        state = _new_state(ease=2.5, interval=10, reps=2)
        result = schedule_review(state, Rating.HARD)
        # new_reps = 3, base = round(10 * 2.36) = 24, × 0.8 = 18.88 → 19
        # ease after hard = 2.5 - 0.14 ≈ 2.36
        assert result.interval_days == round(round(10 * 2.36) * 0.8)


# ── good 路径（SM-2 主公式） ───────────────────────────────────────────────


class TestGood:
    def test_reps_1_uses_first_interval(self) -> None:
        state = _new_state(ease=2.5, interval=0, reps=0)
        result = schedule_review(state, Rating.GOOD)
        assert result.repetitions == 1
        assert result.interval_days == 1

    def test_reps_2_uses_second_interval(self) -> None:
        state = _new_state(ease=2.5, interval=1, reps=1)
        result = schedule_review(state, Rating.GOOD)
        assert result.repetitions == 2
        assert result.interval_days == 6

    def test_reps_3_uses_main_formula(self) -> None:
        state = _new_state(ease=2.5, interval=6, reps=2)
        result = schedule_review(state, Rating.GOOD)
        assert result.repetitions == 3
        # ease 不变（q=4 → delta = 0），interval = round(6 * 2.5) = 15
        assert result.ease_factor == pytest.approx(2.5, abs=0.001)
        assert result.interval_days == 15

    def test_ease_unchanged(self) -> None:
        state = _new_state(ease=2.5)
        result = schedule_review(state, Rating.GOOD)
        # SM-2: q=4 → delta = 0.1 - 1*(0.08+0.02) = 0
        assert result.ease_factor == pytest.approx(2.5, abs=0.001)

    def test_lapses_unchanged(self) -> None:
        state = _new_state(lapses=3)
        result = schedule_review(state, Rating.GOOD)
        assert result.lapses == 3


# ── easy 路径 ──────────────────────────────────────────────────────────────


class TestEasy:
    def test_reps_increment(self) -> None:
        state = _new_state(ease=2.5, interval=6, reps=2)
        result = schedule_review(state, Rating.EASY)
        assert result.repetitions == 3

    def test_ease_rises(self) -> None:
        state = _new_state(ease=2.5)
        result = schedule_review(state, Rating.EASY)
        # SM-2: q=5 → delta = 0.1 - 0 = 0.1
        assert result.ease_factor == pytest.approx(2.6, abs=0.001)

    def test_interval_easy_bonus(self) -> None:
        state = _new_state(ease=2.5, interval=10, reps=2)
        result = schedule_review(state, Rating.EASY)
        # ease_new = 2.6；base interval = round(10 * 2.6) = 26；× 1.3 = 33.8 → 34
        expected = round(round(10 * 2.6) * 1.3)
        assert result.interval_days == expected

    def test_easy_at_first_review(self) -> None:
        # reps=0 → reps=1 → first_interval 1 → × 1.3 → 1.3 → 夹紧 1
        state = _new_state(reps=0, interval=0)
        result = schedule_review(state, Rating.EASY)
        assert result.interval_days >= INTERVAL_MIN_DAYS


# ── 边界 ────────────────────────────────────────────────────────────────────


class TestBoundary:
    def test_ease_never_below_min(self) -> None:
        # 多次 again 让 ease 一直降，但永远 ≥ 1.3
        state = _new_state(ease=1.4)
        for _ in range(10):
            result = schedule_review(state, Rating.AGAIN)
            state = CardState(
                ease_factor=result.ease_factor,
                interval_days=result.interval_days,
                repetitions=result.repetitions,
                lapses=result.lapses,
            )
        assert state.ease_factor == EASE_FACTOR_MIN

    def test_interval_never_below_min(self) -> None:
        state = _new_state(ease=1.3, interval=1, reps=2)
        result = schedule_review(state, Rating.HARD)
        # base × 0.8 可能很小，但必 ≥ 1
        assert result.interval_days >= INTERVAL_MIN_DAYS

    def test_next_review_at_format_iso(self) -> None:
        state = _new_state()
        when = datetime(2026, 1, 1, 12, 0, 0)
        result = schedule_review(state, Rating.GOOD, now=when)
        # interval=1 → next = 2026-01-02T12:00:00
        expected_dt = when + timedelta(days=result.interval_days)
        assert result.next_review_at == expected_dt.isoformat(timespec="seconds")


# ── card_state_from_dict ────────────────────────────────────────────────────


class TestCardStateFromDict:
    def test_full_dict(self) -> None:
        card = {
            "ease_factor": 2.3,
            "interval_days": 6,
            "repetitions": 2,
            "lapses": 1,
            "extra": "ignored",
        }
        state = card_state_from_dict(card)
        assert state.ease_factor == 2.3
        assert state.interval_days == 6
        assert state.repetitions == 2
        assert state.lapses == 1

    def test_missing_fields_use_defaults(self) -> None:
        state = card_state_from_dict({})
        assert state.ease_factor == EASE_FACTOR_INIT
        assert state.interval_days == 0
        assert state.repetitions == 0
        assert state.lapses == 0


# ── Anki 4 档语义锁定（D4 mapping 表） ─────────────────────────────────────


class TestAnkiAlignment:
    """从同一个起点状态出发，4 档结果必须满足 again < hard < good < easy 的 interval 序关系。"""

    def test_interval_order_again_lt_hard_lt_good_lt_easy(self) -> None:
        base = _new_state(ease=2.5, interval=10, reps=2)
        r_again = schedule_review(base, Rating.AGAIN).interval_days
        r_hard = schedule_review(base, Rating.HARD).interval_days
        r_good = schedule_review(base, Rating.GOOD).interval_days
        r_easy = schedule_review(base, Rating.EASY).interval_days
        assert r_again < r_hard < r_good < r_easy

    def test_ease_order_again_lt_hard_lt_good_lt_easy(self) -> None:
        base = _new_state(ease=2.5)
        e_again = schedule_review(base, Rating.AGAIN).ease_factor
        e_hard = schedule_review(base, Rating.HARD).ease_factor
        e_good = schedule_review(base, Rating.GOOD).ease_factor
        e_easy = schedule_review(base, Rating.EASY).ease_factor
        assert e_again < e_hard < e_good < e_easy

    def test_only_again_increments_lapses(self) -> None:
        base = _new_state(lapses=5)
        assert schedule_review(base, Rating.AGAIN).lapses == 6
        assert schedule_review(base, Rating.HARD).lapses == 5
        assert schedule_review(base, Rating.GOOD).lapses == 5
        assert schedule_review(base, Rating.EASY).lapses == 5

    def test_only_again_resets_reps_to_zero(self) -> None:
        base = _new_state(reps=3)
        assert schedule_review(base, Rating.AGAIN).repetitions == 0
        assert schedule_review(base, Rating.HARD).repetitions == 4
        assert schedule_review(base, Rating.GOOD).repetitions == 4
        assert schedule_review(base, Rating.EASY).repetitions == 4
