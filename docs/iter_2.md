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
- LLM API调用: openai /anthropic / google / azure / 国内的应该都是 openai API
- Agent 循环/架构：ReAct, plan and execute, loop 等
- Session ：一次对话记录
- Memory ： per 用户， 跨 session记忆
- Prompt ： 理解 system/user/assistant prompt，实现类似 GHC/cursor 在 .github/.cursor 目录下的 prompt 文件，或自定义agent
- Tools ：Agent 代码自实现的 tool(RAG,web search, etc) + 类似 GHC 把插件当作工具
- Skills ：支持标准 Skills 注入（https://agentskills.io/home），实现参考 GHC 
- MCP ： 支持标准MCP(https://modelcontextprotocol.io/docs/getting-started/intro) ，实现参考 GHC/Cursor
- Thinking模式：增加更多模型支持，流式输出优化，可折叠等
- Harness ： LLM 直接返回文本 → 输出最终回答，评价回答，决定是否继续提问 => 建立反馈机制，让 AI 能自我检查和修正。
- 防止 prompt injection
- CnP Refine

TBD:
- 多Agent/SubAgent/A2A协议
- 支持sandbox
- 用户自定义 Workflow?


# 4.Agent 改进计划
1. Review 现有实现
2. 重新整体设计AgentA架构：Agent,RAG,UI·
3. 三种实现方式如何管理
4. 清理前期不必要功能/代码
5. 根据新的设计，调整代码框架
6. 补全当前 Agent 的最新功能/技术
7. 确定 AgentA中 Agent 部分的需求：哪些是本项目应该支持，能够支持，值得支持的
8. 如何评估 Agent实现
9. 开始实现
10.配套 tools(参考/tools目录下的 RAG tool)
11.文档更新（design,readme）