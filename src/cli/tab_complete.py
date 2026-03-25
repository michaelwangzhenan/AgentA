"""
CLI Tab 补全模块

提供基于 prompt_toolkit 的 Tab 自动补全功能。
将补全逻辑从 main.py 中解耦，便于独立测试和扩展。

使用方式：
    from cli.tab_complete import CLI_COMMANDS, make_completer

    prompt_session = PromptSession(completer=make_completer(memory, custom_prompts))
"""

from collections.abc import Sequence

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
    "/reload-skills",
    "/save",
    "/quit",
    "/exit",
]


def make_completer(
    memory: MemoryStore,
    custom_prompts: dict[str, str] | None = None,
    custom_skills: Sequence[str] | None = None,
) -> WordCompleter:
    """
    构建 Tab 补全器，动态注入 session ID、自定义 prompt 命令和 skill 命令。

    每次调用都从 MemoryStore 实时读取 session 列表，
    确保新建/删除 session 后补全列表即时更新。

    Args:
        memory: 当前进程共享的 MemoryStore 实例。
        custom_prompts: scan_prompts() 返回的 {"/cmd": "内容"} 映射，可为 None。
        custom_skills: skill 命令名称列表（如 ["/example-skill"]），可为 None。

    Returns:
        WordCompleter，可直接赋给 PromptSession.completer。
    """
    session_list = memory.list_sessions()
    prompt_cmds: list[str] = list(custom_prompts.keys()) if custom_prompts else []
    skill_cmds: list[str] = list(custom_skills) if custom_skills else []

    # Tab 候选词：实际输入内容为完整 session_id，显示文字为“短 id + 首问”
    session_words: list[str] = []
    session_display: dict[str, str] = {}
    del_words: list[str] = []
    del_display: dict[str, str] = {}
    for s in session_list:
        full_id = s["session_id"]
        short_id = full_id[:8]
        first_q = (s["first_user_msg"] or "").replace("\n", " ")[:30]
        label = f"{short_id}  {first_q}" if first_q else short_id

        session_cmd = f"/session {full_id}"
        session_words.append(session_cmd)
        session_display[session_cmd] = f"/session {label}"

        del_cmd = f"/del-session {full_id}"
        del_words.append(del_cmd)
        del_display[del_cmd] = f"/del-session {label}"

    display_dict = {**session_display, **del_display}
    words: list[str] = CLI_COMMANDS + prompt_cmds + skill_cmds + session_words + del_words
    return WordCompleter(words, display_dict=display_dict, sentence=True, match_middle=False)
