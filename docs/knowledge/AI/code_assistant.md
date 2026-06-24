# 1. CodeGraph

面向 AI 编程 agent 的本地代码知识图谱工具。本文介绍它是什么、怎么装、怎么用，照着操作即可在 AgentA 仓库里接入。

> 适用环境：Windows + PowerShell + Cursor（本仓库当前开发环境）。

## 1.1. 这是个什么工具

CodeGraph 是一个**本地运行的 MCP（Model Context Protocol）服务**。它提前把整个代码库解析成一张"知识图谱"——函数、类、调用关系、import、继承等都存进本地 SQLite 数据库（带全文搜索）。

接入后，Cursor 里的 AI agent 理解代码时，不再靠反复 grep、读文件去摸索，而是**直接查这张图**，一次调用就能拿到结构化的答案。

带来的好处：

| 好处 | 说明 |
|---|---|
| 更省钱、更快 | 官方在 7 个真实项目实测：平均成本省 16%、token 少 47%、工具调用少 58% |
| 100% 本地 | 数据不出本机，无需 API key，只用 SQLite |
| 索引不过期 | 监听文件改动，约 2 秒后自动增量更新，不用手动维护 |
| 零配置 | 没有配置文件，自动跳过 `node_modules` / `.venv` / `dist` 和 `.gitignore` 内容 |
| 多语言 | 支持 20+ 语言（含 Python、TypeScript、JavaScript），并能识别常见 Web 框架路由 |

> 本仓库是 Python 后端 + React/TS 前端，全部落在 CodeGraph 的支持范围内。

## 1.2. 安装步骤

### 1.2.1. 装 CodeGraph 命令行工具

打开 PowerShell，二选一：

方式 A（推荐，自带运行时，不依赖 Node）：

```powershell
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex
```

方式 B（已装 Node，任意版本都行）：

```powershell
npm i -g @colbymchenry/codegraph
```

> 安装脚本会把 `codegraph` 加到 PATH，但**不会改当前这个终端**。装完请**关掉再开一个新的 PowerShell 窗口**，否则下一步找不到命令。

验证：

```powershell
codegraph version
```

### 1.2.2. 把 CodeGraph 接到 Cursor

在**新终端**里运行安装器，它会把 CodeGraph 的 MCP 服务写进 Cursor 的配置：

```powershell
codegraph install
```

交互过程中会问三件事：

- 配置哪些 agent —— 选 `Cursor`（也可同时勾选别的）
- 是否把 `codegraph` 装到 PATH —— 选是
- 作用于所有项目还是仅当前项目 —— 想全局复用选 global，只给本仓库用选 local

想跳过交互、只配 Cursor，可直接：

```powershell
codegraph install --target=cursor --yes
```

### 1.2.3. 在 AgentA 仓库里建索引

```powershell
cd C:\DiskD\sourceCode\mygithub\AgentA
codegraph init
```

`init` 会创建本地 `.codegraph\` 目录并**同时构建完整图谱**。本仓库规模大约几十秒到一两分钟。在根目录建一次即可，后端 Python 和前端 TS/React 会一起索引，无需分开。

查状态确认：

```powershell
codegraph status
```

### 1.2.4. 重启 Cursor

完全退出 Cursor 再打开，让它加载新的 MCP 配置。之后只要项目里存在 `.codegraph\` 目录，agent 就会自动启用 CodeGraph。

## 1.3. 怎么用

### 1.3.1. 日常使用（主要场景）

装好后**基本无感**：在 Cursor 里正常向 agent 提问，agent 会自动用 CodeGraph 的工具替代大量 grep 和读文件。比如问"登录请求是怎么走到数据库的""改这个函数会影响哪些地方"，它会直接查图谱给答案。

索引也**无需手动维护**：你或 agent 改了文件，CodeGraph 监听到后约 2 秒自动增量更新。

### 1.3.2. 命令行工具（可选，手动排查时用）

agent 用的能力，命令行也能直接调，方便自己确认：

| 命令 | 作用 |
|---|---|
| `codegraph explore <问题>` | 主力命令。一次返回相关符号的源码 + 调用关系 + 影响范围 |
| `codegraph node <符号或文件>` | 看单个符号的完整源码 + 调用链，或按文件读取 |
| `codegraph query <关键词>` | 按名字搜索符号 |
| `codegraph callers <符号>` | 找一个函数的所有调用点 |
| `codegraph impact <符号>` | 分析改动一个符号会波及哪些代码 |
| `codegraph status` | 查看索引统计、是否有待同步文件 |
| `codegraph sync` | 手动增量更新（一般不用，自动同步已覆盖） |

### 1.3.3. 常用维护命令

| 命令 | 作用 |
|---|---|
| `codegraph upgrade` | 升级到最新版（加 `--check` 只看有没有更新） |
| `codegraph uninit` | 移除本项目的 `.codegraph\` 索引 |
| `codegraph uninstall` | 从所有 agent 配置中卸载 CodeGraph（不删项目索引） |

## 1.4. 注意事项

- **`.codegraph\` 不要提交 git**：这是本地索引，建议加进 `.gitignore`。
- **自动忽略范围**：默认跳过 `node_modules` / `.venv` / `dist` / `build`、`.gitignore` 里的内容，以及大于 1MB 的文件——第三方代码和虚拟环境不会被索引进来。
- **索引慢或缺符号**：先确认大目录已被排除；改完文件等一两秒让它自动同步，必要时手动 `codegraph sync`。

## 1.5. 参考链接

- 项目仓库：<https://github.com/colbymchenry/codegraph>
- 官方文档：<https://colbymchenry.github.io/codegraph/>

# 2. Cursor 规则（.cursor/rules/*.mdc）加载机制

`.cursor/rules/` 下的每个 `.mdc` 是**独立加载**的——Cursor 不会把它们合并，而是根据**每个文件自己的 frontmatter**（开头 `---` 之间那段）决定何时把它放进 AI 的上下文。

## 2.1. 四种加载方式

由 frontmatter 里的 `alwaysApply`、`globs`、`description` 三个字段组合决定：

先看总览，再逐一说明：

| 类型 | frontmatter 配置 | 何时加载 |
|---|---|---|
| Always（总是） | `alwaysApply: true` | 每次对话都自动注入，无条件 |
| Auto Attached（自动挂载） | 设了 `globs`（如 `globs: src/**/*.py`） | 当对话涉及匹配该 glob 的文件时才注入 |
| Agent Requested（按需） | 设了 `description`，`alwaysApply: false`，无 `globs` | 由 AI 根据 `description` 自己判断要不要拉进来 |
| Manual（手动） | 三者都不设 | 只有在对话里用 `@规则名` 显式引用时才加载 |

> "类型"列只是 Cursor 根据字段组合自动归出的类别名，**不用写进文件**。你只填 `alwaysApply` / `globs` / `description` 这几个字段，字段名区分大小写，`alwaysApply` 是驼峰、值只有 `true` / `false`。

### Always（总是）

每次对话都无条件加载。适合放**永远要遵守**的硬规则（工程公约、安全红线）。代价是常驻上下文，所以要写短。

```
---
description: AgentA 工程公约
alwaysApply: true
---
所有代码注释用中文，只解释"为什么"不解释"做了什么"。
```

### Auto Attached（自动挂载）

只有当本轮对话**涉及匹配 `globs` 的文件**时才注入，平时不占上下文。适合"某类文件专属"的规则。比如只在改前端组件时才提醒的规范：

```
---
description: 前端组件规范
globs: frontend/src/components/**/*.tsx
alwaysApply: false
---
组件用函数式写法，props 用 TypeScript 接口显式声明。
```

> 当你打开 / 编辑 / 在对话里引用 `frontend/src/components/Foo.tsx` 时，这条才会被挂上。

### Agent Requested（按需）

规则**摆在那里但不自动加载**，由 AI 读 `description` 自己判断"这次任务用不用得上"再决定拉不拉进来。所以 `description` 要写清楚"什么时候该用我"。适合不常用、但特定任务很关键的规则：

```
---
description: 写数据库迁移脚本时遵守的命名与回滚规范
alwaysApply: false
---
迁移文件命名 V{序号}__{描述}.sql；每个迁移必须配套 down 脚本。
```

> 当你说"帮我加一个数据库迁移"时，AI 看到 `description` 觉得相关，就会自己加载这条；问别的则不加载。

### Manual（手动）

三个字段都不设，**只有你在对话里用 `@规则名` 显式点名**才加载。适合偶尔手动调用的模板 / 检查清单：

```
---
---
发版前检查清单：1) 跑全量测试 2) 更新 CHANGELOG 3) 打 tag。
```

> 平时完全不出现，你在对话里输入 `@release-checklist` 才会被拉进来。



## 2.2. 要点

- **各自独立**：每个 `.mdc` 按自己的 frontmatter 走，不存在"主文件 include 子文件"。
- **生效时机**：规则在**对话开始时**确定。新建或改了 `.mdc`，要**开新对话**才生效。
- **嵌套目录**：子目录里也能放 `.cursor/rules/`（如 `frontend/.cursor/rules/`），给特定子项目加局部规则。
- **控制体积**：Always 规则一直占上下文，要写得短；细节性内容更适合用 `globs` 或 `description` 按需加载。