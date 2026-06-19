# 1. Reviewed

## 1.1. 文档更新
design -> 简化，重建
README -> 重新设计
代码指南 -> 重建

项目介绍材料
PPT

## 1.2. RAG

1. 讨论：影响RAG质量的因素 -> embedding模型/入库算法/召回算法
2. download_models.py 上UI， 支持任意模型下载
3. 下载后可根据配置使用
4. 选定入库主题/资料
5. 生成真正有效的golden 集
6. 各模型对比实验
7. UI 页面可选多份报告，进行对比
8. 消融实验
9. 入库流程优化 -> 对标业内
10. 召回流程和算法 -> 对标业内
11. 入库支持更多的文档格式
12. UI 知识库:用户只能删除自己入库文件
13. online 模型（Key + API）

# 2. iter_1 Review

# 3. iter_2 backlog Review

[iter2 Backlog](v_1_0/iteration/iter_2_agent.md#413-backlog)

# 4. iter_99 and more

## 4.1. UI 改进

新主题 Vs 优化当前已有主题

不同内容用不同 颜色，字体，高亮？
图标？
动画？
logo?


## 4.2. 记忆/rules/skils/mcp 合并到一页

进去后还要带左边的图标
其它2级页面也加图标


## 4.3. harness

agenta 的 harness 功能就是Critic，并不是自我反思/自我纠正（Relection）。 只是harnness 概念里的一个很“窄”的子集。

**已经改名为 Critic**



## 4.4. hardcode prompt to file

所有 hardcode 的 prompt 都做成文件，统一外置管理。

1. 格式化 prompt， 加适用场景说明这段用在哪里
2. 每个用途一个文件，还是一个大文件？
3. 这些文件是启动就读进内存？
4. 除了生成代码，哪些场景用？UT？ eval？

**起因**：`quiz_critic.txt` / `rag_critic.txt` 是生产用的 critic 评分 prompt（quiz 批改自检 / RAG 召回过滤的 criteria），却放在 `tools/agent_eval/critic/` 下，导致 `src/`（生产）反向依赖 `tools/agent_eval/`（评估目录）——目录归属不合理。

**现状**：项目里 prompt 两种写法并存、不统一——

- 外置文件：仅 critic 的 `quiz_critic.txt` / `rag_critic.txt`（`CriticManager._load_prompt` 加载，缺失 fail-fast）。
- 内联常量（主流）：散在多处，如 `agent_commons.SYSTEM_PROMPT`、`tools._SHORT_ANSWER_JUDGE_SYS`、`critic_manager._RAG_BATCH_SYS/USER_TEMPLATE`、`research_engine._PLAN/_SUBAGENT/_REFLECT/_SYNTH_SYSTEM`、`autogpt_agent._PLAN/_EXECUTE_SYSTEM`、`golden_gen._GEN_SYS`、`query_rewriter._MULTI_QUERY/_HYDE/_TRANSLATE_PROMPT`、`eval_common.llm_judge._JUDGE_SYS/USER_TEMPLATE`、各 eval 的 `_JUDGE_CRITERIA` 等。

**目标**：统一约定（要么全内联、要么全外置）。倾向全外置到生产侧（如 `src/.../prompts/`），消除散落 + 修掉反向依赖；外置需定：目录结构、加载/缓存机制、命名约定、fail-fast 策略、是否所有都外置（很短的片段是否值得）。

**注**：纯"跟 eval 共享"不是外置理由——eval 经 `CriticManager` 间接用，内联常量一样能共享。外置的真正价值是 prompt 与代码分离、便于不改码地 review/diff/迭代 prompt。改动面大（涉及多模块 + UT），单独立项再做。

## 4.5. new tool

LLM 可用性工具：扫描并返回可用的 LLM 列表。包括 agenta 未列的LLM？

## 4.6. download_models.py 上UI

## 4.7. workflow

## 4.8. 多语言

## 4.9. skill os

## 4.10. 新业务

## 4.11. WebUI 导出对话

## 4.12. Skills 渐进披露第三层

**scripts 层未实现：当前只做了 catalog + prompt body 两层**

**起因**：Skills 规范（agentskills.io）的渐进披露有三层——catalog（目录）/ prompt body（正文）/ scripts（脚本）。AgentA 目前只实现前两层：catalog 启动时进 base system_prompt，body 在 LLM 调 `load_skill` 时进 messages 历史。

**现状**：scripts 层缺失。即 SKILL.md 目录下随附的可执行脚本（按规范由 agent 在需要时调用，进一步省 context、把确定性逻辑交给代码）尚无加载 / 执行机制。

**目标**：补齐第三层——定义脚本的发现（SKILL.md 同目录）、调用入口、执行沙箱 / 权限边界、与现有 tool / `load_skill` 的关系。涉及安全面（执行外部脚本），改动较大，单独立项再做。


## 4.13. iter4 遗留问题

**空名 tool_call 兜底：根因未治本，靠 provider 层每次全量扫历史兜底**

**起因**：`src/llm/provider.py` 的 `_sanitize_messages_for_llm` 在每次 `chat()` 调用前全量扫一遍历史，丢弃空名 tool_call + 连带丢对应 orphan tool 响应。这是给"早期写进历史的空名 tool_call 脏数据"做的防御性兜底（注释里的"早期 bug 残留"指 AgentA 自己早期流式拼接 / 持久化的残留）。

**现状**：根因没治本，只在读取侧兜底——

- 产生侧：`_run_openai_stream` 流式拼接初始 `name=""` 靠 delta 累加，provider 若给了 `id` 却始终不推 `name`（畸形流）仍可能拼出空名。
- 写入侧：`tool_call_engine.process` 无条件先把 assistant 消息落库（`self._session_store.append`）再解析执行，**没有"空名不落库"校验**，今天产生的空名仍会被持久化。
- 读取侧：provider 层每次调用都 O(历史长度) 全量扫一遍兜底。
- 注：`.` → `_`_（MCP namespaced tool 名适配）是常态需求、永久保留，不在本优化范围。

**目标**：把空名拦截前移到源头（落库处丢弃空名 tool_call）+ 一次性清洗老 DB，之后简化 provider 层的空名兜底分支（`.`→`_`_ 仍保留）。属"向后兼容 vs 激进清理"取舍，单独立项再做。

## 4.14. MCP fetch 的 SSRF 防御未与 host 对齐

**MCP `fetch` 的 URL 拦截依赖 server 端，host 侧 `url_guard` 未共用**

**起因**：内置 `fetch_url` 在 `_tool_fetch_url` 里显式调 `url_guard.is_url_safe` 做 SSRF 拦截；而 MCP `fetch.fetch` 在 `_execute_mcp_tool` 里直接把请求转发给子进程，**没过 host 侧 `url_guard`**。

**现状**：MCP fetch 的 SSRF 防御完全依赖 server 自身实现（`mcp-server-fetch` 默认不抓内网，但这是 server 端约定、非 host 强制）。`url_guard.py` docstring 写的"二者共用同一道防线"是设计意图，当前实现尚未对齐（详 design §3.13）。

**目标**：让 MCP tool 的出站 URL 也过 host 侧 `url_guard`（或等价拦截层），使内置与 MCP 两条 fetch 路径共用同一道 SSRF 防线，不依赖各 server 自觉。

## 4.15. Web「学而时习」页支持 load plan 进会话上下文

`<active_study_plan>`**（四层 prompt 第 4 层）注入目前是 CLI-only**

**起因**：`/study load` 只在 CLI 有（`handlers.py` 调 `learning_plan_store.mark_loaded`），Web/API 没有等价入口。导致 Web 会话里 `get_loaded(session_id)` 永远返回 None，第 4 层 `<active_study_plan>` 始终为空——CLI 与 Web 功能不对齐。

**现状**：Web 有计划 CRUD（`api/routes/plans.py`，对应「学而时习」页）+ tool 查询（`query_study_status`），但没有"把某计划加载进当前会话上下文"这步。且 loaded 映射是 store 单例的内存 dict（`_loaded_by_session`），不持久化。

**目标**：在「学而时习」页加一个"加载到当前会话"的操作（API 调 `mark_loaded(session_id, plan_id)`），让 Web 会话也能注入 `<active_study_plan>`。需定：session_id 从哪来（Web 当前会话）、内存态映射在多 worker / 重启下是否要持久化、与 CLI load 语义如何统一。

## 4.16. 优化3个业务的`SKILL.md`

3个都是AI生成的，还没有Review过。

## 4.17. 学习计划 / 测验 结合 deep-research

**用深度研究的多源材料喂给建计划 / 出题，提升质量与 grounding**

**起因**：学习计划（`create_study_plan`）/ 测验（`create_quiz`）现在的"查资料"只是 skill step 里**一次 `search_knowledge(top_k=10)`**（+ 可选 web_search 兜底），相对浅；deep-research 有子问题拆解 → 并行子代理 → KB+web 多源 → 反思补查 → 带引用综述，材料深得多。对测验尤其有价值（skill 强约束"不能编 KB 没有的事实题"，深度研究能降幻觉）。

**现状**：deep-research 是**顶层替代流程**——`chat.py` 里 `req.mode=="deep_research"` 时直接跑 `ResearchEngine.run()`，**完全绕过 agent 主循环**（不走 tools / skills / make_plan）。所以它和学习计划/测验是互斥两条路径，碰不到一起。两者其实正交：plan-execute 管"建计划/出题的过程组织"，deep-research 能加深"每步查资料的深度"。

**两种结合姿势**：

- 方向 A（研究下沉为可调能力）：skill 的"查 KB"步从 `search_knowledge` 升级为调 research_engine 产出 brief 再建计划/题。质量上限高，但要把 research_engine 从顶层流程改造成 agent 循环可调的 tool/子代理，改动大。
- 方向 B（研究后接业务）：deep-research 出报告后，提供"基于这份研究生成学习计划 / 出测验"的后续动作。改造小、UX 自然，需解决"散文报告 → 结构化 tasks/questions + 引用映射"。倾向先做 B 验证价值。

**待定**：何时值得跑深度研究（成本/延迟高——大/陌生主题？KB 命中稀疏自动触发？显式 opt-in？）；触发 UX；报告转结构化的稳定性。

## 4.18. 配置项
1. Review 配置是否合理，有用
2. .env VS UI 同步

## 4.19. 