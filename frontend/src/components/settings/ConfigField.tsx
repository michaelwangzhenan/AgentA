import { useEffect, useRef, useState } from 'react'
import { Info, Loader2, RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { ConfigItemView } from '@/types/config'
import { cn } from '@/lib/utils'

export type ConfigFieldProps = {
  item: ConfigItemView
  /** 本地值（受控）；undefined 表示用 item.value */
  localValue?: unknown
  /** 行内校验错误（来自后端） */
  error?: string | null
  /** 用户改值；父级负责更新 localValue + 自动触发保存 */
  onChange: (next: unknown) => void
  /** 用户点重置（DELETE 该项 override） */
  onReset: () => void
  /** 当前是否在保存中（含 debounce 等待） */
  saving?: boolean
  /** 依赖项未满足时灰显并禁止交互（如开关关闭、模式不匹配） */
  disabled?: boolean
}

export function ConfigField(props: ConfigFieldProps) {
  const { item, localValue, error, onChange, onReset, saving, disabled } = props
  const value = localValue !== undefined ? localValue : item.value

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card px-3 py-2.5 transition-colors',
        saving && 'border-l-4 border-l-primary',
        error && 'border-l-4 border-l-destructive',
      )}
      data-config-key={item.key}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <label
              htmlFor={`cfg-${item.key}`}
              className="text-sm font-medium leading-none"
            >
              {item.brief}
            </label>
            <DetailHint item={item} />
            {item.source === 'override' && (
              <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
                已修改
              </span>
            )}
            {item.danger && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-900 dark:bg-amber-950 dark:text-amber-100">
                敏感
              </span>
            )}
          </div>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">{item.key}</p>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {saving && (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              保存中
            </span>
          )}
          {!saving && (
            <Button
              size="icon-xs"
              variant="ghost"
              onClick={onReset}
              disabled={disabled || item.source !== 'override'}
              title={item.source === 'override' ? '重置为默认值' : '已是默认值'}
            >
              <RotateCcw />
            </Button>
          )}
        </div>
      </div>

      <div
        className={cn('mt-2', disabled && 'pointer-events-none opacity-50')}
        aria-disabled={disabled || undefined}
      >
        <FieldControl item={item} value={value} onChange={onChange} />
      </div>

      {error && (
        <p className="mt-1.5 text-xs text-destructive">{error}</p>
      )}
      {item.side_effect_hint && !error && (
        <p className="mt-1.5 text-xs text-muted-foreground">提示：{item.side_effect_hint}</p>
      )}
    </div>
  )
}

// ─── 详情 hint：info 图标 hover / focus 弹 tooltip ─────────────────────

function DetailHint({ item }: { item: ConfigItemView }) {
  const lines = [
    item.detail,
    item.options ? `可选值：${item.options.join(' / ')}` : null,
    typeof item.min === 'number' || typeof item.max === 'number'
      ? `范围：${item.min ?? '−∞'} ~ ${item.max ?? '+∞'}`
      : null,
    `默认值：${formatValue(item.default)}`,
  ].filter(Boolean)
  return (
    <span className="group/hint relative inline-flex">
      <span
        className="inline-flex cursor-help text-muted-foreground hover:text-foreground"
        tabIndex={0}
        aria-label={`详细说明：${item.brief}`}
      >
        <Info className="size-3.5" />
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-50 mt-1 hidden w-max max-w-[380px] whitespace-pre-line wrap-break-word rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs leading-relaxed text-popover-foreground shadow-md group-hover/hint:block group-focus-within/hint:block"
      >
        {lines.join('\n')}
      </span>
    </span>
  )
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.length === 0 ? '[]' : v.join(', ')
  return String(v)
}

// ─── 控件分发 ──────────────────────────────────────────────────────────

function FieldControl({
  item,
  value,
  onChange,
}: {
  item: ConfigItemView
  value: unknown
  onChange: (next: unknown) => void
}) {
  if (!item.editable) {
    return (
      <span className="font-mono text-xs text-muted-foreground">
        {formatValue(value)}
      </span>
    )
  }
  switch (item.type) {
    case 'bool':
      return (
        <Switch
          id={`cfg-${item.key}`}
          checked={Boolean(value)}
          onCheckedChange={(v: boolean) => onChange(v)}
        />
      )
    case 'int':
    case 'float':
      return (
        <NumberInput
          id={`cfg-${item.key}`}
          value={typeof value === 'number' ? value : Number(value ?? 0)}
          min={item.min ?? undefined}
          max={item.max ?? undefined}
          step={item.type === 'int' ? 1 : 0.01}
          onChange={(v) => onChange(item.type === 'int' ? Math.trunc(v) : v)}
        />
      )
    case 'enum_str': {
      const opts = item.options ?? []
      // ≤4 项用 RadioGroup；>4 用 Select
      if (opts.length > 0 && opts.length <= 4) {
        return (
          <RadioGroup
            name={`cfg-${item.key}`}
            value={String(value ?? '')}
            options={opts}
            onChange={onChange}
          />
        )
      }
      return (
        <NativeSelect
          id={`cfg-${item.key}`}
          value={String(value ?? '')}
          options={opts}
          onChange={onChange}
        />
      )
    }
    case 'multi_enum_str':
      return (
        <CheckboxList
          name={`cfg-${item.key}`}
          value={Array.isArray(value) ? (value as string[]) : []}
          options={item.options ?? []}
          onChange={onChange}
        />
      )
    case 'string':
    case 'path':
    default:
      return (
        <Input
          id={`cfg-${item.key}`}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
        />
      )
  }
}

// ─── 复用的小控件 ──────────────────────────────────────────────────────

function NumberInput({
  value,
  min,
  max,
  step,
  id,
  onChange,
}: {
  value: number
  min?: number
  max?: number
  step: number
  id: string
  onChange: (next: number) => void
}) {
  // 用本地 string state 管理输入态，避免边输入边改动数字（如清空 → 退化成 0 抖动）
  const [text, setText] = useState<string>(String(value))
  const lastValueRef = useRef<number>(value)

  useEffect(() => {
    if (value !== lastValueRef.current) {
      setText(String(value))
      lastValueRef.current = value
    }
  }, [value])

  return (
    <Input
      id={id}
      type="number"
      value={text}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const t = e.target.value
        setText(t)
        const n = Number(t)
        if (t === '' || Number.isNaN(n)) return
        lastValueRef.current = n
        onChange(n)
      }}
      className="max-w-[160px]"
    />
  )
}

function RadioGroup({
  name,
  value,
  options,
  onChange,
}: {
  name: string
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-3">
      {options.map((opt) => (
        <label
          key={opt}
          className="inline-flex cursor-pointer items-center gap-1.5 text-sm"
        >
          <input
            type="radio"
            name={name}
            value={opt}
            checked={value === opt}
            onChange={() => onChange(opt)}
            className="accent-primary"
          />
          <span className="font-mono text-xs">{opt}</span>
        </label>
      ))}
    </div>
  )
}

function NativeSelect({
  id,
  value,
  options,
  onChange,
}: {
  id: string
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  // 跟 MemoryView 添加记忆下拉一致：bg-background + color-scheme 控制 popup 的亮 / 暗模式
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="flex h-9 w-full max-w-[260px] rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring scheme-light dark:scheme-dark"
    >
      {options.map((opt) => (
        <option key={opt} value={opt}>
          {opt}
        </option>
      ))}
    </select>
  )
}

function CheckboxList({
  name,
  value,
  options,
  onChange,
}: {
  name: string
  value: string[]
  options: string[]
  onChange: (next: string[]) => void
}) {
  const set = new Set(value)
  return (
    <div className="flex flex-wrap gap-3">
      {options.map((opt) => {
        const checked = set.has(opt)
        return (
          <label
            key={opt}
            className="inline-flex cursor-pointer items-center gap-1.5 text-sm"
          >
            <input
              type="checkbox"
              name={name}
              value={opt}
              checked={checked}
              onChange={() => {
                const next = new Set(set)
                if (checked) next.delete(opt)
                else next.add(opt)
                onChange(options.filter((o) => next.has(o)))
              }}
              className="accent-primary"
            />
            <span className="font-mono text-xs">{opt}</span>
          </label>
        )
      })}
    </div>
  )
}
