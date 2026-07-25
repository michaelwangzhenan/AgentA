import type { SiteConfig } from '@/types/site'

const DEFAULT_CONFIG: SiteConfig = {
  notice: '',
  demoAccount: {
    username: '',
    password: '',
    note: '',
  },
  contact: {
    phone: '',
    email: '',
    wechat: {
      label: '微信',
      hint: '扫码或搜索微信号添加',
      id: '',
      qrImage: '',
    },
    linkedin: '',
    github: '',
  },
}

let cached: SiteConfig | null = null

export async function loadSiteConfig(): Promise<SiteConfig> {
  if (cached) return cached
  try {
    const res = await fetch('/site.json', { cache: 'no-cache' })
    if (!res.ok) {
      cached = DEFAULT_CONFIG
      return cached
    }
    const data = (await res.json()) as Partial<SiteConfig>
    cached = {
      notice: data.notice?.trim() || DEFAULT_CONFIG.notice,
      demoAccount: {
        username: data.demoAccount?.username?.trim() ?? '',
        password: data.demoAccount?.password ?? '',
        note:
          data.demoAccount?.note?.trim() || DEFAULT_CONFIG.demoAccount.note,
      },
      contact: {
        phone: data.contact?.phone?.trim() ?? '',
        email: data.contact?.email?.trim() ?? '',
        wechat: {
          label: data.contact?.wechat?.label?.trim() || DEFAULT_CONFIG.contact.wechat.label,
          hint: data.contact?.wechat?.hint?.trim() || DEFAULT_CONFIG.contact.wechat.hint,
          id: data.contact?.wechat?.id?.trim() ?? '',
          qrImage: data.contact?.wechat?.qrImage?.trim() ?? '',
        },
        linkedin: data.contact?.linkedin?.trim() ?? '',
        github: data.contact?.github?.trim() ?? '',
      },
    }
    return cached
  } catch {
    cached = DEFAULT_CONFIG
    return cached
  }
}
