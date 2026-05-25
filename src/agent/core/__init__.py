"""
Agent 公共层 helpers —— 三种 Agent 实现（Python / LangChain / AutoGPT）共享的业务策略。

设计原则参见 docs/design.md §5：
- 此包内的 helper 封装"何时调依赖、如何编排结果"的业务策略
- 依赖层（ChatHistoryStore / UserMemoryStore / LLMProvider）仍位于 src/memory/ 与 src/llm/
- helper 命名约定：*Manager / *Engine / *Policy / *Bus

模块清单（随 §4.5 重构逐步落地）：
- history_manager.py      HistoryManager     历史截断 + skill_pair 保护 + system 拼接
- memory_manager.py       MemoryManager      UserMemory 触发 + 提取 + 注入 system_prompt
- event_bus.py            EventBus           统一事件分发（多订阅 + 异常隔离）
- tool_call_engine.py     ToolCallEngine     工具调用编排（执行 + 格式化 + 引导提示 + 写历史）
- thinking_policy.py      ThinkingPolicy     adaptive thinking budget 估算
"""
