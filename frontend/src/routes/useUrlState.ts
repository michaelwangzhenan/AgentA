import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

type PatchValue = string | number | null | undefined

export type UrlPatchOptions = { replace?: boolean }

/** 读写当前路径的查询参数；等于默认或空值的键会从网址里删掉。 */
export function useUrlState() {
  const [searchParams, setSearchParams] = useSearchParams()

  const get = useCallback(
    (key: string, defaultValue = ''): string => searchParams.get(key) ?? defaultValue,
    [searchParams],
  )

  const getInt = useCallback(
    (key: string, defaultValue: number): number => {
      const raw = searchParams.get(key)
      if (raw == null || raw === '') return defaultValue
      const n = parseInt(raw, 10)
      return Number.isNaN(n) ? defaultValue : n
    },
    [searchParams],
  )

  const getCsv = useCallback(
    (key: string): string[] => {
      const raw = searchParams.get(key)
      if (!raw) return []
      return raw.split(',').map((s) => s.trim()).filter(Boolean)
    },
    [searchParams],
  )

  const patch = useCallback(
    (updates: Record<string, PatchValue>, options?: UrlPatchOptions) => {
      const next = new URLSearchParams(searchParams)
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === '') {
          next.delete(key)
        } else {
          next.set(key, String(value))
        }
      }
      setSearchParams(next, { replace: options?.replace ?? false })
    },
    [searchParams, setSearchParams],
  )

  const clear = useCallback(
    (keys: string[], options?: UrlPatchOptions) => {
      const next = new URLSearchParams(searchParams)
      for (const key of keys) next.delete(key)
      setSearchParams(next, { replace: options?.replace ?? false })
    },
    [searchParams, setSearchParams],
  )

  return useMemo(
    () => ({ searchParams, get, getInt, getCsv, patch, clear }),
    [searchParams, get, getInt, getCsv, patch, clear],
  )
}
