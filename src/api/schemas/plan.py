"""学习计划端点响应模型"""

from pydantic import BaseModel


class PlanTask(BaseModel):
    id: int
    plan_id: int
    stage_idx: int
    order_idx: int
    title: str
    status: str
    note: str | None = None
    completed_at: str | None = None


class PlanSummary(BaseModel):
    id: int
    goal: str
    weeks: int
    status: str
    is_active: bool
    created_at: str
    updated_at: str
    task_count: int = 0
    done_count: int = 0


class Plan(BaseModel):
    id: int
    goal: str
    weeks: int
    status: str
    is_active: bool
    created_at: str
    updated_at: str
    tasks: list[PlanTask]


class PlanListResponse(BaseModel):
    plans: list[PlanSummary]
