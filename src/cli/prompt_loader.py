"""
自定义 Prompt 加载模块

扫描 advanced/prompts/ 目录下的 *.prompt.md 文件，
将文件名（去掉 .prompt.md 后缀）映射为 CLI 命令（加 / 前缀）。

使用方式：
    from src.cli.prompt_loader import scan_prompts

    custom_prompts = scan_prompts("advanced/prompts")
    # {"/ 5g-expert": "...", "/code-assistant": "..."}
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 合法的 prompt 名称：只允许字母、数字、连字符和下划线
PROMPT_NAME_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")

# 内置保留命令（不含 / 前缀），prompt 文件名不允许与这些冲突
RESERVED_COMMANDS: frozenset[str] = frozenset({
    "help",
    "ingest",
    "clear",
    "history",
    "session",
    "del-session",
    "clean-session",
    "reload-prompts",
    "quit",
    "exit",
})


def load_prompt_file(path: str | Path) -> str:
    """
    读取单个 .prompt.md 文件内容，去除首尾空白。

    Args:
        path: 文件路径（str 或 Path）。

    Returns:
        文件正文内容（已 strip）。
    """
    return Path(path).read_text(encoding="utf-8").strip()


def scan_prompts(prompts_dir: str | Path) -> dict[str, str]:
    """
    扫描目录下所有 *.prompt.md 文件，返回命令→内容映射。

    文件名规则：{prompt_name}.prompt.md → CLI 命令 /{prompt_name}
    跳过条件（记录 warning）：
      - 文件名（去后缀后）不匹配 PROMPT_NAME_RE
      - 名称与 RESERVED_COMMANDS 中的内置命令冲突

    Args:
        prompts_dir: 要扫描的目录路径，不存在时静默返回空字典。

    Returns:
        {"/cmd_name": "prompt 内容"} 映射，按文件名字母序排列。
    """
    dir_path = Path(prompts_dir)
    if not dir_path.exists():
        return {}
    if not dir_path.is_dir():
        logger.warning("[PromptLoader] %s 不是目录，跳过扫描", prompts_dir)
        return {}

    result: dict[str, str] = {}
    suffix = ".prompt.md"

    for file in sorted(dir_path.iterdir()):
        # 只处理 *.prompt.md 文件
        if not file.is_file() or not file.name.endswith(suffix):
            continue

        # 提取 prompt 名称（去掉 .prompt.md 后缀）
        prompt_name = file.name[: -len(suffix)]

        # 校验名称合法性
        if not PROMPT_NAME_RE.match(prompt_name):
            logger.warning(
                "[PromptLoader] 跳过 %s：名称 '%s' 包含非法字符（只允许字母、数字、- 和 _）",
                file.name,
                prompt_name,
            )
            continue

        # 校验不与内置命令冲突
        if prompt_name.lower() in RESERVED_COMMANDS:
            logger.warning(
                "[PromptLoader] 跳过 %s：名称 '%s' 与内置命令冲突",
                file.name,
                prompt_name,
            )
            continue

        try:
            content = load_prompt_file(file)
            cmd = f"/{prompt_name}"
            result[cmd] = content
            logger.debug("[PromptLoader] 已加载 %s → %s", file.name, cmd)
        except OSError as e:
            logger.warning("[PromptLoader] 读取 %s 失败：%s", file.name, e)

    return result
