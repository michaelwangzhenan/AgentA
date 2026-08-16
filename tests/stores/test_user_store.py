"""UserStore UT：账号 / 密码哈希 / 登录态 / 每用户 rules。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.stores.user_store import ROLE_ADMIN, ROLE_USER, UserStore


@pytest.fixture
def store(tmp_path: Path) -> UserStore:
    s = UserStore(db_path=str(tmp_path / "auth.db"))
    yield s
    s.close()


class TestAccount:
    def test_create_and_default_role(self, store: UserStore) -> None:
        user = store.create_user("alice", "pw123")
        assert user is not None
        assert user["username"] == "alice"
        assert user["role"] == ROLE_USER

    def test_create_admin_role(self, store: UserStore) -> None:
        user = store.create_user("root", "pw", role=ROLE_ADMIN)
        assert user["role"] == ROLE_ADMIN

    def test_duplicate_username_rejected(self, store: UserStore) -> None:
        assert store.create_user("bob", "x") is not None
        assert store.create_user("bob", "y") is None

    def test_empty_username_or_password_rejected(self, store: UserStore) -> None:
        assert store.create_user("", "x") is None
        assert store.create_user("u", "") is None

    def test_password_not_stored_plaintext(self, store: UserStore) -> None:
        store.create_user("carol", "secret")
        row = store._conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", ("carol",)
        ).fetchone()
        assert row["password_hash"] != "secret"


class TestCaseInsensitiveUsername:
    def test_duplicate_ignores_case(self, store: UserStore) -> None:
        assert store.create_user("Admin", "x") is not None
        # admin / ADMIN 视为同一占用
        assert store.create_user("admin", "y") is None
        assert store.create_user("ADMIN", "z") is None

    def test_login_ignores_case(self, store: UserStore) -> None:
        store.create_user("Michael", "pw")
        assert store.verify_password("michael", "pw") is not None
        assert store.verify_password("MICHAEL", "pw") is not None

    def test_lookup_ignores_case(self, store: UserStore) -> None:
        store.create_user("Bob", "pw")
        assert store.get_user_by_username("bob") is not None

    def test_rename_to_existing_case_variant_rejected(self, store: UserStore) -> None:
        store.create_user("taken", "pw")
        u = store.create_user("me", "pw")
        assert store.update_username(u["id"], "TAKEN") == "taken"


class TestPasswordVerify:
    def test_correct_password(self, store: UserStore) -> None:
        store.create_user("dave", "right")
        assert store.verify_password("dave", "right") is not None

    def test_wrong_password(self, store: UserStore) -> None:
        store.create_user("dave", "right")
        assert store.verify_password("dave", "wrong") is None

    def test_unknown_user(self, store: UserStore) -> None:
        assert store.verify_password("ghost", "x") is None


class TestSession:
    def test_create_and_lookup(self, store: UserStore) -> None:
        user = store.create_user("eve", "pw")
        token = store.create_session(user["id"], ttl_days=7)
        found = store.get_user_by_token(token)
        assert found is not None
        assert found["id"] == user["id"]

    def test_invalid_token(self, store: UserStore) -> None:
        assert store.get_user_by_token("nope") is None
        assert store.get_user_by_token("") is None

    def test_delete_session(self, store: UserStore) -> None:
        user = store.create_user("frank", "pw")
        token = store.create_session(user["id"], ttl_days=7)
        store.delete_session(token)
        assert store.get_user_by_token(token) is None

    def test_expired_session_rejected(self, store: UserStore) -> None:
        user = store.create_user("grace", "pw")
        token = store.create_session(user["id"], ttl_days=7)
        # 手动把过期时间改到过去
        past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        with store._conn:
            store._conn.execute(
                "UPDATE auth_sessions SET expires_at = ? WHERE token = ?", (past, token)
            )
        assert store.get_user_by_token(token) is None


class TestUpdateUsername:
    def test_ok(self, store: UserStore) -> None:
        u = store.create_user("oldname", "pw")
        assert store.update_username(u["id"], "newname") == "ok"
        assert store.get_user_by_id(u["id"])["username"] == "newname"

    def test_taken(self, store: UserStore) -> None:
        store.create_user("taken", "pw")
        u = store.create_user("me", "pw")
        assert store.update_username(u["id"], "taken") == "taken"

    def test_empty_invalid(self, store: UserStore) -> None:
        u = store.create_user("me", "pw")
        assert store.update_username(u["id"], "  ") == "invalid"

    def test_notfound(self, store: UserStore) -> None:
        assert store.update_username(9999, "x") == "notfound"


class TestUpdatePassword:
    def test_ok_then_login_with_new(self, store: UserStore) -> None:
        u = store.create_user("pwuser", "old")
        assert store.update_password(u["id"], "old", "new") == "ok"
        assert store.verify_password("pwuser", "new") is not None
        assert store.verify_password("pwuser", "old") is None

    def test_wrong_old(self, store: UserStore) -> None:
        u = store.create_user("pwuser", "old")
        assert store.update_password(u["id"], "bad", "new") == "wrong_old"

    def test_empty_new_invalid(self, store: UserStore) -> None:
        u = store.create_user("pwuser", "old")
        assert store.update_password(u["id"], "old", "") == "invalid"


class TestUserManagement:
    def test_list_users_sorted(self, store: UserStore) -> None:
        store.create_user("a", "pw")
        store.create_user("b", "pw")
        users = store.list_users()
        assert [u["username"] for u in users] == ["a", "b"]

    def test_count_admins(self, store: UserStore) -> None:
        store.create_user("u", "pw")
        store.create_user("root", "pw", role=ROLE_ADMIN)
        assert store.count_admins() == 1

    def test_delete_user_removes_account_session_rules(self, store: UserStore) -> None:
        u = store.create_user("victim", "pw")
        token = store.create_session(u["id"], ttl_days=7)
        store.set_rules(u["id"], "some rules")
        assert store.delete_user(u["id"]) is True
        assert store.get_user_by_id(u["id"]) is None
        assert store.get_user_by_token(token) is None
        assert store.get_rules(u["id"]) == ""

    def test_delete_unknown_returns_false(self, store: UserStore) -> None:
        assert store.delete_user(9999) is False

    def test_update_role(self, store: UserStore) -> None:
        u = store.create_user("roleuser", "pw")
        assert u is not None
        assert store.update_role(u["id"], ROLE_ADMIN) is True
        updated = store.get_user_by_id(u["id"])
        assert updated is not None
        assert updated["role"] == ROLE_ADMIN
        assert store.update_role(u["id"], "nope") is False
        assert store.update_role(9999, ROLE_USER) is False


class TestLlmSettings:
    def test_default_all_none(self, store: UserStore) -> None:
        s = store.get_settings(1)
        assert s == {"active_model": None, "thinking_enabled": None, "thinking_budget": None}

    def test_set_and_get(self, store: UserStore) -> None:
        store.set_settings(1, active_model="kimi-k2.5", thinking_enabled=True, thinking_budget=8000)
        s = store.get_settings(1)
        assert s["active_model"] == "kimi-k2.5"
        assert s["thinking_enabled"] is True
        assert s["thinking_budget"] == 8000

    def test_partial_update_keeps_other_fields(self, store: UserStore) -> None:
        store.set_settings(1, active_model="kimi-k2.5", thinking_enabled=True, thinking_budget=8000)
        # 只改模型，thinking 不动
        store.set_settings(1, active_model="deepseek-v4-flash")
        s = store.get_settings(1)
        assert s["active_model"] == "deepseek-v4-flash"
        assert s["thinking_enabled"] is True
        assert s["thinking_budget"] == 8000

    def test_thinking_off_persists_as_false_not_none(self, store: UserStore) -> None:
        store.set_settings(1, thinking_enabled=True, thinking_budget=2048)
        store.set_settings(1, thinking_enabled=False)
        s = store.get_settings(1)
        assert s["thinking_enabled"] is False
        assert s["thinking_budget"] == 2048

    def test_per_user_isolated(self, store: UserStore) -> None:
        store.set_settings(1, active_model="kimi-k2.5")
        store.set_settings(2, active_model="deepseek-v4-flash")
        assert store.get_settings(1)["active_model"] == "kimi-k2.5"
        assert store.get_settings(2)["active_model"] == "deepseek-v4-flash"

    def test_delete_user_clears_settings(self, store: UserStore) -> None:
        u = store.create_user("settingsuser", "pw")
        store.set_settings(u["id"], active_model="kimi-k2.5")
        store.delete_user(u["id"])
        assert store.get_settings(u["id"])["active_model"] is None


class TestRules:
    def test_default_empty(self, store: UserStore) -> None:
        assert store.get_rules(1) == ""

    def test_set_and_get(self, store: UserStore) -> None:
        store.set_rules(1, "用中文回答")
        assert store.get_rules(1) == "用中文回答"

    def test_per_user_isolated(self, store: UserStore) -> None:
        store.set_rules(1, "user1 rules")
        store.set_rules(2, "user2 rules")
        assert store.get_rules(1) == "user1 rules"
        assert store.get_rules(2) == "user2 rules"

    def test_upsert_overwrites(self, store: UserStore) -> None:
        store.set_rules(1, "v1")
        store.set_rules(1, "v2")
        assert store.get_rules(1) == "v2"
