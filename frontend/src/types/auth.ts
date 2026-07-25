export type UserRole = 'user' | 'admin'

export type UserInfo = {
  id: number
  username: string
  role: UserRole
  created_at?: string
  can_manage_users?: boolean
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
