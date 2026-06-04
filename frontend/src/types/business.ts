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
