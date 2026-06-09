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
  section?: string | null // 组内子分区标题；缺省 = 不分区
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
  hidden?: boolean // true 时设置面板不渲染（聊天页 Composer 仍经此接口读写）
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
  tier?: string // 能力/价位档位：min / low / medium / high / max（空 = 不显示徽章）
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
