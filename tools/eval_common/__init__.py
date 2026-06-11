"""评估通用基础设施：跨 RAG / agent 各域复用的 LLM-judge 核心。

- `judge_with_llm`：给 (prompt, output) 按 criteria 调 LLM 打分，软失败返回 `JudgeResult`。
- 域专用评委（RAG 的 faithfulness / 相关度、plan / quiz 的评分标准）各自在所属目录里写，
  只 import 这里的通用核心，互不依赖。
"""

from tools.eval_common.llm_judge import JudgeResult, judge_with_llm

__all__ = [
    "JudgeResult",
    "judge_with_llm",
]
