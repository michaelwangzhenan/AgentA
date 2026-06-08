# 1. 多用户并发

## 1.1. 现状
多用户同时聊天时，agent只有1个，要互相等

## 1.2. 目标
多用户同时聊天时，agent可以同时处理多个用户请求, 用户不感知延迟

## 1.3. 方案

根因：Agent 是进程级单例（`get_agent` 用 `lru_cache(maxsize=1)`），把**每请求才变**的状态挂在了实例上：
1. `self.session_id`：路由处理时 `agent.session_id = req.session_id` 直接改实例。
2. `self.events`（EventBus）：`set_event_callback` 对实例做 clear + subscribe。

两个用户同时进 `agent.run`，会互相覆盖 `session_id`、把事件推给别人。现在靠 `_AGENT_LOCK`（全局锁）串行化所有请求来回避，代价就是"互相等"。

可利用的现状（无需改）：
- `run()` 内部本就**每次新建** `HistoryManager` / `MemoryManager` / `ToolCallEngine` / `CitationBuilder`，不共享。
- 用户 / 模型偏好已用 `contextvars`（`use_user` / `use_llm_prefs`）按请求隔离，流式在 executor 线程入口已重设。
- 底层 store（`chat_history` / `user_memory` / `quiz` / `srs`）均 `check_same_thread=False` + 自带 `threading.Lock`，并发安全。

方向（具体留单独 task）：
- 搬走 per-request 状态：`session_id` 与事件回调改为 `run()` 入参（或一个 RunContext），EventBus 每次 run 局部新建，不再写实例字段；`last_usage` 等读写态一并改为返回值。
- 去掉 `_AGENT_LOCK`：单例只保留不可变配置（`system_prompt` / `skills` / `tools`），只读并发共享。
- 并发模型不变：FastAPI 同步路由 + threadpool / `run_in_executor`（LLM 调用是网络 IO，GIL 不挡）。
- 限流：加信号量限制同时在跑的 `run` 数，避免并发把 LLM 配额 / CPU（含 `search_knowledge` 精排）打满。
- 验证：并发发多个不同 session 的请求，断言事件不串台、`session_id` 不互窜。

## 1.4. 实现步骤

核心思路：把挂在单例上的 per-request 状态（`session_id` / 事件 bus / usage）降级为 `run()` 调用内的**局部量**，单例只留只读配置。改动做成**纯增量**——`run()` 新增可选 kwargs，旧调用不传则回落到实例字段，CLI / 测试零改动。

调用面盘点（改动前先确认这些是全部入口）：

| 调用方 | 现在怎么用单例可变状态 | 是否共享单例 |
|---|---|---|
| `/chat`（非流式） | 写 `agent.session_id`、`run()`、回读 `agent.session_id` | 是（`get_agent` 单例） |
| `/chat/stream` | 写 `agent.session_id`、`set_session_id`、`set_event_callback`、`run()` | 是 |
| CLI `handlers.py` | `set_event_callback`、`agent.approval_callback`、`run()`、读 `agent.last_usage` | 否（`make_agent` 自建） |
| `AgentAPI` Protocol | 约定 `session_id` / `last_usage` / `events` / `run` / `set_event_callback` | — |

步骤：

1. **`run()` 加可选入参（向后兼容的地基）**
   签名改为 `run(user_input, *, session_id=None, event_callback=None)`。两者都不传 = 完全保持现有行为（用 `self.session_id` / `self.events`）；传了则走下面的局部路径。可选参数不破坏 `AgentAPI` 的 `runtime_checkable` 校验，CLI / 既有 UT 不用改。

2. **`run()` 内去掉对 `self.session_id` 的依赖**
   开头取 `sid = session_id or self.session_id`，函数体内所有 `self.session_id`（传给 `HistoryManager` / `MemoryManager` / `ToolCallEngine`、`chat_history.append`、各 event payload）一律换成局部 `sid`。

3. **per-run 事件 bus**
   把 `set_event_callback` 里"清空+按 `ALL_EVENT_TYPES` 注册 wrapper"的逻辑抽成 helper `_bind_callback(bus, cb)`。`run()` 内确定本次用的 bus：传了 `event_callback` → 新建局部 `EventBus` 并 `_bind_callback`；没传 → 用 `self.events`（CLI 老路径）。`_on_thinking_chunk` / `_on_token_chunk` / `_token_callback_for_provider` 与 `ToolCallEngine(events=...)` 统一改用本次选定的 bus。这样并发的两个 `run` 各自一个 bus，事件不串台。

4. **usage 不再以实例字段为权威**
   `run()` 用局部累计 token，经 `final_answer` 事件 payload + 返回值带出（API 本来就从事件 payload 取，不读 `agent.last_usage`）。仅当**未传** per-request 上下文时（CLI）才回写 `self.last_usage`，保持 `_print_token_usage` 可用；API 路径不写不读它，无竞争。

5. **改 API 路由 + 去锁**
   `/chat`：`sid = req.session_id or str(uuid4())`（调用方自己生成，不再回读 `agent.session_id`），`reply = agent.run(req.message, session_id=sid)`，返回 `sid`。`/chat/stream`：同样自算 `sid`，`set_session_id(sid)` 后 `agent.run(req.message, session_id=sid, event_callback=_on_event)`，删掉 `set_event_callback(...)` 前后两处调用。两个路由删除 `_AGENT_LOCK`（含其注释）。`use_user` / `use_llm_prefs` / executor 线程内 `set_session_id` 保持不动（都是 `ContextVar`，已按请求隔离）。
   附带修正：当前 `req.session_id` 为空时会回读到单例构造期的 uuid（多个新会话撞同一 id）；改为调用方生成新 uuid 后该隐患一并消除。

6. **并发限流**
   加配置项 `MAX_CONCURRENT_AGENT_RUNS`（信号量限制同时在跑的 `run` 数），保护 LLM 配额 / CPU（含 `search_knowledge` 精排）。按公约新增 config 项三处同步：`src/config.py` + `.env.example` + `.env`。

7. **三种 Agent 实现 + Protocol 对齐**
   `LangChainAgent` / `AutoGPTAgent` 的 `run()` 同步加相同可选 kwargs（否则切到这两种实现时多用户仍串台）；`agent_api.py` 的 `run` 签名加可选参数。`tests/test_agent_protocol.py` 的 `isinstance` 断言仍通过。

8. **测试**
   既有 UT 全绿（CLI 老路径行为不变即验证向后兼容）。新增并发 UT：mock LLM，多线程并发 `run` 不同 `session_id` + 各自 `event_callback`，断言事件不串台、各 session 的 `chat_history.append` 落对、usage 不互窜。

不破坏现有功能的保证：

- CLI 不传新 kwargs → 走 `self.session_id` / `self.events` / `self.last_usage` 老逻辑，行为零变化；且 CLI 用 `make_agent` 自建实例，本就不碰 API 单例。
- 底层 store（`chat_history` / `user_memory` / `quiz` / `srs`）已是 `check_same_thread=False` + 自带 `Lock`，并发 `append` 安全；DB 写很快，非瓶颈。
- `get_agent` 的 `lru_cache` + skills `reload`（`cache_clear`）在去锁后仍安全：in-flight 的 `run` 持有旧实例引用照常跑完，新请求拿新实例，共享 store 不被替换。

需单独留意（本期 API 不受影响，标注后续）：

- `approval_callback`（plan 审批）仍是实例字段，CLI 单线程没问题；API 当前不设它（默认放行）。将来 API 要支持 plan 审批，需同样改成 per-run 传入。
- `on_thinking_chunk` 构造参数订阅在 `self.events` 上；它只服务 CLI 单实例路径，API 走 per-run bus 不依赖它，二者不重叠。

# 2. search_knowledge 召回慢

## 2.1. 现状

`search_knowledge` 一次约 100 秒，慢得离谱。

数据口径：取一次真实调用（query="RAG 技术趋势 市场前景 应用场景"）的逐段时间戳。采样时有两个会话并发、CPU 与 LLM 有争用，绝对值偏高；单用户会更快，但各段相对大小成立。

各段耗时（从大到小）：

| 段 | 耗时 | 性质 | 说明 |
|---|---|---|---|
| query 改写（多条改写 + 翻译） | ~37s | LLM 调用 | 检索前先让 LLM 生成改写，串行挡住后面所有步骤 |
| dense + BM25 检索（3 条 query） | ~23s | CPU 编码 | 慢在 `bge-m3`（1024 维）对 3 条 query 逐条 CPU 编码；向量 / BM25 查询本身很快 |
| Cross-Encoder 精排（24 候选） | ~21s | CPU 推理 | `bge-reranker-base` 对 `RERANKER_RECALL_MULTIPLIER(3)×top_k(8)≈24` 个候选逐对打分 |
| RAG critic（harness 采点） | ~15s | LLM 调用 | `HARNESS_RAG_ENABLED` 默认开，每次都超时后放行原始结果，对质量零增益、纯浪费 |
| reranker 模型加载 | ~2s | 一次性 | 仅首次检索触发，之后命中缓存 |

纠正一个常见误判：最大头不是精排，而是 query 改写（LLM）和 CPU 编码；"向量检索很快"只指查到候选这一步，但其中的 embedding 编码并不快。

## 2.2. 优化方向（按性价比，先做省得多又改得少的）

| 编号 | 优先级 | 项 | 做法 | 状态 |
|---|---|---|---|---|
| 1 | P0 | 关 RAG critic | `HARNESS_RAG_ENABLED` 默认改为关（每次都超时放行、零增益）；想用的人 UI 可开 | ✅ 已做 |
| 2 | P0 | query 改写按需触发 | 新增 `RAG_REWRITE_MIN_QUERY_LEN`（默认 6）：query 太短（多为精确术语）时跳过 multi-query / HyDE 改写 | ✅ 已做 |
| 3 | P1 | 降精排候选数 | `RERANKER_RECALL_MULTIPLIER` 默认保持 2（更快）。配 2 原会触发既有 bug 召回归零（精排「候选≤top_k×2 跳过打分」后，min_score 仍按 RRF 小分过滤把结果全删）。已治本：精排门槛改为候选≥2 即打分；精排阈值只对真正精排过的 hit 生效（`Hit.reranked` 标记），未精排的放行 | ✅ 已做 |
| 4 | P1 | embedding 提速 | query 编码按 (模型, 文本) 做进程级 LRU 缓存，重复 / 改写命中相同文本零编码 | ✅ 已做 |
| 5 | P2 | 感知延迟 | 新增 `tool_progress` 事件，检索工具运行中在 UI 显示「改写中 / 检索中 / 校验中」 | ✅ 已做 |
| 6 | P2 | 釜底抽薪（不做） | embedding + rerank 移到 GPU 机或托管 API（provider 抽象已具备条件） | — |

涉及配置项均已在「系统配置 → RAG」UI 暴露，可不重启动态调。换更快小模型 / ONNX 量化 / GPU 留待后续。


# 3. log 分析（RAG 召回以外的性能问题）

范围：RAG 召回慢见第 2 节，这里只看其余的耗时点。从 `logs/uvicorn.log` 看，除 RAG 外还有这些：

| 优先级 | 瓶颈 | 现象（实测） | 建议 |
|---|---|---|---|
| P0 | agent 工具循环串行 | 一次问答里每轮 = 1 次 LLM + 工具执行，逐轮串行累加。web 类问答实测 ~40~75 秒（de4ad2af 7 轮 ~43s；676b 5 轮 ~75s） | 用 prompt 引导减少轮数（一轮多查、别反复试）；轮次上限按场景调（已可配 / UI 可改） |
| P0 | 同一轮多个工具调用仍串行 | 一轮要抓多个 URL 时逐个 `fetch` 串行（每个几秒） | 同一轮的多个工具调用并行执行（`web_search` + 多个 `fetch` 可并发） |
| P1 | 开 thinking 时单轮变慢 | 单轮推理思考 ~28~32 秒才出内容（676b 第 6 轮 15:37:50→15:38:22） | thinking 默认关 / 按场景调档；简单问答不开 |
| P1 | 空内容重试多花一轮 | 模型把答案塞进思考、正文留空时，会去 tools 重试一轮（多一次完整 LLM 调用） | 鲁棒性换来的延迟，可接受；注意别让它频繁触发（属模型问题） |
| P2 | 开发态热重载 + MCP 重连 | 改一次文件 uvicorn 反复重启，每次重连 MCP server ~6 秒（16:08~16:10 多次 reload） | 仅开发态影响；部署用 `--no-reload` 即可消除 |

方法论：同 RAG，先在 agent 循环与工具执行处埋点量耗时，确认大头（大概率是工具循环串行）再动手。
