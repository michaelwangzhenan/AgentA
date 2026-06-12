# iter_14 Deep Research（深度研究）验收报告

> 对照本期需求/设计（[iter_14_enh.md](../iter_14_enh.md) Deep Research 章节）逐项核对。
> 验收方式：不耗 token 的项跑自动化（UT 全套 / 前端 typecheck）直接拿结果；耗 token 的项（真实 LLM / 真实 KB / 联网）给出验证路径与 UT 覆盖证据，需真实环境手动复核。
> 验收时间：2026-06-10。

## 1. 结论速览

| 维度 | 结果 |
|---|---|
| 后端 UT（`pytest -q`） | **1517 passed, 133 deselected**（含本期新增：research_engine 14 条、citation web 7 条、stream mode 分派 2 条） |
| 前端 typecheck（`tsc --noEmit -p tsconfig.app.json`） | **0 error** |
| config 同步（`config.py` / `.env` / `.env.example` / `config_meta.py` UI 注册） | **达标**：7 项 `DEEP_RESEARCH_*` 四处对齐 |
| P0/P1 review | **无遗留**：修了 1 个 P1（子代理工具集越界） |
| 既有功能回归 | **无破坏**：普通 chat（`agent.run`）路径零改动；mode 缺省/`chat` 仍走原流程 |

## 2. 验收标准与核对

| 编号 | 标准 | 验收方式 | 结果 |
|---|---|---|---|
| D1 | 前端有「深度研究」开关，仅全局启用时显示，开则请求带 `mode=deep_research` | `Composer.tsx` 读 `useComposerSettings.deepResearchEnabled` 门控显示，submit 传 mode；`tsc` 通过 | ✅ |
| D2 | 后端按 `mode` 分派：deep_research 走 `ResearchEngine`，否则走 `agent.run` | `test_api_chat_stream::test_stream_deep_research_mode_dispatches_to_research_engine` / `..._default_mode_uses_agent_not_research_engine` | ✅ |
| D3 | 深度研究跳过语义缓存 + 跳过降级路由，且把 `auto` 解析成具体模型 | `chat.py` 流式端点 `is_deep` 分支：不查 cache、`route(enabled=False)` 取 baseline | ✅ 代码路径 + dispatch UT 覆盖 |
| D4 | 四阶段编排：规划 → 并行子代理 → 反思补查 → 综述 | `test_research_engine::TestHappyPath`（事件序列 started→plan→subagent_start×N→subagent_end×N→synthesizing→final_answer） | ✅ |
| D5 | 规划：拆 3~上限个子问题，越界裁剪，失败软降级单问题 | `TestPlan`（解析 / `test_clamps_to_max` / `test_plan_failure_degrades_to_single`） | ✅ |
| D6 | 受限子代理：仅 3 个检索 tool、独立 in-memory 上下文、不写 `ChatHistoryStore` | `TestGetResearchTools`（工具集 ⊆ {search_knowledge, web_search, fetch_url}）+ happy path 仅落库 2 条（用户问题 + 最终报告） | ✅ |
| D7 | 软失败：单子代理全空/异常 → 标记失败、记 note，不中断整体 | `TestSubagentSoftFailure`（全空内容 → status=failed，整体仍出报告 + final_answer） | ✅ |
| D8 | 反思补查：缺口时派 ≤2 个补查子问题，最多 1 轮 | `TestReflect::test_followups_trigger_extra_subagents`（2+1 子代理）/ `test_sufficient_no_followups` | ✅ |
| D9 | 统一引用：KB + web 共用一套 `[n]` 编号，报告末尾自动追加来源块 | `test_citation_builder::TestRegisterWeb`（连续编号 / url 去重 / 渲染格式 / 与 KB source 不撞）+ 子代理工具调用 UT 计来源 | ✅ |
| D10 | 进度可视化：发一组 `research_*` 事件，前端研究面板渲染四阶段 + 子代理行 | `event_bus` 新增 7 个 `research_*` 常量；`useChat.ts` 处理全部事件；`ResearchPanel.tsx` 渲染；`tsc` 通过 | ✅ 代码 + 类型校验；真实视觉效果需起服务手动看 |
| D11 | 收尾对齐 `agent.run`：流式 token、`final_answer` 带聚合 usage、最终报告落库 | happy path UT 校验 final_answer.usage 非空、used_tools=True、落库 assistant 一条 | ✅ |
| D12 | 并发安全：子代理线程共享引用器/来源计数加锁，LLM 偏好经 contextvar 传播 | `CitationBuilder._lock` / `_Usage._lock` / `_sources_lock`；`copy_context().run` 传播 `use_llm_prefs` 的 ContextVar | ✅ 代码审查 |

> D3/D4/D8/D10 等标"需手动复核"的部分：均为**耗 token / 需真实联网/KB** 项，核心代码路径已被 UT 锁住（分派、四阶段编排、软失败、事件序列、引用编号），逻辑正确性有保证，仅"真实 LLM 输出质量 / 视觉呈现"需起服务实测。

## 3. 本期改动范围

| 层 | 文件 | 说明 |
|---|---|---|
| 配置 | `src/config.py` / `.env` / `.env.example` / `src/api/config_meta.py` | 7 项 `DEEP_RESEARCH_*`（开关 / 子问题数 / 并行数 / 子代理轮次 / 单代理来源上限 / 总来源上限 / 反思开关），含 UI「Deep Research」分组 |
| 事件 | `src/agent/core/event_bus.py` | 7 个 `research_*` 事件常量 + 注册进 `ALL_EVENT_TYPES` |
| 引用 | `src/agent/core/citation_builder.py` | `Citation` 加 `url`/`title`；`register_web()`（哨兵 key 防撞 KB）；`_render_one` web 分支 |
| 工具 | `src/agent/tools.py` | `web_search`/`fetch_url` 接 `citation_builder`（`cite_web` 门控）；`get_research_tools()` 限定 3 检索工具 |
| 引擎 | `src/agent/core/research_engine.py`（新） | `ResearchEngine` 四阶段编排 + 受限子代理 bounded ReAct + `_Usage` 跨线程累计 |
| API | `src/api/schemas/chat.py` / `src/api/routes/chat.py` | `ChatRequest.mode`；流式端点按 mode 分派、跳缓存/路由、解析 baseline |
| 前端类型 | `frontend/src/types/chat.ts` | `ChatMode` / `research_*` 事件 / `ResearchState` 等；`AssistantMessage.research` |
| 前端逻辑 | `frontend/src/api/client.ts` / `hooks/useChat.ts` / `hooks/useComposerSettings.ts` | 传 mode；处理 `research_*` 维护 `ResearchState`；开关状态 + 全局启用读取 |
| 前端 UI | `frontend/src/components/chat/Composer.tsx` / `ResearchPanel.tsx`（新）/ `MessageBubble.tsx` / `ChatView.tsx` | 深度研究开关按钮；研究进度面板；气泡接入 |
| 测试 | `tests/test_research_engine.py`（新）/ `tests/test_citation_builder.py` / `tests/test_api_chat_stream.py` / `tests/conftest.py` | research_engine 14 条 + citation web 7 条 + stream mode 2 条；conftest 隔离语义缓存 |

## 4. Review 发现与处理

| 级别 | 现象 | 处理 |
|---|---|---|
| P1 | `get_research_tools()` 实现返回**所有**名单内 tool，与设计「仅 3 个检索 tool」及自身 docstring 不符，子代理会拿到 plan/业务/skill tool | **已修**：限定 `{search_knowledge, web_search, fetch_url}` 再过名单门；新增 `TestGetResearchTools` 锁定 |
| P1 | `mode=deep_research` 且用户模型为 `auto` 时，`prefs.active_model="auto"` 会原样传到 `chat()` → `get_active_model()` 解析失败 | **已修**：`route(enabled=False)` 取具体 baseline（不触发分类 LLM、不降级） |
| — | api chat 流式测试随机失败：语义缓存共用进程级 ChromaDB 不随测试隔离，历次跑积累条目导致随机命中、跳过 `agent.run` | 既有隔离缺口，非本期引入；仿照 conftest 关 USER_MEMORY 的做法，默认关 `SEMANTIC_CACHE_ENABLED`（需缓存的测试自行 monkeypatch 开），消除不确定性 |
| — | 子代理在线程池并行，共享 `CitationBuilder` / 总来源计数 / usage 累计 | 设计阶段已规避：三者均加锁；LLM 偏好经 `copy_context` 传播 |

未发现 P0。

## 5. 决策记录（复杂任务按业内标准自决）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 子代理是否复用 `ToolCallEngine` / `Agent` | 否，引擎内自建受限 ReAct | 二者与 `ChatHistoryStore` 耦合会污染用户历史，且事件路由会与主时间线冲突；仅复用 `assistant_message` helper |
| 深度研究是否走语义缓存 | 否 | 多源研究永不可缓存，且「重质量不重速度」定位 |
| 来源计数粒度 | 按工具调用计（每次成功检索 +1） | 简单稳定；精确到条目意义不大且实现复杂 |
| 测试缓存隔离 | conftest 默认关语义缓存 | 与既有「关 USER_MEMORY 避免外部写入」同思路；最小且确定 |
