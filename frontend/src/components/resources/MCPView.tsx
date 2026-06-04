import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { listMCPServers, listMCPTools } from '@/api/client'
import type { MCPServer, MCPTool } from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'

function statusBadge(status: string) {
  if (status === 'connected') {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-green-50 px-1.5 py-0.5 text-[10px] text-green-900 dark:bg-green-950 dark:text-green-100">
        <CheckCircle2 className="h-3 w-3" />
        connected
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-900 dark:bg-red-950 dark:text-red-100">
        <AlertCircle className="h-3 w-3" />
        failed
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      <Loader2 className="h-3 w-3 animate-spin" />
      {status}
    </span>
  )
}

export function MCPView() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [tools, setTools] = useState<MCPTool[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, t] = await Promise.all([listMCPServers(), listMCPTools()])
      setServers(s)
      setTools(t)
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
      title="MCP Servers"
      subtitle="进程级 MCP 客户端连接状态（只读；server 配置改 .agenta/mcp.json 后重启进程生效）"
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
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          Server 列表 ({servers.length})
        </div>
        {loading ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : servers.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">
            未配置 server；可在 .agenta/mcp.json 添加
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {servers.map((s) => (
              <li key={s.name} className="px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  {statusBadge(s.status)}
                  <span className="text-[10px] text-muted-foreground">
                    {s.tool_count} tools
                  </span>
                </div>
                {s.command && (
                  <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={s.command}>
                    {s.command}
                  </div>
                )}
                {s.error && (
                  <div className="mt-0.5 text-[11px] text-red-700 dark:text-red-300">
                    {s.error}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card">
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          工具清单 ({tools.length})
        </div>
        {loading ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
        ) : tools.length === 0 ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">无</p>
        ) : (
          <ul className="divide-y divide-border">
            {tools.map((t) => (
              <li key={t.name} className="px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono font-medium">{t.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {t.server}
                  </span>
                </div>
                {t.description && (
                  <div className="text-foreground/80">{t.description}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </ResourcePage>
  )
}
