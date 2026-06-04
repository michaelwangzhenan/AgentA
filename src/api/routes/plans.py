"""学习计划只读端点（list / active / detail）。

跟 LLM `create_study_plan` / `update_study_progress` / `query_study_status` 工具同 store；
不在 UI 暴露新建 / 修改路径（业务编排让 LLM 走完整流程）。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_plan_store
from src.api.schemas.plan import Plan, PlanListResponse, PlanSummary
from src.memory.learning_plan_store import LearningPlanStore

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=PlanListResponse)
def list_plans(
    store: LearningPlanStore = Depends(get_plan_store),
) -> PlanListResponse:
    rows = store.list_plans(include_abandoned=False)
    return PlanListResponse(plans=[PlanSummary(**row) for row in rows])


@router.get("/active", response_model=Plan | None)
def get_active_plan(
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan | None:
    plan = store.get_active()
    if plan is None:
        return None
    return Plan(**plan)


@router.get("/{plan_id}", response_model=Plan)
def get_plan(
    plan_id: int,
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan:
    plan = store.get_plan_with_tasks(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plan id={plan_id} 不存在",
        )
    return Plan(**plan)
