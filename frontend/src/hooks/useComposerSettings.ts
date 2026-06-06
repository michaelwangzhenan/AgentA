import { useCallback, useEffect, useMemo, useState } from 'react'
import { getConfig, getModels, patchConfig } from '@/api/client'
import type { ConfigItemView, ProviderModels } from '@/types/config'

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high'

// 档位 → THINKING_ENABLED + THINKING_BUDGET 预设（budget 具体值见 §2.2 决策记录）
const LEVEL_BUDGET: Record<Exclude<ThinkingLevel, 'off'>, number> = {
  low: 2048,
  medium: 8000,
  high: 32000,
}

function budgetToLevel(enabled: boolean, budget: number): ThinkingLevel {
  if (!enabled) return 'off'
  if (budget <= 4096) return 'low'
  if (budget <= 16000) return 'medium'
  return 'high'
}

function findItem(
  groups: { items: ConfigItemView[] }[],
  key: string,
): ConfigItemView | undefined {
  for (const g of groups) {
    const it = g.items.find((i) => i.key === key)
    if (it) return it
  }
  return undefined
}

/** Composer 的模型 / 推理档位设置，读写 /api/config（下一条消息生效）。 */
export function useComposerSettings() {
  const [loading, setLoading] = useState(true)
  const [providers, setProviders] = useState<ProviderModels[]>([])
  const [activeModel, setActiveModel] = useState('')
  const [level, setLevelState] = useState<ThinkingLevel>('off')

  const load = useCallback(async () => {
    try {
      const [catalog, cfg] = await Promise.all([getModels(), getConfig()])
      setProviders(catalog.providers)
      setActiveModel(catalog.active)
      const enItem = findItem(cfg.groups, 'THINKING_ENABLED')
      const budgetItem = findItem(cfg.groups, 'THINKING_BUDGET')
      const enabled = Boolean(enItem?.value)
      const budget = Number(budgetItem?.value ?? 8000)
      setLevelState(budgetToLevel(enabled, budget))
    } catch (e) {
      console.error('[useComposerSettings] 读配置失败', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const setModel = useCallback(async (id: string) => {
    setActiveModel(id) // 乐观更新
    try {
      await patchConfig('ACTIVE_MODEL', id)
    } catch (e) {
      console.error('[useComposerSettings] 切模型失败', e)
      void load()
    }
  }, [load])

  const setLevel = useCallback(async (lv: ThinkingLevel) => {
    setLevelState(lv)
    try {
      if (lv === 'off') {
        await patchConfig('THINKING_ENABLED', false)
      } else {
        await patchConfig('THINKING_ENABLED', true)
        await patchConfig('THINKING_BUDGET', LEVEL_BUDGET[lv])
      }
    } catch (e) {
      console.error('[useComposerSettings] 设推理档位失败', e)
      void load()
    }
  }, [load])

  // 当前模型的 label + 是否支持 thinking，从目录里查
  const active = useMemo(() => {
    for (const p of providers) {
      const m = p.models.find((x) => x.id === activeModel)
      if (m) return { label: m.label, thinking: m.thinking }
    }
    return { label: activeModel, thinking: false }
  }, [providers, activeModel])

  return {
    loading,
    providers,
    activeModel,
    activeModelLabel: active.label,
    setModel,
    level,
    setLevel,
    thinkingSupported: active.thinking,
  }
}
