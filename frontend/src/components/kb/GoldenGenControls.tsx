import { useEffect, useState } from 'react'

import { getGoldenGenOptions } from '@/api/client'
import type { GoldenGenOptions } from '@/types/eval'

export type GoldenGenControlsProps = {
  goldenLlm: string
  goldenMaxQ: number
  onGoldenLlmChange: (v: string) => void
  onGoldenMaxQChange: (v: number) => void
  disabled?: boolean
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
}: GoldenGenControlsProps) {
  const [opts, setOpts] = useState<GoldenGenOptions>(FALLBACK)

  useEffect(() => {
    getGoldenGenOptions()
      .then(setOpts)
      .catch(() => setOpts(FALLBACK))
  }, [])

  const llmDisabled = disabled || goldenLlm === 'none'
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
          {opts.llm_choices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="shrink-0">出题数</span>
        <input
          type="number"
          min={opts.max_q_min}
          max={opts.max_q_max}
          value={goldenMaxQ}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10)
            if (!Number.isNaN(n)) onGoldenMaxQChange(n)
          }}
          disabled={llmDisabled}
          className={`${selectCls} w-14 tabular-nums`}
          aria-label="生成 Golden 数量"
        />
      </label>
    </>
  )
}
