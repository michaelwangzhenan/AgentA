"""
LLM-judge 核心实现 —— `judge_with_llm` 函数式 helper

用法（[`eval_plan.py`](../plan/eval_plan.py) / [`eval_learning_plan.py`](../plan_business/eval_learning_plan.py)）：

    from tools.agent_eval.judge import judge_with_llm

    res = judge_with_llm(
        role_intro="你是一个学习计划质量评委",
        prompt=user_question,
        output=plan_markdown,
        criteria=\"\"\"- **完整性**（满分 1.5）：阶段 + 任务覆盖学习目标的关键方面
- **顺序合理性**（满分 1）：阶段先后依赖正确
- **可执行性**（满分 1.5）：每条任务动词起头、可独立勾选
- **时间分配**（满分 1）：阶段时长与目标周数协调
\"\"\",
    )
    if res.score is not None and res.score >= 4.0:
        ...

返回 `JudgeResult(score, reason, raw)`；任何失败（LLM 异常 / 非 JSON / score 越界）
score 为 None、reason 为人类可读错误说明，raw 保留 LLM 原始返回便于排查。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from src.llm.provider import chat

logger = logging.getLogger(__name__)

# 评委 system prompt 模板：动态填入 role / criteria / 分值区间
_JUDGE_SYS_TEMPLATE = """{role_intro}。下面给你看：

1. 任务输入（用户原始问题 / 目标 / 上下文）
2. 被评估的输出（Agent 产出的 plan / 答案 / 任务清单等）

请按以下维度评分（每项加权后累加，总分范围 {score_min}-{score_max}）：

{criteria}

输出**严格的 JSON**，格式：

{{"score": <{score_min}-{score_max} 之间浮点数>, "reason": "<≤ 80 字简评>"}}

只输出 JSON 一段，不要带 markdown 代码块、不要前后说明、不要换行外的多余字符。"""

# 用户消息模板：拼问题 + 输出，分隔清晰让 judge LLM 不混淆
_JUDGE_USER_TEMPLATE = """## 任务输入
{prompt}

## 被评估的输出
{output}"""


@dataclass(frozen=True)
class JudgeResult:
    """
    LLM-judge 单次评分结果。

    Attributes:
        score:  分数；失败时为 None。
        reason: judge 给的简评（≤ 80 字）；失败时为错误说明。
        raw:    LLM 原始返回字符串，便于排查解析错误。
    """
    score: float | None
    reason: str
    raw: str

    @property
    def ok(self) -> bool:
        """便捷判断：score 是否取到。"""
        return self.score is not None


def judge_with_llm(
    *,
    prompt: str,
    output: str,
    criteria: str,
    role_intro: str = "你是一个 Agent 输出质量评委",
    score_min: float = 0.0,
    score_max: float = 5.0,
    temperature: float = 0.0,
) -> JudgeResult:
    """
    调一次 LLM 给 (prompt, output) 评分，按 criteria 维度。

    Args:
        prompt:     用户原始问题 / 任务输入文本。
        output:     被评估的 Agent 输出（plan markdown / 答案 / 任务列表等）。
        criteria:   评分维度描述，建议用 markdown 列表（每项标注满分权重）。
        role_intro: 评委身份描述（用于 system prompt 第一句），不同业务可自定义。
        score_min:  分数下限，默认 0.0。
        score_max:  分数上限，默认 5.0。
        temperature: chat 调用温度，默认 0.0（评分场景需稳定）。

    Returns:
        JudgeResult。score=None 表示失败（reason 含错误原因）；不抛异常。
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return JudgeResult(score=None, reason="prompt 为空，跳过 judge", raw="")
    if not isinstance(output, str) or not output.strip():
        return JudgeResult(score=None, reason="output 为空，跳过 judge", raw="")
    if score_max <= score_min:
        return JudgeResult(score=None, reason=f"score 区间非法 [{score_min}, {score_max}]", raw="")

    sys_prompt = _JUDGE_SYS_TEMPLATE.format(
        role_intro=role_intro.strip(),
        criteria=criteria.strip(),
        score_min=score_min,
        score_max=score_max,
    )
    user_msg = _JUDGE_USER_TEMPLATE.format(prompt=prompt.strip(), output=output.strip())
    judge_msgs = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        resp = chat(judge_msgs, temperature=temperature)
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001 — 评估场景需把所有 LLM 异常软返回
        logger.warning("[judge] LLM 调用失败: %s", e)
        return JudgeResult(score=None, reason=f"judge LLM 调用失败：{e}", raw="")

    # 容错解析：剥离 markdown 代码块 / 取首个 {...} JSON 对象
    m = re.search(r"\{.*?\}", raw.replace("\n", " "), re.DOTALL)
    if not m:
        return JudgeResult(score=None, reason=f"judge 返回非 JSON：{raw[:200]!r}", raw=raw)
    try:
        data = json.loads(m.group(0))
        score = float(data.get("score", -1))
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return JudgeResult(score=None, reason=f"judge JSON 解析失败：{e}", raw=raw)

    if not (score_min <= score <= score_max):
        return JudgeResult(
            score=None,
            reason=f"judge score 越界 [{score_min}, {score_max}]：{score}",
            raw=raw,
        )
    reason = str(data.get("reason", "")).strip() or "（无）"
    return JudgeResult(score=score, reason=reason, raw=raw)
