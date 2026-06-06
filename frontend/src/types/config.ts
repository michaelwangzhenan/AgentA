// 跟后端 src/api/schemas/config.py + src/api/config_meta.py 对齐

export type ConfigItemType =
  | 'bool'
  | 'int'
  | 'float'
  | 'string'
  | 'path'
  | 'enum_str'
  | 'multi_enum_str'

export type ConfigSource = 'default' | 'override'

export type ConfigItemView = {
  key: string
  group: string
  type: ConfigItemType
  value: unknown
  default: unknown
  source: ConfigSource
  brief: string
  detail: string
  options?: string[] | null
  min?: number | null
  max?: number | null
  side_effect_hint?: string | null
  danger?: boolean
  editable: boolean
}

export type ConfigGroupView = {
  name: string
  label: string
  items: ConfigItemView[]
}

export type ConfigResponse = {
  groups: ConfigGroupView[]
}

export type ConfigItemResponse = {
  item: ConfigItemView
}

export type ConfigReloadResponse = {
  changed_keys: string[]
  config: ConfigResponse
}

// 两档模型目录（厂商 → 模型），对齐后端 GET /api/config/models
export type ModelOption = {
  id: string
  label: string
  thinking: boolean
}

export type ProviderModels = {
  name: string
  label: string
  models: ModelOption[]
}

export type ModelsResponse = {
  active: string
  providers: ProviderModels[]
}
