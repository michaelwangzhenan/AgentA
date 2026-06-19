"""Quiz 端点请求 / 响应模型"""

from pydantic import BaseModel, Field


class QuizQuestion(BaseModel):
    id: int
    quiz_set_id: int
    order_idx: int
    q_type: str
    stem: str
    options: list[str] = []
    correct_answer: str | None = None
    explanation: str | None = None
    user_answer: str | None = None
    score: float = 0.0
    feedback: str | None = None
    critic_flagged: bool = False


class QuizSetSummary(BaseModel):
    id: int
    topic: str
    plan_id: int | None = None
    stage_idx: int | None = None
    num_questions: int
    status: str
    total_score: float | None = None
    created_at: str
    graded_at: str | None = None
    updated_at: str


class QuizSet(BaseModel):
    id: int
    topic: str
    plan_id: int | None = None
    stage_idx: int | None = None
    num_questions: int
    status: str
    total_score: float | None = None
    created_at: str
    graded_at: str | None = None
    updated_at: str
    questions: list[QuizQuestion]


class QuizListResponse(BaseModel):
    quizzes: list[QuizSetSummary]


# ─── 写端点请求体 ─────────────────────────────────────────────────────────


class QuizAnswer(BaseModel):
    question_id: int
    answer: str = ""


class SubmitQuizRequest(BaseModel):
    answers: list[QuizAnswer] = Field(default_factory=list)
