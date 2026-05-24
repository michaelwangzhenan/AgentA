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

## 4.1.Review 现有
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

## 4.4.清理前期不必要功能/代码
根据新的架构，评估哪些功能/代码是不必要的，需要清理
例如：
1. 因为现在已有 tools/rag_cli.py, CLI中的 /ingest 命令可以删掉
2. 考虑到后续还有UI功能，CLI的定位需要重新考虑

## 4.5.根据新的设计，调整代码框架
1.把代码框架，按新的架构调整好
2.回归测试，确保功能正常

## 4.6.Agent 的最新功能/技术探索
[3.Agent 优化方向](#3agent-优化方向) 中已列的 12 项是**必做项**（基础盘）。
本步在此基础上**补充候选**：调研最新 Agent 论文 / 产品（GHC、Cursor、Claude Code 等）+ [3.x TBD] 中的项，列出所有可能新增的功能/技术作为**候选清单**。

输出：候选清单（每项一句话简介 + 主要参考来源）

## 4.7.确定 AgentA 中 Agent 部分的需求：哪些是本项目应该支持，能够支持，值得支持的
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
    2.4 测试功能
    2.5 更新design文档
    2.6 评估是否需要新加工具，在 [配套 tools](#410配套-tools) 实现

## 4.10.配套 tools(参考/tools目录下的 RAG tool)
1. Review 9.2.6 累积的工具候选清单 → 合并、取舍、定优先级
2. 逐工具实现

## 4.11.更新 README