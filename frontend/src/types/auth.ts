export type UserRole = 'readonly' | 'user' | 'admin'

export type PermissionScope =
  | 'chat'
  | 'kb'
  | 'memory'
  | 'usage'
  | 'quality'
  | 'skills'
  | 'db'
  | 'backup'
  | 'profile'
  | 'account'
  | 'config'
  | 'users'

export type UserInfo = {
  id: number
  username: string
  role: UserRole
  created_at?: string
  can_manage_users?: boolean
  capabilities?: PermissionScope[]
}

export type AuthResponse = {
  user: UserInfo
}

export type LlmPrefs = {
  active_model: string
  thinking_enabled: boolean
  thinking_budget: number
}

export type LlmPrefsUpdate = {
  active_model?: string
  thinking_enabled?: boolean
  thinking_budget?: number
}
