"""UserRules —— 把用户 rules 文本包成 system prompt 的 `<user_rules>` 块。

rules 文本本身由 Agent 从 UserStore（每用户一份）按当前用户即时读取，见
`agent._get_active_rules()`；本模块只负责"拼块"，不感知存储来源。
"""
from __future__ import annotations


def build_rules_block(rules_text: str | None) -> str:
    """把 rules 纯文本包成 `<user_rules>...</user_rules>` 块。

    rules 是用户本人写的可信偏好（对齐 Cursor Rules / GHC Custom Instructions），
    属于用户主权内容、应被遵守；不做防注入清洗（那是 untrusted 数据隔离层的职责）。

    Args:
        rules_text: 当前用户的 rules 文本；`None` 或空串 → 返回空串。

    Returns:
        以 `\\n\\n<user_rules>` 开头的字符串，或空串（让上层直接 `base + ""`）。
    """
    if not rules_text:
        return ""
    return (
        "\n\n<user_rules>\n"
        "以下是该用户设定的个人偏好规则，请在回答时遵守：\n"
        + rules_text
        + "\n</user_rules>"
    )
