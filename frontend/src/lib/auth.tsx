import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  getMe,
  login as apiLogin,
  logout as apiLogout,
  setUnauthorizedHandler,
} from '@/api/client'
import type { UserInfo } from '@/types/auth'

type AuthState = {
  user: UserInfo | null
  loading: boolean
  isAdmin: boolean
  isSuperAdmin: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const me = await getMe()
        if (!cancelled) setUser(me)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // 任意请求拿到 401（登录态过期）→ 清空用户，界面回落到登录页
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    setUser(await apiLogin(username, password))
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiLogout()
    } finally {
      setUser(null)
    }
  }, [])

  // 个人信息改动后（如改用户名）重新拉一次 /me，刷新全局用户态
  const refreshUser = useCallback(async () => {
    setUser(await getMe())
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      isAdmin: user?.role === 'admin',
      isSuperAdmin: user?.can_manage_users === true,
      login,
      logout,
      refreshUser,
    }),
    [user, loading, login, logout, refreshUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}
