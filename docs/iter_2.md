# 1. 为什么有 AgentA

通过搭建AgentA 来：
- 实践 Vibe coding ：从零开始，在自己不是什么都了解的情形下，完成一个完整项目
- 学习 RAG ： 通过实现真实的 RAG，来逐步了解其原理和本质
- 学习 Agent ：通过实现一个Agent，来深入理解Agent的原理，以便更好的使用 AI
- 项目展示 ：把本项目包装为一个实战项目，用于找工作/面试

整个项目计划包含4大块：
- RAG
- Agent(CLI 模式)
- UI
- 多种实现方式：Python/LangChain/AutoGPT，以了解各种Agent实现框架


# 2. 当前状态

- 4大块都已有一些实现
- RAG部分已比较完整，暂时不做优化
- Agent 部分是马上要进一步完善的重点
- UI 部分等 Agent 完善后再继续
- 三种实现都有一些框架代码，现在以 Ptyhon 为主，马上进行的 Agent 也以Python 分支继续。

# 3. Agent 优化方向

目标和状态：
1. 实现基本的 Agent 框架，可以进行问答 => Done
2. 接入 RAG 可以回答私有问题 => Done
3. 参考 GHC(github copilot)/cursor/Claude(Web/Desktop) 的实现方式，优化AgentA
4. 关注Agent 最新技术，持续改进

我知道的关于Agent的功能/技术(部分已经初略实现)：
1. LLM API调用: openai /anthropic / google / azure / 国内的应该都是 openai API
2. Agent 循环/架构：ReAct, plan and execute, loop 等
3. Session ：一次对话记录
4. Memory ： per 用户， 跨 session记忆
5. Prompt ： 理解 system/user/assistant prompt，实现类似 GHC/cursor 在 .github/.cursor 目录下的 prompt 文件，或自定义agent
6. Tools ：Agent 代码自实现的 tool(RAG,web search, etc) + 类似 GHC 把插件当作工具
7. Skills ：支持标准 Skills 注入（https://agentskills.io/home），实现参考 GHC 
8. MCP ： 支持标准MCP(https://modelcontextprotocol.io/docs/getting-started/intro) ，实现参考 GHC/Cursor
9. Thinking模式：增加更多模型支持，流式输出优化，可折叠等
10. Harness ： LLM 直接返回文本 → 输出最终回答，评价回答，决定是否继续提问 => 建立反馈机制，让 AI 能自我检查和修正。
11. 防止 prompt injection
12. CnP Refinement

TBD:
- 多Agent/SubAgent/A2A协议
- 支持sandbox
- 用户自定义 Workflow?


# 4. Agent 改进计划

## 4.1. Review 现有代码
Review 完整实现 @AgentA 目录
只了解现状，不需要输出

## 4.2. 重构评估
基于4.1，重新整体设计 AgentA 架构：Agent, RAG, CLI/UI

设计原则：
- **Agent core 与表现层解耦**：Agent core 不假设 IO 形式（CLI / 未来 Web UI / SDK 都能接），通过 Stream / Callback 接口对外，UI 阶段不需要回头改 Agent core
- **RAG 内部不动**，但要重新约定 Agent 调用 RAG 的对外接口（返回结构、metadata 暴露、错误降级行为等）
- **三种 impl 共享公共层**，差异只在 Agent loop 那一层（详见 4.3）

输出：
- 整体架构 mermaid 图，覆盖三大模块（Agent / RAG / CLI/UI）+ 各模块内部结构画到位（作为整个工程的设计文档）
- 写入 [整体架构](design.md#1整体架构) 章节

## 4.3. 三种实现模块化共享
目标：模块化共享，抽离公共部分（LLM provider / RAG / Tools），三个 impl 只换"Agent loop"那一层

**命名约定（4.5 重构 + 后续新增模块都按此走）**：
- **依赖层**（不感知 Agent loop 的底层能力）：数据存储用 `*Store` 后缀，如 `ChatHistoryStore` / `UserMemoryStore`
- **Helper 层**（封装业务策略、被三种 impl 共享）：按角色选后缀
  - `*Manager`：策略编排（如 `HistoryManager` / `MemoryManager`）
  - `*Engine`：执行流水线（如 `ToolCallEngine`）
  - `*Policy`：纯策略判定（如 `ThinkingPolicy`）
  - `*Bus`：事件分发（如 `EventBus`）

## 4.4. 清理前期不必要功能/代码
根据新的架构，评估哪些功能/代码是不必要的，需要清理
例如：
1. 因为现在已有 tools/rag_cli.py, CLI中的 /ingest 命令可以删掉
2. 考虑到后续还有UI功能，CLI的定位需要重新考虑

### 4.4.1. To clean up

按"风险 / 收益 / 重构边界"分三档：

**第一档：直接清理（明确冗余，行为零变化）**

1. **CLI `/ingest` 命令全链路移除** —— 已被 `tools/rag_cli.py ingest` 完全覆盖且更强（叠加了 status / clear / 孤儿 segment 清理 / sidecar 历史），表现层不应再承担 RAG 运维。涉及：
   - `main.py:140-161`：`/ingest` 分支 + 手写参数解析
   - `chainlit_app.py:317-324`：`/ingest` 分支
   - `chainlit_app.py:167-181`：`_parse_ingest_args`
   - `chainlit_app.py`：`AppState.ingest_docs_dir` / `ingest_model_alias` 字段 + ChatSettings 里对应两个 widget
   - `src/cli/handlers.py:63-86`：`run_ingest`
   - `src/cli/ui.py:17-18`：帮助文本两行
   - `src/cli/tab_complete.py`：`/ingest` 补全项
   - `README.md` / `docs/iter_0.md`：`/ingest` 引用同步改为 `tools/rag_cli.py ingest`
   - `tests/test_cli_handlers.py`：`run_ingest` 相关用例移除

2. **`LangChainAgent.chat()` alias 删除** —— `run()` 的赤裸别名，三 impl 中只此一家，破坏 duck-typed 契约统一性。
   - 删 `src/agent/langchain_agent.py:64-65`
   - 改 `tests/test_langchain_agent.py:80` 用 `.run('hi')`

3. **`handlers.py:57` 局部 `import sys` 冗余** —— 文件顶部已 `import sys`，函数内重复。删 1 行。

**第二档：定位调整（不删代码，配合 #1 改文档与帮助文本）**

4. **CLI 重新定位为"开发调试 / 服务器无 GUI" 用** ：
   - 用户向命令（`/help /clear /history /session /memory /thinking /save /reload-*` + Prompt/Skill 切换）全部保留 —— CLI 仍是最快的调试入口
   - 运维类一律走 `tools/`：本次删 `/ingest`，今后类似命令（`/clear-kb` 等）也不再加进 CLI
   - `README.md` "Quickstart" 把 Chainlit UI 提到首位、CLI 作为副入口注明用途；`ui.py` BANNER 副标题加"CLI for dev / headless"
   - 决策记录写进本节，避免未来再有人往 CLI 加运维命令

**第三档：本步只登记，留到 4.5 重构时处理**

> 这些是**架构层耦合问题**，与 helper 抽离绑在一起，单独动会破坏行为或与 4.5 冲突。

5. `src/agent/agent.py` 与 `src/agent/autogpt_agent.py` 反向 import `src.cli.skill_loader` —— Agent core 不应依赖表现层目录；4.5 把 `SkillCatalog` 抽到 `src/agent/core/` 后顺手解掉
6. `src/agent/agent.py` 与 `src/agent/autogpt_agent.py` 直接 import `src.memory.user_memory` 的 `should_extract_immediately` / `extract_memories` —— 应封装进 `MemoryManager` helper
7. `src/cli/ui.py` BANNER 在 import 时 `format(config.IMP_METHOD, ACTIVE_PROVIDER)` —— 运行时切 provider 不刷新；要等 ChatSettings 真正能改 provider 之后一起整改
8. 三个 Agent impl 内部各自构造 SkillCatalog / 拼 `<skill_content>` —— 4.5 抽 `SkillCatalog` helper 时统一

### 4.4.2. Impl plan

按"可独立回滚"分 5 步，每步结束都跑一次回归脚本。

**Step 1: 删除 `/ingest` 全链路（Tier 1.1）**

按依赖自底向上改，避免中间态 broken：

1. **`src/cli/handlers.py`**：删 `run_ingest` 函数（63-86 行）
2. **`src/cli/ui.py`**：删帮助文本第 17-18 行（两行 `/ingest...`）
3. **`src/cli/tab_complete.py`**：删 `CLI_COMMANDS` 中 `/ingest` / `/ingest -m zh` / `/ingest -m en` 三项
4. **`main.py`**：删 `case "/ingest":` 分支（140-161 行）
5. **`chainlit_app.py`**：
   - 删 `case "/ingest":` 分支（317-324 行）
   - 删 `_parse_ingest_args` 函数（167-181 行）
   - 删 `AppState.__init__` 的 `ingest_docs_dir` / `ingest_model_alias` 形参与字段（104-105, 115-116 行）
   - 删 `_runtime_settings_widgets` 中 `TextInput(ingest_docs_dir)` 与 `Select(ingest_model_alias)` 两个 widget（213-219 行）
   - 删 `on_settings_update` 中对这两个键的处理（544-549 行）
   - 删 `on_chat_start` 里 `AppState(...)` 传 ingest_* 形参的痕迹（如果有显式传，本地化看一下）
6. **`tests/test_cli_handlers.py`**：当前没有 `run_ingest` 用例（已确认），无需改动

**Step 2: 删除 `LangChainAgent.chat()` alias（Tier 1.2）**

1. **`src/agent/langchain_agent.py`**：删 64-65 行 `def chat(...)` 方法
2. **`tests/test_langchain_agent.py:80`**：`ag.chat('hi')` 改为 `ag.run('hi')`

**Step 3: 清掉 `handlers.py` 局部冗余 import（Tier 1.3）**

1. **`src/cli/handlers.py:57`**：删函数体内的 `import sys`（顶部 line 8 已有）

**Step 4: 文档同步 + CLI 定位（Tier 2.4）**

仅改"活文档"，历史记录（`docs/iter_0.md` / `docs/iter_1.txt`）保持原样：

1. **`README.md`**：
   - `2.4.启动 AgentA` 调整：Chainlit 段提到 CLI 之前，CLI 段注明"主要用于开发调试 / 无 GUI 场景"
   - 删 quickstart 里 "首次使用先 /ingest 把 ./datasets/data_en 入库"，改写为引用 `python -m tools.rag_cli ingest -m m3`
   - `4.1.RAG 入库` 章节描述里 "（与 `/ingest` 等价）" 字样去掉
2. **`.env.example:23`**：注释 "(/ingest 命令默认扫描的目录)" → "(tools/rag_cli.py ingest 默认扫描的目录)"
3. **`src/rag/ingest.py`** 顶部 docstring 第 8 行 + 332 行的 "/ingest 等价"字样：改为 "与 `tools/rag_cli.py ingest` 等价"
4. **`tools/rag_cli.py`** 顶部 docstring 第 7 行 "main.py 中的 /ingest" 字样改为 "原 CLI `/ingest`（已废弃）"

> `ui.py` BANNER 副标题按用户决定**不加**。

**Step 5: 回归验证**

| 项 | 命令 | 预期 |
|---|---|---|
| 单元测试 | `pytest tests/ -x` | 全绿 |
| CLI 启动 | `python main.py` → `/help` | help 不再出现 `/ingest`；其他命令一切照旧 |
| 运维工具 | `python -m tools.rag_cli status` | 与改前输出一致 |
| Chainlit | `chainlit run chainlit_app.py --port 8000` → 打开 settings 面板 | 不再出现 "Ingest Docs Dir" / "Ingest Embedding Alias" 两项；其他设置正常 |

### 4.4.3. UT Refinement
为后续 4.5 重构 + 4.9 加 feature 时**不破坏现有行为**，先对 UT 做范围聚焦与缺口评估。

**缺口评估**

4.5 会抽出 5 个 helper（`EventBus` / `ToolCallEngine` / `HistoryManager` / `MemoryManager` / `ThinkingPolicy`）。当前 UT 对它们的覆盖：

| 待抽 Helper | 当前 UT 覆盖 | 是否足以防止重构回归 |
|---|---|---|
| `ThinkingPolicy` | `TestEstimateThinkingBudget` 8 例 + `TestAgentThinkingInit/Run` 7 例 | ✅ 足够 |
| `ToolCallEngine` | `TestAgentToolCall` 3 例 + `TestToolGuidance` 4 例 | ✅ 足够（hint 注入、tool_call_id、轮数上限都有） |
| `HistoryManager` | 无 ❌ —— `_load_truncated_history` / `_collect_skill_pairs` 当前 0 个直接单测，只在 integration 间接覆盖 | ❌ 不够 |
| `MemoryManager` | `test_user_memory.py` 覆盖底层 Store + sanitize + extract；但**注入 system_prompt 的行为 + N 轮后自动触发抽取**无 UT | ❌ 不够 |
| `EventBus` | 无 ❌ —— 当前 `set_token_callback` / `set_thinking_callback` 0 个直接单测，只在 chainlit_app 真实运行时验 | ❌ 不够 |

**改进计划**

**A. 4.5 重构前必加（防止重构破坏现有行为）**

| 项 | 测试目标 | 难度 |
|---|---|---|
| A1. `HistoryManager` 行为基线 UT | 把 `agent.py:_load_truncated_history` 的所有分支（按 turn 截断、skill_pair 完整性保护、system 拼接、空历史）固化为单测；重构后直接复用 | 中 |
| A2. `MemoryManager` 行为基线 UT | 覆盖：N 轮后自动触发抽取的时机判定、立即触发关键词（"请记住"/"remember this"）、抽取后注入 system_prompt 的位置 | 中 |
| A3. `EventBus` 契约 UT | 单订阅 / 多订阅 / 取消订阅 / 单个订阅者抛异常不影响其他订阅者 / 事件类型枚举 | 低（纯逻辑） |

**B. 本步可顺手加（覆盖关键缺口，与 4.5 无强绑定）**

| 项 | 测试目标 | 难度 |
|---|---|---|
| B1. `RetrieverAPI.format_search_results` 单测 | 返回字符串包含 source / score / content；多 hit 排序；空 hit | 低 |
| B2. `BaseAgent` Protocol 一致性 UT | 用 `hasattr` / inspect 检查 `Agent` / `AutoGPTAgent` / `LangChainAgent` 都有 `run` / `session_id` / `activate_skill` / `set_event_callback` 等签名（即使本期不测 langchain/autogpt 功能，接口校验是 import-only，0 网络消耗） | 低 |

**C. 4.9 新功能时同步加（feature-by-feature）**

在 [§4.9](#49-开始实现) `step 2.4 测试功能` 落实：每个新 feature **必须**至少 1 个 unit 覆盖核心行为，命名 `tests/test_<feature>.py`。Showcase / Learning / Foundation 三档都执行此规则，差别只在覆盖深度。


**决策**

- A + B 共 5 项 **在 4.5 之前作为前置任务做完**
- C 在 [§4.9](#49-开始实现) 落实

**执行结果**

5 个新增测试文件，全部默认套件中跑：55 passed + 1 skipped（LangChain import 失败）+ 2 xfailed（EventBus 未抽出前的契约 placeholder）。

| 文件 | 用例数 | 覆盖点 |
|---|---|---|
| `tests/test_history_manager.py` | 11 | `_load_truncated_history` 截断 / 空历史 / system 过滤 / SQL 粗粒度上限 + `_collect_skill_pairs` skill 组保护 |
| `tests/test_memory_manager.py` | 10 | `Agent.run` 注入 `<user_context>` 三态 + `_try_extract_memories` 五种触发分支 + 异常静默 |
| `tests/test_event_callbacks.py` | 13（含 2 xfail） | `set_thinking/token_callback` 安装/重置/替换 + `_on_thinking_chunk` 透传 + EventBus 未抽出前的扇出/隔离 placeholder |
| `tests/test_format_search_results.py` | 12 | 空 hits / score vs distance / 多 hit 分隔 / retrievers / heading_path / page_no |
| `tests/test_agent_protocol.py` | 12（含 1 skip + 1 xfail） | Python & AutoGPT 的 `run / activate_skill / session_id` 签名一致；事件接口当前分布；EventBus 未对齐前的 xfail 锁定 |

**对4.5 重构信号约定**：
- `test_event_callbacks.py::TestFutureEventBusContract::test_multiple_subscribers_fan_out` 由 xfail 转 pass = EventBus 多订阅扇出已实现（callback 字段从 `callable|None` 改为 `list[callable]`）
- `test_agent_protocol.py::TestEventInterfaceFutureContract::*` 由 xfail 转 pass = AutoGPT 已接入统一事件接口
- `test_agent_protocol.py::TestLangChainAgentProtocol::*` 由 skip 转 pass = LangChain 环境修复（依赖路线另议：升级到 langchain 1.0+ / 回退 `AgentExecutor` / 改 langgraph）
- `test_event_callbacks.py` 重命名为 `test_event_bus.py`，把测试主语从 `agent` 改为 `agent.events`

## 4.5. 根据新的设计，调整代码框架
1. 把代码框架，按新的架构调整好
2. 回归测试，确保功能正常（[§4.4.3](#443-ut-refinement) 已就位的 5 个安全网文件 + 2 个 xfail 信号会自动提示重构进度）

### 4.5.1. Plan

**目标结构**
实现：[design.md 5.IMP](design.md#5imp)

```
src/agent/
├── agent.py                 # Python 实现 — 只剩 loop 编排，调 helpers
├── autogpt_agent.py         # 持有 EventBus，暴露 set_X_callback 转发
├── langchain_agent.py       # 本期不接（环境 import 失败）
├── tools.py                 # 依赖（已存在）
└── core/                    # 公共层 helpers（新建）
    ├── __init__.py
    ├── history_manager.py        # HistoryManager
    ├── memory_manager.py         # MemoryManager
    ├── event_bus.py              # EventBus（多订阅 + 异常隔离）
    ├── tool_call_engine.py       # ToolCallEngine
    └── thinking_policy.py        # ThinkingPolicy
```

依赖层（`src/memory/chat_history.py` 的 `ChatHistory` / `src/memory/user_memory.py` 的 `UserMemoryStore` / `src/llm/provider.py`）位置不动，只是被 helper 调用。

**9 步小步快跑（每步跑一次 `bash tools/ut.sh -fast`，确保 0 failed**

| 步 | 抽取动作 | 触及面 | 测试关注 |
|---|---|---|---|
| 1 | 创建 `src/agent/core/__init__.py` 占位 | 0 行业务 | `-fast` 全绿 |
| 2 | 抽出 `HistoryManager` → `core/history_manager.py`；agent.py 改调它 | agent.py −60 行 | `-history` + `-fast` |
| 3 | 抽出 `MemoryManager` → `core/memory_manager.py`（含 `<user_context>` 注入 + extract 触发）| agent.py −60 行 | `-mem` + `-fast` |
| 4 | 抽出 `EventBus` → `core/event_bus.py`（callback 字段改 `list[callable]`，新增 `subscribe / unsubscribe / publish` + 异常隔离）；agent.py 的 `set_X_callback / _on_thinking_chunk` 委派给 EventBus | agent.py −25 行 | `-event`：多订阅 xfail 自动 XPASS strict → 摘 xfail 标记 |
| 5 | 抽出 `ToolCallEngine` → `core/tool_call_engine.py`（封装 `_process_tool_calls` + `_assistant_message`） | agent.py −80 行 | `-agent` + `-fast` |
| 6 | 抽出 `ThinkingPolicy` → `core/thinking_policy.py`（封装 `ThinkingConfig` + `estimate_thinking_budget` 调用） | agent.py −30 行 | `-fast` |
| 7 | AutoGPTAgent 持有 EventBus 实例，暴露 `set_thinking_callback / set_token_callback` 转发 | autogpt_agent.py +10 行 | `-proto`：protocol xfail 自动 XPASS strict → 摘 xfail 标记 |
| 8 | `test_event_callbacks.py` 改名 `test_event_bus.py`，测试主语切到 `agent.events`；更新 `tools/ut.sh -event` 指向新文件 | 测试 + 脚本 | `-event` |
| 9 | 全套件回归 + 更新 design.md（反映 `core/` 目录的实际文件位置） | 文档 | `-fast` + `-all` |



### 4.5.2. Result

**新增 5 个 core helper 文件**（`src/agent/core/`，合计 ~425 行含 docstring）：

| 文件 | 行数 | 抽出来源 |
|---|---|---|
| `history_manager.py` | 83 | 原 `Agent._load_truncated_history` + `_collect_skill_pairs` |
| `memory_manager.py` | 106 | 原 `Agent.run` 的 `<user_context>` 拼接 + `Agent._try_extract_memories` |
| `event_bus.py` | 74 | 替代原 `_thinking_chunk_callback` / `_token_chunk_callback` 单值字段，新增多订阅 + 异常隔离 |
| `tool_call_engine.py` | 101 | 原 `Agent._process_tool_calls` + `_assistant_message` + `TOOL_EMPTY_HINT` |
| `thinking_policy.py` | 51 | 原 `Agent` 模块的 `ThinkingConfig` + `run()` 内的 `estimate_thinking_budget` 调用 |


**xfail/skip 信号转换**：
- `test_event_bus.py::TestEventBusFanOut::*`（前 [§4.4.3](#443-ut-refinement) 的 `test_multiple_subscribers_fan_out` xfail）→ pass
- `test_event_bus.py::TestEventBusExceptionIsolation::*` → pass
- `test_agent_protocol.py::TestEventBusInstanceContract::*`（替换前 `TestEventInterfaceFutureContract` xfail）→ pass
- `test_event_callbacks.py` 已改名为 `test_event_bus.py`，主语切到 `agent.events`

**测试结果**：默认套件 `python -m pytest` —— `318 passed, 1 skipped, 110 deselected, 0 xfailed, 0 failed`（前为 315 passed + 2 xfailed，xfail 已全部转 pass）。

### 4.5.3. 遗留问题
1. ChatHistory → ChatHistoryStore => Done
2. LangChain 环境修复 => Done

### 4.5.4. API 收口

对应 design.md：[AgentAPI](design.md#31-agentapi) / [RetrieverAPI](design.md#32-retrieverapi)

[§4.5](#45-根据新的设计调整代码框架) 重构完成后，对照 [design.md §1.3](design.md#13两套接口) 定义的两套对外接口发现还有缺口；进 [§4.6](#46-agent-的最新功能技术探索) 之前先收口，避免在不完整的架构上累积 feature 代码。

**术语对齐**：本节只涉及 [design.md §1.3](design.md#13两套接口) 描述的两套**对外接口** ——
- `AgentAPI`：表现层 ↔ Agent core（架构图 AAPI 节点）
- `RetrieverAPI`：Agent core ↔ RAG（架构图 RAPI 节点）

**Part A · RetrieverAPI **

`search / expand_queries / format_search_results / warm_up / Hit` 5 个 API 在代码层全部存在。本步只做对照：签名 / Hit 字段 / 降级行为 vs design.md，发现不一致就修对应那侧。

**Part B · AgentAPI 完整化（成本 ~半天）**

| 步 | 动作 |
|---|---|
| B1 | `src/agent/agent_api.py` 新建：`AgentAPI(Protocol, runtime_checkable)` 正式声明（即 [design.md §3.1](design.md#31-agentapi) 的 Python 实现）|
| B2 | `event_bus.py` 新增 `AgentEvent` dataclass，`publish(event: AgentEvent)` 单参签名（**D1=A**，不留 shim） |
| B3 | 三种 Agent 实装 `set_event_callback(cb)` ──  **删除** `set_thinking_callback / set_token_callback`（**D2=B**），sweep `chainlit_app.py / cli/handlers.py / tests/*` |
| B4 | loop 内 publish 全部 7 类事件（Python Agent + AutoGPT）；LangChain 只发 `final_answer + error`（**D3=A**） |
| B5 | UT：`test_agent_protocol.py` 用 `isinstance(agent, AgentAPI)`；新建 `test_agent_events.py` 验证事件顺序 + 个数 + payload 关键字段（**D4=A**） |
| B6 | 更新 [design.md §3.1](design.md#31-agentapi) / [§3.2](design.md#32-retrieverapi) 与本节 Result |

**Result（已完成）**

Part A 仅一处微调：design.md `Hit.metadata` 类型 `dict` → `dict | None`（与 dataclass 一致）。

Part B 落地总览：

- **`src/agent/agent_api.py`** 新建：`AgentAPI(Protocol, runtime_checkable)` 含 `session_id / last_usage / thinking_cfg / events` 4 属性 + `run / activate_skill / set_event_callback` 3 方法 —— 即 [design.md §3.1](design.md#31-agentapi) 的 Python 实现
- **`src/agent/core/event_bus.py`** 升级：新增 `AgentEvent` dataclass；`publish` 单参签名（无 shim）；新增 `ALL_EVENT_TYPES` 元组便于遍历
- **`src/agent/agent.py`** 改造：删 `set_thinking_callback / set_token_callback`，新增 `set_event_callback`；`run` 内补 `info / final_answer / error` 发射；空内容 / 超轮次 fallback 路径也发出 `error + final_answer`
- **`src/agent/core/tool_call_engine.py`** 新增 `events` 构造参数；`process` 内每次 execute_tool 前后发出 `tool_call_start / tool_call_end`
- **`src/agent/autogpt_agent.py`** 同上：删旧双方法、加 `set_event_callback`、`run` 入口发 `info`、退出前发 `final_answer`、`activate_skill` 发 `info`；`_execute_task` 工具调用处发 `tool_call_start / tool_call_end`
- **`src/agent/langchain_agent.py`** 删旧双方法、加 `set_event_callback`；`run` 发 `final_answer + error`（D3=A 路线）
- **调用方 sweep**：`chainlit_app.py` 与 `src/cli/handlers.py` 改用 `set_event_callback(_event_router)`，回调内按 `event.type` 分流到旧的 thinking / token chunk 处理逻辑
- **UT**：`test_event_bus.py` 改写为基于 `set_event_callback + events.subscribe`；`test_agent_protocol.py` 新增 `TestAgentAPIIsInstance` 用 `isinstance(agent, AgentAPI)` 校验三种实现；新建 `test_agent_events.py`（6 case）验证事件顺序 + 个数 + payload 关键字段
- **`tools/ut.sh`** 新增 `-events` 模块入口；`-helper` 档由 5 文件扩到 6 文件
- **架构图**：[design.md §1.2](design.md#12整体架构) 删掉 `IMP -.->|"BaseAgent Protocol"| SHARED` 虚线标签 —— 它会与本节代码层的 `class AgentAPI` 混淆，且公共层本就是 import 复用 helper，并无独立 Protocol。改为无 label 的实线依赖


## 4.6. Agent 的最新功能/技术探索
[3.Agent 优化方向](#3agent-优化方向) 中已列的 12 项是**必做项**（基础盘）。
本步在此基础上**补充候选**：调研最新 Agent 论文 / 产品（GHC、Cursor、Claude Code 等）+ [3.x TBD] 中的项，列出所有可能新增的功能/技术作为**候选清单**。

### Agent 相关 feature/技术方案清单


## 4.7. 确定 AgentA 中 Agent 部分的需求

确定哪些是本项目应该支持，能够支持，值得支持的。

输入：[3.x 12 项必做] + [4.6 候选清单]

1. Review 4.6 输出
2. 决策：
   - **12 项必做**：排优先级（先做哪个、后做哪个）
   - **候选清单**：选 3~5 个深做（Q1=B 原则，找有教学/展示亮点的）
3. 未选中的候选 → 作为知识积累

## 4.8. 如何评估 Agent 实现
给出评估 Agent 的方案

## 4.9. 开始实现
1. 根据前面的评估，给出 Agent部分的需求分析（需要支持的功能，以及优先级）
2. 逐功能实现
    2.0 评估 feature 重要性：Showcase(找工作重点讲)/Learning(学习用,能跑通即可)/Foundation(基础设施)
    2.1 Review 当前代码，如已有初步实现，给出优化建议
    2.2 如是新需求，给出实施计划
    2.3 按计划实现功能
    2.4 测试功能（**必加 unit**，遵循 [§4.4.3](#443-ut-refinement) C 规则）
    2.5 更新design文档
    2.6 评估是否需要新加工具，在 [配套 tools](#410配套-tools) 实现

## 4.10. 配套 tools(参考/tools下的 RAG tool)
1. Review 9.2.6 累积的工具候选清单 → 合并、取舍、定优先级
2. 逐工具实现

## 4.11. 更新 README