# 1. Review

## 1.1. Overview
项目背景信息可参考 /docs/iter_XXX.md. 注意：这是历史实现信息，仅作参考，不是当前步骤的标准!

Review 内容：
- Code: 先把现有实现弄扎实
- AgentA 定位再思考：已经实现了不少内容，如何让AgentA更强大
- 本期不包括：文档更新

Review 方式讨论:
- 逐模块：逐模块进行(要先划分模块)
- 前端/后端：前端和后端分别进行
- 整体：整体进行代码审查

## 1.2. Code Review

### 1.2.1. 审查维度
1. 功能：确保功能的正确性，同时考虑可维护性，可扩展性
2. 性能：只针对代码本身，不考虑电脑性能问题
3. Clean Code：按行业标准进行评估和优化
4. 注释：
   - 目的：本工程代码主要AI辅助编写的，增加注释让人类更易理解
   - 注释主要解释业务逻辑，而不是代码本身
   - 让用户看到代码知道这是在干什么，为什么这么写（不要硬套这两个规则，理解这是写注释的思路）
   - 注释写法符合行业标准
   - 删除临时性描述，如：这是 iter_x 加的功能
   - 优化大白话描述，描述要用工程专业口吻，而不是日常聊天语气
5. 架构与依赖方向：Agent core（`src/agent/core/`）不应反向依赖表现层（`src/cli/` / `src/api/`）。iter_2 §4.4 曾登记过几处反向 import（agent 直接 import `src.cli.skill_loader` 等），复查现在是否已消除。
6. 测试覆盖：多用户隔离、级联删除、触发节流这类"行为约束"是否有独立测试锁住（对照 iter_6 §5）。
7. 错误处理与降级：LLM / 检索 / MCP 调用失败时是否有兜底，有没有静默吞掉异常。
8. 配置同步：新老 config 项在 `config.py` / `.env.example` / `.env` 三处是否一致（工程公约 §2.4）。
9. 安全与多用户隔离：业务路由是否都按 `user_id` 过滤、有没有漏网；prompt injection 过滤现状是否仍生效。

> 范围说明：`langchain_agent.py` / `autogpt_agent.py`（及其专用的 `langchain_provider.py` / `langchain_tools.py` / `langchain_history.py`）目前只是初始框架、未正式开发。本期审查**只看主实现 `agent.py` 这条线**，这两个实现的当期问题（含彼此一致性）一律不计入，留待 iter_a / iter_b 解决。

### 1.2.2. 审查方式与模块划分
后端 helper 抽离得比较干净，适合"逐模块"逐个过；前端是另一套技术栈，单独成块。建议先做一遍**整体的依赖方向**快速扫描（对应上面维度 5），再钻进各模块细看。划分如下：

| 模块 | 路径 | 职责 |
|---|---|---|
| LLM 调用层 | `src/llm/` | provider 出口、模型选择、流式 |
| RAG 检索 | `src/rag/` | 解析 / 切分 / 召回 / rerank / BM25 |
| 数据存储 | `src/memory/` | 各 `*Store`（chat / memory / plan / quiz / srs / user） |
| Agent 主循环 + helper | `src/agent/`、`src/agent/core/` | 主实现 `agent.py` + manager / engine / policy / bus（langchain / autogpt 框架本期不看） |
| HTTP 层 | `src/api/` | routes / schemas / deps / 认证门禁 |
| CLI | `src/cli/` | 命令处理、补全、skill 加载 |
| 前端 | `frontend/` | React 页面与交互（单独成块） |

**已定方式**：先做一遍整体扫描（依赖方向，对应维度 5），再按上表逐模块细看，前端单独成块。本期只做 Code Review，定位再思考（§2）留到下一步。

## 1.3. 审查发现

逐模块记录，先全部过完再统一修。优先级：P0 = 正确性 / 安全问题；P1 = 架构 / 可维护性；P2 = Clean Code / 注释。

> 范围：本期只审主实现 `agent.py` 线；`langchain_agent.py` / `autogpt_agent.py` 及其专用 provider / tools / history 是初始框架，当期问题留待 iter_a / iter_b。

### 1.3.1. 整体扫描（依赖方向）

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| G1 | P1 ✅已修复 | `agent.py` import `src.cli.skill_loader` | Agent core 反向依赖表现层 `src/cli/`（iter_2 §4.4 #5 登记，未消除） | `skill_loader.py` 经 `git mv` 迁至新建 `src/skills/`，agent / api / cli / main / eval 全部改引 `src.skills.skill_loader`。复查：`src/agent` 下已无任何 `src.cli` import |
| G2 | P1 ✅已修复 | `chat_history` / `user_memory` / `learning_plan_store` / `quiz_store` / `srs_store` import `src.agent.core.user_context` | 依赖层（`*Store`）反向感知 helper 层，方向倒置 | 先迁出 `agent.core`：`user_context.py` 独立为 `src/core/`（当时最底层共享原语），各 store + `chat.py` + `agent` 改引 `src.core.user_context`。复查：`src/memory` 下已无 `src.agent.core.user_context` import（仅余 G3 的 `security_filter`，属 P2）。**后续**：再并入 `src/memory/user_context.py` 并删除 `src/core/`，消「双 core」歧义且与按用户隔离的持久化同包。 |
| G3 | P2 | `user_memory.py:42` import `security_filter._INJECTION_PATTERNS` | 跨模块 import 私有符号（下划线开头） | `security_filter` 暴露公开接口（如 `contains_injection()`），调用方不碰私有变量 |

### 1.3.2. src/llm

主出口 `provider.py` 整体扎实（function name sanitize/restore、流式 usage、claude/openai 双分支、thinking 分发都处理得细）。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| L1 | P2 ✅已修复 | `provider.py` `_chat_claude`（非 thinking） | 不走 `_sanitize_messages_for_llm`，历史里空名 / 非法 tool_call 在 anthropic 分支不会被清理（openai 分支会） | 把 `_sanitize_messages_for_llm` 提到 provider 分发之前，anthropic / openai 两条分支都先清理空名 tool_call；openai 额外做 tools name sanitize + 调用后还原，claude tool 名按原样透传无需还原 |

> `langchain_provider.py` 的若干问题（未用 `force_temperature`、`httpx.Client` 无人关闭、缺 docstring 等）属 langchain 实现线，本期不计入，留待 iter_a。

### 1.3.3. src/rag

主流程（`retriever` / `ingest` / `bm25_index` / `splitter` / `reranker`）实现细致、降级处理到位。核心问题是 BM25 缓存陈旧。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| R1 | P1 ✅已修复 | `bm25_index.py` `reload_index` | 该函数全仓无人调用（死代码）。后果：`ingest_one`（Web 上传）/ `delete_kb_document` 写盘后，`retriever` 的 `get_index` 进程级缓存不刷新，长驻服务里上传/删除文档后 BM25 召回仍是旧索引（dense 每次新建 PersistentClient 读盘，不受影响） | 根因解法：`ingest_one` / `delete_kb_document` 改用 retriever 同一个 `get_index` 共享实例（写完即对检索可见，不存在双实例陈旧）；`delete_all_kb_documents` 删 pkl 后调新增的 `drop_index` 清进程缓存。死函数 `reload_index` 删除，由 `drop_index` 取代 |
| R2 | P2 ✅已修复 | `ingest.py` `chunk_text` | 标注「向后兼容」，仅老调用方 / 测试用 | 确认生产已无调用（`split_structured` 全面取代），删函数 + `test_rag.py` 的 `TestChunkText` 测试类，并清掉 `splitter.py` 注释里对它的引用 |
| R3 | P2 ✅已修复 | `ingest.py` `_open_collection` | 每次新建 `SentenceTransformerEmbeddingFunction`，未复用 `retriever._get_embedding_fn` 缓存；`ingest_one` 逐次上传有模型加载开销 | 改调 `retriever._get_embedding_fn`（进程级缓存，函数内懒导入避免循环依赖），ingest 与检索端共用同一实例；删掉 `ingest.py` 不再使用的 `SentenceTransformerEmbeddingFunction` import |
| R4 | P2 ✅已修复 | `retriever.py` `search` | 每次调用新建 `chromadb.PersistentClient`，未缓存 | 加 `_get_chroma_client()` 进程级缓存（双检锁），`search` 复用 |
| R5 | P2 ✅已修复 | `retriever.py` `_query_prefix_for` | 用长 OR 列表枚举"不需要前缀"的模型，可读性差 | 改为 `any(marker in name ...)` 子串成员判定（`bge-m3` / `v1.5` 两个标记覆盖原列表） |
| R6 | P2 ✅已修复 | `bm25_index.py` | 末尾 `_ = Any` 仅为消除 unused-import 警告 | 删掉 `_ = Any` 与未使用的 `from typing import Any` |

### 1.3.4. src/memory

`user_store`（密码 pbkdf2 + salt + 常量时间比较、大小写不敏感、级联删除）与各业务 store 的 `user_id` 过滤都做得对。最大问题是线程安全不一致。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| M1 | P1 ✅已修复 | `chat_history` / `srs_store` / `quiz_store` / `learning_plan_store` | 这 4 个 store 用 `check_same_thread=False` 共享单连接，但**没有** `threading.Lock`；而 `user_store` / `user_memory` 有。Web 服务在线程池里并发处理请求、共享进程级 store 单例时，并发写同一 sqlite 连接可能报 `database is locked` 或游标错乱 | 4 个 store 全部补 `threading.Lock`，读 / 写 / 建表一律经锁串行（与 `user_store` 一致）。`threading.Lock` 非重入，已逐一核对"持锁时调另一个加锁方法"的场景，全部把内部调用挪到锁外规避死锁 |
| M2 | P2 ✅已修复 | `chat_history` messages 级方法 | `load` / `clear` / `delete_session` 等只按 `session_id` 操作，不校验归属，全靠 API 层先调 `owns_session`。§1.3.5 已确认 `sessions.py` 当前全覆盖，无现存漏洞；但新增路由漏调即跨用户泄露 | 加锁内 `_owns_unlocked` 复用，给 `load` / `load_last_n_messages` / `clear` / `delete_session` / `truncate_from_user_message` / `rename_session` 都补 `user_id`（默认 `current_user_id()`）兜底：非本人 session 读返回空、写 no-op。默认 / CLI 路径行为不变 |
| M3 | P2 ⏸本期不动 | `user_store.set_settings` | 用 `COALESCE`，传 `None` 保持原值，因此设过的偏好无法再清回「用全局默认」 | 用户决定本期不做（重置偏好属新增小功能，需求不迫切） |
| M4 | P2 ✅已修复 | `user_store` 登录态 | 过期 session 仅在被访问时惰性删除，长期登录的废 token 会堆积 | `create_session` 每次登录顺带 `DELETE ... WHERE expires_at < now`，并暴露 `purge_expired_sessions()` 供定时清理显式调用 |

### 1.3.5. src/agent + core

`agent.py`（ReAct 主循环、plan 自适应轮次上限、引用渲染、错误事件）、`tool_call_engine`、`security_filter`、`tools.py` 实现成熟。**安全层确认无空挂**：`get_tools` 用 `is_tool_allowed` 过滤、`execute_tool` 入口再次 double-check、`fetch_url` 走 `url_guard.is_url_safe`、web / MCP / RAG 外部数据统一 `scrub_injection` + `wrap_untrusted`。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| A1 | P2 ✅已修复 | `agent.py:58` | `__import__("threading").Lock()` 用 import 黑魔法 | 顶部加 `import threading`，两处锁正常引用 |
| A2 | P2 ✅已修复 | `agent.py:64` `_get_shared_chat_history` | 懒加载无锁，并发首次初始化可能建两个实例（与 `_get_shared_user_memory` 的双检锁不一致） | 加 `_chat_history_lock` 双检锁，与 `_get_shared_user_memory` 对齐 |
| A3 | P1 ✅已修复 | 见 §1.3.3 M1 + `deps.py` 自述 | store 并发模型不统一（shared singleton vs 独立 connection 两套并存，部分无锁） | 随 M1 落地：所有 store 现在都有锁，两套实例化策略下都线程安全，"部分无锁"的并发风险已消除。（`deps.py` 仍并存两种实例化路径，但不再有正确性风险；是否进一步收敛为单一策略留 P2 评估） |

> 说明：core 下其余 helper（`history_manager` / `plan_manager` / `srs_scheduler` / `harness_manager` / `mcp_manager` / `citation_builder` / `event_bus` / `thinking_policy` / `url_guard`）做了结构与关键路径抽查，未见 P0/P1；细节问题并入 §1.3.7 横切。

### 1.3.6. 横切问题（跨模块）

| 编号 | 优先级 | 范围 | 问题 | 建议 |
|---|---|---|---|---|
| X1 | P2 ✅已修复 | 主实现线约 25 个源文件 | 注释 / docstring 里残留 `Phase x` / `Step x` / `iter_x` / `§4.9.x` / `D5` 等代号与时效标记 | 删时效标记；保留设计依据的改为自洽大白话表述，不再引外部文档章节号。`tools.py` 三处 LLM / 用户可见字符串里的 `（Phase 2.5）` / `（D13）` / `（D4 ...）` 也一并清理。`langchain_agent.py` / `autogpt_agent.py` 按既定范围不动，留待 iter_a / iter_b |

### 1.3.7. src/api

`deps.py` 依赖注入清晰；`sessions.py` 每个端点都做归属校验（`owns_session` / `get_session_owner`，404 不泄露存在性）；`chat.py` 用全局 `_AGENT_LOCK` 串行化共享 agent 单例、`use_user` / `use_llm_prefs` 在 executor 线程内设置并复位——并发安全到位。所有路由文件都挂了 `get_current_user` / `require_admin`。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| API1 | P2 ⏸本期不动 | `chat.py:40` `_AGENT_LOCK` | 进程级单例 Agent + 全局锁 → 所有 chat 请求全局串行，多用户无法并发对话（已在注释中标为已知取舍） | 用户决定本期不动：个人项目可接受；若要多用户并发，需让 Agent 实例可按请求构造（去单例可变状态） |
| API2 | P2 ⏸本期不动 | `deps.py:1-14` 自述 | store 共享两套策略（shared singleton vs 独立 connection）并存，属技术债 | 并发正确性已随 M1 解决（见 §1.3.4 A3）；两套策略是否收敛为单一，用户决定本期不动 |

### 1.3.8. src/cli

`skill_loader`（扫描 / 解析 / CRUD / 原子写 disabled.json / 孤儿自愈 / catalog 渲染）实现完善，`html.escape` 防 prompt 注入到位——本期已随 C1 迁出到 `src/skills/`。命令处理（`handlers` / `ui` / `tab_complete`）按 dev/headless 定位组织，未见 P0/P1。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| C1 | P1 ✅已修复 | `skill_loader.py` 位置 | 该模块同时被 Agent core（`agent.py` 导 `SkillInfo` / `build_skill_catalog`）与 API（`skills.py` 用 CRUD）使用，却放在 `src/cli/` —— 命名与归属错位（即 §1.3.0 G1 的落点） | 随 G1 一并迁到新建 `src/skills/skill_loader.py`，cli / agent / api 都向它依赖 |

### 1.3.9. frontend

`api/client.ts` 结构清晰（统一 `_ensureOk` + 全局 401 跳登录回调 + SSE 主动 abort 不重试处理）。重点审查了 API client 层；各 React 组件做了结构性抽查，未见 P0/P1。

| 编号 | 优先级 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| F1 | P2 ✅已修复 | `client.ts` 全部 `fetch` / `fetchEventSource` | 未显式设 `credentials: 'include'`，靠同源默认带 cookie。与 iter_6 §3.6 设计描述不符；一旦前后端跨域部署会丢登录态 | 加 `apiFetch` 包装统一带 `credentials: 'include'`，66 处 `fetch` 改走它；`fetchEventSource` 也补 `credentials` |
| F2 | P2 ✅已修复 | `client.ts` `postChat` | 自带一套错误解析（前缀 `HTTP 401:`），与 `_ensureOk` 风格不一致（`postChat` 是非流式 fallback） | 改走统一的 `_ensureOk` |

### 1.3.10. 汇总与修复优先级

未发现 P0。整体结论：**功能正确、安全层（多用户隔离 / tool 名单门 / SSRF / injection 清洗）接通无空挂**；问题集中在架构耦合、并发一致性、注释清理三类。

P1（本期已全部修完 ✅）：

| 编号 | 一句话 | 状态 |
|---|---|---|
| G1 / C1 | `skill_loader` 从 `src/cli/` 搬到新建 `src/skills/`，解除 core→表现层反向依赖 | ✅ 已修复（一处改动同时解 G1、C1） |
| G2 | `user_context` 先下沉到 `src/core/` 解除 `*Store`→`agent.core` 反转，后并入 `src/memory/user_context.py` 并删 `src/core/` | ✅ 已修复 |
| R1 | BM25 缓存陈旧：ingest/delete 改用与 retriever 同一共享实例，删死函数 `reload_index`（换 `drop_index`） | ✅ 已修复 |
| M1 / A3 / API2 | 4 个无锁 store 补 `threading.Lock`，并发正确性统一（API2 的策略收敛留 P2） | ✅ 已修复 |

验证：迁移与加锁后跑全量 fast UT，`1341 passed, 3 skipped`；`src/agent` 已无 `src.cli` import、`src/memory` 已无 `src.agent.core.user_context` import。

P2 进度：
- ✅ 第一批（无行为变更、低风险）：L1（claude 分支也清空名 tool_call）、R4（chroma client 缓存）、R5（前缀判定改集合成员）、R6（删死 import）、A1（去 `__import__` 黑魔法）、A2（chat_history 懒加载补双检锁）、F1（`apiFetch` 统一带 cookie）、F2（postChat 走 `_ensureOk`）。
- ✅ 第二批（用户拍板后做）：R2（删 `chunk_text` 死函数 + 对应测试类）、R3（`_open_collection` 复用 retriever 的 embedding function 缓存）、M4（登录时主动清过期 session + 暴露 `purge_expired_sessions`）、M2（chat_history 会话级方法补 `user_id` 纵深防御，非本人 session 读空 / 写 no-op）、X1（约 25 个源文件注释时效标记清理）。
- ⏸ 用户决定本期不动：M3（偏好重置路径）、API1/API2（Agent 去单例 / store 策略收敛 —— 个人项目当前可接受）。
- 验证：fast UT `1335 passed`（R2 移除 6 个 `chunk_text` 测试），前端 `tsc --noEmit` 通过。

实现要点（已落地）：
- G1/C1：`skill_loader.py` → 新建 `src/skills/`（独立一层），`cli` / `agent` / `api` / `main` / eval 脚本都向它依赖。
- G2：`user_context.py` 先 → `src/core/`（当时最底层共享原语），再 → `src/memory/user_context.py`（与按用户隔离的持久化同包，消「双 core」），store 与 agent 均向下依赖。
- R1：根因解法——让 ingest 与 retriever 共用 `get_index` 单实例（不再各 `load_or_new` 造成双实例），`delete_all` 用 `drop_index` 清缓存。
- M1：4 个 store 补 `threading.Lock`，与 `user_store` 对齐；非重入死锁逐一核对规避。

# 2. 定位再思考

本节盘点接下来值得做的方向，按四个维度评估：**实用性 / 可展示性（作品集、demo 是否吸睛）/ 跟进 AI 潮流 / 求职加分**。

## 2.1. 一个判断
**求职场景下，工程成熟度比再加业务更值钱**

面试 LLM 应用 / Agent / RAG 岗时，"做过一个问答机器人"几乎人人都能说，拉不开差距。真正稀缺的是这句话：**能度量一个 LLM 系统好不好，并能系统性地把它改好、守住、压成本。**

所以跳出业务看，加分项分三层，从纵向加深，比横向再铺一个业务更打动人：

| 层 | 解决什么 | 对应方向（见 §2.3） |
|---|---|---|
| 可度量 | 改动有没有变好，靠数据说话 | 评估 + 可观测闭环 |
| 可信赖 | 被攻击 / 异常时守得住 | AI 安全 / 红队 |
| 有亮点 | 一个能"哇"住人的 demo，蹭上潮流 | Deep Research / 浏览器代理 / 语音 |

一句话叙事目标：**"一个带评估闭环和安全防护的多代理 RAG 平台，能度量、能守住、还能自主深度研究。"**

## 2.2. 候选 A：仍在"个人学习助手"范畴内

复用现有 RAG / Agent / 存储，铺面积、风险低，但求职差异化一般。

**Agent 能力候选**（挑契合"个人学习助手"定位的，不是全做）：

| 候选 | 价值 | 大致成本 |
|---|---|---|
| 深度研究模式（Deep Research） | 多步检索 + web + 综述，强化"主动学新东西"主线 | 中-高（多轮规划 + 结果汇总） |
| 子代理（SubAgent） | 把出题 / 复习 / 研究拆成专职子代理，可并行、各自独立上下文 | 中（已有 EventBus / tool 基础） |
| 长程上下文压缩 / 记忆整合 | 长对话 / 长研究时省 token、防上下文污染 | 中 |
| 事件钩子（Hooks） | 在 SRS 触发复习、plan 完成等节点挂动作（通知 / 自动出题） | 低-中 |
| 自定义 Workflow | 用户声明式编排自己的学习流程 | 高（偏锦上添花） |

**新业务候选**（仍在定位之内）：

| 候选 | 说明 | 复用程度 |
|---|---|---|
| 深度调研 / 综述助手 | 针对知识库 + web 产出结构化报告（与上面深度研究同源） | 高 |
| 阅读精读助手 | 论文 / 书籍逐章摘要 + 提问 + 自动生成复习卡 | 高（接 quiz / srs） |
| 笔记整理与关联 | 自动归类、关联、去重个人笔记，沉淀成知识结构 | 中 |
| 周期性知识简报 | 按兴趣主动推送知识库新增 / 待复习摘要 | 中（接 SRS scheduler） |
| 技术写作助手 | 基于知识库辅助写博客 / 文档 | 中 |
| 企业内 Q&A | 会让定位偏移，列此备查 | 高 |

## 2.3. 候选 B：跳出业务的方向

LLMOps（LLM 运维：把评估 / 监控 / 成本治理工程化）、GraphRAG（基于知识图谱的检索）、computer-use（让模型直接操作浏览器 / 电脑）这类是 2026 仍在涨的方向。

| 方向 | 一句话 | 做了有啥用 | 加分 | 成本 | 复用现有 |
|---|---|---|---|---|---|
| 评估 + 可观测闭环（LLMOps） | golden 数据集 + RAG/Agent 指标（recall@k 前 k 召回率、MRR 平均倒数排名、faithfulness 答案忠实度、answer-relevance 答案相关度、tool 成功率）+ CI 回归门禁 + trace / 成本看板 | 改动好不好用数据说话，回归能被拦住；线上耗时 / 成本 / 错误看得见、可定位 | ★★★★★ | 中 | 已有 `tools/agent_eval/` 脚手架 |
| Deep Research 多代理 | planner + 子代理并行查（KB+web）+ 反思 + 带引用的结构化报告 | 一句话换回一篇跨多源查证的带引用调研报告 | ★★★★ | 中-高 | EventBus / tools / RAG |
| Agentic RAG / GraphRAG | 抽实体-关系建图、多跳检索、查询规划、按需检索 | 能答多跳 / 关系类难题，减少单次检索漏召 | ★★★★ | 中-高 | 向量库 / 检索层 |
| 浏览器 computer-use 代理 | 让 agent 真去操作浏览器取数、填表、截图核对 | 能处理没有 API 的网页场景，真正"替你动手" | ★★★ | 中 | 已有 MCP / 浏览器 MCP |
| AI 安全 / 红队模块 | 把现有注入防御扩成可跑的红队测试集 + guardrail（护栏）评分报告 | 量化防御有效性，回归防住注入，证明系统抗攻击 | ★★★★ | 中 | 已有 prompt injection 防御 |
| 模型路由 + 语义缓存 + 降本 | 按难度 / 成本路由模型、语义缓存、降本看板 | 自动用更便宜的模型 + 命中缓存，直接降延迟和成本 | ★★★ | 中 | 已有 provider 抽象 |
| 语音 / 实时多模态 tutor | 实时语音问答、截图讲解 | 能开口对话、对截图讲解，交互更自然 | ★★★ | 中-高 | 需接 ASR/TTS（语音识别 / 合成），复用低 |
| 代码 / SWE 助手 | 仓库问答 + 改 bug + 跑测试（SWE：软件工程） | 能对着仓库问答、自动改 bug 并跑测试验证 | ★★★ | 高 | 复用一般 |

## 2.4. 选定Feature

| Feature 名 | 功能 | side effect |
|---|---|---|
| 评估 + 可观测闭环 | golden 数据集 + RAG/Agent 指标 + CI 回归门禁 + trace / 成本看板 | 引入结构化指标库（同时有markdown报告）；埋点轻微侵入主链路（须软失败不阻断）；LLM judge 评估耗 token |
| 模型路由 + 语义缓存 + 降本 | 按难度 / 成本路由模型 + 语义缓存命中 + 降本看板 | 缓存可能返回过期 / 不精确结果（需失效策略）；路由判断本身有开销、可能选错模型；多一层逻辑增加复杂度 |
| Deep Research | planner + 子代理并行查（KB+web）+ 反思 + 带引用的结构化报告 | 更慢、更贵（多轮 LLM + 多路检索）；复杂度高；可能放大幻觉 / 跑题，需约束 |
| AI 安全 / 红队模块 | 红队攻击测试集 + guardrail 评分 + CI 拦截率门禁 | 误杀（FPR）可能挡正常输入；红队样本要持续维护；评测耗 token；用户无感、不增体验 |

# 3. 需求定义

## 3.1. 评估 + 可观测闭环

**目标**：质量能用数据度量，线上运行能看见。

- 离线评估：统一 golden 数据集 + 指标（检索 recall@k / MRR、RAG faithfulness / 相关度、Agent 成功率、安全拦截率），出 Markdown 报告，进 CI 回归门禁。
- RAG入库时，根据入库资料，调用LLM自动更新 golden 数据集（后台运行，用户**不**感知，log 可查）
- 在线可观测：每次 chat 埋 trace（检索 / LLM / tool 各阶段耗时、token、成本），落结构化指标库（与 Markdown 报告职责分离）。
- 看板：概览 + 单请求 trace 瀑布 + 成本 / 延迟。
- 分档：最小（离线 + CI）→ 进阶（+ trace + 看板）→ 完整（+ 趋势 / 告警）。

## 3.2. 模型路由 + 语义缓存 + 降本

**目标**：降对话 / RAG 的延迟与成本。

- 路由：按问题难度 / 类型选模型，简单问走小而便宜的模型。
- 语义缓存：相近 query 命中历史结果，跳过重复检索 / 生成。
- 降本看板：复用 §3.1 的成本数据，展示节省效果。
- 验收：常见重复问法延迟 / 成本明显下降，质量不退。

## 3.3. Deep Research

**目标**：一句话换回一篇带引用的调研报告。

- 规划：planner 把问题拆成子问题。
- 并行检索：子代理各自查 KB + web，复用现有 RAG / tools / EventBus。
- 综述：反思去重，产出分节、带引用的结构化报告。
- 边界：限轮数 / 来源数防失控；定位"重质量不重速度"。

## 3.4. AI 安全 / 红队模块

**目标**：把已有防御从"写了"变成"可证明有效、能回归守住"。

- 红队测试集：直接 / 间接注入、越权调用、SSRF、信息泄露、越狱等分类样本。
- 评分：逐类拦截率 + 误杀率（FPR），出报告并进 CI 门禁。
- 被测对象：复用现有 `security_filter` / `url_guard`。
- 定位：用户无感的"隐形护栏"，价值在可信度与面试差异化。
