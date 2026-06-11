"""模型路由：按问题难度在候选池内向更便宜的模型降级。

两条独立关注点：
  1. **候选池**：admin 勾选"已充值可用"的模型（持久化到 ``.agenta/routing_pool.json``）。
     未配置时回落到"provider 已配 api_key"的模型集合。路由只在池内选。
  2. **路由判定**：按 ``MODEL_ROUTING_MODE`` 用规则 / 小模型分类器估计难度，映射到目标
     档位（tier），再在池内选不弱于目标、且**不高于用户基准档位**的最便宜模型。

手选具体模型 = 精确锁定，**不路由**（严格用该模型）；仅 auto 档启用向下路由，基准=池内
最高档，只向下不向上。判定 / 调用出错一律软失败回落基准模型，绝不阻断主链路。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import src.config as config

logger = logging.getLogger(__name__)

# auto 档哨兵：用户未锁定具体模型，完全交给路由（基准取池内最高档）
AUTO_MODEL = "auto"

_POOL_PATH = Path(".agenta/routing_pool.json")
_pool_lock = threading.RLock()

# 档位由弱到强的秩；空档位（未标 tier）按 medium 处理
_TIER_ORDER = ["min", "low", "medium", "high", "max"]


def _tier_rank(model_id: str) -> int:
    m = config.MODEL_CONFIGS.get(model_id)
    tier = (m.tier if m else "") or "medium"
    try:
        return _TIER_ORDER.index(tier)
    except ValueError:
        return _TIER_ORDER.index("medium")


def _price_of(model_id: str) -> float:
    """模型单价代理值（输入价 + 输出价，每 1M token）；未知按 0（最便宜）。"""
    pin, pout = config.MODEL_PRICING_DEFAULTS.get(model_id, (0.0, 0.0))
    return float(pin) + float(pout)


# ── 候选池持久化（admin 全局） ──────────────────────────────────────────────────


def _read_pool_file() -> list[str]:
    if not _POOL_PATH.exists():
        return []
    try:
        data = json.loads(_POOL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, str) and x in config.MODEL_CONFIGS]


def get_pool_config() -> list[str]:
    """admin 显式勾选的候选池（原样返回）；空 = 未配置（回落 available）。"""
    with _pool_lock:
        return _read_pool_file()


def set_pool_config(model_ids: list[str]) -> list[str]:
    """保存候选池（只接受已知模型 id）；返回保存后的列表。"""
    cleaned = [m for m in dict.fromkeys(model_ids) if m in config.MODEL_CONFIGS]
    with _pool_lock:
        _POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _POOL_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_POOL_PATH)
    return cleaned


def _available_models() -> list[str]:
    """provider 已配 api_key 的模型（候选池未显式配置时的默认池）。"""
    out: list[str] = []
    for mid, m in config.MODEL_CONFIGS.items():
        prov = config.PROVIDER_CONFIGS.get(m.provider)
        if prov and prov.api_key:
            out.append(mid)
    return out


def effective_pool() -> list[str]:
    """路由实际使用的候选池：显式配置优先，否则回落到 available。"""
    configured = get_pool_config()
    return configured if configured else _available_models()


# ── 难度判定 ────────────────────────────────────────────────────────────────

# 偏难信号：要求推理 / 设计 / 比较 / 解释原因等
_HARD_PATTERNS = re.compile(
    r"(为什么|原因|分析|比较|对比|设计|架构|推导|证明|权衡|优化|方案|实现思路|debug|"
    r"why|analyz|compare|design|architect|derive|prove|trade.?off|optimi|refactor)",
    re.IGNORECASE,
)
# 偏易信号：定义 / 翻译 / 简单事实查询
_EASY_PATTERNS = re.compile(
    r"(是什么|什么是|定义|翻译|列出|简述|多少|哪一?年|哪个|缩写|全称|"
    r"what is|define|translat|list|how many|when did)",
    re.IGNORECASE,
)


def _rule_difficulty(query: str) -> str:
    """规则启发估计难度：返回 easy / medium / hard。"""
    q = (query or "").strip()
    n = len(q)
    has_code = "```" in q or "def " in q or "class " in q
    hard = bool(_HARD_PATTERNS.search(q)) or has_code or n > 160
    easy = bool(_EASY_PATTERNS.search(q)) and n <= 60
    if hard:
        return "hard"
    if easy:
        return "easy"
    return "medium"


_DIFFICULTY_TO_TIER = {"easy": "min", "medium": "medium", "hard": "max"}


def _classify_difficulty_llm(query: str, classifier_model: str) -> str | None:
    """调小模型给难度打分（1-5），映射 easy / medium / hard。失败返回 None（软失败）。"""
    if not classifier_model or classifier_model not in config.MODEL_CONFIGS:
        return None
    try:
        from src.llm.provider import chat

        with config.use_llm_prefs(classifier_model, False, config.THINKING_BUDGET):
            resp = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是问题难度分级器。只输出一个数字 1-5："
                            "1-2=简单事实/定义/翻译，3=一般问答，4-5=需多步推理/设计/分析。"
                            "不要输出任何解释。"
                        ),
                    },
                    {"role": "user", "content": (query or "")[:500]},
                ],
                temperature=0.0,
            )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"[1-5]", text)
        if not m:
            return None
        score = int(m.group())
        difficulty = "easy" if score <= 2 else "hard" if score >= 4 else "medium"
        logger.info(
            "[router] 难度分类器 %s 判定 score=%d → %s", classifier_model, score, difficulty
        )
        return difficulty
    except Exception:
        logger.warning("[router] 难度分类器调用失败（已忽略，回落规则）", exc_info=True)
        return None


def _difficulty(query: str, mode: str, classifier_model: str) -> tuple[str, str]:
    """返回 (难度, 实际判定方式)。hybrid 仅在规则判为 medium 时再调分类器。"""
    if mode == "classifier":
        d = _classify_difficulty_llm(query, classifier_model)
        if d is not None:
            return d, "classifier"
        return _rule_difficulty(query), "rule(fallback)"
    if mode == "hybrid":
        d = _rule_difficulty(query)
        if d == "medium":
            d2 = _classify_difficulty_llm(query, classifier_model)
            if d2 is not None:
                return d2, "hybrid"
        return d, "hybrid(rule)"
    return _rule_difficulty(query), "rule"


# ── 路由决策 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteDecision:
    """一次路由的结果。

    model_id:  实际要用的模型（已解析，绝不是 auto）。
    baseline:  "不路由时本应使用"的模型，用于估算节省。
    downgraded: 是否真的降到了更便宜的档位。
    difficulty / mode / reason: 供日志 / 看板说明。
    """

    model_id: str
    baseline: str
    downgraded: bool
    difficulty: str
    mode: str
    reason: str


def _resolve_baseline(selected_model: str, pool: list[str]) -> str:
    """基准模型：手选具体模型即该模型；auto 取池内最高档（最贵优先做平局）。"""
    if selected_model and selected_model != AUTO_MODEL and selected_model in config.MODEL_CONFIGS:
        return selected_model
    if not pool:
        return config.ACTIVE_MODEL
    return max(pool, key=lambda m: (_tier_rank(m), _price_of(m)))


def route(
    query: str,
    selected_model: str,
    *,
    enabled: bool | None = None,
    mode: str | None = None,
    classifier_model: str | None = None,
) -> RouteDecision:
    """按难度在候选池内向下选模型；只降不升，软失败回落基准。"""
    enabled = config.MODEL_ROUTING_ENABLED if enabled is None else enabled
    mode = (mode if mode is not None else config.MODEL_ROUTING_MODE) or "rule"
    classifier_model = (
        classifier_model if classifier_model is not None
        else config.MODEL_ROUTING_CLASSIFIER_MODEL
    )

    pool = effective_pool()
    baseline = _resolve_baseline(selected_model, pool)

    # 未启用：直接用基准，不路由
    if not enabled:
        return RouteDecision(baseline, baseline, False, "", "off", f"路由未启用，用 {baseline}")

    # 手选具体模型 = 精确锁定，不路由（仅 auto 档才向下降级）
    if selected_model and selected_model != AUTO_MODEL and selected_model in config.MODEL_CONFIGS:
        return RouteDecision(baseline, baseline, False, "", mode, f"手选 {baseline}，精确锁定不路由")

    base_rank = _tier_rank(baseline)
    # 只考虑池内"不强于基准"的模型（含基准本身作为下限）
    candidates = [m for m in pool if _tier_rank(m) <= base_rank]
    if baseline not in candidates:
        candidates.append(baseline)
    if len(candidates) <= 1:
        return RouteDecision(
            baseline, baseline, False, "", mode, f"候选池无比 {baseline} 更便宜的模型"
        )

    difficulty, used_mode = _difficulty(query, mode, classifier_model)
    target_rank = min(_TIER_ORDER.index(_DIFFICULTY_TO_TIER[difficulty]), base_rank)

    # 不弱于目标档位的候选里挑最便宜；没有则退回全部候选里最便宜
    eligible = [m for m in candidates if _tier_rank(m) >= target_rank] or candidates
    chosen = min(eligible, key=lambda m: (_price_of(m), _tier_rank(m)))

    downgraded = chosen != baseline
    reason = (
        f"难度={difficulty}（{used_mode}）→ 目标档位={_TIER_ORDER[target_rank]}，"
        f"基准 {baseline} → 选用 {chosen}"
    )
    return RouteDecision(chosen, baseline, downgraded, difficulty, used_mode, reason)
