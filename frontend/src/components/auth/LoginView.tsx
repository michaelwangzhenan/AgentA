import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PublicHero } from '@/components/public/PublicHero'
import { PublicPageShell } from '@/components/public/PublicPageShell'
import { useAuth } from '@/lib/auth'

export function LoginView() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit =
    username.trim().length > 0 && password.length > 0 && !submitting

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <PublicPageShell aside={<PublicHero />}>
      <form onSubmit={handleSubmit} className="w-full space-y-5">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">登录</h2>
          <p className="text-sm text-muted-foreground">使用已有账号登录 AgentA</p>
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
              autoComplete="current-password"
              maxLength={128}
              placeholder="请输入密码"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
            {error}
          </div>
        )}

        <Button type="submit" className="w-full" disabled={!canSubmit}>
          {submitting ? '处理中…' : '登录'}
        </Button>

        <p className="text-center text-sm leading-relaxed text-muted-foreground">
          本站不开放注册。请{' '}
          <Link
            to="/contact"
            className="text-foreground underline underline-offset-2 hover:text-primary"
          >
          联系我们
          </Link>
          {' '}获取体验账号。
        </p>
      </form>
    </PublicPageShell>
  )
}
