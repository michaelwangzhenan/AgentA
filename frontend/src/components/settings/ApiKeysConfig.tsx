import { useCallback, useEffect, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getApiKeys, resetApiKey, updateApiKey } from '@/api/client'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import type { ApiKeyView } from '@/types/apiKeys'

/** API 密钥配置：仅 admin 可见。后端永不返回明文，只展示脱敏串 + 是否已配置。
 *  保存后下一次 LLM 调用即生效，无需重启服务。 */
export function ApiKeysConfig() {
  const [items, setItems] = useState<ApiKeyView[]>([])
  const [loading, setLoading] = useState(true)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await getApiKeys())
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const save = async (id: string) => {
    const value = (drafts[id] ?? '').trim()
    if (!value) return
    setBusy(id)
    try {
      const updated = await updateApiKey(id, value)
      setItems((prev) => prev.map((it) => (it.id === id ? updated : it)))
      setDrafts((prev) => ({ ...prev, [id]: '' }))
      toast.success(`已更新 ${updated.label} 的 API key`)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const reset = async (item: ApiKeyView) => {
    setBusy(item.id)
    try {
      const updated = await resetApiKey(item.id)
      setItems((prev) => prev.map((it) => (it.id === item.id ? updated : it)))
      setDrafts((prev) => ({ ...prev, [item.id]: '' }))
      toast.success(`已恢复 ${updated.label} 为环境变量值`)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        出于安全，界面只显示脱敏后的尾段，不回显完整密钥。输入新值后点保存即可覆盖；
        已用 UI 覆盖的项可点恢复按钮回退到环境变量值。
      </p>

      <div className="space-y-2">
        {items.map((item) => {
          const draft = drafts[item.id] ?? ''
          const rowBusy = busy === item.id
          return (
            <div
              key={item.id}
              className="rounded-md border border-border p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{item.label}</span>
                    <span
                      className={cn(
                        'rounded px-1.5 py-0.5 text-[10px] font-medium',
                        item.configured
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                          : 'bg-muted text-muted-foreground',
                      )}
                    >
                      {item.configured ? '已配置' : '未配置'}
                    </span>
                    {item.source === 'override' && (
                      <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium text-accent-foreground">
                        UI 覆盖
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                    {item.configured ? item.masked : '—'}
                    <span className="ml-2 font-sans">{item.env}</span>
                  </div>
                </div>
                {item.source === 'override' && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title="恢复为环境变量值"
                    disabled={rowBusy}
                    onClick={() => void reset(item)}
                  >
                    <RotateCcw className="h-4 w-4" />
                  </Button>
                )}
              </div>

              <div className="mt-2 flex items-center gap-2">
                <Input
                  type="password"
                  value={draft}
                  placeholder="输入新 key 后保存"
                  autoComplete="off"
                  onChange={(e) =>
                    setDrafts((prev) => ({ ...prev, [item.id]: e.target.value }))
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void save(item.id)
                  }}
                />
                <Button
                  size="sm"
                  disabled={!draft.trim() || rowBusy}
                  onClick={() => void save(item.id)}
                >
                  <Check className="mr-1 h-3.5 w-3.5" />
                  保存
                </Button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
