
# 1. RAG

## 1.1. 文件职责速查

| 路径 | 角色 | 主要入口 |
|---|---|---|
| `src/rag/parser.py` | 多格式 → 纯文本（txt/md/html/pdf/docx/pptx/xlsx + OCR 兜底）| `parse_file(path)` |
| `src/rag/splitter.py` | 结构化分块（识别 Markdown 标题与 PDF 页号作为锚点，把父级标题路径作为前缀注入 chunk 文本）| `split_structured(text, ...)` |
| `src/rag/ingest.py` | 入库主流程（遍历目录 → parse → split → 双索引写盘 + 幂等增量）| `ingest_all(...)` · CLI `python -m src.rag.ingest` |
| `src/rag/bm25_index.py` | BM25 Okapi 自实现（倒排索引 + bigram 中文分词 + pickle 持久化）| `get_index(coll)` |
| `src/rag/query_rewriter.py` | 三轴 query 改写（Multi-Query / HyDE / 翻译轴），LRU 缓存包装 | `expand_queries(query)` |
| `src/rag/retriever.py` | 检索总枢纽：多 query × 多 collection × dense+bm25 → RRF → 阈值 → rerank → dedupe(去重）) | `search(query, ..., rerank=None)` |
| `src/rag/reranker.py` | Cross-Encoder 精排，输出统一 sigmoid 归一化到 [0,1] | `rerank(query, hits, top_k)` |
| `tools/rag_eval/runner.py` | 端到端检索评估（黄金集 → 指标 → Markdown 报告 + `.log` 伴生文件）| `python -m tools.rag_eval.runner` |


## 1.2. 两条主调用链

**生产路径**（Agent 在线检索）：

```
Agent 工具调用
  └─ src/agent/tools.py · _tool_search_knowledge
       └─ src/rag/query_rewriter.py · expand_queries(query)         ← 三轴改写
            └─ src/rag/retriever.py · search(query, queries=...)    ← 总枢纽
                 ├─ _query_collection(...)                          ← Dense 召回
                 ├─ _query_bm25(...) → bm25_index.get_index()       ← Sparse 召回
                 ├─ _rrf_fuse(...)                                   ← RRF 融合
                 └─ src/rag/reranker.py · rerank(...)               ← Cross-Encoder 精排
```

**评估路径**（离线 ablation）：

```
python -m tools.rag_eval.runner [--no-rewriter] [--no-rerank] [-o report.md] [-v]
  └─ tools/rag_eval/runner.py · main() → evaluate()
       ├─ [默认] src/rag/query_rewriter.py · expand_queries(query)  ← 可用 --no-rewriter 关
       └─ src/rag/retriever.py · search(query, queries=..., rerank=...)  ← rerank 透传 ablation
            └─ (dense + BM25 → RRF → 阈值 → rerank → dedupe，同生产路径)
       → 指标聚合（hit@1/@3/@k · MRR）
       → 存储 Markdown 报告 + 同名 .log 伴生文件
```

## 1.3. 推荐阅读顺序

**先读"主线"**，掌握"用户 query 进来后到底走了哪几步"：

1. `src/rag/retriever.py · search()` —— 整个 RAG 的"主函数"，看明白它就掌握了 80% 的检索逻辑。重点关注其中的阶段化日志（`[search]` 前缀），它是流程的天然导览。
2. `src/rag/reranker.py · rerank()` —— 短小，看完能理解 sigmoid 归一化与 score 字段语义。
3. `src/rag/query_rewriter.py · expand_queries()` —— 三轴改写如何各自降级、如何合并去重。

**再按需要往下钻**：

- 想优化召回质量 / 阈值 → `retriever.py` 的 dense 阈值过滤与 RRF 段
- 想加新文档格式 → `parser.py` 的 `parse_file()` 与各 `_parse_*` 私有函数
- 想调分块策略 → `splitter.py · split_structured()`
- 想加 / 改指标 → `tools/rag_eval/runner.py · evaluate()` 与 `_render_markdown()`
- 想理解入库幂等性 → `ingest.py · ingest_all()` 的 `content_sha1` 比对逻辑

## 1.4. 常见改动落点

| 需求 | 改动文件 | 关键函数 / 配置 |
|---|---|---|
| 切换 embedding 模型 / 加新 alias | `src/config.py` | `EMBEDDING_MODELS` 字典；ingest 后自动新建 collection |
| 调 RRF / 阈值 / 去重 | `.env` | `RRF_K` / `RAG_DENSE_MIN_SCORE_*` / `RAG_K_PER_SOURCE` |
| 切换 reranker 模型 | `.env` | `RERANKER_MODEL` + `RAG_RERANK_MIN_SCORE`（统一 sigmoid 后仍需按分布微调） |
| 关闭某个改写轴 | `.env` | `RAG_QUERY_REWRITE_ENABLED` / `RAG_HYDE_ENABLED` / `RAG_TRANSLATE_QUERY_ENABLED` |
| Agent / 评估临时关 rerank | 调用方 | `search(..., rerank=False)`，无需改全局 config |
| 加新指标 | `tools/rag_eval/runner.py` | `EvalReport` 字段 + `evaluate()` 累加 + `_render_markdown()` 渲染 |



# 2. Agent 代码

## 2.1. 文件职责速查

| 路径 | 角色 | 主要入口 |
|---|---|---|
| `src/agent/agent_api.py` | `AgentAPI` Protocol：表现层 ↔ Agent core 契约（duck-typed，三种实现都满足，详 [§3.1](#31-agentapi)） | `AgentAPI`（Protocol） |
| `src/agent/agent.py` | Python ReAct Agent 主实现：拼 system → loop（LLM ↔ tool）→ final；含 `SYSTEM_PROMPT` 与模块级共享 store 单例 | `Agent(...).run(user_input)` · `SYSTEM_PROMPT` |
| `src/agent/autogpt_agent.py` | Auto-GPT 风格 Agent：Plan → Execute（子 ReAct）→ Review 三阶段 | `AutoGPTAgent(...).run(user_input)` |
| `src/agent/langchain_agent.py` | LangChain `AgentExecutor` 实现，公共层接口对齐，loop 由 LangChain 接管 | `LangChainAgent(...).run(user_input)` |
| `src/agent/langchain_tools.py` | 把 `tools.py` 的 OpenAI 风格 schema 包装成 LangChain `StructuredTool` | `build_langchain_tools()` |
| `src/agent/tools.py` | 全部业务 tool 定义 + `execute_tool` 路由（RAG / web / fetch / plan / study / quiz / srs / skill / mcp） | `get_tools(skill_bodies)` · `execute_tool(name, args, ...)` |
| `src/agent/core/event_bus.py` | 事件总线：10 类 `AgentEvent` 分发，多订阅扇出 + 异常隔离 | `EventBus` · `EVENT_*` 常量 |
| `src/agent/core/tool_call_engine.py` | 工具调用一轮编排：执行 + 结果格式化 + 写历史 + 叠加发 `plan_*` 事件 | `ToolCallEngine.process(message, messages)` |
| `src/agent/core/history_manager.py` | 历史按轮截断 + skill_pair 完整性保护 + system 拼接 | `HistoryManager.load_truncated()` |
| `src/agent/core/memory_manager.py` | UserMemory 注入 `<user_context>` + 节流自动提取 | `MemoryManager.build_system_prompt()` · `try_extract()` |
| `src/agent/core/thinking_policy.py` | Adaptive Extended Thinking budget 估算（LOW / MED / HIGH 三档） | `ThinkingPolicy.effective_budget(messages)` |
| `src/agent/core/citation_builder.py` | RAG 引用编号管理：跨同轮多次 `search_knowledge` 累计编号 + 末尾 `— sources —` 块渲染（详 [§3.6](#36-引用管理)） | `CitationBuilder.register()` · `render()` |
| `src/agent/core/plan_manager.py` | `PlanState` / `PlanStep` dataclass + 从 messages reconstruct plan 状态（详 [§3.8.1](#381-数据载体)） | `reconstruct_from_messages(messages)` |
| `src/agent/core/srs_scheduler.py` | SM-2 公式纯函数（4 档 → ease / interval / repetitions / lapses，详 [§3.11.2](#3112-sm-2-算法核心)） | `schedule_review(card, rating)` |
| `src/agent/core/harness_manager.py` | Q1 测验批改自检 + R1 RAG 召回过滤；复用 `judge_with_llm`（详 [§3.12](#312-harness-自检)） | `HarnessManager.review_grading()` · `filter_chunks()` |
| `src/agent/core/rules_loader.py` | 把用户 rules 文本拼成 `<user_rules>` block（rules 文本由 Agent 按当前用户从 `user_rules` 读，详 [§3.5](#35-prompt-管理)） | `build_rules_block()` |
| `src/agent/core/mcp_config.py` | MCP servers 配置解析 + UI 编辑路径的 CRUD 辅助 + disabled 列表管理（详 [§3.14.3](#3143-配置文件) / [§3.14.4](#3144-web-ui-管理)） | `load_mcp_config()` · `add_server()` · `update_server()` · `delete_server()` · `rename_server()` · `toggle_server()` |
| `src/agent/core/mcp_manager.py` | MCP server 子进程生命周期 + tool 发现 / 调用（asyncio loop 跑在后台线程） | `MCPManager.start_all()` · `start_one()` · `stop_one()` · `reload()` · `list_tools()` · `call_tool()` |
| `src/agent/core/url_guard.py` | SSRF 防护：私网 / 链路本地 / 保留段 IP 拦截 | `is_url_safe(url)` |
| `src/agent/core/security_filter.py` | Prompt-injection 启发式清洗 + tool 白名单 + `<untrusted_*>` 包装 | `wrap_untrusted()` · `scrub_injection()` · `is_tool_allowed()` |
| `tools/agent_eval/` | Agent 端到端评估（plan / quiz / srs / memory / harness / security / mcp / perf） | 各子目录 `eval_*.py`（详 [§3.13](#313-评估方法)） |
| `tests/test_agent*.py` | 单元 + 集成测试（protocol / events / active_plan / autogpt / langchain） | `pytest tests/test_agent*.py` |

## 2.2. 三条主调用链

**生产路径**（Python ReAct，默认实现 `IMP_METHOD=PYTHON`）：

```
src/agent/agent.py · Agent.run(user_input)
  ├─ HistoryManager.load_truncated()                       ← 截断 + skill_pair 保护
  ├─ MemoryManager.build_system_prompt(base)               ← 拼 <user_context>
  ├─ rules_loader.build_rules_block()                      ← 拼 <user_rules>
  ├─ build_active_study_plan_block(session_id)             ← 拼 <active_study_plan>
  └─ for iteration in range(MAX_HARD_CAP_ROUNDS):
       ├─ ThinkingPolicy.effective_budget(messages)         ← Adaptive 三档预算
       ├─ src/llm/provider.py · call_with_thinking(...)      ← 流式 / 非流式
       └─ if message.tool_calls:
            └─ ToolCallEngine.process(message, messages)
                 ├─ src/agent/tools.py · execute_tool(name, args, ...)
                 │    ├─ _tool_search_knowledge → src/rag/retriever.py · search(...)
                 │    ├─ _tool_make_plan / _tool_update_step / _tool_abort_plan
                 │    ├─ _tool_create_quiz → harness_manager · review_grading(...)
                 │    └─ ... (study / srs / skill / mcp / web / fetch)
                 ├─ security_filter · wrap_untrusted + scrub_injection
                 ├─ EventBus.publish(tool_call_start / tool_call_end)
                 └─ EventBus.publish(plan_created / plan_step_start / plan_step_end)
          else:                                              ← LLM 直接返文本
            ├─ EventBus.publish(final_answer)
            └─ MemoryManager.try_extract(user_input, reply)   ← 节流自动提取
```

**AutoGPT 三阶段路径**（`IMP_METHOD=AUTOGPT`）：

```
src/agent/autogpt_agent.py · AutoGPTAgent.run(user_input)
  ├─ [Plan]    LLM 生成 JSON 任务列表（≤ MAX_PLAN_TASKS）
  ├─ [Execute] for task: 子 ReAct 循环（≤ MAX_TASK_TOOL_ROUNDS）
  │              └─ src/agent/tools.py · execute_tool(...)
  ├─ [Review]  LLM 汇总所有任务结果 → final_answer
  └─ [Persist] 仅写 user + 最终 assistant（不写中间 task / tool message）
```

**评估路径**（离线）：

```
python -m tools.agent_eval.<feature>.eval_*
  └─ tools/agent_eval/<feature>/eval_*.py
       ├─ 真发起 Agent.run(...)（端到端）或直调 core helper（隔离评估）
       ├─ 指标聚合（recall / accuracy / latency / token / cost）
       └─ 落 Markdown 报告 + 同名 .log trace 到 tools/agent_eval/reports/
```

## 2.3. 推荐阅读顺序

**先读"主线"**，掌握"用户问题进来后 Agent 走了哪几步"：

1. `src/agent/agent.py · Agent.run()` —— Agent 的"主函数"，看明白即掌握 80% 推理逻辑。重点看：四层 system 拼接 → `for iteration` 主循环 → `tool_calls` 分支与 `final_answer` 分支的分叉点。
2. `src/agent/core/tool_call_engine.py · ToolCallEngine.process()` —— 工具调用一轮的"小循环"：执行 tool → 包 `<untrusted_*>` → 写历史 → 叠加发 `plan_*` 事件，主循环把每轮 tool_calls 都委托给它。
3. `src/agent/core/event_bus.py · EventBus.publish()` —— 短，看完能理解 10 类事件如何扇出到表现层（CLI / Chainlit）以及异常如何隔离。
4. `src/agent/tools.py · execute_tool()` —— 路由总线：所有业务 tool 在此 dispatch；按需深入某个 `_tool_*` 私有函数。

**再按需要往下钻**：

- 想理解 plan-execute → `tool_call_engine.py · _maybe_publish_plan_events()` + `core/plan_manager.py · reconstruct_from_messages()`
- 想调 thinking budget → `core/thinking_policy.py · effective_budget()`（LOW / MED / HIGH 阈值）
- 想加新业务 tool → `tools.py` 末尾找一个 `_tool_*` 拷贝 + `get_tools()` 加 schema + `execute_tool()` 加 case
- 想换 / 加 Agent 实现 → 对照 `agent_api.py · AgentAPI` Protocol 三件套：`run` / `activate_skill` / `set_event_callback`
- 想理解 RAG 引用编号 → `core/citation_builder.py · register()` 与 `render()`
- 想接 MCP server → `core/mcp_config.py`（配置 schema） + `core/mcp_manager.py`（subprocess + asyncio）
- 想加 prompt-injection 防护 → `core/security_filter.py · scrub_injection()` 的 `_PATTERNS` 表
- 想加 Harness critic 题型 → `core/harness_manager.py` + 对应 prompt 模板文件

## 2.4. 常见改动落点

| 需求 | 改动文件 | 关键函数 / 配置 |
|---|---|---|
| 切换默认 Agent 实现（Python / LangChain / AutoGPT） | `.env` | `IMP_METHOD` |
| 调推理上限 / plan 步预算 | `src/agent/agent.py` | `MAX_TOOL_ROUNDS` / `MAX_TOTAL_ROUNDS` / `MAX_HARD_CAP_ROUNDS` / `_PLAN_ROUNDS_PER_STEP` |
| 改 SYSTEM_PROMPT（必查场景 / 工具策略 / 引用规范） | `src/agent/agent.py` | `SYSTEM_PROMPT` |
| 加新业务 tool | `src/agent/tools.py` | `get_tools()` 加 schema + `execute_tool()` 加 dispatch + 新 `_tool_*` 实现 |
| 改 Extended Thinking 阈值 | `.env` | `THINKING_ENABLED` / `THINKING_BUDGET_MODE` / `THINKING_BUDGET_*` |
| 改用户记忆自动提取节流 | `.env` | `USER_MEMORY_EXTRACT_EVERY_N` / `USER_MEMORY_EXTRACT_MIN_INPUT_LEN` |
| 开 / 关 plan 用户审批 | `.env` | `PLAN_PERMISSION_MODE` |
| 调 SM-2 调度参数 | `src/agent/core/srs_scheduler.py` | `_clip_ease()` / `_update_ease()` / `_interval_from_repetitions()` |
| 加 Harness critic 题型 | `src/agent/core/harness_manager.py` | `review_grading()` / `filter_chunks()` + 对应 prompt 模板 |
| 加 prompt-injection 模板拦截 | `src/agent/core/security_filter.py` | `_PATTERNS` 列表 + `scrub_injection()` |
| 调 SSRF 拦截范围 | `src/agent/core/url_guard.py` | `is_url_safe()` |
| 改 tool 白 / 黑名单（禁用某个 tool） | `.env` | `TOOL_ALLOWLIST` / `TOOL_BLOCKLIST` |
| 接入 MCP server | `.agenta/mcp/config.json` | `servers.<name>`；启动时 `mcp_manager.py · start_all()` 自动接入 |
| 加新事件类型 | `src/agent/core/event_bus.py` | 新 `EVENT_*` 常量 → `ALL_EVENT_TYPES` + 表现层 `_event_router` 加 case |
| 加 Agent 评估 task | `tools/agent_eval/<feature>/` | 新建子目录，参考已有 `plan/` `quiz/` `srs/` 等 feature |


# 3. UI 代码指南

面向「没做过前端、但想看懂本项目前端并能改一些小地方」的读者。读完能定位到某块界面对应哪个文件、看懂代码大致结构、自己动手改字号 / 间距 / 颜色这类样式。

## 3.1. 代码框架

前端是一个 React 单页应用，技术栈一句话：**React + TypeScript 写界面逻辑，Tailwind CSS 写样式，Vite 负责本地开发和打包**。

| 名词 | 一句话解释 |
|---|---|
| React | 把界面拆成一个个「组件」（可复用的界面块）的框架 |
| TypeScript | 带类型标注的 JavaScript，编辑器能提前帮你查错 |
| Tailwind CSS | 用一串短 class 名（如 `text-lg`）直接写样式，不单独写 CSS 文件 |
| Vite | 开发时启动本地服务器、改完代码自动刷新（热更新）；上线时打包 |
| shadcn / lucide-react / sonner | 现成的基础控件库 / 图标库 / 弹窗提示库 |

代码都在 `frontend/src/` 下，按职责分目录：

```mermaid
graph TD
    main["main.tsx<br/>程序入口，挂载到网页"] --> App["App.tsx<br/>整体布局：左侧导航 + 右侧当前页面"]
    App --> auth["components/auth/<br/>登录 / 注册页（未登录时只显示这页）"]
    App --> Sidebar["components/sidebar/<br/>左侧导航栏（底部显示当前用户 + 退出）"]
    App --> Views["右侧各页面（按导航切换）"]

    Views --> chat["components/chat/<br/>聊天页（最核心）"]
    Views --> settings["components/settings/<br/>设置页"]
    Views --> resources["components/resources/<br/>记忆 / 规则 / 技能 / MCP"]
    Views --> business["components/business/<br/>学习计划 / 测验 / 复习"]
    Views --> kb["components/kb/<br/>知识库"]

    subgraph 公共底座
      ui["components/ui/<br/>通用控件：按钮 / 输入框 / 下拉菜单"]
      hooks["hooks/<br/>可复用逻辑：useChat 收发消息等"]
      api["api/client.ts<br/>跟后端通信"]
      types["types/<br/>数据类型定义"]
      lib["lib/<br/>小工具：主题 / 样式合并"]
      css["index.css<br/>主题色变量 + Tailwind 引入"]
    end

    chat -.用到.-> ui
    chat -.用到.-> hooks
    hooks -.调用.-> api
```

目录速记：

| 目录 / 文件 | 放什么 |
|---|---|
| `components/chat/` | 聊天界面的所有块：消息列表、气泡、输入框、思考块、工具调用块 |
| `components/auth/` | 登录 / 注册页；没登录时整个应用只显示这一页 |
| `components/ui/` | 最底层通用控件（按钮、下拉菜单等），别的组件拼装它们 |
| `hooks/` | 抽出来复用的逻辑，函数名以 `use` 开头（核心是 `useChat`，管消息收发） |
| `api/client.ts` | 所有「请求后端」的函数都在这（含登录 / 注册 / 退出） |
| `lib/auth.tsx` | 管「当前登录的是谁」：登录 / 注册 / 退出、是否管理员，都从这取 |
| `types/` | 描述数据长什么样（如一条消息有哪些字段） |
| `lib/` | 零碎工具函数（`cn` 合并样式、主题切换） |
| `index.css` | 全局主题色、字体、圆角等变量 |

## 3.2. 流程图

以「用户发一条消息，看到 AI 流式回答」为例，看数据怎么流动：

```mermaid
sequenceDiagram
    participant U as 用户
    participant C as Composer（输入框）
    participant H as useChat（状态管理）
    participant API as api/client.ts
    participant BE as 后端 /api/chat/stream
    participant B as MessageBubble（气泡）

    U->>C: 输入文字，点发送
    C->>H: 调 send(text)
    H->>API: 发起流式请求（SSE）
    API->>BE: POST /api/chat/stream
    BE-->>API: 不断推送事件<br/>(思考 / 文字 / 工具调用 / plan)
    API-->>H: 每来一个事件，更新 messages
    H-->>B: messages 变了，React 自动重画气泡
    B-->>U: 屏幕上逐字出现回答
```

要点：

- **状态在 `useChat` 里**：`messages`（消息数组）是唯一数据源；它一变，所有用到它的界面自动重画。这是 React 的核心思想——**改数据，不直接改界面**。
- **后端是流式（SSE）**：回答不是一次性返回，而是一段段推过来，所以能看到「逐字蹦」和中间的思考 / 工具调用过程。
- **组件只负责「把数据画出来」**：`MessageBubble` 拿到一条消息，按它的字段决定显示文字、思考块还是工具块（见 §3.1 的 `components/chat/`）。

## 3.3. 语法基础

看懂前端代码只需先掌握这几个概念，够改样式用了。

**1. 组件 = 返回界面的函数**。函数名大写开头，`return` 里那段「像 HTML」的就是界面：

```tsx
function Hello() {
  return <div className="text-lg">你好</div>
}
```

**2. JSX = 在 JS 里写界面标签**。标签里用 `{}` 插入变量或表达式：

```tsx
const name = '小明'
return <div>你好，{name}</div>   // 显示：你好，小明
```

**3. props = 父组件传给子组件的参数**（就是函数入参）：

```tsx
function Badge({ text }: { text: string }) {
  return <span>{text}</span>
}
// 用：<Badge text="free" />
```

**4. state = 组件自己的可变数据**，用 `useState`，变了界面自动重画：

```tsx
const [count, setCount] = useState(0)   // count 当前值；setCount 改它
```

**5. className + Tailwind = 用短 class 名控制样式**（最常改的就是这里）：

```tsx
<button className="px-2 text-sm text-muted-foreground">按钮</button>
//                  左右内边距  字号小   文字用「次要」色
```

**6. 条件 / 列表渲染**（代码里到处是这两种写法）：

| 写法 | 含义 |
|---|---|
| `{ok && <X/>}` | `ok` 为真才显示 `X` |
| `{ok ? <A/> : <B/>}` | 真显示 `A`，假显示 `B` |
| `{list.map((x) => <X key={x.id} .../>)}` | 把数组每一项渲染成一个组件 |

**7. TypeScript 类型**：冒号后面是类型标注（`text: string` 表示 text 是字符串），只是给编辑器查错用，不影响运行逻辑。看不懂类型时可先跳过，专注 `return` 里的界面部分。

## 3.4. 页面调整指南

改字号 / 间距 / 颜色 / 对齐这类样式，**只改 `className` 里的 Tailwind 工具类即可**，不用碰逻辑。步骤：

1. **定位文件**：按下表从「界面区域」找到对应组件文件。
2. **找到那段 JSX**：在文件里搜界面上的文字或附近元素。
3. **改 `className`**：替换里面的工具类（见速查表）。
4. **存盘看效果**：开发服务器（`npm run dev`）会热更新，浏览器自动刷新，不用重启。

界面区域 → 文件对照：

| 界面区域 | 文件 |
|---|---|
| 登录 / 注册页（含左上 logo + 标题） | `components/auth/LoginView.tsx` |
| 左侧导航栏（底部当前用户名 / 退出按钮；管理员才显示技能 / MCP / 设置入口） | `components/sidebar/Sidebar.tsx` |
| 聊天输入框 / 模型选择 / 工具条 | `components/chat/Composer.tsx` |
| 消息气泡（用户 / AI、附件卡片、操作按钮） | `components/chat/MessageBubble.tsx` |
| 工具调用块（如 `update_step`） | `components/chat/ToolBlock.tsx` |
| 思考过程块 | `components/chat/ThinkingBlock.tsx` |
| 学习计划块 | `components/chat/PlanBlock.tsx` |
| 设置页 | `components/settings/SettingsView.tsx` |
| 记忆 / 规则 / 技能 / MCP 页 | `components/resources/` 下对应文件 |
| 全局主题色 / 字体 / 圆角 | `index.css` |

常用 Tailwind 工具类速查：

| 想改 | 类名示例 | 说明 |
|---|---|---|
| 字号 | `text-xs` `text-sm` `text-base` `text-lg` `text-xl` | 从小到大 |
| 字重 | `font-normal` `font-medium` `font-bold` | 常规 / 中等 / 加粗 |
| 文字颜色 | `text-foreground` `text-muted-foreground` `text-green-600` | 主色 / 次要色 / 具体色 |
| 背景色 | `bg-background` `bg-muted` `bg-primary` | 用主题变量，自动适配深浅色 |
| 内边距 | `p-2`（四周）`px-2`（左右）`py-1`（上下） | 数字越大越宽 |
| 外边距 | `m-2` `mt-1` `mb-2` | 同上，t/b/l/r 指方向 |
| 元素间距 | `gap-2` | 配合 `flex` 用，控制子元素间隔 |
| 宽 / 高 | `w-8` `h-8` `w-full` `max-w-3xl` | 固定值 / 占满 / 最大宽度 |
| 横向排列 | `flex items-center justify-between` | 一行排列、垂直居中、两端对齐 |
| 圆角 | `rounded-md` `rounded-full` | 中等圆角 / 全圆 |

实战例子（就是上一轮改过的）：把工具条上当前模型名的字号从大调小，在 `Composer.tsx` 找到模型选择按钮，把 `text-lg` 改成 `text-sm` 即可：

```tsx
// 改前
className="... px-2 text-lg text-muted-foreground ..."
// 改后（字号变小）
className="... px-2 text-sm text-muted-foreground ..."
```

小贴士：

- **颜色优先用主题变量**（`text-muted-foreground` / `bg-muted` 等），它们在深色 / 浅色模式下会自动切换；直接写 `text-gray-500` 这种会在另一种模式下不协调。
- 一个 `className` 里可以堆很多类，**顺序不影响效果**，按「布局 → 间距 → 字体 → 颜色」分组写更好读。
- 改坏了不要慌，Tailwind 类是纯样式，删掉多写的类就回到原样，不会影响功能。