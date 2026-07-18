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
