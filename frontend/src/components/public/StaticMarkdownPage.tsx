import { useEffect, useState } from 'react'

import { StaticMarkdownContent } from '@/components/public/StaticMarkdownContent'
import { StaticPageShell } from '@/components/public/StaticPageShell'
import { loadStaticPageMarkdown, type StaticPageConfig } from '@/lib/staticPages'

type StaticMarkdownPageProps = {
  page: StaticPageConfig
}

export function StaticMarkdownPage({ page }: StaticMarkdownPageProps) {
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setMarkdown(null)
    setError(null)

    void loadStaticPageMarkdown(page.slug)
      .then((text) => {
        if (!cancelled) setMarkdown(text)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载失败')
        }
      })

    return () => {
      cancelled = true
    }
  }, [page.slug])

  return (
    <StaticPageShell showLogo={page.showLogo} title={page.showLogo ? undefined : page.title}>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : markdown === null ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : (
        <StaticMarkdownContent source={markdown} />
      )}
    </StaticPageShell>
  )
}
