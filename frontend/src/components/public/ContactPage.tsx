import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, Link2, Mail, MessageCircle, Phone, UserRound } from 'lucide-react'

import { buttonVariants } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { loadSiteConfig } from '@/lib/siteConfig'
import { cn } from '@/lib/utils'
import type { SiteConfig } from '@/types/site'

type ContactRow = {
  key: string
  icon: typeof Phone
  label: string
  value: string
  href?: string
  external?: boolean
}

function buildRows(config: SiteConfig): ContactRow[] {
  const { contact } = config
  const rows: ContactRow[] = []

  if (contact.phone) {
    rows.push({
      key: 'phone',
      icon: Phone,
      label: '电话',
      value: contact.phone,
      href: `tel:${contact.phone.replace(/\s/g, '')}`,
    })
  }
  if (contact.email) {
    rows.push({
      key: 'email',
      icon: Mail,
      label: '邮件',
      value: contact.email,
      href: `mailto:${contact.email}`,
    })
  }
  if (contact.wechat.id || contact.wechat.hint) {
    rows.push({
      key: 'wechat',
      icon: MessageCircle,
      label: contact.wechat.label || '微信',
      value: contact.wechat.id || contact.wechat.hint,
    })
  }
  if (contact.linkedin) {
    rows.push({
      key: 'linkedin',
      icon: Link2,
      label: 'LinkedIn',
      value: contact.linkedin.replace(/^https?:\/\/(www\.)?linkedin\.com\/in\//i, ''),
      href: contact.linkedin,
      external: true,
    })
  }
  if (contact.github) {
    rows.push({
      key: 'github',
      icon: ExternalLink,
      label: 'GitHub',
      value: contact.github.replace(/^https?:\/\/(www\.)?github\.com\//i, ''),
      href: contact.github,
      external: true,
    })
  }

  return rows
}

export function ContactPage() {
  const { user } = useAuth()
  const [config, setConfig] = useState<SiteConfig | null>(null)

  useEffect(() => {
    void loadSiteConfig().then(setConfig)
  }, [])

  const rows = config ? buildRows(config) : []
  const qrImage = config?.contact.wechat.qrImage
  const demo = config?.demoAccount
  const hasDemoAccount = Boolean(demo?.username)

  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-y-auto px-4 py-8 pb-12">
      <div className="w-full max-w-md space-y-6 rounded-2xl border border-border bg-card p-8 shadow-sm">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">联系我们</h2>
          </div>

          {!config ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : rows.length === 0 && !hasDemoAccount ? (
            <p className="text-sm text-muted-foreground">
              联系方式尚未配置，请编辑 frontend/public/site.json。
            </p>
          ) : rows.length > 0 ? (
            <ul className="space-y-3">
              {rows.map((row) => {
                const Icon = row.icon
                const inner = (
                  <>
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs text-muted-foreground">{row.label}</span>
                      <span className="block truncate text-sm font-medium">{row.value}</span>
                    </span>
                  </>
                )
                return (
                  <li key={row.key}>
                    {row.href ? (
                      <a
                        href={row.href}
                        target={row.external ? '_blank' : undefined}
                        rel={row.external ? 'noopener noreferrer' : undefined}
                        className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:bg-muted/50"
                      >
                        {inner}
                      </a>
                    ) : (
                      <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
                        {inner}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          ) : null}

          {qrImage ? (
            <div className="rounded-xl border border-border bg-card p-4 text-center">
              <p className="mb-3 text-xs text-muted-foreground">
                {config?.contact.wechat.hint || '微信二维码'}
              </p>
              <img
                src={qrImage}
                alt="微信二维码"
                className="mx-auto h-36 w-36 rounded-lg border border-border object-contain"
              />
            </div>
          ) : null}

          {config?.notice ? (
            <p className="text-xs leading-relaxed text-muted-foreground">{config.notice}</p>
          ) : null}

          {hasDemoAccount && demo ? (
            <div className="rounded-xl border border-border bg-muted/30 px-4 py-3">
              <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                <UserRound className="h-4 w-4 text-muted-foreground" />
                体验账号
              </div>
              <dl className="space-y-1.5 text-sm">
                <div className="flex gap-2">
                  <dt className="w-14 shrink-0 text-muted-foreground">用户名</dt>
                  <dd className="font-medium">{demo.username}</dd>
                </div>
                {demo.password ? (
                  <div className="flex gap-2">
                    <dt className="w-14 shrink-0 text-muted-foreground">密码</dt>
                    <dd className="font-medium">{demo.password}</dd>
                  </div>
                ) : null}
              </dl>
              {demo.note ? (
                <p className="mt-2 text-xs text-muted-foreground">{demo.note}</p>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2 pt-2">
            {user ? (
              <Link to="/chat" className={cn(buttonVariants({ variant: 'default' }))}>
                进入应用
              </Link>
            ) : (
              <Link to="/" className={cn(buttonVariants({ variant: 'default' }))}>
                返回登录
              </Link>
            )}
          </div>
      </div>
    </div>
  )
}
