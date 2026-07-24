import type { ViewKind } from '@/components/sidebar/Sidebar'

export const MASTERY_TABS = ['plans', 'quizzes', 'srs'] as const
export type MasteryTab = (typeof MASTERY_TABS)[number]

export const USAGE_TABS = ['mine', 'savings', 'all', 'savings_all', 'pricing'] as const
export type UsageTab = (typeof USAGE_TABS)[number]

export const QUALITY_TABS = ['trace', 'security_runtime', 'offline', 'golden'] as const
export type QualityTab = (typeof QUALITY_TABS)[number]

export const DATABASE_TABS = ['chroma', 'bm25', 'sqlite', 'maintenance'] as const
export type DatabaseTab = (typeof DATABASE_TABS)[number]

export const SETTINGS_SECTIONS = [
  'profile',
  'password',
  'system',
  'apikeys',
  'users',
  'account',
] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]

export function isMasteryTab(v: string): v is MasteryTab {
  return (MASTERY_TABS as readonly string[]).includes(v)
}

export function isUsageTab(v: string): v is UsageTab {
  return (USAGE_TABS as readonly string[]).includes(v)
}

export function isQualityTab(v: string): v is QualityTab {
  return (QUALITY_TABS as readonly string[]).includes(v)
}

export function isDatabaseTab(v: string): v is DatabaseTab {
  return (DATABASE_TABS as readonly string[]).includes(v)
}

export function isSettingsSection(v: string): v is SettingsSection {
  return (SETTINGS_SECTIONS as readonly string[]).includes(v)
}

export function viewKindFromPathname(pathname: string): ViewKind {
  const seg = pathname.split('/').filter(Boolean)[0] ?? 'chat'
  if (seg === 'chat') return 'chat'
  if (seg === 'kb') return 'kb'
  if (seg === 'memory') return 'memory'
  if (seg === 'rules') return 'rules'
  if (seg === 'skills') return 'skills'
  if (seg === 'mcp') return 'mcp'
  if (seg === 'mastery') return 'mastery'
  if (seg === 'usage') return 'usage'
  if (seg === 'quality') return 'quality'
  if (seg === 'database') return 'database'
  if (seg === 'backup') return 'backup'
  if (seg === 'settings') return 'settings'
  return 'chat'
}

export function pathForView(view: ViewKind, sessionId?: string | null): string {
  switch (view) {
    case 'chat':
      return sessionId ? `/chat/${sessionId}` : '/chat'
    case 'kb':
      return '/kb'
    case 'memory':
      return '/memory'
    case 'rules':
      return '/rules'
    case 'skills':
      return '/skills'
    case 'mcp':
      return '/mcp'
    case 'mastery':
      return sessionId
        ? `/mastery/plans?session=${encodeURIComponent(sessionId)}`
        : '/mastery/plans'
    case 'usage':
      return '/usage/mine'
    case 'quality':
      return '/quality/trace'
    case 'database':
      return '/database/chroma'
    case 'backup':
      return '/backup'
    case 'settings':
      return '/settings/profile'
    default:
      return '/chat'
  }
}

export function chatPath(sessionId: string): string {
  return `/chat/${sessionId}`
}

export function kbPath(alias?: string): string {
  return alias ? `/kb/${encodeURIComponent(alias)}` : '/kb'
}

export function masteryPath(tab: MasteryTab, sessionId?: string | null): string {
  const base = `/mastery/${tab}`
  if (!sessionId) return base
  return `${base}?session=${encodeURIComponent(sessionId)}`
}

export function qualityGoldenPath(params: {
  docId?: string
  fromAlias?: string
  docLabel?: string
}): string {
  const sp = new URLSearchParams()
  if (params.docId) sp.set('docId', params.docId)
  if (params.fromAlias) sp.set('fromAlias', params.fromAlias)
  if (params.docLabel) sp.set('docLabel', params.docLabel)
  const q = sp.toString()
  return q ? `/quality/golden?${q}` : '/quality/golden'
}

export function databasePath(
  tab: DatabaseTab,
  seg1?: string,
  seg2?: string,
  search?: URLSearchParams | string,
): string {
  let path = `/database/${tab}`
  if (seg1) path += `/${encodeURIComponent(seg1)}`
  if (seg2) path += `/${encodeURIComponent(seg2)}`
  if (!search) return path
  const q = typeof search === 'string' ? search : search.toString()
  return q ? `${path}?${q}` : path
}

/** 合并查询参数补丁并保留当前其它键（用于切库/表时保留筛选）。 */
export function mergeSearchParams(
  current: URLSearchParams,
  updates: Record<string, string | number | null | undefined>,
): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === '') next.delete(key)
    else next.set(key, String(value))
  }
  return next
}

/** epoch 秒 → YYYY-MM-DD（本地时区），供日期筛选进网址。 */
export function epochToDateInput(epoch?: number): string {
  if (epoch == null || !Number.isFinite(epoch) || epoch <= 0) return ''
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return ''
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
