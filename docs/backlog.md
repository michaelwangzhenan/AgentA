1. m1. iter_1 Review

# 2. iter_2 backlog Review

[iter2 Backlog](v_1_0/iteration/iter_2_agent.md#413-backlog)

# 3. iter_99 Review

## 3.1. 项目介绍

项目介绍材料
PPT

## 3.2. embedding

Embedding 模型 选择 与 管理
消融实验选 模型

## 3.3. UI 改进

不同内容用不同 颜色，字体，高亮？
图标？
动画？
logo?

## 3.4. 记忆/rules/skils/mcp 合并到一页

进去后还要带左边的图标
其它2级页面也加图标

## 3.5. support more doc types

## 3.6. 知识库

用户只能删除自己入库文件

## 3.7. harness

agenta 的 harness 功能就是自我反思/自我纠正（Relection）? 只是harnness 概念里的一个子集？

## 3.8. hardcode prompt to file

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

## 3.9. new tool

扫描并返回 可用的 LLM 列表。
包括 agenta 未知的？

## 3.10. download_models.py 上UI

## 3.11. workflow

## 3.12. 多语言

## 3.13. skill os

## 3.14. 新业务

# 4. others

## 4.1. WebUI 导出对话

## 4.2. rag eval 消融实验对比

UI 页面可选多份报告，进行对比 -> 消融实验对比

## 4.3. Online RAG 模型
