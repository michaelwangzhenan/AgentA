# 1. 背景

RAG 和 Agent 部分都进行了优化和升级，现在开始 UI 部分。

# 2. 现状与思考

当前 Web UI 是当时的一个尝试，并非真实需求，也没有仔细设计。是对当时CLI 功能的界面化，完全不符合 UI 本身的特性，要从头全新设计。

当前项目实现了 web UI draft，选择的是用 chainlit 来实现。
思考：当前项目做 desk app UI 还是 Web UI？→ 选 Web UI
技术选型：Vite + React vs Next.js -> 在学习完基础知识后决定

学习方式：实践 > 理论
1. 理解前/后端基本概念和框架
2. 需求定义：先知道要什么，然后再决定用什么做、如何做。
3. 技术选型：了解技术栈，知道什么工具是干什么的
4. 在 AI 辅助下完成代码

# 3. chainlit 现状清单

| 文件 | 怎么改 |
|---|---|
| `chainlit_app.py` | 删 |
| `.chainlit/` 目录 | 删 |
| `chainlit.md` | 删 |
| `public/custom.css` | 删 |
| `public/custom.js` | 删 |
| `tools/ui_debug.ps1` | 删（新 UI 启动脚本另写） |
| `requirements.txt` | 删 `chainlit` 那行 |
| `.gitignore` | 删 `.chainlit/translations/` 段 |
| `README.md` | 删 3 处 chainlit 启动 / 介绍引用 |
| `docs/design.md` | 删 3 处架构图里 `chainlit_app._event_router` 引用 |
| `src/agent/*.py` | 删注释里的 "Chainlit"  |
| `src/llm/provider.py` | 删注释里的 "Chainlit"  |
| `src/cli/skill_loader.py`| 删注释里的 "Chainlit"  |
| `tests/conftest.py` | 删注释里的 "Chainlit"  |


# 4. 需求定义

## 4.1. 功能

chat 主区交互：
- 流式输出（thinking + token）
- 停止生成
- 消息编辑 + 重发
- 回答 regenerate（同 / 换 provider）
- 代码块 syntax highlight + copy

Agent 状态可视化：
- 📋 Plan checkbox + 单步状态
- 💭 Thinking 折叠
- 🛠 Tool call 折叠 + 详情
- 📊 Token 消耗（每轮 / 累计）
- 引用 [n] hover 预览 + jump to source

资源管理：
- 会话：新建 / 改名 / 切换 / 删除 / 清空 / 导出 md / 全文搜索
- 用户记忆：列表 / 增删改
- prompt -> `.agenta/rules.md`（查看 / 编辑）
- skills：列表 / 启停 / reload
- mcp server：列表 / 健康状态 / config 查看
- 知识库：文档列表 / 上传入库 / 入库进度 / 清库

业务面板（学习/研究助理，可扩展）：
- 学习计划
- Quiz：出题 / MCQ 答题 / 批改
- SRS：日历 + 卡片复习 + 4 档评分

系统配置：
- LLM provider（含 Thinking 开关 / budget / adaptive）
- RAG top_k 等检索参数
- .env 中挑一些主要的
- 主题（亮 / 暗）
- 语言

反馈机制：
- toast（角落小条幅，自动消失）
- loading（异步进行中提示）
- error inline（错误就近显示，不弹窗）

调试：
- 复用/优化 CLI 的调试功能 /logs 下的日志


## 4.2. UX 风格

参考 Claude 的 Web 版：

- 配色：暖白主背景 + 深灰文本 + 暖橙 accent；暗色模式翻成深棕灰 + 暖白
- 字体：英文 Inter / system-ui；中文 PingFang SC / Microsoft YaHei
- 圆角：气泡 / 卡片 ~12px；按钮 / 输入框 ~6px
- 间距：留白宽松；消息间距 ≥ 16px
- 过渡动画：温和（150–200ms ease），不夸张
- 具体 hex / 字号 / 字重实施时定

布局（参照 Claude Web）：
- 左侧栏：顶部 `[+ 新对话]` → 资源菜单（知识库 / Skills / MCP / Rules / 记忆）→ Recents 列表 → 底部用户区（账号 / 设置入口 / 主题切换 / 折叠）
- 主区顶部细条：当前 chat 标题 + model / provider 切换
- 主区 chat：消息流；sources / tools / token 作为**内嵌折叠按钮**显示在回答下方，点击才弹出
- 主区底部输入框区：输入框 + 附件 + skill 触发 + Thinking 开关
- 右侧 detail panel：**默认折叠**，点 sources / tools / token 按钮时滑出；可常驻 pin

页面布局图：

```
┌──────────────────────┬──────────────────────────────────────────────┐
│ ⚡ AgentA      ◀     │   💬 当前 chat 标题            [Qwen ▾]      │
│ ──────────────────── │ ──────────────────────────────────────────── │
│ [+ 新对话]            │                                              │
│                      │  user: …                                     │
│ 📚 知识库             │                                              │
│ 🔧 Skills            │  ai:  📋 Plan                                │
│ 🔌 MCP               │      ✅ Step 1                               │
│ 📝 Rules             │      ⏳ Step 2                               │
│ 🧠 用户记忆           │      💭 Thinking ▾                          │
│                      │      正文（流式打字中…）                       │
│ ──────────────────── │                                              │
│ 🔍 Recents           │   [📚 sources(2)] [🛠 tools(3)] [📊 token]   │
│ 今日                  │                                              │
│ · …                  │  Tabs: [Chat][计划][Quiz][SRS]               │
│ · …                  │                                              │
│ 昨日                  │  ┌───────────────────────────────────────┐  │
│ · …                  │  │ + 📎 [输入框…]                    [↑] │  │
│                      │  │   Thinking ◯                          │  │
│ ──────────────────── │  └───────────────────────────────────────┘  │
│ 👤 Michael · ⚙ · 🌙  │                                              │
└──────────────────────┴──────────────────────────────────────────────┘
```

注：sources / tools / token 内嵌按钮点击 → 右侧滑出 detail panel（默认折叠）；
左侧栏可整体折叠收成图标列。



# 5. 技术选型

## 5.1. 基础知识

### 5.1.1. 前端与后端
现在要做的 UI 就是前端，后端是 agent 和 RAG 部分。
Agent Core 提供的 agent API + agent Event 可以给 CLI 和 UI 共用。

JS/TS 常用于前端开发
python/java/go/rust 常用于后端开发

### 5.1.2. 流式输出
流式输出是指在网页上实时显示文本，而不是一次性加载所有内容。
LLM 回答是一个字一个字往外吐的。前端如果没法处理"一段一段进来的数据"，就只能等全部生成完再一次性显示，体验差很多。

后端推流给前端的两种主流方式：
- SSE（Server-Sent Events）：单向，后端 → 前端推消息，HTTP 长连接。对 chat 场景最合适，简单可靠。
- WebSocket：双向通讯，前后端都能主动发消息。比 SSE 复杂，只在双向流式场景必要（如语音对讲 / 多人协作）。

### 5.1.3. 前后端怎么对话

前端要读 / 改后端数据，靠 **HTTP 请求**。
- 请求 = method（GET / POST / PATCH / DELETE）+ URL + body
- 返回 = status code（200 OK / 4xx 客户端错 / 5xx 服务端错）+ body
- body 通常是 **JSON**（key-value 字典，前后端通用）

**REST**：一种约定 —— URL 表达"资源"，method 表达"操作"。例：
- `GET /api/sessions` 取会话列表
- `POST /api/sessions` 新建会话
- `PATCH /api/sessions/abc123` 改某个会话（如重命名标题）
- `DELETE /api/sessions/abc123` 删某个会话

URL 两种粒度：
- **集合** `/sessions` —— 所有会话
- **单体** `/sessions/{id}` —— 某一个

method 大致对应 **CRUD**（Create / Read / Update / Delete）：GET = Read，POST = Create，PATCH = Update，DELETE = Delete。

业界实际很少严格"纯 REST"，常被叫 "REST-like" 或 "JSON-over-HTTP"。

### 5.1.4. 网页是怎么渲染的

一张网页 = HTML（骨架）+ CSS（样式）+ JS（交互）：

- **HTML**：页面结构，嵌套标签组成（如 `<div>`、`<button>`、`<p>`）
- **CSS**（Cascading Style Sheets, 层叠样式表）：视觉样式 —— 颜色、字体、间距、布局。用法分两端：
  - HTML 端给标签贴名字：`<button class="btn">点我</button>`。`class` 是 HTML 标签的一个属性，相当于"分类标签"，名字自取，多个元素可共用同一个
  - CSS 端按名字选中、上样式：`.btn { color: white; background: blue; padding: 8px }`，开头的点号 `.` 表示"按 class 选"，意思是"凡 class 含 `btn` 的元素，涂成蓝底白字、内边距 8px"
- **JS**：跑在浏览器里的脚本，负责交互（点击、输入、网络请求……）

浏览器加载页面时，会把 HTML 解析成 **DOM**（Document Object Model, 文档对象模型）—— 内存中的一棵标签树，每个 HTML 标签是一个节点。
CSS 和 JS 都基于这棵树工作：
- CSS 按选择器匹配 DOM 节点、给它们上色 / 排版；
- JS 通过 DOM 读写页面，如 `document.getElementById('msg').textContent = '新内容'` 把某段文字换掉。现代 UI 框架（React / Vue / Svelte / Solid / Angular…）是封装了一层，最终还是改 DOM。

同一个页面，HTML 可以**在不同时机、由不同角色**生成，演化出三种主流渲染模式：

| 模式 | HTML 何时生成 / 谁来生成 | 优点 | 缺点 |
|---|---|---|---|
| **CSR**（Client-Side Rendering, 客户端渲染） | 服务器先送一个**空壳 HTML + JS 文件**；JS 在浏览器里跑起来后，动态生成页面内容 | 后端只负责返回数据，前端打包后是一堆静态文件，部署简单 | 首屏会白屏一小段（要等 JS 下载并执行完才出内容），对 SEO 不友好 |
| **SSR**（Server-Side Rendering, 服务端渲染） | 用户每次请求时，**服务端实时拼好完整 HTML** 返回 | 首屏立刻看到内容、SEO 友好（爬虫直接读到文字） | 必须 7×24 运行一个 Node.js 服务端，架构更复杂 |
| **SSG**（Static Site Generation, 静态生成） | **打包构建时**一次性把每页 HTML 全部预生成，部署为静态文件 | 部署最便宜（任何静态托管都行）、加载最快 | 内容在构建时就定死了，要更新得重新打包发布 |

相关概念说明：

- **SEO**（Search Engine Optimization, 搜索引擎优化）—— 让 Google / 百度 这类搜索引擎能抓到并收录你的页面，用户搜关键词时能搜到你。爬虫主要读 HTML 文本：CSR 给爬虫的是空壳 HTML、抓不到内容；SSR / SSG 直接把内容写进 HTML、爬虫秒读，收录效果好。
- **静态托管 vs Node.js 服务端** —— CSR / SSG 打包出来的是一堆**静态文件**（`.html` / `.js` / `.css`），扔到 Nginx 目录、对象存储、GitHub Pages 这类静态托管就行，没有进程要养。SSR 不一样：每次用户请求都要在**服务端跑 JS 代码**临时拼 HTML 返回，所以必须有一个 24/7 运行的 Node.js 进程 —— 装 Node 运行时 → `npm start` 起进程 → 用 PM2 / systemd / 容器守护（崩了自动重启）→ 配 Nginx 反向代理 → 监控、日志、按流量扩容…… 跟部署一个 FastAPI 服务一个套路，只是技术栈换成 Node。

### 5.1.5. 浏览器加载流程

输入网址 → 看到页面:

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant BR as 浏览器
    participant DNS as DNS 服务器
    participant SV as Web 服务器

    U->>BR: 地址栏输入 URL，回车
    BR->>DNS: 域名解析（host → IP）
    DNS-->>BR: 返回 IP 地址
    BR->>SV: 建立 TCP 连接（HTTPS 还需 TLS 握手）
    SV-->>BR: 连接就绪
    BR->>SV: GET / HTTP/1.1
    SV-->>BR: 200 OK<br/>HTML 文档
    Note over BR: 边接收边解析 HTML，开始建 DOM
    BR->>SV: 遇到 <link> / <script> / <img>，并行拉取
    SV-->>BR: CSS / JS / 图片等子资源
    Note over BR: CSS 解析 → CSSOM（CSS 对象模型）<br/>JS 执行（可能改 DOM）
    Note over BR: DOM + CSSOM → Render Tree<br/>Layout → Paint → 像素呈现
    U->>BR: 用户点击 / 输入等交互
    Note over BR: JS 改 DOM、发新请求<br/>（跨域请求在此触发 CORS 检查 → 见 §5.1.6）
```

几个关键点：

- **DNS / TCP / TLS** 是 HTTP 之下的网络层基础，浏览器自动完成。
- **HTML 流式解析**：浏览器拿到 HTML 是边下载边解析的，不等全部下完。所以越靠前的内容能越早出现在屏幕上
- **子资源并行加载**：HTML 里出现 `<link rel="stylesheet" href="style.css">` / `<script src="app.js">` / `<img src="...">` 时，浏览器**立即并行发起**这些请求，不等 HTML 解析完
- **JS 默认会阻塞 HTML 解析**：遇到 `<script>` 标签浏览器要停下 HTML 解析、先下载并执行 JS，再继续解析。可以加 `async`（下完就执行、不保证顺序）或 `defer`（下完先存着，等 HTML 解析完再执行）来避免阻塞
- **渲染**：DOM（HTML 解析结果）+ CSSOM（CSS 解析结果）合成 **Render Tree**（实际要画的节点）→ **Layout**（算每个节点的位置和大小）→ **Paint**（往屏幕上画像素）
- **交互期**：页面首次渲染完后，用户的每次操作（点击 / 输入 / 滚动）触发 JS 跑，JS 可能改 DOM（视图更新）、可能发新请求（fetch / SSE / WebSocket），这条线一直跑到关闭页面

### 5.1.6. 浏览器安全机制：CORS

**CORS**（Cross-Origin Resource Sharing, 跨域资源共享）：浏览器的一项安全机制 —— 默认不允许网页向**不同来源**的服务发请求。"来源"指 URL 的 `scheme + host + port` 三者组合（**scheme** = 协议，如 `http` / `https`；**host** = 域名或 IP；**port** = 端口号），任意一个不同就算"跨域"。例如 `http://localhost:8000` 三段分别是 `http` / `localhost` / `8000`。

**谁跟谁比、什么时候比**：浏览器记住"当前网页是从哪个 URL 加载来的"（地址栏 URL 对应的来源）。当网页里的 JS 发起请求（`fetch` / `XHR` / 打开 SSE 流等，对应 §5.1.5 流程图最后一段"交互期"）时，浏览器拿**网页自身的来源** vs **请求目标的来源**比一下 —— 不同则触发 CORS 检查。

例：你访问 `http://localhost:5173/index.html`（**网页来源** = `http://localhost:5173`），网页里的 JS 跑 `fetch('http://localhost:8000/api/sessions')`（**请求目标来源** = `http://localhost:8000`），端口不同 → 跨域 → 浏览器拦下请求、抛 CORS 错。**注意：拦截发生在浏览器这一层、不在后端**，后端代码很可能根本不知道有这次失败请求。

两种思路（互补，开发期和生产期都各有对应办法）：

**思路 A：让浏览器看到同源 → 跨域检查根本不触发**

把前端和后端"伪装"成同一来源，浏览器查不出跨域。开发期 / 生产期各自有现成方案：

- **开发期**：前端 dev server（开发期间在本机临时起的 HTTP 服务，给前端代码用 —— 详见 §5.1.7）配置一个代理，把所有 `/api/*` 请求转发给后端
- **生产期**：Nginx / Caddy 等反向代理把前端静态文件和后端 API 挂在**同一个域名**下、按路径分发（如 `https://myapp.com/` 给前端、`https://myapp.com/api/*` 转后端）

这是**主流单用户 / 单部署**项目的默认做法 —— 既无 CORS 烦恼、也避免暴露后端真实端口给外网。

**思路 B：后端启用 CORS → 浏览器放行真正的跨域请求**

如果前后端**就是要部署在不同域**（例如 `app.myapp.com` ↔ `api.myapp.com`，或后端给多个不同前端共用），那真有跨域 —— 这时让后端在响应里加 `Access-Control-Allow-Origin` 这类 header，明确告诉浏览器"这些来源我允许"。例如 FastAPI 加一行 `app.add_middleware(CORSMiddleware, allow_origins=[...])` 即可。

回到你可能的疑问：**dev server 只在开发期跑，那生产 CORS 怎么办？**—— dev server 在开发期顶替了思路 A 中"反向代理"那个角色；生产期换成 Nginx 接手，**同一个"让浏览器看到同源"思路** 跑两套实现。两边底层逻辑一致，不存在断层。

### 5.1.7. 前端工程化基础

前端工程化涉及以下 6 个方面：运行时、依赖、约定、语言特性、构建、开发服务（开发期用，跟最终浏览器里跑的代码无关）：

**(1) Node.js**：浏览器之外的 JavaScript 运行时。既能跑后端服务，也是前端工具链（打包、dev server 等）的运行环境。

**(2) npm**（Node Package Manager）：JS 包管理器。常用命令：

| 命令 | 作用 |
|---|---|
| `npm install <pkg>`（简写 `npm i <pkg>`） | 装单个依赖到当前项目 |
| `npm install` | 按 `package.json` 一次性装全部依赖 |
| `npm run <script名>` | 跑 `package.json` 里 `scripts` 字段定义的命令（如 `npm run dev`、`npm run build`）|

`yarn` / `pnpm` 是 npm 的替代品，能力近似、`pnpm` 更省磁盘，本项目用哪个不影响概念理解。

**(3) `package.json` + `node_modules/`**：

- `package.json`：项目清单，**类比 `requirements.txt` + `setup.py` 合体**。列出依赖、版本、可执行脚本（`scripts` 字段定义 `npm run dev` 这种快捷命令）
- `node_modules/`：依赖装在这里（**类比 venv 的 `site-packages/`**）；体积大、不进 git，靠 `package.json` + `package-lock.json` 锁定版本，重装就 `npm install`

**(4) 模块系统**（`import` / `export`）：JS 也有 import / export，类比 Python：

| Python | JS |
|---|---|
| `from utils import format_time` | `import { formatTime } from './utils'` |
| `import utils` | `import utils from './utils'`（默认导出） |
| 文件即模块 | 文件即模块 |

每个组件 / 工具放一个 `.ts` / `.tsx` 文件、跨文件 `import`，跟 Python 一个习惯。

**(5) 打包**（bundle）：浏览器只认 `.html` / `.js` / `.css`，不认 `.tsx`、不认 `import './foo'` 这种相对路径源码。**打包工具**把整个项目源码：

- 编译：`.tsx`（TypeScript + JSX）→ `.js`
- 合并：解析 `import` 图，把几百个源文件合成几个 bundle 文件
- 压缩：删空格、混淆变量名、tree-shaking 砍掉没用到的代码
- 输出：一个 `dist/` 目录，里面是浏览器能直接消化的产物

**(6) dev server**：工程化工具链的另一件核心 —— **开发期间**临时起的本地 HTTP 服务（典型 `http://localhost:某端口`），让浏览器能直接访问你正在改的前端代码。除了起服务，通常还包含：

- **实时编译**：浏览器请求时把 `.tsx` 这类源码现场转译成 `.js` 发回
- **热刷新**（Hot Module Replacement, HMR）：改源文件后**浏览器自动**显示新结果，不用手动 F5
- **代理转发**：把 `/api/*` 之类请求转到后端（§5.1.6 CORS 思路 A 在开发期就靠它）

注意：dev server 只在**开发时**跑；打包发布后前端产物变成纯静态文件由 Nginx / 静态托管直接吐给浏览器，dev server 不再参与。

类比 Python：dev server ≈ FastAPI 的 `uvicorn --reload` 模式，区别是它服务的不是 API、是**前端代码**给浏览器消化。

打包和 dev server 通常由**同一套工具**一起提供（如 Vite、Webpack、Parcel、Rsbuild 等）；具体选哪个放 §5.2 / §5.3 聊。

### 5.1.8. 浏览器开发者工具

浏览器自带的调试面板，前端开发必备。Chrome / Edge / Firefox 按 **F12** 或右键"检查"打开。三个最常用 tab：

| Tab | 干什么用 |
|---|---|
| **Elements** | 看当前页面实时 DOM 树、看每个元素的 CSS 样式、改样式即时生效（验证效果用） |
| **Console** | JS 报错、`console.log()` 输出（前端的 `print`）、可现场跑 JS 表达式 |
| **Network** | 监听所有 HTTP 请求 / 响应：URL、method、status、headers、body 都能看。**SSE 流也能看到事件一条条进来**，调试 chat 流式特别有用 |

后面调前后端联调遇到问题，第一反应就是开 Network 看请求；遇到页面挂了开 Console 看报错。

### 5.1.9. 声明式 vs 命令式

现代主流 UI 框架（React / Vue / Svelte / Solid / Angular…）都基于同一种思路写 UI。先看两种风格的区别：

- **命令式**（imperative）：一步步告诉机器**怎么做** —— 先找到元素、再改它的属性。`document.getElementById('msg').textContent = '新内容'`（§5.1.4 出现过）就是典型命令式：你亲自操刀 DOM
- **声明式**（declarative）：只描述"**结果长啥样**"，怎么从当前状态变成目标状态由框架算

类比一下"你跟机器说的话"：

| 风格 | 说法 |
|---|---|
| 命令式 | "把那个 div 的字改成『新内容』；再加一个 div 显示『又一条』；再隐藏第三个 div……" |
| 声明式 | "这块区域应该显示当前 `messages` 数组里的每一条消息" |

声明式的核心是 **UI = f(state)**：你只管维护**数据状态**（state），UI 是数据的**函数**；状态变了，框架自动 diff 出哪些 DOM 节点要改、再去改。写交互的脑力负担小很多。

命令式时代以 jQuery 为代表；现在主流 UI 框架基本都是声明式。

### 5.1.10. 完整流程：一次 agent 对话

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant FE as 前端 (浏览器)
    participant BE as 后端 (FastAPI + Agent)

    Note over U,BE: ① 打开应用，拉会话列表（REST）
    U->>FE: 打开页面
    FE->>BE: GET /api/sessions
    BE-->>FE: 200 OK<br/>[{id, title}, ...]
    Note over FE: 渲染左侧会话列表

    Note over U,BE: ② 新建会话（REST）
    U->>FE: 点"新对话"
    FE->>BE: POST /api/sessions
    BE-->>FE: 200 OK<br/>{id: "s1", ...}

    Note over U,BE: ③ 发消息 + 流式接收（SSE）
    U->>FE: 输入"帮我查一下 React hooks"<br/>点发送
    FE->>BE: POST /api/sessions/s1/messages<br/>（响应为 SSE 流）

    Note over BE: 启动 agent loop<br/>调 LLM
    BE-->>FE: event: token<br/>{"chunk":"我"}
    BE-->>FE: event: token<br/>{"chunk":"来查一下…"}
    Note over FE: 气泡逐字追加

    BE-->>FE: event: tool_call<br/>{name:"rag_search", q:"React hooks"}
    Note over FE: 显示"正在搜索…"
    Note over BE: 执行 RAG 检索
    BE-->>FE: event: tool_result<br/>{chunks:[...]}
    Note over FE: 渲染引用源列表

    Note over BE: LLM 续写
    BE-->>FE: event: token<br/>{"chunk":"根据文档…"}
    BE-->>FE: event: token<br/>{"chunk":"..."}
    BE-->>FE: event: done
    Note over FE: 关闭 loading 状态
```

关键点：

- **拉列表 / 新建会话**用普通 REST：请求一次、响应一次，body 是完整 JSON
- **发消息后改用 SSE**：单向长连接，后端边产 token 边推；前端边收边渲染，不等全部完成
- **Agent 内部 loop 在后端展开**（LLM → tool call → tool result → 续写）；前端只需识别 SSE 事件类型（`token` / `tool_call` / `tool_result` / `done`），不必懂 agent 状态机
- **同一个 session id 串起来**：之后所有对话事件都挂在 `/api/sessions/s1/...` 下，刷新页面也能从 DB 恢复

## 5.2. 相关技术

### 5.2.1. 概述

先建立"前端三件套 vs 工具栈"的对应（理解后面各节都靠它），再列本期各层候选 + 整体架构图。

**(1) 前端三件套 vs 工具栈对应**

一张网页 = **HTML（结构骨架）+ CSS（样式）+ JavaScript（交互）** —— 这是浏览器最终消化的三种产物形式。但前端工程化时源码不是直接这三种，而是通过工具链编译出这三种。下面这套技术怎么落到三件套：

| 工具 | 你写的源码长啥样 | 最终落到浏览器哪一层 |
|---|---|---|
| **Tailwind CSS** | `class="p-4 bg-blue-500"`（直接写在 HTML 标签的 `class` 属性里） | **CSS** —— 构建时 Tailwind 扫源码、把用到的工具类生成成 CSS 规则、产出一份 `.css` 文件 |
| **React** | `.tsx` 文件（JSX 描述结构 + JS 逻辑） | **JS（兼管动态生成 HTML）** —— JSX 被编译成 JS 函数调用、运行时**动态产出 HTML 元素**插入 DOM；交互（`onClick` 等）也在 JS 里 |
| **shadcn/ui** | `import { Button } from '@/components/ui/button'`（用 React 组件） | **横跨三层** —— 它就是"已经封好的 React 组件 + 内嵌 Tailwind class"，所以同时落 JS（React 部分）、HTML（组件渲染出的结构）、CSS（内嵌 Tailwind class 编译出的样式） |

几个关键澄清：

- **React 实际上"管两层"：JS + 动态 HTML** —— 回扣 §5.1.4 的 CSR（客户端渲染）：浏览器拿到的 `index.html` 几乎是空壳（只有 `<div id="root"></div>`），整个页面结构是 JS 跑起来后**动态生成**塞进去的。React 干的就是这件事
- **Tailwind 反常的地方：不切到 `.css` 文件写** —— 传统写 CSS 是建个 `styles.css` 文件、写 `.card { padding: 1rem; ... }` 规则。Tailwind 反过来 —— 你直接在 HTML 标签 `class` 属性里组合**预定义工具类**，构建时 Tailwind 扫描源码、把用到的类生成最终 CSS。**写法上几乎不碰 `.css` 文件，但最终产物还是 CSS**
- **shadcn/ui = React + Tailwind 的成品组件套装** —— shadcn 每个组件源码大概长这样（简化版）：

```tsx
export function Button({ children, ...props }) {
  return (
    <button
      className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-md"
      {...props}
    >
      {children}
    </button>
  )
}
```

React 那部分（`function Button` + JSX 标签 + `{...props}`）→ JS / 动态 HTML；`className=` 里那串 Tailwind 工具类 → CSS。用的时候 `<Button>确定</Button>` 一行调用，背后复杂 class 列表已经被组件吸收掉了 —— 这就是 §5.2.6 (5) 里"组件化封装解决 Tailwind class 一长串看着乱"的具体例子。

源码层 ↔ 浏览器层映射总览：

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart TB
    subgraph Source["源码层"]
        TSX[".tsx 文件<br/>React 组件 + JSX"]
        TW["Tailwind 工具类<br/>(写在 className 属性里)"]
        SH["shadcn/ui 组件<br/>import { Button } 用"]
    end
    VITE["Vite（构建工具，详 §5.2.9）<br/>调用 @vitejs/plugin-react + Tailwind PostCSS 插件"]
    subgraph Browser["浏览器消化层"]
        H["HTML<br/>(JS 动态生成的 DOM)"]
        C["CSS<br/>(Tailwind 编译产物)"]
        J["JS<br/>(React 编译产物)"]
    end
    SH -->|"内含 React 代码"| TSX
    SH -->|"内含 Tailwind class"| TW
    TSX --> VITE
    TW --> VITE
    VITE -->|"编译"| J
    VITE -->|"扫描 + 编译"| C
    J -->|"运行时生成"| H
```

**(2) 按层看候选技术**

**加粗** 的是 §5.3 待决的主候选。**所有候选 §5.2 后续小节都会展开**，方便对比后再拍板。

| 层 | 角色 | 候选 |
|---|---|---|
| **语言层** | 浏览器最终消化的代码 + 写代码用的语言 | HTML / CSS / JavaScript / **TypeScript** |
| **工程化层** | 开发期工具链：运行时 + 包管理 + 构建打包 | **Node.js**（运行时）/ **npm**（包管理）/ 打包工具见 UI 层（Vite 或 Next.js 内置）|
| **前端 UI 层** | 浏览器里运行时的框架、样式方案、组件库 | UI 库 **React**（确定要用）<br/>**是否套元框架**：直接 **React + Vite**（纯 CSR） vs **Next.js**（内含 React + 自带构建 / SSR / SSG）<br/>样式 **Tailwind CSS**（或原生 CSS / CSS-in-JS）<br/>组件库 **shadcn/ui**（或 MUI / Ant Design）|
| **通信层** | 前后端通信协议 | **HTTP REST**（常规请求）/ **SSE**（chat 流式响应）/ WebSocket（本项目不用）|
| **后端层** | Python 服务 + 业务代码 | **FastAPI**（本期新加，包项目已有的 **Agent + RAG** 核心成 HTTP 接口） |
| **部署层** | 生产环境反向代理 + 静态托管 | **Nginx**（或 Caddy / 静态托管平台）|

整体架构与数据流（**以 React + Vite 候选**为例 —— Next.js 候选会内置 Vite 那一层，整体管线略有不同，§5.2.5 对比展开）：

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart TB
    subgraph BuildTime["构建期（一次性，开发机上跑）"]
        direction TB
        SRC["源码<br/>TypeScript + React + Tailwind CSS"]
        BUILD["Vite 打包<br/>(在 Node.js + npm 上跑)"]
        DIST["静态产物<br/>.html / .js / .css"]
        SRC --> BUILD --> DIST
    end

    subgraph Runtime["运行期（每次用户请求）"]
        direction TB
        PROXY["反向代理 / 静态托管<br/>生产期: Nginx<br/>开发期: Vite dev server"]
        BR["浏览器<br/>React 渲染 + shadcn/ui 组件 + DOM"]
        BE["FastAPI 后端<br/>Agent + RAG 业务"]
    end

    DIST -->|"部署"| PROXY
    PROXY -->|"静态文件 (/ 和 /static/*)"| BR
    BR <-->|"HTTP REST / SSE: /api/*"| PROXY
    PROXY -.->|"/api/* 透传"| BE
```

读图要点：

- **构建期（上方框）**：源码 → Vite 打包 → 静态产物。**部署一次**之后不再跑
- **运行期（下方框）**：浏览器跟后端的所有通信都走**同一道反向代理**：静态文件请求落到代理本地、`/api/*` 透传到后端进程（同源化，规避 CORS —— §5.1.6 思路 A）
- **开发期 vs 生产期**：架构同构、中间那层"反向代理"的实现不一样 ——
  - 开发期 = Vite dev server 顶替（同时承担打包 + 转译 + 代理 + HMR 热刷新）
  - 生产期 = Nginx 顶替（只做静态托管 + 代理，不再编译；JS 真正在浏览器里跑）

### 5.2.2. JavaScript / TypeScript

**JavaScript（JS）**：浏览器原生唯一支持的脚本语言。其他语言要在浏览器跑，**必须先编译/转译到 JS**（这也是 TypeScript 存在的前提）。

几个关键点：

- **动态弱类型**：变量类型运行时定，会做**隐式转换**（如 `"1" + 1 === "11"`、`[] + {} === "[object Object]"`）—— 反直觉行为是 JS 的"坑"主要来源
- **版本叫 ECMAScript（ES）**，每年一版。**ES6（即 ES2015）是分水岭**：引入 `class` / `let` / `const` / 箭头函数 / 模板字符串 / 模块系统 等；之后大家说"现代 JS"基本指 ES6+ 的写法
- **运行环境**：浏览器是主场，**Node.js** 让它也能跑服务端 / 工具链（详 §5.1.7 (1)）

**TypeScript（TS）**：JS 的**超集** + 静态类型层。微软出品，2012 年起，现在是大型前端项目事实默认（**新 React 项目 90%+ 都用 TS**）。

- **"超集"含义**：合法 JS 就是合法 TS（不必改逻辑就能跑），TS 只是在 JS 之上**加类型标注**
- **类型擦除**：`.ts` / `.tsx` 源码经打包工具编译后变成**纯 JS**；**运行时只有 JS 在跑、TS 类型不存在**。类型只在**编译期**帮你查错 + 帮编辑器做自动补全 / 跳转 / 重构
- **为什么大家加 TS**：少踩 `undefined is not a function` 这种运行时翻车；IDE 体验明显更好；项目大起来后可维护性强

同一段代码 JS vs TS 对比：

```js
// JS
const send = async (msg) => {
  const result = await fetchData(msg.content)
  return result.text
}
```

```ts
// TS：加了类型标注
interface Message { id: string; content: string }

const send = async (msg: Message): Promise<string> => {
  const result = await fetchData(msg.content)
  return result.text
}
```

**类比 Python**：跟 Python 类型注解（`def f(x: int) -> str`）思路一致 —— **编译/编辑期检查、运行时不强制**。Python 用过类型注解的人上手 TS 概念上差别不大；差别仅在 Python 的类型是语言原生、TS 的类型是后加层。

### 5.2.3. Node.js

Node.js 是 JS 运行时、npm 是包管理器 —— 这一节从**实操**角度补全：怎么装、怎么开新项目、几个包管理器怎么选、npx 又是什么。

**(1) 安装 + 验证**

Windows 下两种装法：

| 方式 | 优点 | 缺点 |
|---|---|---|
| **官网安装包**（[nodejs.org](https://nodejs.org)） | 简单，下完即用 | 多版本切换麻烦 |
| **多版本管理工具**（如 nvm-windows / fnm） | 多版本并存，按项目切换 | 多装一层工具 |

选最新的 **LTS（Long-Term Support, 长期支持）** 版本（偶数版本号，如 22.x、24.x），维护周期长、企业项目都用它。

装完验证：

```bash
node -v   # 如 v22.10.0
npm -v    # 如 10.9.0
```

**(2) 跑 JS 脚本**


```bash
node script.js        # 跑一个文件
node                  # 进入 REPL（交互式，类似 python 命令）
```

**(3) 项目级流程：从 package.json 装齐依赖 + 跑命令**

先记住一条**核心模式**：

> **只要当前目录有 `package.json` 但缺 `node_modules/`，就 `npm install`（无参数）一把装齐**。`package.json` 怎么来的不影响 —— 脚手架生成、git clone、手写都一样。

`npm install` 命令有**两种语义**，区分清楚：

| 命令形式 | 干什么 | 典型场景 |
|---|---|---|
| `npm install`（无参数） | 读当前 `package.json`，**按清单装齐**所有依赖到 `node_modules/` | 脚手架刚生成完 / git clone 完 / 清掉 `node_modules` 想重装 |
| `npm install <包名>` | **加一个新依赖**到当前项目（同时写进 `package.json` 清单） | 开发过程中需要引入新工具 / 新库 |

**(3a) 从零起一个新项目** —— 通常**用脚手架一键生成骨架**，不会手动一步步建：

```bash
npx create-vite my-app    # 脚手架生成项目骨架：package.json、index.html、src/ 等（不装依赖）
cd my-app                 # 此时有 package.json，没有 node_modules/
npm install               # 按上面那条核心模式：读 package.json 装齐依赖
npm run dev               # 跑起来
```

教学用，也可以**手动一步步建**（实际很少这样）：

```bash
mkdir my-app && cd my-app
npm init -y                          # 手动生成最简 package.json
npm install vite                     # 加一个新依赖（自动写入 package.json）
npm install --save-dev typescript    # -D / --save-dev = 装为"开发依赖"（开发 / 构建用、运行时不需要）
# 之后手动编辑 package.json 加 scripts、自己建目录、自己写代码……
```

**(3b) 接手别人的项目**（从 git clone 拿到的代码） —— `package.json` 在仓库里，但 `node_modules/` **不进 git**（被 `.gitignore` 排除，体积大且可从清单还原）：

```bash
git clone <repo> && cd <repo>     # 此时有 package.json，没有 node_modules/
npm install                       # 同一条核心模式：读 package.json 装齐依赖
npm run dev                       # 跑起来
```

可以看到 **(3a) 和 (3b) 的 `npm install` 是同一回事** —— 都是"按 package.json 装齐"。区别只在 `package.json` 的**来源**：脚手架现写 vs git 拉下来。

**(3c) `scripts` 字段是怎么工作的**

`package.json` 里的 `scripts` 字段定义快捷命令（脚手架或前任已写好）：

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint ."
  }
}
```

执行 `npm run dev` 时，npm 找到 `scripts.dev = "vite"`，于是去 `node_modules/.bin/vite` 跑这个命令。`npm run build` / `npm run lint` 同理。
**`node_modules/.bin/` 里的工具不在系统 PATH 里、不能直接命令行调用**，必须靠 `npm run`（或 `npx`）来跑。

**(4) npm / yarn / pnpm**

三个包管理器，都能装 npm 仓库里的包：

| 工具 | 特点 | 命令示例 |
|---|---|---|
| **npm** | Node 自带、最广用、最稳定 | `npm install` / `npm run dev` |
| **yarn** | Facebook 出品，2016 因速度优势火过一阵；现在 npm 也补齐了 | `yarn add react` / `yarn dev` |
| **pnpm** | **省磁盘**：用硬链接共享全局缓存（同一个包不会被 100 个项目各装 100 份）；启动也快 | `pnpm install` / `pnpm dev` |

**命令大同小异、行为基本兼容**。本项目用 **npm**（跟 Node.js 一起装、无需额外工具）就够；如果你同时维护多个前端项目，可考虑 pnpm 省点磁盘。

**(5) npx**

**本质**：一个**智能命令运行器**。你跑 `npx <工具> <参数>` 时，它按下面顺序找 `<工具>` 然后执行：

1. **当前项目 `node_modules/.bin/` 里有？** → 直接跑
2. **没有？** → 从 npm registry **临时下载**该包到一个缓存目录（默认 `~/.npm/_npx/`）、跑完留着备下次用 —— **不写进 `package.json`、不污染 `node_modules/`、不动全局 PATH**

随 npm 5.2+ 自带（装了 Node 就有），不用额外装。

**为什么需要它**：npm 包里的 CLI 工具（`vite` / `eslint` / `create-vite` 等）必须装在 `node_modules/.bin/` 才能跑，但这目录**不在系统 PATH**，命令行打 `vite` 找不到。在 npx 出现前你只有两个不爽的选项：

| 老办法 | 怎么做 | 痛点 |
|---|---|---|
| **全局装** | `npm install -g vite` | 污染全局；不同项目要不同版本会冲突 |
| **本地装 + 写 `scripts`** | 装到本地 → `package.json` 加 `"vite": "vite"` → `npm run vite` | 一次性的命令也要写进 `scripts`，烦 |

npx 把这两个痛点一起解了：

| 场景 | npx 用法 | npx 帮你做的事 |
|---|---|---|
| 跑**本项目装好的**工具、不想加进 `scripts` | `npx vite` | 自动去 `node_modules/.bin/` 找，比敲 `./node_modules/.bin/vite` 短 |
| 跑**没装的**一次性工具 / 脚手架 | `npx create-vite my-app`<br/>`npx prettier --write .` | 自动下载、跑完缓存（下次更快），不污染项目 |
| 跑**特定版本** | `npx vite@4` | 跟项目里装的版本无关，临时切到指定版本 |

**跟 `npm run` 的关系**：

| 命令 | 跑什么 | 来源 |
|---|---|---|
| `npm run <script>` | `package.json` 里 `scripts` 字段定义的快捷命令 | **本项目长期要跑的命令** |
| `npx <pkg> [args]` | 任意 npm 包的可执行入口 | **本地装的 或 临时下载** |

简记：**`npm run` 跑自家长期脚本；`npx` 跑别人家工具（一次性或临时）**。

实务里你最常见的就两种场景：

- `npx create-xxx my-app` —— 用脚手架起新项目（§5.2.3 (3a) 那个例子就是这个）
- `npx prettier --write .` / `npx eslint --fix .` —— 临时跑代码格式化 / 检查

补一句：`yarn dlx` / `pnpm dlx` 是 yarn / pnpm 的等价物（`dlx` = download & execute），概念完全一样。

### 5.2.4. React

**(1) 是什么**

React 是 Meta 开源（2013）的**前端 UI 库**。它自己定义自己是"库"（library）而非"框架"（framework）：**只管把"状态 → 视图"这件事渲染好**，其他事（路由、构建、数据获取、状态管理）都不管，留给生态。

定位决定了：用 React 时你需要**自己拼套件**（详 (5)），但每件事都能换、职责清晰。

**(2) 跟传统 DOM 操作的思路差异**

跟传统 jQuery / 原生 DOM 操作风格反过来（§5.1.9 讲过命令式 vs 声明式）：

| 风格 | 怎么写 |
|---|---|
| **命令式**（jQuery / 原生 DOM） | 你**亲自操刀**：找到 DOM 元素 → 改它的属性 / 文本 / 类名；每次状态变化都要手动同步 |
| **React（声明式）** | 你只描述"**当前状态下 UI 长啥样**"；状态变了 React 自动算出 DOM 怎么改 |

直观对比"点击按钮、计数 +1"：

```javascript
// 命令式（jQuery / 原生）
let count = 0;
document.getElementById('btn').addEventListener('click', () => {
  count++;
  document.getElementById('display').textContent = count;
});
```

```jsx
// React（声明式）
function Counter() {
  const [count, setCount] = useState(0);
  return (
    <div>
      <span>{count}</span>
      <button onClick={() => setCount(count + 1)}>+1</button>
    </div>
  );
}
```

React 这边你**没碰任何 DOM**，只描述 "UI = `count` 这个状态对应的样子"；`setCount` 一调用、React 自动 diff 出 `<span>` 该改、改之。

**(3) 核心概念**

| 概念 | 一句话 |
|---|---|
| **组件**（component） | UI 的最小复用单位，本质是个**返回 JSX 的 JavaScript 函数**（如上面的 `Counter`） |
| **JSX** | "在 JS 里写 HTML"的语法糖，编译期被翻译成 `React.createElement(...)` 调用；同时支持 `{表达式}` 嵌入 JS |
| **props**（属性） | 父组件传给子组件的"参数"（**只读**），类似函数参数：`<Button text="确定" />` |
| **state**（状态） | 组件内部"可变数据"，靠 `useState` 等 Hook 管理；状态变 → 组件重新渲染 |
| **单向数据流** | 数据靠 props 父 → 子；子要改父的数据，靠父传一个回调下来（如 `<input onChange={...}>`） |
| **Hooks** | `useState` / `useEffect` 等以 `use` 开头的函数，React 16.8 后的标准 API，给函数组件加状态 / 副作用 |

**(4) 工作原理**

```mermaid
flowchart LR
    S["状态变化<br/>setCount(1)"] --> RE["组件函数重新执行<br/>返回新 JSX"]
    RE --> VDOM["生成新虚拟 DOM 树<br/>(JS 对象)"]
    VDOM --> DIFF["跟上次虚拟 DOM diff<br/>算出最小改动"]
    DIFF --> DOM["只改真正变了的 DOM<br/>(浏览器实际渲染)"]
```

**虚拟 DOM**：React 在内存里维护一份 UI 的 JS 对象表示，**比直接操作浏览器真实 DOM 便宜得多**（真实 DOM 操作会触发 reflow / repaint）。状态变时 React 先在虚拟 DOM 里算清要改啥、再批量同步到真实 DOM。

实际写代码时这层完全**对你透明** —— 你只写组件函数、调 `setState`，diff 算法是 React 内部的事。

**(5) "用 React" 实际上是拼一套**

React 不带这些能力，要自己挑：

| 责任 | 典型选择 |
|---|---|
| 构建 / dev server | **Vite**（§5.2.9）/ Webpack / Rsbuild |
| 路由 | React Router |
| 全局状态（跨组件） | Context API / Zustand / Redux |
| 数据获取（带缓存 / 重试） | TanStack Query / SWR / 自己 `fetch` |
| 样式方案 | **Tailwind CSS**（§5.2.6）/ CSS Modules / CSS-in-JS |
| 组件库 | **shadcn/ui**（§5.2.7）/ MUI / Mantine / Ant Design |
| 表单 | React Hook Form / Formik |

加粗的是本项目候选。**Vite + Tailwind + shadcn/ui** 是当前社区里跟 React 配的"黄金组合"之一，社区有现成模板（如 `npx create-vite -- --template react-ts`），不会真的从零拼。

**(6) 编辑器体验 / 生态**

- **TypeScript**：React 跟 TS 配合极顺（组件 props 直接当 TS interface 类型）。本项目走 TS 不走 JS（§5.2.2）
- **React DevTools**：浏览器扩展（Chrome / Firefox），看组件树、看每个组件当前的 props / state，调试比命令式时代香多了（§5.1.8 浏览器 DevTools 的延伸）
- **AI 友好**：训练语料里 React 占绝对大头，Copilot / Cursor 写 React 组件相当熟

**(7) 对本项目为什么用 React**

- 候选竞品里 **Vue 也很优秀**，但：
  - **shadcn/ui 只支持 React** —— shadcn 是本项目想要的组件库（§5.2.7），Vue 对应方案（shadcn-vue 是社区移植）社区规模小一档
  - React 生态规模更大，AI 写 React 组件更熟
  - 单用户私有工具不需要 Vue 的"渐进增强"优势
- React 跟 **FastAPI + Vite + Tailwind + shadcn** 这套组合的耦合最自然
- 声明式 + 组件化匹配 Claude / ChatGPT 这类聊天 UI 的天然组件分解（消息气泡 / 工具调用块 / 侧栏 session 列表等）

跟 Next.js 的对比 + 选型决策见 §5.2.5。

### 5.2.5. React vs Next.js

这是真正需要选型的地方 —— 两者都是当前前端主流，但定位差异大。React 已在 §5.2.4 详讲，本节聚焦 Next.js 的差异 + 整体对比 + 对本项目的初步看法。

**(1) React** —— 详 §5.2.4。简言之：库 + 自拼套件（构建 / 路由 / 状态 / 样式 / 组件库都自挑），灵活、职责清晰；典型组合 `React + Vite + Tailwind + shadcn/ui + React Router + ...`。

**(2) Next.js**

Vercel 主导（2016），**基于 React 的全栈框架**。在 React 之上**预装预配**了一整套，开箱即用：

| 维度 | Next.js 做了啥 |
|---|---|
| 构建 / dev server | 内置（Turbopack / Webpack） |
| 路由 | **文件系统路由** —— `app/foo/page.tsx` 自动对应 `/foo` |
| 渲染模式 | CSR / SSR / SSG / ISR 全支持，按需混用（详见 §5.1.4） |
| 后端能力 | **API routes** / **Server Components** / **Server Actions** —— 自带一个 Node 服务端，能在同一项目里写后端接口 |
| 优化 | 图片 / 字体 / 元数据 / 代码分割 一系列开箱优化 |
| SEO | 天然友好（SSR / SSG） |

**优点**：**约定胜配置**（convention over configuration）、一站式；中大型内容站 / 电商 / 重 SEO 场景里**省事优势**明显。
**代价**：Next.js 自带的能力**大半绑定它的 Node 后端**，你不用那部分就是白搭；学习坡度也比"裸 React"陡（特别 App Router 引入 Server Components 之后）。

**(3) 整体对比**

| 维度 | React + Vite | Next.js |
|---|---|---|
| 类型 | 库 + 自己拼套件 | 一站式框架 |
| 是否带 Node 服务端 | ❌（纯前端，部署纯静态产物） | ✅（要 Node 进程跑 SSR / API routes，除非用纯静态导出） |
| 渲染模式 | CSR 为主 | CSR / SSR / SSG / ISR 灵活混用 |
| 路由 | React Router 等（显式声明） | 文件系统约定（隐式） |
| 后端 API | **不带** —— 用你自己的后端（如 FastAPI） | **内置** —— 同项目写 API routes，或调外部 |
| SEO | 默认弱（CSR），要好得自己上 SSR / 预渲染 | 默认强 |
| 学习曲线 | React 本身简单，拼装散件要花时间 | 概念多（路由约定 / RSC / 缓存策略 / Server Actions），跟模板能跑、深入要花时间 |
| 适合场景 | SPA、内部工具、跟非 Node 后端搭、个人项目 | 内容站、重 SEO 场景、要 SSR 的大型应用、想用一个项目搞前后端 |
| 部署 | `dist/` 一坨静态文件，Nginx / 任意静态托管 | 部署 Node 服务（Vercel 一键 / 自托管），或退化成静态导出 |

**(4) 对本项目的初步看法**

需求侧关键事实（来自 §1 / §4）：

- 单用户、本地 / 私有部署
- 已有 **Python 写的 Agent + RAG 核心**（`src/agent/` + `src/rag/`），目前对外只有 CLI 入口；**本期会在它之上新加一层 FastAPI** 把核心包成 HTTP 接口（详 §6 实现）
- **不需要 SEO**（私有工具，没人 Google 它）
- 主要交互：聊天、文件拖拽入库、看 session 历史；流式输出用 SSE
- "我不学前端、只提需求"

把 Next.js 的核心卖点逐条对一下：

| Next.js 卖点 | 本项目用得上吗 |
|---|---|
| SSR / SEO | ❌ 私有工具，没人搜 |
| API routes / Server Components / Server Actions | ❌ 后端确定走 FastAPI（包 Python 写的 Agent + RAG 核心），不需要再起一层 Node API |
| 文件系统路由 | 用得上，但 React Router 也能做，差别不大 |
| 图片 / 字体 / 元数据优化 | ❌ 不是内容站 |

也就是 **Next.js 给的额外能力本项目大半用不到，却要为此承担一个 Node 服务端 + 一堆复杂概念**。反观 **React + Vite**：纯静态产物 → Nginx 直接服务 → 调 FastAPI；shadcn/ui 在 React + Vite + Tailwind 这组合下也最舒服 —— 整条流程简单、可控。

所以 §5.3 会倾向 **FastAPI + Vite + React + shadcn/ui** 这条线；正式决定留给 §5.3。

### 5.2.6. Tailwind CSS

**(1) 是什么**

Tailwind CSS 是个 **utility-first CSS 框架**（utility-first = "工具类优先"）。Adam Wathan 2017 出，现在 React 生态最主流的 CSS 方案之一。

它跟传统 CSS 的思路**反过来**：

| 风格 | 怎么写 |
|---|---|
| **传统 CSS** | 自己定义 class（如 `.card`） → 给 class 写一坨样式 → HTML 里引用 class 名 |
| **Tailwind** | 框架预定义一大堆**原子级工具类**（如 `p-4` = padding 1rem、`bg-blue-500` = 蓝色背景） → 你直接在 HTML 里**组合**它们、不自己写 CSS |

直观对比同一个卡片：

```css
/* 传统 CSS */
.card {
  padding: 1rem;
  background: white;
  border-radius: 0.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
```
```html
<!-- 传统 CSS：HTML 引用 class -->
<div class="card">...</div>

<!-- Tailwind：不写 CSS 文件，直接 HTML 组合工具类 -->
<div class="p-4 bg-white rounded-lg shadow">...</div>
```

**(2) 为什么这种"反常"做法反而火了**

传统 CSS 痛点 → Tailwind 怎么解：

| 传统 CSS 痛点 | Tailwind 的解法 |
|---|---|
| **命名困难** —— 给每个组件想 class 名（`card` / `card-header` / `card-title-active` ...）很累 | 不命名了，直接用预定义工具类组合 |
| **样式越积越多** —— 项目久了 CSS 几千行，改一处怕影响别处 | 样式直接在 HTML 里，删元素 = 删样式，无残留 |
| **HTML 和 CSS 文件来回跳** | 全在一个文件 |
| **没用的 class 难清理** | 构建时**自动 tree-shake**：扫描源码、只把**用到过**的 class 编译进最终 CSS，没用的全去掉 → **最终 CSS 体积非常小**（一般几十 KB） |
| **样式天马行空、跨页面不统一** | 预定义的**设计 token** 约束你的选择（见下） |

**(3) 设计 token（约束你不要乱写）**

Tailwind 不是"啥都能写"。颜色、间距、字号等都是**预定义离散值**：

| 类别 | 工具类示例 | 对应 CSS 值 |
|---|---|---|
| 间距 | `p-1` / `p-2` / `p-4` / `p-8` | `padding: 4px` / `8px` / `16px` / `32px`（按 4px 步进） |
| 颜色 | `bg-blue-500` / `text-gray-900` | 预定义色阶（每色 50/100/.../900） |
| 字号 | `text-sm` / `text-base` / `text-lg` | 14px / 16px / 18px |
| 圆角 | `rounded` / `rounded-lg` / `rounded-full` | 4px / 8px / 9999px |
| 响应式 | `md:p-8` | 中屏以上 `padding: 32px`（移动优先思路） |
| 状态 | `hover:bg-blue-600` / `focus:ring-2` | 鼠标悬停 / 聚焦时的样式 |

这种**离散约束**反过来是个优点 —— 你不会随便写 `padding: 13px` 让 UI 稀奇古怪，组件之间天然协调。需要例外时也能逃生：`p-[13px]` / `bg-[#abc123]` 用方括号包任意值。

**(4) 工作原理**

```mermaid
flowchart LR
    SRC[".tsx / .html 源码<br/>含 class='p-4 bg-blue-500'"] -->|"Tailwind 插件扫描源码"| GEN["生成 CSS<br/>只含用到的 class 规则"]
    GEN --> CSS["最终 styles.css<br/>体积小（几十 KB）"]
    CSS --> BR["浏览器加载"]
```

Tailwind 是个 **构建期工具**（跟 Vite 集成），运行时浏览器看到的就是一份普通 CSS。

**(5) "HTML 里 class 一长串、看着乱" 怎么办**

实际写 Tailwind 经常一个元素 class 列表很长：

```html
<button class="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-md transition">
  确定
</button>
```

确实视觉上比传统 `<button class="btn-primary">` 脏。两个常见缓解办法：

1. **组件化封装** —— React 里写一个 `<Button>` 组件、把 class 列表封进去，使用方就清爽 —— **这就是 shadcn/ui 在做的事（§5.2.7 详讲）**
2. **Prettier 插件自动排序** class —— 按一个标准顺序排，至少让长 class 列表有规律

**(6) 编辑器体验**

- VS Code 装 **Tailwind CSS IntelliSense** 插件 → 输入 `p-` 自动补全所有间距类、悬停看到对应 CSS 值
- AI 工具（Copilot / Cursor）对 Tailwind 也很熟，写起来基本是"描述意图、AI 补 class"

**(7) 对本项目为什么用 Tailwind**

- 跟 React + Vite 集成简单（一行 npm 安装 + 几行配置）
- 跟 **shadcn/ui 强绑定** —— shadcn 的组件全部用 Tailwind class 写，**用 shadcn 就要用 Tailwind**（§5.2.7）
- 默认设计 token 已经够好，单用户私有工具不需要复杂主题
- 单页应用、组件不多，最终 CSS 体积极小

### 5.2.7. shadcn/ui

**(1) 是什么**

shadcn/ui 是个人开发者 @shadcn（Vercel 工程师）2023 年发布的一套 **React 组件集合**，基于 **Radix UI**（无样式的行为层）+ **Tailwind CSS**（样式层）构建。**它不是传统意义上的"组件库"**，而是一种**新型分发方式**：你不 `npm install` 它、而是**用 CLI 把组件源码复制进你的项目**。

GitHub 80k+ 星，当前 React + Tailwind 生态里最火的组件方案，已经是事实标准。

**(2) 反常的"安装方式"：复制源码进项目**

跟传统组件库（MUI / Ant Design / Mantine）的核心差异：

| 维度 | 传统组件库 | shadcn/ui |
|---|---|---|
| 安装 | `npm install antd` → 装到 `node_modules/` | `npx shadcn@latest add button` → CLI **把 Button 的源码文件直接复制到 `src/components/ui/button.tsx`** |
| 引用 | `import { Button } from 'antd'`（从 `node_modules`） | `import { Button } from '@/components/ui/button'`（从**你自己的项目**） |
| 代码位置 | `node_modules/` 里，你不动 | 你的项目里，**你可以随便改** |
| 升级方式 | `npm update antd` | 重新跑 `npx shadcn@latest add button`（覆盖原文件） |
| 定制深度 | 受限于库提供的 API（theme / className） | **完全自由** —— 它就是你的代码，直接编辑 |

为什么这么反常？解决传统组件库两大痛点：

1. **定制难** —— 传统库每个组件只暴露有限的 props，想改细节要么硬用 `className` 强行覆盖 CSS（脆弱）、要么 fork 库（更脆弱）。shadcn 让组件源码就在你项目里，**改就行**
2. **bundle 膨胀** —— 传统库即使 tree-shake 也可能拖入运行时依赖。shadcn 只复制你用到的组件、依赖也明确（Radix + Tailwind），最终 bundle 小

代价：组件**没有自动更新**。但聊天 UI / 后台工具这类应用，组件升级频率很低，**自由度比自动更新更重要**。

**(3) 组件结构（一个 Button 长啥样）**

跑完 `npx shadcn@latest add button`，`src/components/ui/button.tsx` 大概长这样（简化版）：

```tsx
import { cva } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-md text-sm font-medium ...",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        outline: "border border-input bg-background hover:bg-accent ...",
        ghost: "hover:bg-accent hover:text-accent-foreground",
      },
      size: { sm: "h-8 px-3", default: "h-9 px-4", lg: "h-10 px-8" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
)

export function Button({ className, variant, size, ...props }) {
  return (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}
```

读这段就懂结构：

- **`cva`**（class-variance-authority）—— 小工具，定义"基础 class + 按 `variant` / `size` 切不同 class"的规则
- **`cn`** —— shadcn 自带的 helper，合并 class（智能去重、支持外部 `className` 覆盖）
- **Tailwind class** —— 所有样式都靠 Tailwind 工具类堆出来（§5.2.6）
- **Radix UI** —— 这里 Button 没用到；复杂组件（Dialog / Dropdown / Tooltip 等）会用 Radix 拿到无样式的**行为基础**：焦点管理、键盘导航、屏幕阅读器支持、动画过渡

**(4) 跟 Radix / Tailwind / cva 的关系**

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart TB
    subgraph SCN["shadcn/ui 组件源码（复制进你项目）"]
        BTN["Button.tsx"]
        DLG["Dialog.tsx"]
        DRP["Dropdown.tsx"]
    end
    subgraph DEPS["底层依赖（npm 装的）"]
        RDX["Radix UI<br/>(行为 + 无障碍 + 无样式)"]
        TW["Tailwind CSS<br/>(样式工具类)"]
        CVA["cva + clsx<br/>(class 合并工具)"]
    end
    BTN -->|"复杂组件用"| RDX
    DLG --> RDX
    DRP --> RDX
    BTN -->|"className= 写"| TW
    DLG --> TW
    DRP --> TW
    BTN --> CVA
    DLG --> CVA
    DRP --> CVA
```

shadcn/ui 本质 = **把 Radix（行为）+ Tailwind（样式）+ cva（变体管理）三者粘合在一起的、可读可改的源码模板**。

**(5) 实际使用流程**

```bash
npx shadcn@latest init
# 跟着 CLI 问答选：TypeScript、Tailwind 配置位置、color scheme（默认 / new-york / ...）等

npx shadcn@latest add button
npx shadcn@latest add dialog
npx shadcn@latest add input
# 用哪个 add 哪个；每次 add 都会在 src/components/ui/ 下生成一个 .tsx 文件
```

业务代码里调用跟传统组件库**几乎一样**：

```tsx
import { Button } from "@/components/ui/button"

<Button variant="outline" onClick={handleClick}>取消</Button>
```

差别只在它的源码**就在你项目里**、你可以改。

**(6) 对本项目为什么用 shadcn/ui**

- 跟 **React + Vite + Tailwind** 组合最自然（这套组合的当前事实标准）
- 聊天 UI 需要的组件 shadcn 都有：`Button` / `Input` / `Textarea` / `ScrollArea` / `Sheet` / `Dialog` / `Tooltip` / `DropdownMenu` / `Avatar` / `Card` ……
- 美学接近 Claude / Vercel / Linear 这类现代极简风，匹配 §4.2 UX 风格目标
- **可改的源码**对自定义聊天气泡 / 工具调用块这类**非标准 UI** 很友好 —— 想改直接动源码、不用跟库的 API 较劲
- AI 友好：Cursor / Copilot 训练语料里 shadcn 出现频率极高，AI 写起来熟

替代方案为啥不选：

| 方案 | 不选的理由 |
|---|---|
| MUI / Ant Design / Mantine | 设计风格偏企业级、自带主题系统笨重、bundle 大、改细节难 |
| 完全裸 Radix + 自己写 Tailwind | 工作量太大；shadcn 已经做完了"裸 Radix + Tailwind 粘合"的最佳实践 |
| 自己从 0 写组件 | 重复造轮子 |

### 5.2.8. FastAPI

**(1) 是什么**

FastAPI 是 Sebastián Ramírez 2018 年发布的 **Python 现代 web 框架**，基于 **Starlette**（异步 web 基础库）+ **Pydantic**（基于类型提示的数据校验库）+ Python `async`/`await`。GitHub 80k+ 星，当前 Python 后端增长最快的框架之一。

定位：**API 优先**（注意是 API、不是全栈页面渲染） —— 适合给前端 / SDK / 其他服务提供 HTTP 接口。本项目正是这个用法（包 Python Agent + RAG 核心成 HTTP API 给前端调）。

**(2) 跟其他 Python web 框架的对比**

| 框架 | 定位 | 适合 |
|---|---|---|
| **Django** | 全栈 / batteries-included（电池全自带：ORM、Admin 后台、模板引擎一应俱全） | 内容站、传统 CRUD 应用、需要管理后台 |
| **Flask** | 微框架，自由组合 | 小项目、传统同步 API、教学 |
| **FastAPI** | API 优先、async-first、强类型 | 现代 RESTful API、需要高性能 / OpenAPI 文档 / 强类型 |

FastAPI 为啥火起来，靠三个东西：

1. **基于 Python 类型提示做参数校验和文档**（靠 Pydantic）
2. **原生 async/await 支持**（性能跟 Node.js / Go 不相上下）
3. **自动生成 OpenAPI / Swagger 交互式文档**

**(3) 核心：类型提示驱动一切**

最简单的例子：

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ChatRequest(BaseModel):
    session_id: str
    message: str

@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    return {"reply": f"收到: {req.message}"}
```

就这几行，FastAPI 帮你做了：

- **请求体反序列化**：客户端发 JSON，FastAPI 按 `ChatRequest` 类型解析；字段缺失 / 类型错自动返回 422
- **请求体校验**：`session_id` 必须 str、`message` 必须 str
- **响应序列化**：函数返回 dict，FastAPI 自动 JSON 化
- **自动生成交互文档**：访问 `http://localhost:8000/docs` 看到一个 Swagger UI，能直接在网页上发请求测试
- **TypeScript 类型同步**：基于 OpenAPI 可以一键生成前端 TypeScript 类型定义（避免前后端类型不一致）

跟传统 Flask 对比，校验和错误处理的工作量差距：

```python
# Flask 写法：校验、错误处理全部手写
@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    session_id = data.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return jsonify({"error": "session_id 必须是非空字符串"}), 400
    message = data.get("message")
    if not message or not isinstance(message, str):
        return jsonify({"error": "message 必须是非空字符串"}), 400
    return jsonify({"reply": f"收到: {message}"})
```

FastAPI 把这些都交给类型系统了。

**(4) async / sync 自动适配**

路由函数可以是 sync（普通 `def`）也可以是 async（`async def`），FastAPI 自动适配：

```python
@app.get("/api/sessions")
async def list_sessions():
    return await db.fetch_sessions()  # async：FastAPI 直接 await

@app.get("/api/health")
def health():
    return {"ok": True}  # sync：FastAPI 放进 thread pool 跑、不阻塞事件循环
```

为啥这个对本项目重要：

- **LLM 调用是慢 I/O**（等 OpenAI 响应可能几秒到几十秒）→ async 让单进程能并发处理多个用户请求
- **某些库（比如 `sqlite3` / `chromadb`）不支持 async** → 用 sync 写、FastAPI 自动塞进 thread pool 跑，不用为了 async 改一切

**(5) SSE 流式响应**

§5.1.3 / §5.1.10 都提过聊天 UI 要靠 SSE 实时把 LLM 的 token 推给前端。FastAPI 写 SSE 直接：

```python
from fastapi.responses import StreamingResponse

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    async def generator():
        async for chunk in agent.stream(req.message):
            yield f"data: {chunk}\n\n"  # SSE 协议格式
    return StreamingResponse(generator(), media_type="text/event-stream")
```

浏览器侧只需 `new EventSource('/api/chat/stream')` 就能实时收到 chunk —— 是聊天 UI 流式输出的标准做法。

**(6) 部署：uvicorn**

FastAPI 自己不带 HTTP 服务器，需要 **uvicorn**（一个 ASGI 服务器）跑它：

```bash
pip install fastapi uvicorn
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
# --reload: 开发期热重载，改代码自动重启（类似 §5.1.7 dev server 的 HMR）
```

生产期通常 `uvicorn + gunicorn`（多进程 worker）。本项目单用户，单进程 `uvicorn` 就够。

**(7) 对本项目为什么用 FastAPI**

- **后端核心是 Python**（Agent + RAG 在 `src/agent/` + `src/rag/`），用 Python web 框架最自然 —— 比起来要是用 Node 框架包 Python，得多一层进程交互（Subprocess 或 gRPC），徒增复杂度
- **聊天 UI 主要数据流是 SSE** —— FastAPI 的 `StreamingResponse` 写起来直接
- **Pydantic 强类型 + OpenAPI 文档** —— 哪怕单人开发也享受到 API 改动后前端类型自动同步
- **async-first** 对 LLM 这种慢 I/O 场景天然合适
- 用 Flask 理论上也行，但要自己写 JSON 校验、自己写 SSE、没自动文档；本项目用 FastAPI 不会比 Flask 写得复杂，长期省事

### 5.2.9. Vite

**(1) 是什么**

Vite（法语"快"的意思，发音 /vit/）是 **Evan You**（Vue 框架作者）2020 年出的下一代前端构建工具。"开发服务器 + 生产构建"一体，**只在开发期 + 构建期用，部署后不参与运行**（部署的是它打出的静态产物，不是 Vite 本身）。

现在是 React / Vue / Svelte / Solid 等社区的事实标准 build tool；Vue 官方脚手架、Svelte 官方脚手架、shadcn/ui 的推荐模板都基于 Vite。

**(2) 解决了 Webpack 时代的痛点**

老的 Webpack 全量打包：

- 大项目启动慢（几十秒到几分钟）
- 每次保存代码 HMR 也慢（要算依赖图、增量打包）
- 配置复杂（一个 `webpack.config.js` 动辄几百行）

Vite 做的两件事革命性改进：

| 阶段 | Webpack 做法 | Vite 做法 |
|---|---|---|
| **开发期** | 先把所有源文件打包成几个大 bundle、再让浏览器加载 | **不打包**。浏览器要哪个文件、dev server 现场转译那个文件返回（用浏览器原生 ES Modules） |
| **生产期** | 自己的 bundler 打包 | 用 **Rollup**（业界公认产物质量最高的打包器之一）打包 |

第一点是 Vite 的核心创新 —— 启动时**几乎瞬间**起，且**项目多大都一样快**（因为根本没打包）。

**(3) 开发期工作原理**

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart LR
    BR["浏览器"] -->|"1. 请求 main.tsx"| VITE["Vite dev server"]
    VITE -->|"2. 现场转译成 ES Module 格式的 .js"| BR
    BR -->|"3. 遇到 import './Foo.tsx'，再请求"| VITE
    VITE -->|"4. 按需转译 Foo.tsx"| BR
```

浏览器**请求一个、转译一个** —— 不是先全量打包再给浏览器，所以**项目多大、启动多快没关系**。

`node_modules/` 里的依赖（npm 包，跨项目相对稳定）走一次性预打包：用 **esbuild**（Go 写的、比传统 JS 打包器快 10-100 倍）一次性预打包成 ES Modules、缓存到 `node_modules/.vite/` 下；之后浏览器请求依赖直接从缓存返回。

**(4) HMR（Hot Module Replacement，热模块替换）**

改一行代码 → 保存 → 浏览器**毫秒级**更新；且**保留页面状态**（你已经填了一半的表单、滚到一半的位置、打开的 modal 都不丢）。

工作原理：

- Vite dev server 监听源文件变化
- 检测到改动 → 算出受影响的模块
- 通过 WebSocket 通知浏览器："这几个模块替换一下"
- 浏览器只替换那几个模块、不刷整个页面

跟传统"改完代码手动 F5"对比，HMR 极大提速反复调样式 / 调交互的过程。

**(5) 生产构建**

```bash
npm run build       # 实际跑 vite build，内部用 Rollup
```

输出到 `dist/` 目录：

```
dist/
├── index.html                       # 入口 HTML
├── assets/index-a3b9c2f1.js         # 业务 JS（合并 + 压缩 + tree-shake）
├── assets/index-d8e7f3a5.css        # 业务 CSS（Tailwind 编译产物 + 其他）
└── assets/logo-7c1f2e9b.png         # 图片 / 字体等静态资源
```

文件名带 hash → **浏览器缓存友好**（内容变 hash 变、缓存自动失效；内容不变就一直命中缓存）。

`dist/` 整个目录扔给 Nginx 静态托管即可上线（§5.2.10 详讲）。

**(6) `vite.config.ts` 关键配置**

本项目 Vite 配置文件大概长这样：

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

关键两件事：

- **`plugins: [react()]`** —— 装 React 插件（让 Vite 知道怎么转译 JSX、启用 Fast Refresh）
- **`server.proxy`** —— 开发期把所有 `/api/*` 请求代理到 FastAPI 后端（`http://localhost:8000`）。这就是 §5.1.6 CORS "思路 A：让浏览器看到同源" 在开发期的具体实现 —— 浏览器只跟 `localhost:5173` 一个来源对话，Vite dev server 在后台帮你转发 `/api/*` 到 FastAPI，**完全绕过 CORS**

**(7) 对本项目为什么用 Vite**

- 跟 **React + Tailwind + shadcn/ui** 生态深度集成，社区现成模板（`npx create-vite -- --template react-ts`）
- 启动 / HMR 速度极快，写起来体验好
- 生产构建（Rollup）产物质量高
- 配置简单（一个 10 行左右的 `vite.config.ts` 就能跑起来），跟 Webpack 配置量差一个数量级
- 内置 dev server proxy → 开发期 CORS 一行配置直接解决（§5.1.6）
- 不选 Next.js 内置 build tool 的理由：Next.js 是个全栈框架、绑定 Node 服务端，本项目用不上那些（§5.2.5 已分析）

### 5.2.10. Nginx

**(1) 是什么**

Nginx 是俄罗斯工程师 Igor Sysoev 2004 年发布的**高性能 HTTP 服务器 + 反向代理**。最初为了解决 Apache 在高并发下的性能问题，用**事件驱动 + 异步非阻塞**模型。现在是全球最广用的 web 服务器之一（活跃站点占比超过 30%，跟 Apache 并列前茅）。

发音：**engine-X**。开源 + 有商业版（Nginx Plus）。

**(2) 在本项目里干啥**

3 件事：

1. **静态文件托管** —— 把前端 `dist/` 里的 `.html` / `.js` / `.css` / 图片直接吐给浏览器
2. **反向代理** —— 把 `/api/*` 请求透传到 FastAPI 后端进程（同源化、规避 CORS —— **§5.1.6 思路 A 的生产期实现**）
3. **运维标配** —— 可选的 HTTPS、gzip 压缩、缓存 header、访问日志等

> **Nginx 不是必需的、但是最稳的选择**。本项目单用户私有部署，理论上 FastAPI 自己也能 mount 静态文件（用 `StaticFiles`）—— 但 Nginx 处理静态文件比 Python 进程快好几倍、且抗压能力强，行业惯例是用 Nginx 分担这个职责。

**(3) 跟 Vite dev server 的关系（呼应 §5.1.6 思路 A）**

| 阶段 | 谁负责"同一来源 + 路由分发"角色 |
|---|---|
| **开发期** | Vite dev server（`localhost:5173`）—— 同时承担打包 + HMR + 代理 |
| **生产期** | Nginx —— 静态文件 + `/api/*` 代理到 FastAPI |

两个不同的工具、**同一个角色**：让浏览器看到所有请求都是同源（同一个 scheme + host + port），完全绕过 CORS。

**(4) 典型 `nginx.conf` 配置**

```nginx
server {
    listen 80;
    server_name agenta.local;          # 或 localhost / IP

    # 1. 前端静态文件
    root /var/www/agenta;              # dist/ 解压到这里
    index index.html;

    # SPA 路由兜底：所有未匹配路径都返回 index.html（让 React Router 接管）
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 2. /api/* 转发到 FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # SSE 流式响应必需：禁用缓冲、保持长连接
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

关键 4 点：

- **`root` + `index`** —— 告诉 Nginx 静态文件在哪、默认入口是 `index.html`
- **`try_files ... /index.html`** —— **SPA 路由兜底**：浏览器访问 `/sessions/abc` 这种深链接时 Nginx 找不到该文件、返回 `index.html`、让前端 React Router 接管路由
- **`location /api/ { proxy_pass ... }`** —— `/api/*` 透传到 `localhost:8000`（FastAPI 跑在那）
- **SSE 必须 `proxy_buffering off`** —— 默认 Nginx 会**缓冲**后端响应（攒成大块再发），SSE 流式输出就废了；必须关掉缓冲 + 调大 `proxy_read_timeout` 应对长会话

**(5) HTTPS（可选，本项目暂不需要）**

如果将来需要 HTTPS（公网部署、对接需要 secure context 的浏览器 API 等）：

- 用 **Let's Encrypt** 免费证书（`certbot` 一键申请 + 自动续期）
- Nginx 监听 443 + 配 SSL 证书路径 + 80 端口跳转到 443

本项目纯本地 / 内网部署，**用 HTTP 就够**（`localhost` 浏览器视为 secure context、对应 API 限制都不触发）。

**(6) 替代品**

| 替代 | 跟 Nginx 对比 |
|---|---|
| **Caddy** | 配置文件更简洁、**自动 HTTPS**（带 Let's Encrypt 集成）；社区比 Nginx 小但够用 |
| **Traefik** | 容器原生（Docker 标签自动配路由）；适合微服务 / 多服务场景 |
| **FastAPI 直接 `app.mount(StaticFiles(...))`** | 跳过 Nginx；最简单但静态文件性能差、生产不推荐 |
| **GitHub Pages / Vercel / Netlify** | 静态托管平台、零运维；但**无法**反向代理到你的 FastAPI 后端 —— 适合纯前端 demo |

本项目用 **Nginx**，最稳、网上配置示例最多、AI 也最熟。

**(7) 对本项目的部署架构**

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart LR
    BR["浏览器<br/>http://agenta.local 或 localhost"]
    NGX["Nginx<br/>(80 端口)"]
    DIST["dist/ 静态文件<br/>/var/www/agenta/"]
    FA["FastAPI<br/>(127.0.0.1:8000)"]

    BR <-->|"所有请求都到 Nginx"| NGX
    NGX -->|"/ 和 /assets/*: 读盘返回"| DIST
    NGX <-->|"/api/*: 反向代理"| FA
```

整套部署流程：

1. 开发机跑 `npm run build` 出 `dist/` → 拷到服务器的 Nginx 静态目录（`/var/www/agenta/`）
2. 服务器跑 `uvicorn src.api.main:app --port 8000` 起 FastAPI（用 systemd / supervisord 守护进程）
3. Nginx 配置好两个 `location` 块（静态 + `/api/*` 代理）
4. 浏览器访问域名 → Nginx → 按路径分发到静态文件 / FastAPI

部署细节（systemd 守护、容器化、备份等）放 §6 实现 / 后续运维 iter。



## 5.3. 选型决策

**最终选型**：`Python + TypeScript + FastAPI + Vite + React + Tailwind CSS + shadcn/ui + Nginx`

**技术栈分层职责**：

| 层 | 工具 / 技术 | 负责干啥 | 详见 |
|---|---|---|---|
| **前端语言** | TypeScript | 前端源码语言（JS + 类型系统） | §5.2.2 |
| **前端 UI 库** | React | 声明式 UI 渲染 + 组件化 | §5.2.4 |
| **前端样式** | Tailwind CSS | utility-first CSS 工具类 | §5.2.6 |
| **前端组件库** | shadcn/ui | 复制源码的 React 组件套装（基于 Radix UI + Tailwind） | §5.2.7 |
| **前端路由** | React Router | 浏览器内多页面切换（聊天主页 / session 详情等） | §5.2.4 (5) |
| **前端工程化运行时** | Node.js | 跑构建工具 / dev server 的 JS 运行时 | §5.2.3 |
| **前端包管理** | npm | 装依赖、跑 scripts | §5.2.3 |
| **前端构建 / dev server** | Vite | 开发期现场转译 + HMR、构建期用 Rollup 打包 | §5.2.9 |
| **后端语言** | Python | 后端源码语言（项目已有 Agent + RAG 核心） | — |
| **后端 web 框架** | FastAPI | HTTP API 路由 + Pydantic 校验 + SSE 流式响应 + OpenAPI 文档 | §5.2.8 |
| **后端 HTTP 服务器** | uvicorn | ASGI 服务器，跑 FastAPI；开发期 `--reload` 热重载 | §5.2.8 (6) |
| **通信协议** | HTTP REST + SSE | 常规请求走 REST、聊天流式响应走 SSE | §5.1.3 |
| **生产部署：反向代理 + 静态托管** | Nginx | 静态文件托管 + `/api/*` 反向代理到 FastAPI | §5.2.10 |
| **生产部署：进程守护** | systemd | 让 uvicorn 24/7 跑、崩了自动重启 | §6 |

**项目已有（不变）**：

- `src/agent/` —— Python Agent core（含三种实现：Python / LangChain / AutoGPT）
- `src/rag/` —— RAG 检索 + 重排
- `src/memory/` —— 会话历史 + memory + 复习计划存储

本期新加的就是上面表格里**所有非 Python 的工具** + 一层 FastAPI（在新目录 `src/api/` 里写）把上面三个 Python 模块包成 HTTP 接口。

**整体开发流程**：

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'edgeLabelBackground': '#fffbe6'}}}%%
flowchart TB
    subgraph Dev["① 开发期（开发机本地跑）"]
        direction LR
        FE_SRC["前端源码<br/>TS + React + Tailwind + shadcn/ui"]
        BE_SRC["后端源码<br/>FastAPI + Pydantic"]
        VITE_DEV["Vite dev server<br/>(:5173)<br/>npm run dev"]
        UV_DEV["FastAPI<br/>(:8000)<br/>uvicorn --reload"]
        FE_SRC --> VITE_DEV
        BE_SRC --> UV_DEV
        VITE_DEV <-.->|"/api/* 走 Vite proxy"| UV_DEV
    end

    subgraph Build["② 构建期（开发机一次性跑）"]
        VITE_BUILD["Vite build<br/>npm run build"]
        DIST_OUT["dist/<br/>.html / .js / .css / 静态资源"]
        VITE_BUILD --> DIST_OUT
    end

    subgraph Prod["③ 生产期（部署到服务器）"]
        direction LR
        NGX_PROD["Nginx (:80)<br/>静态托管 + 反代"]
        UV_PROD["FastAPI (:8000)<br/>uvicorn + systemd 守护"]
        NGX_PROD <-->|"/api/*"| UV_PROD
    end

    FE_SRC -->|"npm run build"| VITE_BUILD
    DIST_OUT -->|"拷贝 dist/ 到 Nginx 目录"| NGX_PROD
    BE_SRC -.->|"部署 + systemd 守护"| UV_PROD
```

读图要点：

- **① 开发期**：前端写 `.tsx` 源码 → Vite dev server 实时编译 + HMR；后端写 FastAPI 路由 → uvicorn 热重载。两边都跑在 localhost，浏览器只跟 `:5173` 一个来源对话，`/api/*` 由 Vite proxy 透传到 `:8000`（同源化、避 CORS —— §5.1.6 思路 A 开发期实现）
- **② 构建期**：前端 `npm run build` 一次性产出 `dist/` 静态产物
- **③ 生产期**：`dist/` 拷到服务器 Nginx 静态目录；FastAPI 用 uvicorn + systemd 守护跑在 `:8000`；Nginx 配两个 location，`/` 给静态文件、`/api/*` 反代到 FastAPI（同源化、避 CORS —— §5.1.6 思路 A 生产期实现）


# 6. 实现
## 6.1 环境准备

我用的是公司电脑，没有 admin 权限。前面实现 MCP 时已经装过 Node.js，过程详见 [`knowlege.md §8.12`](./knowlege.md) 和 [`iter_2_agent.md` 附录"环境摸底"](./iter_2_agent.md)。

**Node.js 侧现状**（2026-05-31 实证，继承 MCP 实施期）：

| 项 | 状态 | 备注 |
|---|---|---|
| Node.js v22.22.3 | ✅ | portable zip 装在 `C:\DiskD\sourceCode\node-v22.22.3-win-x64\`，已加用户 PATH（无 admin） |
| npm 10.9.8 / npx 10.9.8 | ✅ | `where.exe node` 优先解析到上述路径；Cursor IDE 自带的 helper `node.exe` 排第二位 |
| `registry.npmjs.org` 公司网络可达 | ✅ | `npx -y` 正常工作（MCP 阶段已验证 `cowsay` / `server-filesystem`） |

本期 Node 侧**无需重装**。

**Python 侧**（在现有 venv 装，全部用户级、无 admin 影响）：

| 包 | 作用 | 备注 |
|---|---|---|
| `fastapi` | Web 框架（详 §5.2.8） | 核心包 |
| `uvicorn[standard]` | **ASGI 服务器** —— 在端口监听 HTTP 请求、把请求 dispatch 给 FastAPI 应用对象、把响应写回 socket。跟 FastAPI 是"服务器 + 框架"的关系（类比 Flask + gunicorn） | `[standard]` 是 pip 的 extras 语法，多装一组可选加速 / 便利包：`httptools`（C 实现的 HTTP 解析器）/ `uvloop`（高性能 asyncio 事件循环，Linux/Mac 生效、Windows 自动跳过）/ `websockets` / `watchfiles`（`--reload` 监听文件变化）/ `python-dotenv`。不带 `[standard]` 也能跑，性能略降；装 `[standard]` 是社区惯例 |
| `python-multipart` | 解析 HTTP `multipart/form-data` 请求体（浏览器表单 + 文件上传协议格式） | 纯 Python、单一职责库。FastAPI 核心不内置 multipart 解析；只要写 `File(...)` / `Form(...)` 接收文件就必须装它，否则启动报 `Form data requires "python-multipart" to be installed`。本期 [Step 4 知识库拖拽上传](#645-step-4---知识库--拖拽入库) 必用，一次装上免得到时漏 |

`requirements.txt` 加 3 行：

```diff
+ # Web API (iter 4)
+ fastapi
+ uvicorn[standard]
+ python-multipart
```

一次性装：

```bash
pip install fastapi uvicorn[standard] python-multipart
```

**前端依赖怎么走？**

前端依赖不在 `requirements.txt`（那是 Python 那套），而在 `frontend/package.json`。这个文件**当前还不存在**，会在 [Step 0](#641-step-0---项目骨架) 通过脚手架命令自动生成 / 增量写入：

| 命令 | 自动加进 `package.json` 的典型依赖 |
|---|---|
| `npm create vite@latest frontend -- --template react-ts` | `react` / `react-dom` / `typescript` / `vite` / `@vitejs/plugin-react` |
| `npm install -D tailwindcss postcss autoprefixer` | `tailwindcss` / `postcss` / `autoprefixer` |
| `npx shadcn@latest init`（含后续 `npx shadcn@latest add <component>`） | `@radix-ui/react-*` / `class-variance-authority` / `clsx` / `tailwind-merge` / `lucide-react` |

也就是说：**前端依赖列表不需要在这里预先列**，跑完 Step 0 自然到位。本节只需关心 Python 侧。

**编辑器扩展**（Cursor / VS Code 插件市场装，可选但推荐）：

- **Tailwind CSS IntelliSense** —— class 名补全 + 悬停看对应 CSS 值（§5.2.6 (6)）
- **ESLint + Prettier** —— 前端代码风格 / 静态检查

**端口确认**：

```powershell
netstat -ano | findstr ":5173 :8000"
```

5173（Vite dev）+ 8000（FastAPI），启动前确认两个端口空闲。


## 6.2 目录/文件结构

```
AgentA/                              # 项目根
├── src/                             # 后端 Python
│   ├── agent/                       # 已有，不动
│   ├── rag/                         # 已有，不动
│   ├── memory/                      # 已有，不动
│   ├── llm/                         # 已有，不动
│   ├── cli/                         # 已有，不动
│   ├── config.py                    # 已有，按需加 API 相关配置项
│   └── api/                         # 【本期新增】FastAPI 层
│       ├── __init__.py
│       ├── main.py                  # FastAPI app + 路由挂载入口
│       ├── deps.py                  # 依赖注入：单例 Agent / Store / ...
│       ├── routes/                  # 各业务路由
│       │   ├── __init__.py
│       │   ├── health.py            # /api/health
│       │   ├── chat.py              # /api/chat/*
│       │   ├── sessions.py          # /api/sessions/*
│       │   ├── kb.py                # /api/kb/*
│       │   ├── memory.py            # /api/memory/*
│       │   ├── rules.py             # /api/rules/*
│       │   ├── skills.py            # /api/skills/*
│       │   ├── mcp.py               # /api/mcp/*
│       │   ├── config.py            # /api/config/*
│       │   └── learning.py          # /api/learning/*（业务面板）
│       └── schemas/                 # Pydantic 请求 / 响应模型
│           ├── __init__.py
│           ├── chat.py
│           ├── session.py
│           └── ...
│
├── frontend/                        # 【本期新增】前端项目（独立目录）
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts               # 含 /api/* proxy 到 :8000
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── components.json              # shadcn/ui 配置
│   ├── index.html                   # 入口 HTML
│   └── src/
│       ├── main.tsx                 # React 挂载入口
│       ├── App.tsx                  # 根组件 + 路由
│       ├── components/
│       │   ├── ui/                  # shadcn/ui 组件（CLI 复制进来）
│       │   ├── chat/                # 聊天主区（MessageList / Composer / ...）
│       │   ├── sidebar/             # 左侧栏（Recents / 资源菜单 / ...）
│       │   └── panel/               # 右侧 detail panel
│       ├── pages/                   # 顶级页面（按 React Router 组织）
│       ├── hooks/                   # 自定义 hooks（useChatStream / useSession / ...）
│       ├── lib/                     # 通用工具（cn / 时间格式化 / ...）
│       ├── api/                     # 后端 API 客户端封装（fetch / SSE）
│       ├── store/                   # 全局状态（Zustand）
│       └── types/                   # TS 类型（部分由后端 OpenAPI 自动生成）
│
├── tests/
│   ├── test_agent_*.py              # 已有
│   ├── test_rag_*.py                # 已有
│   └── test_api_*.py                # 【本期新增】API 层 UT（用 FastAPI TestClient）
│
├── docs/
│   ├── design.md                    # 本 iter 完成后回写 "§N Web UI + API 层" 章节
│   └── iter_4_UI.md                 # 本文档
│
├── tools/
│   └── dev.ps1                      # 【可选】一键起前后端两进程
│
└── requirements.txt                 # 加 fastapi / uvicorn / python-multipart
```

**设计约定**：

- **前端独立 `frontend/`**，跟 `src/` 平行 —— 前后端依赖 / 构建 / 锁定文件互不影响
- **后端 API 层 `src/api/`，不动现有 `src/agent/` / `src/rag/`** —— API 通过 `deps.py` 拿核心模块实例（依赖注入），保持 Agent core 跟 HTTP 解耦
- **路由按业务功能拆文件**，每个文件一个 `APIRouter`，`main.py` 统一挂载 —— 避免单文件膨胀
- **Pydantic schema 单独放 `schemas/`** —— 跟路由解耦，方便复用 + 生成 OpenAPI

## 6.3 `design.md` 同步策略

**本 iter 期间**：所有 UI + API 设计写在 `iter_4_UI.md`（§6 本节就够），**不动 `docs/design.md`**。

**本 iter 完成后**：往 `docs/design.md` 加一节 **"§N. Web UI + API 层"**，写：

- 新增目录结构（`src/api/` + `frontend/`）的角色 / 边界
- API 路由总览（端点列表 + 各自职责）
- 前后端边界（什么放前端、什么放后端）
- SSE / CORS / 部署（开发 vs 生产）

**理由**：`docs/design.md` 是"**当前态架构**"文档（工程公约 §3）。本 iter 进行中代码不稳定、写进去容易过时；做完一次回写、跟最新代码对齐。这也跟之前定的"README / design 等新 UI 好了再更新"对齐。

## 6.4 分步实现

按"**先架子、后填肉**"拆 7 个 Step。每个 Step 一个里程碑、独立可验收。

| Step | 主题 | 里程碑（看得到的效果） |
|---|---|---|
| **Step 0** | 项目骨架 | 后端 `/api/health` + 前端空页 + Vite proxy 跑通 |
| **Step 1** | 最小聊天回路（非流式） | 输入框发消息、Agent 返回完整答案（一次性） |
| **Step 2** | 流式输出 + Agent 状态 | SSE 流式打字 + Thinking / Plan / Tool 折叠块 |
| **Step 3** | Session 管理 | 左侧栏 Recents、新建 / 切换 / 改名 / 删除、刷新不丢 |
| **Step 4** | 知识库 + 拖拽入库 | 拖文件 → 进度 → 入库；文档列表 / 删除 |
| **Step 5** | 其他资源管理 | rules / memory / skills / mcp 4 类 CRUD |
| **Step 6** | 系统配置 + 主题 + 反馈 + 调试 | LLM 参数面板 + 暗色模式 + toast / error / loading + 日志查看 |
| **Step 7** | 业务面板 | 学习计划 / Quiz / SRS |

每个 Step 用同一份模板：① 目标 ② 实现内容 ③ 修改 / 新增列表（表格） ④ UT 策略 ⑤ 人工验收步骤。

---

### 6.4.1 Step 0 - 项目骨架

**目标**：把架子搭起来 —— 前端能调到后端 `/api/health`，开发期"改代码 → 浏览器自动更新"的回路跑通。

**实现内容**：

- 后端：建 `src/api/main.py` + `routes/health.py`，跑起 `uvicorn`
- 前端：`npm create vite@latest frontend -- --template react-ts` 初始化
- 前端：装 Tailwind CSS + 配 PostCSS
- 前端：装 shadcn/ui（`npx shadcn@latest init`）
- 前端：`vite.config.ts` 配 `/api/*` proxy → `:8000`
- 前端：`App.tsx` 调一次 `/api/health`、把结果显示在页面（验证管线通了）

**修改 / 新增列表**：

| 操作 | 文件 / 目录 | 说明 |
|---|---|---|
| 新增 | `src/api/__init__.py` | API 包标识 |
| 新增 | `src/api/main.py` | `app = FastAPI(...)` + 挂 `health_router` + CORS 中间件（dev 期允许 `:5173`） |
| 新增 | `src/api/routes/__init__.py` | routes 包标识 |
| 新增 | `src/api/routes/health.py` | `GET /api/health` → `{"ok": true, "version": "..."}` |
| 修改 | `requirements.txt` | 加 `fastapi` / `uvicorn[standard]` / `python-multipart`（详 §6.1） |
| 新增 | `frontend/` 整个目录 | `npm create vite@latest` 生成（react-ts 模板） |
| 新增 | `frontend/tailwind.config.ts` + `postcss.config.js` + `src/index.css` | Tailwind 初始化 |
| 新增 | `frontend/components.json` + `src/lib/utils.ts` | `npx shadcn@latest init` 生成 |
| 修改 | `frontend/vite.config.ts` | 加 `server.proxy` 配 `/api` → `http://localhost:8000` |
| 修改 | `frontend/src/App.tsx` | `useEffect` 里调 `/api/health`、显示 `API health: OK ✓` 或错误 |
| 修改 | `.gitignore` | 加 `frontend/node_modules/` / `frontend/dist/` / `frontend/.vite/` |
| 新增 | `tools/dev.ps1`（可选） | 一键起前后端两进程的 PowerShell 脚本 |

**UT 策略**：

- 后端：`tests/test_api_health.py` —— 用 `fastapi.testclient.TestClient` 测 `GET /api/health` 返回 200 + `{"ok": true}`
- 前端：本 Step 不写 UT（前端 UT 整个 iter 都不上）
- `pytest -q` 默认 fast set 不挂

**人工验收步骤**：

1. 终端 1：`.\.venv\Scripts\python -m uvicorn src.api.main:app --reload --reload-dir src --port 8000` —— 看到 `Will watch for changes in these directories: ['...\src']` + `Uvicorn running on http://127.0.0.1:8000`。`--reload-dir src` 限制只盯 Python 源码（不加的话默认 watch 项目根，改前端 / 文档也会误触发后端重启）
2. 浏览器开 `http://localhost:8000/docs` —— 看到 Swagger UI，有 `GET /api/health` 端点；点 `Try it out` → `Execute`，返回 200 + `{"ok": true, "version": "..."}`
3. 终端 2：`cd frontend && npm install`（首次）→ `npm run dev` —— 看到 `Local: http://localhost:5173/`
4. 浏览器开 `http://localhost:5173` —— 页面显示 `API health: OK ✓`（或类似字样）
5. F12 → Network → 刷新页面，能看到一条请求 `GET http://localhost:5173/api/health` 实际响应来自 `:8000`（说明 Vite proxy 工作）
6. 改一行 `App.tsx` 里的文字（比如把 `OK ✓` 改成 `OK 🎉`）、保存 —— 浏览器**毫秒级**显示新文字、不刷整页（HMR 工作）

通过以上 6 条 = Step 0 完成。

---

### 6.4.2 Step 1 - 最小聊天回路（非流式）

**目标**：建立"前端发消息 → 后端跑 Agent → 返回完整答案 → 前端显示"的最小闭环。一次性返回（**等几秒看到答案、不打字效果**），但**多轮对话在内存中有记忆**（同一进程不重启时）。

**本 Step 不做**：

| 项 | 留给 |
|---|---|
| 流式打字 / SSE | Step 2 |
| Thinking / Plan / Tool 可视化 | Step 2 |
| Session 列表 / 切换 / 持久化 | Step 3 |
| 错误就近显示 / toast | Step 6 |
| 暗色模式 / 设置 / 引用渲染 | Step 6 / Step 5 |

**对接现有代码的策略**：

- 复用 [`AgentAPI` Protocol](../src/agent/agent_api.py)（表现层 ↔ Agent core 的对外契约）—— API 层只依赖此契约，不绑定具体实现（Python / LangChain / AutoGPT）
- **API 层 Agent 用单例**（FastAPI app 启动时建一个、跨请求复用）—— 一个浏览器开发期内对话有记忆；服务器重启 / 进程换就丢
- 单例 Agent 用**最朴素的默认值**（`Agent(verbose=False)`），不加载 skills / rules / prompt 文件
- Step 5（其他资源管理）再统一抽出 composition root 跟 CLI 共享配置

**实现内容**：

后端：

- `src/api/deps.py` —— 单例 Agent 工厂（`get_agent()`）
- `src/api/schemas/chat.py` —— `ChatRequest` / `ChatResponse` Pydantic 模型
- `src/api/routes/chat.py` —— `POST /api/chat` 端点
- `src/api/main.py` —— 挂载 chat router

前端：

- `npx shadcn@latest add input textarea scroll-area` —— 装 3 个组件
- `src/types/chat.ts` —— `Message` / `ChatRequest` / `ChatResponse` TypeScript 类型
- `src/api/client.ts` —— 后端 API 客户端封装（基于 `fetch`）
- `src/components/chat/MessageList.tsx` —— 消息列表（user / assistant 区分气泡）
- `src/components/chat/Composer.tsx` —— 输入框 + 发送按钮（`Textarea` + `Button`，Cmd/Ctrl+Enter 发送）
- `src/App.tsx` —— 改成聊天主界面（用上面两个组件 + 自管 `messages` state）

**修改 / 新增列表**：

| 操作 | 文件 | 说明 |
|---|---|---|
| 新增 | `src/api/deps.py` | `get_agent()` 单例工厂；`@lru_cache` 或模块级变量都行 |
| 新增 | `src/api/schemas/__init__.py` | 包标识 |
| 新增 | `src/api/schemas/chat.py` | `ChatRequest(message: str)` / `ChatResponse(reply: str, session_id: str)` |
| 新增 | `src/api/routes/chat.py` | `POST /api/chat`，`Depends(get_agent)` 注入，`reply = agent.run(req.message)` |
| 修改 | `src/api/main.py` | `include_router(chat.router, prefix="/api", tags=["chat"])` |
| 新增 | `tests/test_api_chat.py` | mock `Agent.run`，测路由 200 / 422 / 异常兜底 |
| 新增 | `frontend/src/types/chat.ts` | TS 类型（跟后端 Pydantic 对齐） |
| 新增 | `frontend/src/api/client.ts` | `postChat(message): Promise<ChatResponse>` |
| 新增 | `frontend/src/components/chat/MessageList.tsx` | `<ul>` 渲染 messages；user 右对齐、assistant 左对齐；shadcn 颜色 tokens |
| 新增 | `frontend/src/components/chat/Composer.tsx` | shadcn `<Textarea>` + `<Button>`；`Enter` 发送、`Shift+Enter` 换行；`loading` 时禁用 |
| 新增 | `frontend/src/components/ui/{input,textarea,scroll-area}.tsx` | `npx shadcn add` 自动生成 |
| 修改 | `frontend/src/App.tsx` | 改成聊天界面：管 `messages: Message[]` 和 `loading` state；调 `postChat` 把 user message 推进 messages、等响应后 push assistant message |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| 后端 | `tests/test_api_chat.py`：用 `monkeypatch` mock `Agent.run` 返回固定字符串，`TestClient.post("/api/chat", json={"message":"hi"})` 断言 200 + reply 字段；缺字段返 422；Agent.run 抛异常时返 500（或 fallback 字符串 —— 按 `AgentAPI` 契约 "失败时返回 'Error: <msg>' 而非抛异常"，理论上 Agent.run 不该抛，但要兜底） |
| 前端 | 不写 UT（前端 UT 整个 iter 都不上） |

**人工验收步骤**：

1. 后端 + 前端两个进程都在跑（沿用 Step 0 的命令）
2. 浏览器开 `http://localhost:5173/` —— 看到聊天界面：上方消息区（空）、下方输入框 + 发送按钮
3. 输入 `hello`，回车发送 —— 自己的消息立刻出现在右侧（user 气泡）；输入框被禁用、显示 "thinking…" 提示
4. **等几秒**（LLM 调用同步、非流式）—— assistant 回复整段出现在左侧（assistant 气泡）；输入框恢复可用
5. **测多轮记忆**：再输 `我刚才说了什么？` —— agent 应该能答出 "你刚才说了 hello"（说明 chat_history 在内存里被复用）
6. **F12 → Network**：找到一条 `POST /api/chat`，状态 200，Request Body `{"message":"..."}`，Response Body `{"reply":"...","session_id":"..."}`
7. **测异常**：把后端 uvicorn `Ctrl+C` 杀掉，再发一条消息 —— 应该看到红字 "ERROR" 或类似（Step 6 才做精细错误展示，本 Step 红字 / 错误码即可）

通过以上 7 条 = Step 1 完成。

**风险点 / 已知限制**（不影响本 Step 验收）：

| 项 | 说明 |
|---|---|
| LLM 调用很慢时浏览器看起来"假死" | 同步等待，本 Step 接受；Step 2 上 SSE 解决 |
| `Agent.run` 是同步 + IO bound | FastAPI 会自动把同步路由扔到 thread pool 跑、不阻塞 event loop —— 写 `def chat()`（不带 `async`）即可 |
| 服务器重启 = chat 历史全丢 | 本 Step 接受；Step 3 上 session 持久化 |
| `system_prompt` / skills / rules 都是默认值 | 体感比 CLI 简陋（agent "傻"一些），本 Step 接受；Step 5 抽 composition root 跟 CLI 一致 |

---

### 6.4.3 Step 2 - 流式输出 + Agent 状态

> 留待 Step 1 完成后展开。

### 6.4.4 Step 3 - Session 管理

> 留待 Step 2 完成后展开。

### 6.4.5 Step 4 - 知识库 + 拖拽入库

> 留待 Step 3 完成后展开。

### 6.4.6 Step 5 - 其他资源管理

> 留待 Step 4 完成后展开。

### 6.4.7 Step 6 - 系统配置 + 主题 + 反馈 + 调试

> 留待 Step 5 完成后展开。

### 6.4.8 Step 7 - 业务面板
> 留待 Step 6 完成后展开。
