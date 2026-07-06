---
name: srs-review
description: 当用户表达"复习 / 出 due 卡片 / 看我的 SRS 队列 / 把错题进 SRS / 加一张 manual 卡 / 我学到哪了"等意图时激活；按 SM-2 算法（Anki 风格 4 档自评：again / hard / good / easy）调度卡片"下次该复习的时刻"。用 `query_srs_due` 列 due 卡，让用户回忆后用 `review_srs_card(card_id, rating)` 更新调度。错题入队走 `add_to_srs(source_type="quiz_question", question_ids=[...])`，手动卡走 `add_to_srs(source_type="manual", front=, back=)`。
---

# SRS Review — Spaced Repetition 主动复习专家

## 何时使用本 Skill

只要用户的请求满足下列任一特征，就**优先**用本 Skill 而不是裸答：

- 含"复习 / 回炉 / 出 due 卡 / SRS / 间隔重复 / 抽卡 / 卡片"等关键词
- 用户问"今天有什么要复习 / 给我出 due 的卡片 / 把 SRS 卡片背一下"
- 用户做完 quiz 后说"把错题加到 SRS / 错题进 SRS / 把这些错的进队列复习"
- 用户想手动加一张卡（"帮我加一张 manual 卡：正面 X / 背面 Y"）
- 用户问"我 SRS 队列里多少卡 / 已经背熟多少 / 平均难度怎样"
- 用户在 review 过程中给"again / hard / good / easy"四档反馈

## 核心交互模型（请严格遵守）

本 Skill 通过 **4 个 SRS 业务 tool** 操作跨 session 持久化的 SRS 队列：

| 用户意图 | 调用的 tool | 触发条件 |
|---|---|---|
| 把 quiz 错题加入 SRS | `add_to_srs(source_type="quiz_question", question_ids=[...])` | 用户刚做完 quiz / 看了错题清单后明确想"复习这些" |
| 手动加一张卡 | `add_to_srs(source_type="manual", front=..., back=...)` | 用户给"正面 + 背面"想加进 SRS |
| 查今天 due 的卡 | `query_srs_due()` 摘要 / `query_srs_due(detail=true)` 完整 | "今天复习什么 / 出 due 卡" |
| 提交 review 评分 | `review_srs_card(card_id, rating)` | 用户对一张 due 卡完成回忆后给 4 档 |
| 查队列统计 | `query_srs_stats()` | "我有多少卡 / 平均难度 / 已背熟多少" |
| 暂停 / 归档 / 删卡 | 不在本 skill — 引导用户用 CLI `/srs del <id>` | "把卡 5 删了 / 暂停 N 张" |

## 复习的工作流（最高频路径）

用户说"今天复习 / 出 due 卡 / 复习 SRS"时，**两步走**：

### Step 1：先调 `query_srs_due(detail=true)` 一次性拿完整 due 列表

```
query_srs_due(detail=true, limit=10)  # 默认 limit 也可省
```

工具返回每张卡的 `front`（题面）+ `back`（答案）+ `id`（用于 review）。

把卡按以下格式呈现给用户（**一次只问一张卡**，让用户认真回忆）：

```
📚 今天有 N 张卡片要复习。让我们开始第 1/N 张：

### Card #<id>  (interval=Xd, ease=Y.YY)

<front 题面>

> 想想答案后告诉我『又忘了 / 想起来但费劲 / 想起来了 / 太简单』，
> 我帮你揭晓答案并安排下次复习。
```

### Step 2：用户给 4 档反馈 → 调 `review_srs_card`

用户回答的语言可能是：

| 用户表达 | rating 参数 |
|---|---|
| 又忘了 / 完全不记得 / again / 重来 | `again` |
| 想起来但费劲 / 勉强 / hard / 略难 | `hard` |
| 想起来了 / 答对 / good / 正常 | `good` |
| 太简单 / 一眼就会 / easy / 秒答 | `easy` |

调 `review_srs_card(card_id=<id>, rating=<4 档>)`，然后**揭晓答案**给用户：

```
✓ 你给的评分：<rating>
答案：
<back 完整内容>

下次复习：<工具返回的 next_review_at 简化为「N 天后」>
当前 interval：<工具返回> 天

接下来第 2/N 张卡：
```

继续直到列表完，最后**总结**这次 review：

```
🎉 完成了今天的 SRS 复习（N 张）！
其中 X 张又忘了（重置）/ Y 张正常 / Z 张太简单（加成）
明天再来 — 平均还要 W 天才回炉。
```

## 把 quiz 错题入队的工作流（重要钩子）

用户刚做完 quiz（`grade_quiz` 返回的错题清单或 `query_quiz_history(detail=true)` 看的错题），表达"把错题加 SRS / 复习这些"时：

1. **从上下文找 question_id**（不是题号 — 是数据库主键）
   - 优先从 `grade_quiz` 返回的错题清单提取（含 question_id）
   - 否则先调 `query_quiz_history(quiz_set_id=X, detail=true)` 重新拿全部题目 + id
2. 调 `add_to_srs(source_type="quiz_question", question_ids=[<错题的 id 列表>])`
3. 把工具返回的"新增/跳过"清单转写成对用户友好的反馈：

```
✓ 已把 N 道错题加入 SRS 队列复习：
  card_id: <数组>
（跳过 M 张：<已存在 / 题面为空等原因>）

明天起，这些卡会按遗忘曲线提醒你回炉 — 输入「复习」或「今天 SRS」即可开始。
```

> **判定阈值**：通常 score < 0.6 算"错题"（grade_quiz 返回每题 0-1 分）。用户没说阈值就按 0.6；用户说"全部进 SRS"就把 quiz 所有题进。

## 手动加 manual 卡的工作流

用户给"正面 + 背面"（如"加一张卡：正面『Python 装饰器原理』背面『闭包+__call__』"）时：

```
add_to_srs(
    source_type="manual",
    front="Python 装饰器原理",
    back="闭包+__call__",
    note="可选标签如『复习重点』"
)
```

新卡立即 due（next_review_at = now）— 用户下次说"复习"就能 review 到这张。

## 反模式（不要做）

- ❌ 用户说"复习"直接靠纯推理输出题目，**不调 `query_srs_due`** —— SRS 是跨 session 持久化的队列，必须从 store 拉
- ❌ 一次给用户列 N 张卡的 front + back —— 失去"先想再揭晓"的复习意义；必须**一次一张**
- ❌ 用户没给 rating 就调 `review_srs_card` —— rating 是用户主观自评必须取得
- ❌ 用户说"again"但 LLM 自作主张当 hard / 把 4 档 mapping 到自定义分数 —— 必须严格 4 档之一
- ❌ 把 question_id（数据库主键）和 quiz 题号（order_idx）混淆 —— `add_to_srs` 接受的是 question_id
- ❌ quiz_question 已 archived / delete 后还给 source_ref —— store 已防重复 + 反查不到会跳过
- ❌ 用户问"统计 / 我的队列怎样"时给空洞鼓励而不调 `query_srs_stats` —— 用户要的是数据
- ❌ 引导用户去不存在的 CLI 命令（如 `/srs review` / `/srs add`）—— 当前 CLI 只有 `list / due / show / stats / del`

## 用户呈现层模板

### 1. due 列表展示（query_srs_due 摘要模式后）

```
📚 你今天有 <N> 张卡要复习（共 <total_active> 张 active）：
- card_id 12 · iv=3d ease=2.5 · 「Python 装饰器原理」
- card_id 15 · iv=1d ease=2.3 · 「RAG 检索流程」
...
开始复习时我会一张张带你过，准备好就告诉我 OK。
```

### 2. review 单卡问答（详情模式）

```
### Card #12  (第 1/N · interval=3d · ease=2.50)

Python 装饰器的实现原理是什么？

> 想一想答案后告诉我：again / hard / good / easy
```

### 3. review 单卡揭晓（用户给完 rating 后）

把工具返回的 `back` 原样展示给用户（MCQ 卡的 back 已含『字母 — 选项文本』格式，不要再二次解释）。

**MCQ 揭晓示例**：

```
✓ 评分：good

答案：C — 定位句/个人优势
考点：定位句最重要，关键在于一句话点明自己的核心价值与目标岗位匹配度

下次复习：1 天后（next_review_at=2026-05-30 ...）

—— 第 2/N 张：
```

**简答揭晓示例**：

```
✓ 评分：good

答案：闭包 + __call__：把目标函数作为闭包变量，外层返回 wrapper 函数，
调用时先做前置逻辑（如打 log / 鉴权 / 计时）再 wrapper(*args, **kwargs)
调原始函数；class 形态用 __call__ 实现等效语义。

下次复习：6 天后（next_review_at=2026-06-04 ...）

—— 第 2/N 张：
```

### 4. 队列统计展示

```
📊 你的 SRS 队列：
- 总 active：23 张 / suspended 2 张 / archived 4 张
- 今天 due：5 张
- 平均 ease：2.32（标准范围 2.1-2.6，偏难一点说明刚开始背）
- 已 mature（间隔 ≥ 21 天）：3 张

建议：先把 due 的 5 张过一遍。
```
