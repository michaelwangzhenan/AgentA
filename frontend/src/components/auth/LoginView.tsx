import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PublicHero } from '@/components/public/PublicHero'
import { PublicPageShell } from '@/components/public/PublicPageShell'
import { useAuth } from '@/lib/auth'
import { loadSiteConfig } from '@/lib/siteConfig'

export function LoginView() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [demoSubmitting, setDemoSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [demoAccount, setDemoAccount] = useState<{ username: string; password: string } | null>(
    null,
  )

  useEffect(() => {
    void loadSiteConfig().then((cfg) => {
      if (cfg.demoAccount?.username && cfg.demoAccount?.password) {
        setDemoAccount({
          username: cfg.demoAccount.username,
          password: cfg.demoAccount.password,
        })
      }
    })
  }, [])

  const busy = submitting || demoSubmitting
  const canSubmit = username.trim().length > 0 && password.length > 0 && !busy

  const handleLogin = async (u: string, p: string) => {
    setSubmitting(true)
    setError(null)
    try {
      await login(u, p)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    await handleLogin(username.trim(), password)
  }

  const handleDemoLogin = async () => {
    if (!demoAccount || busy) return
    setDemoSubmitting(true)
    setError(null)
    try {
      await login(demoAccount.username, demoAccount.password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setDemoSubmitting(false)
    }
  }

  return (
    <PublicPageShell aside={<PublicHero />}>
      <div className="w-full space-y-6">
        {demoAccount ? (
          <section className="space-y-3">
            <div className="space-y-1">
              <h2 className="text-xl font-semibold tracking-tight">访客浏览</h2>
            </div>
            <Button
              type="button"
              className="w-full"
              disabled={busy}
              onClick={() => void handleDemoLogin()}
            >
              {demoSubmitting ? '进入中…' : '立即体验'}
            </Button>
          </section>
        ) : null}
        <p className="text-center text-sm leading-relaxed text-muted-foreground">
          本站不开放注册，请{' '}
          <Link
            to="/contact"
            className="text-foreground underline underline-offset-2 hover:text-primary"
          >
            联系我们
          </Link>
          {' '}获取更多权限。
        </p>

        {demoAccount ? (
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
          </div>
        ) : null}

        <section className="space-y-4">
          <div className="space-y-1">
            <h2 className="text-xl font-semibold tracking-tight">登录</h2>
            <p className="text-sm text-muted-foreground">使用用户名与密码登录</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
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
                  autoFocus={!demoAccount}
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

            {error ? (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
                {error}
              </div>
            ) : null}

            <Button type="submit" variant="outline" className="w-full" disabled={!canSubmit}>
              {submitting ? '处理中…' : '登录'}
            </Button>
          </form>
        </section>

      </div>
    </PublicPageShell>
  )
}
