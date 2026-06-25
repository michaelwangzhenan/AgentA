# 1. .cursor/

## 1.1. 目录结构


| 路径                   | 类型  | 作用                                           |
| -------------------- | --- | -------------------------------------------- |
| `.cursor/rules/`     | 目录  | 存放`.mdc` 规则文件，给 Agent 提供**持久化上下文和编码规范**      |
| `.cursor/skills/`    | 目录  | 存放 Skill 目录（每个含`SKILL.md`），赋予 Agent **专项能力** |
| `.cursor/mcp.json`   | 文件  | 项目级 MCP 服务器配置，定义本项目专用的 MCP 工具                |
| `.cursor/hooks.json` | 文件  | 定义 Hook 触发规则，指向`.cursor/hooks/` 下的脚本         |
| `.cursor/hooks/`     | 目录  | 存放 Hook 脚本（bash/python 等），被`hooks.json` 引用   |
| `.cursor/commands/`  | 目录  | 存放自定义斜杠命令（每个`.md` 一条），在对话里用 `/命令名` 调用        |


## 1.2. 作用域

两级：**用户级** `~/.cursor/`（跨所有项目）、**项目级** `<项目>/.cursor/`（随 git 共享）。同一组件两级都有时，项目级优先。


| 组件       | 用户级                                               | 项目级                                  | 备注                             |
| -------- | ------------------------------------------------- | ------------------------------------ | --------------------------------- |
| rules    | Settings 的 User Rules（可靠） `~/.cursor/rules/*.mdc` | `<项目>/.cursor/rules/*.mdc`           | 只支持 `.mdc`，不支持 `.md`<br/>`globs` 规则只对自己所在根目录的文件生效<br/>项目子目录里的其它 `.cursor/rules/` 不会被扫描<br/>一个workspace 多个根目录时，多个根目录下的`.cursor/rules/` 会被合并。<br/>**注意：**多根目录时，同一个 `.cursor/rules/` 下的.mdc 必须全部都在文件下，或没有文件夹。 |
| skills   | `~/.cursor/skills/`                               | `<项目>/.cursor/skills/`               | 支持放在项目子目录，但只作用于该子目录（等价隐式 `paths`）  |
| mcp      | `~/.cursor/mcp.json`                              | `<项目>/.cursor/mcp.json`              | 同名 server 项目级覆盖全局；只扫描这两个文件，其它位置忽略 |
| hooks    | `~/.cursor/hooks.json` + `hooks/`                 | `<项目>/.cursor/hooks.json` + `hooks/` | 两级都触发、按事件合并；冲突时高优先级覆盖            |
| commands | `~/.cursor/commands/`                             | `<项目>/.cursor/commands/`             | 所有 commands 下的command 都会列出来       |


# 2. rules

`.cursor/rules/` 下的每个 `.mdc` 是**独立加载**的。
何时加载由 frontmatter 里的 `alwaysApply`、`globs`、`description` 三个字段组合决定，即**每个文件自己的 frontmatter**（开头 `---` 之间那段）决定何时把它放进 AI 的上下文。
 `alwaysApply` / `globs` / `description` 这几个字段名区分大小写，`alwaysApply` 是驼峰、值只有 `true` / `false`。

## 2.1. 始终加载

每次对话都无条件加载。适合放永远要遵守的硬规则（工程公约、安全红线）。代价是常驻上下文，所以要写短。

```
---
description: AgentA 工程公约
alwaysApply: true
---
所有代码注释用中文，只解释"为什么"不解释"做了什么"。
```

## 2.2. 匹配加载

只有当本轮对话涉及匹配 `globs` 的文件时才注入，平时不占上下文。适合"某类文件专属"的规则。
比如只在改前端组件时才提醒的规范：

```
---
description: 前端组件规范
globs: frontend/src/components/**/*.tsx
alwaysApply: false
---
组件用函数式写法，props 用 TypeScript 接口显式声明。
```

## 2.3. Agent按需加载

AI 读 `description` 后自己判断"这次任务用不用得上"再决定拉不拉进来。
所以 `description` 要写清楚"什么时候该用"。
适合不常用、但特定任务很关键的规则：

```
---
description: 写数据库迁移脚本时遵守的命名与回滚规范
alwaysApply: false
---
迁移文件命名 V{序号}__{描述}.sql；每个迁移必须配套 down 脚本。
```

## 2.4. 手动加载

三个字段都不设，只有在对话里用 `@规则名` 显式点名才加载。

```
---
---
发版前检查清单：1) 跑全量测试 2) 更新 CHANGELOG 3) 打 tag。
```

# 3. skills

每个 skill 是**一个文件夹**（含 `SKILL.md`，可选 `scripts/` / `references/` / `assets/`）。
Cursor 启动时扫描所有 skill，平时只把 frontmatter 里的 `name` + `description` 放进上下文；任务命中 `description` 才去读 `SKILL.md` 正文（正文引用的脚本是"运行拿输出"，源码不进上下文）。
`name` 必须与文件夹名一致；`description` 决定何时触发，要写清"做什么 + 什么时候用"。

```
.cursor/skills/
  resume-writer/
    SKILL.md
    scripts/md_to_docx.py
```

## 3.1. 自动加载

不设 `disable-model-invocation` 时，AI 读 `description` 自己判断要不要用。适合希望 Agent 主动接管的任务。

```
---
name: deploy-staging
description: 把应用部署到 staging 环境时使用，含构建、推送、健康检查步骤。
---
```

## 3.2. 文件匹配加载

设 `paths`（glob），只有动到匹配文件时才浮现，平时不占上下文。

```
---
name: tsx-test-writer
description: 给前端组件写测试。
paths: frontend/**/*.tsx
---
```

## 3.3. 手动加载

设 `disable-model-invocation: true` 则**不自动触发**，只能在对话里打 `/skill名` 调用（或 `@` 选中作为上下文）。适合你想明确发起、不希望 Agent 乱接管的任务。

```
---
name: resume-writer
description: 把 Markdown 简历导出成 Word。
disable-model-invocation: true
---
```

# 4. mcp

MCP（Model Context Protocol）给 Agent 接外部工具和数据源——rules/skills 给的是文本上下文，MCP 给的是真能调的工具（查库、调 API、操作 GitHub 等）。
用 `mcp.json` 声明要连哪些 server，顶层固定 `mcpServers`，每个 key 是自起的 server 名。

## 4.1. 本地 server（stdio）

Cursor 把它当子进程拉起，用 `command` / `args` / `env`。本机的 CodeGraph 就是这种：

```json
{
  "mcpServers": {
    "codegraph": {
      "type": "stdio",
      "command": "codegraph",
      "args": ["serve", "--mcp", "--path", "${workspaceFolder}"]
    }
  }
}
```

## 4.2. 远程 server（Streamable HTTP / SSE）

只给 `url` + `headers`。Streamable HTTP 是当前标准，SSE 是旧格式（能用但在淘汰）。

```json
{
  "mcpServers": {
    "my-remote": {
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

## 4.3. 要点

- **变量插值**：`command`/`args`/`env`/`url`/`headers` 里可用 `${env:NAME}`、`${workspaceFolder}`、`${userHome}` 等。
- **生效时机**：改完重启 Cursor，或在 `Settings > Tools & MCP` 里 toggle。
- **静默失效**：JSON 写错（多个逗号）整份被默默忽略；Windows 路径反斜杠要转义 `C:\\Users\\...`。
- **密钥别提交**：含 token 时用 `${env:...}` 或占位符，真 key 不进 git。

# 5. hooks

在 Agent 循环的生命周期节点插入自己的脚本，用来**观察**（记日志/审计）或**拦截**（挡危险 shell、改权限、格式化）。两个文件配合：`hooks.json` 声明"哪个事件跑哪个脚本"，`.cursor/hooks/` 下放真正执行的脚本（用户级/项目级位置见 §1.2）。

`hooks.json` 里的脚本路径以**它所在目录**为基准：项目级写 `.cursor/hooks/x.sh`，用户级写 `./hooks/x.sh`。

## 5.1. 配置结构

`version: 1` 固定；`hooks` 下按事件名列数组，每项一个脚本：

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      { "command": ".cursor/hooks/guard.sh", "failClosed": true }
    ],
    "preToolUse": [
      { "command": ".cursor/hooks/validate.sh", "matcher": "Shell|Write" }
    ],
    "afterFileEdit": [
      { "command": ".cursor/hooks/format.sh" }
    ]
  }
}
```

单项可选字段：`matcher`（正则，限定只对某些工具 `Shell`/`Read`/`Write`/`Task`/`MCP` 触发）、`timeout`（超时秒数）、`failClosed`（true = 脚本失败/超时就挡住该动作，默认放行）、`loop_limit`（给 `stop` 这类可循环事件限次）。

## 5.2. 脚本怎么通信

- **输入**：Cursor 把 JSON payload 经 **stdin** 喂给脚本（含 `hook_event_name` / `command` / `file_path` / `workspace_roots` 等）。
- **输出**：脚本往 **stdout 打 JSON**。
- **放行/拦截**：退出码 `0` 放行、`2` 拦截；`before`* 事件还可在 JSON 里返回 `permission`（`allow` / `ask` / `deny`）+ `user_message` / `agent_message`。

## 5.3. 常用事件


| 阶段            | 事件                                             |
| ------------- | ---------------------------------------------- |
| 会话            | `sessionStart` / `sessionEnd`                  |
| 提交输入          | `beforeSubmitPrompt`                           |
| 工具（带 matcher） | `preToolUse` / `postToolUse`                   |
| Shell         | `beforeShellExecution` / `afterShellExecution` |
| MCP           | `beforeMCPExecution` / `afterMCPExecution`     |
| 文件            | `beforeReadFile` / `afterFileEdit`             |
| 子代理           | `subagentStart` / `subagentStop`               |
| 其他            | `stop` / `preCompact` / `afterAgentResponse`   |


## 5.4. 要点

- **自动重载**：改 `hooks.json` 保存即重载，不生效再重启。
- **调试**：底部 Output 面板选 `Hooks`，看每次触发的 INPUT/OUTPUT JSON。
- **可执行权限**：类 Unix 下脚本要 `chmod +x`；Windows 下命令走配置的 shell，注意脚本解释器。
- **典型用途**：`beforeShellExecution` 挡危险命令并记日志、`afterFileEdit` 自动格式化、`beforeReadFile` 读文件前脱敏。

# 6. commands

你手动触发的可复用 prompt：打 `/命令名` 就把对应 `.md` 文件内容当 prompt 注入。和 rules（按场景自动套）、skills（Agent 判断相关才用）正好相反，commands 是**显式手动**那一极，还能传参。

## 6.1. 写法

一个命令一个 `.md` 文件，**文件名就是命令名**，整篇 markdown 就是 prompt。文件里用 `$ARGUMENTS` 占位，调用时命令名后面的文字会整段替换进去：

```markdown
---
argument-hint: <issue 编号或描述>
---
# Fix Issue
修复下面这个 issue，遵守项目编码规范并补测试：

$ARGUMENTS
```

存成 `.cursor/commands/fix-issue.md` → 打 `/fix-issue 42` 时，`$ARGUMENTS` 就被替换成 `42`。

## 6.2. 要点

- **传参**：用 `$ARGUMENTS` 占位，命令名后面的整段文字替换进去；不写 `$ARGUMENTS` 时，那段文字就当普通上下文附在末尾。
- **frontmatter 可选**：`description`（自动补全里的说明）、`argument-hint`（提示该传什么参数）、`name`（kebab-case）；不加也行，支持 `.md` / `.mdc` / `.markdown` / `.txt`。
- **最佳实践**：在 Agent 模式下用最稳。

