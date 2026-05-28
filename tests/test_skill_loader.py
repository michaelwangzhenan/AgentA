"""
测试：Skills 加载、catalog 构建、工具路由集成

测试内容：
    - scan_skills / _parse_skill_md 对各类 SKILL.md 的解析行为 + 失败明细
    - build_skill_catalog 生成的 XML 格式
    - format_scan_banner 的 CLI / WebUI 共用文案
    - execute_tool("load_skill", ...) 在有/无 skill_bodies 时的路由
    - Agent 接受 dict[str, SkillInfo] 后 system_prompt 含 description，_skill_bodies 正确
"""

import textwrap
from pathlib import Path

import pytest

from src.agent.agent import Agent
from src.agent.tools import execute_tool, get_tools
from src.cli.skill_loader import (
    ScanResult,
    SkillInfo,
    SkillLoadFailure,
    build_skill_catalog,
    format_scan_banner,
    scan_skills,
)


# ── 辅助：在临时目录中创建 SKILL.md ──────────────────────────────────────────

def _write_skill(base: Path, skill_dir: str, content: str) -> Path:
    """在 base/<skill_dir>/SKILL.md 写入 content 并返回路径。"""
    skill_path = base / skill_dir / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content, encoding="utf-8")
    return skill_path


_VALID_SKILL_MD = textwrap.dedent("""\
    ---
    name: example
    description: 一个示例 Skill，用于单元测试
    ---
    # 示例 Skill

    这是 Skill 的正文内容。
""")


# ── TestSkillLoader ───────────────────────────────────────────────────────────

class TestSkillLoader:
    """测试 scan_skills 对各类 SKILL.md 的解析行为（loaded 维度）"""

    def test_parse_valid_skill(self, tmp_path: Path) -> None:
        """合法的 SKILL.md 应被正确解析，SkillInfo 各字段准确"""
        _write_skill(tmp_path, "example", _VALID_SKILL_MD)
        result = scan_skills(tmp_path)

        assert "example" in result.loaded
        assert result.failed == []
        info = result.loaded["example"]
        assert info.name == "example"
        assert info.description == "一个示例 Skill，用于单元测试"
        assert "Skill 的正文" in info.body
        assert info.location.name == "SKILL.md"

    def test_skip_missing_description(self, tmp_path: Path) -> None:
        """缺少 description 的 SKILL.md 应跳过（不报错），并进 failed 列表"""
        content = "---\nname: nodesc\n---\n# 无描述\n"
        _write_skill(tmp_path, "nodesc", content)
        result = scan_skills(tmp_path)
        assert "nodesc" not in result.loaded
        assert any(f.reason == "missing_description" for f in result.failed)

    def test_skip_no_frontmatter(self, tmp_path: Path) -> None:
        """无 YAML frontmatter 的文件应跳过并进 failed"""
        content = "# 无 frontmatter\n正文\n"
        _write_skill(tmp_path, "nofm", content)
        result = scan_skills(tmp_path)
        assert "nofm" not in result.loaded
        assert any(f.reason == "missing_frontmatter" for f in result.failed)

    def test_yaml_colon_fix(self, tmp_path: Path) -> None:
        """description 中含冒号时，YAML 修复后应正常解析"""
        content = textwrap.dedent("""\
            ---
            name: colon-skill
            description: HTTP: 超文本传输协议
            ---
            正文
        """)
        _write_skill(tmp_path, "colon-skill", content)
        result = scan_skills(tmp_path)
        assert "colon-skill" in result.loaded
        assert "HTTP" in result.loaded["colon-skill"].description
        assert result.failed == []

    def test_name_conflict_keeps_first(self, tmp_path: Path) -> None:
        """同名 skill 冲突时，先发现的优先；后发现的进 failed 并标 duplicate_name"""
        _write_skill(tmp_path, "alpha/dup", _VALID_SKILL_MD)
        second = textwrap.dedent("""\
            ---
            name: example
            description: 第二个 example，应被忽略
            ---
            第二个正文
        """)
        _write_skill(tmp_path, "beta/dup", second)
        result = scan_skills(tmp_path)
        # 先发现的（alpha 按字母序在前）保留
        assert result.loaded["example"].description == "一个示例 Skill，用于单元测试"
        dup_failures = [f for f in result.failed if f.reason.startswith("duplicate_name:")]
        assert len(dup_failures) == 1
        assert "example" in dup_failures[0].reason
        # 失败条目里也要带正确的源文件路径，方便用户定位
        assert dup_failures[0].path.parent.name == "dup"
        assert "beta" in str(dup_failures[0].path)

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        """目录不存在时应返回空 ScanResult，不抛出异常"""
        result = scan_skills(tmp_path / "no_such_dir")
        assert result.loaded == {}
        assert result.failed == []

    def test_ignores_non_skill_md(self, tmp_path: Path) -> None:
        """非 SKILL.md 的文件不会被处理，也不进 failed"""
        other = tmp_path / "example" / "README.md"
        other.parent.mkdir(parents=True)
        other.write_text("# 不是 SKILL.md\n", encoding="utf-8")
        result = scan_skills(tmp_path)
        assert result.loaded == {}
        assert result.failed == []


# ── TestScanResultFailures ────────────────────────────────────────────────────

class TestScanResultFailures:
    """专项覆盖 scan_skills 的 failed 列表 — 验收 ④ 失败可见的基础设施"""

    def test_yaml_parse_error_reason(self, tmp_path: Path) -> None:
        """YAML 语法错（无法靠 colon 修复时）应进 failed，reason 含 yaml_parse_error 前缀"""
        # 用 unbalanced bracket 制造一个 colon-fix 也救不了的 YAML 错误
        content = textwrap.dedent("""\
            ---
            name: bad-yaml
            description: [unbalanced
            ---
            正文
        """)
        _write_skill(tmp_path, "bad-yaml", content)
        result = scan_skills(tmp_path)
        assert "bad-yaml" not in result.loaded
        assert len(result.failed) == 1
        assert result.failed[0].reason.startswith("yaml_parse_error:")
        assert result.failed[0].path.parent.name == "bad-yaml"

    def test_frontmatter_not_closed_reason(self, tmp_path: Path) -> None:
        """frontmatter 开头有 --- 但找不到闭合 --- 时进 failed"""
        content = "---\nname: open\ndescription: 没闭合\n# 没有第二个 ---\n正文\n"
        _write_skill(tmp_path, "open-fm", content)
        result = scan_skills(tmp_path)
        assert "open-fm" not in result.loaded
        assert any(f.reason == "frontmatter_not_closed" for f in result.failed)

    def test_missing_name_reason(self, tmp_path: Path) -> None:
        """frontmatter 里 name 为空且无法从目录名推断时（这里手工构造 name: 空串）"""
        # _parse_skill_md 在 name 为空时会回退到 path.parent.name，所以单纯不写 name
        # 也不会触发 missing_name。这里通过把目录名也清空构造场景不太现实，改为
        # 显式验证：name 缺失但目录名存在时**应该 fallback 到目录名**（仍 loaded）
        content = "---\ndescription: 无 name 字段，应回退到目录名\n---\n正文\n"
        _write_skill(tmp_path, "fallback-name", content)
        result = scan_skills(tmp_path)
        assert "fallback-name" in result.loaded
        assert result.failed == []

    def test_multiple_failures_collected_in_order(self, tmp_path: Path) -> None:
        """多个失败应按发现顺序保留在 failed 列表里"""
        _write_skill(tmp_path, "a-good", _VALID_SKILL_MD)
        _write_skill(tmp_path, "b-no-fm", "# 无 frontmatter\n")
        _write_skill(tmp_path, "c-no-desc", "---\nname: nodesc\n---\n")
        result = scan_skills(tmp_path)
        assert len(result.loaded) == 1
        assert "example" in result.loaded
        assert len(result.failed) == 2
        # b-no-fm 先扫到，c-no-desc 后
        assert "b-no-fm" in str(result.failed[0].path)
        assert result.failed[0].reason == "missing_frontmatter"
        assert "c-no-desc" in str(result.failed[1].path)
        assert result.failed[1].reason == "missing_description"


# ── TestFormatScanBanner ──────────────────────────────────────────────────────

class TestFormatScanBanner:
    """format_scan_banner — CLI / WebUI 共用的启动文案"""

    def test_empty_result_shows_zero_skills(self) -> None:
        success, failure = format_scan_banner(ScanResult())
        assert "未发现 Skills" in success
        assert failure == ""

    def test_loaded_only_shows_names(self) -> None:
        info = SkillInfo(name="alpha", description="d", location=Path("/x/alpha/SKILL.md"), body="b")
        result = ScanResult(loaded={"alpha": info})
        success, failure = format_scan_banner(result)
        assert "已加载 Skills（1 个）" in success
        assert "alpha" in success
        assert failure == ""

    def test_failed_appears_in_failure_block(self) -> None:
        info = SkillInfo(name="alpha", description="d", location=Path("/x/alpha/SKILL.md"), body="b")
        result = ScanResult(
            loaded={"alpha": info},
            failed=[SkillLoadFailure(path=Path("/x/bad/SKILL.md"), reason="missing_description")],
        )
        success, failure = format_scan_banner(result)
        assert "alpha" in success
        assert "加载失败 1 个" in failure
        assert "missing_description" in failure
        assert "bad" in failure


# ── TestRealAgentaSkills ──────────────────────────────────────────────────────

class TestRealAgentaSkills:
    """对仓库 .agenta/skills/ 真实目录的烟雾测试（防止 example-skill / study-planner 退化）"""

    def test_repo_skills_loadable(self) -> None:
        repo_skills = Path(__file__).resolve().parents[1] / ".agenta" / "skills"
        if not repo_skills.is_dir():
            pytest.skip(".agenta/skills 目录不存在，跳过")
        result = scan_skills(repo_skills)
        # 至少两个：example-skill 和 study-planner（Step 0 验收 ②/④ 的载体）
        assert "example-skill" in result.loaded
        assert "study-planner" in result.loaded
        # 仓库内置 skill 必须 0 失败，否则 main.py 启动就会刷红
        assert result.failed == [], (
            f"仓库内置 skill 解析失败：{[(str(f.path), f.reason) for f in result.failed]}"
        )


# ── TestBuildSkillCatalog ─────────────────────────────────────────────────────

class TestBuildSkillCatalog:
    """测试 build_skill_catalog 输出格式"""

    def _make_info(self, name: str, desc: str) -> SkillInfo:
        return SkillInfo(
            name=name,
            description=desc,
            location=Path(f"/fake/{name}/SKILL.md"),
            body="正文",
        )

    def test_empty_skills_returns_empty_string(self) -> None:
        assert build_skill_catalog({}) == ""

    def test_catalog_contains_name_and_description(self) -> None:
        skills = {"example": self._make_info("example", "这是示例描述")}
        catalog = build_skill_catalog(skills)
        assert "<name>example</name>" in catalog
        assert "<description>这是示例描述</description>" in catalog

    def test_catalog_has_no_location_tag(self) -> None:
        """catalog 不应暴露本地文件系统路径"""
        skills = {"example": self._make_info("example", "示例")}
        catalog = build_skill_catalog(skills)
        assert "<location>" not in catalog

    def test_catalog_contains_available_skills_wrapper(self) -> None:
        skills = {"s1": self._make_info("s1", "技能一"), "s2": self._make_info("s2", "技能二")}
        catalog = build_skill_catalog(skills)
        assert "<available_skills>" in catalog
        assert "</available_skills>" in catalog
        assert catalog.count("<skill>") == 2


# ── TestToolLoadSkill ─────────────────────────────────────────────────────────

class TestToolLoadSkill:
    """测试 execute_tool('load_skill', ...) 的路由与 skill_bodies 传递"""

    def test_load_known_skill_returns_ok(self) -> None:
        skill_bodies = {"example": "# 这是专业指令\n详细内容"}
        result = execute_tool("load_skill", {"name": "example"}, skill_bodies)
        assert result.status == "ok"
        assert "<skill_content" in result.content
        assert "example" in result.content
        assert "详细内容" in result.content

    def test_load_unknown_skill_returns_error(self) -> None:
        skill_bodies = {"real-skill": "body"}
        result = execute_tool("load_skill", {"name": "ghost"}, skill_bodies)
        assert result.status == "error"
        assert "ghost" in result.content

    def test_load_skill_without_bodies_returns_error(self) -> None:
        """skill_bodies 为 None 时，load_skill 返回 error（不崩溃）"""
        result = execute_tool("load_skill", {"name": "any"}, None)
        assert result.status == "error"

    def test_get_tools_includes_load_skill_when_bodies_given(self) -> None:
        tools = get_tools({"myskill": "body"})
        names = {t["function"]["name"] for t in tools}
        assert "load_skill" in names

    def test_get_tools_no_load_skill_when_empty(self) -> None:
        tools = get_tools({})
        names = {t["function"]["name"] for t in tools}
        assert "load_skill" not in names

    def test_get_tools_no_load_skill_when_none(self) -> None:
        tools = get_tools(None)
        names = {t["function"]["name"] for t in tools}
        assert "load_skill" not in names


# ── TestAgentWithSkills ───────────────────────────────────────────────────────

class TestAgentWithSkills:
    """测试 Agent(skills=dict[str, SkillInfo]) 行为"""

    def _make_info(self, name: str, desc: str, body: str = "指令正文") -> SkillInfo:
        return SkillInfo(
            name=name,
            description=desc,
            location=Path(f"/fake/{name}/SKILL.md"),
            body=body,
        )

    def test_system_prompt_contains_description(self) -> None:
        """系统提示中应包含 skill 的 description（而非只有 name）"""
        info = self._make_info("code-review", "用于代码审查的专家指令")
        agent = Agent(skills={"code-review": info})
        assert "用于代码审查的专家指令" in agent.system_prompt
        assert "<name>code-review</name>" in agent.system_prompt

    def test_system_prompt_no_absolute_path(self) -> None:
        """系统提示中不应包含 <location> 标签（避免泄露本地路径）"""
        info = self._make_info("s1", "描述")
        agent = Agent(skills={"s1": info})
        assert "<location>" not in agent.system_prompt

    def test_skill_bodies_extracted_correctly(self) -> None:
        """_skill_bodies 应包含传入的 body 内容"""
        info = self._make_info("writer", "写作助手", "# 写作规范\n保持简洁")
        agent = Agent(skills={"writer": info})
        assert agent._skill_bodies == {"writer": "# 写作规范\n保持简洁"}

    def test_no_skills_gives_empty_bodies(self) -> None:
        agent = Agent(skills=None)
        assert agent._skill_bodies == {}

    def test_agent_without_skills_no_catalog(self) -> None:
        """没有 skills 时，system_prompt 不含 available_skills 标签"""
        agent = Agent(skills=None)
        assert "<available_skills>" not in agent.system_prompt
