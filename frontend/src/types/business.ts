// Step 7：业务面板共享类型（plan / quiz / srs）

// ─── Plan ────────────────────────────────────────────────────────────

export type PlanTask = {
  id: number
  plan_id: number
  stage_idx: number
  order_idx: number
  title: string
  status: string
  note: string | null
  completed_at: string | null
}

export type PlanSummary = {
  id: number
  goal: string
  weeks: number
  status: string
  is_active: boolean
  created_at: string
  updated_at: string
  task_count: number
  done_count: number
}

export type Plan = {
  id: number
  goal: string
  weeks: number
  status: string
  is_active: boolean
  created_at: string
  updated_at: string
  tasks: PlanTask[]
}

export type PlanListResponse = {
  plans: PlanSummary[]
}

// ─── Quiz ────────────────────────────────────────────────────────────

export type QuizQuestion = {
  id: number
  quiz_set_id: number
  order_idx: number
  q_type: string
  stem: string
  options: string[]
  correct_answer: string | null
  explanation: string | null
  user_answer: string | null
  score: number
  feedback: string | null
  harness_flagged: boolean
}

export type QuizSetSummary = {
  id: number
  topic: string
  plan_id: number | null
  stage_idx: number | null
  num_questions: number
  status: string
  total_score: number | null
  created_at: string
  graded_at: string | null
  updated_at: string
}

export type QuizSet = QuizSetSummary & {
  questions: QuizQuestion[]
}

export type QuizListResponse = {
  quizzes: QuizSetSummary[]
}

// ─── SRS ─────────────────────────────────────────────────────────────

export type SRSCard = {
  id: number
  source_type: string
  source_ref: number | null
  front: string
  back: string
  note: string | null
  ease_factor: number
  interval_days: number
  repetitions: number
  lapses: number
  next_review_at: string
  last_reviewed_at: string | null
  status: string
  created_at: string
  updated_at: string
}

export type SRSCardListResponse = {
  cards: SRSCard[]
}

// ─── 任务状态 / Quiz 类型 中文标签 ─────────────────────────────────

export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待办',
  success: '已完成',
  skipped: '已跳过',
}

export const QUIZ_TYPE_LABELS: Record<string, string> = {
  mcq_single: '单选',
  mcq_multi: '多选',
  short_answer: '简答',
}

export const QUIZ_STATUS_LABELS: Record<string, string> = {
  created: '未答',
  in_progress: '答题中',
  graded: '已批改',
  archived: '已归档',
}

export const SRS_STATUS_LABELS: Record<string, string> = {
  active: '激活',
  suspended: '暂停',
  archived: '归档',
}

// ─── 写端点请求体 ─────────────────────────────────────────────────────

export type CreatePlanTaskInput = {
  stage_idx: number
  order_idx: number
  title: string
}

export type CreatePlanInput = {
  goal: string
  weeks: number
  tasks: CreatePlanTaskInput[]
}

export type QuizAnswerInput = {
  question_id: number
  answer: string
}

export type SRSRating = 'again' | 'hard' | 'good' | 'easy'

// 4 档评分中文标签 + 语义说明（用于按钮 + Tooltip）
export const SRS_RATING_LABELS: Record<SRSRating, string> = {
  again: '重来',
  hard: '困难',
  good: '良好',
  easy: '容易',
}

export const SRS_RATING_HINTS: Record<SRSRating, string> = {
  again: '完全没记住，很快重新出现',
  hard: '想起来但很吃力，间隔略缩短',
  good: '正常答对，按计划拉长间隔',
  easy: '太简单了，间隔拉得更长',
}
