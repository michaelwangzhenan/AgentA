import { useCallback, useEffect, useState } from 'react'
import { Clock, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  createSRSCard,
  listSRSCards,
  listSRSDue,
  reviewSRSCard,
  setSRSCardStatus,
} from '@/api/client'
import {
  SRS_RATING_HINTS,
  SRS_RATING_LABELS,
  SRS_STATUS_LABELS,
  type SRSCard,
  type SRSRating,
} from '@/types/business'
import { toast } from '@/lib/toast'
import { useWriteScope } from '@/lib/permissions'

const RATING_ORDER: SRSRating[] = ['again', 'hard', 'good', 'easy']

// due 复习器：翻面 + 4 档评分，逐张过
function Reviewer({
  cards,
  onReviewed,
  readOnly = false,
  writeTip,
}: {
  cards: SRSCard[]
  onReviewed: () => void
  readOnly?: boolean
  writeTip?: string
}) {
  const [idx, setIdx] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState(false)

  // cards 刷新后从头开始
  useEffect(() => {
    setIdx(0)
    setRevealed(false)
  }, [cards])

  if (cards.length === 0) {
    return (
      <p className="px-3 py-2 text-sm text-muted-foreground">没有到期卡片，继续保持 🎉</p>
    )
  }
  if (idx >= cards.length) {
    return (
      <div className="px-3 py-4 text-center text-sm">
        <p className="text-muted-foreground">本轮 {cards.length} 张已复习完 🎉</p>
        <Button size="sm" variant="outline" className="mt-2" onClick={onReviewed}>
          再查一次到期卡
        </Button>
      </div>
    )
  }

  const card = cards[idx]

  const rate = async (rating: SRSRating) => {
    setBusy(true)
    try {
      await reviewSRSCard(card.id, rating)
      if (idx + 1 >= cards.length) {
        // 最后一张：通知父组件刷新（会重置 idx）
        onReviewed()
      } else {
        setIdx((i) => i + 1)
        setRevealed(false)
      }
    } catch (e) {
      toast.error(`评分失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-3">
      <div className="mb-2 text-[10px] text-muted-foreground">
        第 {idx + 1} / {cards.length} 张
      </div>
      <div className="rounded-md border border-border bg-background p-4">
        <div className="text-sm font-medium">{card.front}</div>
        {revealed ? (
          <div className="mt-3 border-t border-border pt-3 text-sm text-foreground/80 whitespace-pre-wrap">
            {card.back}
            {card.note && (
              <div className="mt-1 text-[11px] text-muted-foreground">备注：{card.note}</div>
            )}
          </div>
        ) : (
          <div className="mt-3">
            <Button size="sm" variant="outline" onClick={() => setRevealed(true)}>
              显示答案
            </Button>
          </div>
        )}
      </div>

      {revealed && (
        <div className="mt-3 grid grid-cols-4 gap-2">
          {RATING_ORDER.map((r) => (
            <Button
              key={r}
              size="sm"
              variant="outline"
              disabled={busy || readOnly}
              title={readOnly ? writeTip : SRS_RATING_HINTS[r]}
              onClick={() => rate(r)}
            >
              {SRS_RATING_LABELS[r]}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}

function CardRow({
  card,
  onStatus,
  readOnly = false,
  writeTip,
}: {
  card: SRSCard
  onStatus: (id: number, action: 'suspend' | 'resume' | 'archive') => void
  readOnly?: boolean
  writeTip?: string
}) {
  return (
    <li className="group rounded-md border border-border p-3 text-sm">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="font-medium">{card.front}</div>
          <div className="text-foreground/80">{card.back}</div>
          {card.note && (
            <div className="text-[11px] text-muted-foreground">备注：{card.note}</div>
          )}
        </div>
        <div className="shrink-0 text-right text-[10px] text-muted-foreground">
          <div>{SRS_STATUS_LABELS[card.status] ?? card.status}</div>
          <div>ease {card.ease_factor.toFixed(2)}</div>
          <div>间隔 {card.interval_days}d</div>
          <div>已复习 {card.repetitions} 次</div>
          {card.lapses > 0 && <div className="text-red-700">忘记 {card.lapses} 次</div>}
        </div>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <div className="text-[10px] text-muted-foreground">
          来源：{card.source_type}
          {card.source_ref ? ` (${card.source_ref})` : ''} · 下次 {card.next_review_at}
        </div>
        <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {card.status === 'active' && (
            <button
              className="rounded px-1.5 py-0.5 text-[10px] hover:bg-accent disabled:opacity-40"
              disabled={readOnly}
              title={readOnly ? writeTip : undefined}
              onClick={() => onStatus(card.id, 'suspend')}
            >
              暂停
            </button>
          )}
          {card.status === 'suspended' && (
            <button
              className="rounded px-1.5 py-0.5 text-[10px] hover:bg-accent disabled:opacity-40"
              disabled={readOnly}
              title={readOnly ? writeTip : undefined}
              onClick={() => onStatus(card.id, 'resume')}
            >
              恢复
            </button>
          )}
          {card.status !== 'archived' && (
            <button
              className="rounded px-1.5 py-0.5 text-[10px] text-destructive hover:bg-accent disabled:opacity-40"
              disabled={readOnly}
              title={readOnly ? writeTip : undefined}
              onClick={() => onStatus(card.id, 'archive')}
            >
              归档
            </button>
          )}
        </div>
      </div>
    </li>
  )
}

export function SRSView() {
  const { allowed: canWriteMemory, tip: memoryTip } = useWriteScope('memory')
  const [due, setDue] = useState<SRSCard[]>([])
  const [all, setAll] = useState<SRSCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [addOpen, setAddOpen] = useState(false)
  const [addFront, setAddFront] = useState('')
  const [addBack, setAddBack] = useState('')
  const [adding, setAdding] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [d, a] = await Promise.all([listSRSDue(), listSRSCards()])
      setDue(d)
      setAll(a)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const resetAddForm = () => {
    setAddFront('')
    setAddBack('')
  }

  const submitAdd = async () => {
    const front = addFront.trim()
    const back = addBack.trim()
    if (!front || !back) return
    setAdding(true)
    try {
      await createSRSCard({ front, back })
      toast.success('已新建卡片，立即可复习')
      setAddOpen(false)
      resetAddForm()
      await refresh()
    } catch (e) {
      toast.error(`新建失败：${(e as Error).message}`)
    } finally {
      setAdding(false)
    }
  }

  const handleStatus = async (
    id: number,
    action: 'suspend' | 'resume' | 'archive',
  ) => {
    try {
      await setSRSCardStatus(id, action)
      await refresh()
    } catch (e) {
      toast.error(`操作失败：${(e as Error).message}`)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          间隔重复：答得越熟，下次出现的间隔越长。到期的卡会进"待复习"，逐张翻面打分即可。
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <Button onClick={() => setAddOpen(true)} size="sm" disabled={!canWriteMemory} title={canWriteMemory ? undefined : memoryTip}>
            <Plus className="mr-1 h-4 w-4" />
            新建卡片
          </Button>
          <Button onClick={refresh} size="sm" variant="outline" disabled={loading}>
            刷新
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-amber-200 bg-amber-50/40 dark:border-amber-900 dark:bg-amber-950/30">
        <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2 text-sm font-medium dark:border-amber-900">
          <Clock className="h-4 w-4 text-amber-600" />
          待复习 ({due.length})
        </div>
        {loading && due.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : (
          <Reviewer cards={due} onReviewed={refresh} readOnly={!canWriteMemory} writeTip={memoryTip} />
        )}
      </div>

      <div className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          全部卡片 ({all.length})
        </div>
        {loading && all.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : all.length === 0 ? (
          <div className="px-3 py-8 text-center text-sm text-muted-foreground">
            还没有复习卡。点"新建卡片"手动加，或在测验里把错题转成复习卡。
          </div>
        ) : (
          <ul className="space-y-2 p-3">
            {all.map((c) => (
              <CardRow key={c.id} card={c} onStatus={handleStatus} readOnly={!canWriteMemory} writeTip={memoryTip} />
            ))}
          </ul>
        )}
      </div>

      <Dialog
        open={addOpen}
        onOpenChange={(o: boolean) => {
          if (adding) return
          setAddOpen(o)
          if (!o) resetAddForm()
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新建复习卡</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                正面 <span className="text-muted-foreground/70">（问题 / 提示）</span>
              </label>
              <Input
                value={addFront}
                onChange={(e) => setAddFront(e.target.value)}
                placeholder="什么是 RAG？"
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                背面 <span className="text-muted-foreground/70">（答案 / 要点）</span>
              </label>
              <Textarea
                value={addBack}
                onChange={(e) => setAddBack(e.target.value)}
                placeholder="检索增强生成：先检索知识库相关片段，再让模型据此作答。"
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddOpen(false)
                resetAddForm()
              }}
              disabled={adding}
            >
              取消
            </Button>
            <Button
              onClick={submitAdd}
              disabled={!canWriteMemory || !addFront.trim() || !addBack.trim() || adding}
              title={canWriteMemory ? undefined : memoryTip}
            >
              {adding ? '新建中...' : '新建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
