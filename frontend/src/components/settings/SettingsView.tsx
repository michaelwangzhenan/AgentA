import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { ConfigField } from '@/components/settings/ConfigField'
import { getConfig, patchConfig, reloadConfig, resetConfig } from '@/api/client'
import type { ConfigGroupView, ConfigItemView } from '@/types/config'
import { cn } from '@/lib/utils'
import { toast } from '@/lib/toast'

type LocalEdit = {
  value: unknown
  error: string | null
  saving: boolean
}

// 自动保存延时：text / number 输入用 debounce 避免每按一键都 PATCH；
// switch / select / radio / checkbox 是离散动作，立即触发。
const DEBOUNCE_MS = 600

function isInstantType(t: ConfigItemView['type']): boolean {
  return t === 'bool' || t === 'enum_str' || t === 'multi_enum_str'
}

export function SettingsView({ embedded = false }: { embedded?: boolean } = {}) {
  const [groups, setGroups] = useState<ConfigGroupView[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [edits, setEdits] = useState<Record<string, LocalEdit>>({})
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [pendingDanger, setPendingDanger] = useState<{ key: string; value: unknown } | null>(null)

  // 延时保存定时器（每个 key 独立，新 change 来了清旧再排）
  const saveTimersRef = useRef<Record<string, ReturnType<typeof setTimeout> | undefined>>({})
  // 最新 groups 的引用，让 commitSave 拿到最新 item.side_effect_hint 等
  const groupsRef = useRef<ConfigGroupView[]>(groups)
  useEffect(() => {
    groupsRef.current = groups
  }, [groups])

  const refresh = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await getConfig()
      setGroups(res.groups)
      setActiveGroup((prev) => prev ?? res.groups[0]?.name ?? null)
    } catch (e) {
      setLoadError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleReload = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await reloadConfig()
      setGroups(res.config.groups)
      setActiveGroup((prev) => prev ?? res.config.groups[0]?.name ?? null)
      const n = res.changed_keys.length
      if (n === 0) {
        toast.success('overrides 文件已是最新，无变化')
      } else {
        toast.success(`已从文件同步 ${n} 项配置：${res.changed_keys.join(', ')}`)
      }
    } catch (e) {
      setLoadError((e as Error).message)
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 卸载时清掉所有 pending 定时器，避免内存泄漏 + 切走 view 又触发保存
  useEffect(() => {
    const timers = saveTimersRef.current
    return () => {
      for (const t of Object.values(timers)) {
        if (t) clearTimeout(t)
      }
    }
  }, [])

  const inflightCount = Object.values(edits).filter((e) => e.saving).length

  // 有保存在飞 / 有 debounce 排队时，离开页面给浏览器原生警告
  const hasPendingWork = inflightCount > 0 || Object.keys(saveTimersRef.current).length > 0
  useEffect(() => {
    if (!hasPendingWork) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasPendingWork])

  // ─── 单项操作 ─────────────────────────────────────────────────────

  const cancelPendingSave = (key: string) => {
    const existing = saveTimersRef.current[key]
    if (existing) {
      clearTimeout(existing)
      saveTimersRef.current[key] = undefined
    }
  }

  const scheduleSave = (item: ConfigItemView, value: unknown) => {
    cancelPendingSave(item.key)
    const fire = () => {
      saveTimersRef.current[item.key] = undefined
      void commitSave(item.key, value)
    }
    if (isInstantType(item.type)) {
      fire()
    } else {
      saveTimersRef.current[item.key] = setTimeout(fire, DEBOUNCE_MS)
    }
  }

  const setLocalValue = (item: ConfigItemView, value: unknown) => {
    // 改回原值 → 取消 pending、清掉 edit
    if (deepEqual(value, item.value)) {
      cancelPendingSave(item.key)
      setEdits((prev) => {
        const next = { ...prev }
        delete next[item.key]
        return next
      })
      return
    }

    setEdits((prev) => ({
      ...prev,
      [item.key]: { value, error: null, saving: prev[item.key]?.saving ?? false },
    }))

    // 危险项：先弹二次确认 Dialog，确认后才保存
    if (item.danger) {
      cancelPendingSave(item.key)
      setPendingDanger({ key: item.key, value })
      return
    }

    scheduleSave(item, value)
  }

  const commitSave = async (key: string, value: unknown) => {
    setEdits((prev) => ({
      ...prev,
      [key]: { value, error: null, saving: true },
    }))
    try {
      const updated = await patchConfig(key, value)
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          items: g.items.map((it) => (it.key === key ? updated : it)),
        })),
      )
      setEdits((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
      // 副作用 hint 已 inline 显示在行内，不再 toast，避免每次拨动 Switch 都弹
    } catch (e) {
      setEdits((prev) => ({
        ...prev,
        [key]: { value, saving: false, error: (e as Error).message },
      }))
      toast.error((e as Error).message)
    }
  }

  // 危险项 Dialog: 取消时把 local edit 也回滚（让 UI 控件视觉态回到原值）
  const cancelDanger = () => {
    if (pendingDanger) {
      setEdits((prev) => {
        const next = { ...prev }
        delete next[pendingDanger.key]
        return next
      })
    }
    setPendingDanger(null)
  }

  const confirmDanger = async () => {
    if (!pendingDanger) return
    const { key, value } = pendingDanger
    setPendingDanger(null)
    await commitSave(key, value)
  }

  const resetItem = async (item: ConfigItemView) => {
    try {
      const updated = await resetConfig(item.key)
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          items: g.items.map((it) => (it.key === item.key ? updated : it)),
        })),
      )
      toast.success(`${item.brief} 已重置`)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // ─── 搜索过滤 ─────────────────────────────────────────────────────
  // 无搜索：只显示当前 activeGroup；有搜索：跨所有组展示命中项（VSCode 设置面板风格）
  const searching = search.trim().length > 0
  const visibleGroups = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) {
      const g = groups.find((it) => it.name === activeGroup)
      if (!g) return []
      return [{ ...g, items: g.items.filter((it) => !it.hidden) }]
    }
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((it) => {
          if (it.hidden) return false
          return (
            it.key.toLowerCase().includes(q) ||
            it.brief.toLowerCase().includes(q) ||
            it.detail.toLowerCase().includes(q)
          )
        }),
      }))
      .filter((g) => g.items.length > 0)
  }, [groups, search, activeGroup])

  // 各组在搜索状态下的命中数（仅命中数 > 0 的组在左导航上显示徽章）
  const groupMatchCounts = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return {} as Record<string, number>
    const counts: Record<string, number> = {}
    for (const g of groups) {
      counts[g.name] = g.items.filter(
        (it) =>
          !it.hidden &&
          (it.key.toLowerCase().includes(q) ||
            it.brief.toLowerCase().includes(q) ||
            it.detail.toLowerCase().includes(q)),
      ).length
    }
    return counts
  }, [groups, search])

  // 各组的 inflight 保存项数（左导航徽章；用 inflight 而不是单纯 edits，
  // 因为 edits 里也含已经保存失败留下来的 error 状态项）
  const groupSavingCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const [key, ed] of Object.entries(edits)) {
      if (!ed.saving) continue
      const item = findItem(groups, key)
      if (!item) continue
      counts[item.group] = (counts[item.group] ?? 0) + 1
    }
    return counts
  }, [edits, groups])

  const toolbar = (
    <>
      {inflightCount > 0 && (
        <span className="text-xs text-muted-foreground">{inflightCount} 项保存中…</span>
      )}
      <Button
        onClick={handleReload}
        size="sm"
        variant="outline"
        disabled={loading}
        title="重新读取 .agenta/config_overrides.json 并同步到内存；手动改过该文件后用。"
      >
        从文件重载
      </Button>
      <Button onClick={refresh} size="sm" variant="outline" disabled={loading}>
        刷新
      </Button>
    </>
  )

  const content = (
    <>
      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {loadError}
        </div>
      )}

      {/* 搜索 */}
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="按 key / 名称 / 描述搜索…"
          className="pl-7"
        />
      </div>

      {loading && groups.length === 0 && (
        <p className="text-sm text-muted-foreground">加载中…</p>
      )}

      {/* 左导航 + 右内容 */}
      {!loading && groups.length > 0 && (
        <div className="flex flex-1 gap-4 min-h-0">
          {/* 左侧分组导航 */}
          <nav className="sticky top-0 w-36 shrink-0 self-start">
            <ul className="space-y-0.5">
              {groups.map((g) => {
                const matchCount = groupMatchCounts[g.name]
                const savingN = groupSavingCounts[g.name]
                const isActive = !searching && activeGroup === g.name
                const dimmed = searching && (matchCount ?? 0) === 0
                return (
                  <li key={g.name}>
                    <button
                      type="button"
                      onClick={() => {
                        setActiveGroup(g.name)
                        // 用户点导航时清掉搜索，回到单组视图
                        if (searching) setSearch('')
                      }}
                      className={cn(
                        'flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                        isActive
                          ? 'bg-muted font-medium text-foreground'
                          : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                        dimmed && 'opacity-50',
                      )}
                    >
                      <span>{g.label}</span>
                      <span className="flex items-center gap-1">
                        {savingN ? (
                          <span className="rounded bg-primary/15 px-1 text-[10px] text-primary">
                            {savingN}
                          </span>
                        ) : null}
                        {searching && (matchCount ?? 0) > 0 ? (
                          <span className="rounded bg-primary/15 px-1 text-[10px] text-primary">
                            {matchCount}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>

          {/* 右侧内容 */}
          <div className="min-w-0 flex-1 space-y-4">
            {visibleGroups.length === 0 && searching && (
              <p className="text-sm text-muted-foreground">没有匹配的配置项</p>
            )}

            {visibleGroups.map((g) => {
              const renderField = (item: ConfigItemView) => {
                const edit = edits[item.key]
                return (
                  <ConfigField
                    key={item.key}
                    item={item}
                    localValue={edit?.value}
                    error={edit?.error ?? null}
                    saving={edit?.saving}
                    onChange={(v) => setLocalValue(item, v)}
                    onReset={() => resetItem(item)}
                  />
                )
              }
              return (
                <section key={g.name} className="space-y-3">
                  {/* 仅搜索状态下展示组标题（多组并存）；非搜索是单组视图，标题冗余 */}
                  {searching && (
                    <h2 className="border-b border-border pb-1 text-sm font-semibold tracking-tight">
                      {g.label}
                    </h2>
                  )}
                  {splitSections(g.items).map(({ section, items }) =>
                    section ? (
                      // 有子分区：标题 + 卡片把同类项框在一起
                      <div
                        key={section}
                        className="overflow-hidden rounded-lg border border-border/60 bg-muted/20"
                      >
                        <div className="border-b border-border/60 bg-muted/40 px-3 py-1.5 text-xs font-semibold tracking-wide text-muted-foreground">
                          {section}
                        </div>
                        <div className="space-y-2 p-3">{items.map(renderField)}</div>
                      </div>
                    ) : (
                      // 无子分区：直接平铺
                      <div key="__nosection" className="space-y-2">
                        {items.map(renderField)}
                      </div>
                    ),
                  )}
                </section>
              )
            })}
          </div>
        </div>
      )}

      {/* 危险项二次确认 */}
      <AlertDialog
        open={pendingDanger !== null}
        onOpenChange={(open) => {
          // 关闭 = 取消（无论点 X 还是 ESC）：回滚 local edit
          if (!open) cancelDanger()
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认修改敏感配置</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDanger
                ? `${pendingDanger.key} 是敏感配置，改动会立即影响安全相关行为。是否继续？`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={cancelDanger}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDanger}>确认修改</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )

  // 嵌入设置弹窗时去掉整页 ResourcePage 外壳，只保留工具条 + 正文（自带滚动）
  if (embedded) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="flex items-center justify-end gap-2">{toolbar}</div>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
          {content}
        </div>
      </div>
    )
  }

  return (
    <ResourcePage
      title="设置"
      subtitle="改完立即生效；持久化到 .agenta/config_overrides.json，下次启动仍生效"
      toolbar={toolbar}
    >
      {content}
    </ResourcePage>
  )
}

// ─── helpers ──────────────────────────────────────────────────────

/** 把组内的项按 section 聚成有序块（保留首次出现顺序）；无 section 的归到 null 块。 */
function splitSections(
  items: ConfigItemView[],
): { section: string | null; items: ConfigItemView[] }[] {
  const order: (string | null)[] = []
  const buckets = new Map<string | null, ConfigItemView[]>()
  for (const it of items) {
    const sec = it.section ?? null
    if (!buckets.has(sec)) {
      buckets.set(sec, [])
      order.push(sec)
    }
    buckets.get(sec)!.push(it)
  }
  return order.map((sec) => ({ section: sec, items: buckets.get(sec)! }))
}

function findItem(groups: ConfigGroupView[], key: string): ConfigItemView | undefined {
  for (const g of groups) {
    const m = g.items.find((it) => it.key === key)
    if (m) return m
  }
  return undefined
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false
    return a.every((v, i) => deepEqual(v, b[i]))
  }
  return false
}
