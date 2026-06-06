"""学习计划端点：只读查询（list / active / detail）+ 页面写操作（新建 / 改任务 / 激活 / 放弃）。

跟 LLM `create_study_plan` / `update_study_progress` / `query_study_status` 工具同 store。
写端点对应"学而时习"页面内的手动操作；都是纯本地 SQLite 写，不调大模型。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_plan_store
from src.api.schemas.plan import (
    CreatePlanRequest,
    Plan,
    PlanListResponse,
    PlanSummary,
    UpdateTaskRequest,
)
from src.memory.learning_plan_store import LearningPlanStore

router = APIRouter(prefix="/plans", tags=["plans"])


def _load_plan_or_404(store: LearningPlanStore, plan_id: int) -> Plan:
    """读 plan + tasks，不存在抛 404；写端点统一用它回填最新状态。"""
    plan = store.get_plan_with_tasks(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plan id={plan_id} 不存在",
        )
    return Plan(**plan)


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
    return _load_plan_or_404(store, plan_id)


# ─── 写端点（页面内操作）────────────────────────────────────────────────


@router.post("", response_model=Plan, status_code=status.HTTP_201_CREATED)
def create_plan(
    req: CreatePlanRequest,
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan:
    """手动新建计划并设为 active；可同时带阶段任务。"""
    try:
        plan_id = store.create_plan(goal=req.goal, weeks=req.weeks, set_active=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if req.tasks:
        store.add_tasks(plan_id, [t.model_dump() for t in req.tasks])
    return _load_plan_or_404(store, plan_id)


@router.patch("/{plan_id}/tasks/{task_id}", response_model=Plan)
def update_task(
    plan_id: int,
    task_id: int,
    req: UpdateTaskRequest,
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan:
    """更新任务状态 / 备注（pending / success / skipped）。"""
    ok = store.update_task_status(plan_id, task_id, req.status, req.note)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"更新失败：task_id={task_id} 不属于 plan_id={plan_id}，或 status={req.status!r} 非法",
        )
    return _load_plan_or_404(store, plan_id)


@router.post("/{plan_id}/activate", response_model=Plan)
def activate_plan(
    plan_id: int,
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan:
    """把指定 plan 切为当前 active（其余自动取消 active）。"""
    ok = store.switch_active(plan_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法激活 plan_id={plan_id}（不存在或已放弃）",
        )
    return _load_plan_or_404(store, plan_id)


@router.post("/{plan_id}/abandon", response_model=Plan)
def abandon_plan(
    plan_id: int,
    store: LearningPlanStore = Depends(get_plan_store),
) -> Plan:
    """放弃计划（标记 abandoned + 取消 active）。"""
    ok = store.abandon_plan(plan_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plan id={plan_id} 不存在",
        )
    return _load_plan_or_404(store, plan_id)
