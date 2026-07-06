# 1. 如何选 Agent 架构

规则稳定 → Workflow
需要理解生成 → 受控单 Agent
需要连续行动 → ReAct / Plan-and-Execute
需要专家分工 → Orchestrator-Workers
需要专家接管会话 → Handoff
需要长流程状态恢复 → Workflow Graph
只是为了显得高级 → 不要上多 Agent

# 2. Agent 4 层

→ Prompt 
→ 加 Context（RAG、记忆、工具结果）
→ 加 Harness（权限、验证、持久状态） 
→ 加 Loop（调度、分诊、成本上限）

LangChain Loop：
Loop 1：Agent Loop -> Prompt/Context/Harness
while(goal) { think → act → observe }

Loop 2：Verification Loop -> Harness 验证
在外层加 Grader：按 rubric 检查输出，失败则把反馈打回模型重试。
工程上更强调 可配置的 rubric + 可重复执行的检查器，而不是让模型「觉得自己改好了」
代价：延迟和 token 成本上升。 思考：生产场景质量通常比速度重要？这层 loop 值得默认开启？

Loop 3：Event Driven Loop -> Loop 调度
事件驱动 loop：新文档落地、定时 cron、webhook 到达 → 触发 Agent

Loop 4：Hill Climbing Loop -> Loop 运营
每次 Agent 运行产生 trace：模型做了什么、调了哪些工具、Grader 反馈如何。这些 trace 是高价值信号。Hill climbing loop 让 分析 Agent 读 trace，找出问题，直接改写 Harness 配置——prompt、tool 定义、Grader rubric 等。


| 现象              | 可能缺               | 建议动作                            |
| --------------- | ----------------- | ------------------------------- |
| 单次对话很好，关窗就忘     | Context / Harness | 加 STATE、记忆、持久化                  |
| 多轮能跑但结果不稳定      | L2 Verification   | 加 rubric + 确定性检查                |
| 只能手动点运行         | L3 Event Driven   | cron / webhook / Slack 触发       |
| 同样错误反复出现        | L4 Hill Climbing  | trace 分析 → 改 prompt/tool/grader |
| prompt 改到第七版仍不稳 | 可能修错层             | 先查 Context 检索，再查 Harness 权限     |
| 跑一夜 token 烧穿    | Loop Engineering  | budget、L1 只报告、分诊降噪              |


