"""一次性诊断脚本：扫历史 messages 找非法 function name。"""
import json
import re
import sqlite3
import sys
from pathlib import Path

# 加 src 到 path 让我们能 import 当前 tools 注册
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VALID = re.compile(r"^[a-zA-Z0-9_-]+$")

# 1. 扫所有 messages 里的 tool_calls
conn = sqlite3.connect("sqlite_db/chat_history.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT session_id, role, content, tool_calls FROM messages "
    "WHERE tool_calls IS NOT NULL AND tool_calls != '' AND tool_calls != '[]'"
).fetchall()
print(f"==> 含 tool_calls 的历史消息数: {len(rows)}")

bad_in_history = []
for r in rows:
    try:
        tc = json.loads(r["tool_calls"])
    except Exception:
        continue
    if not isinstance(tc, list):
        continue
    for c in tc:
        fn = (c.get("function") or {}).get("name") or c.get("name") or ""
        if not VALID.match(fn):
            bad_in_history.append((r["session_id"][:8], fn))
print(f"==> 历史中非法 function name 条数: {len(bad_in_history)}")
for sid, fn in bad_in_history[:20]:
    print(f"   session={sid}  name={fn!r}")

# 2. 扫当前注册的 tools（仅检查名字，不实际执行）
print()
print("==> 检查当前已注册 tool 的名字")
try:
    from src.agent.agent import Agent

    a = Agent()
    tools = a.get_tools() if hasattr(a, "get_tools") else []
    bad_current = []
    for t in tools:
        name = (t.get("function") or {}).get("name") or t.get("name") or ""
        if not VALID.match(name):
            bad_current.append(name)
    print(f"   注册 tool 总数: {len(tools)}")
    print(f"   非法名字数: {len(bad_current)}")
    for n in bad_current:
        print(f"     -> {n!r}")
except Exception as e:
    print(f"   检查失败: {e}")
