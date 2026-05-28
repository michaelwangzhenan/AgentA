---
name: study-planner
description: 当用户需要规划学习计划、复习计划、面试备考路线、考试冲刺，或希望跟踪 / 更新 / 切换长期学习目标时激活；用 `create_study_plan` 把计划落到 SQLite，跨 session 持久化；用 `update_study_progress` / `query_study_status` 维护进度。新建计划前先用 `make_plan` 把"查领域 → 列阶段 → 列任务 → 落库"拆成 4 步执行。
---

# Study Planner — 学习计划生成与跟踪专家

## 何时使用本 Skill

只要用户的请求满足下列任一特征，就**优先**用本 Skill 而不是裸答：

- 含"复习计划 / 学习计划 / 学习路线 / 备考 / 面试准备 / N 天 / 两周 / 速成"等关键词
- 给定一个学习目标 + 一段时间（"8 周准备 ML 面试"、"两周搞定 Transformer 基础"）
- 询问自己已有学习计划（"我学到哪了"、"我下一步该干啥"、"我有哪些计划"）
- 报告任务完成 / 跳过（"今天看完了 FastAPI 文档"、"这周太忙跳过 X"）
- 想结束某个计划（"我不学这个了"）

## 核心交互模型（请严格遵守）

本 Skill 通过 **3 个学习计划业务 tool** 操作跨 session 持久化的学习计划，
另复用 Phase 2.1 的 **`make_plan` / `update_step` / `abort_plan`** 拆解"新建计划"这个复杂任务本身。

| 用户意图 | 调用的 tool | 触发条件 |
|---|---|---|
| 新建一个长期学习计划 | `make_plan` 拆 4 步 → 各步走对应 tool → 最后 `create_study_plan` 落库 | 用户首次给出学习目标且无 active plan / 明确说 "新建" |
| 查 active 学习计划进度 | `query_study_status()` 不传参 | "我学到哪了"、"下一步是啥" |
| 查指定 plan / 列全部 plan | `query_study_status(plan_id=X)` / `query_study_status(list_all=true)` | "我所有的计划"、"看下 plan 3" |
| 更新单个任务状态 | `update_study_progress(plan_id, task_id, status, note?)` | "完成了 X"、"跳过 Y" |
| 结束 / 放弃某计划 | 不在本 skill — 引导用户用 CLI `/study abandon <plan_id>` | "我不学这个了" |

> 多 plan 并存：同时仅 1 个 plan 为 active；用户用 CLI `/study switch <id>` 切换。
> 不要在 skill 内试图直接切 active —— Agent 没有 `switch_active_plan` tool。

## 新建计划的工作流（D5 嵌套：先 `make_plan` 再 `create_study_plan`）

**收到学习目标后，第一步永远是 `make_plan`，把"新建计划"这个任务本身拆成 4 步**：

```
make_plan(steps=[
    "检索 KB 中与<目标>相关的已有资料 / 笔记",
    "确定阶段拆分（按周/月，3-12 个阶段）",
    "列每阶段 3-6 个具体可执行任务",
    "调 create_study_plan 落库 + 把概要呈现给用户"
])
```

随后按 plan 顺序逐步执行，**每完成一步调 `update_step`**：

| Step | 主要 tool | 关键动作 |
|---|---|---|
| 1. 查领域 | `search_knowledge(query=<目标关键词>)` | 命中即作为推荐资料附在任务后；命中为空换 `web_search` 兜底 |
| 2. 列阶段 | （纯推理输出） | 阶段数 ≤ 12；时段 ≤ 2 周用"天"为阶段，> 2 周用"周"为阶段 |
| 3. 列任务 | （纯推理输出） | 每阶段 3-6 任务；每个任务**动词起头 + 可勾选 + 时长 ≤ 60 分钟**（超出拆分）；尽量挂上 step 1 检索到的资料 |
| 4. 落库 | `create_study_plan(goal, weeks, tasks=[{stage_idx, order_idx, title}, ...])` | tool 返回 `plan_id`；再向用户回放计划概要 + 提醒"完成任务时告诉我，我帮你打勾" |

**注意**：
- `create_study_plan` 一次性接收**全部 tasks**，不要分多次调用；调用前先在推理中把 task 列表组织好
- `title` 字段就是任务描述（如 `"完成 Pandas 官方 10min 教程"`），**不要**带 `[ ]` checkbox 或 `[n]` 引用标记 —— 这些是给用户看的呈现层修饰
- 用户给的资料引用（`[n]`）只在最终回放计划概要给用户时使用，不进 DB

## 进度更新的工作流

用户报告完成 / 跳过任务时：

1. 如果心里没把握 task_id，先调 `query_study_status()` 拿到当前 active plan 全貌
2. 找到匹配的 task，调 `update_study_progress(plan_id, task_id, status="success" 或 "skipped", note=<关键收获或跳过原因>)`
3. tool 返回"下一个待办"提示；按需向用户播报并提供轻度鼓励 / 衔接

## 跨 session 恢复场景

用户新 session 问"我学到哪了 / 我有啥计划"：

- 默认先 `query_study_status()` 看 active plan
- 若返回 `[empty]`，再调 `query_study_status(list_all=true)` 列全部
- 若全部为空，转去引导用户新建计划

## 反模式（不要做）

- ❌ 收到学习目标直接输出 markdown 计划而不落库 —— 失去跨 session 价值，违反本 skill 核心目的
- ❌ 跳过 `make_plan` 直接调 `create_study_plan` —— 违反 D5 嵌套契约；用户看不到推理过程
- ❌ 在 `create_study_plan` 的 `title` 字段塞 markdown 修饰（checkbox / 引用标记）—— 污染 DB
- ❌ 一次只塞 1-2 个 tasks 多次调 `create_study_plan` —— 会产生多个 plan
- ❌ 用户问"我学到哪了"就重新生成新计划 —— 应先 `query_study_status` 查既有
- ❌ 引用编号自己编（编号只能来自 `search_knowledge` 工具返回的清单）
- ❌ 假装 KB 里有但其实没的资料 — 这是用户最不能容忍的幻觉
- ❌ 多 plan 切换在 skill 内试图自动完成 —— 这是 CLI `/study switch` 的职责，请直接引导用户

## 用户呈现层模板（落库后展示用，仅给用户看）

落库成功后，向用户回放时建议这样组织（**不是**塞进 `create_study_plan` 入参）：

```
✓ 已建好「<目标>」的学习计划（共 <N> 阶段 / <M> 个任务，已 active）

### Stage 1：<本阶段主题>
- [ ] <任务 1>（XX 分钟）— 资料：…  [n]
- [ ] <任务 2>（XX 分钟）— 资料：…  [n]

### Stage 2 ...

→ 立刻开始第 1 步：<task_id=1 的标题>
（完成任何任务时告诉我，我会帮你打勾；想看进度随时说"我学到哪了"）
```
