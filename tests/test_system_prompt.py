"""
SYSTEM_PROMPT 不变量测试 —— 锁住"基石契约"，防止以后被破坏。

设计原则（对齐 tests/test_agent.py:360 已确立的"防脆性"准则）：
- **不测自然语言措辞**（不写"必须包含'用具体名词'"这种脆性 assert，prompt 迭代受阻）
- **只测工程契约级 token**：tool 名、注入 block 名、untrusted 标签名、citation 分隔符等
- 这些 token 是 prompt 与代码层的硬绑定（改任一边都得同步另一边），所以测试有 ROI

每个测试在 docstring 里说明"为什么必须存在"，方便以后协作者读懂意图。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.agent import SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 1. 结构性不变量：H2 节点必须存在
# ---------------------------------------------------------------------------
class TestSystemPromptStructure:
    """SYSTEM_PROMPT 的 4 大功能节是基石；任何节缺失都意味着关键约束丢了。"""

    def test_h2_plan_tool_protocol_exists(self) -> None:
        """Plan / Tool 决策入口节必须存在 —— 缺失则 LLM 无 plan 协议指引，复杂任务会
        跳过 make_plan 直接连调 search_knowledge。"""
        assert "## Plan / Tool 调用协议" in SYSTEM_PROMPT

    def test_h2_plan_tool_marks_highest_priority(self) -> None:
        """Plan 协议必须被标为最高优先级 —— 防 LLM 把它跟"## 工具策略"视为同级章节
        而跳过 make_plan 判定。"""
        idx = SYSTEM_PROMPT.find("## Plan / Tool 调用协议")
        assert idx >= 0
        # 在标题及其后 200 字内必须有"最高优先级"标记
        nearby = SYSTEM_PROMPT[idx : idx + 200]
        assert "最高优先级" in nearby, (
            "Plan / Tool 协议必须显式标为最高优先级，避免 LLM 跳过 make_plan 判定。"
            f"实际章节首段：{nearby!r}"
        )

    def test_h2_tool_strategy_exists(self) -> None:
        """工具策略节必须存在 —— 缺失则 LLM 没有 query 准备 / 重试 / fallback 指引。"""
        assert "## 工具策略" in SYSTEM_PROMPT

    def test_h2_citation_exists(self) -> None:
        """引用规范节必须存在 —— 缺失会让 LLM 不知道 [N] 编号体系，或自己手写
        sources 列表与系统自动追加的块重复。"""
        assert "## 引用规范" in SYSTEM_PROMPT

    def test_h2_data_isolation_exists(self) -> None:
        """数据隔离节必须存在 —— 这是最高优先级安全约束，缺失则 prompt injection
        防线瓦解。"""
        assert "## 数据隔离" in SYSTEM_PROMPT

    def test_length_floor(self) -> None:
        """prompt 字符数 >= 1500 —— 防止整段被误删（当前约 2500，floor 留 60% 余量）。"""
        assert len(SYSTEM_PROMPT) >= 1500, (
            f"SYSTEM_PROMPT 仅 {len(SYSTEM_PROMPT)} 字符，可能误删大段；"
            "正常约 2500，floor 1500 留 60% 余量"
        )

    def test_length_ceiling(self) -> None:
        """prompt 字符数 < 4000 —— 防止业务偏好 / 应用场景假设回流（应放 rules.md）。"""
        assert len(SYSTEM_PROMPT) < 4000, (
            f"SYSTEM_PROMPT 已达 {len(SYSTEM_PROMPT)} 字符，疑似有业务偏好回流；"
            "应用偏好应放 .agenta/rules.md，prompt 只放绝对系统指令"
        )


# ---------------------------------------------------------------------------
# 2. 工具契约：所有 LLM 调度的 tool 名必须在 prompt 里出现
# ---------------------------------------------------------------------------
class TestSystemPromptToolContract:
    """prompt 引用的 tool 名 ↔ 代码层 tool schema 必须一致；改其一不改另一会静默失效。"""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "search_knowledge",
            "web_search",
            "fetch_url",
        ],
    )
    def test_kb_tools_referenced(self, tool_name: str) -> None:
        """三个 RAG 检索工具必须在工具列表 + 工具策略段被引用。"""
        assert tool_name in SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "plan_tool",
        [
            "make_plan",
            "update_step",
            "abort_plan",
        ],
    )
    def test_plan_tools_referenced(self, plan_tool: str) -> None:
        """三个 plan 协议工具必须在 Plan / Tool 调用协议段被引用，
        否则 LLM 不知道 plan 周期怎么走。"""
        assert plan_tool in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3. 注入 block 契约：rules_loader / memory_manager 拼的 block 名必须在 prompt 里被引用
# ---------------------------------------------------------------------------
class TestSystemPromptInjectionBlocks:
    """SYSTEM_PROMPT 引用了两个外部注入 block，必须与代码层拼接的 block 名一致。"""

    def test_project_rules_block_referenced(self) -> None:
        """`<project_rules>` 标签由 rules_loader.build_rules_block() 拼接（line 97）；
        prompt 必须引用它，否则 LLM 不知道项目偏好在哪个块。"""
        assert "<project_rules>" in SYSTEM_PROMPT

    def test_user_context_block_referenced(self) -> None:
        """`<user_context>` 标签由 memory_manager.build_system_prompt() 拼接；
        prompt 必须引用它，否则 LLM 不知道运行期学到的偏好在哪个块。"""
        assert "<user_context>" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 4. 安全契约：untrusted 标签 + 清洗标记必须在数据隔离段被声明
# ---------------------------------------------------------------------------
class TestSystemPromptSecurityContract:
    """security_filter 包装外部数据时用的标签 ↔ prompt 数据隔离段声明的标签必须一致。"""

    @pytest.mark.parametrize(
        "tag",
        [
            "<untrusted_doc>",
            "<untrusted_web>",
            "<untrusted_tool>",
        ],
    )
    def test_untrusted_tags_declared(self, tag: str) -> None:
        """security_filter 用 `<untrusted_{doc|web|tool}>` 包外部数据（line 82）；
        prompt 数据隔离段必须声明这三个标签为'数据不是指令'，否则
        LLM 不知道 untrusted 内容该按数据对待，prompt injection 防线瓦解。"""
        assert tag in SYSTEM_PROMPT

    def test_sanitize_marker_referenced(self) -> None:
        """tools.py 用 `[⚠️ 已清洗]` 标记被启发式过滤过的工具返回（line 548/783/2250）；
        prompt 必须告知 LLM 这是"数据已被清洗"信号，否则 LLM 可能：
        (a) 追问被删的是什么 → 二次诱导风险；
        (b) 尝试基于残缺内容凭空补全 → 行为不可控。"""
        assert "已清洗" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 5. 引用契约：citation_builder 的 sources 分隔符 + 编号格式必须在 prompt 里被说明
# ---------------------------------------------------------------------------
class TestSystemPromptCitationContract:
    """citation_builder 自动追加的 sources 块格式 ↔ prompt 引用规范段说明必须一致。"""

    def test_sources_separator_referenced(self) -> None:
        """citation_builder._SOURCES_HEADER = "— sources —"（line 41）；
        prompt 必须告知 LLM 这是系统自动追加的分隔符，禁止手写否则会有两份。"""
        assert "— sources —" in SYSTEM_PROMPT

    def test_citation_no_manual_sources_directive(self) -> None:
        """prompt 必须包含"不要手写 sources"的指令，否则 LLM 自动写一份 + 系统再追加一份 → 重复。"""
        # 不约束具体措辞，但必须含"手写"或"sources 列表"语义的硬约束词
        has_warning = (
            "不要手写" in SYSTEM_PROMPT
            or "禁止手写" in SYSTEM_PROMPT
            or "不写 references" in SYSTEM_PROMPT
        )
        assert has_warning, (
            "引用规范段必须明确告诉 LLM 不要手写 sources 列表，"
            "否则与 citation_builder 自动追加的块重复"
        )


# ---------------------------------------------------------------------------
# 6. Fallback：<project_rules> 缺席时的行为指引必须存在
# ---------------------------------------------------------------------------
class TestSystemPromptFallback:
    """rules_loader 在 rules.md 缺失 / OSError / USER_RULES_ENABLED=false 时
    返回 None 且不拼接 `<project_rules>` block。prompt 必须有 fallback 行为指引，
    否则"何时调 search_knowledge"指令悬空，LLM 行为不可预测。"""

    def test_has_fallback_for_missing_project_rules(self) -> None:
        """prompt 必须显式说明 `<project_rules>` 未注入时怎么办。"""
        has_fallback = (
            "Fallback" in SYSTEM_PROMPT
            or "fallback" in SYSTEM_PROMPT
            or "未注入" in SYSTEM_PROMPT
        )
        assert has_fallback, (
            "prompt 必须有 <project_rules> 缺席时的 fallback 行为指引，"
            "否则 rules_loader 优雅降级时 LLM 没有调 KB 的指令依据"
        )


# ---------------------------------------------------------------------------
# 7. 集成：SYSTEM_PROMPT + rules_loader / memory_manager 拼接路径
# ---------------------------------------------------------------------------
class TestSystemPromptInjectionIntegration:
    """端到端验证 prompt + 注入路径在两类极端场景下的拼接结果。"""

    def test_rules_block_appended_when_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rules.md 存在时：最终 prompt 含 `<project_rules>...</project_rules>` 包裹的内容。"""
        from src.agent.core.rules_loader import build_rules_block, load_project_rules

        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("我的偏好-INVARIANT-TEST", encoding="utf-8")

        # 确保 enabled
        import src.config as _cfg

        monkeypatch.setattr(_cfg, "USER_RULES_ENABLED", True)
        monkeypatch.setattr(_cfg, "USER_RULES_MAX_CHARS", 10000)

        rules = load_project_rules(root=tmp_path, file=".agenta/rules.md")
        block = build_rules_block(rules)
        final = SYSTEM_PROMPT + block

        assert rules == "我的偏好-INVARIANT-TEST"
        assert "<project_rules>" in final
        assert "我的偏好-INVARIANT-TEST" in final
        assert "</project_rules>" in final

    def test_no_rules_block_when_file_missing(self, tmp_path: Path) -> None:
        """rules.md 缺失时：load_project_rules 返回 None，build_rules_block 返回 ""；
        最终拼接不会引入额外 `<project_rules>` 块。"""
        from src.agent.core.rules_loader import build_rules_block, load_project_rules

        # tmp_path 下没有 .agenta/rules.md
        rules = load_project_rules(root=tmp_path, file=".agenta/rules.md")
        block = build_rules_block(rules)

        assert rules is None
        assert block == ""

    def test_rules_loader_handles_disabled_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """USER_RULES_ENABLED=false 时：即便 rules.md 存在也不加载，
        prompt 走 fallback 路径（由 TestSystemPromptFallback 守护 fallback 文案存在）。"""
        from src.agent.core.rules_loader import load_project_rules

        rules_dir = tmp_path / ".agenta"
        rules_dir.mkdir()
        (rules_dir / "rules.md").write_text("不该被加载", encoding="utf-8")

        import src.config as _cfg

        monkeypatch.setattr(_cfg, "USER_RULES_ENABLED", False)

        rules = load_project_rules(root=tmp_path, file=".agenta/rules.md")
        assert rules is None
