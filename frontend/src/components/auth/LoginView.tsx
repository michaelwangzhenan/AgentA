import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/lib/auth'
import logoUrl from '@/assets/agentA_logo.svg'

type Mode = 'login' | 'register'

export function LoginView() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit =
    username.trim().length > 0 &&
    password.length > 0 &&
    (mode === 'login' || confirmPassword.length > 0) &&
    !submitting

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    if (mode === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      if (mode === 'login') {
        await login(username.trim(), password)
      } else {
        await register(username.trim(), password)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const switchMode = () => {
    setMode((m) => (m === 'login' ? 'register' : 'login'))
    setPassword('')
    setConfirmPassword('')
    setError(null)
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-card p-8 shadow-sm"
      >
        <div className="space-y-1 text-center">
          <div className="flex items-center justify-center">
            <img src={logoUrl} alt="AgentA logo" className="h-20 w-20" />
            <h1 className="text-3xl font-semibold">AgentA</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            {mode === 'login' ? '登录你的账号' : '注册新账号'}
          </p>
        </div>

        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="username">
              用户名
            </label>
            <Input
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              maxLength={64}
              placeholder="请输入用户名"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="password">
              密码
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              maxLength={128}
              placeholder="请输入密码"
            />
          </div>
          {mode === 'register' && (
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="confirm-password">
                确认密码
              </label>
              <Input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                maxLength={128}
                placeholder="请再次输入密码"
              />
            </div>
          )}
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
            {error}
          </div>
        )}

        <Button type="submit" className="w-full" disabled={!canSubmit}>
          {submitting ? '处理中…' : mode === 'login' ? '登录' : '注册'}
        </Button>

        <p className="text-center text-sm text-muted-foreground">
          {mode === 'login' ? '还没有账号？' : '已有账号？'}
          <button
            type="button"
            onClick={switchMode}
            className="ml-1 font-medium text-primary hover:underline"
          >
            {mode === 'login' ? '去注册' : '去登录'}
          </button>
        </p>
      </form>
    </div>
  )
}
