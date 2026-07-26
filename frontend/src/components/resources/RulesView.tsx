import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { readRules, writeRules } from '@/api/client'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'
import { useWriteScope } from '@/lib/permissions'

// 与后端 config.USER_RULES_MAX_CHARS 保持一致：超出会被 PUT /api/rules 拒绝（400），
// 这里实时提示字数并提前禁用保存，避免用户白写。
const MAX_RULES_CHARS = 4000

export function RulesView() {
  const { allowed: canWriteMemory, tip: memoryTip } = useWriteScope('memory')
  const [text, setText] = useState('')
  const [originalText, setOriginalText] = useState('')
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
  const overLimit = text.length > MAX_RULES_CHARS

  const handleSave = async () => {
    setSaving(true)
    try {
      const resp = await writeRules(text)
      setOriginalText(text)
      toast.success(`已保存 ${resp.length} 字符；下一轮对话即生效`)
    } catch (e) {
      toast.error(`保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <ResourcePage
      title="Rules"
      subtitle="你的个人偏好规则（每个用户独享，对齐 Cursor Rules / GHC Custom Instructions），会注入到每轮对话的系统提示里"
    >
      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {loadError}
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={loading || saving || !canWriteMemory}
        readOnly={!canWriteMemory}
        placeholder={loading ? '加载中…' : '在此撰写你的个人 rules（Markdown）'}
        className="min-h-[400px] w-full flex-1 resize-y rounded-md border border-border bg-card p-3 font-mono text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-ring"
        spellCheck={false}
      />

      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          提示：rules 每轮对话动态读取，保存后开新一轮对话即被 LLM 看到，无需重启。
        </p>
        <div className="flex items-center gap-3">
          <span
            className={`text-xs ${overLimit ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'}`}
          >
            {text.length} / {MAX_RULES_CHARS}
          </span>
          <Button
            onClick={handleSave}
            disabled={!canWriteMemory || !dirty || saving || loading || overLimit}
            title={canWriteMemory ? undefined : memoryTip}
            size="sm"
          >
            {saving ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
    </ResourcePage>
  )
}
