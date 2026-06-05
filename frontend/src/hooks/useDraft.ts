import { useCallback, useEffect, useState } from 'react'

const PREFIX = 'agenta:draft:'

/**
 * 按 session 把未发送的草稿存 localStorage：切走再切回不丢。
 * 返回 [draft, setDraft, clearDraft]。
 */
export function useDraft(
  sessionId: string | null,
): [string, (v: string) => void, () => void] {
  const key = sessionId ? PREFIX + sessionId : null
  const [draft, setDraftState] = useState('')

  // 切 session 时从 localStorage 读回对应草稿
  useEffect(() => {
    if (!key) {
      setDraftState('')
      return
    }
    try {
      setDraftState(localStorage.getItem(key) ?? '')
    } catch {
      setDraftState('')
    }
  }, [key])

  const setDraft = useCallback(
    (v: string) => {
      setDraftState(v)
      if (!key) return
      try {
        if (v) localStorage.setItem(key, v)
        else localStorage.removeItem(key)
      } catch {
        // localStorage 不可用（隐私模式）时忽略
      }
    },
    [key],
  )

  const clearDraft = useCallback(() => {
    setDraftState('')
    if (!key) return
    try {
      localStorage.removeItem(key)
    } catch {
      // 忽略
    }
  }, [key])

  return [draft, setDraft, clearDraft]
}
