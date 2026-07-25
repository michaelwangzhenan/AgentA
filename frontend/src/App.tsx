import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { LoginView } from '@/components/auth/LoginView'
import { SiteFooter } from '@/components/layout/SiteFooter'
import { AppRoutes } from '@/routes/AppRoutes'
import { useAuth } from '@/lib/auth'

function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen flex-col bg-background">
      <div className="min-h-0 flex-1 overflow-hidden pb-8">{children}</div>
      <SiteFooter />
    </div>
  )
}

function App() {
  const { user, loading: authLoading } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const pendingRedirectRef = useRef<string | null>(null)
  const prevUserRef = useRef(user)
  const authInitializedRef = useRef(false)

  // 未登录时记录深链目标（App 层 ref，避免 LoginView 卸载丢失）
  if (!authLoading && !user) {
    const target = location.pathname + location.search
    if (target && target !== '/') {
      pendingRedirectRef.current = target
    }
  }

  // 登录成功后回跳（跳过首屏 auth 恢复，避免刷新深链被拉到 /chat）
  useEffect(() => {
    if (authLoading) return

    if (!authInitializedRef.current) {
      authInitializedRef.current = true
      prevUserRef.current = user
      return
    }

    if (!prevUserRef.current && user) {
      const to = pendingRedirectRef.current || '/chat'
      pendingRedirectRef.current = null
      navigate(to, { replace: true })
    }
    prevUserRef.current = user
  }, [user, authLoading, navigate])

  if (authLoading) {
    return (
      <AppShell>
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          加载中…
        </div>
      </AppShell>
    )
  }

  if (!user) {
    return (
      <AppShell>
        <LoginView />
      </AppShell>
    )
  }

  return (
    <AppShell>
      <AppRoutes />
    </AppShell>
  )
}

export default App
