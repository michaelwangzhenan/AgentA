"""SRS（间隔重复）只读端点（due / list / detail）。

跟 LLM `query_srs_due` / `review_srs_card` / `add_to_srs` 工具同 store；
UI 不提供 4 档评分（多轮对话型任务，留给 chat）。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_srs_store
from src.api.schemas.srs import SRSCard, SRSCardListResponse
from src.memory.srs_store import SRSStore

router = APIRouter(prefix="/srs", tags=["srs"])


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
    card = store.get_card(card_id)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"srs card id={card_id} 不存在",
        )
    return SRSCard(**card)
