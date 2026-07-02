# 1. claude-mem

## 1.1. 这是个什么工具

claude-mem 是给 coding agent 装的**跨会话长期记忆**插件（作者 thedotmack）。它靠生命周期 hook 自动捕获你这次会话干了啥，用 AI 压缩成语义摘要存进本地库，下次开新会话再按当前任务检索、自动注入上下文——本质是**轻量 RAG + 记忆层**，解决"agent 写完就忘、每次都要重新解释项目背景"。

跨平台：Claude Code、Cursor、Codex、Gemini CLI、OpenCode 等。

## 1.2. 怎么工作

全自动，无需手动记录：

| 环节 | 做什么 |
| --- | --- |
| 捕获 | 5 个生命周期 hook（SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd）记录工具调用、文件改动 |
| 压缩 | 用 AI 把原始上下文压成语义摘要（约 5000 token → 200 token）|
| 存储 | 本地 SQLite（session / observation / summary）+ Chroma 向量库 |
| 注入 | 新会话按当前任务检索相关记忆，自动塞进上下文 |
| 检索 | `mem-search` skill 三层渐进式查询（search → timeline → get_observations），省 token |

常驻一个 worker 服务（`localhost:37777`，带 Web Viewer 实时看记忆流）。

## 1.3. 安装（Windows + Cursor）

前置：Node ≥ 20（Bun 缺失会自动装）。

1. 安装并接入 Cursor（`user` = 对所有项目生效，去掉则只装当前项目）：

```powershell
npx claude-mem cursor install user
```

2. 启动记忆 worker：

```powershell
claude-mem start
```

3. 没有 Claude Code 时，需配一个压缩用的 provider（如 Gemini 免费额度）：

```powershell
claude-mem settings set CLAUDE_MEM_PROVIDER gemini
claude-mem settings set CLAUDE_MEM_GEMINI_API_KEY <你的key>
```

4. 重启 Cursor 加载 hook。验证：浏览器开 `http://localhost:37777`，提交一次对话后能看到 observation 出现。

> 它会把记忆写进 `.cursor/rules/claude-mem-context.mdc`，Cursor 每次会话自动带上。注意 `npm install -g claude-mem` 只装 SDK、不装 hook 和 worker，必须用 `npx claude-mem ... install`。

## 1.4. 怎么用

装好后全自动，平时不用管；想主动查历史时用自然语言让 agent 走 `mem-search`，例如"上次这个模块我们怎么改的"。

## 1.5. 本项目结论

暂不安装（可选试用）。要先分清两个层面，别混淆：

| | claude-mem | AgentA 的 memory |
| --- | --- | --- |
| 服务对象 | 开发 AgentA 时用的 Cursor / coding agent | AgentA 产品的最终用户 |
| 层面 | 开发工具层 | 产品功能层（`src/stores/user_memory`、RAG 引用注入、记忆召回 eval）|

两者管的是不同的事，**不冲突也不重复**。只能从"要不要给开发用的 Cursor 装记忆"角度评估。结论偏不装：它偏重（常驻 worker + Chroma + 一堆 hook，无 Claude Code 还要自配 provider），而 Cursor 自带 Memories 已有重叠，对单人 / 中小项目 ROI 不高。

## 1.6. 参考链接

- 项目仓库：[https://github.com/thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)
- 官方文档：[https://docs.claude-mem.ai](https://docs.claude-mem.ai)
