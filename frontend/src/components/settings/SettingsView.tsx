import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { getConfig } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import type { AppConfig } from '@/types/config'

type Row = { label: string; value: React.ReactNode }

function fmt(v: unknown): React.ReactNode {
  if (v === null || v === undefined) return <span className="text-muted-foreground">—</span>
  if (typeof v === 'boolean') {
    return (
      <span
        className={
          'inline-block rounded px-1.5 py-0.5 text-[10px] ' +
          (v
            ? 'bg-green-50 text-green-900 dark:bg-green-950 dark:text-green-100'
            : 'bg-muted text-muted-foreground')
        }
      >
        {v ? 'true' : 'false'}
      </span>
    )
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-muted-foreground">[]</span>
    return (
      <span className="flex flex-wrap gap-1">
        {v.map((it) => (
          <span
            key={String(it)}
            className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]"
          >
            {String(it)}
          </span>
        ))}
      </span>
    )
  }
  return <span className="font-mono text-xs">{String(v)}</span>
}

function Section({ title, rows }: { title: string; rows: Row[] }) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-3 py-2 text-sm font-medium">{title}</div>
      <dl className="divide-y divide-border">
        {rows.map((r) => (
          <div key={r.label} className="flex items-start gap-3 px-3 py-2 text-sm">
            <dt className="w-44 shrink-0 text-muted-foreground">{r.label}</dt>
            <dd className="min-w-0 flex-1 break-all">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function SettingsView() {
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCfg(await getConfig())
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
      title="系统配置"
      subtitle="当前运行时配置摘要（只读；修改请编辑 .env 后重启 uvicorn）"
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
      {loading && !cfg && (
        <p className="text-sm text-muted-foreground">加载中…</p>
      )}
      {cfg && (
        <>
          <Section
            title="LLM"
            rows={[
              { label: 'active_provider', value: fmt(cfg.llm.active_provider) },
              { label: 'model', value: fmt(cfg.llm.model) },
              { label: 'force_temperature', value: fmt(cfg.llm.force_temperature) },
              { label: 'thinking_enabled', value: fmt(cfg.llm.thinking_enabled) },
              { label: 'thinking_budget', value: fmt(cfg.llm.thinking_budget) },
              { label: 'available_providers', value: fmt(cfg.llm.available_providers) },
            ]}
          />
          <Section
            title="RAG"
            rows={[
              { label: 'top_k', value: fmt(cfg.rag.top_k) },
              { label: 'k_per_source', value: fmt(cfg.rag.k_per_source) },
              { label: 'active_embeddings', value: fmt(cfg.rag.active_embeddings) },
              { label: 'default_embedding', value: fmt(cfg.rag.default_embedding) },
              { label: 'reranker_enabled', value: fmt(cfg.rag.reranker_enabled) },
              { label: 'reranker_model', value: fmt(cfg.rag.reranker_model) },
              { label: 'query_rewrite_enabled', value: fmt(cfg.rag.query_rewrite_enabled) },
              { label: 'ocr_fallback_enabled', value: fmt(cfg.rag.ocr_fallback_enabled) },
              { label: 'chunk_size', value: fmt(cfg.rag.chunk_size) },
              { label: 'chunk_overlap', value: fmt(cfg.rag.chunk_overlap) },
            ]}
          />
          <Section
            title="Memory"
            rows={[
              { label: 'enabled', value: fmt(cfg.memory.enabled) },
              { label: 'auto_extract', value: fmt(cfg.memory.auto_extract) },
              { label: 'max_chars', value: fmt(cfg.memory.max_chars) },
            ]}
          />
          <Section
            title="Rules"
            rows={[
              { label: 'enabled', value: fmt(cfg.rules.enabled) },
              { label: 'file', value: fmt(cfg.rules.file) },
              { label: 'max_chars', value: fmt(cfg.rules.max_chars) },
            ]}
          />
          <Section
            title="MCP"
            rows={[
              { label: 'enabled', value: fmt(cfg.mcp.enabled) },
              { label: 'config_file', value: fmt(cfg.mcp.config_file) },
              { label: 'connect_timeout_sec', value: fmt(cfg.mcp.connect_timeout_sec) },
              { label: 'call_timeout_sec', value: fmt(cfg.mcp.call_timeout_sec) },
            ]}
          />
          <Section
            title="Security"
            rows={[
              { label: 'mode', value: fmt(cfg.security.mode) },
              { label: 'plan_permission_mode', value: fmt(cfg.security.plan_permission_mode) },
            ]}
          />
          <Section
            title="Web"
            rows={[
              { label: 'upload_dir', value: fmt(cfg.web.upload_dir) },
              { label: 'max_upload_mb', value: fmt(cfg.web.max_upload_mb) },
            ]}
          />
          <Section
            title="Log"
            rows={[{ label: 'level', value: fmt(cfg.log.level) }]}
          />
        </>
      )}
    </ResourcePage>
  )
}
