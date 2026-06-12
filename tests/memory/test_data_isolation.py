"""多用户数据隔离 UT：验证各 store 按 user_id 互不串数据。

用 `use_user` 上下文切换当前用户，模拟不同请求归属不同人。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.config as config
from src.core.user_context import current_user_id, use_user
from src.memory.chat_history import ChatHistoryStore
from src.memory.learning_plan_store import LearningPlanStore
from src.memory.quiz_store import QuizStore
from src.memory.srs_store import SRSStore
from src.memory.user_memory import UserMemoryStore


def test_use_user_context_switch() -> None:
    with use_user(7):
        assert current_user_id() == 7
        with use_user(9):
            assert current_user_id() == 9
        assert current_user_id() == 7


class TestLlmPrefsOverride:
    """use_llm_prefs 把当前请求的模型 / thinking 压进 contextvar，退出复位。"""

    def test_model_override_and_reset(self) -> None:
        models = list(config.MODEL_CONFIGS.keys())
        other = next(m for m in models if m != config.ACTIVE_MODEL)
        baseline = config.current_active_model()
        with config.use_llm_prefs(other, thinking_enabled=False, thinking_budget=2048):
            assert config.current_active_model() == other
            # get_active_model 也按覆盖解析
            _, model_cfg = config.get_active_model()
            assert model_cfg is config.MODEL_CONFIGS[other]
        # 退出后复位
        assert config.current_active_model() == baseline

    def test_thinking_override_and_reset(self) -> None:
        assert config.current_thinking_override() is None
        with config.use_llm_prefs(config.ACTIVE_MODEL, thinking_enabled=True, thinking_budget=12345):
            assert config.current_thinking_override() == (True, 12345)
        assert config.current_thinking_override() is None

    def test_nested_isolation(self) -> None:
        models = list(config.MODEL_CONFIGS.keys())
        a, b = models[0], models[1]
        with config.use_llm_prefs(a, thinking_enabled=False, thinking_budget=2048):
            assert config.current_active_model() == a
            with config.use_llm_prefs(b, thinking_enabled=True, thinking_budget=8000):
                assert config.current_active_model() == b
                assert config.current_thinking_override() == (True, 8000)
            assert config.current_active_model() == a


class TestChatHistoryIsolation:
    def test_sessions_scoped_by_user(self, tmp_path: Path) -> None:
        store = ChatHistoryStore(db_path=str(tmp_path / "chat.db"))
        store.create_empty_session("s-a", "A 的会话", user_id=1)
        store.create_empty_session("s-b", "B 的会话", user_id=2)

        a_sessions = {s["session_id"] for s in store.list_sessions(user_id=1)}
        b_sessions = {s["session_id"] for s in store.list_sessions(user_id=2)}
        assert a_sessions == {"s-a"}
        assert b_sessions == {"s-b"}
        store.close()

    def test_owns_session(self, tmp_path: Path) -> None:
        store = ChatHistoryStore(db_path=str(tmp_path / "chat.db"))
        store.create_empty_session("s-a", "", user_id=1)
        assert store.owns_session("s-a", user_id=1) is True
        assert store.owns_session("s-a", user_id=2) is False
        assert store.get_session_owner("s-a") == 1
        store.close()


class TestUserMemoryIsolation:
    def test_memories_scoped_by_user(self, tmp_path: Path) -> None:
        store = UserMemoryStore(db_path=str(tmp_path / "mem.db"))
        store.add("用户A说中文", user_id=1)
        store.add("用户B说英文", user_id=2)

        a = store.load_all(user_id=1)
        b = store.load_all(user_id=2)
        assert len(a) == 1 and a[0]["text"] == "用户A说中文"
        assert len(b) == 1 and b[0]["text"] == "用户B说英文"
        store.close()

    def test_delete_scoped(self, tmp_path: Path) -> None:
        store = UserMemoryStore(db_path=str(tmp_path / "mem.db"))
        mid = store.add("一条记忆", user_id=1)
        # 用户 2 删不掉用户 1 的条目
        assert store.delete(mid, user_id=2) is False
        assert store.delete(mid, user_id=1) is True
        store.close()


class TestLearningPlanIsolation:
    def test_plans_scoped_by_user(self, tmp_path: Path) -> None:
        store = LearningPlanStore(db_path=str(tmp_path / "plan.db"))
        store.create_plan(goal="A 的计划", user_id=1)
        store.create_plan(goal="B 的计划", user_id=2)

        a = store.list_plans(user_id=1)
        b = store.list_plans(user_id=2)
        assert len(a) == 1 and a[0]["goal"] == "A 的计划"
        assert len(b) == 1 and b[0]["goal"] == "B 的计划"
        store.close()

    def test_active_plan_independent_per_user(self, tmp_path: Path) -> None:
        store = LearningPlanStore(db_path=str(tmp_path / "plan.db"))
        store.create_plan(goal="A", set_active=True, user_id=1)
        store.create_plan(goal="B", set_active=True, user_id=2)
        # 两人各自有 active，互不影响
        assert store.get_active(user_id=1)["goal"] == "A"
        assert store.get_active(user_id=2)["goal"] == "B"
        store.close()


class TestQuizIsolation:
    def test_quizzes_scoped_by_user(self, tmp_path: Path) -> None:
        store = QuizStore(db_path=str(tmp_path / "quiz.db"))
        store.create_quiz_set(topic="A quiz", num_questions=3, user_id=1)
        store.create_quiz_set(topic="B quiz", num_questions=3, user_id=2)

        a = store.list_quiz_sets(user_id=1)
        b = store.list_quiz_sets(user_id=2)
        assert len(a) == 1 and a[0]["topic"] == "A quiz"
        assert len(b) == 1 and b[0]["topic"] == "B quiz"
        store.close()


class TestSRSIsolation:
    def test_cards_scoped_by_user(self, tmp_path: Path) -> None:
        store = SRSStore(db_path=str(tmp_path / "srs.db"))
        store.add_card(source_type="manual", front="A 卡", back="a", user_id=1)
        store.add_card(source_type="manual", front="B 卡", back="b", user_id=2)

        a = store.list_cards(user_id=1)
        b = store.list_cards(user_id=2)
        assert len(a) == 1 and a[0]["front"] == "A 卡"
        assert len(b) == 1 and b[0]["front"] == "B 卡"
        store.close()


class TestCascadeDeleteByUser:
    """admin 删用户时各 store 的 delete_all_for_user 只清目标用户，不误伤他人。"""

    def test_chat_history(self, tmp_path: Path) -> None:
        store = ChatHistoryStore(db_path=str(tmp_path / "chat.db"))
        store.create_empty_session("s-a", "A", user_id=1)
        store.append("s-a", {"role": "user", "content": "hi"}, user_id=1)
        store.create_empty_session("s-b", "B", user_id=2)
        store.delete_all_for_user(1)
        assert store.list_sessions(user_id=1) == []
        assert store.load("s-a") == []
        assert len(store.list_sessions(user_id=2)) == 1
        store.close()

    def test_learning_plan(self, tmp_path: Path) -> None:
        store = LearningPlanStore(db_path=str(tmp_path / "plan.db"))
        store.create_plan(goal="A", user_id=1)
        store.create_plan(goal="B", user_id=2)
        store.delete_all_for_user(1)
        assert store.list_plans(user_id=1) == []
        assert len(store.list_plans(user_id=2)) == 1
        store.close()

    def test_quiz(self, tmp_path: Path) -> None:
        store = QuizStore(db_path=str(tmp_path / "quiz.db"))
        store.create_quiz_set(topic="A", num_questions=3, user_id=1)
        store.create_quiz_set(topic="B", num_questions=3, user_id=2)
        store.delete_all_for_user(1)
        assert store.list_quiz_sets(user_id=1) == []
        assert len(store.list_quiz_sets(user_id=2)) == 1
        store.close()

    def test_srs(self, tmp_path: Path) -> None:
        store = SRSStore(db_path=str(tmp_path / "srs.db"))
        store.add_card(source_type="manual", front="A", back="a", user_id=1)
        store.add_card(source_type="manual", front="B", back="b", user_id=2)
        store.delete_all_for_user(1)
        assert store.list_cards(user_id=1) == []
        assert len(store.list_cards(user_id=2)) == 1
        store.close()
