import { useCallback, useEffect, useState } from 'react'
import { getConfig, patchConfig } from '@/api/client'
import type { ConfigItemView } from '@/types/config'

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high'

// 档位 → THINKING_ENABLED + THINKING_BUDGET 预设（budget 具体值见 §2.2 决策记录）
const LEVEL_BUDGET: Record<Exclude<ThinkingLevel, 'off'>, number> = {
  low: 2048,
  medium: 8000,
  high: 32000,
}

// 哪些 provider 名实际支持 extended thinking（其余灰显）。
// 必须与后端 call_with_thinking 的分支严格对齐：当前只有 claude / qwen 真正实现，
// 其余 provider 后端会静默降级成普通 chat，所以前端也只对这两个放开。
const THINKING_PROVIDER_HINTS = ['claude', 'qwen']

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
  const [providers, setProviders] = useState<string[]>([])
  const [activeProvider, setActiveProvider] = useState('')
  const [level, setLevelState] = useState<ThinkingLevel>('off')

  const load = useCallback(async () => {
    try {
      const cfg = await getConfig()
      const provItem = findItem(cfg.groups, 'ACTIVE_PROVIDER')
      const enItem = findItem(cfg.groups, 'THINKING_ENABLED')
      const budgetItem = findItem(cfg.groups, 'THINKING_BUDGET')
      if (provItem) {
        setProviders(provItem.options ?? [])
        setActiveProvider(String(provItem.value ?? ''))
      }
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

  const setProvider = useCallback(async (p: string) => {
    setActiveProvider(p) // 乐观更新
    try {
      await patchConfig('ACTIVE_PROVIDER', p)
    } catch (e) {
      console.error('[useComposerSettings] 切 provider 失败', e)
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

  const thinkingSupported = THINKING_PROVIDER_HINTS.some((h) =>
    activeProvider.toLowerCase().includes(h),
  )

  return {
    loading,
    providers,
    activeProvider,
    setProvider,
    level,
    setLevel,
    thinkingSupported,
  }
}
