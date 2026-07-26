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
import type { PermissionScope, UserInfo } from '@/types/auth'

type AuthState = {
  user: UserInfo | null
  loading: boolean
  isAdmin: boolean
  isReadonly: boolean
  isSuperAdmin: boolean
  canRead: (scope: PermissionScope) => boolean
  canWrite: (scope: PermissionScope) => boolean
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

  const refreshUser = useCallback(async () => {
    setUser(await getMe())
  }, [])

  const caps = useMemo(() => new Set(user?.capabilities ?? []), [user?.capabilities])

  const canWrite = useCallback(
    (scope: PermissionScope) => caps.has(scope),
    [caps],
  )

  const canRead = useCallback(
    (scope: PermissionScope) => {
      if (scope === 'users') return user?.can_manage_users === true
      if (scope === 'account') return user?.role !== 'readonly'
      return true
    },
    [user],
  )

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      isAdmin: user?.role === 'admin',
      isReadonly: user?.role === 'readonly',
      isSuperAdmin: user?.can_manage_users === true,
      canRead,
      canWrite,
      login,
      logout,
      refreshUser,
    }),
    [user, loading, canRead, canWrite, login, logout, refreshUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}
