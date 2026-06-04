"""Quiz 只读端点（list / detail）。

跟 LLM `create_quiz` / `grade_quiz` / `query_quiz_history` 工具同 store；
UI 不提供答题 / 批改（多轮对话型任务，留给 chat）。
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_quiz_store
from src.api.schemas.quiz import QuizListResponse, QuizSet, QuizSetSummary
from src.memory.quiz_store import QuizStore

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


@router.get("", response_model=QuizListResponse)
def list_quizzes(
    store: QuizStore = Depends(get_quiz_store),
) -> QuizListResponse:
    rows = store.list_quiz_sets(include_archived=False)
    return QuizListResponse(quizzes=[QuizSetSummary(**row) for row in rows])


@router.get("/{quiz_set_id}", response_model=QuizSet)
def get_quiz(
    quiz_set_id: int,
    store: QuizStore = Depends(get_quiz_store),
) -> QuizSet:
    quiz = store.get_quiz_with_questions(quiz_set_id)
    if quiz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"quiz id={quiz_set_id} 不存在",
        )
    return QuizSet(**quiz)
