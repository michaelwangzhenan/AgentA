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
| `src/agent/*.py` | 删注释里的 "Chainlit" / "chainlit_app.py" 提及 |
| `src/llm/provider.py` | 删注释里的 "Chainlit" / "chainlit_app.py" 提及 |
| `src/cli/skill_loader.py`| 删注释里的 "Chainlit" / "chainlit_app.py" 提及 |
| `tests/conftest.py` | 删注释里的 "Chainlit" / "chainlit_app.py" 提及 |


# 4. 需求定义

## 4.1. 功能

系统配置
侧栏导航
hover 预览

LLM选择（包含Thinking 模式）
会话管理
用户记忆管理

prompt 管理 -> rules.md
skills 管理
mcp 管理
业务功能（学习/研究助理）-> 可扩展更多功能

有 debugging 能力

参考行业标准，看看还有哪些功能适合本项


## 4.2. UX 风格
参考 Claude 的 Web 版(包括配色、字体、布局等)

页面布局图:



# 5. 技术选型

## 5.1. 扫盲基础知识

**前端与后端**
现在要做的 UI 就是前端，后端是 agent 和 RAG 部分。
Agent Core 提供的 agent API + agent Event 可以给 CLI 和 UI 共用。

JS/TS 是前端语言
python/java/go/rust 是后端语言

**流式输出**
流式输出是指在网页上实时显示文本，而不是一次性加载所有内容。
LLM 回答是一个字一个字往外吐的。前端如果没法处理"一段一段进来的数据"，就只能等全部生成完再一次性显示，体验差很多。

后端推流给前端的两种主流方式：
- SSE（Server-Sent Events）：单向，后端 → 前端推消息，HTTP 长连接。对 chat 场景最合适，简单可靠。
- WebSocket：双向通讯，前后端都能主动发消息。比 SSE 复杂，但前端要"主动取消生成"之类场景才需要。
记住这两个词是为了下面对比时不发懵。本项目用 SSE 就够了。

## 5.2. 相关技术

快速学习相关技术基础

**技术栈 mermaid 图**


逐个了解：
HTML
CSS

FastAPI
React

tailwind
shadcn/ui
...

## 5.3. 选型决策

FastAPI + Vite + React + shadcn/ui
VS
FastAPI + Next.js + shadcn/ui

拖拽入库？

# 6. 实现

