import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { getRoutingPool, putRoutingPool } from '@/api/client'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import type { RoutingModel, RoutingPoolResponse } from '@/types/routing'

/** 模型路由候选池：仅 admin。勾选"已充值可用"的模型，路由只在池内向更便宜的模型降级。
 *  不勾选任何项时回落到"已配 api_key"的全部模型。 */
export function RoutingPoolConfig() {
  const [data, setData] = useState<RoutingPoolResponse | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await getRoutingPool()
      setData(resp)
      setSelected(new Set(resp.models.filter((m) => m.selected).map((m) => m.model_id)))
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const byProvider = useMemo(() => {
    const groups: Record<string, { label: string; models: RoutingModel[] }> = {}
    for (const m of data?.models ?? []) {
      const g = (groups[m.provider] ??= { label: m.provider_label, models: [] })
      g.models.push(m)
    }
    return Object.values(groups)
  }, [data])

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const save = async () => {
    setSaving(true)
    try {
      const resp = await putRoutingPool([...selected])
      setData(resp)
      setSelected(new Set(resp.models.filter((m) => m.selected).map((m) => m.model_id)))
      toast.success('候选池已保存')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        勾选已充值、可用的模型纳入路由候选池。路由（当前
        {data?.enabled ? '已启用' : '未启用'}，模式 <code>{data?.mode}</code>）只会在池内向
        <strong>更便宜的档位</strong>降级，不会越过用户所选模型向上。
        {!data?.configured && '（当前未显式配置，已回落到所有「已配 API key」的模型）'}
      </p>

      <div className="space-y-3">
        {byProvider.map((g) => (
          <div key={g.label} className="rounded-md border border-border p-3">
            <div className="mb-2 text-xs font-medium text-muted-foreground">{g.label}</div>
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
              {g.models.map((m) => (
                <label
                  key={m.model_id}
                  className={cn(
                    'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-foreground/5',
                    !m.available && 'opacity-60',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(m.model_id)}
                    onChange={() => toggle(m.model_id)}
                    className="h-4 w-4"
                  />
                  <span className="flex-1 truncate">{m.label}</span>
                  <span
                    className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    title="价格档位：越靠 min 越便宜，路由按此向更便宜档位降级"
                  >
                    {m.tier}
                  </span>
                  {!m.available && (
                    <span
                      className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-600 dark:text-amber-400"
                      title="该模型所属厂商尚未配置 API key，需先在「API 密钥」页配置"
                    >
                      未配 key
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={saving} onClick={() => void save()}>
          <Check className="mr-1 h-3.5 w-3.5" />
          保存候选池
        </Button>
        <span className="text-xs text-muted-foreground">已选 {selected.size} 个</span>
      </div>
    </div>
  )
}
