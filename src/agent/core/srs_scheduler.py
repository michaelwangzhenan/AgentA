"""
SM-2 调度算法核心

SuperMemo 2（Wozniak, 1987）是 Anki 默认调度器的算法祖先：每张卡按"答对/答错 +
难度自评"动态调整 `ease_factor`（难度因子）、`interval_days`（下次回炉天数）、
`repetitions`（累计答对次数）。本模块只做**纯函数式公式计算**，不感知 SQLite / 卡片
来源 / UI；持久化由 [`src.memory.srs_store.SRSStore.update_review_state`]
(../../memory/srs_store.py) 负责。

Anki 4 档自评 → SM-2 公式 mapping：

| 用户自评 | 含义 | SM-2 q score | ease 变化 | interval 变化 |
|---|---|---|---|---|
| `again` | 完全忘了 | 1 | -0.2 | 重置：repetitions=0, interval=1 |
| `hard` | 想起来但费劲 | 3 | -0.15 | interval × 0.8 hard penalty（最小 1 天）|
| `good` | 正常答对 | 4 | 不变 | 走 SM-2 主公式（reps=1→1d, reps=2→6d, reps≥3→prev × ease）|
| `easy` | 太简单 | 5 | +0.15 | 走主公式后再 × 1.3 easy bonus |

公式参考：
- 标准 SM-2 ease 更新公式（quality q ∈ {0..5}）：
    new_ease = old_ease + (0.1 - (5-q) * (0.08 + (5-q) * 0.02))
- ease 下限 1.3（SM-2 原版约定）；上限不强制（Anki 实践 ~2.5 常见，但
  超过 2.5 也合法 — 用户连答 easy 时合理上扬）。

暂不实现的高级特性（留待 FSRS 升级时考虑）：
- 学习阶段 learning_steps（Anki 新卡前几次 review 走分钟级别 graduated steps）
- 模糊间隔 fuzz（避免大量卡片同一天 due）
- 同卡当天连续多次 review 的 lapses 计数策略
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

import src.config as config


# ── 公式常量 ──────────────────────────────────────────────────────────────────

# ease 下限：SM-2 原版约定，低于此值卡片"无法再变难"
EASE_FACTOR_MIN: float = 1.3
# 新卡 ease 初始值：SM-2 / Anki 默认 2.5
EASE_FACTOR_INIT: float = 2.5
# interval 最小天数：1 天（SM-2 不允许同日二次出现，避免短期记忆作弊）
INTERVAL_MIN_DAYS: int = 1
# hard 档 interval penalty 系数（Anki 默认 1.2，但因 ease 同步下调 -0.15，
# 这里用 0.8 实现"想起来但费劲 → 间隔略缩"语义，跟 4 档语义对齐）
HARD_INTERVAL_MULTIPLIER: float = 0.8
# easy 档 interval bonus 系数（Anki 默认 1.3）
EASY_INTERVAL_MULTIPLIER: float = 1.3


# ── 4 档评分 ──────────────────────────────────────────────────────────────────


class Rating(str, Enum):
    """Anki 4 档用户自评。"""

    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


# 4 档 → SM-2 q score（mapping 表）
_RATING_QUALITY: dict[Rating, int] = {
    Rating.AGAIN: 1,
    Rating.HARD: 3,
    Rating.GOOD: 4,
    Rating.EASY: 5,
}


def parse_rating(raw: str) -> Rating:
    """
    宽松解析用户输入的 rating 字符串 → Rating enum。

    Args:
        raw: 用户输入。允许形态：'again' / 'AGAIN' / ' Again ' / 'good' 等。

    Raises:
        ValueError: 输入不是合法的 4 档之一。
    """
    if not isinstance(raw, str):
        raise ValueError(f"rating 必须是字符串，收到 {type(raw).__name__}")
    norm = raw.strip().lower()
    for r in Rating:
        if r.value == norm:
            return r
    raise ValueError(
        f"rating 必须是 'again' / 'hard' / 'good' / 'easy' 之一，收到 {raw!r}"
    )


# ── 状态数据载体 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CardState:
    """
    review 公式的输入快照（卡片当前调度状态）。

    Attributes:
        ease_factor: 当前难度因子（≥ 1.3）。
        interval_days: 当前 interval 天数（0 = 新卡 / 从未 review）。
        repetitions: 累计成功答对次数（again 时重置为 0）。
        lapses: 累计答错次数（again 时 +1）。
    """

    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int


@dataclass(frozen=True)
class ScheduleResult:
    """
    review 公式的输出（写库用的新状态 + 计算好的 next_review_at）。

    Attributes:
        ease_factor: 新 ease，已夹紧到 ≥ 1.3。
        interval_days: 新 interval，已夹紧到 ≥ 1。
        repetitions: 新累计答对次数（again → 0；其它 +1）。
        lapses: 新累计答错次数（again → +1；其它不变）。
        next_review_at: ISO 8601 本地时间字符串 — 由 now + interval 计算。
        rating: 本次 review 的评分（4 档；方便 caller 日志 / UI 复用）。
    """

    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int
    next_review_at: str
    rating: Rating


# ── 核心公式 ─────────────────────────────────────────────────────────────────


def _clip_ease(value: float) -> float:
    """夹紧 ease：下限 1.3，无上限。"""
    return max(EASE_FACTOR_MIN, float(value))


def _clip_interval(value: float) -> int:
    """夹紧 interval：下限 1 天，向上取整避免"几乎一天但显示 0 天"。"""
    iv = int(round(max(INTERVAL_MIN_DAYS, float(value))))
    return max(INTERVAL_MIN_DAYS, iv)


def _update_ease(old: float, q: int) -> float:
    """
    SM-2 原版 ease 更新公式：
        new_ease = old_ease + (0.1 - (5-q) * (0.08 + (5-q) * 0.02))

    q ∈ {1, 3, 4, 5}（4 档 mapping 后只有这 4 个取值）。
    """
    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    return _clip_ease(old + delta)


_FIRST_INTERVAL_DAYS_CACHED: int = 1
_SECOND_INTERVAL_DAYS_CACHED: int = 6


def _interval_from_repetitions(reps: int, prev_interval: int, ease: float) -> int:
    """
    SM-2 主 interval 公式（仅 q >= 3 即 hard/good/easy 时调用；again 在外部重置）：

    - repetitions == 1：第一次答对，interval = SRS_FIRST_INTERVAL_DAYS（默认 1）
    - repetitions == 2：第二次答对，interval = SRS_SECOND_INTERVAL_DAYS（默认 6）
    - repetitions >= 3：interval = round(prev_interval * ease)
    """
    if reps == 1:
        return _clip_interval(config.SRS_FIRST_INTERVAL_DAYS)
    if reps == 2:
        return _clip_interval(config.SRS_SECOND_INTERVAL_DAYS)
    return _clip_interval(prev_interval * ease)


def schedule_review(
    state: CardState,
    rating: Rating | str,
    *,
    now: datetime | None = None,
) -> ScheduleResult:
    """
    根据当前卡片状态 + 用户 4 档自评 → 计算新的 SM-2 调度状态。

    Args:
        state: 当前卡片调度状态（来自 SRSStore.get_card 后提取）。
        rating: 用户 4 档自评（Rating enum 或 'again' / 'hard' / 'good' / 'easy' 字符串）。
        now: 可选当前时间覆盖（UT 用）；None 取系统当前本地时间。

    Returns:
        ScheduleResult — 新状态 + ISO `next_review_at`，由 caller 写库。

    Raises:
        ValueError: rating 不是合法 4 档之一。
    """
    r = rating if isinstance(rating, Rating) else parse_rating(rating)
    q = _RATING_QUALITY[r]
    new_ease = _update_ease(state.ease_factor, q)

    if r is Rating.AGAIN:
        # again：重置 repetitions、interval=1、lapses+1
        new_reps = 0
        new_interval = INTERVAL_MIN_DAYS
        new_lapses = state.lapses + 1
    else:
        new_reps = state.repetitions + 1
        new_lapses = state.lapses
        new_interval = _interval_from_repetitions(new_reps, state.interval_days, new_ease)
        if r is Rating.HARD:
            new_interval = _clip_interval(new_interval * HARD_INTERVAL_MULTIPLIER)
        elif r is Rating.EASY:
            new_interval = _clip_interval(new_interval * EASY_INTERVAL_MULTIPLIER)

    when = now if now is not None else datetime.now()
    next_at = (when + timedelta(days=new_interval)).isoformat(timespec="seconds")

    return ScheduleResult(
        ease_factor=new_ease,
        interval_days=new_interval,
        repetitions=new_reps,
        lapses=new_lapses,
        next_review_at=next_at,
        rating=r,
    )


# ── helper：从 SRSStore card dict 构造 CardState ─────────────────────────────


def card_state_from_dict(card: dict) -> CardState:
    """
    把 SRSStore.get_card 返回的 dict 转成 CardState（避免 caller 重复键访问）。
    """
    return CardState(
        ease_factor=float(card.get("ease_factor", EASE_FACTOR_INIT)),
        interval_days=int(card.get("interval_days", 0)),
        repetitions=int(card.get("repetitions", 0)),
        lapses=int(card.get("lapses", 0)),
    )


__all__: tuple[str, ...] = (
    "Rating",
    "CardState",
    "ScheduleResult",
    "schedule_review",
    "parse_rating",
    "card_state_from_dict",
    "EASE_FACTOR_INIT",
    "EASE_FACTOR_MIN",
    "INTERVAL_MIN_DAYS",
)
