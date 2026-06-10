// 模型路由候选池类型（iter_14）。对齐 src/api/routes/routing.py。

export type RoutingModel = {
  model_id: string
  label: string
  provider: string
  provider_label: string
  tier: string
  available: boolean // provider 是否已配 api_key
  selected: boolean // 是否在候选池
}

export type RoutingPoolResponse = {
  enabled: boolean
  mode: string
  configured: boolean
  models: RoutingModel[]
}
