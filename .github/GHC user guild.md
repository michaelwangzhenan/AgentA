# 1. Session
Chat Session 类型: 侧边栏、Editor、New Window
开启新话题时，新建 new session 保持对话聚焦
Chat session history 可以导出: Ctrl+Shift+P -> Chat: Export Chat...
在 chat session 中导航: Ctrl+Alt+↑/↓

# 2. Context
## 2.1 Workspace indexing
Remote, Local, Basic（三种索引级别）

## 2.2 自动上下文
默认使用: 选择的代码、打开的文件、文件名

## 2.3 使用 # mention
- Files/Folders: `#file:main.cpp` `#folder:include/`
- Code symbols: `#class:MyClass` `#function:calculateSum`
- Tools: `#terminal` `#git` `#problems`
- Codebase: `#codebase` 进行全代码库语义搜索
- Webpage: `#fetch <网址>` 引用网页内容

## 2.4 使用 @ 专家模式
指定领域话题: `@vscode` `@terminal` `@workspace`
- `@workspace`: 项目结构、构建配置
- `@terminal`: Shell 命令、脚本
- `@vscode`: 编辑器功能、设置

## 2.5 多模态
直接粘贴图片作为 context（截图、UML图、错误消息等）

# 3. Plan Agent
在 Chat 点击Agent 切换进入 Plan 模式
描述high level task, agent *会列出执行步骤*
确认执行计划后，切回Agent 模式开始修改


