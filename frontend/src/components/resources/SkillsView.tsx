import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { listSkills } from '@/api/client'
import type { SkillsResponse } from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'

export function SkillsView() {
  const [data, setData] = useState<SkillsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await listSkills())
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
      title="Skills"
      subtitle="当前 .agenta/skills/ 加载的 SKILL.md 清单（只读；改文件后重启进程生效）"
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

      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm font-medium">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          已加载 ({data?.loaded.length ?? 0})
        </div>
        {loading ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : !data || data.loaded.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">无</p>
        ) : (
          <ul className="divide-y divide-border">
            {data.loaded.map((s) => (
              <li key={s.location} className="px-3 py-2 text-sm">
                <div className="font-medium">{s.name}</div>
                <div className="text-foreground/80">{s.description}</div>
                <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={s.location}>
                  {s.location}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {data && data.failed.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950">
          <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2 text-sm font-medium dark:border-amber-900">
            <AlertTriangle className="h-4 w-4 text-amber-600" />
            加载失败 ({data.failed.length})
          </div>
          <ul className="divide-y divide-amber-200 dark:divide-amber-900">
            {data.failed.map((f, idx) => (
              <li key={idx} className="px-3 py-2 text-sm">
                <div className="truncate font-mono text-[11px]" title={f.path}>
                  {f.path}
                </div>
                <div className="text-amber-900 dark:text-amber-200">{f.reason}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ResourcePage>
  )
}
