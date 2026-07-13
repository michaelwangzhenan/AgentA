"""Golden 生成可选 LLM / 数量 — 入库 UI、L2、CLI 共用。"""

from __future__ import annotations

import src.config as config

GOLDEN_LLM_NONE = "none"
GOLDEN_LLM_KIMI = "kimi-k2.5"
GOLDEN_LLM_DEEPSEEK = "deepseek-v4-flash"

GOLDEN_LLM_CHOICES: tuple[str, ...] = (
    GOLDEN_LLM_NONE,
    GOLDEN_LLM_KIMI,
    GOLDEN_LLM_DEEPSEEK,
)

GOLDEN_LLM_LABELS: dict[str, str] = {
    GOLDEN_LLM_NONE: "不生成",
    GOLDEN_LLM_KIMI: "Kimi K2.5",
    GOLDEN_LLM_DEEPSEEK: "DeepSeek V4 Flash",
}

GOLDEN_MAX_Q_MIN = 1
GOLDEN_MAX_Q_MAX = 20
GOLDEN_CHARS_PER_Q = 1000


def normalize_golden_llm(raw: str | None) -> str:
    """规范为 GOLDEN_LLM_CHOICES 之一；未知值回落 none。"""
    s = (raw or "").strip().lower()
    if s in ("", "null", "none"):
        return GOLDEN_LLM_NONE
    if s in GOLDEN_LLM_CHOICES:
        return s
    # 兼容旧文案 / 简称
    if s in ("kimi", "kimi2.5", "kimi-k2.5"):
        return GOLDEN_LLM_KIMI
    if s in ("deepseek", "deepseek-v4-flash", "deepseek v4 flash"):
        return GOLDEN_LLM_DEEPSEEK
    return GOLDEN_LLM_NONE


def effective_golden_llm(request_llm: str | None) -> str:
    """请求参数优先，缺省读 config.EVAL_GOLDEN_LLM。"""
    if request_llm is not None and str(request_llm).strip() != "":
        return normalize_golden_llm(request_llm)
    return normalize_golden_llm(config.EVAL_GOLDEN_LLM)


def should_generate_golden(llm_choice: str) -> bool:
    return normalize_golden_llm(llm_choice) != GOLDEN_LLM_NONE


def model_id_for_golden(llm_choice: str) -> str:
    """把 golden LLM 选项转为 MODEL_CONFIGS 的 model id；none 时抛 ValueError。"""
    choice = normalize_golden_llm(llm_choice)
    if choice == GOLDEN_LLM_NONE:
        raise ValueError("golden LLM 为 none，不应调用 model_id_for_golden")
    if choice not in (GOLDEN_LLM_KIMI, GOLDEN_LLM_DEEPSEEK):
        raise ValueError(f"不支持的 golden LLM: {choice!r}")
    return choice


def clamp_golden_max_q(value: int | None) -> int:
    """出题上限：限制在合法范围；None 时用 config.EVAL_GOLDEN_MAX_Q。"""
    n = config.EVAL_GOLDEN_MAX_Q if value is None else int(value)
    return max(GOLDEN_MAX_Q_MIN, min(GOLDEN_MAX_Q_MAX, n))


def compute_golden_max_q(char_count: int, cap: int | None = None) -> int:
    """按字数自动算题数（每 GOLDEN_CHARS_PER_Q 一题），再与 UI/env 上限取 min。"""
    auto = max(GOLDEN_MAX_Q_MIN, (max(0, int(char_count)) + GOLDEN_CHARS_PER_Q - 1) // GOLDEN_CHARS_PER_Q)
    return min(auto, clamp_golden_max_q(cap))


def resolve_llm_for_manual_generate(request_llm: str | None) -> str:
    """L2 手动「生成评估」：显式请求优先，否则 env；仍为 none 时默认 kimi-k2.5。"""
    choice = effective_golden_llm(request_llm)
    if choice == GOLDEN_LLM_NONE:
        return GOLDEN_LLM_KIMI
    return choice


def api_gen_options() -> dict:
    """供前端下拉用的选项与默认数量。"""
    return {
        "llm_choices": [
            {"value": v, "label": GOLDEN_LLM_LABELS[v]} for v in GOLDEN_LLM_CHOICES
        ],
        "max_q_default": config.EVAL_GOLDEN_MAX_Q,
        "max_q_min": GOLDEN_MAX_Q_MIN,
        "max_q_max": GOLDEN_MAX_Q_MAX,
    }
