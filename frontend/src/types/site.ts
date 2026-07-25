export type SiteWechatConfig = {
  label: string
  hint: string
  id: string
  qrImage: string
}

export type SiteDemoAccount = {
  username: string
  password: string
  note: string
}

export type SiteContactConfig = {
  phone: string
  email: string
  wechat: SiteWechatConfig
  linkedin: string
  github: string
}

export type SiteConfig = {
  notice: string
  demoAccount: SiteDemoAccount
  contact: SiteContactConfig
}
