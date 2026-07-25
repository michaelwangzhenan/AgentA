import { Fragment } from 'react'
import { Link } from 'react-router-dom'

import { toast } from '@/lib/toast'

const ICP_NUMBER = '浙ICP备2026055936号-1'
const ICP_URL = 'https://beian.miit.gov.cn/'
const PSB_NUMBER = '浙公网安备33010502013300号'
const PSB_URL =
  'https://beian.mps.gov.cn/#/query/webSearch?code=33010502013300'

const PRODUCT_LINKS = [
  { label: '关于 AgentA', href: null },
  { label: '使用须知', href: null },
  { label: '帮助中心', href: null },
  { label: '隐私政策', href: null },
  { label: '联系我们', href: '/contact' },
] as const

const linkClass =
  'text-white/85 transition-colors hover:text-white hover:underline'

function showComingSoon() {
  toast.info('页面建设中')
}

export function SiteFooter() {
  return (
    <footer className="fixed inset-x-0 bottom-0 z-30 border-t border-transparent bg-black/50 px-3 py-1.5 text-center text-xs backdrop-blur-sm dark:border-white/10 dark:bg-black/70">
      <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
        {PRODUCT_LINKS.map((item, index) => (
          <Fragment key={item.label}>
            {index > 0 ? (
              <span className="text-white/40" aria-hidden>
                ·
              </span>
            ) : null}
            {item.href ? (
              <Link to={item.href} className={linkClass}>
                {item.label}
              </Link>
            ) : (
              <button type="button" onClick={showComingSoon} className={linkClass}>
                {item.label}
              </button>
            )}
          </Fragment>
        ))}

        <span className="text-white/40" aria-hidden>
          ·
        </span>
        <a
          href={ICP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={linkClass}
        >
          {ICP_NUMBER}
        </a>

        <span className="text-white/40" aria-hidden>
          ·
        </span>
        <a
          href={PSB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`inline-flex items-center gap-1 ${linkClass}`}
        >
          <img
            src="/beian-icon.png"
            alt="公安备案图标"
            width={14}
            height={15}
            className="inline-block"
          />
          {PSB_NUMBER}
        </a>
      </div>
    </footer>
  )
}
