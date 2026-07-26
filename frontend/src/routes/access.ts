import type { ViewKind } from '@/components/sidebar/Sidebar'
import {
  isSettingsSection,
  viewKindFromPathname,
  type SettingsSection,
} from '@/routes/paths'

const ACCOUNT_SETTINGS_SECTIONS: SettingsSection[] = ['password', 'account']

/** 仅主账号可访问的路由（用户管理） */
export function routeRequiresSuperAdmin(pathname: string): boolean {
  const view = viewKindFromPathname(pathname)
  const parts = pathname.split('/').filter(Boolean)
  const sub = parts[1]

  if (view === 'settings' && sub && isSettingsSection(sub)) {
    return sub === 'users'
  }

  return false
}

/** readonly 不可访问账号写操作相关设置页 */
export function routeRequiresAccountSettings(pathname: string): boolean {
  const view = viewKindFromPathname(pathname)
  const parts = pathname.split('/').filter(Boolean)
  const sub = parts[1]

  if (view === 'settings' && sub && isSettingsSection(sub)) {
    return ACCOUNT_SETTINGS_SECTIONS.includes(sub)
  }

  return false
}

/** 保留兼容：原 admin 页现三档均可读，不再拦路由 */
export function routeRequiresAdmin(_pathname: string): boolean {
  return false
}

export function routeRequiresReadonlyBlock(_view: ViewKind): boolean {
  return false
}
