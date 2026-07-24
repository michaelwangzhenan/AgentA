import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownAZ,
  ArrowUpAZ,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Eye,
  Pencil,
  PauseCircle,
  Plus,
  RefreshCw,
  Search,
  SplitSquareHorizontal,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { MarkdownEditor } from '@/components/ui/markdown-editor'
import { MarkdownPreview } from '@/components/ui/markdown-preview'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  createSkill,
  deleteSkill,
  listSkills,
  reloadSkills,
  renameSkill,
  toggleSkill,
  updateSkill,
} from '@/api/client'
import type { SkillItem, SkillsResponse } from '@/types/resources'
import { ResourcePage } from '@/components/resources/ResourcePage'
import { toast } from '@/lib/toast'
import { cn } from '@/lib/utils'
import { useUrlState } from '@/routes/useUrlState'

const NAME_PATTERN = /^[a-zA-Z0-9_-]+$/

// 新建 skill 时预填的 SKILL.md 骨架（#6 模板化）
const NEW_SKILL_TEMPLATE = `## 何时使用

描述这个 skill 适合在什么场景被调用。

## 步骤

1. 第一步
2. 第二步

## 注意事项

- 约束 / 边界条件 / 易错点
`

type ViewMode = 'edit' | 'split' | 'preview'

type SortDir = 'asc' | 'desc'

// ============================================================================
// 顶层视图
// ============================================================================

export function SkillsView() {
  const url = useUrlState()
  const [data, setData] = useState<SkillsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [reloading, setReloading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const expanded = useMemo(() => new Set(url.getCsv('open')), [url.searchParams])
  const [editing, setEditing] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  const query = url.get('q')
  const sortDir: SortDir = url.get('sort') === 'desc' ? 'desc' : 'asc'
  const setQuery = (v: string) => url.patch({ q: v || null })
  const setSortDir = (d: SortDir) => url.patch({ sort: d === 'asc' ? null : d })

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await listSkills())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleReload = async () => {
    setReloading(true)
    try {
      const resp = await reloadSkills()
      const parts = [
        `${resp.loaded_count} 个加载成功`,
        resp.disabled_count > 0 ? `${resp.disabled_count} 个已禁用` : null,
        resp.failed_count > 0 ? `${resp.failed_count} 个失败` : null,
      ].filter(Boolean)
      toast.success(`已重新扫描：${parts.join('，')}。新对话立即生效，已开对话需新建 session。`)
      await refresh()
    } catch (e) {
      toast.error(`重载失败：${(e as Error).message}`)
    } finally {
      setReloading(false)
    }
  }

  const toggleExpand = (name: string) => {
    const next = new Set(expanded)
    if (next.has(name)) {
      next.delete(name)
      if (editing === name) setEditing(null)
    } else {
      next.add(name)
    }
    const arr = [...next]
    url.patch({ open: arr.length ? arr.join(',') : null })
  }

  const handleToggle = async (name: string, enabled: boolean) => {
    try {
      await toggleSkill(name, enabled)
      toast.success(`${name} 已${enabled ? '启用' : '禁用'}。新对话生效。`)
      await refresh()
    } catch (e) {
      toast.error(`切换失败：${(e as Error).message}`)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleteBusy(true)
    try {
      await deleteSkill(deleteTarget)
      toast.success(`已删除 skill：${deleteTarget}`)
      setDeleteTarget(null)
      await refresh()
    } catch (e) {
      toast.error(`删除失败：${(e as Error).message}`)
    } finally {
      setDeleteBusy(false)
    }
  }

  // 过滤 + 排序：作用于 loaded / disabled 两个数组（保持分组）
  const filteredLoaded = useMemo(
    () => filterAndSort(data?.loaded ?? [], query, sortDir),
    [data?.loaded, query, sortDir],
  )
  const filteredDisabled = useMemo(
    () => filterAndSort(data?.disabled ?? [], query, sortDir),
    [data?.disabled, query, sortDir],
  )

  const busy = loading || reloading
  const existingNames = new Set([
    ...(data?.loaded.map((s) => s.name) ?? []),
    ...(data?.disabled.map((s) => s.name) ?? []),
  ])

  // toolbar 内容：搜索 / 排序 / 新建 / 重新加载，挪到"已启用" section header 右侧
  const sectionToolbar = (
    <>
      <SearchBox value={query} onChange={setQuery} disabled={busy} />
      <Button
        onClick={() => setSortDir(sortDir === 'asc' ? 'desc' : 'asc')}
        size="sm"
        variant="outline"
        disabled={busy}
        className="gap-1.5"
        aria-label={`按名称排序，当前 ${sortDir === 'asc' ? '升序' : '降序'}`}
      >
        {sortDir === 'asc' ? (
          <ArrowDownAZ className="h-3.5 w-3.5" />
        ) : (
          <ArrowUpAZ className="h-3.5 w-3.5" />
        )}
        名称 {sortDir === 'asc' ? 'A→Z' : 'Z→A'}
      </Button>
      <Button
        onClick={() => setCreateOpen(true)}
        size="sm"
        disabled={busy}
        className="gap-1.5"
      >
        <Plus className="h-3.5 w-3.5" />
        新建 Skill
      </Button>
      <Button
        onClick={handleReload}
        size="sm"
        variant="outline"
        disabled={busy}
        className="gap-1.5"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${reloading ? 'animate-spin' : ''}`} />
        {reloading ? '重载中…' : '重新加载'}
      </Button>
    </>
  )

  return (
    <ResourcePage
      title="Skills"
      subtitle="管理 .agenta/skills/ 下的 SKILL.md（点击行查看 body；可编辑 / 改名 / 新建 / 删除 / 启停）"
    >
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
          {error}
        </div>
      )}

      <SkillsSection
        title="已启用"
        icon={<CheckCircle2 className="h-4 w-4 text-green-600" />}
        items={filteredLoaded}
        loading={loading}
        emptyMessage={query ? '无匹配项' : '无'}
        expanded={expanded}
        editing={editing}
        existingNames={existingNames}
        actions={sectionToolbar}
        onToggleExpand={toggleExpand}
        onStartEdit={(name) => setEditing(name)}
        onCancelEdit={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          refresh()
        }}
        onToggle={handleToggle}
        onDelete={(name) => setDeleteTarget(name)}
        enabled={true}
      />

      {(data?.disabled.length ?? 0) > 0 && (
        <SkillsSection
          title="已禁用"
          icon={<PauseCircle className="h-4 w-4 text-muted-foreground" />}
          items={filteredDisabled}
          loading={false}
          emptyMessage={query ? '无匹配项' : ''}
          expanded={expanded}
          editing={editing}
          existingNames={existingNames}
          onToggleExpand={toggleExpand}
          onStartEdit={(name) => setEditing(name)}
          onCancelEdit={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            refresh()
          }}
          onToggle={handleToggle}
          onDelete={(name) => setDeleteTarget(name)}
          enabled={false}
        />
      )}

      {data && data.failed.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950">
          <div className="flex items-center gap-2 border-b border-amber-200 px-3 py-2 text-sm font-medium dark:border-amber-900">
            <AlertTriangle className="h-4 w-4 text-amber-600" />
            加载失败 ({data.failed.length})
          </div>
          <ul className="divide-y divide-amber-200 dark:divide-amber-900">
            {data.failed.map((f, idx) => (
              <li key={idx} className="px-3 py-2 text-sm">
                <div className="truncate font-mono text-[11px]" title={f.path}>
                  {f.path}
                </div>
                <div className="text-amber-900 dark:text-amber-200">{f.reason}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <CreateSkillDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        existingNames={existingNames}
        onCreated={() => {
          setCreateOpen(false)
          refresh()
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(o) => !o && !deleteBusy && setDeleteTarget(null)}
        title={`删除 skill：${deleteTarget}？`}
        description={
          <>
            将删除 <code>.agenta/skills/{deleteTarget}/</code>{' '}
            整个目录（含 scripts/ 等子文件）。该操作不可恢复。
          </>
        }
        loading={deleteBusy}
        confirmLabel="删除"
        onConfirm={handleDelete}
        contentProps={{
          onKeyDown: (e) => {
            if (deleteBusy) return
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void handleDelete()
            }
          },
        }}
      />
    </ResourcePage>
  )
}

// ============================================================================
// 工具函数
// ============================================================================

function filterAndSort(
  items: SkillItem[],
  query: string,
  dir: SortDir,
): SkillItem[] {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? items.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q),
      )
    : items
  const sorted = [...filtered].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, 'zh-Hans-CN', {
      sensitivity: 'base',
    })
    return dir === 'asc' ? cmp : -cmp
  })
  return sorted
}

// ============================================================================
// SearchBox
// ============================================================================

function SearchBox({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled: boolean
}) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder="搜索 name / description"
        className="h-8 w-56 pl-7 pr-7 text-xs"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label="清除搜索"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

// ============================================================================
// SkillsSection / SkillRow
// ============================================================================

type SkillsSectionProps = {
  title: string
  icon: React.ReactNode
  items: SkillItem[]
  loading: boolean
  emptyMessage: string
  expanded: Set<string>
  editing: string | null
  enabled: boolean
  existingNames: Set<string>
  /** section header 右侧的工具栏槽位（只对"已启用"section 传入）*/
  actions?: React.ReactNode
  onToggleExpand: (name: string) => void
  onStartEdit: (name: string) => void
  onCancelEdit: () => void
  onSaved: () => void
  onToggle: (name: string, enabled: boolean) => void
  onDelete: (name: string) => void
}

function SkillsSection({
  title,
  icon,
  items,
  loading,
  emptyMessage,
  expanded,
  editing,
  enabled,
  existingNames,
  actions,
  onToggleExpand,
  onStartEdit,
  onCancelEdit,
  onSaved,
  onToggle,
  onDelete,
}: SkillsSectionProps) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm font-medium">
        <div className="flex items-center gap-2">
          {icon}
          {title} ({items.length})
        </div>
        {actions && (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </div>
      {loading ? (
        <p className="px-3 py-2 text-sm text-muted-foreground">加载中…</p>
      ) : items.length === 0 ? (
        emptyMessage && <p className="px-3 py-2 text-sm text-muted-foreground">{emptyMessage}</p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((s) => (
            <SkillRow
              key={s.location}
              skill={s}
              enabled={enabled}
              expanded={expanded.has(s.name)}
              editing={editing === s.name}
              existingNames={existingNames}
              onToggleExpand={() => onToggleExpand(s.name)}
              onStartEdit={() => onStartEdit(s.name)}
              onCancelEdit={onCancelEdit}
              onSaved={onSaved}
              onToggle={(en) => onToggle(s.name, en)}
              onDelete={() => onDelete(s.name)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

type SkillRowProps = {
  skill: SkillItem
  enabled: boolean
  expanded: boolean
  editing: boolean
  existingNames: Set<string>
  onToggleExpand: () => void
  onStartEdit: () => void
  onCancelEdit: () => void
  onSaved: () => void
  onToggle: (enabled: boolean) => void
  onDelete: () => void
}

function SkillRow({
  skill,
  enabled,
  expanded,
  editing,
  existingNames,
  onToggleExpand,
  onStartEdit,
  onCancelEdit,
  onSaved,
  onToggle,
  onDelete,
}: SkillRowProps) {
  return (
    <li className="text-sm">
      <div className="flex items-start gap-2 px-3 py-2">
        <Switch
          checked={enabled}
          onCheckedChange={onToggle}
          aria-label={`${enabled ? '禁用' : '启用'} ${skill.name}`}
          className="mt-0.5"
        />
        <button
          type="button"
          onClick={onToggleExpand}
          className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={expanded ? '折叠' : '展开'}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <button
          type="button"
          onClick={onToggleExpand}
          className="flex-1 min-w-0 text-left"
        >
          <div className="font-medium">{skill.name}</div>
          <div className={`${enabled ? 'text-foreground/80' : 'text-muted-foreground'}`}>
            {skill.description}
          </div>
          <div
            className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground"
            title={skill.location}
          >
            {skill.location}
          </div>
        </button>
        <div className="flex shrink-0 gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={onStartEdit}
            disabled={editing}
            aria-label="编辑"
            className="h-7 w-7 p-0"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            aria-label="删除"
            className="h-7 w-7 p-0 text-destructive hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {expanded && !editing && (
        <div className="border-t border-border bg-muted/30 px-3 py-2">
          <MarkdownPreview source={skill.body} />
        </div>
      )}

      {editing && (
        <EditSkillForm
          skill={skill}
          existingNames={existingNames}
          onCancel={onCancelEdit}
          onSaved={onSaved}
        />
      )}
    </li>
  )
}

// ============================================================================
// View mode tabs（Edit / Split / Preview）
// ============================================================================

function ViewModeTabs({
  mode,
  onChange,
}: {
  mode: ViewMode
  onChange: (m: ViewMode) => void
}) {
  const items: { id: ViewMode; label: string; icon: React.ReactNode }[] = [
    { id: 'edit', label: 'Edit', icon: <SquarePen className="h-3.5 w-3.5" /> },
    { id: 'split', label: 'Split', icon: <SplitSquareHorizontal className="h-3.5 w-3.5" /> },
    { id: 'preview', label: 'Preview', icon: <Eye className="h-3.5 w-3.5" /> },
  ]
  return (
    <div className="inline-flex items-center gap-0.5 rounded-md border border-border bg-muted/30 p-0.5">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          onClick={() => onChange(it.id)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors',
            mode === it.id
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          )}
          aria-pressed={mode === it.id}
        >
          {it.icon}
          {it.label}
        </button>
      ))}
    </div>
  )
}

function BodyEditorWithPreview({
  value,
  onChange,
  disabled,
  mode,
  placeholder,
  fillHeight = false,
}: {
  value: string
  onChange: (v: string) => void
  disabled: boolean
  mode: ViewMode
  placeholder?: string
  /** true: 撑满父级高度（父级要 flex-1 + min-h-0）；false: 用 min-h-[240px] 行为 */
  fillHeight?: boolean
}) {
  const previewBoxClass = fillHeight
    ? 'h-full overflow-auto rounded-md border border-border bg-muted/20 p-3'
    : 'min-h-[240px] overflow-auto rounded-md border border-border bg-muted/20 p-3'

  if (mode === 'preview') {
    return (
      <div className={previewBoxClass}>
        <MarkdownPreview source={value} />
      </div>
    )
  }
  if (mode === 'split') {
    return (
      <div
        className={cn(
          'grid grid-cols-1 gap-2 lg:grid-cols-2',
          fillHeight && 'h-full',
        )}
      >
        <MarkdownEditor
          value={value}
          onChange={onChange}
          disabled={disabled}
          placeholder={placeholder}
          fillHeight={fillHeight}
        />
        <div className={previewBoxClass}>
          <MarkdownPreview source={value} />
        </div>
      </div>
    )
  }
  return (
    <MarkdownEditor
      value={value}
      onChange={onChange}
      disabled={disabled}
      placeholder={placeholder}
      fillHeight={fillHeight}
    />
  )
}

// ============================================================================
// EditSkillForm（含改名）
// ============================================================================

type EditSkillFormProps = {
  skill: SkillItem
  existingNames: Set<string>
  onCancel: () => void
  onSaved: () => void
}

function EditSkillForm({
  skill,
  existingNames,
  onCancel,
  onSaved,
}: EditSkillFormProps) {
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description)
  const [body, setBody] = useState(skill.body)
  const [saving, setSaving] = useState(false)
  const [mode, setMode] = useState<ViewMode>('split')

  const nameChanged = name !== skill.name
  const nameError = (() => {
    if (!nameChanged) return null
    if (!name) return 'name 不能为空'
    if (!NAME_PATTERN.test(name)) return 'name 只能含字母 / 数字 / 下划线 / 连字符'
    if (name.length > 64) return 'name 超过 64 字符'
    if (existingNames.has(name)) return `name "${name}" 已存在`
    return null
  })()

  const handleSave = async () => {
    if (!description.trim()) {
      toast.error('description 不能为空')
      return
    }
    if (nameError) {
      toast.error(nameError)
      return
    }
    setSaving(true)
    try {
      // 改名要先 rename 再 update（rename 会重写 SKILL.md，update 把当前 description / body 写回）
      let targetName = skill.name
      if (nameChanged) {
        await renameSkill(skill.name, { new_name: name })
        targetName = name
      }
      await updateSkill(targetName, {
        description: description.trim(),
        body,
        // 不传 frontmatter_extra → 后端保留磁盘已有 extra 字段（passthrough）
      })
      toast.success(
        nameChanged
          ? `已改名 ${skill.name} → ${name} 并保存`
          : `已更新 skill：${skill.name}`,
      )
      onSaved()
    } catch (e) {
      toast.error(`保存失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  const actionButtons = (
    <div className="flex shrink-0 gap-2">
      <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
        取消
      </Button>
      <Button size="sm" onClick={handleSave} disabled={saving || nameError !== null}>
        {saving ? '保存中…' : '保存'}
      </Button>
    </div>
  )

  return (
    <div className="border-t border-border bg-muted/30 px-3 py-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          编辑：<span className="font-mono">{skill.name}</span>
        </span>
        {actionButtons}
      </div>
      <div>
        <label className="block text-xs font-medium text-muted-foreground mb-1">
          name {nameChanged && <span className="text-amber-600">（修改将触发改名）</span>}
        </label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={saving}
          placeholder="skill 唯一标识（同时是目录名）"
        />
        {nameError && <p className="mt-1 text-xs text-destructive">{nameError}</p>}
      </div>
      <div>
        <label className="block text-xs font-medium text-muted-foreground mb-1">description</label>
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={saving}
          placeholder="skill 触发描述（LLM 看到的）"
        />
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-medium text-muted-foreground">
            body (Markdown)
          </label>
          <ViewModeTabs mode={mode} onChange={setMode} />
        </div>
        <BodyEditorWithPreview
          value={body}
          onChange={setBody}
          disabled={saving}
          mode={mode}
          placeholder="SKILL.md frontmatter 之后的正文"
        />
      </div>
      <div className="flex justify-end gap-2 pt-1">{actionButtons}</div>
    </div>
  )
}

// ============================================================================
// CreateSkillDialog（含模板预填 + split view）
// ============================================================================

type CreateSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingNames: Set<string>
  onCreated: () => void
}

function CreateSkillDialog({
  open,
  onOpenChange,
  existingNames,
  onCreated,
}: CreateSkillDialogProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [body, setBody] = useState(NEW_SKILL_TEMPLATE)
  const [saving, setSaving] = useState(false)
  const [mode, setMode] = useState<ViewMode>('split')

  useEffect(() => {
    if (!open) {
      setName('')
      setDescription('')
      setBody(NEW_SKILL_TEMPLATE)
      setMode('split')
    }
  }, [open])

  const nameError = (() => {
    if (!name) return null
    if (!NAME_PATTERN.test(name)) return 'name 只能含字母 / 数字 / 下划线 / 连字符'
    if (name.length > 64) return 'name 超过 64 字符'
    if (existingNames.has(name)) return `name "${name}" 已存在`
    return null
  })()

  const canSave =
    name.length > 0 && nameError === null && description.trim().length > 0 && !saving

  const handleCreate = async () => {
    if (!canSave) return
    setSaving(true)
    try {
      await createSkill({ name, description: description.trim(), body })
      toast.success(`已创建 skill：${name}`)
      onCreated()
    } catch (e) {
      toast.error(`创建失败：${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          // 默认 DialogContent 是 grid + sm:max-w-sm；这里用 flex column 撑满 viewport
          'grid-cols-none! flex flex-col',
          'w-[calc(100vw-2rem)] sm:max-w-none',
          'max-w-[1400px]',
          'h-[calc(100vh-2rem)] max-h-[900px]',
        )}
      >
        <DialogHeader>
          <DialogTitle>新建 Skill</DialogTitle>
          <DialogDescription>
            创建 <code>.agenta/skills/&lt;name&gt;/SKILL.md</code>；保存后会自动重载 Agent。
          </DialogDescription>
        </DialogHeader>

        {/* 中间内容区 flex-1 撑满；name/description 固定，body section 再 flex-1 让编辑器顶到底 */}
        <div className="flex flex-1 min-h-0 flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">name</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={saving}
                placeholder="my_skill"
                autoFocus
              />
              {nameError && <p className="mt-1 text-xs text-destructive">{nameError}</p>}
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">description</label>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={saving}
                placeholder="说明 LLM 何时该调这个 skill"
              />
            </div>
          </div>
          <div className="flex flex-1 min-h-0 flex-col">
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground">
                body (Markdown，可空；已预填模板)
              </label>
              <ViewModeTabs mode={mode} onChange={setMode} />
            </div>
            <div className="flex-1 min-h-0">
              <BodyEditorWithPreview
                value={body}
                onChange={setBody}
                disabled={saving}
                mode={mode}
                placeholder="SKILL.md frontmatter 之后的正文"
                fillHeight
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <DialogClose render={<Button variant="outline" disabled={saving}><X className="h-3.5 w-3.5" />取消</Button>} />
          <Button onClick={handleCreate} disabled={!canSave}>
            {saving ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
