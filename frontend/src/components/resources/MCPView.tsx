import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  ArrowDownAZ,
  ArrowUpAZ,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  PauseCircle,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
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
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  createMCPServer,
  deleteMCPServer,
  listMCPServers,
  listMCPTools,
  reloadMCPServers,
  renameMCPServer,
  toggleMCPServer,
  updateMCPServer,
} from '@/api/client'
import type { MCPServer, MCPTool } from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { useUrlState } from '@/routes/useUrlState'

const NAME_PATTERN = /^[a-zA-Z0-9_-]+$/

type SortDir = 'asc' | 'desc'

// ============================================================================
// 顶层视图
// ============================================================================

export function MCPView() {
  const url = useUrlState()
  const [servers, setServers] = useState<MCPServer[]>([])
  const [tools, setTools] = useState<MCPTool[]>([])
  const [loading, setLoading] = useState(true)
  const [reloading, setReloading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const expanded = useMemo(() => new Set(url.getCsv('open')), [url.searchParams])
  const [editing, setEditing] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)

  const query = url.get('q')
  const sortDir: SortDir = url.get('sort') === 'desc' ? 'desc' : 'asc'
  const setQuery = (v: string) => url.patch({ q: v || null })
  const setSortDir = (d: SortDir) => url.patch({ sort: d === 'asc' ? null : d })

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
    void refresh()
  }, [refresh])

  const handleReload = async () => {
    setReloading(true)
    try {
      const resp = await reloadMCPServers()
      const parts = [
        `${resp.connected} 个已连接`,
        resp.failed > 0 ? `${resp.failed} 个失败` : null,
        resp.total - resp.enabled > 0
          ? `${resp.total - resp.enabled} 个已禁用`
          : null,
      ].filter(Boolean)
      toast.success(`已重新加载：${parts.join('，')}。新对话立即生效。`)
      await refresh()
    } catch (e) {
      toast.error(`重载失败：${(e as Error).message}`)
    } finally {
      setReloading(false)
    }
  }

  const toggleExpand = (name: string) => {
    const next = new Set(expanded)
    if (next.has(name)) {
      next.delete(name)
      if (editing === name) setEditing(null)
    } else {
      next.add(name)
    }
    const arr = [...next]
    url.patch({ open: arr.length ? arr.join(',') : null })
  }

  const handleToggle = async (name: string, enabled: boolean) => {
    try {
      await toggleMCPServer(name, { enabled })
      toast.success(`${name} 已${enabled ? '启用' : '禁用'}。`)
      await refresh()
    } catch (e) {
      toast.error(`切换失败：${(e as Error).message}`)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteMCPServer(deleteTarget)
      toast.success(`已删除 server：${deleteTarget}`)
      setDeleteTarget(null)
      await refresh()
    } catch (e) {
      toast.error(`删除失败：${(e as Error).message}`)
    }
  }

  // 三段：connected → 状态在 connected 且 enabled；disabled → enabled=false；failed → status=failed
  const grouped = useMemo(() => groupServers(servers), [servers])
  const filteredEnabled = useMemo(
    () => filterAndSort(grouped.enabled, query, sortDir),
    [grouped.enabled, query, sortDir],
  )
  const filteredDisabled = useMemo(
    () => filterAndSort(grouped.disabled, query, sortDir),
    [grouped.disabled, query, sortDir],
  )
  const filteredFailed = useMemo(
    () => filterAndSort(grouped.failed, query, sortDir),
    [grouped.failed, query, sortDir],
  )

  // 按 server 分组的 tools，行展开时按需引用
  const toolsByServer = useMemo(() => {
    const m = new Map<string, MCPTool[]>()
    for (const t of tools) {
      const arr = m.get(t.server) ?? []
      arr.push(t)
      m.set(t.server, arr)
    }
    return m
  }, [tools])

  const busy = loading || reloading
  const existingNames = new Set(servers.map((s) => s.name))

  const sectionToolbar = (
    <>
      <SearchBox value={query} onChange={setQuery} disabled={busy} />
      <Button
        onClick={() => setSortDir(sortDir === 'asc' ? 'desc' : 'asc')}
        size="sm"
        variant="outline"
        disabled={busy}
        className="gap-1.5"
        aria-label={`按名称排序，当前 ${sortDir === 'asc' ? '升序' : '降序'}`}
      >
        {sortDir === 'asc' ? (
          <ArrowDownAZ className="h-3.5 w-3.5" />
        ) : (
          <ArrowUpAZ className="h-3.5 w-3.5" />
        )}
        名称 {sortDir === 'asc' ? 'A→Z' : 'Z→A'}
      </Button>
      <Button
        onClick={() => setCreateOpen(true)}
        size="sm"
        disabled={busy}
        className="gap-1.5"
      >
        <Plus className="h-3.5 w-3.5" />
        新建 Server
      </Button>
      <Button
        onClick={handleReload}
        size="sm"
        variant="outline"
        disabled={busy}
        className="gap-1.5"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${reloading ? 'animate-spin' : ''}`} />
        {reloading ? '重载中…' : '重新加载'}
      </Button>
    </>
  )

  return (
    <ResourcePage
      title="MCP Servers"
      subtitle="管理 .agenta/mcp/config.json 下的 MCP server（点击行查看详情；可编辑 / 改名 / 新建 / 删除 / 启停；改动实时生效）"
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      <MCPSection
        title="已启用"
        icon={<CheckCircle2 className="h-4 w-4 text-green-600" />}
        items={filteredEnabled}
        loading={loading}
        emptyMessage={query ? '无匹配项' : '无'}
        expanded={expanded}
        editing={editing}
        existingNames={existingNames}
        toolsByServer={toolsByServer}
        actions={sectionToolbar}
        onToggleExpand={toggleExpand}
        onStartEdit={(name) => setEditing(name)}
        onCancelEdit={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          void refresh()
        }}
        onToggle={handleToggle}
        onDelete={(name) => setDeleteTarget(name)}
      />

      {filteredDisabled.length > 0 && (
        <MCPSection
          title="已禁用"
          icon={<PauseCircle className="h-4 w-4 text-muted-foreground" />}
          items={filteredDisabled}
          loading={false}
          emptyMessage={query ? '无匹配项' : ''}
          expanded={expanded}
          editing={editing}
          existingNames={existingNames}
          toolsByServer={toolsByServer}
          onToggleExpand={toggleExpand}
          onStartEdit={(name) => setEditing(name)}
          onCancelEdit={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void refresh()
          }}
          onToggle={handleToggle}
          onDelete={(name) => setDeleteTarget(name)}
        />
      )}

      {filteredFailed.length > 0 && (
        <MCPSection
          title="加载失败"
          icon={<AlertTriangle className="h-4 w-4 text-amber-600" />}
          items={filteredFailed}
          loading={false}
          emptyMessage={query ? '无匹配项' : ''}
          expanded={expanded}
          editing={editing}
          existingNames={existingNames}
          toolsByServer={toolsByServer}
          onToggleExpand={toggleExpand}
          onStartEdit={(name) => setEditing(name)}
          onCancelEdit={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void refresh()
          }}
          onToggle={handleToggle}
          onDelete={(name) => setDeleteTarget(name)}
          tone="failed"
        />
      )}

      <CreateMCPDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        existingNames={existingNames}
        onCreated={() => {
          setCreateOpen(false)
          void refresh()
        }}
      />

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(o: boolean) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void handleDelete()
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>删除 server：{deleteTarget}？</AlertDialogTitle>
            <AlertDialogDescription>
              将从 <code>.agenta/mcp/config.json</code> 移除该 server，并立即停止其子进程。该操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={handleDelete}
              autoFocus
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ResourcePage>
  )
}

// ============================================================================
// 工具函数
// ============================================================================

function groupServers(items: MCPServer[]): {
  enabled: MCPServer[]
  disabled: MCPServer[]
  failed: MCPServer[]
} {
  const enabled: MCPServer[] = []
  const disabled: MCPServer[] = []
  const failed: MCPServer[] = []
  for (const s of items) {
    if (!s.enabled) {
      disabled.push(s)
    } else if (s.status === 'failed') {
      failed.push(s)
    } else {
      enabled.push(s)
    }
  }
  return { enabled, disabled, failed }
}

function filterAndSort(
  items: MCPServer[],
  query: string,
  dir: SortDir,
): MCPServer[] {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? items.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.command.toLowerCase().includes(q),
      )
    : items
  const sorted = [...filtered].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, 'zh-Hans-CN', { sensitivity: 'base' })
    return dir === 'asc' ? cmp : -cmp
  })
  return sorted
}

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
  if (status === 'closed') {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
        closed
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

// ============================================================================
// SearchBox
// ============================================================================

function SearchBox({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled: boolean
}) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder="搜索 name / command"
        className="h-8 w-56 pl-7 pr-7 text-xs"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label="清除搜索"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

// ============================================================================
// MCPSection / MCPRow
// ============================================================================

type MCPSectionProps = {
  title: string
  icon: React.ReactNode
  items: MCPServer[]
  loading: boolean
  emptyMessage: string
  expanded: Set<string>
  editing: string | null
  existingNames: Set<string>
  toolsByServer: Map<string, MCPTool[]>
  actions?: React.ReactNode
  tone?: 'failed'
  onToggleExpand: (name: string) => void
  onStartEdit: (name: string) => void
  onCancelEdit: () => void
  onSaved: () => void
  onToggle: (name: string, enabled: boolean) => void
  onDelete: (name: string) => void
}

function MCPSection({
  title,
  icon,
  items,
  loading,
  emptyMessage,
  expanded,
  editing,
  existingNames,
  toolsByServer,
  actions,
  tone,
  onToggleExpand,
  onStartEdit,
  onCancelEdit,
  onSaved,
  onToggle,
  onDelete,
}: MCPSectionProps) {
  const wrapperCls =
    tone === 'failed'
      ? 'rounded-lg border border-amber-200 bg-amber-50/50 dark:border-amber-900 dark:bg-amber-950/30'
      : 'rounded-lg border border-border bg-card'
  const headerCls =
    tone === 'failed'
      ? 'flex flex-wrap items-center gap-2 border-b border-amber-200 px-3 py-2 text-sm font-medium dark:border-amber-900'
      : 'flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm font-medium'
  const dividerCls =
    tone === 'failed'
      ? 'divide-y divide-amber-200 dark:divide-amber-900'
      : 'divide-y divide-border'

  return (
    <div className={wrapperCls}>
      <div className={headerCls}>
        <div className="flex items-center gap-2">
          {icon}
          {title} ({items.length})
        </div>
        {actions && (
          <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
      {loading ? (
        <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
      ) : items.length === 0 ? (
        emptyMessage && (
          <p className="px-3 py-2 text-sm text-muted-foreground">{emptyMessage}</p>
        )
      ) : (
        <ul className={dividerCls}>
          {items.map((s) => (
            <MCPRow
              key={s.name}
              server={s}
              tools={toolsByServer.get(s.name) ?? []}
              expanded={expanded.has(s.name)}
              editing={editing === s.name}
              existingNames={existingNames}
              onToggleExpand={() => onToggleExpand(s.name)}
              onStartEdit={() => onStartEdit(s.name)}
              onCancelEdit={onCancelEdit}
              onSaved={onSaved}
              onToggle={(en) => onToggle(s.name, en)}
              onDelete={() => onDelete(s.name)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

type MCPRowProps = {
  server: MCPServer
  tools: MCPTool[]
  expanded: boolean
  editing: boolean
  existingNames: Set<string>
  onToggleExpand: () => void
  onStartEdit: () => void
  onCancelEdit: () => void
  onSaved: () => void
  onToggle: (enabled: boolean) => void
  onDelete: () => void
}

function MCPRow({
  server,
  tools,
  expanded,
  editing,
  existingNames,
  onToggleExpand,
  onStartEdit,
  onCancelEdit,
  onSaved,
  onToggle,
  onDelete,
}: MCPRowProps) {
  return (
    <li className="text-sm">
      <div className="flex items-start gap-2 px-3 py-2">
        <Switch
          checked={server.enabled}
          onCheckedChange={onToggle}
          aria-label={`${server.enabled ? '禁用' : '启用'} ${server.name}`}
          className="mt-0.5"
        />
        <button
          type="button"
          onClick={onToggleExpand}
          className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? '折叠' : '展开'}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <button
          type="button"
          onClick={onToggleExpand}
          className="flex-1 min-w-0 text-left"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{server.name}</span>
            {statusBadge(server.status)}
            <span className="text-[10px] text-muted-foreground">
              {server.tool_count} tools
            </span>
          </div>
          <div
            className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground"
            title={`${server.command} ${server.args.join(' ')}`}
          >
            {server.command}{' '}
            {server.args.length > 0 && (
              <span>{server.args.join(' ')}</span>
            )}
          </div>
          {server.error && (
            <div className="mt-0.5 text-[11px] text-red-700 dark:text-red-300">
              {server.error}
            </div>
          )}
        </button>
        <div className="flex shrink-0 gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={onStartEdit}
            disabled={editing}
            aria-label="编辑"
            className="h-7 w-7 p-0"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            aria-label="删除"
            className="h-7 w-7 p-0 text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {expanded && !editing && (
        <div className="border-t border-border bg-muted/30 px-3 py-2">
          <ServerDetails server={server} tools={tools} />
        </div>
      )}

      {editing && (
        <EditMCPForm
          server={server}
          existingNames={existingNames}
          onCancel={onCancelEdit}
          onSaved={onSaved}
        />
      )}
    </li>
  )
}

// ============================================================================
// 行展开：只读详情（command / args / env / tools）
// ============================================================================

function ServerDetails({ server, tools }: { server: MCPServer; tools: MCPTool[] }) {
  return (
    <div className="space-y-3 text-sm">
      <DetailField label="Command">
        <code className="font-mono text-[12px]">{server.command}</code>
      </DetailField>
      <DetailField label="Args">
        {server.args.length === 0 ? (
          <span className="text-muted-foreground">无</span>
        ) : (
          <ul className="space-y-0.5 font-mono text-[12px]">
            {server.args.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        )}
      </DetailField>
      <DetailField label="Env">
        {Object.keys(server.env).length === 0 ? (
          <span className="text-muted-foreground">无</span>
        ) : (
          <ul className="space-y-0.5 font-mono text-[12px]">
            {Object.entries(server.env).map(([k, v]) => (
              <li key={k}>
                <span className="text-foreground/80">{k}</span>={v}
              </li>
            ))}
          </ul>
        )}
      </DetailField>
      <DetailField label={`Tools (${tools.length})`}>
        {tools.length === 0 ? (
          <span className="text-muted-foreground">
            {server.status === 'connected' ? '该 server 未暴露 tool' : '未连接，暂无 tool'}
          </span>
        ) : (
          <ul className="space-y-1">
            {tools.map((t) => (
              <li key={t.name} className="rounded border border-border bg-background/60 px-2 py-1">
                <div className="font-mono text-[12px] font-medium">{t.name}</div>
                {t.description && (
                  <div className="text-[12px] text-foreground/80">{t.description}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </DetailField>
    </div>
  )
}

function DetailField({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-0.5 text-[11px] font-medium text-muted-foreground">{label}</div>
      <div>{children}</div>
    </div>
  )
}

// ============================================================================
// 编辑 / 新建 共用：args / env 编辑器 + 字段校验
// ============================================================================

function ArgsEditor({
  value,
  onChange,
  disabled,
}: {
  value: string[]
  onChange: (v: string[]) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-1">
      {value.map((a, i) => (
        <div key={i} className="flex items-center gap-1">
          <Input
            value={a}
            onChange={(e) => {
              const next = [...value]
              next[i] = e.target.value
              onChange(next)
            }}
            disabled={disabled}
            placeholder={`arg ${i + 1}`}
            className="h-8 font-mono text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            disabled={disabled}
            className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
            aria-label="移除该参数"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...value, ''])}
        disabled={disabled}
        className="gap-1.5"
      >
        <Plus className="h-3.5 w-3.5" />
        添加参数
      </Button>
    </div>
  )
}

type EnvPair = { key: string; value: string }

function EnvEditor({
  value,
  onChange,
  disabled,
}: {
  value: EnvPair[]
  onChange: (v: EnvPair[]) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-1">
      {value.map((kv, i) => (
        <div key={i} className="flex items-center gap-1">
          <Input
            value={kv.key}
            onChange={(e) => {
              const next = [...value]
              next[i] = { ...kv, key: e.target.value }
              onChange(next)
            }}
            disabled={disabled}
            placeholder="KEY"
            className="h-8 w-40 font-mono text-xs"
          />
          <span className="text-muted-foreground">=</span>
          <Input
            value={kv.value}
            onChange={(e) => {
              const next = [...value]
              next[i] = { ...kv, value: e.target.value }
              onChange(next)
            }}
            disabled={disabled}
            placeholder="value（支持 ${VAR} 引用进程 env）"
            className="h-8 flex-1 font-mono text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            disabled={disabled}
            className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
            aria-label="移除该环境变量"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...value, { key: '', value: '' }])}
        disabled={disabled}
        className="gap-1.5"
      >
        <Plus className="h-3.5 w-3.5" />
        添加环境变量
      </Button>
    </div>
  )
}

function envObjectToPairs(env: Record<string, string>): EnvPair[] {
  return Object.entries(env).map(([key, value]) => ({ key, value }))
}

function envPairsToObject(pairs: EnvPair[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { key, value } of pairs) {
    const k = key.trim()
    if (!k) continue
    out[k] = value
  }
  return out
}

// ============================================================================
// EditMCPForm（行内编辑表单）
// ============================================================================

type EditMCPFormProps = {
  server: MCPServer
  existingNames: Set<string>
  onCancel: () => void
  onSaved: () => void
}

function EditMCPForm({
  server,
  existingNames,
  onCancel,
  onSaved,
}: EditMCPFormProps) {
  const [name, setName] = useState(server.name)
  const [command, setCommand] = useState(server.command)
  const [args, setArgs] = useState<string[]>([...server.args])
  const [env, setEnv] = useState<EnvPair[]>(envObjectToPairs(server.env))
  const [saving, setSaving] = useState(false)

  const trimmedName = name.trim()
  const nameChanged = trimmedName !== server.name
  const nameError = !trimmedName
    ? '名称不能为空'
    : !NAME_PATTERN.test(trimmedName)
    ? '只能含字母 / 数字 / 下划线 / 连字符'
    : nameChanged && existingNames.has(trimmedName)
    ? '已存在同名 server'
    : null
  const cmdError = !command.trim() ? 'command 不能为空' : null
  const argsError = args.some((a) => a.trim() === '') ? '参数不能为空（删掉或填值）' : null
  const envKeyError = env.some(
    (kv, i) => kv.key.trim() === '' || env.findIndex((x) => x.key.trim() === kv.key.trim()) !== i,
  )
    ? '环境变量 key 不能为空且不能重复'
    : null
  const formError = nameError || cmdError || argsError || envKeyError

  const handleSave = async () => {
    if (formError) {
      toast.error(formError)
      return
    }
    setSaving(true)
    try {
      let currentName = server.name
      if (nameChanged) {
        await renameMCPServer(currentName, { new_name: trimmedName })
        currentName = trimmedName
      }
      await updateMCPServer(currentName, {
        command: command.trim(),
        args: args.map((a) => a.trim()).filter((a) => a !== ''),
        env: envPairsToObject(env),
      })
      toast.success(`已保存 server：${currentName}`)
      onSaved()
    } catch (e) {
      toast.error(`保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const formButtons = (
    <div className="flex shrink-0 gap-1.5">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onCancel}
        disabled={saving}
      >
        取消
      </Button>
      <Button
        type="button"
        size="sm"
        onClick={handleSave}
        disabled={saving || !!formError}
      >
        {saving ? '保存中…' : '保存'}
      </Button>
    </div>
  )

  return (
    <div className="border-t border-border bg-muted/30">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-muted-foreground">
          编辑：{server.name}
        </span>
        <div className="ml-auto">{formButtons}</div>
      </div>

      <div className="space-y-3 px-3 py-3 text-sm">
        <FormField label="名称" error={nameError}>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={saving}
            className="h-8 font-mono text-xs"
          />
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            改名会同步移动 config.json key 与 disabled.json 状态。
          </p>
        </FormField>

        <FormField label="Command" error={cmdError}>
          <Input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            disabled={saving}
            placeholder="如：npx / python / node"
            className="h-8 font-mono text-xs"
          />
        </FormField>

        <FormField label="Args" error={argsError}>
          <ArgsEditor value={args} onChange={setArgs} disabled={saving} />
        </FormField>

        <FormField label="Env" error={envKeyError}>
          <EnvEditor value={env} onChange={setEnv} disabled={saving} />
        </FormField>
      </div>

      <div className="flex justify-end border-t border-border px-3 py-2">
        {formButtons}
      </div>
    </div>
  )
}

function FormField({
  label,
  error,
  children,
}: {
  label: string
  error: string | null
  children: React.ReactNode
}) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {error && <p className="mt-1 text-[11px] text-destructive">{error}</p>}
    </div>
  )
}

// ============================================================================
// 新建 server 对话框
// ============================================================================

type CreateMCPDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingNames: Set<string>
  onCreated: () => void
}

function CreateMCPDialog({
  open,
  onOpenChange,
  existingNames,
  onCreated,
}: CreateMCPDialogProps) {
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState<string[]>([])
  const [env, setEnv] = useState<EnvPair[]>([])
  const [saving, setSaving] = useState(false)

  // 对话框打开时重置一次
  useEffect(() => {
    if (open) {
      setName('')
      setCommand('')
      setArgs([])
      setEnv([])
      setSaving(false)
    }
  }, [open])

  const trimmedName = name.trim()
  const nameError = !trimmedName
    ? '名称不能为空'
    : !NAME_PATTERN.test(trimmedName)
    ? '只能含字母 / 数字 / 下划线 / 连字符'
    : existingNames.has(trimmedName)
    ? '已存在同名 server'
    : null
  const cmdError = !command.trim() ? 'command 不能为空' : null
  const argsError = args.some((a) => a.trim() === '') ? '参数不能为空（删掉或填值）' : null
  const envKeyError = env.some(
    (kv, i) => kv.key.trim() === '' || env.findIndex((x) => x.key.trim() === kv.key.trim()) !== i,
  )
    ? '环境变量 key 不能为空且不能重复'
    : null
  const formError = nameError || cmdError || argsError || envKeyError

  const handleCreate = async () => {
    if (formError) {
      toast.error(formError)
      return
    }
    setSaving(true)
    try {
      await createMCPServer({
        name: trimmedName,
        command: command.trim(),
        args: args.map((a) => a.trim()).filter((a) => a !== ''),
        env: envPairsToObject(env),
      })
      toast.success(`已创建 server：${trimmedName}`)
      onCreated()
    } catch (e) {
      toast.error(`创建失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          'grid-cols-none! flex flex-col',
          'w-[calc(100vw-2rem)] sm:max-w-none',
          'max-w-[900px]',
          'max-h-[90vh]',
        )}
      >
        <DialogHeader>
          <DialogTitle>新建 MCP Server</DialogTitle>
          <DialogDescription>
            写入 <code>.agenta/mcp/config.json</code> 并立即拉起子进程；下一轮对话即可被 LLM 看到。
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-3 overflow-y-auto pr-1 text-sm">
          <FormField label="名称" error={nameError}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={saving}
              placeholder="如：filesystem / fetch"
              className="h-8 font-mono text-xs"
            />
          </FormField>

          <FormField label="Command" error={cmdError}>
            <Input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              disabled={saving}
              placeholder="如：npx / python / node"
              className="h-8 font-mono text-xs"
            />
          </FormField>

          <FormField label="Args" error={argsError}>
            <ArgsEditor value={args} onChange={setArgs} disabled={saving} />
            <p className="mt-1 text-[11px] text-muted-foreground">
              示例：<code>-y</code> + <code>@modelcontextprotocol/server-filesystem</code> + <code>./</code>
            </p>
          </FormField>

          <FormField label="Env" error={envKeyError}>
            <EnvEditor value={env} onChange={setEnv} disabled={saving} />
            <p className="mt-1 text-[11px] text-muted-foreground">
              value 内的 <code>${'${VAR}'}</code> 在启动时按当前进程 env 展开；变量缺失时保留字面量。
            </p>
          </FormField>
        </div>

        <DialogFooter>
          <DialogClose
            render={
              <Button variant="outline" disabled={saving}>
                取消
              </Button>
            }
          />
          <Button onClick={handleCreate} disabled={saving || !!formError}>
            {saving ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
