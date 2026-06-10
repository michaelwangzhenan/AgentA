import { useCallback, useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/button'
import { MarkdownPreview } from '@/components/ui/markdown-preview'
import { getReportContent, getReports } from '@/api/client'
import type { ReportItem } from '@/types/eval'

export function ReportsViewer() {
  const [reports, setReports] = useState<ReportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setReports((await getReports()).reports)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const open = async (name: string) => {
    setSelected(name)
    try {
      setContent((await getReportContent(name)).content)
    } catch (e) {
      toast.error((e as Error).message)
      setContent('')
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">报告列表</h2>
          <Button variant="outline" size="sm" onClick={refresh}>
            刷新
          </Button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto rounded-md border border-border">
          {reports.length > 0 ? (
            <ul>
              {reports.map((r) => (
                <li key={r.name}>
                  <button
                    onClick={() => open(r.name)}
                    className={cn(
                      'w-full border-b border-border px-3 py-2 text-left text-xs last:border-0 hover:bg-accent/40',
                      selected === r.name && 'bg-accent',
                    )}
                  >
                    <div className="truncate font-medium">{r.name}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {new Date(r.modified_at * 1000).toLocaleString()}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              {loading ? '加载中…' : '暂无报告（跑 python -m tools.agent_eval.run_all 后生成）'}
            </p>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-border p-4">
        {selected ? (
          <MarkdownPreview source={content} />
        ) : (
          <p className="text-sm text-muted-foreground">从左侧选择一份报告查看</p>
        )}
      </div>
    </div>
  )
}
