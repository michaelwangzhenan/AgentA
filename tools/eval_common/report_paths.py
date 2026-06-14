"""离线评估报告的统一落盘位置：``tools/reports/<eval>/``。

各 eval 脚本不再各自往 ``agent_eval/reports`` / ``rag_eval/reports`` 写，统一调
``reports_dir("<eval>")`` 拿到 ``tools/reports/<eval>/``（自动建目录）。后端报告接口
只扫这一个根（递归），report name = 相对 ``tools/reports`` 的路径。
"""
from __future__ import annotations

from pathlib import Path

# 本文件在 tools/eval_common/ 下；parents[1] = tools/
REPORTS_ROOT = Path(__file__).resolve().parents[1] / "reports"


def reports_dir(eval_name: str) -> Path:
    """返回 ``tools/reports/<eval_name>/`` 并确保目录存在。"""
    d = REPORTS_ROOT / eval_name
    d.mkdir(parents=True, exist_ok=True)
    return d
