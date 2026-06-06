import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { archiveQuiz, getQuiz, listQuizzes, submitQuiz } from '@/api/client'
import {
  QUIZ_STATUS_LABELS,
  QUIZ_TYPE_LABELS,
  type QuizAnswerInput,
  type QuizQuestion,
  type QuizSet,
  type QuizSetSummary,
} from '@/types/business'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'

const MCQ_TYPES = ['mcq_single', 'mcq_multi']

function letterFor(index: number): string {
  return String.fromCharCode(65 + index)
}

// 已批改：只读展示题目 + 得分
function GradedQuestion({ q }: { q: QuizQuestion }) {
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
            <ul className="ml-1 space-y-0.5 text-foreground/80">
              {q.options.map((opt, i) => (
                <li key={i}>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {letterFor(i)}.
                  </span>{' '}
                  {opt}
                </li>
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

// 答题模式：单题录入控件
function AnswerQuestion({
  q,
  value,
  onChange,
}: {
  q: QuizQuestion
  value: string
  onChange: (next: string) => void
}) {
  const isMulti = q.q_type === 'mcq_multi'
  const isMcq = MCQ_TYPES.includes(q.q_type)

  const toggleLetter = (letter: string) => {
    if (isMulti) {
      const set = new Set(value.split(''))
      if (set.has(letter)) set.delete(letter)
      else set.add(letter)
      onChange(Array.from(set).sort().join(''))
    } else {
      onChange(letter)
    }
  }

  return (
    <li className="rounded-md border border-border p-3 text-sm">
      <div className="flex items-start gap-2">
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
          {q.order_idx}
        </span>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-center gap-1">
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {QUIZ_TYPE_LABELS[q.q_type] ?? q.q_type}
            </span>
          </div>
          <div className="font-medium">{q.stem}</div>
          {isMcq ? (
            <ul className="space-y-1">
              {q.options.map((opt, i) => {
                const letter = letterFor(i)
                const checked = value.includes(letter)
                return (
                  <li key={i}>
                    <button
                      type="button"
                      onClick={() => toggleLetter(letter)}
                      className={cn(
                        'flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left',
                        checked
                          ? 'border-primary bg-primary/10'
                          : 'border-border hover:bg-accent/60',
                      )}
                    >
                      <span
                        className={cn(
                          'flex h-5 w-5 shrink-0 items-center justify-center font-mono text-[11px]',
                          isMulti ? 'rounded' : 'rounded-full',
                          checked
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground',
                        )}
                      >
                        {letter}
                      </span>
                      <span>{opt}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <Textarea
              value={value}
              onChange={(e) => onChange(e.target.value)}
              placeholder="作答…"
              rows={3}
            />
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
  const [error, setError] = useState<string | null>(null)

  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState<QuizSetSummary | null>(null)

  const refreshList = useCallback(async (preferId?: number) => {
    setLoadingList(true)
    setError(null)
    try {
      const items = await listQuizzes()
      setList(items)
      setSelectedId((prev) => {
        if (preferId !== undefined) return preferId
        if (prev !== null && items.some((q) => q.id === prev)) return prev
        return items[0]?.id ?? null
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingList(false)
    }
  }, [])

  useEffect(() => {
    refreshList()
  }, [refreshList])

  const reloadDetail = useCallback(async (id: number) => {
    try {
      const q = await getQuiz(id)
      setSelected(q)
      setAnswers({})
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null)
      return
    }
    reloadDetail(selectedId)
  }, [selectedId, reloadDetail])

  const isAnswering = selected !== null && selected.status === 'created'
  const answeredCount = useMemo(
    () => Object.values(answers).filter((v) => v.trim() !== '').length,
    [answers],
  )

  const submit = async () => {
    if (!selected) return
    const payload: QuizAnswerInput[] = selected.questions.map((q) => ({
      question_id: q.id,
      answer: answers[q.id] ?? '',
    }))
    setSubmitting(true)
    try {
      const graded = await submitQuiz(selected.id, payload)
      setSelected(graded)
      setAnswers({})
      toast.success(`已批改，总分 ${graded.total_score ?? 0} 分`)
      refreshList(selected.id)
    } catch (e) {
      toast.error(`提交失败：${(e as Error).message}`)
    } finally {
      setSubmitting(false)
    }
  }

  const confirmArchive = async () => {
    if (!archiveTarget) return
    try {
      await archiveQuiz(archiveTarget.id)
      toast.success('已归档')
      setArchiveTarget(null)
      await refreshList()
    } catch (e) {
      toast.error(`归档失败：${(e as Error).message}`)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          在聊天里让 AI 基于知识库出题（"考我 5 道 attention 机制的题"），出好的题会出现在这里，直接作答提交即可自动批改。
        </p>
        <Button onClick={() => refreshList()} size="sm" variant="outline" disabled={loadingList}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      {list.length === 0 && !loadingList && (
        <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
          还没有测验。去聊天里说"用我的知识库给我出 5 道题，主题：…"，AI 出好后回到这里作答。
        </div>
      )}

      {list.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[280px_minmax(0,1fr)]">
          <div className="rounded-lg border border-border bg-card">
            <div className="border-b border-border px-3 py-2 text-sm font-medium">
              全部测验 ({list.length})
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
            {selected && (
              <>
                <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2">
                  <div className="min-w-0">
                    <h2 className="truncate font-semibold" title={selected.topic}>
                      {selected.topic}
                    </h2>
                    <div className="text-[10px] text-muted-foreground">
                      {QUIZ_STATUS_LABELS[selected.status] ?? selected.status} · 创建于{' '}
                      {selected.created_at}
                      {selected.total_score !== null ? ` · 总分 ${selected.total_score}` : ''}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {isAnswering && (
                      <Button size="sm" onClick={submit} disabled={submitting}>
                        {submitting ? '批改中...' : `提交批改 (${answeredCount}/${selected.questions.length})`}
                      </Button>
                    )}
                    {selected.status !== 'archived' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => setArchiveTarget(list.find((q) => q.id === selected.id) ?? null)}
                      >
                        归档
                      </Button>
                    )}
                  </div>
                </div>
                {isAnswering ? (
                  <ul className="space-y-2 p-3">
                    {selected.questions.map((q) => (
                      <AnswerQuestion
                        key={q.id}
                        q={q}
                        value={answers[q.id] ?? ''}
                        onChange={(next) =>
                          setAnswers((prev) => ({ ...prev, [q.id]: next }))
                        }
                      />
                    ))}
                  </ul>
                ) : (
                  <ul className="space-y-2 p-3">
                    {selected.questions.map((q) => (
                      <GradedQuestion key={q.id} q={q} />
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      )}

      <AlertDialog
        open={archiveTarget !== null}
        onOpenChange={(o: boolean) => !o && setArchiveTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>归档该测验？</AlertDialogTitle>
            <AlertDialogDescription>
              "{archiveTarget?.topic}" 将从列表中隐藏。数据会保留，可通过聊天查询历史。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={confirmArchive}>归档</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
