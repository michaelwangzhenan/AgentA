"""
Skills 发现与解析模块

扫描 .agenta/skills/ 下的 **/SKILL.md，解析 YAML frontmatter，
返回按 name 索引的 SkillInfo 字典 + 加载失败明细 + 已禁用清单。

规范参考: https://agentskills.io/specification

禁用状态走"状态分离"模式（对齐业内 Cursor / Claude.ai 做法）：SKILL.md 本身
保持纯净（仅 name / description / allowed-tools 等开放标准字段），是否启用记在
独立的 `.agenta/skills/disabled.json` 文件里。这样 SKILL.md 可跨 agent 复用。
"""

import html
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MAX_SCAN_DEPTH = 4
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

# 约定路径（相对项目根；不是 .env 可覆盖配置 — 单用户 CLI 场景没必要做成配置项）。
# 调用方既可不传（默认用本路径，main.py 等启动入口走该分支），
# 也可显式传绝对路径（评估脚本可能从任意 cwd 启动 — 见 recall_skill.py）。
DEFAULT_SKILLS_DIR = Path(".agenta/skills")

# disabled 状态文件 fallback：实际默认从 config.SKILLS_DISABLED_FILE 读（可 .env 覆盖）
# 此处保留为兜底常量，配合 UT 不依赖 .env 注入时仍能工作
DEFAULT_DISABLED_FILE = Path(".agenta/skills/disabled.json")

# 合法 skill name 正则：与 LLM tool name 命名规则对齐（avoid OpenAI tool naming error）
SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _resolve_disabled_file(disabled_file: str | Path | None) -> Path:
    """显式传入优先；否则读 config.SKILLS_DISABLED_FILE；config 缺失时 fallback 兜底常量。"""
    if disabled_file is not None:
        return Path(disabled_file)
    try:
        from src import config as _cfg
        return Path(_cfg.SKILLS_DISABLED_FILE)
    except (ImportError, AttributeError):
        return DEFAULT_DISABLED_FILE


# 受 UI / runtime 显式管理的 frontmatter key，passthrough 时排除在 frontmatter_extra 之外
_RESERVED_FRONTMATTER_KEYS = frozenset({"name", "description"})


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    location: Path   # SKILL.md 绝对路径
    body: str        # frontmatter 之后的 Markdown 正文
    frontmatter_extra: dict = field(default_factory=dict)  # name/description 之外的 frontmatter 字段


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

    loaded   — 启用且解析成功的 name → SkillInfo（进 ## Skills catalog）
    disabled — 解析成功但被禁用的 name → SkillInfo（UI 显示，不进 catalog；可重启用）
    failed   — 解析失败的 SKILL.md 与原因
    """
    loaded: dict[str, SkillInfo] = field(default_factory=dict)
    disabled: dict[str, SkillInfo] = field(default_factory=dict)
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

    # 收集 name/description 以外的 frontmatter 字段（如 allowed-tools / model 等）
    # 仅做 passthrough 保留：UI 编辑后写回时按原样输出，不丢失
    extra = {k: v for k, v in meta.items() if k not in _RESERVED_FRONTMATTER_KEYS}

    return SkillInfo(
        name=name,
        description=description,
        location=path.absolute(),
        body=body,
        frontmatter_extra=extra,
    )


def read_disabled_list(disabled_file: str | Path | None = None) -> set[str]:
    """读 disabled 列表文件（JSON 数组），返回 set。

    文件不存在 / 格式异常 → 返回空 set（视作"没禁用任何 skill"），warning 记录。
    """
    path = _resolve_disabled_file(disabled_file)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.warning("[SkillLoader] %s 内容不是 JSON 数组，忽略", path)
            return set()
        return {str(name) for name in data if isinstance(name, str)}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[SkillLoader] 读 %s 失败: %s，按空 disabled 处理", path, e)
        return set()


def write_disabled_list(
    names: set[str], disabled_file: str | Path | None = None
) -> None:
    """原子写 disabled 列表：先写临时文件，再 rename，避免半写状态被读到。

    name 排序后写入，git diff 友好。
    """
    path = _resolve_disabled_file(disabled_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_names = sorted(names)
    text = json.dumps(sorted_names, ensure_ascii=False, indent=2) + "\n"
    # tempfile 必须跟目标文件同目录，否则 rename 跨设备会失败
    fd, tmp_name = tempfile.mkstemp(
        prefix=".skills_disabled.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)   # 原子 rename（Windows / POSIX 都保证）
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def scan_skills(
    skills_dir: str | Path | None = None,
    max_depth: int = MAX_SCAN_DEPTH,
    disabled_file: str | Path | None = None,
) -> ScanResult:
    """扫描目录下所有子目录中的 SKILL.md。

    Args:
        skills_dir: 不传 → 用 `DEFAULT_SKILLS_DIR`（约定路径 `.agenta/skills`）。
        max_depth: 最大递归深度。
        disabled_file: 不传 → 用 `DEFAULT_DISABLED_FILE`（`.agenta/skills/disabled.json`）。

    返回 ScanResult(loaded, disabled, failed)：
      - loaded：{name: SkillInfo}，启用的 + 解析成功的
      - disabled：{name: SkillInfo}，被禁用列表标记的（仍解析成功，仅不进 catalog）
      - failed：list[SkillLoadFailure]，解析失败 + 同名冲突 + 非法 name

    **孤儿自愈**：disabled 文件里某 name 在磁盘已不存在 → 启动扫描时自动从文件移除
    （写回磁盘），避免列表长期堆积已删除的 skill name。

    目录不存在 / 不是目录时返回空 ScanResult（不读 disabled 文件，避免误清理）。
    """
    result = ScanResult()
    dir_path = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
    if not dir_path.exists():
        return result
    if not dir_path.is_dir():
        logger.warning("[SkillLoader] %s 不是目录，跳过", skills_dir)
        return result

    disabled_names = read_disabled_list(disabled_file)

    # 临时收集所有解析成功的 SKILL.md（启用 + 禁用），用于后续分流
    parsed_skills: dict[str, SkillInfo] = {}

    def _scan(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        skill_md = current / "SKILL.md"
        if skill_md.is_file():
            parsed = _parse_skill_md(skill_md)
            if isinstance(parsed, SkillLoadFailure):
                result.failed.append(parsed)
            elif parsed.name in parsed_skills:
                logger.warning("[SkillLoader] name 冲突 %r，保留先发现的", parsed.name)
                result.failed.append(
                    SkillLoadFailure(path=skill_md, reason=f"duplicate_name: {parsed.name}")
                )
            else:
                parsed_skills[parsed.name] = parsed
        try:
            for child in sorted(current.iterdir()):
                if child.is_dir() and child.name not in _SKIP_DIRS:
                    _scan(child, depth + 1)
        except OSError as e:
            logger.warning("[SkillLoader] 扫描目录失败 %s: %s", current, e)

    _scan(dir_path, 0)

    # 分流：disabled 列表里的进 .disabled；其它进 .loaded
    for name, info in parsed_skills.items():
        if name in disabled_names:
            result.disabled[name] = info
        else:
            result.loaded[name] = info

    # 孤儿自愈：disabled 文件里有但磁盘已不存在的 name → 写回清理过的 list
    orphans = disabled_names - set(parsed_skills.keys())
    if orphans:
        cleaned = disabled_names - orphans
        logger.info(
            "[SkillLoader] 清理 disabled 文件中的孤儿 name：%s",
            sorted(orphans),
        )
        try:
            write_disabled_list(cleaned, disabled_file)
        except OSError as e:
            logger.warning("[SkillLoader] 清理 disabled 孤儿失败：%s", e)

    # 每个 API 请求都会触发一次扫描，用 INFO 会刷屏（一次运行几十条）。降到 DEBUG，
    # 平时不打；CLI 启动用 format_scan_banner 单独打到 stdout，不依赖这条日志。
    logger.debug(
        "[SkillLoader] 发现 %d 个 skill: 启用 %s, 禁用 %s（失败 %d 个）",
        len(parsed_skills),
        list(result.loaded.keys()),
        list(result.disabled.keys()),
        len(result.failed),
    )
    return result


# ---------------------------------------------------------------------------
# CRUD 辅助 —— 给 API 层 (api/routes/skills.py) 复用
# ---------------------------------------------------------------------------


class SkillIOError(Exception):
    """skill CRUD 操作的统一异常类，API 层捕获后映射到 4xx 状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _skill_dir(name: str, skills_dir: str | Path | None = None) -> Path:
    """返回 .agenta/skills/{name}/ 路径。"""
    base = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
    return base / name


def validate_skill_name(name: str) -> None:
    """校验 name 合法性，违反任一规则抛 SkillIOError(code='invalid_name')。

    规则：
    1. 非空字符串
    2. 字符集 `[a-zA-Z0-9_-]+`（防路径注入；跟 OpenAI tool naming 规则对齐）
    3. 长度 1-64（防止超长破坏路径）
    """
    if not isinstance(name, str) or not name:
        raise SkillIOError("invalid_name", "skill name 不能为空")
    if len(name) > 64:
        raise SkillIOError("invalid_name", "skill name 不能超过 64 字符")
    if not SKILL_NAME_PATTERN.match(name):
        raise SkillIOError(
            "invalid_name",
            "skill name 只能含字母 / 数字 / 下划线 / 连字符（^[a-zA-Z0-9_-]+$）",
        )


def _format_skill_md(
    name: str,
    description: str,
    body: str,
    extra: dict | None = None,
) -> str:
    """组装 SKILL.md 文本：frontmatter（name + description + 任意 extra）+ body。

    `extra` 是 `name` / `description` 之外的 frontmatter 字段（如 `allowed-tools`）；
    用 yaml.safe_dump 序列化以正确处理列表 / 嵌套结构，保证 round-trip 不丢字段。
    """
    name_line = f"name: {_yaml_scalar(name)}\n"
    desc_line = f"description: {_yaml_scalar(description)}\n"
    extra_block = ""
    if extra:
        extra_block = yaml.safe_dump(extra, allow_unicode=True, sort_keys=False)
    frontmatter = "---\n" + name_line + desc_line + extra_block + "---\n"
    return frontmatter + body.rstrip("\n") + "\n"


def _yaml_scalar(s: str) -> str:
    """对 name / description 单行 scalar 值做最小化引号处理。

    含特殊字符（`:` / `#` / 换行 / 引号 / 反斜杠）时用双引号包裹并 escape；
    其他情况直接裸写，git diff 友好。
    """
    if any(c in s for c in ":#\n\"'\\"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def create_skill(
    name: str,
    description: str,
    body: str,
    skills_dir: str | Path | None = None,
    frontmatter_extra: dict | None = None,
) -> SkillInfo:
    """新建 skill：创建目录 `<skills_dir>/<name>/` 并写 SKILL.md。

    Args:
        frontmatter_extra: name/description 之外的 frontmatter 字段（按原样写入；
            agentskills.io 标准字段如 `allowed-tools` 也走这里 passthrough）。

    Raises:
        SkillIOError(code="invalid_name"): name 非法
        SkillIOError(code="missing_description"): description 为空
        SkillIOError(code="already_exists"): 目录已存在
        SkillIOError(code="write_failed"): 文件系统错误
    """
    validate_skill_name(name)
    if not description.strip():
        raise SkillIOError("missing_description", "description 不能为空")

    target = _skill_dir(name, skills_dir)
    if target.exists():
        raise SkillIOError("already_exists", f"skill '{name}' 已存在")
    try:
        target.mkdir(parents=True, exist_ok=False)
        skill_md = target / "SKILL.md"
        skill_md.write_text(
            _format_skill_md(name, description.strip(), body, frontmatter_extra),
            encoding="utf-8",
        )
    except OSError as e:
        raise SkillIOError("write_failed", f"创建 skill 失败：{e}") from e

    return SkillInfo(
        name=name,
        description=description.strip(),
        location=skill_md.absolute(),
        body=body.rstrip("\n"),
        frontmatter_extra=dict(frontmatter_extra or {}),
    )


def update_skill(
    name: str,
    description: str,
    body: str,
    skills_dir: str | Path | None = None,
    frontmatter_extra: dict | None = None,
) -> SkillInfo:
    """更新 skill：重写 SKILL.md（name 不变；改名走 `rename_skill`）。

    Args:
        frontmatter_extra: 不传 (None) 时**保留磁盘上现有的 extra 字段**（不覆盖）；
            传空 dict {} 时清空所有 extra 字段；传非空 dict 时整体替换。

    Raises:
        SkillIOError(code="invalid_name"): name 非法
        SkillIOError(code="missing_description"): description 为空
        SkillIOError(code="not_found"): skill 目录或 SKILL.md 不存在
        SkillIOError(code="write_failed"): 文件系统错误
    """
    validate_skill_name(name)
    if not description.strip():
        raise SkillIOError("missing_description", "description 不能为空")

    target = _skill_dir(name, skills_dir)
    skill_md = target / "SKILL.md"
    if not skill_md.is_file():
        raise SkillIOError("not_found", f"skill '{name}' 不存在")

    # 不传 extra 时回读磁盘已有 extra，避免 UI 不感知的字段被静默清掉
    if frontmatter_extra is None:
        existing = _parse_skill_md(skill_md)
        if isinstance(existing, SkillInfo):
            frontmatter_extra = dict(existing.frontmatter_extra)
        else:
            frontmatter_extra = {}

    try:
        skill_md.write_text(
            _format_skill_md(name, description.strip(), body, frontmatter_extra),
            encoding="utf-8",
        )
    except OSError as e:
        raise SkillIOError("write_failed", f"写 skill 失败：{e}") from e

    return SkillInfo(
        name=name,
        description=description.strip(),
        location=skill_md.absolute(),
        body=body.rstrip("\n"),
        frontmatter_extra=dict(frontmatter_extra),
    )


def delete_skill(name: str, skills_dir: str | Path | None = None) -> None:
    """删除 skill：递归删除目录 `<skills_dir>/<name>/`。

    Raises:
        SkillIOError(code="invalid_name"): name 非法
        SkillIOError(code="not_found"): 目录不存在
        SkillIOError(code="write_failed"): 文件系统错误
    """
    validate_skill_name(name)
    target = _skill_dir(name, skills_dir)
    if not target.exists():
        raise SkillIOError("not_found", f"skill '{name}' 不存在")
    if not target.is_dir():
        raise SkillIOError("write_failed", f"'{name}' 不是目录")
    try:
        import shutil
        shutil.rmtree(target)
    except OSError as e:
        raise SkillIOError("write_failed", f"删除 skill 失败：{e}") from e


def rename_skill(
    old_name: str,
    new_name: str,
    skills_dir: str | Path | None = None,
    disabled_file: str | Path | None = None,
) -> SkillInfo:
    """重命名 skill：把 `<dir>/<old_name>/` 改名为 `<dir>/<new_name>/`，
    同时同步 SKILL.md frontmatter 中的 `name:` 字段（强一致：目录名 == frontmatter name）。

    若 old_name 在 disabled list 中，自动迁移到 new_name（保持禁用状态）。

    Raises:
        SkillIOError(code="invalid_name"): new_name 非法
        SkillIOError(code="not_found"): old skill 目录不存在
        SkillIOError(code="already_exists"): new skill 目录已存在
        SkillIOError(code="write_failed"): 文件系统错误
    """
    validate_skill_name(new_name)
    if old_name == new_name:
        # 同名 no-op：直接读回当前状态返回，前端 UX 一致
        skill_md = _skill_dir(old_name, skills_dir) / "SKILL.md"
        if not skill_md.is_file():
            raise SkillIOError("not_found", f"skill '{old_name}' 不存在")
        info = _parse_skill_md(skill_md)
        if isinstance(info, SkillLoadFailure):
            raise SkillIOError("write_failed", f"读取 skill 失败：{info.reason}")
        return info

    src = _skill_dir(old_name, skills_dir)
    dst = _skill_dir(new_name, skills_dir)
    if not src.is_dir():
        raise SkillIOError("not_found", f"skill '{old_name}' 不存在")
    if dst.exists():
        raise SkillIOError("already_exists", f"skill '{new_name}' 已存在")

    try:
        # 先读旧 SKILL.md 拿到 description / body / extra（rename 不动这些）
        old_md = src / "SKILL.md"
        parsed = _parse_skill_md(old_md)
        if isinstance(parsed, SkillLoadFailure):
            raise SkillIOError(
                "write_failed",
                f"旧 skill SKILL.md 解析失败，拒绝改名：{parsed.reason}",
            )

        # 改目录（os.replace 跨分区会失败 → 改用 Path.rename，同分区原子）
        src.rename(dst)
        new_md = dst / "SKILL.md"
        # 重写 SKILL.md 同步 frontmatter name 字段
        new_md.write_text(
            _format_skill_md(
                new_name,
                parsed.description,
                parsed.body,
                parsed.frontmatter_extra,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        raise SkillIOError("write_failed", f"改名失败：{e}") from e

    # 迁移 disabled list 里的状态
    try:
        disabled = read_disabled_list(disabled_file)
        if old_name in disabled:
            new_set = (disabled - {old_name}) | {new_name}
            write_disabled_list(new_set, disabled_file)
    except OSError as e:
        # 主体已成功，仅迁移 disabled 失败：log 但不回滚
        logger.warning("[SkillLoader] rename 后迁移 disabled list 失败：%s", e)

    return SkillInfo(
        name=new_name,
        description=parsed.description,
        location=new_md.absolute(),
        body=parsed.body,
        frontmatter_extra=dict(parsed.frontmatter_extra),
    )


def toggle_skill(
    name: str,
    enabled: bool,
    disabled_file: str | Path | None = None,
    *,
    valid_names: set[str] | None = None,
) -> bool:
    """启用 / 禁用 skill：修改 disabled list 文件。

    Args:
        name: skill name
        enabled: True 启用 / False 禁用
        disabled_file: 不传 → 用 DEFAULT_DISABLED_FILE
        valid_names: 如传入，则要求 name ∈ valid_names（API 层在调用前已扫盘）

    Raises:
        SkillIOError(code="not_found"): valid_names 传入且 name 不在其中
        SkillIOError(code="write_failed"): 文件系统错误

    Returns:
        新的 enabled 状态（与入参一致；返回是为了 API 回显方便）
    """
    if valid_names is not None and name not in valid_names:
        raise SkillIOError("not_found", f"skill '{name}' 不存在")

    current = read_disabled_list(disabled_file)
    if enabled:
        new_set = current - {name}
    else:
        new_set = current | {name}

    if new_set != current:
        try:
            write_disabled_list(new_set, disabled_file)
        except OSError as e:
            raise SkillIOError("write_failed", f"写 disabled 列表失败：{e}") from e
    return enabled


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
