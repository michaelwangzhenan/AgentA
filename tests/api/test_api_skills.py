"""Skills 端点 UT。

用 monkeypatch 改 DEFAULT_SKILLS_DIR + SKILLS_DISABLED_FILE 指到 tmp_path 构造的目录树。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.agent.core.skill_loader as skill_loader
import src.config as _cfg
from src.api.deps import get_agent
from src.api.main import app


def _write_skill(dir_path: Path, name: str, description: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_invalid_skill(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # skills 目录 + disabled.json 都指向 tmp_path
    monkeypatch.setattr(skill_loader, "DEFAULT_SKILLS_DIR", tmp_path)
    disabled_file = tmp_path / "skills_disabled.json"
    monkeypatch.setattr(_cfg, "SKILLS_DISABLED_FILE", str(disabled_file))
    # 每个测试前清掉 agent 缓存：避免上一条测试构造的 agent 缓存影响本测
    get_agent.cache_clear()
    yield TestClient(app)
    get_agent.cache_clear()


def test_skills_empty_dir(client: TestClient) -> None:
    r = client.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == {"loaded": [], "disabled": [], "failed": []}


def test_skills_loaded(client: TestClient, tmp_path: Path) -> None:
    _write_skill(tmp_path / "skill_a", "skill_a", "测试技能 A", "skill body A")
    _write_skill(tmp_path / "skill_b", "skill_b", "测试技能 B", "skill body B")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body["loaded"]) == 2
    assert body["failed"] == []
    names = {s["name"] for s in body["loaded"]}
    assert names == {"skill_a", "skill_b"}


def test_skills_loaded_returns_body(client: TestClient, tmp_path: Path) -> None:
    """P0：GET /api/skills 必须返回 body 字段，前端展开看 body 才有内容。"""
    _write_skill(tmp_path / "demo", "demo", "演示 skill", "## 步骤 1\n做点事情")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body["loaded"]) == 1
    item = body["loaded"][0]
    assert "body" in item, "GET /api/skills 必须暴露 body 字段供 UI 展开查看"
    assert "步骤 1" in item["body"]
    assert "做点事情" in item["body"]


def test_skills_failed(client: TestClient, tmp_path: Path) -> None:
    _write_invalid_skill(tmp_path / "broken")
    _write_skill(tmp_path / "good", "good", "OK skill", "body")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body["loaded"]) == 1
    assert body["loaded"][0]["name"] == "good"
    assert len(body["failed"]) == 1
    assert "missing_frontmatter" in body["failed"][0]["reason"]


# ---------------------------------------------------------------------------
# P0：POST /api/skills/reload
# ---------------------------------------------------------------------------
class TestSkillsReload:
    """reload 端点 = 重新扫盘 + 清 Agent 单例缓存。"""

    def test_reload_empty_dir_returns_counts(self, client: TestClient) -> None:
        r = client.post("/api/skills/reload")
        assert r.status_code == 200
        assert r.json() == {"loaded_count": 0, "disabled_count": 0, "failed_count": 0}

    def test_reload_counts_match_scan(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _write_skill(tmp_path / "ok", "ok", "OK", "body")
        _write_invalid_skill(tmp_path / "broken")

        r = client.post("/api/skills/reload")
        assert r.status_code == 200
        assert r.json() == {"loaded_count": 1, "disabled_count": 0, "failed_count": 1}

    def test_reload_clears_agent_cache(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """reload 必须 cache_clear，否则磁盘新内容下一轮 chat 看不到。"""
        # 触发一次构造 → 进入 lru_cache
        _write_skill(tmp_path / "alpha", "alpha", "first", "body-a")
        agent_before = get_agent()

        # 改盘 + reload
        _write_skill(tmp_path / "beta", "beta", "second", "body-b")
        r = client.post("/api/skills/reload")
        assert r.status_code == 200

        # cache 应已清空 → 下次 get_agent() 是新实例
        agent_after = get_agent()
        assert agent_after is not agent_before, (
            "reload 必须清 get_agent lru_cache，否则下一轮对话仍用旧 catalog"
        )

    def test_reload_picks_up_new_skill_on_disk(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """reload 后 GET /api/skills 立即列出新加的 skill。"""
        _write_skill(tmp_path / "old", "old", "first", "body")
        r1 = client.get("/api/skills")
        names1 = {s["name"] for s in r1.json()["loaded"]}
        assert names1 == {"old"}

        # 磁盘加一个新 skill 然后 reload
        _write_skill(tmp_path / "new_one", "new_one", "second", "body")
        client.post("/api/skills/reload")

        r2 = client.get("/api/skills")
        names2 = {s["name"] for s in r2.json()["loaded"]}
        assert names2 == {"old", "new_one"}


# ---------------------------------------------------------------------------
# P0：get_agent() 必须真正加载 Skills（修 pre-existing bug）
# ---------------------------------------------------------------------------
class TestGetAgentLoadsSkills:
    """`api/deps.py:get_agent()` 之前漏传 skills，导致 Web UI 启动的 Agent
    看不到 .agenta/skills/ 内容。本测试集守护该回路。"""

    def test_agent_loads_skills_from_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_agent() 构造时必须扫描 .agenta/skills/ 并把 skills 传给 Agent。"""
        monkeypatch.setattr(skill_loader, "DEFAULT_SKILLS_DIR", tmp_path)
        _write_skill(tmp_path / "demo", "demo", "演示", "skill body")

        get_agent.cache_clear()
        try:
            agent = get_agent()
            # Agent 实例内部应有 _skill_bodies；含本 skill
            assert hasattr(agent, "_skill_bodies"), (
                "Agent 必须暴露 _skill_bodies 字段（load_skill 工具的数据源）"
            )
            assert "demo" in agent._skill_bodies, (
                "get_agent() 必须把 scan_skills() 结果传给 Agent；"
                "否则 LLM 看不到 ## Skills catalog 也调不到 load_skill"
            )
            assert agent._skill_bodies["demo"] == "skill body"
        finally:
            get_agent.cache_clear()

    def test_agent_handles_empty_skills_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """skills 目录空时 get_agent() 不能炸。"""
        monkeypatch.setattr(skill_loader, "DEFAULT_SKILLS_DIR", tmp_path)
        get_agent.cache_clear()
        try:
            agent = get_agent()
            assert agent._skill_bodies == {}
        finally:
            get_agent.cache_clear()


# ===========================================================================
# P1: CRUD（创建 / 更新 / 删除）
# ===========================================================================
class TestCreateSkill:
    """POST /api/skills 创建 skill。"""

    def test_create_success(self, client: TestClient, tmp_path: Path) -> None:
        r = client.post(
            "/api/skills",
            json={"name": "my_skill", "description": "演示", "body": "## 步骤\n做事"},
        )
        assert r.status_code == 201
        item = r.json()
        assert item["name"] == "my_skill"
        assert item["description"] == "演示"
        # 磁盘上应有目录 + SKILL.md
        skill_md = tmp_path / "my_skill" / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        assert "name: my_skill" in text
        assert "description: 演示" in text
        assert "## 步骤" in text

    def test_create_invalid_name_with_dot(self, client: TestClient) -> None:
        """name 含 . 等非法字符应被拒，防路径注入。"""
        r = client.post(
            "/api/skills",
            json={"name": "bad.name", "description": "x", "body": ""},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_create_invalid_name_with_slash(self, client: TestClient, tmp_path: Path) -> None:
        """name 含 / 应被拒（路径注入防御）。"""
        r = client.post(
            "/api/skills",
            json={"name": "../etc", "description": "x", "body": ""},
        )
        # name 含 . 已经 fails，..在外面的目录不会被创建
        assert r.status_code == 400
        # 防御性：tmp_path 之外不应该出现 etc 目录
        assert not (tmp_path.parent / "etc").exists()

    def test_create_already_exists(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "dupe", "dupe", "first", "body")
        r = client.post(
            "/api/skills",
            json={"name": "dupe", "description": "second", "body": "body2"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "already_exists"

    def test_create_empty_description(self, client: TestClient) -> None:
        r = client.post(
            "/api/skills",
            json={"name": "x", "description": "  ", "body": ""},
        )
        # pydantic min_length=1 校验先生效（422）；如果允许空格通过 pydantic，则后端 400
        assert r.status_code in (400, 422)

    def test_create_triggers_reload(self, client: TestClient, tmp_path: Path) -> None:
        """创建后 cache 应被清，下次 get_agent 重建实例时含新 skill。"""
        agent_before = get_agent()
        r = client.post(
            "/api/skills",
            json={"name": "fresh", "description": "新", "body": "B"},
        )
        assert r.status_code == 201
        agent_after = get_agent()
        assert agent_after is not agent_before
        assert "fresh" in agent_after._skill_bodies


class TestUpdateSkill:
    """PUT /api/skills/{name} 更新现有 skill。"""

    def test_update_success(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "demo", "demo", "old desc", "old body")
        r = client.put(
            "/api/skills/demo",
            json={"description": "new desc", "body": "new body"},
        )
        assert r.status_code == 200
        item = r.json()
        assert item["description"] == "new desc"
        assert "new body" in item["body"]
        # 磁盘也已更新
        text = (tmp_path / "demo" / "SKILL.md").read_text(encoding="utf-8")
        assert "new desc" in text
        assert "new body" in text

    def test_update_not_found(self, client: TestClient) -> None:
        r = client.put(
            "/api/skills/missing",
            json={"description": "x", "body": "y"},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"

    def test_update_preserves_name_in_frontmatter(self, client: TestClient, tmp_path: Path) -> None:
        """改 description / body 后 frontmatter name 必须保持不变。"""
        _write_skill(tmp_path / "stable", "stable", "old", "body")
        client.put("/api/skills/stable", json={"description": "new", "body": "new"})
        text = (tmp_path / "stable" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: stable" in text


class TestDeleteSkill:
    """DELETE /api/skills/{name}"""

    def test_delete_success(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "goner" / "scripts", "goner", "x", "y")
        # 在 scripts 子目录下放点东西模拟真实 skill 目录
        (tmp_path / "goner" / "scripts" / "helper.py").write_text("print('hi')")

        r = client.delete("/api/skills/goner")
        assert r.status_code == 204
        assert not (tmp_path / "goner").exists(), "整个目录应被递归删除（含 scripts/）"

    def test_delete_not_found(self, client: TestClient) -> None:
        r = client.delete("/api/skills/never_existed")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"


# ===========================================================================
# P2: enable / disable toggle + disabled 列表持久化
# ===========================================================================
class TestToggleSkill:
    """POST /api/skills/{name}/toggle"""

    def test_disable_existing_skill(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "demo", "demo", "x", "y")
        r = client.post("/api/skills/demo/toggle", json={"enabled": False})
        assert r.status_code == 200
        assert r.json() == {"name": "demo", "enabled": False}

        # GET 后该 skill 应在 disabled 数组里
        r2 = client.get("/api/skills")
        names_disabled = {s["name"] for s in r2.json()["disabled"]}
        names_loaded = {s["name"] for s in r2.json()["loaded"]}
        assert "demo" in names_disabled
        assert "demo" not in names_loaded

    def test_enable_disabled_skill(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "demo", "demo", "x", "y")
        # 先禁用
        client.post("/api/skills/demo/toggle", json={"enabled": False})
        # 再启用
        r = client.post("/api/skills/demo/toggle", json={"enabled": True})
        assert r.status_code == 200
        assert r.json() == {"name": "demo", "enabled": True}

        r2 = client.get("/api/skills")
        names_loaded = {s["name"] for s in r2.json()["loaded"]}
        names_disabled = {s["name"] for s in r2.json()["disabled"]}
        assert "demo" in names_loaded
        assert "demo" not in names_disabled

    def test_toggle_not_found(self, client: TestClient) -> None:
        r = client.post("/api/skills/missing/toggle", json={"enabled": False})
        assert r.status_code == 404

    def test_toggle_idempotent_already_disabled(self, client: TestClient, tmp_path: Path) -> None:
        """重复禁用同一 skill 应 200 不报错（幂等）。"""
        _write_skill(tmp_path / "demo", "demo", "x", "y")
        client.post("/api/skills/demo/toggle", json={"enabled": False})
        r = client.post("/api/skills/demo/toggle", json={"enabled": False})
        assert r.status_code == 200

    def test_disabled_skill_not_in_agent_catalog(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """被禁用的 skill 不应进 Agent.skill_bodies（即不进 ## Skills catalog）。"""
        _write_skill(tmp_path / "alpha", "alpha", "a", "body-a")
        _write_skill(tmp_path / "beta", "beta", "b", "body-b")

        # 禁用 beta
        client.post("/api/skills/beta/toggle", json={"enabled": False})

        agent = get_agent()
        assert "alpha" in agent._skill_bodies
        assert "beta" not in agent._skill_bodies, (
            "禁用的 skill 不能进 Agent 的 skill_bodies，否则 LLM 仍能调到"
        )


class TestDisabledPersistence:
    """skills/disabled.json 文件读写 + 孤儿自愈"""

    def test_disabled_file_atomic_write(
        self, tmp_path: Path
    ) -> None:
        """write_disabled_list 必须原子写 —— 写完后没有 .tmp 残留。"""
        disabled_file = tmp_path / "skills_disabled.json"
        skill_loader.write_disabled_list({"a", "b"}, disabled_file)
        # 文件应存在且内容是 JSON 数组
        import json as _json
        data = _json.loads(disabled_file.read_text(encoding="utf-8"))
        assert sorted(data) == ["a", "b"]
        # 不应有遗留的 .tmp 文件
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], f"原子写不应留 tmp 文件，发现：{leftovers}"

    def test_disabled_file_sorted_for_git_diff(self, tmp_path: Path) -> None:
        """写入时 name 排序，保证 git diff 稳定。"""
        disabled_file = tmp_path / "skills_disabled.json"
        skill_loader.write_disabled_list({"zoo", "alpha", "mike"}, disabled_file)
        import json as _json
        data = _json.loads(disabled_file.read_text(encoding="utf-8"))
        assert data == ["alpha", "mike", "zoo"]

    def test_read_disabled_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空 set（不抛异常）。"""
        result = skill_loader.read_disabled_list(tmp_path / "nope.json")
        assert result == set()

    def test_read_disabled_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        """文件内容非法时返回空 set + warning（不抛异常）。"""
        path = tmp_path / "corrupt.json"
        path.write_text("not json {{{", encoding="utf-8")
        result = skill_loader.read_disabled_list(path)
        assert result == set()

    def test_orphan_self_heal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """disabled 文件里某 name 在磁盘已不存在 → 启动 scan_skills 自动清理。"""
        monkeypatch.setattr(skill_loader, "DEFAULT_SKILLS_DIR", tmp_path)
        disabled_file = tmp_path / "skills_disabled.json"
        monkeypatch.setattr(_cfg, "SKILLS_DISABLED_FILE", str(disabled_file))

        # 磁盘只有 alpha；disabled 列表却有 alpha + ghost + zombie
        _write_skill(tmp_path / "alpha", "alpha", "x", "y")
        skill_loader.write_disabled_list({"alpha", "ghost", "zombie"}, disabled_file)

        result = skill_loader.scan_skills()

        # alpha 仍被禁用，ghost / zombie 应已清理
        remaining = skill_loader.read_disabled_list(disabled_file)
        assert remaining == {"alpha"}, (
            f"孤儿 name 应被自动清理，但实际 disabled list = {remaining}"
        )
        assert "alpha" in result.disabled
        assert "alpha" not in result.loaded


class TestGetSkillsResponseShape:
    """GET /api/skills 的响应结构应符合 SkillsResponse schema。"""

    def test_response_has_disabled_field(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "a", "a", "x", "y")
        r = client.get("/api/skills")
        body = r.json()
        assert "loaded" in body
        assert "disabled" in body, "GET /api/skills 必须返回 disabled 字段供 UI 渲染"
        assert "failed" in body
        assert isinstance(body["disabled"], list)

    def test_disabled_items_have_body(self, client: TestClient, tmp_path: Path) -> None:
        """禁用的 skill 也要返回 body，UI 展开后能看到正文。"""
        _write_skill(tmp_path / "demo", "demo", "x", "## section\ncontent")
        client.post("/api/skills/demo/toggle", json={"enabled": False})
        r = client.get("/api/skills")
        disabled = r.json()["disabled"]
        assert len(disabled) == 1
        assert "## section" in disabled[0]["body"]


class TestReloadResponseShape:
    """POST /api/skills/reload 的响应应含 disabled_count。"""

    def test_reload_returns_disabled_count(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _write_skill(tmp_path / "a", "a", "x", "y")
        _write_skill(tmp_path / "b", "b", "x", "y")
        client.post("/api/skills/a/toggle", json={"enabled": False})

        r = client.post("/api/skills/reload")
        body = r.json()
        assert body == {"loaded_count": 1, "disabled_count": 1, "failed_count": 0}


# ===========================================================================
# P3: frontmatter passthrough（未知字段 round-trip 保留）
# ===========================================================================
class TestFrontmatterPassthrough:
    """name / description 之外的 frontmatter 字段（如 allowed-tools）必须保留。

    规则：
    - GET 暴露 frontmatter_extra 字段；
    - PUT 不传 frontmatter_extra（None）→ 磁盘原有 extra 保留；
    - PUT 传 {} → 清空 extra；
    - PUT 传非空 dict → 整体替换。
    """

    def test_get_exposes_extra_fields(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: demo\n"
            "description: 测试\n"
            "allowed-tools:\n"
            "  - read_file\n"
            "  - run_shell\n"
            "model: gpt-4o\n"
            "---\n"
            "body content\n",
            encoding="utf-8",
        )
        r = client.get("/api/skills")
        assert r.status_code == 200
        item = r.json()["loaded"][0]
        extra = item["frontmatter_extra"]
        assert extra["allowed-tools"] == ["read_file", "run_shell"]
        assert extra["model"] == "gpt-4o"

    def test_update_without_extra_preserves_existing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """PUT 不传 frontmatter_extra → 磁盘原有 extra 字段保留（不被静默清掉）。"""
        skill_dir = tmp_path / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: demo\n"
            "description: 旧\n"
            "allowed-tools: [tool_a]\n"
            "---\n"
            "old body\n",
            encoding="utf-8",
        )
        # 老前端只传 description / body，不知道 frontmatter_extra
        r = client.put(
            "/api/skills/demo",
            json={"description": "新 desc", "body": "new body"},
        )
        assert r.status_code == 200

        # 磁盘上的 allowed-tools 应仍然存在
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "allowed-tools" in text
        assert "tool_a" in text
        # GET 也应包含
        get_resp = client.get("/api/skills")
        assert get_resp.json()["loaded"][0]["frontmatter_extra"]["allowed-tools"] == ["tool_a"]

    def test_update_with_empty_extra_clears_fields(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """PUT 显式传空 dict {} → extra 字段被清空。"""
        skill_dir = tmp_path / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: demo\n"
            "description: 旧\n"
            "allowed-tools: [tool_a]\n"
            "---\nbody\n",
            encoding="utf-8",
        )
        r = client.put(
            "/api/skills/demo",
            json={"description": "new", "body": "body", "frontmatter_extra": {}},
        )
        assert r.status_code == 200
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "allowed-tools" not in text

    def test_update_with_new_extra_replaces(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """PUT 传非空 dict → 整体替换 extra 字段。"""
        skill_dir = tmp_path / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: x\nfoo: bar\n---\nbody\n",
            encoding="utf-8",
        )
        r = client.put(
            "/api/skills/demo",
            json={
                "description": "new",
                "body": "body",
                "frontmatter_extra": {"allowed-tools": ["t1"]},
            },
        )
        assert r.status_code == 200
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "foo: bar" not in text
        assert "allowed-tools" in text

    def test_create_with_extra(self, client: TestClient, tmp_path: Path) -> None:
        """POST 创建时支持传 frontmatter_extra。"""
        r = client.post(
            "/api/skills",
            json={
                "name": "with_tools",
                "description": "x",
                "body": "body",
                "frontmatter_extra": {"allowed-tools": ["read_file"]},
            },
        )
        assert r.status_code == 201
        text = (tmp_path / "with_tools" / "SKILL.md").read_text(encoding="utf-8")
        assert "allowed-tools" in text
        assert "read_file" in text


# ===========================================================================
# P3: rename 端点
# ===========================================================================
class TestRenameSkill:
    """POST /api/skills/{name}/rename — 强一致改名（目录 + frontmatter name 同步）。"""

    def test_rename_success(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "old_name", "old_name", "x", "body")
        r = client.post("/api/skills/old_name/rename", json={"new_name": "new_name"})
        assert r.status_code == 200
        item = r.json()
        assert item["name"] == "new_name"

        assert not (tmp_path / "old_name").exists()
        new_md = tmp_path / "new_name" / "SKILL.md"
        assert new_md.is_file()
        text = new_md.read_text(encoding="utf-8")
        assert "name: new_name" in text
        assert "name: old_name" not in text

    def test_rename_preserves_body_and_extra(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """改名不能丢 body / extra fields。"""
        skill_dir = tmp_path / "alpha"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: alpha\n"
            "description: 描述\n"
            "allowed-tools: [t1, t2]\n"
            "---\n"
            "## 步骤\nbody content\n",
            encoding="utf-8",
        )
        r = client.post("/api/skills/alpha/rename", json={"new_name": "beta"})
        assert r.status_code == 200
        text = (tmp_path / "beta" / "SKILL.md").read_text(encoding="utf-8")
        assert "## 步骤" in text
        assert "body content" in text
        assert "allowed-tools" in text
        assert "t1" in text and "t2" in text

    def test_rename_preserves_subdirectories(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """改名是 mv 整个目录，scripts/ 等子文件应一起搬。"""
        _write_skill(tmp_path / "src_skill", "src_skill", "x", "y")
        (tmp_path / "src_skill" / "scripts").mkdir()
        (tmp_path / "src_skill" / "scripts" / "helper.py").write_text("print('hi')")

        r = client.post("/api/skills/src_skill/rename", json={"new_name": "dst_skill"})
        assert r.status_code == 200
        assert not (tmp_path / "src_skill").exists()
        assert (tmp_path / "dst_skill" / "scripts" / "helper.py").read_text() == "print('hi')"

    def test_rename_not_found(self, client: TestClient) -> None:
        r = client.post("/api/skills/missing/rename", json={"new_name": "anything"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"

    def test_rename_target_exists(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "a", "a", "x", "y")
        _write_skill(tmp_path / "b", "b", "x", "y")
        r = client.post("/api/skills/a/rename", json={"new_name": "b"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "already_exists"

    def test_rename_invalid_new_name(self, client: TestClient, tmp_path: Path) -> None:
        _write_skill(tmp_path / "ok", "ok", "x", "y")
        r = client.post("/api/skills/ok/rename", json={"new_name": "../etc"})
        assert r.status_code == 400
        # 旧目录还在
        assert (tmp_path / "ok").exists()

    def test_rename_same_name_is_noop(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """new_name == old_name 时返回当前状态，不应报 already_exists。"""
        _write_skill(tmp_path / "demo", "demo", "x", "y")
        r = client.post("/api/skills/demo/rename", json={"new_name": "demo"})
        assert r.status_code == 200
        assert r.json()["name"] == "demo"

    def test_rename_migrates_disabled_state(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """改名时若旧 name 在 disabled 列表 → 迁移到新 name 保持禁用。"""
        _write_skill(tmp_path / "old", "old", "x", "y")
        client.post("/api/skills/old/toggle", json={"enabled": False})

        r = client.post("/api/skills/old/rename", json={"new_name": "renamed"})
        assert r.status_code == 200

        # 改名后 renamed 应在 disabled 区
        get_resp = client.get("/api/skills")
        body = get_resp.json()
        names_disabled = {s["name"] for s in body["disabled"]}
        names_loaded = {s["name"] for s in body["loaded"]}
        assert "renamed" in names_disabled
        assert "renamed" not in names_loaded
        assert "old" not in names_disabled

    def test_rename_triggers_agent_reload(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """改名后下次 get_agent() 必须看到新 name（cache 已清）。"""
        _write_skill(tmp_path / "before", "before", "x", "y")
        agent_before = get_agent()
        assert "before" in agent_before._skill_bodies

        client.post("/api/skills/before/rename", json={"new_name": "after"})
        agent_after = get_agent()
        assert agent_after is not agent_before
        assert "after" in agent_after._skill_bodies
        assert "before" not in agent_after._skill_bodies
