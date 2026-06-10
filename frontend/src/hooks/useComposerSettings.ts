import { useCallback, useEffect, useMemo, useState } from 'react'
import { getConfig, getLlmPrefs, getModels, patchLlmPrefs } from '@/api/client'
import type { ProviderModels } from '@/types/config'

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

/** Composer 的模型 / 推理档位设置，读写每用户偏好 /api/auth/llm-prefs（下一条消息生效，各用户互不干扰）。 */
export function useComposerSettings() {
  const [loading, setLoading] = useState(true)
  const [providers, setProviders] = useState<ProviderModels[]>([])
  const [activeModel, setActiveModel] = useState('')
  const [level, setLevelState] = useState<ThinkingLevel>('off')
  // 深度研究：是否启用（来自全局配置，决定开关是否显示）+ 本会话当前是否开启
  const [deepResearchEnabled, setDeepResearchEnabled] = useState(false)
  const [deepResearch, setDeepResearch] = useState(false)

  const load = useCallback(async () => {
    try {
      const [catalog, prefs] = await Promise.all([getModels(), getLlmPrefs()])
      setProviders(catalog.providers)
      setActiveModel(prefs.active_model)
      setLevelState(budgetToLevel(prefs.thinking_enabled, prefs.thinking_budget))
    } catch (e) {
      console.error('[useComposerSettings] 读配置失败', e)
    } finally {
      setLoading(false)
    }
    // 深度研究开关是否显示，单独读全局配置（失败则隐藏，安全降级）
    try {
      const cfg = await getConfig()
      const item = cfg.groups
        .flatMap((g) => g.items)
        .find((it) => it.key === 'DEEP_RESEARCH_ENABLED')
      setDeepResearchEnabled(item?.value === true)
    } catch {
      setDeepResearchEnabled(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const setModel = useCallback(async (id: string) => {
    setActiveModel(id) // 乐观更新
    try {
      await patchLlmPrefs({ active_model: id })
    } catch (e) {
      console.error('[useComposerSettings] 切模型失败', e)
      void load()
    }
  }, [load])

  const setLevel = useCallback(async (lv: ThinkingLevel) => {
    setLevelState(lv)
    try {
      if (lv === 'off') {
        await patchLlmPrefs({ thinking_enabled: false })
      } else {
        await patchLlmPrefs({ thinking_enabled: true, thinking_budget: LEVEL_BUDGET[lv] })
      }
    } catch (e) {
      console.error('[useComposerSettings] 设推理档位失败', e)
      void load()
    }
  }, [load])

  // 当前模型的 label + 是否支持 thinking，从目录里查
  const active = useMemo(() => {
    if (activeModel === 'auto') return { label: '自动', thinking: false }
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
    deepResearchEnabled,
    deepResearch,
    setDeepResearch,
  }
}
