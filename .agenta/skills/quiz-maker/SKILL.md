---
name: quiz-maker
description: 当用户希望出题自检 / 测验、把学习计划 stage 出成题、做错题复盘、回顾历史 quiz 时激活；用 `create_quiz` 把 5-15 道题（60% MCQ + 40% 简答固定混合）一次性落库到 SQLite；用户作答后用 `grade_quiz` 自动批改 + 反馈；用 `query_quiz_history` 跨 session 查历史 / 看错题。新建 quiz 前先用 `make_plan` 把"解析意图 → 查 KB → 出题 → 落库"拆成 4 步执行。
---

# Quiz Maker — 知识库自检出题与批改专家

## 何时使用本 Skill

只要用户的请求满足下列任一特征，就**优先**用本 Skill 而不是裸答：

- 含"考考我 / 出题 / 出几道题 / 测试一下 / 自测 / 测验 / 小测 / 复习题 / 错题复盘"等关键词
- 给定一个主题 / 知识点 + 想"测一下"（"考考我 RAG 检索"、"出 5 道 ML 面试题"）
- 想绑学习计划某阶段做练习（"用 active 学习计划 stage 2 出题"、"针对我刚学完的 Python 列表出题"）
- 查询自己的 quiz 历史（"我做过哪些 quiz"、"列出最近 5 个 quiz"）
- 复盘错题（"上次 quiz 我哪些错了"、"看一下 quiz 8 的答案"）
- 用户在 chat 里以"1.B 2.AC 3. xxx"格式回复你刚出过的 quiz —— 那是在作答，按"批改工作流"走

## 核心交互模型（请严格遵守）

本 Skill 通过 **3 个 quiz 业务 tool** 操作跨 session 持久化的 quiz：

| 用户意图 | 调用的 tool | 触发条件 |
|---|---|---|
| 新出一个 quiz | `make_plan` 拆 4 步 → 各步走对应 tool → 最后 `create_quiz` 落库 | 用户首次给主题 + 想测验 / 明确说"新出" |
| 批改用户作答 | `grade_quiz(quiz_set_id, user_answers)` | 用户在 chat 里以题号 + 答案格式回复刚出的 quiz |
| 查 quiz 历史列表 | `query_quiz_history()` 不传参 / 仅传 limit | "我做过哪些 quiz"、"最近 quiz 列表" |
| 查某 plan 的 quiz | `query_quiz_history(plan_id=X)` | "我 plan 3 的 quiz" / "ML 学习计划做过哪些 quiz" |
| 查单个 quiz 详情 / 错题 | `query_quiz_history(quiz_set_id=X, detail=true)` | "看下 quiz 5"、"上次 quiz 哪些错了" |
| 归档 / 删除某 quiz | 不在本 skill — 引导用户用 CLI `/quiz del <id>` | "把 quiz 3 删了" |

## 新建 quiz 的工作流（D5 嵌套：先 `make_plan` 再 `create_quiz`）

**收到出题请求后，第一步永远是 `make_plan`，把"新建 quiz"这个任务本身拆成 4 步**：

```
make_plan(steps=[
    "解析意图（topic / plan_id / stage_idx；确定出题主题与题数）",
    "查 KB 检索主题相关内容作为出题素材",
    "按 60% MCQ + 40% 简答比例组织题目（题干 + 选项 + 标答 + 考点说明）",
    "调 create_quiz 一次性落库 + 把题目呈现给用户"
])
```

随后按 plan 顺序逐步执行，**每完成一步调 `update_step`**：

| Step | 主要 tool | 关键动作 |
|---|---|---|
| 1. 解析意图 | （纯推理输出） | 确认 topic（用户裸说的话题）/ 或 plan_id+stage_idx（用户提到的学习计划阶段）；用户没说题数默认 10 道；用户说"出 X 题"则按 X |
| 2. 查 KB | `search_knowledge(query=<主题关键词>)` | 把命中的 chunk 作为出题素材；命中为空可换 `web_search` 兜底；带 `top_k=10` 拿足量材料 |
| 3. 组织题目 | （纯推理输出） | 按 60% MCQ + 40% 简答固定比例：10 题 = 6 MCQ + 4 简答；5 题 = 3 MCQ + 2 简答；MCQ 中单选 / 多选大致各半；每题题干 + 选项 + 标答 + 简短考点说明（≤ 80 字） |
| 4. 落库 | `create_quiz(topic, plan_id?, stage_idx?, questions=[...])` | tool 返回 `quiz_set_id`；再向用户依次呈现题目并提醒"按 『1.B 2.AC 3. <文字>』格式回答" |

### create_quiz 的 questions 格式（严格遵守）

每道题是一个 dict：

```
{
  "order_idx": 1,                       // 题号，从 1 起，连续递增
  "q_type": "mcq_single",               // mcq_single / mcq_multi / short_answer 三选一
  "stem": "题干文本",                    // 不带题号前缀，纯题干
  "options": ["北京", "上海", "广州", "深圳"],  // MCQ 必填（≥ 2 项）；简答省略 / 留空
  "correct_answer": "B",                // MCQ: 单选『B』 / 多选『AC』；简答: 标准答案文本
  "explanation": "中国 5G NR 标准化主要在 BUPT..."  // 可选，≤ 80 字
}
```

**严格约束**：

- `order_idx` 从 1 起、连续递增，**不能跳号 / 不能从 0 起**
- MCQ 的 `correct_answer` 只能含字母 A-H（按 options 顺序对应），不带其他符号
- 多选题 `correct_answer` 多个字母无分隔（『AC』而不是『A,C』），工具内部归一化
- **不要**在 `stem` / `options` / `correct_answer` 里塞 `[n]` 引用编号（引用是给用户呈现层用的）
- **不要**编造 KB 里没有的事实题；优先从 step 2 检索到的内容出题

## 批改的工作流（用户作答 → grade_quiz）

用户上一轮看到你出的题，本轮用一段自然语言作答（例：『1.B 2.AC 3. RAG 是检索增强生成，先 retrieve 再 generate 的范式 4.D 5. ...』）。

**你的任务（不调 tool 前的推理）**：

1. 从上文找到刚出的 quiz 的 `quiz_set_id` 与每题的 `question_id`（这两个值由 `create_quiz` 返回 + `query_quiz_history` 可重新拿到）
2. 解析用户自然语言回复，把"题号 → 答案串"映射到"question_id → 答案串"
3. 调 `grade_quiz(quiz_set_id, user_answers={"<qid>": "<ans>", ...})`
4. 工具返回总分 + 错题清单 → 把它转写成更友好的反馈给用户（不要照抄 tool 返回；加鼓励 / 重点提示）

> 用户写的是**题号**（『1』），你要转成**question_id**（数据库主键）传给 tool。
> 如果 question_id 不确定，先调 `query_quiz_history(quiz_set_id=X, detail=true)` 拿全部题目的 id。

## 查 quiz 历史 / 错题复盘的工作流

用户问"我做过哪些 quiz / 上次哪些错了 / 看 quiz 5"：

| 用户问法 | 调 tool |
|---|---|
| "我做过哪些 quiz / 列出 quiz 历史" | `query_quiz_history()` 不传参 |
| "我 plan 3 的 quiz / ML 学习计划做过的 quiz" | `query_quiz_history(plan_id=3)` |
| "看 quiz 5（不要批改细节）" | `query_quiz_history(quiz_set_id=5)` |
| "上次 quiz（id=5）我哪些错了 / 看 quiz 5 的错题" | `query_quiz_history(quiz_set_id=5, detail=true)` |

把工具结果转写成对用户更友好的呈现（突出错题 / 提示薄弱点 / 建议复习方向）。

## 反模式（不要做）

- ❌ 收到出题请求直接输出 markdown 题目而不落库 —— 失去跨 session 复盘价值，违反本 skill 核心目的
- ❌ 跳过 `make_plan` 直接调 `create_quiz` —— 违反 D7 嵌套约定；用户看不到推理过程
- ❌ 出题前不调 `search_knowledge` —— 容易凭空编与 KB 无关的题
- ❌ 凭空编造 KB 里没有的事实题（如把"3GPP 标准号"编个假的）—— 用户最不能容忍的幻觉
- ❌ 题型比例随意（一次全简答 / 全单选）—— 必须 60% MCQ + 40% 简答
- ❌ 一次只塞 1-2 个 questions 多次调 `create_quiz` —— 会产生多个无关 quiz_set
- ❌ 用户给一段自然语言作答后**重新生成新 quiz**，而不是调 `grade_quiz` 批改既有 quiz
- ❌ 批改后 tool 已返回总分 + 错题，又**重新调用 chat 模型重批一次** —— 浪费 token
- ❌ 引导用户用不存在的 CLI 命令（如 `/quiz retry`、`/quiz again`）—— 当前只有 `list / show / del` 三个

## 用户呈现层模板

### 1. 题目呈现（`create_quiz` 落库后展示给用户）

```
✓ 已建好『<主题>』的 quiz（quiz_set_id=<id>，共 <N> 道：M 道 MCQ + K 道简答）

### 第 1 题（单选）
<题干>
A. <选项 A>
B. <选项 B>
C. <选项 C>
D. <选项 D>

### 第 2 题（多选）
<题干>
A. <…>
B. <…>
...

### 第 N 题（简答）
<题干>

——
作答时按 『1.B 2.AC 3. <文字答案>』格式发我，我自动批改 + 给反馈。
```

### 2. 批改结果呈现（`grade_quiz` 后展示给用户）

```
📝 quiz_set_id=<id>『<主题>』批改结果

🎯 总分 <X>/100  ✓ <做对题数>/<总题数>

✅ 全对的题：第 1, 2, 5 题
⚠️ 薄弱点：
  - 第 3 题（多选）：你答 A，正确 AC —— <考点提示>
  - 第 7 题（简答）：得分 0.4/1.0 —— <LLM 反馈>
     标答：<…>

下一步建议：
  - 错题集中在 <X 主题>，建议再看看 KB 里的 <相关章节>
  - 想重做 / 换主题 / 看错题详情都可以告诉我
```
