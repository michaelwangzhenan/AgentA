# AgentA 控件对照 · 表 A 命名对齐表

> 统一 UI 控件的中英文 / 书面 / 口语叫法，方便交流（尤其跟 AI 对话时）快速对齐"说的是哪块"。
> 配套 [表 B 定位与属性表](./UI_name_mapping_B.md)：同一 **编号** 指同一控件，两表同序，对照看用编号或同一行位置。

## 怎么读这张表

- **行单位是"控件类型"**：一个组件一行。`Dialog` 这类可复用控件，UI 里有多个实例（重命名弹窗、删除确认…）共用一行。
- **编号**：3 位数字，跨两表唯一；按分类分段编排。
- **可见性**：`页面可见` = 界面上能直接看到、能指着说"这个"；`仅代码` = 只是代码里的抽象/骨架，界面上认不出独立的它。
- 想知道某控件在哪、可改什么 → 拿编号去[表 B](./UI_name_mapping_B.md)。

## 布局容器

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 001 | 侧边栏 | Sidebar | 左侧栏、左边那条 | 页面可见 |
| 002 | 资源页骨架 | ResourcePage | 资源页、页面外壳 | 仅代码 |
| 003 | 页面标题栏 | Page Header | 顶栏、标题区 | 页面可见 |
| 004 | 主从双栏布局 | Master-Detail Layout | 左列表右详情 | 页面可见 |
| 005 | AI 助手侧栏 | Chat Aside | 右边聊天、侧栏聊天 | 页面可见 |
| 006 | 卡片 / 分区 | Card / Section | 框在一起的那块 | 页面可见 |
| 007 | 滚动区 | ScrollArea | 自定义滚动条容器 | 仅代码 |
| 008 | 可折叠 | Collapsible | 折叠面板、展开收起 | 页面可见 |

## 导航

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 009 | 视图导航按钮 | Nav Item / ViewNavButton | 菜单项、左边的入口、那个 tab | 页面可见 |
| 010 | 子标签导航 | Side Tab Nav | 页内左侧那几个、二级导航 | 页面可见 |
| 011 | 业务 Tab 栏 | Tab Bar | 顶部那几个 tab | 页面可见 |
| 012 | 视图模式切换 | View Mode Tabs | Edit/Split/Preview 三个钮 | 页面可见 |
| 013 | 面包屑 | Breadcrumb | 上面那条路径、返回上一层 | 页面可见 |
| 014 | 分段选择器 | Segmented Control | 胶囊切换、今日/近7天那种 | 页面可见 |
| 015 | 分页器 | Pager / Pagination | 上一页下一页、每页条数 | 页面可见 |
| 016 | 可排序列头 | Sortable Header | 点列名排序、升降序箭头 | 页面可见 |
| 017 | 展开 / 折叠钮 | Expand Toggle | 小三角、Chevron | 页面可见 |
| 018 | 回到底部按钮 | Scroll-to-Bottom | 回到最新、跳到底部 | 页面可见 |
| 019 | 回答版本切换器 | Version Switcher | 多版本切换、1/3 那个 | 页面可见 |
| 020 | 会话下拉 | Session Dropdown | 换会话、会话名下拉 | 页面可见 |
| 021 | 主题切换 | Theme Toggle | 换肤、深色开关 | 页面可见 |

## 输入

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 022 | 按钮 | Button | 钮、主按钮、图标钮 | 页面可见 |
| 023 | 输入框 | Input | 单行文本框、输入栏 | 页面可见 |
| 024 | 多行输入框 | Textarea | 大输入框、多行输入 | 页面可见 |
| 025 | 开关 | Switch | 拨动开关、启停钮、toggle | 页面可见 |
| 026 | 复选框 | Checkbox | 勾选框、多选 | 页面可见 |
| 027 | 单选组 | Radio Group | 几个圆点选项 | 页面可见 |
| 028 | 下拉框 | Native Select | 原生下拉 | 页面可见 |
| 029 | 搜索框 | SearchBox | 放大镜输入、搜那个 | 页面可见 |
| 030 | 数字输入框 | Number Input | 数字框 | 页面可见 |
| 031 | 拖放上传区 | DropZone | 拖拽区、虚线框 | 页面可见 |
| 032 | 文件选择器 | File Input | 选文件、上传弹框 | 仅代码 |
| 033 | Markdown 编辑器 | MarkdownEditor | MD 编辑框、CodeMirror | 页面可见 |
| 034 | 消息输入区 | Composer | 输入区、聊天框、底部那个框 | 页面可见 |
| 035 | 斜杠命令菜单 | Slash Command Menu | 斜杠菜单、/skill 补全 | 页面可见 |
| 036 | 模型选择器 | Model Selector | 选模型、Auto/厂商菜单 | 页面可见 |
| 037 | 推理强度选择器 | Thinking Level Selector | 深度思考档位、Brain 下拉 | 页面可见 |
| 038 | 深度研究开关 | Deep Research Toggle | 显微镜按钮、深度研究模式 | 页面可见 |
| 039 | 语音听写按钮 | Speech Input Button | 麦克风、语音输入 | 页面可见 |
| 040 | 发送 / 停止按钮 | Send / Stop Button | 发送钮、停止生成、箭头/方块 | 页面可见 |

## 反馈

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 041 | 对话框 | Dialog / Modal | 弹窗、模态框、弹出来那个 | 页面可见 |
| 042 | 确认对话框 | Alert Dialog | 确认框、删除确认、二次确认 | 页面可见 |
| 043 | 下拉菜单 | Dropdown Menu | 下拉、三个点菜单 | 页面可见 |
| 044 | 轻提示 | Toast | 提示条、右下角提示、操作成功条 | 页面可见 |
| 045 | 错误提示条 | Error Banner | 红框报错、加载失败 | 页面可见 |
| 046 | 空状态占位 | Empty State | 还没数据那块、虚线框 | 页面可见 |
| 047 | 加载占位 | Loading / Spinner | 加载中…、转圈 | 页面可见 |
| 048 | 进度条 | Progress Bar | 那条主色的条 | 页面可见 |
| 049 | 详情提示 | Info Tooltip | 旁边小 i、悬浮说明 | 页面可见 |
| 050 | 流式光标 | Streaming Cursor | 打字光标、闪烁竖线 | 页面可见 |
| 051 | 安全警示条 | Warning Banner | 琥珀色警告、风险提示 | 页面可见 |

## 数据展示

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 052 | 统计卡 | Stat Card | 上面那几个数字框 | 页面可见 |
| 053 | 趋势图 | Trend Chart | 柱状图、趋势那块 | 页面可见 |
| 054 | 数据表格 | Data Table | 表格、那张大表 | 页面可见 |
| 055 | 徽标 / 标签 | Badge / Pill | 小标签、小圆标 | 页面可见 |
| 056 | 字数计数 | Character Counter | 字数、x/上限 | 页面可见 |
| 057 | Token 估算 | Token Estimate | token 数、~N tokens | 页面可见 |
| 058 | Markdown 渲染器 | Markdown | MD 渲染、富文本回答 | 页面可见 |
| 059 | 代码块 | Code Block | 带复制按钮的代码区 | 页面可见 |
| 060 | Markdown 预览 | MarkdownPreview | 只读渲染、正文预览 | 页面可见 |
| 061 | 会话列表 | Session List / Recents | 历史会话、最近会话 | 页面可见 |
| 062 | 元数据块 | Metadata Block | metadata JSON | 页面可见 |
| 063 | 空会话欢迎区 | Empty State (Chat) | 欢迎屏、打招呼那块 | 页面可见 |

## 页面级视图

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 064 | 聊天页 | Chat View | 聊天页、主聊天区、右边那块 | 页面可见 |
| 065 | 知识库页 | Knowledge Base View | 知识库页、KB 页 | 页面可见 |
| 066 | 用户记忆页 | Memory View | 记忆页、用户记忆 | 页面可见 |
| 067 | Rules 页 | Rules View | 个人规则页 | 页面可见 |
| 068 | Skills 页 | Skills View | 技能管理、SKILL.md 页 | 页面可见 |
| 069 | MCP 页 | MCP View | MCP 管理、server 列表页 | 页面可见 |
| 070 | 学而时习页 | Mastery View | 学而时习、学习页 | 页面可见 |
| 071 | 用量看板页 | Usage View | 用量页、用量看板 | 页面可见 |
| 072 | 质量看板页 | Quality View | 质量页、评估看板 | 页面可见 |
| 073 | 数据库页 | DB Show View | 数据库、dbshow | 页面可见 |
| 074 | 备份与恢复页 | Backup View | 备份页 | 页面可见 |
| 075 | 设置页 | Settings Page | 设置、个人设置 | 页面可见 |
| 076 | 登录页 | Login View | 登录界面、注册页 | 页面可见 |

## 业务块

| 编号 | 中文名 | 英文名 | 别名 / 口语 | 可见性 |
|---|---|---|---|---|
| 077 | 用户消息气泡 | User Bubble | 用户气泡、右边那条、我发的消息 | 页面可见 |
| 078 | 助手消息区 | Assistant Bubble | 助手回复区、左边那坨、Agent 回答 | 页面可见 |
| 079 | 消息列表区 | Message List | 消息流、聊天记录区 | 页面可见 |
| 080 | 工具调用块 | Tool Block | 工具块、工具卡、联网搜索那条 | 页面可见 |
| 081 | 待办计划块 | Plan Block | Plan、待办列表、步骤清单 | 页面可见 |
| 082 | 思考过程块 | Thinking Block | 思考折叠框、推理过程 | 页面可见 |
| 083 | 深度研究面板 | Research Panel | 研究进度、子问题列表 | 页面可见 |
| 084 | 参考资料面板 | Sources Panel | 引用来源、参考资料 | 页面可见 |
| 085 | 工作过程块 | Work Block | 工作过程折叠 | 仅代码 |
| 086 | 入库面板 | Ingest Panel | 入库区、上传那块 | 页面可见 |
| 087 | 文档列表 | Document List | 文档表格、文档管理 | 页面可见 |
| 088 | 学习计划视图 | Plans View | 学习计划 tab、计划页 | 页面可见 |
| 089 | 测验视图 | Quizzes View | 测验 tab、出题页 | 页面可见 |
| 090 | 复习视图 | SRS View | 复习 tab、间隔重复页 | 页面可见 |
| 091 | SRS 复习器 | SRS Reviewer | 翻面打分、待复习卡 | 页面可见 |
| 092 | 会话监控看板 | Trace Dashboard | trace 页、会话监控 | 页面可见 |
| 093 | 实时安全监控 | Runtime Monitor | 实时安全、线上拦截 | 页面可见 |
| 094 | 离线评估页 | Offline Eval View | 离线评估、跑评估那页 | 页面可见 |
| 095 | 评估运行器 | Eval Runner | 选模型开始评估那块 | 页面可见 |
| 096 | Golden 管理 | Golden Manager | golden 管理、标准集维护 | 页面可见 |
| 097 | 报告浏览器 | Reports Viewer | 独立报告页 | 仅代码 |
| 098 | 用量仪表盘 | Usage Dashboard | 用量图表区 | 页面可见 |
| 099 | 降本面板 | Savings Panel | 降本页、省钱那块 | 页面可见 |
| 100 | 单价配置 | Pricing Config | 单价页、改模型价格 | 页面可见 |
| 101 | 系统配置面板 | Settings View | 系统配置、改 .env 那种设置 | 页面可见 |
| 102 | 配置项行 | Config Field | 一条配置、那个开关行 | 页面可见 |
| 103 | 路由候选池 | Routing Pool Config | 候选池、勾模型 | 页面可见 |
| 104 | API 密钥面板 | API Keys Config | 配 key、厂商密钥 | 页面可见 |
| 105 | 用户管理 | User Management | 删用户、用户列表 | 页面可见 |
| 106 | 个人信息面板 | Profile Settings | 改用户名 | 页面可见 |
| 107 | 密码与安全面板 | Password Settings | 改密码 | 页面可见 |
| 108 | 注销账号面板 | Account Deletion | 删自己账号 | 页面可见 |
| 109 | 维护面板 | Maintenance Panel | 维护 tab、清理数据 | 页面可见 |
