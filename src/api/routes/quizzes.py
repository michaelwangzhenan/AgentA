"""
Quiz 端点：测验集查询与页面写操作（答题批改 / 归档；与 LLM quiz 工具同 store）。

- GET /api/quizzes：列出当前用户测验集
- GET /api/quizzes/{quiz_set_id}：测验集详情（含题目与作答）
- POST /api/quizzes/{quiz_set_id}/submit：提交作答并批改（简答可走 LLM judge）
- POST /api/quizzes/{quiz_set_id}/archive：归档测验集
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user, get_quiz_store
from src.api.permissions import require_write
from src.api.schemas.quiz import (
    QuizListResponse,
    QuizSet,
    QuizSetSummary,
    SubmitQuizRequest,
)
from src.stores.quiz_store import QuizStore

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


@router.get("", response_model=QuizListResponse)
def list_quizzes(
    store: QuizStore = Depends(get_quiz_store),
    user: dict = Depends(get_current_user),
) -> QuizListResponse:
    rows = store.list_quiz_sets(include_archived=False, user_id=user["id"])
    return QuizListResponse(quizzes=[QuizSetSummary(**row) for row in rows])


@router.get("/{quiz_set_id}", response_model=QuizSet)
def get_quiz(
    quiz_set_id: int,
    store: QuizStore = Depends(get_quiz_store),
    user: dict = Depends(get_current_user),
) -> QuizSet:
    quiz = store.get_quiz_with_questions(quiz_set_id, user_id=user["id"])
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"quiz id={quiz_set_id} 不存在",
        )
    return QuizSet(**quiz)


# ─── 写端点（页面内操作）────────────────────────────────────────────────


@router.post("/{quiz_set_id}/submit", response_model=QuizSet)
def submit_quiz(
    quiz_set_id: int,
    req: SubmitQuizRequest,
    store: QuizStore = Depends(get_quiz_store),
    user: dict = Depends(require_write("memory")),
) -> QuizSet:
    """提交答案批改：选择题本地字符串比对，简答走 LLM-judge；落库后返回带分数的 quiz。"""
    # 复用 tools.py 的批改 helper，避免重复实现两套评分逻辑（详 §2.5 决策）。
    from src.agent.tools import _MCQ_TYPES, _grade_one_mcq, _grade_one_short_answer

    quiz = store.get_quiz_with_questions(quiz_set_id, user_id=user["id"])
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"quiz id={quiz_set_id} 不存在",
        )
    if quiz["status"] == "archived":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"quiz id={quiz_set_id} 已归档，无法批改",
        )
    questions = quiz["questions"]
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"quiz id={quiz_set_id} 没有题目，无法批改",
        )

    answer_map = {a.question_id: a.answer for a in req.answers}
    gradings: list[dict] = []
    total_raw = 0.0
    for q in questions:
        user_ans = answer_map.get(q["id"], "")
        if q["q_type"] in _MCQ_TYPES:
            score, fb = _grade_one_mcq(user_ans, q["correct_answer"])
        else:
            score, fb = _grade_one_short_answer(q["stem"], user_ans, q["correct_answer"])
        gradings.append({
            "question_id": q["id"], "user_answer": user_ans,
            "score": score, "feedback": fb,
        })
        total_raw += score

    total_score = round(total_raw * 100.0 / len(questions), 1)
    if not store.update_grading(quiz_set_id, gradings, total_score, user_id=user["id"]):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"持久化批改结果失败（quiz id={quiz_set_id}）",
        )
    return get_quiz(quiz_set_id, store, user)


@router.post("/{quiz_set_id}/archive", response_model=QuizSet)
def archive_quiz(
    quiz_set_id: int,
    store: QuizStore = Depends(get_quiz_store),
    user: dict = Depends(require_write("memory")),
) -> QuizSet:
    """归档 quiz（软删除，不再出现在默认列表）。"""
    if not store.archive_quiz_set(quiz_set_id, user_id=user["id"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法归档 quiz id={quiz_set_id}（不存在或已归档）",
        )
    return get_quiz(quiz_set_id, store, user)
