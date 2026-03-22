"""
Skills 发现与解析模块

扫描 advanced/skills/ 下的 **/SKILL.md，解析 YAML frontmatter，
返回按 name 索引的 SkillInfo 字典。

规范参考: https://agentskills.io/specification
"""

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MAX_SCAN_DEPTH = 4
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    location: Path   # SKILL.md 绝对路径
    body: str        # frontmatter 之后的 Markdown 正文


def _safe_parse_yaml(yaml_text: str, path: Path) -> dict | None:
    """宽容解析 YAML：失败时尝试修复 unquoted value 中的冒号。"""
    try:
        return yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        pass
    # 修复：将 'key: value: with colon' 中 value 用双引号包裹
    try:
        fixed = re.sub(
            r'^(\s*[\w-]+:\s*)(.+:.+)$',
            lambda m: m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"',
            yaml_text,
            flags=re.MULTILINE,
        )
        return yaml.safe_load(fixed) or {}
    except yaml.YAMLError as e:
        logger.warning("[SkillLoader] %s YAML 解析失败，跳过: %s", path, e)
        return None


def _parse_skill_md(path: Path) -> SkillInfo | None:
    """解析单个 SKILL.md，失败时记录 warning 并返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[SkillLoader] 读取失败 %s: %s", path, e)
        return None

    if not text.startswith("---"):
        logger.warning("[SkillLoader] %s 缺少 YAML frontmatter，跳过", path)
        return None

    # 匹配闭合 ---，要求独占一行（避免误匹配正文中的 Markdown 水平线）
    close_match = re.search(r'\n---\s*(\n|$)', text[3:])
    if close_match is None:
        logger.warning("[SkillLoader] %s frontmatter 未闭合，跳过", path)
        return None

    close_start = 3 + close_match.start()          # \n 的绝对位置
    close_end   = 3 + close_match.end()            # 闭合行之后的位置
    yaml_text = text[3:close_start].strip()
    body = text[close_end:].strip()

    meta = _safe_parse_yaml(yaml_text, path)
    if meta is None:
        return None

    description = str(meta.get("description", "")).strip()
    if not description:
        logger.warning("[SkillLoader] %s 缺少 description，跳过", path)
        return None

    name = str(meta.get("name", "")).strip() or path.parent.name
    if not name:
        logger.warning("[SkillLoader] %s 无法确定 name，跳过", path)
        return None

    if name != path.parent.name:
        logger.warning("[SkillLoader] %s name=%r 与目录名 %r 不一致", path, name, path.parent.name)

    return SkillInfo(name=name, description=description, location=path.absolute(), body=body)


def scan_skills(
    skills_dir: str | Path,
    max_depth: int = MAX_SCAN_DEPTH,
) -> dict[str, SkillInfo]:
    """
    扫描目录下所有子目录中的 SKILL.md，返回 {name: SkillInfo} 字典。

    同名时先发现的优先；max_depth 限制最大递归深度。
    """
    dir_path = Path(skills_dir)
    if not dir_path.exists():
        return {}
    if not dir_path.is_dir():
        logger.warning("[SkillLoader] %s 不是目录，跳过", skills_dir)
        return {}

    result: dict[str, SkillInfo] = {}

    def _scan(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        skill_md = current / "SKILL.md"
        if skill_md.is_file():
            info = _parse_skill_md(skill_md)
            if info:
                if info.name in result:
                    logger.warning("[SkillLoader] name 冲突 %r，保留先发现的", info.name)
                else:
                    result[info.name] = info
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir() and child.name not in _SKIP_DIRS:
                    _scan(child, depth + 1)
        except OSError as e:
            logger.warning("[SkillLoader] 扫描目录失败 %s: %s", current, e)

    _scan(dir_path, 0)
    logger.info("[SkillLoader] 发现 %d 个 skill: %s", len(result), list(result.keys()))
    return result


def build_skill_catalog(skills: dict[str, SkillInfo]) -> str:
    """构建插入 system prompt 末尾的 skill catalog 文本块。无 skill 时返回空串。"""
    if not skills:
        return ""

    xml_lines = ["<available_skills>"]
    for info in skills.values():
        xml_lines += [
            "  <skill>",
            f"    <name>{html.escape(info.name)}</name>",
            f"    <description>{html.escape(info.description)}</description>",
            "  </skill>",
        ]
    xml_lines.append("</available_skills>")

    return (
        "\n\n## Skills\n"
        "以下 Skill 提供特定领域的专业指令。\n"
        "当任务与某个 Skill 的描述匹配时，先调用 `load_skill` 工具加载完整指令，再执行任务。\n\n"
        + "\n".join(xml_lines)
    )
