import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { readRules, writeRules } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'

export function RulesView() {
  const [text, setText] = useState('')
  const [originalText, setOriginalText] = useState('')
  const [path, setPath] = useState('')
  const [exists, setExists] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<
    { kind: 'success' | 'error'; message: string } | null
  >(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setNotice(null)
    try {
      const resp = await readRules()
      setText(resp.text)
      setOriginalText(resp.text)
      setPath(resp.path)
      setExists(resp.exists)
    } catch (e) {
      setNotice({ kind: 'error', message: (e as Error).message })
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
    setNotice(null)
    try {
      const resp = await writeRules(text)
      setOriginalText(text)
      setExists(true)
      setNotice({
        kind: 'success',
        message: `已保存 ${resp.length} 字符；${
          resp.restart_required ? '重启 uvicorn 或新建 session 后生效' : '已生效'
        }`,
      })
    } catch (e) {
      setNotice({ kind: 'error', message: `保存失败：${(e as Error).message}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <ResourcePage
      title="项目 Rules"
      subtitle={`覆盖项目级偏好；写入到 ${path || '...'}${exists ? '' : '（文件尚未创建）'}`}
      toolbar={
        <Button onClick={handleSave} disabled={!dirty || saving || loading} size="sm">
          {saving ? '保存中…' : '保存'}
        </Button>
      }
    >
      {notice && (
        <div
          className={
            'flex items-start gap-2 rounded-md border px-3 py-2 text-sm ' +
            (notice.kind === 'success'
              ? 'border-green-200 bg-green-50 text-green-900 dark:border-green-900 dark:bg-green-950 dark:text-green-100'
              : 'border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100')
          }
        >
          {notice.kind === 'success' ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          )}
          <span>{notice.message}</span>
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={loading || saving}
        placeholder={loading ? '加载中…' : '在此撰写项目级 rules（Markdown）'}
        className="min-h-[400px] w-full resize-y rounded-md border border-border bg-card p-3 font-mono text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring"
        spellCheck={false}
      />

      <p className="text-xs text-muted-foreground">
        提示：rules 在 Agent 启动时一次性加载，编辑后建新 session 或重启 uvicorn 才会被 LLM 看到。
      </p>
    </ResourcePage>
  )
}
