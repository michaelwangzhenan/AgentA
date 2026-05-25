# 1.为什么有 AgentA

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


# 2.当前状态

- 4大块都已有一些实现
- RAG部分已比较完整，暂时不做优化
- Agent 部分是马上要进一步完善的重点
- UI 部分等 Agent 完善后再继续
- 三种实现都有一些框架代码，现在以 Ptyhon 为主，马上进行的 Agent 也以Python 分支继续。

# 3.Agent 优化方向

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


# 4.Agent 改进计划

## 4.1.Review 现有代码
Review 完整实现 @AgentA 目录
只了解现状，不需要输出

## 4.2.重构评估
基于4.1，重新整体设计 AgentA 架构：Agent, RAG, CLI/UI

设计原则：
- **Agent core 与表现层解耦**：Agent core 不假设 IO 形式（CLI / 未来 Web UI / SDK 都能接），通过 Stream / Callback 接口对外，UI 阶段不需要回头改 Agent core
- **RAG 内部不动**，但要重新约定 Agent 调用 RAG 的对外接口（返回结构、metadata 暴露、错误降级行为等）
- **三种 impl 共享公共层**，差异只在 Agent loop 那一层（详见 4.3）

输出：
- 整体架构 mermaid 图，覆盖三大模块（Agent / RAG / CLI/UI）+ 各模块内部结构画到位（作为整个工程的设计文档）
- 写入 [整体架构](design.md#1整体架构) 章节

## 4.3.三种实现模块化共享
目标：模块化共享，抽离公共部分（LLM provider / RAG / Tools），三个 impl 只换"Agent loop"那一层

**命名约定（4.5 重构 + 后续新增模块都按此走）**：
- **依赖层**（不感知 Agent loop 的底层能力）：数据存储用 `*Store` 后缀，如 `ChatHistoryStore` / `UserMemoryStore`
- **Helper 层**（封装业务策略、被三种 impl 共享）：按角色选后缀
  - `*Manager`：策略编排（如 `HistoryManager` / `MemoryManager`）
  - `*Engine`：执行流水线（如 `ToolCallEngine`）
  - `*Policy`：纯策略判定（如 `ThinkingPolicy`）
  - `*Bus`：事件分发（如 `EventBus`）

## 4.4.清理前期不必要功能/代码
根据新的架构，评估哪些功能/代码是不必要的，需要清理
例如：
1. 因为现在已有 tools/rag_cli.py, CLI中的 /ingest 命令可以删掉
2. 考虑到后续还有UI功能，CLI的定位需要重新考虑

### 4.4.1. To clean up

按"风险 / 收益 / 重构边界"分三档：

#### 第一档：直接清理（明确冗余，行为零变化）

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

#### 第二档：定位调整（不删代码，配合 #1 改文档与帮助文本）

4. **CLI 重新定位为"开发调试 / 服务器无 GUI" 用** ：
   - 用户向命令（`/help /clear /history /session /memory /thinking /save /reload-*` + Prompt/Skill 切换）全部保留 —— CLI 仍是最快的调试入口
   - 运维类一律走 `tools/`：本次删 `/ingest`，今后类似命令（`/clear-kb` 等）也不再加进 CLI
   - `README.md` "Quickstart" 把 Chainlit UI 提到首位、CLI 作为副入口注明用途；`ui.py` BANNER 副标题加"CLI for dev / headless"
   - 决策记录写进本节，避免未来再有人往 CLI 加运维命令

#### 第三档：本步只登记，留到 4.5 重构时处理

> 这些是**架构层耦合问题**，与 helper 抽离绑在一起，单独动会破坏行为或与 4.5 冲突。

5. `src/agent/agent.py` 与 `src/agent/autogpt_agent.py` 反向 import `src.cli.skill_loader` —— Agent core 不应依赖表现层目录；4.5 把 `SkillCatalog` 抽到 `src/agent/core/` 后顺手解掉
6. `src/agent/agent.py` 与 `src/agent/autogpt_agent.py` 直接 import `src.memory.user_memory` 的 `should_extract_immediately` / `extract_memories` —— 应封装进 `MemoryManager` helper
7. `src/cli/ui.py` BANNER 在 import 时 `format(config.IMP_METHOD, ACTIVE_PROVIDER)` —— 运行时切 provider 不刷新；要等 ChatSettings 真正能改 provider 之后一起整改
8. 三个 Agent impl 内部各自构造 SkillCatalog / 拼 `<skill_content>` —— 4.5 抽 `SkillCatalog` helper 时统一

### 4.4.2. Impl plan

按"可独立回滚"分 5 步，每步结束都跑一次回归脚本。

#### Step 1: 删除 `/ingest` 全链路（Tier 1.1）

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

#### Step 2: 删除 `LangChainAgent.chat()` alias（Tier 1.2）

1. **`src/agent/langchain_agent.py`**：删 64-65 行 `def chat(...)` 方法
2. **`tests/test_langchain_agent.py:80`**：`ag.chat('hi')` 改为 `ag.run('hi')`

#### Step 3: 清掉 `handlers.py` 局部冗余 import（Tier 1.3）

1. **`src/cli/handlers.py:57`**：删函数体内的 `import sys`（顶部 line 8 已有）

#### Step 4: 文档同步 + CLI 定位（Tier 2.4）

仅改"活文档"，历史记录（`docs/iter_0.md` / `docs/iter_1.txt`）保持原样：

1. **`README.md`**：
   - `2.4.启动 AgentA` 调整：Chainlit 段提到 CLI 之前，CLI 段注明"主要用于开发调试 / 无 GUI 场景"
   - 删 quickstart 里 "首次使用先 /ingest 把 ./datasets/data_en 入库"，改写为引用 `python -m tools.rag_cli ingest -m m3`
   - `4.1.RAG 入库` 章节描述里 "（与 `/ingest` 等价）" 字样去掉
2. **`.env.example:23`**：注释 "(/ingest 命令默认扫描的目录)" → "(tools/rag_cli.py ingest 默认扫描的目录)"
3. **`src/rag/ingest.py`** 顶部 docstring 第 8 行 + 332 行的 "/ingest 等价"字样：改为 "与 `tools/rag_cli.py ingest` 等价"
4. **`tools/rag_cli.py`** 顶部 docstring 第 7 行 "main.py 中的 /ingest" 字样改为 "原 CLI `/ingest`（已废弃）"

> `ui.py` BANNER 副标题按用户决定**不加**。

#### Step 5: 回归验证

| 项 | 命令 | 预期 |
|---|---|---|
| 单元测试 | `pytest tests/ -x` | 全绿 |
| CLI 启动 | `python main.py` → `/help` | help 不再出现 `/ingest`；其他命令一切照旧 |
| 运维工具 | `python -m tools.rag_cli status` | 与改前输出一致 |
| Chainlit | `chainlit run chainlit_app.py --port 8000` → 打开 settings 面板 | 不再出现 "Ingest Docs Dir" / "Ingest Embedding Alias" 两项；其他设置正常 |

### 4.4.3 UT Refinement
为后续 4.5 重构 + 4.9 加 feature 时**不破坏现有行为**，先对 UT 做范围聚焦与缺口评估。

#### 缺口评估

4.5 会抽出 5 个 helper（`EventBus` / `ToolCallEngine` / `HistoryManager` / `MemoryManager` / `ThinkingPolicy`）。当前 UT 对它们的覆盖：

| 待抽 Helper | 当前 UT 覆盖 | 是否足以防止重构回归 |
|---|---|---|
| `ThinkingPolicy` | `TestEstimateThinkingBudget` 8 例 + `TestAgentThinkingInit/Run` 7 例 | ✅ 足够 |
| `ToolCallEngine` | `TestAgentToolCall` 3 例 + `TestToolGuidance` 4 例 | ✅ 足够（hint 注入、tool_call_id、轮数上限都有） |
| `HistoryManager` | 无 ❌ —— `_load_truncated_history` / `_collect_skill_pairs` 当前 0 个直接单测，只在 integration 间接覆盖 | ❌ 不够 |
| `MemoryManager` | `test_user_memory.py` 覆盖底层 Store + sanitize + extract；但**注入 system_prompt 的行为 + N 轮后自动触发抽取**无 UT | ❌ 不够 |
| `EventBus` | 无 ❌ —— 当前 `set_token_callback` / `set_thinking_callback` 0 个直接单测，只在 chainlit_app 真实运行时验 | ❌ 不够 |

#### 改进计划

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

在 §4.9 `step 2.4 测试功能` 落实：每个新 feature **必须**至少 1 个 unit 覆盖核心行为，命名 `tests/test_<feature>.py`。Showcase / Learning / Foundation 三档都执行此规则，差别只在覆盖深度。


#### 决策

- A + B 共 5 项 **在 4.5 之前作为前置任务做完**
- C 在 §4.9 落实

#### 执行结果

5 个新增测试文件，全部默认套件中跑：55 passed + 1 skipped（LangChain import 失败）+ 2 xfailed（EventBus 未抽出前的契约 placeholder）。

| 文件 | 用例数 | 覆盖点 |
|---|---|---|
| `tests/test_history_manager.py` | 11 | `_load_truncated_history` 截断 / 空历史 / system 过滤 / SQL 粗粒度上限 + `_collect_skill_pairs` skill 组保护 |
| `tests/test_memory_manager.py` | 10 | `Agent.run` 注入 `<user_context>` 三态 + `_try_extract_memories` 五种触发分支 + 异常静默 |
| `tests/test_event_callbacks.py` | 13（含 2 xfail） | `set_thinking/token_callback` 安装/重置/替换 + `_on_thinking_chunk` 透传 + EventBus 未抽出前的扇出/隔离 placeholder |
| `tests/test_format_search_results.py` | 12 | 空 hits / score vs distance / 多 hit 分隔 / retrievers / heading_path / page_no |
| `tests/test_agent_protocol.py` | 12（含 1 skip + 1 xfail） | Python & AutoGPT 的 `run / activate_skill / session_id` 签名一致；事件接口当前分布；EventBus 未对齐前的 xfail 锁定 |

**§4.5 重构信号约定**：
- `test_event_callbacks.py::TestFutureEventBusContract::test_multiple_subscribers_fan_out` 由 xfail 转 pass = EventBus 多订阅扇出已实现（callback 字段从 `callable|None` 改为 `list[callable]`）
- `test_agent_protocol.py::TestEventInterfaceFutureContract::*` 由 xfail 转 pass = AutoGPT 已接入统一事件接口
- `test_agent_protocol.py::TestLangChainAgentProtocol::*` 由 skip 转 pass = LangChain 环境修复（依赖路线另议：升级到 langchain 1.0+ / 回退 `AgentExecutor` / 改 langgraph）
- `test_event_callbacks.py` 重命名为 `test_event_bus.py`，把测试主语从 `agent` 改为 `agent.events`

## 4.5.根据新的设计，调整代码框架
1. 把代码框架，按新的架构调整好
2. 回归测试，确保功能正常（§4.4.3 已就位的 5 个安全网文件 + 2 个 xfail 信号会自动提示重构进度）

## 4.6.Agent 的最新功能/技术探索
[3.Agent 优化方向](#3agent-优化方向) 中已列的 12 项是**必做项**（基础盘）。
本步在此基础上**补充候选**：调研最新 Agent 论文 / 产品（GHC、Cursor、Claude Code 等）+ [3.x TBD] 中的项，列出所有可能新增的功能/技术作为**候选清单**。

输出：候选清单（每项一句话简介 + 主要参考来源）

## 4.7.确定 AgentA 中 Agent 部分的需求

确定哪些是本项目应该支持，能够支持，值得支持的。

输入：[3.x 12 项必做] + [4.6 候选清单]

1. Review 4.6 输出
2. 决策：
   - **12 项必做**：排优先级（先做哪个、后做哪个）
   - **候选清单**：选 3~5 个深做（Q1=B 原则，找有教学/展示亮点的）
3. 未选中的候选 → 作为知识积累

## 4.8.如何评估 Agent 实现
给出评估 Agent 的方案

## 4.9.开始实现
1. 根据前面的评估，给出 Agent部分的需求分析（需要支持的功能，以及优先级）
2. 逐功能实现
    2.0 评估 feature 重要性：Showcase(找工作重点讲)/Learning(学习用,能跑通即可)/Foundation(基础设施)
    2.1 Review 当前代码，如已有初步实现，给出优化建议
    2.2 如是新需求，给出实施计划
    2.3 按计划实现功能
    2.4 测试功能（**必加 unit**，遵循 §4.4.3 C 规则）
    2.5 更新design文档
    2.6 评估是否需要新加工具，在 [配套 tools](#410配套-tools) 实现

## 4.10.配套 tools(参考/tools下的 RAG tool)
1. Review 9.2.6 累积的工具候选清单 → 合并、取舍、定优先级
2. 逐工具实现

## 4.11.更新 README