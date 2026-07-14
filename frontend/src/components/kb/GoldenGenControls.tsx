import { useEffect, useState } from 'react'

import { getGoldenGenOptions } from '@/api/client'
import type { GoldenGenOptions } from '@/types/eval'

export const GOLDEN_LLM_LABELS: Record<string, string> = {
  none: '不生成',
  'kimi-k2.5': 'Kimi K2.5',
  'deepseek-v4-flash': 'DeepSeek V4 Flash',
}

export type GoldenGenControlsProps = {
  goldenLlm: string
  goldenMaxQ: number
  onGoldenLlmChange: (v: string) => void
  onGoldenMaxQChange: (v: number) => void
  disabled?: boolean
  /** 入库 true（含不生成）；L2 手动生成 false */
  includeNone?: boolean
}

const FALLBACK: GoldenGenOptions = {
  llm_choices: [
    { value: 'none', label: '不生成' },
    { value: 'kimi-k2.5', label: 'Kimi K2.5' },
    { value: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  ],
  max_q_default: 3,
  max_q_min: 1,
  max_q_max: 20,
}

export function GoldenGenControls({
  goldenLlm,
  goldenMaxQ,
  onGoldenLlmChange,
  onGoldenMaxQChange,
  disabled,
  includeNone = true,
}: GoldenGenControlsProps) {
  const [opts, setOpts] = useState<GoldenGenOptions>(FALLBACK)

  useEffect(() => {
    getGoldenGenOptions()
      .then((o) => {
        setOpts(o)
        if (!includeNone) {
          const first = o.llm_choices.find((c) => c.value !== 'none')
          if (first && goldenLlm === 'none') onGoldenLlmChange(first.value)
        }
      })
      .catch(() => setOpts(FALLBACK))
    // 仅挂载时按服务端选项校正 L2 默认 LLM
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeNone])

  const llmChoices = includeNone
    ? opts.llm_choices
    : opts.llm_choices.filter((c) => c.value !== 'none')
  const llmDisabled = disabled || (includeNone && goldenLlm === 'none')
  const selectCls =
    'rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground disabled:opacity-50'

  return (
    <>
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="shrink-0">评估题 LLM</span>
        <select
          value={goldenLlm}
          onChange={(e) => onGoldenLlmChange(e.target.value)}
          disabled={disabled}
          className={selectCls}
          aria-label="评估题 LLM"
        >
          {llmChoices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="shrink-0">出题上限</span>
        <input
          type="number"
          min={opts.max_q_min}
          max={opts.max_q_max}
          value={goldenMaxQ}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10)
            if (Number.isNaN(n)) return
            const clamped = Math.min(opts.max_q_max, Math.max(opts.max_q_min, n))
            onGoldenMaxQChange(clamped)
          }}
          onBlur={() => {
            const clamped = Math.min(
              opts.max_q_max,
              Math.max(opts.max_q_min, goldenMaxQ || opts.max_q_default),
            )
            if (clamped !== goldenMaxQ) onGoldenMaxQChange(clamped)
          }}
          disabled={llmDisabled}
          className={`${selectCls} w-14 tabular-nums`}
          aria-label="生成 Golden 出题上限"
        />
      </label>
    </>
  )
}
