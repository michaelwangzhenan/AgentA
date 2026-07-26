import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toast } from '@/lib/toast'
import { useWriteScope } from '@/lib/permissions'
import { getPricing, putPricing } from '@/api/client'
import type { PricingItem } from '@/types/usage'

type Draft = Record<string, { input: string; output: string }>

export function PricingConfig() {
  const { allowed: canWriteUsage, tip: usageTip } = useWriteScope('usage')
  const [items, setItems] = useState<PricingItem[]>([])
  const [currency, setCurrency] = useState('¥')
  const [draft, setDraft] = useState<Draft>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getPricing()
      setItems(res.items)
      setCurrency(res.currency)
      const d: Draft = {}
      for (const it of res.items) {
        d[it.model_id] = { input: String(it.input_price), output: String(it.output_price) }
      }
      setDraft(d)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 按 provider 分组展示
  const grouped = useMemo(() => {
    const m = new Map<string, { label: string; rows: PricingItem[] }>()
    for (const it of items) {
      const g = m.get(it.provider) ?? { label: it.provider_label, rows: [] }
      g.rows.push(it)
      m.set(it.provider, g)
    }
    return [...m.values()]
  }, [items])

  // 算出相对当前生效值有改动的项
  const changed = useMemo(() => {
    const out: { model_id: string; input_price: number; output_price: number }[] = []
    for (const it of items) {
      const d = draft[it.model_id]
      if (!d) continue
      const pin = Number(d.input)
      const pout = Number(d.output)
      if (Number.isNaN(pin) || Number.isNaN(pout) || pin < 0 || pout < 0) continue
      if (pin !== it.input_price || pout !== it.output_price) {
        out.push({ model_id: it.model_id, input_price: pin, output_price: pout })
      }
    }
    return out
  }, [items, draft])

  const setField = (modelId: string, field: 'input' | 'output', value: string) => {
    if (!canWriteUsage) return
    setDraft((prev) => ({ ...prev, [modelId]: { ...prev[modelId], [field]: value } }))
  }

  const save = async () => {
    if (changed.length === 0) return
    setSaving(true)
    try {
      const res = await putPricing(changed)
      setItems(res.items)
      setCurrency(res.currency)
      toast.success(`已保存 ${changed.length} 项单价`)
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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          单价为每 100 万（1M）token 的价格，币种 {currency}。默认值为内置参考价（2026-06
          快照，国产厂商按 ¥ 折算）；修改后覆盖默认值，用于成本估算。
        </p>
        <Button size="sm" disabled={!canWriteUsage || saving || changed.length === 0} title={canWriteUsage ? undefined : usageTip} onClick={save}>
          {changed.length > 0 ? `保存（${changed.length}）` : '保存'}
        </Button>
      </div>

      {grouped.map((g) => (
        <section key={g.label} className="overflow-hidden rounded-md border border-border">
          <div className="border-b border-border bg-muted/50 px-3 py-1.5 text-xs font-medium text-muted-foreground">
            {g.label}
          </div>
          <table className="w-full table-fixed text-sm">
            <colgroup>
              {/* 模型列吃剩余宽度，其余列固定 —— 保证各 provider 表第 2/3/4 列横向对齐 */}
              <col />
              <col className="w-36" />
              <col className="w-36" />
              <col className="w-28" />
            </colgroup>
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">模型</th>
                <th className="px-3 py-2 font-medium">输入 / 1M</th>
                <th className="px-3 py-2 font-medium">输出 / 1M</th>
                <th className="px-3 py-2 font-medium">来源</th>
              </tr>
            </thead>
            <tbody>
              {g.rows.map((it) => {
                const d = draft[it.model_id] ?? { input: '', output: '' }
                return (
                  <tr key={it.model_id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2">
                      <div>{it.label}</div>
                      <div className="text-[11px] text-muted-foreground">{it.model_id}</div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1">
                        <span className="text-muted-foreground">{currency}</span>
                        <Input
                          type="number"
                          min={0}
                          step="0.01"
                          value={d.input}
                          onChange={(e) => setField(it.model_id, 'input', e.target.value)}
                          readOnly={!canWriteUsage}
                          disabled={!canWriteUsage}
                          className="h-7 w-24"
                        />
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1">
                        <span className="text-muted-foreground">{currency}</span>
                        <Input
                          type="number"
                          min={0}
                          step="0.01"
                          value={d.output}
                          onChange={(e) => setField(it.model_id, 'output', e.target.value)}
                          readOnly={!canWriteUsage}
                          disabled={!canWriteUsage}
                          className="h-7 w-24"
                        />
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {it.is_override ? (
                        <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-accent-foreground">
                          已自定义
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">默认</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  )
}
