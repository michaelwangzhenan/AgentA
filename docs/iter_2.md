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
[3.Agent 优化方向](#3-agent-优化方向) 中已列的 12 项。
本步在此基础上**补充候选**：调研最新 Agent 论文 / 产品（GHC、Cursor、Claude Code 等）+ [3.x TBD] 中的项，列出所有可能新增的功能/技术作为**候选清单**。

**本步原则**：广撒网、只登记、不判断、不实现。所有取舍留到 [§4.7](#47-确定-agenta-中-agent-部分的需求)。

### 4.6.1. 更多可能feature 清单

按来源分组。每行格式：`feature 名` — 一句话说明（来源缩写）。来源缩写：[Cursor]=Cursor IDE、[CC]=Claude Code、[GHC]=GitHub Copilot、[Devin]=Cognition Devin、[Manus]=Manus、[Replit]=Replit Agent、[CU]=Anthropic Computer Use、[Operator]=OpenAI Operator、[Mariner]=Google Mariner、[Paper]=学术论文、[TBD]=本文 [§3](#3-agent-优化方向) TBD 项。

**A. Cursor**（IDE 内置 Agent，2025–2026）
- **多模式切换**：Agent / Ask / Plan / Debug 四档，每档独立 context 窗口 [Cursor]
- **并行 subagents**：研究 / shell / 浏览器子代理在独立 context 跑，结果回填主对话 [Cursor]
- **[x]Custom subagent**：`.cursor/agents/*.md` 用 markdown + frontmatter 定义自定义子代理 [Cursor]
- **Cloud / Background Agent**：云 VM 跑长任务，自动开 `agent/<task>` 分支并提 PR [Cursor]
- **多渠道触发**：Slack `@cursor` / GitHub issue 评论 / Linear / Web / 移动端 [Cursor]
- **Rules 三层**：project rules / user rules / team rules（每次对话自动注入）[Cursor]
- **Bugbot**：PR 级自动 code review + 安全问题标记，effort 可调（高 effort 多花成本换更深审查）[Cursor]
- **Auto-Run Allowlist**：自动执行命令白名单（与防 prompt injection 配套）[Cursor]
- **Design Mode / Agents Window / PR review lifecycle / 环境版本历史** [Cursor]
- **Composer 训练数据**：把"开发者真实在 Cursor 中的会话"反哺基模（产品差异化路线）[Cursor]

**B. Claude Code**（CLI Agent，2026）
- **Subagent frontmatter 全字段**：`tools / disallowedTools / model / permissionMode / mcpServers / hooks / skills / memory / isolation / maxTurns / background` [CC]
- **Skills 预加载**：subagent 启动时把指定 skill **完整内容**注入 context（不是仅 description）[CC]
- **Hooks lifecycle**：`PreToolUse / PostToolUse / Stop / SubagentStart / SubagentStop / UserPromptSubmit / UserPromptExpansion / SessionStart / TaskCreated / TaskCompleted` 等事件钩子 [CC]
- **Hook 4 种类型**：`command`（shell）/ `http`（POST URL）/ `mcp_tool`（调 MCP 工具）/ `prompt`（单次 LLM 判定）/ `agent`（开 subagent 验证条件，experimental）[CC]
- **3 档 Memory**：`user`（跨项目）/ `project`（仓内共享）/ `local`（gitignore）[CC]
- **6 档 Permission Mode**：`default / acceptEdits / auto / dontAsk / bypassPermissions / plan` [CC]
- **Worktree Isolation**：subagent 自动开 git worktree 隔离工作 [CC]
- **Headless / SDK 模式**：`claude -p` + Python/TS SDK，可塞进 CI/CD；`--bare` 跳过自动发现保证 reproducibility [CC]
- **输出格式**：`text / json / stream-json`（新行分隔流式 JSON）[CC]
- **AGENTS.md 协议**：跨 AI agent 共享的项目指令文件 [CC] [GHC]

**C. GitHub Copilot Coding Agent**
- **Coding Agent**：从 issue 直接产 PR（云上自主，开发者只批 review）[GHC]
- **[x]Custom instructions 三层**：`.github/copilot-instructions.md`（repo 全局）+ `.github/instructions/*.instructions.md` + `AGENTS.md`（跨 agent）[GHC]
- **applyTo glob frontmatter**：路径模式触发的 path-specific 指令 [GHC]
- **excludeAgent**：按 agent 类型禁用某指令文件（如 code-review / cloud-agent）[GHC]
- **[x]Custom agents YAML**：单文件定义 prompt（≤30k 字符）+ tools + mcp-servers + model + 调用条件 [GHC]
- **[x]MCP server 仓库级配置**：repo settings 里 JSON 形式管理 MCP servers + secrets + toolsets header [GHC]
- **Code Review Agent**：PR 自动评审（与 Bugbot 同类，独立于 coding agent）[GHC]

**D. 自主/沙箱 Agent 产品**
- **沙箱云 VM 全套**：shell + editor + browser 的隔离环境（Devin 范式）[Devin]
- **[x]多 sub-agent 协同**：内部 Planner + Coder + Critic（Devin），或 Planner / Tool / Answer Agent（MIRROR）[Devin] [Paper]
- **Auto-Triage**：跨渠道（Slack / Linear / GitHub / 观测平台）监控告警 → 自动起调查 → 给上下文 / 推荐操作 / 直接出 PR [Devin]
- **跨 incident 记忆 + 去重 + 责任人追踪** [Devin]
- **多 OS 支持**：Linux + Windows + Android Emulator [Devin] [Manus]
- **CodeAct**：用 Python 代码片段作为 action（Manus），比 JSON tool call 表达能力强 [Manus]
- **Computer Use（OS 级）**：截屏 + 鼠标 + 键盘，控任意桌面应用（不限浏览器）[CU]
- **Browser-only Agent**：隔离 Chromium 跑 web 任务（不能访问本地文件）[Operator] [Mariner]
- **Headless / 程序化触发**：通过 API/SDK 让 Agent 跑后台任务 [Devin] [CC]
- **Spec-driven multi-agent**（Intent 范式）：本地 worktree + coordinator agent，开发者保留架构话语权 [Devin 对比]

**E. 学术/论文前沿**（2025–2026）
- **Prospective Reflection**：在 plan 阶段就反思，而非动后反思（PreFlect）[Paper]
- **Multi-agent Self-Improvement**：principle-based + procedural reflection 单循环合并（MARS）[Paper]
- **Intra + Inter Reflection**：动前自查 + 动后跨 agent 反思 + dual memory（MIRROR）[Paper]
- **Hierarchical Roles**：Context Manager + Meta-Thinker + Executor 三角色分离（COMPASS）[Paper]
- **Editable Meta-Improvement**：把"如何改进自己"的过程本身做成可改写代码（HyperAgents）[Paper]
- **Workspace Reconstruction**：每轮重建工作区，防止 mono-context 累积污染（IterResearch，已扩到 2048 轮 / 40K context）[Paper]
- **Semantic Memory Compression**：CLS 启发的语义压缩（SimpleMem，节省 30× token）[Paper]
- **Budget-Aware Context Mgmt**：把压缩当作 budget-constrained 决策（BACM / ContextBudget）[Paper]
- **Near-Constant Memory**：单步整合，每轮把 (Si, Ai, Oi) 剪掉，只留单一压缩状态（MEM1，3.5× perf / 3.7× 省内存）[Paper]
- **Step-level Compression + Plan Retell**：每步压缩 + 周期重述计划防遗忘（CSIM）[Paper]
- **Verifier-Driven Trajectory**：用独立 verifier agent 验证子任务/最终答案（Marco DeepResearch）[Paper]
- **Long-Horizon Trajectory 合成**：训练数据离线生成（OpenResearcher）[Paper]
- **Deep Research 产品形态**：OpenAI Deep Research / Claude Research / Kimi-Researcher / Grok DeepSearch / Gemini Deep Research [Paper]
- **Test-time Scaling**：受控 compute budget 下让 agent 在难题上继续推理 [Paper]
- **Test-time Verifier**：agent 自己当 verifier，结果不通过就再推一轮 [Paper]

**F. 来自 [§3](#3-agent-优化方向) TBD 的项**
- **多 Agent / SubAgent / A2A 协议** ：Google A2A、Anthropic 子代理协议、跨 agent 消息交换标准
- **Sandbox 支持** ：本地 docker / E2B / Modal 之类的隔离执行环境
- **用户自定义 Workflow** ：声明式（YAML / DSL）或可视化（Flow 图）编排 Agent 流程

### 4.6.2. 合并后的所有可能 feature 列表

把 [§3 12 项必做](#3-agent-优化方向) + [§3.x TBD](#3-agent-优化方向) + [§4.6.1](#461-更多可能feature-清单) 全部合并去重，按"能力领域"重新分大类。每条标记来源（[§3] = 已在 12 项必做、[§3.x TBD] = TBD 项、其余沿用 §4.6.1 的来源缩写）。

**A. Agent 循环与推理范式**
- A1. 基础 loop（ReAct / Plan-Execute / Loop）[§3 #2]
- A2. Plan 模式（生成计划 → 用户/自动审批 → 执行）[Cursor] [Devin]
- A3. Debug 模式（专做 runtime evidence 收集）[Cursor]
- A4. CodeAct：用代码作 action 替代 JSON tool call [Manus]
- A5. Hierarchical roles 分离（Planner / Executor / Context Mgr / Meta-Thinker）[Paper]
- A6. Workspace reconstruction（每轮重建，不累积）[Paper]
- A7. Interaction Scaling（百~千轮稳定运行）[Paper]
- A8. Test-time Scaling（在 budget 内多算几轮）[Paper]
- A9. Self-Improving Loop（meta-improvement，运行中改进自己）[Paper]

**B. 多 Agent / 子 Agent 协作**
- B1. 内部多 agent 流水线（Planner + Coder + Critic 等）[Devin] [Paper]
- B2. 并行 subagents（独立 context，结果回填主对话）[Cursor] [CC]
- B3. Custom subagent 定义（markdown/YAML frontmatter）[Cursor] [CC] [GHC]
- B4. Subagent 工具/模型/权限/skill 隔离 [CC]
- B5. Subagent 内 hook 生命周期 [CC]
- B6. A2A 协议（跨 agent 消息交换标准）[§3.x TBD]
- B7. Spec-driven coordinator（保留架构话语权）[Devin 对比]
- B8. Subagent Worktree 隔离（git 分支级）[CC]

**C. 会话 / Session 与触发入口**
- C1. Session（单次对话记录）[§3 #3]
- C2. 多渠道触发（Slack / GitHub issue / Linear / Web / 移动端 / Email）[Cursor] [Devin]
- C3. Headless / `-p` 程序化运行 [CC]
- C4. SDK 形式封装（Python / TS）[CC]
- C5. CI/CD 集成（`--bare` 跳过本地配置保证可重现）[CC]
- C6. 输出流格式标准化（text / json / stream-json）[CC]
- C7. 后台/异步 Cloud Agent + 自动 PR [Cursor] [Devin] [GHC]
- C8. Auto-Triage（监控告警自动转 Agent 任务）[Devin]

**D. 记忆 / 上下文工程**
- D1. Per-user 跨 session memory [§3 #4]
- D2. Memory 分级（user / project / local）[CC]
- D3. 跨 incident / 跨任务记忆 + 去重 [Devin]
- D4. 语义压缩（CLS 风格，semantic gating）[Paper]
- D5. Online 语义合成（写时去重）[Paper]
- D6. Intent-aware Retrieval [Paper]
- D7. Budget-aware 压缩决策 [Paper]
- D8. Near-constant memory（每轮整合 + 剪枝）[Paper]
- D9. Step-level 压缩 + 计划重述 [Paper]
- D10. 长 context（百万 token 级）协同 [Paper]

**E. 提示与自定义**
- E1. system/user/assistant prompt + 项目目录 prompt 文件（`.cursor/` / `.github/`）[§3 #5]
- E2. Custom Instructions 三层（repo-wide / path-specific / cross-agent）[GHC]
- E3. AGENTS.md 跨 AI agent 标准 [CC] [GHC]
- E4. applyTo glob 路径触发 [GHC]
- E5. excludeAgent 按 agent 类型禁用指令 [GHC]
- E6. Rules 三层（project / user / team）[Cursor]
- E7. 自定义 prompt files（`.prompt.md`）[GHC] [Cursor]

**F. Tools 与扩展**
- F1. 自实现 tools（RAG / web search 等）[§3 #6]
- F2. 把外部插件当作工具 [§3 #6]
- F3. Skills 标准（agentskills.io）[§3 #7]
- F4. Skill 完整内容预加载到 subagent context [CC]
- F5. MCP 标准支持 [§3 #8]
- F6. MCP 仓库级 JSON 配置 + secrets + toolsets header [GHC]
- F7. MCP scope 到 subagent [CC]
- F8. Browser-as-tool（Browser MCP / Stagehand / browser-use）[CU]
- F9. Computer Use（截屏 + 鼠键控任意 desktop 应用）[CU]
- F10. Browser-only Agent（隔离 Chromium）[Operator] [Mariner]
- F11. Deep Research 工具集（search / open / find / scrape / cite）[Paper]

**G. 反思 / 自检 / 自改进 / Harness**
- G1. Harness（基础自检/重问/修正）[§3 #10]
- G2. Prospective Reflection（plan 阶段就反思）[Paper]
- G3. Retrospective Reflection（动后反思，Reflexion 经典）[Paper]
- G4. Intra-reflection（动前自查）+ Inter-reflection（动后跨 agent）[Paper]
- G5. Principle + Procedural Reflection 单循环合并 [Paper]
- G6. Verifier Agent（独立验证子任务/最终答案）[Paper]
- G7. Test-time Verifier（自验，未过则继续推）[Paper]
- G8. Critic 子代理（plan 评审 / code review 评审）[Devin]
- G9. Editable Meta-Improvement [Paper]

**H. 沙箱 / 隔离 / 执行环境**
- H1. Sandbox 支持 [§3.x TBD]
- H2. 云 VM 全套（shell + editor + browser）[Devin]
- H3. 多 OS（Linux / Windows / Android Emulator）[Devin] [Manus]
- H4. Worktree 级隔离 [CC]
- H5. Auto-Run Allowlist [Cursor]
- H6. Permission Mode 6 档 [CC]
- H7. 隔离 Chromium [Operator] [Mariner]

**I. 长任务 / 异步 / 后台**
- I1. 长任务异步跑（云上跑、邮件/desktop/Slack 通知）[Cursor] [Devin]
- I2. 自动开 `agent/<task>` 分支并提 PR [Cursor] [Devin] [GHC]
- I3. 多 agent 并行任务监控页 [Cursor]
- I4. 长 horizon（百~千轮 tool call）稳定运行 [Paper]

**J. 安全 / 防注入 / 治理**
- J1. 防 Prompt Injection [§3 #11]
- J2. 命令白名单 + 网络白名单 [Cursor] [Devin]
- J3. 沙箱隔离防 data exfiltration [Devin]
- J4. 敏感操作前置 user prompt [CU] [Operator]
- J5. 团队级 policy 配置（managed settings）[CC]
- J6. CnP Refinement（基础 prompt 卫生）[§3 #12]

**K. 评估 / 评测**
- K1. Eval 框架（已有，TODO 后续扩）[新登记]
- K2. Agent Benchmark 跑分：SWE-bench / OSWorld / WebVoyager / GAIA / BrowseComp / Terminal-Bench [Paper] [新登记]
- K3. Token / Cost 计量 + budget 控制 [CC] [Paper]
- K4. Trajectory 日志结构化（便于回放、回归、训练）[Paper]
- K5. Code review agent / Bugbot 风格的 PR 级评审 [Cursor] [GHC]

**L. Thinking / 推理可视化**
- L1. Extended Thinking 模式（折叠 / 流式）[§3 #9]
- L2. 多模型 thinking 支持 [§3 #9]
- L3. Streaming token + thinking 分流（事件总线）[已实现 §4.5.4]

**M. UI / 表现层**
- M1. CLI 模式（已有）
- M2. Chainlit Web UI（已有）
- M3. Agents Window（并行任务面板）[Cursor]
- M4. Design Mode（UI 草图 → 代码）[Cursor]
- M5. PR review lifecycle 可视化 [Cursor]
- M6. Interactive Planning（Devin v2 风格）[Devin]

**N. Workflow / 编排**
- N1. 用户自定义 Workflow（声明式 YAML / DSL / 可视化 Flow）[§3.x TBD]
- N2. Hook lifecycle 自动化（PreToolUse / PostToolUse / Stop 等）[CC]
- N3. Slash command / 自定义快捷指令 [CC] [Cursor]

## 4.7. 确定 AgentA 中 Agent 部分的需求

确定哪些是本项目应该支持，能够支持，值得支持的。

### 4.7.1. 项目定位

**定位**：**个人学习者的私有 AI 助手**（个人知识管理 + 主动学习）。

**核心用户画像**：技术从业者/IT工程师 —— 平时积累大量论文、博客、技术书、笔记、代码片段；既需要"快速查旧资料"，也需要"主动学新东西"。

**场景（按 phase 演进）**

| Phase | 场景 | 增量 |
|---|---|---|
| Phase 1 | 个人知识助手 | RAG 能力 + 基础 Agent loop + Memory + 引用展示 |
| Phase 2 | + 学习/研究助理 | 在个人知识助手上叠加：**Plan**（学习计划）/ **SRS**（Spaced Repetition 主动复习）/ **Quiz**（出题） |

**两个场景共享 ~80%+ 代码**：RAG / Agent loop / EventBus / UI / Memory / Skill / MCP 全部共用；增量只在"学习闭环"业务逻辑（plan / SRS 调度 / quiz）。


- 个人知识助手是私有版 NotebookLM，叠加**主动学习/研究助理（Plan + SRS + Quiz）**
- 场景差异化：NotebookLM 只是被动 Q&A，本项目加入"主动陪你学"的循环，且全本地、隐私可控


### 4.7.2. 选定 Feature 列表
**已有 feature 优化**

| # | feature | 来源 | 实施位置 | 优化重点 |
|---|---|---|---|---|
| 1 | LLM API调用 | [§3 #1] | 可选项 → [Phase 3.4](#473-实施顺序) | 现状保留；可选加本地 LLM（呼应"全本地、隐私可控"卖点） |
| 2 | Agent 循环 | [§3 #2] | [Phase 2.1](#473-实施顺序) | 现状 ReAct 保留，Phase 2 引入 Plan-Execute（Plan 业务依赖） |
| 3 | Session | [§3 #3] | [Phase 1.1](#473-实施顺序) | 列表 / 搜索 / 恢复跨次对话 |
| 4 | Memory | [§3 #4] | [Phase 1.2](#473-实施顺序) | 单层 → user / project / local 三层（参考 Claude Code）；从被动 extract 升级为主动 consolidation |
| 5 | Prompt | [§3 #5] | [Phase 1.3](#473-实施顺序) | 用户偏好文件 `.agenta/rules.md`（参考 Cursor Rules / Copilot Custom Instructions） |
| 6 | Tools | [§3 #6] | 新增 tool 在 [Phase 2.2/2.3/2.4](#473-实施顺序) | 现有 RAG / web_search 保留；为 Phase 2 新增 plan_tracker / quiz_grader / srs_scheduler |
| 7 | Skills | [§3 #7] | [Phase 1.5](#473-实施顺序) | 框架强化（Skill 清单 / 自动加载 / Skill registry），承载 C3 的 quiz_maker / review_card / study_planner 等 |
| 8 | Thinking 模式 | [§3 #9] | [Phase 3.1](#473-实施顺序) | **本期仅做 CLI 渲染优化**（thinking 段分隔标记、流式分块输出）；WebUI 端的可折叠/展开等渲染留待 [design.md §4.2 WebUI](design.md#42webui) 单独优化任务 |

**新增 feature**

| # | feature | 类别 | 来源 | 实施位置 | 备注 |
|---|---|---|---|---|---|
| 1 | MCP | 基础设施 | [§3 #8] | [Phase 3.2](#473-实施顺序) | 至少接 1-2 个标杆 server（fetch / filesystem / 第三方知识源） |
| 2 | 防 prompt injection | 安全 | [§3 #11] | [Phase 3.3](#473-实施顺序) | RAG 召回内容过滤 + 命令白名单 |
| 3 | Harness（自检 / 反思） | 通用能力 | [§3 #10] + [§4.6.2 G1/G3] | [Phase 2.5](#473-实施顺序) | Reflexion 风格：Quiz 答案自评、Plan 执行回顾、RAG 召回判定 |
| 4 | 引用展示（jump to source） | C1 UX | [§4.6.2 衍生] | [Phase 1.4](#473-实施顺序) | 输出格式约定：每条回答含 `[source: file#section]`，UI 可点击跳转原文 |
| 5 | **Plan**：学习计划生成 | C3 业务 | [新] + [§4.6.2 A2] | [Phase 2.2](#473-实施顺序) | 用户给出目标（如"准备 ML 面试"）→ Agent 生成阶段性计划 + 进度跟踪 |
| 6 | **SRS**：Spaced Repetition 主动复习调度 | C3 业务 | [新] + [§4.6.2 C8 风格] | [Phase 2.4](#473-实施顺序) | 后台 scheduler 按遗忘曲线主动触发复习；与被动 Q&A 拉开根本差距 |
| 7 | **Quiz**：出题 | C3 业务 | [新] + [§4.6.2 F1/F3] | [Phase 2.3](#473-实施顺序) | 基于知识库内容自动出题（多选/简答），结合 Harness 自评 |


### 4.7.3. 实施顺序

按 [§4.7.1 phase 演进表](#471-项目定位) 分 3 块，每块内部按"依赖 → 业务 → 增强"排序。每个 feature 标 P0/P1/P2 优先级（同 phase 内）：P0 = phase 出口必备、P1 = 强化、P2 = 锦上添花。

**Phase 1: 个人知识助手 **
个人笔记 + 阅读笔记 + 收藏文章的自然语言问答 + 主动复盘

| 序 | feature | 类型 | 优先级 | 出口判据 |
|---|---|---|---|---|
| 1.1 | Session 列表/搜索/恢复 | 已有强化 | P0 | CLI 能 `/sessions list` 看历史会话并恢复 |
| 1.2 | Memory 三层 + 主动 consolidation | 已有强化 | P0 | 跨次对话能记住"用户喜欢中文回答""偏好引用论文页码" |
| 1.3 | Prompt 用户偏好（`.agenta/rules.md`）| 已有强化 | P1 | 项目根放 rules.md 自动注入 |
| 1.4 | 引用展示（jump to source）| 新 | P0 | 每条回答末尾列 `[source: file#section]`，Chainlit 可点击 |
| 1.5 | Skills 框架强化（registry / 自动加载）| 已有强化 | P0 | 为 Phase 2 的 Quiz/Plan Skill 铺路 |

**Phase 1 目标**：能塞个人文档 → 自然语言查 → 跨 session 记得偏好 → 答案带可跳转引用。

**Phase 2: 学习/研究助理 **
个人书籍/论文/课程笔记 → 检索 + 总结 + 测验 + 复习计划

| 序 | feature | 类型 | 优先级 | 出口判据 |
|---|---|---|---|---|
| 2.1 | Agent 循环升级（Plan-Execute） | 已有强化 | P0 | 2.2 前置依赖；Agent 能执行多步骤计划 |
| 2.2 | **Plan**：学习计划生成 | 新 P0 业务 | P0 | 用户给目标（"准备 ML 面试"）→ Agent 输出阶段计划 + 跟踪进度 |
| 2.3 | **Quiz**：出题（Skill + tool） | 新 P0 业务 | P0 | 基于 KB 内容自动出 5~10 题（多选/简答），用户答完给反馈 |
| 2.4 | **SRS**：主动复习调度 | 新 P0 业务 | P0 | 后台 scheduler 按遗忘曲线提醒复习；可对接通知（邮件 / 系统 toast） |
| 2.5 | **Harness**：自检 / 反思 | 新 | P1 | Quiz 答案自评、Plan 执行后回顾、RAG 召回相关性自判 |

**Phase 2 目标**：用户给学习目标 → 生成计划 → 出题 → 复习 → 自检循环，全程 Agent 主动驱动。

**Phase 3: 工具和安全补强**

| 序 | feature | 类型 | 优先级 | 出口判据 |
|---|---|---|---|---|
| 3.1 | Thinking 模式 **CLI 渲染优化**（段分隔标记 / 流式分块输出） | 已有强化 | P1 | CLI 跑带 thinking 的 query 时，thinking 段有清晰起止标记 + 与正文 token 不混；WebUI 渲染**不在本期 scope**，留待 [design.md §4.2 WebUI](design.md#42webui) |
| 3.2 | MCP（接入 1-2 个标杆 server） | 新 | P0 | 至少 fetch + filesystem 能跑通；求职硬通货 |
| 3.3 | 防 prompt injection（召回过滤 + 命令白名单） | 新 | P1 | 面试能讲"safety 怎么做的" |
| 3.4 | 本地 LLM 支持 | 已有强化 | P2 | 呼应"全本地、隐私可控"卖点；非必需 |

## 4.8. 如何评估 Agent 实现（方法论）

本节只产出评估方法论（评什么 / 怎么评 / 什么时候评）和工具列表，不写代码。
要列清楚每个 feature 该评什么维度、每个 Phase 出口该达到的评估标准，要同时考虑复杂度和工作量。
尽量考虑多feature综合评估，避免工具爆炸。

### 4.8.1. 评估方法论

**评什么（维度）**

按 [§4.7.2 feature 列表](#472-选定-feature-列表) 倒推，agent 评估维度如下：

| 维度 | 适用 feature | 说明 |
|---|---|---|
| 功能正确性 | 所有 | 输入→输出基础正确性 |
| 记忆/检索准确性 | Memory / SRS / 引用展示 / RAG | 召回是否准、引用是否对得上原文 |
| 推理质量 | Plan / Quiz / Harness / Agent 循环 | LLM 输出"好坏"（超出"对错"的主观判定） |
| 安全性 | 防 prompt injection | 攻击样本是否被识别拦截 |
| UX 流畅度 | Thinking CLI / Session / Skills | 主观体验 |
| 性能 / 成本 | 所有 | 延迟、token、API 费用 |

**怎么评（方法清单）**

| 方法 | 描述 | 工作量 | 适用维度 |
|---|---|---|---|
| Unit Test | 函数级单测 | 低（pytest 已就位） | 功能正确性 |
| Profiling | 延迟 / token / cost 自动采集 | 低 | 性能 / 成本 |
| Golden Set | 标准 Q-A 对集合，自动跑 + match | 中 | 记忆 / 检索 / 引用 |
| 人工评分 | 自己跑 + 1~5 评分 + 记笔记 | 低（费时） | UX |
| LLM-as-Judge | frontier 模型评分（指定 judge prompt） | 中 | 推理质量 |
| Trajectory Replay | 录 agent 完整事件流 → 离线分析 / 回放 / diff | 中 | 多轮交互复杂 feature |
| Adversarial Test | 攻击样本库 + 拦截率统计 | 中 | 安全 |

**什么时候评**

| 时机 | 触发点 | 跑什么 |
|---|---|---|
| Feature 完成 | [§4.9](#49-开始实现) step 5 | 该 feature 的所有评估方法 |
| Phase 出口 | Phase 末尾人工触发 | 该 phase 全部 feature 的**综合场景** eval |
| Release 前 | [§4.11](#411-更新-readme) 前 | 跑全套，产出 report 作为求职亮点 |
| CI 回归 | 每次代码改动 | 仅 Unit + Profiling（LLM-judge 因成本不上 CI） |

**Feature × 评估方法 矩阵**

✅ = 推荐做，○ = 可选，空白 = 不适用：

| Feature | Unit | Profiling | Golden | Judge | Replay | 人工 | Adv |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1.1 Session | ✅ | ✅ | | | | ○ | |
| 1.2 Memory | ✅ | ✅ | ✅ | | | | |
| 1.3 Prompt | ✅ | | | | | ✅ | |
| 1.4 引用展示 | ✅ | | ✅ | | | | |
| 1.5 Skills | ✅ | | | | | ○ | |
| 2.1 Plan-Execute | ✅ | ✅ | | | ✅ | | |
| 2.2 Plan | ✅ | | | ✅ | ○ | | |
| 2.3 Quiz | ✅ | | ✅ | ✅ | | | |
| 2.4 SRS | ✅ | | ✅ | | | | |
| 2.5 Harness | ✅ | | | ✅ | ✅ | | |
| 3.1 Thinking CLI | ✅ | | | | | ✅ | |
| 3.2 MCP | ✅ | ✅ | | | | | |
| 3.3 防 prompt injection | ✅ | | | | | | ✅ |
| 3.4 本地 LLM | ✅ | ✅ | | | | | |

**Phase 出口标准**（综合场景 eval，非单 feature）

| Phase | 出口判据 |
|---|---|
| Phase 1: 个人知识助手 | 跑 20 个真实个人知识查询场景：引用准确率 ≥ 90% / Memory 跨 session 召回 ≥ 80% / UX 自评 ≥ 4(满分5) |
| Phase 2: 学习/研究助理 | 跑 1 个完整学习目标闭环（如"准备 ML 面试"）：Plan 生成 → 3 轮 Quiz → SRS 调度 → 进度可视化全程无中断；Plan / Quiz judge 评分 ≥ 4 |
| Phase 3: 工具和安全补强 | feature × eval 矩阵全跑通；性能/成本数据齐全；adversarial test 100 样本拦截率 ≥ 95% |


### 4.8.2. 评估工具列表

按"多 feature 综合复用"原则组织，**避免每 feature 一个独立工具**。

**Framework 层（[§4.10](#410-配套-toolstoolsagent_eval) 实现）—— 跨 feature 通用基础设施**

| 工具 | 用途 | 谁用 |
|---|---|---|
| `runner` | 统一 eval 入口（按 feature / phase / full 选择） | 所有 eval |
| `report` | 渲染 markdown / JSON 报告 | 所有 eval |
| `judge` | LLM-as-Judge 通用模块（judge prompt + 待评内容 → 分数 + 理由） | Plan / Quiz / Harness |
| `trajectory` | 录制 agent run 完整事件流 / 离线回放 / diff | Plan-Execute / Harness / 任何多轮 feature |
| `profiler` | 延迟、token、cost 自动采集 | 所有 |
| `golden_loader` | golden set 加载 / 版本管理 | Memory / 引用展示 / SRS |
| `adversarial_runner` | 攻击样本批跑 + 拦截率统计 | 防 prompt injection |

**Feature 专属层（[§4.9](#49-开始实现) 各子章节实现）—— 只放 dataset + 该 feature 专属 metric**

每个 feature 在 `tools/agent_eval/features/<feature>/` 下放：
- `dataset.json`（golden set / judge prompt / 攻击样本，按需）
- `metric.py`（专属打分逻辑，调 framework 通用模块）

**跨 feature 的工具复用关系**

| 复用关系 | 共享对象 |
|---|---|
| Plan / Quiz / Harness | 共享 `judge` framework，各自 judge prompt 不同 |
| Memory / 引用展示 / SRS | 共享 `golden_loader`，各自 dataset schema 略不同 |
| Plan-Execute / Harness | 共享 `trajectory` 录制回放 |
| 全部 feature | 共享 `runner` / `report` / `profiler` |

**避免工具爆炸的硬约束**

1. **不为单一 feature 新增 framework 工具**：除非至少 2 个 feature 能用
2. **Dataset 走声明式 JSON/YAML**：不写 dataset 加载代码，统一用 `golden_loader`
3. **Judge prompt 走文件**：`tools/agent_eval/features/plan/judge.txt` 这种，不硬编码进 Python
4. **新工具进 framework 前先在 feature 层重复 2 次**：第 2 次出现复制粘贴才上升为 framework



## 4.9. 开始实现
按[4.7.3](#473-实施顺序)的实施顺序，逐功能实现：
1. Review 当前代码，如已有初步实现，给出优化建议，写到对应子章节，如 [4.9.1 Session 列表/搜索/恢复(phase 1.1)](#491-session-列表搜索恢复phase-11)
2. 如是新需求，给出实施计划，写到对应子章节，如 [4.9.1 Session 列表/搜索/恢复(phase 1.1)](#491-session-列表搜索恢复phase-11)
3. 按计划实现代码
4. 添加 UT 进行初步验证
5. 按 [§4.8 评估方法](#48-如何评估-agent-实现方法论)进行评估，工具在 `tools/agent_eval/` 统一管理。
6. 更新design文档 到 [design.md](design.md) 对应子章节， 如 [Session 管理](design.md#33-session-管理)。desgin 文件要写的简练，抓住重点，风格要跟原来的章节统一。


### 4.9.1 Session 管理 (phase 1.1)

**Step 1 · Review 现状**

Session 基础设施已经相当厚（`src/memory/chat_history.py` + `src/cli/handlers.py` + `src/cli/tab_complete.py`）：自动创建、`list_sessions()` API（含 first_user_msg + msg_count）、`/session <id>` 切换 + prompt 恢复、`/del-session`、`/clean-session`、`/history`、`/save`、tab 补全（短 id + 首问 label）全部已实现。**[§4.7.3](#473-实施顺序) Phase 1.1 字面 P0 出口已满足**。

按 feature 名 "列表/搜索/**恢复**" 三件套 + [§4.8 UX 流畅度](#481-评估方法论) 维度做缺口分析：

| 等级 | 缺口 | 影响 |
|---|---|---|
| A | **无搜索能力** | feature 名明写"搜索"但 `list_sessions()` 只 ORDER BY，session >30 个时找不到东西 |
| A | `/session` 双语义重载（list + switch 共用一个命令） | 新用户难记，UX 自评扣分 |
| B | 时间用 ISO 字符串 | 不直观 |
| B | 当前活跃 session 在 list 里无标记 | 一眼看不出在哪 |
| B | 切换后无最近消息预览 | 切回旧 session 必须再敲 `/history` |
| Punt | 分页 / Session 命名 / 标签 / 收藏 | over-engineering for MVP |
| Punt | Chainlit 端同步 | 出口判据明写 "CLI 能"，留 [design.md §4.2 WebUI](design.md#42webui) 一并处理 |
| Punt | Session 关联 project 层 | 等 [Phase 1.2 Memory 三层](#473-实施顺序) 做时按需 ALTER TABLE 加列 |

**Step 2 · 实施计划**

本期 scope：A1+A2 必做 + B1+B2+B3 强化（共 5 项）。命令拆分用**硬拆分**：`/session <id>` 仅做 switch，`/sessions [query]` 做 list/搜索。原则：复数=对集合操作，单数=对单个操作。

| 改动 | 文件 | 内容 |
|---|---|---|
| 1 | `src/memory/chat_history.py` | `list_sessions(query=None, limit=None)`：按 session_id 前缀或 first_user_msg LIKE 过滤 |
| 2 | `src/cli/handlers.py` | `list_sessions` 输出：时间格式化（B1）+ 当前 session 高亮（B2）+ 接受 query 参数 |
| 3 | `src/cli/handlers.py` | `switch_session` 末尾追加最近 2 条消息预览（B3） |
| 4 | `main.py` | 拆 `/sessions [query]`（list/搜索） vs `/session <id>`（switch 专用）；单 `/session` 报错提示用 `/sessions` |
| 5 | `src/cli/ui.py` | help 文本更新 |
| 6 | `src/cli/tab_complete.py` | 补全加 `/sessions`，`/session` 只补全 `<id>` 形式 |
| 7 | `tests/test_chat_history_search.py` | search filter 单测 |
| 8 | `tests/test_cli_handlers.py` | 命令拆分后的行为 |

[§4.8 评估](#48-如何评估-agent-实现方法论) 按 [Feature × 方法矩阵](#481-评估方法论) Session 行：**Unit ✅ + Profiling ✅ + 人工评分 ○**。

**Step 3 · 代码实现**

按计划 8 项改动全部落地（无 scope 蔓延）。关键代码位置：

| 改动 | 实现位置 | 行号 |
|---|---|---|
| Store 层 search filter | `src/memory/chat_history.py:list_sessions` | `query=None, limit=None`，LIKE OR session_id 前缀 |
| 时间格式化 | `src/cli/handlers.py:_format_relative_time` | 今天/昨天/N 天前/日期/降级 5 分支 |
| List 输出强化 | `src/cli/handlers.py:list_sessions` | 多 `query` 与 `current_session_id` 参数，▶ 标记当前 |
| 切换预览 | `src/cli/handlers.py:switch_session` | 末尾 `_SWITCH_PREVIEW_COUNT=2` 条预览 |
| 命令硬拆分 | `main.py /sessions` / `main.py /session <id>` | 单 `/session` 报错，引导用 `/sessions` |
| Help / Tab | `src/cli/ui.py` + `src/cli/tab_complete.py` | `/sessions` 加入静态命令表 |

**Step 4 · UT 结果**

- 新增 [`tests/test_memory.py::TestListSessionsSearch`](../tests/test_memory.py) 11 个测试覆盖 query/limit 6 种边界（None / 空串 / 全空白 / id 前缀 / 大小写 / limit=0）
- 扩展 [`tests/test_cli_handlers.py`](../tests/test_cli_handlers.py) `TestFormatRelativeTime` 7 个测试（5 个时间分支 + 解析失败 + 长串截断）、`TestListSessionsOutput` 4 个、`TestSwitchSessionPreview` 3 个
- 全量回归：`pytest` → **352 passed, 0 failed**（含 18 个本次新增测试，跑 52s）

**Step 5 · §4.8 评估**

| 维度 | 方法 | 结果 |
|---|---|---|
| Functional | Unit (18 新增) | 全过；search filter / 时间格式化 / 高亮 / 预览 / 命令路由全覆盖 |
| Perf/Cost | Profiling | [`tools/agent_eval/feature_session/session_perf_eval.py`](../tools/agent_eval/feature_session/session_perf_eval.py) 跑 size = 10/100/1000/5000；5000 sessions 时查询 13.3ms / 渲染 35.6ms，全面优于判据（< 50ms / < 200ms） |
| UX | 人工实测（5 分制） | **待人工实测回填**。维度：列表清晰度 / 搜索易用性 / 切换流畅度 / 命令记忆度。测试动作：`/sessions`、`/sessions <关键词>`、`/session <id>`、单 `/session`（验报错提示） |

| 指标 | size=10 | size=100 | size=1000 | size=5000 |
|---|---:|---:|---:|---:|
| 查询无 filter | 0.10ms | 0.31ms | 3.00ms | 13.32ms |
| 查询 id 前缀 | 0.10ms | 0.16ms | 0.50ms | 2.14ms |
| 查询 keyword LIKE | 0.10ms | 0.18ms | 0.99ms | 4.03ms |
| 渲染全量 | 0.14ms | 0.76ms | 10.75ms | 35.59ms |
| 渲染过滤 | 0.13ms | 0.42ms | 2.10ms | 9.65ms |

观察：搜索因结果集变小通常**比无 filter 更快**；FTS5 全文索引 / B-tree 索引等性能优化在 10K 量级内完全没必要 —— 与 [§4.8.2 硬约束 1](#482-评估工具列表)（"不为单一 feature 新增 framework 工具"）同精神：当前 1 个 feature 不上抽象，等真到瓶颈再说。

**Step 6 · design.md 同步**

新增 [`design.md §3.3 Session 管理`](design.md#33-session-管理)：表结构 / API 列表 / CLI 命令约定（单复数分工原则）。

**总结**

- 改动量：6 个源文件 + 2 个测试文件 + 1 个 eval 脚本，无破坏性 API 变更
- 出口判据：**已超出** [§4.7.3 Phase 1.1](#473-实施顺序) 字面 P0（"看 + 恢复"），补齐"搜索"+ UX 强化 5 项
- Punt 项确认：分页 / Session 命名 / Chainlit 同步 / project 关联 留到对应 Phase 处理


### 4.9.2 Memory 管理 (phase 1.2)
三层 + 主动 consolidation

### ...


## 4.10. 配套 tools（tools/agent_eval）
这里只包含 Agent 评估工具框架，具体feature 的工具在对应[4.9](#49-开始实现)子章节实现。


## 4.11. 更新 README


## 4.12. CnP 优化
性能优化


# 5. Future

本节登记当前 scope 之外、本架构可扩展但暂不实现的方向，供未来评估。

## 5.1. C4 企业内 Q&A

**场景**：把 AgentA 部署为公司内部 HR / IT / 销售知识库的多轮问答助手，员工自然语言提问，Agent 检索 + 多轮澄清 + 流程引导。

**复用**：RAG / Agent loop / EventBus / UI / Memory / Skill / MCP 全部沿用，无需重构。

**增量**：权限过滤（按用户角色裁剪可见知识库）/ 审计日志（每次查询/回答留痕）/ SSO 集成 / 多用户 session 隔离 / 流程引导 Skill（如"年假申请流程"）。

**为何当前不做**

- 演示数据需伪造，面试故事性弱（不如 C1/C3 是"自己每天在用"）
- 增量代码是"企业 infra 维度"（权限/审计/SSO），与本项目想展示的 RAG + Agent 技术深度无强相关
- 三场景全做会让 [项目定位](#471-项目定位) 散掉（同时面向"个人学习者"和"企业员工"，定位不聚焦）

**何时考虑做**：找企业 AI 应用 / SaaS 方向岗位时，作为"我做过的扩展性验证"加补丁实现；或拿到真实企业数据集（去敏后）可用时。