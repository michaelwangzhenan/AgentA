import { useCallback, useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { listSRSCards, listSRSDue } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { SRS_STATUS_LABELS, type SRSCard } from '@/types/business'

function CardRow({ card }: { card: SRSCard }) {
  return (
    <li className="rounded-md border border-border p-3 text-sm">
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
          <div>已 review {card.repetitions} 次</div>
          {card.lapses > 0 && <div className="text-red-700">lapse {card.lapses}</div>}
        </div>
      </div>
      <div className="mt-1 text-[10px] text-muted-foreground">
        来源：{card.source_type}
        {card.source_ref ? ` (${card.source_ref})` : ''} · 下次 {card.next_review_at}
        {card.last_reviewed_at ? ` · 上次 ${card.last_reviewed_at}` : ''}
      </div>
    </li>
  )
}

export function SRSView() {
  const [due, setDue] = useState<SRSCard[]>([])
  const [all, setAll] = useState<SRSCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <ResourcePage
      title="SRS 间隔重复"
      subtitle="在 chat 里复习：让 LLM 调 query_srs_due 列卡，回答后用 review_srs_card 评分；这里只看进度"
      toolbar={
        <Button onClick={refresh} size="sm" variant="outline" disabled={loading}>
          刷新
        </Button>
      }
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-amber-200 bg-amber-50/40 dark:border-amber-900 dark:bg-amber-950/30">
        <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2 text-sm font-medium dark:border-amber-900">
          <Clock className="h-4 w-4 text-amber-600" />
          到期 due ({due.length})
        </div>
        {loading && due.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : due.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">
            没有到期卡片，继续保持
          </p>
        ) : (
          <ul className="space-y-2 p-3">
            {due.map((c) => (
              <CardRow key={c.id} card={c} />
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          全部卡片 ({all.length})
        </div>
        {loading && all.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : all.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">
            暂无 SRS 卡片。在 chat 里让 LLM 调 add_to_srs 添加，或基于 quiz 错题入队
          </p>
        ) : (
          <ul className="space-y-2 p-3">
            {all.map((c) => (
              <CardRow key={c.id} card={c} />
            ))}
          </ul>
        )}
      </div>
    </ResourcePage>
  )
}
