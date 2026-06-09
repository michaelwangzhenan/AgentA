/** 单项 API key 的脱敏视图（后端永不返回明文）。 */
export type ApiKeyView = {
  id: string
  label: string
  env: string
  configured: boolean
  masked: string
  source: 'env' | 'override'
}

export type ApiKeysResponse = {
  items: ApiKeyView[]
}
