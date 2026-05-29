"""
LLM-judge 通用 helper（Phase 2.2 [§4.9.7 D6 / D11](../../../docs/iter_2_agent.md#497-学习计划生成-phase-22)）

把"用 LLM 给主观输出打分"这件常见事抽成函数式工具，给 plan / learning-plan / 答案质量
等多个评估场景共享。设计原则：
  - 函数式而非 class（D11："function-level helper 不是 framework"）
  - 不引入新依赖（沿用 `src.llm.provider.chat`）
  - 失败软返回 `JudgeResult(score=None, reason=...)`，不抛异常（评估脚本能继续跑完其它 case）
"""

from tools.agent_eval.judge.llm_judge import JudgeResult, judge_with_llm

__all__ = ["JudgeResult", "judge_with_llm"]
