import type { ViewKind } from '@/components/sidebar/Sidebar'
import {
  isQualityTab,
  isSettingsSection,
  isUsageTab,
  viewKindFromPathname,
  type QualityTab,
  type SettingsSection,
  type UsageTab,
} from '@/routes/paths'

const ADMIN_VIEWS: ViewKind[] = ['skills', 'mcp', 'database', 'backup']

const ADMIN_QUALITY_TABS: QualityTab[] = ['security_runtime', 'offline', 'golden']
const ADMIN_USAGE_TABS: UsageTab[] = ['all', 'savings_all', 'pricing']
const ADMIN_SETTINGS_SECTIONS: SettingsSection[] = ['system', 'apikeys', 'users']

export function routeRequiresAdmin(pathname: string): boolean {
  const view = viewKindFromPathname(pathname)
  if (ADMIN_VIEWS.includes(view)) return true

  const parts = pathname.split('/').filter(Boolean)
  const sub = parts[1]

  if (view === 'quality' && sub && isQualityTab(sub)) {
    return ADMIN_QUALITY_TABS.includes(sub)
  }
  if (view === 'usage' && sub && isUsageTab(sub)) {
    return ADMIN_USAGE_TABS.includes(sub)
  }
  if (view === 'settings' && sub && isSettingsSection(sub)) {
    return ADMIN_SETTINGS_SECTIONS.includes(sub)
  }

  return false
}
