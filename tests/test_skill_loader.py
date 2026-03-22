"""
测试：Skills 加载、catalog 构建、工具路由集成

测试内容：
    - scan_skills / _parse_skill_md 对各类 SKILL.md 的解析行为
    - build_skill_catalog 生成的 XML 格式
    - execute_tool("load_skill", ...) 在有/无 skill_bodies 时的路由
    - Agent 接受 dict[str, SkillInfo] 后 system_prompt 含 description，_skill_bodies 正确
"""

import textwrap
from pathlib import Path

import pytest

from src.agent.agent import Agent
from src.agent.tools import execute_tool, get_tools
from src.cli.skill_loader import SkillInfo, build_skill_catalog, scan_skills


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
    """测试 scan_skills 对各类 SKILL.md 的解析行为"""

    def test_parse_valid_skill(self, tmp_path: Path) -> None:
        """合法的 SKILL.md 应被正确解析，SkillInfo 各字段准确"""
        _write_skill(tmp_path, "example", _VALID_SKILL_MD)
        result = scan_skills(tmp_path)

        assert "example" in result
        info = result["example"]
        assert info.name == "example"
        assert info.description == "一个示例 Skill，用于单元测试"
        assert "Skill 的正文" in info.body
        assert info.location.name == "SKILL.md"

    def test_skip_missing_description(self, tmp_path: Path) -> None:
        """缺少 description 的 SKILL.md 应跳过（不报错，仅 warning）"""
        content = "---\nname: nodesc\n---\n# 无描述\n"
        _write_skill(tmp_path, "nodesc", content)
        result = scan_skills(tmp_path)
        assert "nodesc" not in result

    def test_skip_no_frontmatter(self, tmp_path: Path) -> None:
        """无 YAML frontmatter 的文件应跳过"""
        content = "# 无 frontmatter\n正文\n"
        _write_skill(tmp_path, "nofm", content)
        result = scan_skills(tmp_path)
        assert "nofm" not in result

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
        assert "colon-skill" in result
        assert "HTTP" in result["colon-skill"].description

    def test_name_conflict_keeps_first(self, tmp_path: Path) -> None:
        """同名 skill 出现冲突时，先发现的优先"""
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
        assert result["example"].description == "一个示例 Skill，用于单元测试"

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        """目录不存在时应返回空字典，不抛出异常"""
        result = scan_skills(tmp_path / "no_such_dir")
        assert result == {}

    def test_ignores_non_skill_md(self, tmp_path: Path) -> None:
        """非 SKILL.md 的文件不会被处理"""
        other = tmp_path / "example" / "README.md"
        other.parent.mkdir(parents=True)
        other.write_text("# 不是 SKILL.md\n", encoding="utf-8")
        result = scan_skills(tmp_path)
        assert result == {}


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
