"""
Skills 发现与解析模块

扫描 .agenta/skills/ 下的 **/SKILL.md，解析 YAML frontmatter，
返回按 name 索引的 SkillInfo 字典 + 加载失败明细。

规范参考: https://agentskills.io/specification
"""

import html
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MAX_SCAN_DEPTH = 4
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

# 约定路径（相对项目根；不是 .env 可覆盖配置 — 单用户 CLI 场景没必要做成配置项）。
# 调用方既可不传（默认用本路径，main.py / chainlit_app.py 等启动入口走该分支），
# 也可显式传绝对路径（评估脚本可能从任意 cwd 启动 — 见 recall_skill.py）。
DEFAULT_SKILLS_DIR = Path(".agenta/skills")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    location: Path   # SKILL.md 绝对路径
    body: str        # frontmatter 之后的 Markdown 正文


@dataclass(frozen=True)
class SkillLoadFailure:
    """单个 SKILL.md 加载失败的明细，用于 CLI / WebUI 显式回显给用户。

    reason 取自下列字符串常量（便于上层按 prefix 做分支或国际化）：
      - read_failed: <os_error>
      - missing_frontmatter
      - frontmatter_not_closed
      - yaml_parse_error: <yaml_error>
      - missing_description
      - missing_name
      - duplicate_name: <name>
    """
    path: Path
    reason: str


@dataclass
class ScanResult:
    """scan_skills() 的返回结构。

    loaded — name → SkillInfo
    failed — 顺序记录所有失败的 SKILL.md 与失败原因
    """
    loaded: dict[str, SkillInfo] = field(default_factory=dict)
    failed: list[SkillLoadFailure] = field(default_factory=list)


def _safe_parse_yaml(yaml_text: str, path: Path) -> tuple[dict | None, str | None]:
    """宽容解析 YAML：失败时尝试修复 unquoted value 中的冒号。

    返回 (parsed, error_msg)。成功时 parsed 是 dict，error_msg 为 None；
    失败时 parsed 为 None，error_msg 是 yaml.YAMLError 的字符串描述。
    """
    try:
        return yaml.safe_load(yaml_text) or {}, None
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
        return yaml.safe_load(fixed) or {}, None
    except yaml.YAMLError as e:
        logger.warning("[SkillLoader] %s YAML 解析失败，跳过: %s", path, e)
        return None, str(e)


def _parse_skill_md(path: Path) -> SkillInfo | SkillLoadFailure:
    """解析单个 SKILL.md，成功返回 SkillInfo，失败返回 SkillLoadFailure（带 reason）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[SkillLoader] 读取失败 %s: %s", path, e)
        return SkillLoadFailure(path=path, reason=f"read_failed: {e}")

    if not text.startswith("---"):
        logger.warning("[SkillLoader] %s 缺少 YAML frontmatter，跳过", path)
        return SkillLoadFailure(path=path, reason="missing_frontmatter")

    # 匹配闭合 ---，要求独占一行（避免误匹配正文中的 Markdown 水平线）
    close_match = re.search(r'\n---\s*(\n|$)', text[3:])
    if close_match is None:
        logger.warning("[SkillLoader] %s frontmatter 未闭合，跳过", path)
        return SkillLoadFailure(path=path, reason="frontmatter_not_closed")

    close_start = 3 + close_match.start()          # \n 的绝对位置
    close_end   = 3 + close_match.end()            # 闭合行之后的位置
    yaml_text = text[3:close_start].strip()
    body = text[close_end:].strip()

    meta, err = _safe_parse_yaml(yaml_text, path)
    if meta is None:
        return SkillLoadFailure(path=path, reason=f"yaml_parse_error: {err}")

    description = str(meta.get("description", "")).strip()
    if not description:
        logger.warning("[SkillLoader] %s 缺少 description，跳过", path)
        return SkillLoadFailure(path=path, reason="missing_description")

    name = str(meta.get("name", "")).strip() or path.parent.name
    if not name:
        logger.warning("[SkillLoader] %s 无法确定 name，跳过", path)
        return SkillLoadFailure(path=path, reason="missing_name")

    if name != path.parent.name:
        logger.warning("[SkillLoader] %s name=%r 与目录名 %r 不一致", path, name, path.parent.name)

    return SkillInfo(name=name, description=description, location=path.absolute(), body=body)


def scan_skills(
    skills_dir: str | Path | None = None,
    max_depth: int = MAX_SCAN_DEPTH,
) -> ScanResult:
    """扫描目录下所有子目录中的 SKILL.md。

    Args:
        skills_dir: 不传 → 用 `DEFAULT_SKILLS_DIR`（约定路径 `.agenta/skills`，
            相对当前 cwd）；显式传字符串 / Path → 用该路径（评估脚本 / UT 场景）。
        max_depth: 最大递归深度。

    返回 ScanResult(loaded, failed)：
      - loaded：{name: SkillInfo}，按发现顺序（os 决定）填充
      - failed：list[SkillLoadFailure]，包含解析失败 + 同名冲突两种来源；
        同名冲突时**先发现的优先**，后发现的被丢入 failed 并标 reason="duplicate_name: <name>"

    目录不存在 / 不是目录时直接返回空 ScanResult。
    """
    result = ScanResult()
    dir_path = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
    if not dir_path.exists():
        return result
    if not dir_path.is_dir():
        logger.warning("[SkillLoader] %s 不是目录，跳过", skills_dir)
        return result

    def _scan(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        skill_md = current / "SKILL.md"
        if skill_md.is_file():
            parsed = _parse_skill_md(skill_md)
            if isinstance(parsed, SkillLoadFailure):
                result.failed.append(parsed)
            elif parsed.name in result.loaded:
                logger.warning("[SkillLoader] name 冲突 %r，保留先发现的", parsed.name)
                result.failed.append(
                    SkillLoadFailure(path=skill_md, reason=f"duplicate_name: {parsed.name}")
                )
            else:
                result.loaded[parsed.name] = parsed
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir() and child.name not in _SKIP_DIRS:
                    _scan(child, depth + 1)
        except OSError as e:
            logger.warning("[SkillLoader] 扫描目录失败 %s: %s", current, e)

    _scan(dir_path, 0)
    logger.info(
        "[SkillLoader] 发现 %d 个 skill: %s（失败 %d 个）",
        len(result.loaded), list(result.loaded.keys()), len(result.failed),
    )
    return result


def format_scan_banner(result: ScanResult) -> tuple[str, str]:
    """把 scan_skills 结果渲染成人类可读的两段文本，供 CLI / WebUI 启动时展示。

    返回 (success_line, failure_block)：
      - success_line：单行，"🔧 已加载 Skills（N 个）：name1, name2" 或 "🔧 未发现 Skills"
      - failure_block：多行 "⚠️ 加载失败 N 个：\\n  ✗ <path>：<reason>"；无失败返回空串
    上层按各自 UI 形式拼接（CLI 直接 print，WebUI 包到 message 里）。
    """
    names = list(result.loaded.keys())
    if names:
        success_line = f"🔧 已加载 Skills（{len(names)} 个）：{', '.join(names)}"
    else:
        success_line = "🔧 未发现 Skills"
    if not result.failed:
        return success_line, ""
    lines = [f"⚠️ Skills 加载失败 {len(result.failed)} 个："]
    for f in result.failed:
        lines.append(f"  ✗ {f.path}：{f.reason}")
    return success_line, "\n".join(lines)


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
