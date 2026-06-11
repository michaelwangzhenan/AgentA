"""RAG 答案质量评委：faithfulness（忠实度）与 answer-relevance（相关度）。

复用 ``tools.eval_common`` 的 0-5 分 judge 机制，只是固定好两类 prompt：

- **faithfulness**：答案的论断是否都能在「检索到的资料」里找到支撑（编造 / 无中生有扣分）。
  把问题 + 资料拼进 judge 的"任务输入"，答案作为"被评估的输出"。
- **answer-relevance**：答案是否切题、直接回应了用户问题（不跑题、不答非所问）。

两者都返回 ``JudgeResult``，失败时 score 为 None（不抛异常）。
"""

from __future__ import annotations

from tools.eval_common import JudgeResult, judge_with_llm

_FAITHFULNESS_CRITERIA = """- **有据可依**（满分 4）：答案里的每个事实性论断都能在检索资料中找到支撑；
  能找到则高分，出现资料里没有的信息（编造 / 张冠李戴）则按比例扣分
- **无矛盾**（满分 1）：答案不与检索资料相互冲突"""

_RELEVANCE_CRITERIA = """- **切题**（满分 3）：答案直接回应用户问题的核心诉求，不答非所问
- **聚焦**（满分 2）：不堆砌与问题无关的冗余内容；离题越多扣越多"""


def judge_faithfulness(question: str, context: str, answer: str) -> JudgeResult:
    """评 RAG 答案忠实度：答案是否忠于检索到的资料、不编造。"""
    task_input = f"## 用户问题\n{question}\n\n## 检索到的资料\n{context}"
    return judge_with_llm(
        role_intro="你是一个 RAG 答案忠实度（faithfulness）评委，只关心答案是否忠于给定资料",
        prompt=task_input,
        output=answer,
        criteria=_FAITHFULNESS_CRITERIA,
    )


def judge_answer_relevance(question: str, answer: str) -> JudgeResult:
    """评答案相关度：答案是否切题、直接回应了用户问题。"""
    return judge_with_llm(
        role_intro="你是一个答案相关度（answer-relevance）评委，只关心答案是否切题",
        prompt=question,
        output=answer,
        criteria=_RELEVANCE_CRITERIA,
    )
