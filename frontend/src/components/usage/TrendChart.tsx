import { useMemo } from 'react'

import type { SeriesRow, UsageMetric } from '@/types/usage'
import { CHART_COLORS, formatMetric, metricValue } from './format'

type TrendChartProps = {
  rows: SeriesRow[]
  metric: UsageMetric
  currency: string
}

// 依赖零三方图表库：用纯 SVG 画按天堆叠柱状图（决策见 docs/iter_11_token.md §7）。
const H = 240
const PAD_L = 48
const PAD_R = 12
const PAD_T = 12
const PAD_B = 28

export function TrendChart({ rows, metric, currency }: TrendChartProps) {
  const { keys, stacks, maxTotal } = useMemo(() => {
    const dateSet = new Map<string, true>()
    const keyMap = new Map<string, string>() // key -> label
    for (const r of rows) {
      dateSet.set(r.date, true)
      if (!keyMap.has(r.key)) keyMap.set(r.key, r.key_label)
    }
    const dates = [...dateSet.keys()].sort()
    const keys = [...keyMap.entries()].map(([key, label]) => ({ key, label }))
    // date -> key -> value
    const byDate = new Map<string, Map<string, number>>()
    for (const r of rows) {
      const m = byDate.get(r.date) ?? new Map<string, number>()
      m.set(r.key, (m.get(r.key) ?? 0) + metricValue(r, metric))
      byDate.set(r.date, m)
    }
    let maxTotal = 0
    const stacks = dates.map((d) => {
      const m = byDate.get(d) ?? new Map<string, number>()
      let total = 0
      const segs = keys.map(({ key }) => {
        const v = m.get(key) ?? 0
        total += v
        return { key, value: v }
      })
      if (total > maxTotal) maxTotal = total
      return { date: d, segs, total }
    })
    return { keys, stacks, maxTotal }
  }, [rows, metric])

  const colorOf = (key: string) => {
    const idx = keys.findIndex((k) => k.key === key)
    return CHART_COLORS[(idx < 0 ? 0 : idx) % CHART_COLORS.length]
  }

  if (rows.length === 0 || maxTotal === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
        {rows.length === 0 ? '所选范围暂无用量数据' : '所选指标在该范围内均为 0（如所选模型免费）'}
      </div>
    )
  }

  const W = 720
  const plotW = W - PAD_L - PAD_R
  const plotH = H - PAD_T - PAD_B
  const n = stacks.length
  const slot = plotW / n
  const barW = Math.max(2, Math.min(36, slot * 0.6))

  const yScale = (v: number) => plotH - (v / maxTotal) * plotH
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => maxTotal * f)
  // x 轴标签抽稀，避免重叠
  const labelStep = Math.ceil(n / 10)

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        preserveAspectRatio="xMidYMid meet"
        role="img"
      >
        {/* y 轴网格 + 刻度 */}
        {ticks.map((t, i) => {
          const y = PAD_T + yScale(t)
          return (
            <g key={i}>
              <line
                x1={PAD_L}
                y1={y}
                x2={W - PAD_R}
                y2={y}
                className="stroke-border"
                strokeWidth={1}
                strokeDasharray={i === 0 ? '' : '3 3'}
              />
              <text
                x={PAD_L - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground"
                fontSize={10}
              >
                {formatMetric(t, metric, currency)}
              </text>
            </g>
          )
        })}

        {/* 堆叠柱 */}
        {stacks.map((s, i) => {
          const cx = PAD_L + slot * i + slot / 2
          let acc = 0
          return (
            <g key={s.date}>
              {s.segs.map((seg) => {
                if (seg.value <= 0) return null
                const yTop = PAD_T + yScale(acc + seg.value)
                const yBottom = PAD_T + yScale(acc)
                acc += seg.value
                const label = keys.find((k) => k.key === seg.key)?.label ?? seg.key
                return (
                  <rect
                    key={seg.key}
                    x={cx - barW / 2}
                    y={yTop}
                    width={barW}
                    height={Math.max(0.5, yBottom - yTop)}
                    fill={colorOf(seg.key)}
                  >
                    <title>
                      {`${s.date} · ${label}: ${formatMetric(seg.value, metric, currency)}`}
                    </title>
                  </rect>
                )
              })}
              {i % labelStep === 0 && (
                <text
                  x={cx}
                  y={H - 8}
                  textAnchor="middle"
                  className="fill-muted-foreground"
                  fontSize={10}
                >
                  {s.date.slice(5)}
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* 图例 */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {keys.map(({ key, label }) => (
          <span key={key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: colorOf(key) }}
            />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}
