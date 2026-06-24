
## `.cursor/` 目录完整解析

### 何时被读取

`.cursor/` 目录在 **每次 Cursor Agent 会话启动时**自动扫描并加载其中的配置。具体触发时机：

- 打开 workspace / 项目时
- Agent 新对话开始时（`sessionStart`）
- 特定文件匹配规则时（rules 按 glob 按需注入）
- Hook 事件触发时（实时监听，`hooks.json` 保存后立即重载）

---

### 可包含的目录 / 文件一览

| 路径 | 类型 | 作用 | 读取时机 |
|------|------|------|----------|
| `.cursor/rules/` | 目录 | 存放 `.mdc` 规则文件，给 Agent 提供**持久化上下文和编码规范** | 每次对话，按 `alwaysApply` 或文件匹配 glob 自动注入 |
| `.cursor/skills/` | 目录 | 存放 Skill 目录（每个含 `SKILL.md`），赋予 Agent **专项能力** | Agent 判断任务匹配时主动读取 |
| `.cursor/hooks.json` | 文件 | 定义 Hook 触发规则，指向 `.cursor/hooks/` 下的脚本 | Hook 事件发生时实时触发；文件保存即重载 |
| `.cursor/hooks/` | 目录 | 存放 Hook 脚本（bash/python 等），被 `hooks.json` 引用 | 同上，由对应 Hook 事件驱动 |
| `.cursor/mcp.json` | 文件 | 项目级 MCP 服务器配置，定义本项目专用的 MCP 工具 | 项目打开时加载 |

---

### 各目录详细说明

#### 1. `.cursor/rules/` — AI 行为规则
https://cursor.com/cn/docs/rules 
存放 `.mdc` 格式文件，控制 Agent 在该项目中的行为：

```
.cursor/rules/
  python-standards.mdc   # alwaysApply: true
  api-conventions.mdc    # globs: **/*.py
```

frontmatter 三个字段：
- `alwaysApply: true` → 每次对话都注入
- `globs: **/*.ts` → 仅当匹配文件打开时注入
- `description` → 规则说明

.cursor/rules/
  react-patterns.mdc       # Rule with frontmatter (description, globs)
  api-guidelines.md        # 简单的 Markdown 规则
  frontend/                # Organize rules in folders
    components.md


#### 2. `.cursor/skills/` — 专项能力（你的 `pursue` 项目已有）
每个 skill 是一个**子目录**，含 `SKILL.md` 和可选脚本：

```
.cursor/skills/
  resume-writer/          ← pursue 已有
    SKILL.md
    scripts/md_to_docx.py
```

- **项目级** `.cursor/skills/` → 随仓库共享，团队可用
- **用户级** `~/.cursor/skills/` → 跨所有项目可用

#### 3. `.cursor/hooks.json` + `.cursor/hooks/` — 自动化钩子
在 Agent 行为的生命周期节点插入自定义逻辑：

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [{ "command": ".cursor/hooks/guard.sh" }],
    "afterFileEdit":        [{ "command": ".cursor/hooks/format.sh" }]
  }
}
```

常用事件：`beforeShellExecution`、`afterFileEdit`、`preToolUse`、`sessionStart`、`subagentStop` 等。

#### 4. `.cursor/mcp.json` — 项目级 MCP 服务器
为该项目单独配置 MCP 工具（数据库、内部 API 等），不影响全局设置。

---

### 作用域对比

```
~/.cursor/          ← 用户级（跨所有项目）
  skills/
  hooks.json
  hooks/

project/.cursor/    ← 项目级（随 git 共享）
  rules/
  skills/
  hooks.json
  hooks/
  mcp.json
```

你的 `pursue` 项目目前只用了 `skills/`，可以按需添加 `rules/`（统一编码规范）或 `hooks.json`（自动化流程）。