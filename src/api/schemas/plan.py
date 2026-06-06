"""学习计划端点请求 / 响应模型"""

from pydantic import BaseModel, Field


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


# ─── 写端点请求体 ─────────────────────────────────────────────────────────


class CreatePlanTask(BaseModel):
    stage_idx: int = Field(ge=1)
    order_idx: int = Field(ge=1)
    title: str = Field(min_length=1)


class CreatePlanRequest(BaseModel):
    goal: str = Field(min_length=1)
    weeks: int = Field(default=0, ge=0)
    tasks: list[CreatePlanTask] = Field(default_factory=list)


class UpdateTaskRequest(BaseModel):
    # 合法值：pending / success / skipped（由 store 二次校验）
    status: str
    note: str = ""
