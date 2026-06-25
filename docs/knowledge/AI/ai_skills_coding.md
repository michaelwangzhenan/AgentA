# 1. CodeGraph

## 1.1. 这是个什么工具

CodeGraph 是一个**本地运行的 MCP（Model Context Protocol）服务**。它提前把整个代码库解析成一张"知识图谱"——函数、类、调用关系、import、继承等都存进本地 SQLite 数据库（带全文搜索）。

接入后，Cursor 里的 AI agent 理解代码时，不再靠反复 grep、读文件去摸索，而是**直接查这张图**，一次调用就能拿到结构化的答案。

带来的好处：


| 好处      | 说明                                                              |
| ------- | --------------------------------------------------------------- |
| 更省钱、更快  | 官方在 7 个真实项目实测：平均成本省 16%、token 少 47%、工具调用少 58%                   |
| 100% 本地 | 数据不出本机，无需 API key，只用 SQLite                                     |
| 索引不过期   | 监听文件改动，约 2 秒后自动增量更新，不用手动维护                                      |
| 零配置     | 没有配置文件，自动跳过 `node_modules` / `.venv` / `dist` 和 `.gitignore` 内容 |
| 多语言     | 支持 20+ 语言（含 Python、TypeScript、JavaScript），并能识别常见 Web 框架路由       |


## 1.2. 安装步骤

### 1.2.1. 安装 CodeGraph

打开 PowerShell，二选一：

方式 A：

```powershell
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex
```

方式 B（已装 Node）：

```powershell
npm i -g @colbymchenry/codegraph
```

> 安装脚本会把 `codegraph` 加到 PATH，但**不会改当前这个终端**。装完请**关掉再开一个新的 PowerShell 窗口**，否则下一步找不到命令。

验证：

```powershell
codegraph version
```

### 1.2.2. 接入 Cursor

在**新终端**里运行安装器，它会把 CodeGraph 的 MCP 服务写进 Cursor 的配置：

```powershell
codegraph install
```

交互过程中会问三件事：

- 配置哪些 agent —— 时勾需要的 agent
- 是否把 `codegraph` 装到 PATH —— 选是
- 作用于所有项目还是仅当前项目 —— 想全局复用选 global，只给本仓库用选 local

想跳过交互、只配 Cursor，可直接：

```powershell
codegraph install --target=cursor --yes
```

### 1.2.3. 创建索引

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

### 1.3.1. 日常使用

装好后**基本无感**：在 Cursor 里正常向 agent 提问，agent 会自动用 CodeGraph 的工具替代大量 grep 和读文件。比如问"登录请求是怎么走到数据库的""改这个函数会影响哪些地方"，它会直接查图谱给答案。

索引也**无需手动维护**：你或 gent 改了文件，CodeGraph 监听到后约 2 秒自动增量更新。

### 1.3.2. 命令行工具

agent 用的能力，命令行也能直接调，方便自己确认：


| 命令                       | 作用                             |
| ------------------------ | ------------------------------ |
| `codegraph explore <问题>` | 主力命令。一次返回相关符号的源码 + 调用关系 + 影响范围 |
| `codegraph node <符号或文件>` | 看单个符号的完整源码 + 调用链，或按文件读取        |
| `codegraph query <关键词>`  | 按名字搜索符号                        |
| `codegraph callers <符号>` | 找一个函数的所有调用点                    |
| `codegraph impact <符号>`  | 分析改动一个符号会波及哪些代码                |
| `codegraph status`       | 查看索引统计、是否有待同步文件                |
| `codegraph sync`         | 手动增量更新（一般不用，自动同步已覆盖）           |


### 1.3.3. 常用维护命令


| 命令                    | 作用                                |
| --------------------- | --------------------------------- |
| `codegraph upgrade`   | 升级到最新版（加 `--check` 只看有没有更新）       |
| `codegraph uninit`    | 移除本项目的 `.codegraph\` 索引           |
| `codegraph uninstall` | 从所有 agent 配置中卸载 CodeGraph（不删项目索引） |


## 1.4. 注意事项

- **`.codegraph\` 不要提交 git**：这是本地索引，建议加进 `.gitignore`。
- **索引自动忽略范围**：默认跳过 `node_modules` / `.venv` / `dist` / `build`、`.gitignore` 里的内容，以及大于 1MB 的文件。
- **索引慢或缺符号**：先确认大目录已被排除；改完文件等一两秒让它自动同步，必要时手动 `codegraph sync`。

## 1.5. 参考链接

- 项目仓库：[https://github.com/colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)
- 官方文档：[https://colbymchenry.github.io/codegraph/](https://colbymchenry.github.io/codegraph/)

