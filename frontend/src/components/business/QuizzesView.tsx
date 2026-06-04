import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { getQuiz, listQuizzes } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import {
  QUIZ_STATUS_LABELS,
  QUIZ_TYPE_LABELS,
  type QuizQuestion,
  type QuizSet,
  type QuizSetSummary,
} from '@/types/business'
import { cn } from '@/lib/utils'

function QuestionCard({ q }: { q: QuizQuestion }) {
  const answered = q.user_answer !== null && q.user_answer !== ''
  const correct = answered && q.score > 0
  return (
    <li className="rounded-md border border-border p-3 text-sm">
      <div className="flex items-start gap-2">
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
          {q.order_idx}
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1">
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {QUIZ_TYPE_LABELS[q.q_type] ?? q.q_type}
            </span>
            {answered && (
              <span
                className={
                  'rounded px-1.5 py-0.5 text-[10px] ' +
                  (correct
                    ? 'bg-green-50 text-green-900 dark:bg-green-950 dark:text-green-100'
                    : 'bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-100')
                }
              >
                {correct ? `✓ ${q.score}` : `✗ ${q.score}`}
              </span>
            )}
          </div>
          <div className="font-medium">{q.stem}</div>
          {q.options.length > 0 && (
            <ul className="ml-3 list-disc space-y-0.5 text-foreground/80">
              {q.options.map((opt, i) => (
                <li key={i}>{opt}</li>
              ))}
            </ul>
          )}
          {q.user_answer && (
            <div className="text-foreground/80">
              <span className="text-[10px] text-muted-foreground">你的回答：</span>
              {q.user_answer}
            </div>
          )}
          {q.correct_answer && (
            <div className="text-foreground/80">
              <span className="text-[10px] text-muted-foreground">参考答案：</span>
              {q.correct_answer}
            </div>
          )}
          {q.feedback && (
            <div className="text-foreground/80">
              <span className="text-[10px] text-muted-foreground">反馈：</span>
              {q.feedback}
            </div>
          )}
          {q.explanation && (
            <div className="text-[11px] text-muted-foreground">
              <span className="text-[10px]">解释：</span>
              {q.explanation}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

export function QuizzesView() {
  const [list, setList] = useState<QuizSetSummary[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selected, setSelected] = useState<QuizSet | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshList = useCallback(async () => {
    setLoadingList(true)
    setError(null)
    try {
      const items = await listQuizzes()
      setList(items)
      if (selectedId === null && items.length > 0) {
        setSelectedId(items[0].id)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingList(false)
    }
  }, [selectedId])

  useEffect(() => {
    refreshList()
  }, [refreshList])

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoadingDetail(true)
      try {
        const q = await getQuiz(selectedId)
        if (!cancelled) setSelected(q)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      } finally {
        if (!cancelled) setLoadingDetail(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId])

  return (
    <ResourcePage
      title="Quiz 历史"
      subtitle="在 chat 里让 LLM 调 create_quiz 出题，回答后调 grade_quiz 批改；这里只看结果"
      toolbar={
        <Button
          onClick={refreshList}
          size="sm"
          variant="outline"
          disabled={loadingList}
        >
          刷新
        </Button>
      }
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}
      {list.length === 0 && !loadingList && (
        <p className="text-sm text-muted-foreground">
          暂无 quiz。去 chat 让 LLM 出题："考我 5 道 attention 机制的题"
        </p>
      )}
      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[280px_minmax(0,1fr)]">
          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-3 py-2 text-sm font-medium">
              全部 quiz ({list.length})
            </div>
            <ul className="divide-y divide-border">
              {list.map((q) => (
                <li key={q.id}>
                  <button
                    onClick={() => setSelectedId(q.id)}
                    className={cn(
                      'flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-accent/60',
                      selectedId === q.id && 'bg-accent text-accent-foreground',
                    )}
                  >
                    <div className="truncate font-medium" title={q.topic}>
                      {q.topic}
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      {q.num_questions} 题 · {QUIZ_STATUS_LABELS[q.status] ?? q.status}
                      {q.total_score !== null ? ` · ${q.total_score} 分` : ''}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="min-w-0 rounded-lg border border-border bg-card">
            {loadingDetail && !selected && (
              <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
            )}
            {selected && (
              <>
                <div className="border-b border-border px-3 py-2">
                  <h2 className="font-semibold">{selected.topic}</h2>
                  <div className="text-[10px] text-muted-foreground">
                    {QUIZ_STATUS_LABELS[selected.status] ?? selected.status} · 创建于{' '}
                    {selected.created_at}
                    {selected.graded_at ? ` · 批改于 ${selected.graded_at}` : ''}
                    {selected.total_score !== null
                      ? ` · 总分 ${selected.total_score}`
                      : ''}
                  </div>
                </div>
                <ul className="space-y-2 p-3">
                  {selected.questions.map((q) => (
                    <QuestionCard key={q.id} q={q} />
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      )}
    </ResourcePage>
  )
}
