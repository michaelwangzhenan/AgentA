# AgentA 控件对照 · 表 B 定位与属性表

> 按 **编号** 查控件在哪、怎么找到、能改什么。叫法对照见 [表 A 命名对齐表](./UI_name_mapping_A.md)（同编号、同序）。
> **AgentA 实例**写成"从哪进 → 点哪 → 看到的就是它"的定位路径。

## 布局容器

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 001 | 登录后窗口最左侧贯穿全高的竖直栏（顶部"新建会话"、中部视图入口、下部会话列表） | `sidebar/Sidebar.tsx` | 窗口最左、固定宽度的竖直栏 | 宽度、背景色、边框、是否可折叠 |
| 002 | 切到"记忆/Rules/Skills/MCP/用量/质量"任一页，整页外壳（顶栏 + 内容区）就是它 | `resources/ResourcePage.tsx` | 顶部 h1 标题 + 右侧 toolbar 槽 + 滚动内容区 | 标题、副标题、工具条、内容区最大宽度 |
| 003 | 各页最上方那条横向标题区（如聊天页"AgentA"、设置页"设置"） | 各 View 内联 `header` | 底部分隔线 + 粗体标题 + 灰色副标题 | 标题文案、副标题、内边距、边框 |
| 004 | 学而时习"学习计划/测验"页内左列表、右详情的两栏区域 | `business/PlansView.tsx`、`QuizzesView.tsx` | 左窄列表 + 右宽详情的网格 | 左栏宽度、卡片边框/背景 |
| 005 | 学而时习页右侧固定宽的聊天面板（可收起/拖宽） | `business/MasteryView.tsx`（`aside`） | 右侧竖条聊天区，顶有会话下拉 | 默认开闭、宽度上下限、紧凑模式 |
| 006 | 设置/维护等页里把若干内容框在一起的圆角边框块 | `settings/SettingsSection.tsx`、`admin/DBShowView.tsx`（`Card`） | 圆角边框卡片，可带小标题；危险区红框 | 标题、描述、danger 红框、内容 |
| 007 | 暂无页面实例（组件已封装，业务未接入） | `ui/scroll-area.tsx` | 视口 + 细窄圆角自定义滚动条 | 方向、className |
| 008 | 聊天回复里可展开/收起的块（工具块、思考块都基于它） | `ui/collapsible.tsx` | 触发条 + 内容区，200ms 高度过渡 | 默认展开、open 受控、过渡 |

## 导航

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 009 | 侧边栏中部"聊天 / 知识库 / 记忆…"列表里的任意一行 | `Sidebar.tsx` 内 `ViewNavButton` | 带图标的单行可点项，选中态高亮 | 图标、文案、选中态颜色、间距 |
| 010 | 质量看板/用量/数据库/设置 页内左侧那一竖列标签 | `QualityView.tsx`、`UsageView.tsx`、`DBShowView.tsx`、`SettingsPage.tsx` | 页内左侧 `w-28~32` 竖向按钮列表 | 标签项、权限过滤、默认选中、选中态 |
| 011 | 学而时习内容区顶部"学习计划 / 测验 / 复习"三个 tab | `business/MasteryView.tsx`（`TABS`） | 横排带图标 tab，底边高亮选中 | tab 列表、默认选中、图标 |
| 012 | 编辑/新建 Skill 时正文区右上"Edit / Split / Preview"三钮 | `resources/SkillsView.tsx`（`ViewModeTabs`） | 内嵌边框分段，`aria-pressed` 当前项 | 模式列表、默认 mode |
| 013 | 知识库进入某库后顶部"‹ 库列表 / {库名}"；数据库进集合后同理 | `kb/KnowledgeBaseView.tsx`、`admin/DBShowView.tsx` | Chevron 分隔，可点段 hover 高亮 | 文案、层级、分隔符、回退 |
| 014 | 会话监控 / 用量仪表盘顶部"今日/近7天/…"或"我的/全员" | `eval/TraceDashboard.tsx`、`usage/UsageDashboard.tsx` | 圆角边框内多枚互斥小按钮，选中白底 | 选项列表、默认选中、尺寸/颜色 |
| 015 | 文档列表 / 数据库 / 用量明细表底部的翻页区 | `kb/DocumentList.tsx`、`admin/DBShowView.tsx`、`usage/UsageDashboard.tsx` | 上一页/下一页 + 页码（部分带跳转、每页条数） | 每页条数选项、默认页大小、跳转校验 |
| 016 | 文档列表表头"文件名/语言/chunks…"可点，活跃列带升降箭头 | `kb/DocumentList.tsx` | `aria-sort` + 列内按钮 + 半透明箭头 | 默认列/方向、列标签、对齐 |
| 017 | Skills/MCP 行 Switch 右侧的小三角，点开展详情 | `resources/SkillsView.tsx`、`MCPView.tsx` | ChevronRight/Down，切换展开 | 折叠时是否退出编辑态 |
| 018 | 聊天向上翻历史时，消息流底部中央浮出的"↓ 回到最新" | `chat/MessageList.tsx` | 底部居中悬浮、带下箭头小钮 | 触发距离阈值、文案、位置、阴影 |
| 019 | 悬停某条助手回复 → 操作栏右侧 `‹ 2/3 ›`（该回答有多版本时） | `chat/MessageBubble.tsx` | 左右 Chevron + 当前/总数 | 边界禁用、数字格式 |
| 020 | 学而时习 AI 侧栏顶栏左侧"当前会话标题 + 下箭头" | `business/MasteryView.tsx`（`DropdownMenu`） | 标题截断 + ChevronDown，展开为会话列表 | 下拉宽度、最大高度、空列表文案 |
| 021 | 侧边栏最底部、用户名右侧的深浅色切换小钮 | `settings/ThemeToggle.tsx` | 切换深/浅色的小控件 | 主题选项、图标 |

## 输入

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 022 | 几乎每页都有：登录页"登录"钮、设置"保存"、知识库"刷新"等 | `ui/button.tsx` | 圆角可点控件，实心/描边/幽灵/危险等变体 | variant、size、disabled、图标+文案 |
| 023 | 登录页"用户名"标签下、占位符"请输入用户名"的单行框 | `ui/input.tsx` | 单行可输入文本的方框 | 占位符、最大长度、禁用态、聚焦边框色 |
| 024 | 记忆"添加记忆"弹窗里、复习卡背面等处的多行框 | `ui/textarea.tsx` | 圆角多行输入，可随内容增高 | 行数、最大高度、占位符、快捷键 |
| 025 | 设置→系统配置任一布尔项右侧，或 Skills 每行左侧的拨动开关 | `ui/switch.tsx` | 胶囊轨道 + 圆滑块，开启主色右移 | checked、disabled、尺寸 |
| 026 | 文档列表每行左侧 / 表头全选的方框；备份类别勾选 | `kb/DocumentList.tsx`、`admin/BackupView.tsx`（原生） | `accent-primary` 原生 checkbox | 选中范围、选中态行背景 |
| 027 | 系统配置中枚举项≤4 时的横排圆点选项 | `settings/ConfigField.tsx`（`RadioGroup`） | 原生 radio，选项 mono 字 | 选项、当前值 |
| 028 | 入库"目标库"、配置枚举>4、Golden"来源"等处的原生下拉 | `kb/IngestPanel.tsx`、`settings/ConfigField.tsx`、`eval/GoldenManager.tsx` | 原生 `select` | 选项、默认值、禁用态 |
| 029 | Skills/MCP 区标题右侧、系统配置顶部带放大镜的搜索栏 | `resources/SkillsView.tsx`、`settings/SettingsView.tsx` | Input + 左侧 Search 图标 + 有值时 X 清除 | 占位符、宽度、过滤字段 |
| 030 | 系统配置数字项、单价配置"输入/输出价格"、维护"保留天数" | `settings/ConfigField.tsx`、`usage/PricingConfig.tsx`、`admin/DBShowView.tsx` | `type=number`，窄宽，带 min/max/step | 范围、步进、debounce 保存 |
| 031 | 知识库 → 入库面板内"拖文件到这里 或 点击选择"的虚线大框 | `kb/DropZone.tsx` | `border-dashed` + Upload 图标 + 格式/大小提示 | 接受扩展名、禁用态文案、拖拽高亮 |
| 032 | 点拖放区/"选择文件夹"/备份"还原"时弹出的浏览器原生文件框 | `kb/DropZone.tsx`、`IngestPanel.tsx`、`admin/BackupView.tsx` | `type=file` + `hidden`（文件夹版带 webkitdirectory） | multiple、accept、目录模式 |
| 033 | Skills（管理员）→ 某 skill 点铅笔编辑 → 正文 CodeMirror 输入区 | `ui/markdown-editor.tsx` | CodeMirror，MD 高亮，跟随明暗主题 | value、disabled、占位符、最小高度、撑满 |
| 034 | 登录后聊天页最底部、占满宽度、右侧带"发送"的圆角输入卡片 | `chat/Composer.tsx` | 底部 `max-w-3xl` 圆角边框卡片（含工具条） | 外边距、最大宽度、拖拽高亮、背景 |
| 035 | 聊天底部输入框里输入 `/` → 输入框上方弹出 Skills 列表 | `chat/Composer.tsx` | 输入框正上方浮层，mono `/skill名` + 描述 | 匹配数量上限、列表宽度、高亮项 |
| 036 | 聊天输入框工具条"Auto"或当前模型名 → 展开厂商→模型 | `chat/Composer.tsx` | 文字触发器，两级下拉 + tier 彩色徽章 | 默认模型、tier 徽章色、菜单宽度 |
| 037 | 聊天输入框工具条"深度思考：关/低/中/高"（脑图标） | `chat/Composer.tsx` | Brain 图标 + 档位文字；不支持时灰显 | 档位选项、禁用提示、图标 |
| 038 | 聊天输入框工具条"深度研究"（显微镜图标），开启紫底高亮 | `chat/Composer.tsx` | Microscope 图标 + 文案，按下态紫色 | 显隐、按下态颜色、tooltip |
| 039 | 聊天输入框右下圆形麦克风（浏览器支持时），录音中红底 | `chat/Composer.tsx` | 圆形 Mic 图标，listening 态红色 | 显隐、录音态颜色、结果拼接 |
| 040 | 聊天输入框最右圆形钮：空闲为上箭头发送，生成中为方块停止 | `chat/Composer.tsx` | 右端 `rounded-full` 图标按钮 | 图标、禁用态、加载中转圈、尺寸 |

## 反馈

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 041 | 侧边栏会话条悬停 → 点"⋯" → 重命名，弹出的居中浮层 | `ui/dialog.tsx` | 遮罩 + 居中内容（标题/内容/Footer） | 最大宽度、标题、内容、遮罩透明度 |
| 042 | 删会话/删文档/注销账号等点删除时弹的"取消/确认"框 | `ui/alert-dialog.tsx` | 标题 + 描述 + 取消 + 红色确认 | 标题/描述、确认按钮危险样式 |
| 043 | 会话行末"⋯"、聊天输入框"+"等点开的浮动选项列表 | `ui/dropdown-menu.tsx` | 点击后展开的浮动菜单 | 对齐、方向、菜单项、宽度 |
| 044 | 任意操作成功后（如保存配置），屏幕右下角短暂浮现的提示条 | `App.tsx` 内 `Toaster`（sonner） | 右下角短暂出现后消失的小条 | 位置、主题色、停留时长、富色 |
| 045 | 记忆/Rules/数据库等页 API 失败时，内容区顶部红底边框提示 | 多处（如 `MemoryView.tsx`、`DBShowView.tsx`） | `border-red`/`destructive` 浅红底单行 | 文案、颜色、是否可关闭 |
| 046 | 列表无数据时居中的虚线框灰字（如"还没有学习计划"） | 多处（`PlansView.tsx` 等） | 虚线边框居中灰字 + 可选 CTA | 文案、是否带新建按钮 |
| 047 | 各页数据加载时的"加载中…"（部分带转圈 Loader2） | 多处（`KnowledgeBaseView.tsx`、`DBShowView.tsx` 等） | 居中灰字，常带旋转图标 | 文案、是否骨架屏 |
| 048 | 知识库入库进行中"入库中 N/M"区里的横向填充条 | `kb/IngestPanel.tsx` | `bg-muted` 槽 + `bg-primary` 填充 | 高度、颜色、是否显示百分比 |
| 049 | 系统配置项标题旁的 Info 小图标，悬停弹出详细说明 | `settings/ConfigField.tsx`（`DetailHint`） | Info 图标 + hover/focus 弹 tooltip | 说明、可选值、默认值展示 |
| 050 | 聊天助手逐字输出时，正文末尾闪烁的细竖线 | `chat/MessageBubble.tsx`（`StreamingCursor`） | 正文末尾 2px 脉冲竖条 | 宽度、颜色、动画 |
| 051 | 备份与恢复页顶部琥珀色"含明文密钥"警示块 | `admin/BackupView.tsx` | 琥珀边框背景 + AlertTriangle 图标 | 文案、样式 |

## 数据展示

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 052 | 会话监控/用量仪表盘/降本面板顶部那排"对话数/延迟/总Token…"小卡 | `eval/TraceDashboard.tsx`、`usage/UsageDashboard.tsx`、`SavingsPanel.tsx` | 小卡：上标签下大数字，可带 hint | label、value、hint、网格列数 |
| 053 | 用量仪表盘"趋势 · 总 Token（按模型）"区块内的柱状图 | `usage/TrendChart.tsx` | 纯 SVG 堆叠柱状图 + 彩色图例 | metric、币种、数据、空态 |
| 054 | 文档列表/Golden/用量明细/数据库等处的表格 | 多处（`DocumentList.tsx`、`GoldenManager.tsx` 等） | `<table>`，表头 + 行 + 可选 hover/选中 | 列集、空态、行样式、分页 |
| 055 | "默认入库""已配置""connected"等圆角小标签 | 多处（`KnowledgeBaseView.tsx`、`MCPView.tsx` 等） | 圆角小胶囊，按状态着色 | 文案、颜色、显示条件 |
| 056 | Rules 页右下角"1234 / 4000"（超限变红） | `resources/RulesView.tsx` | 超限 `text-red-600` | 上限值、超限样式 |
| 057 | 聊天输入框右下、发送钮左侧"~N tokens"（超限变红） | `chat/Composer.tsx` | 11px 等宽数字，超软上限变红 | 软上限阈值、估算算法、颜色 |
| 058 | 聊天助手回答中的标题/列表/表格/链接等排版内容 | `chat/Markdown.tsx` | GFM 排版：蓝链、圆角表格、行内代码 chip | 标题样式、链接色、列表间距、表格边框 |
| 059 | 聊天助手回答中深色凹陷块，顶栏带语言名 +"复制" | `chat/CodeBlock.tsx` | 顶栏语言标签 + 复制 + 等宽 pre | 语言标签、复制反馈、背景、字号 |
| 060 | Skills 行展开后的正文渲染、离线评估"查看报告"的正文 | `ui/markdown-preview.tsx` | react-markdown 只读渲染，空时占位 | source、容器样式 |
| 061 | 侧边栏中下部"Recents"标题下方、可滚动的会话条列表 | `Sidebar.tsx` 内 `recents-list` | 侧边栏可滚动的会话条列表 | 是否折叠、条目间距、选中态 |
| 062 | 数据库 Chroma/BM25 chunk 详情页的"metadata"区 | `admin/DBShowView.tsx`（`MetadataBlock`） | pre 块 JSON 格式化，空时占位 | 缩进、空态 |
| 063 | 聊天新建会话且未发消息时，主区中央 Logo + 时段问候 | `chat/EmptyState.tsx` | 居中 Logo + 大号问候 + 快捷芯片 | 问候逻辑、Logo 尺寸、芯片文案 |

## 页面级视图

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 064 | 侧边栏中部点"聊天"→ 右侧整页（顶栏 + 消息流/欢迎 + 底部输入） | `chat/ChatView.tsx` | 占满右侧的纵向三区布局 | 顶栏显隐、紧凑模式、间距/最大宽度 |
| 065 | 侧边栏点"知识库"→ 标题"知识库"+ 库列表/单库文档 | `kb/KnowledgeBaseView.tsx` | h1"知识库"，L1 库列表 ↔ L2 文档管理 | 标题文案、内容区最大宽度、L1/L2 切换 |
| 066 | 侧边栏点"记忆"→ 标题"用户记忆"+ 记忆条目列表 | `resources/MemoryView.tsx` | ResourcePage"用户记忆"+ 列表卡片 | 副标题、列表布局 |
| 067 | 侧边栏点"Rules"→ 大段 monospace 文本框 + 字数/保存 | `resources/RulesView.tsx` | 大 `min-h-[400px]` 可拉伸文本区 | 副标题、最大字数 |
| 068 | 管理员 → 侧边栏点"Skills"→ 已启用/已禁用分区 | `resources/SkillsView.tsx` | 启用/禁用分区 + 可选失败区 | 分区标题、空态、失败区 |
| 069 | 管理员 → 侧边栏点"MCP"→ 已启用/已禁用/失败三段 | `resources/MCPView.tsx` | server 分组列表 | 分组逻辑、副标题 |
| 070 | 侧边栏点"学而时习"→ 左业务区（三 tab）+ 可选右 AI 侧栏 | `business/MasteryView.tsx` | 标题"学而时习"+ 副标题"定计划·做测验·间隔复习" | 默认 tab、引导展开、AI 侧栏 |
| 071 | 侧边栏点"用量"→ 左子标签 + 右仪表盘/降本/单价 | `usage/UsageView.tsx` | 标题"用量"+ 左竖向子标签 | 子标签（含管理员项）、默认选中 |
| 072 | 侧边栏点"质量看板"→ 左子导航（会话监控/安全/离线评估/Golden） | `eval/QualityView.tsx` | 标题"质量看板"+ 左竖向子导航 | tab 与权限、跨页筛选、内容宽度 |
| 073 | 管理员 → 侧边栏点"数据库"→ 左 Chroma/BM25/SQLite/维护 | `admin/DBShowView.tsx` | 标题"数据库"+ 四标签 | tabs、默认 tab、最大宽度 |
| 074 | 管理员 → 侧边栏点"备份与恢复"→ 生成/列表/还原三块 | `admin/BackupView.tsx` | 标题 + 警示条 + 备份表 + 还原区 | 备份类别、最大宽度 |
| 075 | 侧边栏底部用户名菜单 →"设置"→ 左分组导航 + 右内容 | `settings/SettingsPage.tsx` | 顶栏"设置"+ 左分组导航（账户/系统/危险区域） | 导航分组、默认 section、权限 |
| 076 | 未登录打开应用 → 居中卡片（Logo + 用户名/密码表单） | `auth/LoginView.tsx` | 全屏居中卡片，登录/注册切换 | 模式切换、字段、错误提示、按钮文案 |

## 业务块

| 编号 | AgentA 实例（怎么找到它） | 代码位置 | 识别特征 | 可修改属性 |
|---|---|---|---|---|
| 077 | 聊天消息流中右对齐、带用户主题色背景的圆角文本块 | `chat/MessageBubble.tsx`（`UserBubble`） | 右对齐 `bg-user-bubble` 圆角块 | 最大宽度、背景/文字色、圆角、操作栏 |
| 078 | 聊天消息流中左对齐的助手回复（含研究/待办/思考/工具/正文） | `chat/MessageBubble.tsx`（`AssistantBubble`） | 左对齐、纵向堆叠多种块 + 灰色正文气泡 | 最大宽度、子块间距、紧凑模式 |
| 079 | 聊天发过至少一条消息后、输入框上方可滚动的消息区 | `chat/MessageList.tsx` | 纵向滚动、居中 `max-w-4xl` 序列 | 内边距、消息间距、自动滚底、最大宽度 |
| 080 | 聊天助手回复中"检索知识库/联网搜索…"可展开条（带状态图标） | `chat/ToolBlock.tsx` | 可折叠条，工具图标 + 标题 + 状态图标 | 标题映射、状态图标、参数/结果区 |
| 081 | 聊天助手回复中标题"待办 N/M"的可折叠步骤清单 | `chat/PlanBlock.tsx` | 灰底块"待办"+ 完成数，步骤带状态 | 默认展开、步骤状态样式、备注 |
| 082 | 聊天助手回复中"思考了 N 秒/正在思考…"可展开条 | `chat/ThinkingBlock.tsx` | 折叠条，Brain 图标，展开为虚线框文本 | 默认折叠、摘要文案、耗时显示 |
| 083 | 开"深度研究"后发消息 → 助手回复顶部紫色显微镜标题 + 子问题 | `chat/ResearchPanel.tsx` | 圆角块"深度研究·{阶段}"+ 子代理/动作行 | 阶段文案、图标色、子行状态、反思区 |
| 084 | 聊天助手回答正文下方"参考资料(N):"可展开编号列表 | `chat/SourcesPanel.tsx` | 书本图标折叠条，展开为 [1][2]… 列表 | 默认折叠、标题格式、列表样式 |
| 085 | 当前界面找不到（组件已实现，未接入消息流） | `chat/WorkBlock.tsx` | 设计为"工作过程"外包，内嵌 Plan + 多个 Tool | 自动折叠、摘要、默认展开 |
| 086 | 知识库 L1 列表下方"入库"卡片（拖放区 + 选文件夹 + 待入库列表） | `kb/IngestPanel.tsx` | `rounded-lg border bg-card`，含 DropZone | 目标库、格式提示、进度展示 |
| 087 | 知识库进某库后"已入库文档"卡片内（过滤 + 表格 + 分页） | `kb/DocumentList.tsx` | 过滤行 + 批量栏 + 可排序表 + 分页 | 默认排序、页大小、列定义 |
| 088 | 学而时习 → tab"学习计划"（左计划列表 + 右详情/任务） | `business/PlansView.tsx` | 说明 + 新建/刷新 + 主从双栏 | 列表默认选中、空态文案 |
| 089 | 学而时习 → tab"测验"（左测验列表 + 右题目/作答/批改） | `business/QuizzesView.tsx` | 说明 + 主从双栏，含选择题/简答/批改块 | 空态文案、默认选中 |
| 090 | 学而时习 → tab"复习"（待复习区 + 全部卡片列表） | `business/SRSView.tsx` | 琥珀色"待复习"区 + 卡片列表 | 说明文案、刷新 |
| 091 | 复习 tab → 琥珀框"待复习(N)"内（翻面 + 四档评分） | `business/SRSView.tsx`（`Reviewer`） | "第 x/N 张"→ 显示答案 → 重来/困难/良好/容易 | 评分按钮文案、进度提示 |
| 092 | 质量看板 → 默认"会话监控"（指标卡 + 延迟条 + 对话明细表） | `eval/TraceDashboard.tsx` | 时间分段 + 指标卡 + 每日延迟 + 明细表 | 默认时间范围、分页大小 |
| 093 | 质量看板（管理员）→"实时安全监控"（拦截统计 + 最近拦截表） | `eval/SecurityPanel.tsx`（`RuntimeMonitor`） | 标题 + 近 30 天拦截统计 + 拦截表 | 统计项、刷新 |
| 094 | 质量看板（管理员）→"离线评估"（左任务导航 + 右运行器） | `eval/OfflineEvalView.tsx` | 左 `w-28` 任务列表 + 右 EvalRunner | 任务列表与说明配置 |
| 095 | 离线评估选任一任务 → 右侧说明卡 + 运行控制 + 摘要 + 历史报告 | `eval/EvalRunner.tsx` | 模型/选项/阈值控件 + 开始评估 + 日志/摘要 | 控件布局、运行禁用、互斥提示 |
| 096 | 质量看板（管理员）→"Golden 管理"（筛选 + 批量 + 大表） | `eval/GoldenManager.tsx` | 状态/来源筛选 + 导出导入新增 + 数据表 | 页大小、筛选常量、状态徽章 |
| 097 | 当前界面找不到（组件存在，无路由引用） | `eval/ReportsViewer.tsx` | 左分类折叠报告列表 + 右 MarkdownPreview | 分类标签/顺序、分组解析 |
| 098 | 用量页 →"我的用量/全员用量"右侧主内容 | `usage/UsageDashboard.tsx` | 工具条 + 概览卡 + 趋势图 + 明细表 | scope、时间范围、指标、分组 |
| 099 | 用量页 →"降本/全员降本"（6 格统计 + 每日节省明细） | `usage/SavingsPanel.tsx` | 时间分段 + 6 格统计 + 明细表 | scope、时间范围 |
| 100 | 用量页（管理员）→"单价配置"（按厂商分组的单价表 + 保存） | `usage/PricingConfig.tsx` | 分组模型单价表 + 顶部保存 | 分组、币种、保存逻辑 |
| 101 | 设置（管理员）→"系统配置"（搜索 + 左配置组 + 右配置项） | `settings/SettingsView.tsx` | 搜索 + 配置组导航 + ConfigField 列表 | 嵌入模式、配置组、搜索、自动保存 |
| 102 | 系统配置右侧任意一条（标题 + key + 控件） | `settings/ConfigField.tsx` | 圆角卡片行，`data-config-key`，保存中左边框高亮 | 控件类型、disabled、危险、副作用提示 |
| 103 | 系统配置 → 模型路由组底部"候选池"卡片（勾模型 + 保存候选池） | `settings/RoutingPoolConfig.tsx` | 按厂商分组模型 checkbox + 保存 | 选中集合、tier 标签、保存 |
| 104 | 设置（管理员）→"API 密钥"（每厂商一行：状态 + 脱敏 + 输入 + 保存） | `settings/ApiKeysConfig.tsx` | 每厂商卡片，状态标签 + 密码输入 | items、脱敏展示、busy |
| 105 | 设置（管理员）→"用户管理"（用户表 + 行末删除） | `settings/UserManagement.tsx` | 用户表格 + 垃圾桶删除 | 列表刷新、自删禁用 |
| 106 | 设置 →"个人信息"（用户名输入 + 保存；头像/语言占位） | `settings/ProfileSettings.tsx` | 用户名字段 + 灰色"即将支持"占位 | 用户名字段、占位显隐 |
| 107 | 设置 →"密码与安全"（三密码框 + 更新密码） | `settings/PasswordSettings.tsx` | 三个密码框 + 更新按钮 | 校验规则、按钮禁用 |
| 108 | 设置 → 危险区域"注销账号"（红框 + 两步确认） | `settings/AccountDeletion.tsx` | 红色 Section + destructive 按钮 + 两步确认 | 说明文案、两步流程 |
| 109 | 数据库 →"维护"（保留期清理/按用户清理/孤儿段/VACUUM 四块） | `admin/DBShowView.tsx`（`MaintenancePanel`） | 四个 Card 纵向排列 + 预览/执行 | 子面板组合 |
