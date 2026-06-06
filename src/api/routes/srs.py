"""SRS（间隔重复）端点：只读查询（due / list / detail）+ 页面写操作（建卡 / 评分 / 状态流转）。

跟 LLM `query_srs_due` / `review_srs_card` / `add_to_srs` 工具同 store。
评分走 SM-2 纯函数（`src.agent.core.srs_scheduler`），全程本地计算，不调大模型。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.agent.core.srs_scheduler import card_state_from_dict, parse_rating, schedule_review
from src.api.deps import get_srs_store
from src.api.schemas.srs import (
    CreateCardRequest,
    ReviewCardRequest,
    SRSCard,
    SRSCardListResponse,
)
from src.memory.srs_store import SRSStore

router = APIRouter(prefix="/srs", tags=["srs"])


def _load_card_or_404(store: SRSStore, card_id: int) -> SRSCard:
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"srs card id={card_id} 不存在",
        )
    return SRSCard(**card)


@router.get("/due", response_model=SRSCardListResponse)
def list_due(
    limit: int | None = None,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCardListResponse:
    rows = store.list_due(limit=limit)
    return SRSCardListResponse(cards=[SRSCard(**row) for row in rows])


@router.get("/cards", response_model=SRSCardListResponse)
def list_cards(
    store: SRSStore = Depends(get_srs_store),
) -> SRSCardListResponse:
    rows = store.list_cards()
    return SRSCardListResponse(cards=[SRSCard(**row) for row in rows])


@router.get("/cards/{card_id}", response_model=SRSCard)
def get_card(
    card_id: int,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    return _load_card_or_404(store, card_id)


# ─── 写端点（页面内操作）────────────────────────────────────────────────


@router.post("/cards", response_model=SRSCard, status_code=status.HTTP_201_CREATED)
def create_card(
    req: CreateCardRequest,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    """手动新建一张复习卡（正面 front / 背面 back）。新卡立即 due。"""
    try:
        card_id = store.add_card(
            source_type="manual", front=req.front, back=req.back,
            source_ref=None, note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _load_card_or_404(store, card_id)


@router.post("/cards/{card_id}/review", response_model=SRSCard)
def review_card(
    card_id: int,
    req: ReviewCardRequest,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    """4 档评分（again / hard / good / easy）→ 跑 SM-2 → 写回下次复习时间。"""
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"srs card id={card_id} 不存在",
        )
    if card["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"card id={card_id} 状态为 {card['status']}，只有 active 卡可复习",
        )
    try:
        rating = parse_rating(req.rating)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    result = schedule_review(card_state_from_dict(card), rating)
    store.update_review_state(
        card_id,
        ease_factor=result.ease_factor,
        interval_days=result.interval_days,
        repetitions=result.repetitions,
        lapses=result.lapses,
        next_review_at=result.next_review_at,
    )
    return _load_card_or_404(store, card_id)


@router.post("/cards/{card_id}/suspend", response_model=SRSCard)
def suspend_card(
    card_id: int,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    """暂停卡片（不再出现在 due 队列，可恢复）。"""
    if not store.suspend(card_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"srs card id={card_id} 不存在",
        )
    return _load_card_or_404(store, card_id)


@router.post("/cards/{card_id}/resume", response_model=SRSCard)
def resume_card(
    card_id: int,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    """从 suspended 恢复为 active。"""
    if not store.resume(card_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"card id={card_id} 不存在或当前不是 suspended，无法恢复",
        )
    return _load_card_or_404(store, card_id)


@router.post("/cards/{card_id}/archive", response_model=SRSCard)
def archive_card(
    card_id: int,
    store: SRSStore = Depends(get_srs_store),
) -> SRSCard:
    """归档卡片（软删除，不再出现在默认列表）。"""
    if not store.archive(card_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"srs card id={card_id} 不存在",
        )
    return _load_card_or_404(store, card_id)
