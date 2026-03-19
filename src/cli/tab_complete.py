"""
CLI Tab 补全模块

提供基于 prompt_toolkit 的 Tab 自动补全功能。
将补全逻辑从 main.py 中解耦，便于独立测试和扩展。

使用方式：
    from cli.tab_complete import CLI_COMMANDS, make_completer

    prompt_session = PromptSession(completer=make_completer(memory, custom_prompts))
"""

from prompt_toolkit.completion import WordCompleter

from src.memory.store import MemoryStore

# 所有静态可补全命令（无需参数，或常用参数组合）
CLI_COMMANDS: list[str] = [
    "/help",
    "/ingest",
    "/ingest -m zh",
    "/ingest -m en",
    "/clear",
    "/history",
    "/session",
    "/del-session",
    "/clean-session",
    "/reload-prompts",
    "/quit",
    "/exit",
]


def make_completer(
    memory: MemoryStore,
    custom_prompts: dict[str, str] | None = None,
) -> WordCompleter:
    """
    构建 Tab 补全器，动态注入 session ID 和自定义 prompt 命令。

    每次调用都从 MemoryStore 实时读取 session 列表，
    确保新建或删除 session 后补全列表即时更新。
    custom_prompts 中的命令（如 /5g-expert）也会加入补全词表。

    Args:
        memory: 当前进程共享的 MemoryStore 实例。
        custom_prompts: scan_prompts() 返回的 {"/cmd": "内容"} 映射，可为 None。

    Returns:
        WordCompleter，可直接赋给 PromptSession.completer。
    """
    session_ids = [s["session_id"] for s in memory.list_sessions()]
    prompt_cmds: list[str] = list(custom_prompts.keys()) if custom_prompts else []
    words: list[str] = (
        CLI_COMMANDS
        + prompt_cmds
        + [f"/session {sid}" for sid in session_ids]
        + [f"/del-session {sid}" for sid in session_ids]
    )
    return WordCompleter(words, sentence=True, match_middle=False)
