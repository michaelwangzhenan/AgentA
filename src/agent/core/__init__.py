"""
Agent 公共层 helpers —— 三种 Agent 实现（Python / LangChain / AutoGPT）共享的业务策略。

设计原则：
- 此包内的 helper 封装"何时调依赖、如何编排结果"的业务策略
- 依赖层（ChatHistoryStore / UserMemoryStore / LLMProvider）仍位于 src/memory/ 与 src/llm/
- helper 命名约定：*Manager / *Engine / *Policy / *Bus

模块清单：
- history_manager.py      HistoryManager     历史截断 + skill_pair 保护 + system 拼接
- memory_manager.py       MemoryManager      UserMemory 触发 + 提取 + 注入 system_prompt
- rules_loader.py         build_rules_block   把用户 rules 文本拼成 <project_rules> 块
- event_bus.py            EventBus           统一事件分发（多订阅 + 异常隔离）
- tool_call_engine.py     ToolCallEngine     工具调用编排（执行 + 格式化 + 引导提示 + 写历史）
- thinking_policy.py      ThinkingPolicy     thinking 配置与 budget
- citation_builder.py     CitationBuilder    RAG 引用展示编排（注册 / 提取 / 渲染 sources 块）
- plan_manager.py         PlanState          Plan-Execute 状态封装 + reconstruct_from_messages
- srs_scheduler.py        schedule_review    SM-2 调度公式 + Anki 4 档 mapping
- security_filter.py      scrub_injection    prompt injection 启发式 / 标签包装 / 名单门
- mcp_config.py           load_mcp_config    MCP server 清单加载（schema + env 展开）
- mcp_manager.py          MCPManager         MCP client 生命周期（后台 thread + event loop）
- url_guard.py            is_url_safe        SSRF 防御（拒内网 IP / file:// / 解析失败）
"""
