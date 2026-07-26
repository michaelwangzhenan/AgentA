export type StaticPageSlug = 'about' | 'terms' | 'help' | 'privacy'

export type StaticPageConfig = {
  slug: StaticPageSlug
  path: `/${string}`
  title: string
  showLogo?: boolean
}

export const STATIC_PAGE_ENTRIES: StaticPageConfig[] = [
  { slug: 'about', path: '/about', title: '关于 AgentA', showLogo: true },
  { slug: 'terms', path: '/terms', title: '使用须知', showLogo: true },
  { slug: 'help', path: '/help', title: '帮助中心', showLogo: true },
  { slug: 'privacy', path: '/privacy', title: '隐私政策', showLogo: true },
]

export const CONTACT_PAGE = {
  path: '/contact',
  title: '联系我们',
  showLogo: true,
} as const

export const PUBLIC_PATHS = [CONTACT_PAGE.path, ...STATIC_PAGE_ENTRIES.map((p) => p.path)] as const

export function isPublicPath(pathname: string): boolean {
  return (PUBLIC_PATHS as readonly string[]).includes(pathname)
}

const markdownCache = new Map<StaticPageSlug, string>()

export async function loadStaticPageMarkdown(slug: StaticPageSlug): Promise<string> {
  const hit = markdownCache.get(slug)
  if (hit !== undefined) return hit

  const res = await fetch(`/pages/${slug}.md`, { cache: 'no-cache' })
  if (!res.ok) {
    throw new Error(`无法加载页面内容（${res.status}）`)
  }
  const text = await res.text()
  markdownCache.set(slug, text)
  return text
}
