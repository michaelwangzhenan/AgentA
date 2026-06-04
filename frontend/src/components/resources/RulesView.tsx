import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { readRules, writeRules } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'

export function RulesView() {
  const [text, setText] = useState('')
  const [originalText, setOriginalText] = useState('')
  const [path, setPath] = useState('')
  const [exists, setExists] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const resp = await readRules()
      setText(resp.text)
      setOriginalText(resp.text)
      setPath(resp.path)
      setExists(resp.exists)
    } catch (e) {
      setLoadError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const dirty = text !== originalText

  const handleSave = async () => {
    setSaving(true)
    try {
      const resp = await writeRules(text)
      setOriginalText(text)
      setExists(true)
      toast.success(
        `已保存 ${resp.length} 字符；${
          resp.restart_required ? '重启 uvicorn 或新建 session 后生效' : '已生效'
        }`,
      )
    } catch (e) {
      toast.error(`保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <ResourcePage
      title="项目 Rules"
      subtitle={`覆盖项目级偏好；写入到 ${path || '...'}${exists ? '' : '（文件尚未创建）'}`}
    >
      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {loadError}
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={loading || saving}
        placeholder={loading ? '加载中…' : '在此撰写项目级 rules（Markdown）'}
        className="min-h-[400px] w-full flex-1 resize-y rounded-md border border-border bg-card p-3 font-mono text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring"
        spellCheck={false}
      />

      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          提示：rules 在 Agent 启动时一次性加载，编辑后建新 session 或重启 uvicorn 才会被 LLM 看到。
        </p>
        <Button onClick={handleSave} disabled={!dirty || saving || loading} size="sm">
          {saving ? '保存中…' : '保存'}
        </Button>
      </div>
    </ResourcePage>
  )
}
