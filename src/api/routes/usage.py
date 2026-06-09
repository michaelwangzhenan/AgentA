"""Token 用量统计端点（iter_11）。

- 本人视角：`/api/usage/summary|series|events|events.csv`（强制按当前用户过滤）
- 全员视角（admin）：`/api/usage/admin/*`（按用户分组 / 下钻）
- 单价：`GET /api/usage/pricing`（登录可读）/ `PUT /api/usage/pricing`（admin 写）

成本按"内置默认 ← admin 覆盖"合并后的单价实时算（详 usage_store.merged_pricing）。
口径与采集见 docs/iter_11_token.md §3 / §4.1。
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

import src.config as _cfg
from src.api.deps import get_current_user, get_usage_store, get_user_store, require_admin
from src.api.schemas.usage import (
    PricingItem,
    PricingResponse,
    PricingUpdateRequest,
    UsageEvent,
    UsageEvents,
    UsageSeries,
    UsageSummary,
    UserUsage,
    UserUsageList,
    SeriesRow,
)
from src.memory.usage_store import UsageStore, cost_of, merged_pricing
from src.memory.user_store import UserStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])

_VALID_RANGES = ("1d", "7d", "30d", "mtd", "last_month")


def _resolve_range(range_key: str) -> tuple[int, int]:
    """把范围关键字解析成 [start, end) 的 epoch 秒（本地时区边界）。"""
    rk = (range_key or "30d").lower()
    if rk not in _VALID_RANGES:
        rk = "30d"
    now = datetime.now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_tomorrow = start_of_today + timedelta(days=1)
    if rk == "1d":
        start, end = start_of_today, start_of_tomorrow
    elif rk == "7d":
        start, end = start_of_today - timedelta(days=6), start_of_tomorrow
    elif rk == "30d":
        start, end = start_of_today - timedelta(days=29), start_of_tomorrow
    elif rk == "mtd":
        start, end = start_of_today.replace(day=1), start_of_tomorrow
    else:  # last_month
        first_this = start_of_today.replace(day=1)
        last_month_end = first_this
        last_month_start = (first_this - timedelta(days=1)).replace(day=1)
        start, end = last_month_start, last_month_end
    return int(start.timestamp()), int(end.timestamp())


def _model_meta(model_id: str) -> tuple[str, str]:
    """返回 (展示名, 档位)；未知模型用 id 兜底。"""
    m = _cfg.MODEL_CONFIGS.get(model_id)
    if m is not None:
        return (m.label or model_id, m.tier)
    return (model_id, "")


# ── 概览 / 趋势 / 明细：本人与全员复用同一套实现，user_id=None 即全员 ──────────────


def _summary(store: UsageStore, start: int, end: int, user_id: int | None) -> UsageSummary:
    pricing = merged_pricing(store)
    rows = store.aggregate_by_model(start, end, user_id=user_id)
    total = prompt = completion = count = 0
    cost = 0.0
    has_unpriced = False
    for r in rows:
        total += r["total_tokens"]
        prompt += r["prompt_tokens"]
        completion += r["completion_tokens"]
        count += r["count"]
        cost += cost_of(r["model_id"], r["prompt_tokens"], r["completion_tokens"], pricing)
        if r["total_tokens"] > 0 and r["model_id"] not in pricing:
            has_unpriced = True
    return UsageSummary(
        start=start, end=end, range="", currency=_cfg.USAGE_CURRENCY,
        total_tokens=total, prompt_tokens=prompt, completion_tokens=completion,
        count=count, cost=round(cost, 6), has_unpriced=has_unpriced,
    )


def _series(
    store: UsageStore,
    start: int,
    end: int,
    user_id: int | None,
    group_by: str,
    usernames: dict[int, str] | None = None,
) -> UsageSeries:
    pricing = merged_pricing(store)
    raw = store.aggregate_series(start, end, user_id=user_id)
    usernames = usernames or {}
    # 按 (date, key) 聚合，cost 在 model 粒度算好再 rollup（none/user 分组也准）
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in raw:
        if group_by == "model":
            key, label = r["model_id"], _model_meta(r["model_id"])[0]
        elif group_by == "user":
            key = str(r["user_id"])
            label = usernames.get(r["user_id"], f"#{r['user_id']}")
        else:  # none
            key, label = "all", "全部"
        slot = acc.setdefault(
            (r["day"], key),
            {"label": label, "total": 0, "prompt": 0, "completion": 0, "count": 0, "cost": 0.0},
        )
        slot["total"] += r["total_tokens"]
        slot["prompt"] += r["prompt_tokens"]
        slot["completion"] += r["completion_tokens"]
        slot["count"] += r["count"]
        slot["cost"] += cost_of(
            r["model_id"], r["prompt_tokens"], r["completion_tokens"], pricing
        )
    rows = [
        SeriesRow(
            date=day, key=key, key_label=v["label"],
            total_tokens=v["total"], prompt_tokens=v["prompt"],
            completion_tokens=v["completion"], count=v["count"],
            cost=round(v["cost"], 6),
        )
        for (day, key), v in sorted(acc.items())
    ]
    return UsageSeries(
        start=start, end=end, range="", group_by=group_by,
        currency=_cfg.USAGE_CURRENCY, rows=rows,
    )


def _events(
    store: UsageStore,
    start: int,
    end: int,
    user_id: int | None,
    model_id: str | None,
    limit: int,
    offset: int,
    usernames: dict[int, str] | None = None,
    with_user: bool = False,
) -> UsageEvents:
    pricing = merged_pricing(store)
    raw, total = store.list_events(
        start, end, user_id=user_id, model_id=model_id, limit=limit, offset=offset
    )
    usernames = usernames or {}
    events: list[UsageEvent] = []
    for e in raw:
        label, tier = _model_meta(e["model_id"])
        events.append(UsageEvent(
            id=e["id"], created_at=e["created_at"], model_id=e["model_id"],
            model_label=label, tier=tier, thinking=e["thinking"],
            prompt_tokens=e["prompt_tokens"], completion_tokens=e["completion_tokens"],
            total_tokens=e["total_tokens"],
            cost=round(cost_of(e["model_id"], e["prompt_tokens"], e["completion_tokens"], pricing), 6),
            session_id=e["session_id"],
            user_id=e["user_id"] if with_user else None,
            username=usernames.get(e["user_id"]) if with_user else None,
        ))
    return UsageEvents(
        events=events, total=total, limit=limit, offset=offset,
        currency=_cfg.USAGE_CURRENCY,
    )


def _csv_safe(value: Any) -> Any:
    """防 CSV 公式注入：以 = + - @ 开头的文本单元格前缀一个单引号。

    导出文件被 Excel / Sheets 打开时，=cmd 之类会被当公式执行；这里中和（OWASP 推荐）。
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _events_csv(events: UsageEvents, with_user: bool) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["time", "model", "type", "prompt_tokens", "completion_tokens",
              "total_tokens", f"cost({events.currency})"]
    if with_user:
        header.insert(1, "user")
    w.writerow(header)
    for e in events.events:
        t = datetime.fromtimestamp(e.created_at).isoformat(timespec="seconds")
        row = [t, e.model_label, "thinking" if e.thinking else "normal",
               e.prompt_tokens, e.completion_tokens, e.total_tokens, e.cost]
        if with_user:
            row.insert(1, e.username or "")
        w.writerow([_csv_safe(c) for c in row])
    return buf.getvalue()


# ── 本人端点 ──────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=UsageSummary)
def my_summary(
    range: str = Query("30d"),
    user: dict = Depends(get_current_user),
    store: UsageStore = Depends(get_usage_store),
) -> UsageSummary:
    start, end = _resolve_range(range)
    out = _summary(store, start, end, user_id=user["id"])
    out.range = range
    return out


@router.get("/series", response_model=UsageSeries)
def my_series(
    range: str = Query("30d"),
    group_by: str = Query("model"),
    user: dict = Depends(get_current_user),
    store: UsageStore = Depends(get_usage_store),
) -> UsageSeries:
    gb = group_by if group_by in ("model", "none") else "model"
    start, end = _resolve_range(range)
    out = _series(store, start, end, user_id=user["id"], group_by=gb)
    out.range = range
    return out


@router.get("/events", response_model=UsageEvents)
def my_events(
    range: str = Query("30d"),
    model_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    store: UsageStore = Depends(get_usage_store),
) -> UsageEvents:
    start, end = _resolve_range(range)
    return _events(store, start, end, user["id"], model_id, limit, offset)


@router.get("/events.csv")
def my_events_csv(
    range: str = Query("30d"),
    model_id: str | None = Query(None),
    user: dict = Depends(get_current_user),
    store: UsageStore = Depends(get_usage_store),
) -> Response:
    start, end = _resolve_range(range)
    data = _events(store, start, end, user["id"], model_id, limit=100_000, offset=0)
    csv_text = _events_csv(data, with_user=False)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="usage_{range}.csv"'},
    )


# ── 全员端点（admin） ──────────────────────────────────────────────────────────


def _username_map(users: UserStore) -> dict[int, str]:
    return {u["id"]: u["username"] for u in users.list_users()}


@router.get("/admin/summary", response_model=UsageSummary)
def admin_summary(
    range: str = Query("30d"),
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
) -> UsageSummary:
    start, end = _resolve_range(range)
    out = _summary(store, start, end, user_id=None)
    out.range = range
    return out


@router.get("/admin/series", response_model=UsageSeries)
def admin_series(
    range: str = Query("30d"),
    group_by: str = Query("model"),
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
    users: UserStore = Depends(get_user_store),
) -> UsageSeries:
    gb = group_by if group_by in ("model", "user", "none") else "model"
    start, end = _resolve_range(range)
    names = _username_map(users) if gb == "user" else None
    out = _series(store, start, end, user_id=None, group_by=gb, usernames=names)
    out.range = range
    return out


@router.get("/admin/users", response_model=UserUsageList)
def admin_users(
    range: str = Query("30d"),
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
    users: UserStore = Depends(get_user_store),
) -> UserUsageList:
    start, end = _resolve_range(range)
    pricing = merged_pricing(store)
    names = _username_map(users)
    acc: dict[int, dict[str, Any]] = {}
    for r in store.aggregate_by_user_model(start, end):
        slot = acc.setdefault(
            r["user_id"],
            {"total": 0, "prompt": 0, "completion": 0, "count": 0, "cost": 0.0},
        )
        slot["total"] += r["total_tokens"]
        slot["prompt"] += r["prompt_tokens"]
        slot["completion"] += r["completion_tokens"]
        slot["count"] += r["count"]
        slot["cost"] += cost_of(r["model_id"], r["prompt_tokens"], r["completion_tokens"], pricing)
    items = [
        UserUsage(
            user_id=uid, username=names.get(uid, f"#{uid}"),
            total_tokens=v["total"], prompt_tokens=v["prompt"],
            completion_tokens=v["completion"], count=v["count"], cost=round(v["cost"], 6),
        )
        for uid, v in acc.items()
    ]
    items.sort(key=lambda x: x.total_tokens, reverse=True)
    return UserUsageList(users=items, currency=_cfg.USAGE_CURRENCY)


@router.get("/admin/events", response_model=UsageEvents)
def admin_events(
    range: str = Query("30d"),
    user_id: int | None = Query(None),
    model_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
    users: UserStore = Depends(get_user_store),
) -> UsageEvents:
    start, end = _resolve_range(range)
    return _events(
        store, start, end, user_id, model_id, limit, offset,
        usernames=_username_map(users), with_user=True,
    )


@router.get("/admin/events.csv")
def admin_events_csv(
    range: str = Query("30d"),
    user_id: int | None = Query(None),
    model_id: str | None = Query(None),
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
    users: UserStore = Depends(get_user_store),
) -> Response:
    start, end = _resolve_range(range)
    data = _events(
        store, start, end, user_id, model_id, limit=100_000, offset=0,
        usernames=_username_map(users), with_user=True,
    )
    csv_text = _events_csv(data, with_user=True)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="usage_all_{range}.csv"'},
    )


# ── 单价配置 ──────────────────────────────────────────────────────────────────


@router.get("/pricing", response_model=PricingResponse)
def get_pricing(
    _: dict = Depends(get_current_user),
    store: UsageStore = Depends(get_usage_store),
) -> PricingResponse:
    """当前生效单价（默认 ← 覆盖 合并）+ 模型元数据，全员可读。"""
    overrides = store.get_pricing_overrides()
    items: list[PricingItem] = []
    for mid, m in _cfg.MODEL_CONFIGS.items():
        pin, pout = overrides.get(mid, _cfg.MODEL_PRICING_DEFAULTS.get(mid, (0.0, 0.0)))
        prov = _cfg.PROVIDER_CONFIGS.get(m.provider)
        items.append(PricingItem(
            model_id=mid, label=m.label or mid, provider=m.provider,
            provider_label=(prov.label if prov else m.provider) or m.provider,
            tier=m.tier, input_price=pin, output_price=pout,
            is_override=mid in overrides,
        ))
    return PricingResponse(currency=_cfg.USAGE_CURRENCY, items=items)


@router.put("/pricing", response_model=PricingResponse)
def put_pricing(
    req: PricingUpdateRequest,
    _: dict = Depends(require_admin),
    store: UsageStore = Depends(get_usage_store),
) -> PricingResponse:
    """保存单价覆盖（仅 admin）。只接受已知模型 id。"""
    bulk = {
        it.model_id: (it.input_price, it.output_price)
        for it in req.items
        if it.model_id in _cfg.MODEL_CONFIGS
    }
    if bulk:
        store.set_pricing_bulk(bulk)
    return get_pricing(_, store)
