# 1. Reviewed

## 1.1. 编写项目介绍

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

# 2. iter_1 Review

# 3. iter_2 backlog Review

[iter2 Backlog](v_1_0/iteration/iter_2_agent.md#413-backlog)

# 4. iter_99 Review

## 4.1. embedding

Embedding 模型 选择 与 管理
消融实验选 模型

## 4.2. UI 改进

不同内容用不同 颜色，字体，高亮？
图标？
动画？
logo?

## 4.3. 记忆/rules/skils/mcp 合并到一页

进去后还要带左边的图标
其它2级页面也加图标

## 4.4. support more doc types

## 4.5. 知识库

用户只能删除自己入库文件

## 4.6. harness

agenta 的 harness 功能就是自我反思/自我纠正（Relection）? 只是harnness 概念里的一个子集？

## 4.7. hardcode prompt to file

所有 hardcode 的 prompt 都做成文件，统一外置管理。

1. 格式化 prompt， 加适用场景说明这段用在哪里
2. 每个用途一个文件，还是一个大文件？
3. 这些文件是启动就读进内存？
4. 除了生成代码，哪些场景用？UT？ eval？

**起因**：`quiz_critic.txt` / `rag_critic.txt` 是生产用的 critic 评分 prompt（quiz 批改自检 / RAG 召回过滤的 criteria），却放在 `tools/agent_eval/harness/` 下，导致 `src/`（生产）反向依赖 `tools/agent_eval/`（评估目录）——目录归属不合理。

**现状**：项目里 prompt 两种写法并存、不统一——

- 外置文件：仅 harness 的 `quiz_critic.txt` / `rag_critic.txt`（`HarnessManager._load_prompt` 加载，缺失 fail-fast）。
- 内联常量（主流）：散在多处，如 `agent_commons.SYSTEM_PROMPT`、`tools._SHORT_ANSWER_JUDGE_SYS`、`harness_manager._RAG_BATCH_SYS/USER_TEMPLATE`、`research_engine._PLAN/_SUBAGENT/_REFLECT/_SYNTH_SYSTEM`、`autogpt_agent._PLAN/_EXECUTE_SYSTEM`、`golden_gen._GEN_SYS`、`query_rewriter._MULTI_QUERY/_HYDE/_TRANSLATE_PROMPT`、`eval_common.llm_judge._JUDGE_SYS/USER_TEMPLATE`、各 eval 的 `_JUDGE_CRITERIA` 等。

**目标**：统一约定（要么全内联、要么全外置）。倾向全外置到生产侧（如 `src/.../prompts/`），消除散落 + 修掉反向依赖；外置需定：目录结构、加载/缓存机制、命名约定、fail-fast 策略、是否所有都外置（很短的片段是否值得）。

**注**：纯"跟 eval 共享"不是外置理由——eval 经 `HarnessManager` 间接用，内联常量一样能共享。外置的真正价值是 prompt 与代码分离、便于不改码地 review/diff/迭代 prompt。改动面大（涉及多模块 + UT），单独立项再做。

## 4.8. new tool

扫描并返回 可用的 LLM 列表。
包括 agenta 未知的？

## 4.9. download_models.py 上UI

## 4.10. workflow

## 4.11. 多语言

## 4.12. skill os

## 4.13. 新业务

# 5. others

## 5.1. WebUI 导出对话

## 5.2. Online RAG 模型

## 5.3. Skills 渐进披露第三层

**scripts 层未实现：当前只做了 catalog + prompt body 两层**

**起因**：Skills 规范（agentskills.io）的渐进披露有三层——catalog（目录）/ prompt body（正文）/ scripts（脚本）。AgentA 目前只实现前两层：catalog 启动时进 base system_prompt，body 在 LLM 调 `load_skill` 时进 messages 历史。

**现状**：scripts 层缺失。即 SKILL.md 目录下随附的可执行脚本（按规范由 agent 在需要时调用，进一步省 context、把确定性逻辑交给代码）尚无加载 / 执行机制。

**目标**：补齐第三层——定义脚本的发现（SKILL.md 同目录）、调用入口、执行沙箱 / 权限边界、与现有 tool / `load_skill` 的关系。涉及安全面（执行外部脚本），改动较大，单独立项再做。

## 5.4. 代码优化

### 5.4.1. iter4 遗留问题

**空名 tool_call 兜底：根因未治本，靠 provider 层每次全量扫历史兜底**

**起因**：`src/llm/provider.py` 的 `_sanitize_messages_for_llm` 在每次 `chat()` 调用前全量扫一遍历史，丢弃空名 tool_call + 连带丢对应 orphan tool 响应。这是给"早期写进历史的空名 tool_call 脏数据"做的防御性兜底（注释里的"早期 bug 残留"指 AgentA 自己早期流式拼接 / 持久化的残留）。

**现状**：根因没治本，只在读取侧兜底——

- 产生侧：`_run_openai_stream` 流式拼接初始 `name=""` 靠 delta 累加，provider 若给了 `id` 却始终不推 `name`（畸形流）仍可能拼出空名。
- 写入侧：`tool_call_engine.process` 无条件先把 assistant 消息落库（`self._session_store.append`）再解析执行，**没有"空名不落库"校验**，今天产生的空名仍会被持久化。
- 读取侧：provider 层每次调用都 O(历史长度) 全量扫一遍兜底。
- 注：`.` → `__`（MCP namespaced tool 名适配）是常态需求、永久保留，不在本优化范围。

**目标**：把空名拦截前移到源头（落库处丢弃空名 tool_call）+ 一次性清洗老 DB，之后简化 provider 层的空名兜底分支（`.`→`__` 仍保留）。属"向后兼容 vs 激进清理"取舍，单独立项再做。

### 5.4.2. MCP fetch 的 SSRF 防御未与 host 对齐

**MCP `fetch` 的 URL 拦截依赖 server 端，host 侧 `url_guard` 未共用**

**起因**：内置 `fetch_url` 在 `_tool_fetch_url` 里显式调 `url_guard.is_url_safe` 做 SSRF 拦截；而 MCP `fetch.fetch` 在 `_execute_mcp_tool` 里直接把请求转发给子进程，**没过 host 侧 `url_guard`**。

**现状**：MCP fetch 的 SSRF 防御完全依赖 server 自身实现（`mcp-server-fetch` 默认不抓内网，但这是 server 端约定、非 host 强制）。`url_guard.py` docstring 写的"二者共用同一道防线"是设计意图，当前实现尚未对齐（详 design §3.13）。

**目标**：让 MCP tool 的出站 URL 也过 host 侧 `url_guard`（或等价拦截层），使内置与 MCP 两条 fetch 路径共用同一道 SSRF 防线，不依赖各 server 自觉。
