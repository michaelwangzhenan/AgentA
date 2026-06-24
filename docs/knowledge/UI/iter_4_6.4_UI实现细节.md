## 6.4 分步实现

按"**先架子、后填肉**"拆 8 个 Step。每个 Step 一个里程碑、独立可验收。

| Step | 主题 | 里程碑（看得到的效果） |
|---|---|---|
| **Step 0** | 项目骨架 | 后端 `/api/health` + 前端空页 + Vite proxy 跑通 |
| **Step 1** | 最小聊天回路（非流式） | 输入框发消息、Agent 返回完整答案（一次性） |
| **Step 2** | 流式输出 + Agent 状态 | SSE 流式打字 + Thinking / Plan / Tool 折叠块 |
| **Step 3** | Session 管理 | 左侧栏会话列表、新建 / 切换 / 改名 / 删除、刷新不丢 |
| **Step 4** | 知识库 + 拖拽入库 | 拖文件 → 进度 → 入库；文档列表 / 删除 |
| **Step 5** | 其他资源管理 | rules / memory / skills / mcp 4 类 CRUD |
| **Step 6** | 系统配置 + 主题 + 反馈 + 调试 | LLM 参数面板 + 暗色模式 + toast / error / loading + 日志查看 |
| **Step 7** | 业务面板 | 学习计划 / Quiz / SRS |
| **Step 8** | 总体验收 | 端到端跑通全功能 + UT 全量回归 |

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
| `system_prompt` / skills / rules 都是默认值 | 体感比 CLI 简陋（agent "傻"一些），本 Step 接受。**实际后续 Step 5 没做 composition root 抽取**：Agent 实例化路径跟 CLI 仍是两份代码，只是各自正常加载 rules / skills / memory（行为基本一致），属于代码重复 backlog，不阻塞功能 |

---

### 6.4.3 Step 2 - 流式输出 + Agent 状态

**目标**：把 Step 1 的"等几秒一次性返回"改成**实时打字流**，并把 Agent 内部的 `thinking` / `plan` / `tool` 三类过程信号也推到前端可视化。完成后体验对齐 ChatGPT / Claude 网页版。

**本 Step 不做**：

| 项 | 留给 |
|---|---|
| 真正中止正在跑的 Agent（前端只断流，后端继续跑完） | Step 6 / 后续 |
| Session 列表 / 切换 / 历史持久化 | Step 3 |
| 暗色模式 / 错误 toast / 重试按钮 | Step 6 |
| Citation / 引用渲染 | Step 5 |

**对接现有代码的策略**：

- 复用 `agent.set_event_callback(fn)` —— Step 1 的单例 Agent 直接订阅事件 → SSE 推给前端
- **Agent core 一行不动**：[`EventBus`](../src/agent/core/event_bus.py) 已就位，10 种事件 + payload schema 早已稳定，参 [`src/agent/agent.py`](../src/agent/agent.py) 各 `publish(AgentEvent(...))` 点
- 流式协议用 **SSE（Server-Sent Events）**，不上 WebSocket：单向流足够、HTTP 兼容、免握手

**Agent 事件 → SSE 帧映射**（按 [`event_bus.py`](../src/agent/core/event_bus.py) 现有 10 种）：

| 事件类型 | payload schema（实证） | 前端怎么渲染 |
|---|---|---|
| `thinking_chunk` | `{"text": str}` | 累加到当前消息的 `ThinkingBlock`（默认折叠，仅显示 header） |
| `token_chunk` | `{"text": str}` | 累加到当前消息正文 markdown 区 |
| `tool_call_start` | `{"name": str, "args": dict, "call_id": str}` | 在正文上方插一张 `ToolBlock` 卡片（默认折叠，仅显示 `🔧 name`） |
| `tool_call_end` | `{"call_id": str, "status": str, "preview": str}` | 找到对应 `call_id` 的卡片，置 status + preview |
| `plan_created` | `{"steps": [{"id": int, "text": str}, ...], ...}` | 渲染 `PlanBlock` checklist |
| `plan_step_start` | `{"step_id": int, "text": str}` | 高亮该步为"进行中"（⏳） |
| `plan_step_end` | `{"step_id": int, "status": str, "note": str}` | 该步标 ✓ / ✗ / ⏭ |
| `final_answer` | `{"text": str, "usage": ..., "aborted_by_user"?: bool}` | 收到即关流；用作"流结束"信号；正文 fallback（若 token_chunk 累加结果跟 text 不一致就以 text 为准） |
| `error` | `{"message": str, "recoverable": bool, "phase": str}` | 红字插在消息底部；不一定关流（后续可能仍有 final_answer 兜底） |
| `info` | `{"message": str, ...}` | 调试用，本 Step 不渲染（开发者 Network tab 看即可） |

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 流式协议 | SSE | 单向流足够；HTTP 友好；不用 ws 升级握手 |
| POST body 携带 message | 前端用 [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source) | 浏览器原生 `EventSource` 只支持 GET，message 太长走 URL 不优雅 |
| 服务器 SSE 库 | `sse-starlette` 的 `EventSourceResponse` | 内置 ping / disconnect 处理；免手写 SSE 帧 |
| 同步 Agent ↔ 异步流的桥 | `Agent.run` 扔 `loop.run_in_executor`；事件回调用 `loop.call_soon_threadsafe(queue.put_nowait, ...)` 入 `asyncio.Queue` | `Agent.run` 同步阻塞 + 事件回调同步；`asyncio.Queue` 非线程安全，必须 `call_soon_threadsafe` |
| 一次请求一条流 | POST → 一条 SSE 流 → 收到 `final_answer` 或线程结束就关流 | 跟 ChatGPT 同款；不维持长连接 |
| 帧格式 | 统一 `event: message` + `data: {"type": "...", "payload": {...}}` | 前端单 listener、按 type 派发；跟 `AgentEvent` 一一对应；OpenAPI 文档化 |
| Thinking / Plan / Tool 折叠 | shadcn `collapsible` 组件 | 让长 thinking / 大 args 不刷屏 |
| 取消语义 | 前端 `AbortController` 断 SSE → UI 停渲染；后端 Agent 继续跑完 | 真正中止 Agent 涉及 core 改造，本 Step 明确不做 |
| 自动滚动 | 用户在底部 → 新 token 跟随滚动；用户向上滚 → 暂停跟随；用户重新滚到底 → 恢复跟随 | 业界标准体感 |
| Step 1 的 `POST /api/chat` 怎么办 | **保留** | 非流式 fallback / 测试入口；前端默认调 `/api/chat/stream`，老接口不删 |

**实现内容**：

后端：

- `requirements.txt` 加 `sse-starlette`（同步 `.env` 不涉及 —— 它是纯库不读环境变量）
- `src/api/routes/chat.py` —— 新增 `POST /api/chat/stream`：
  - 路由内建临时 `asyncio.Queue`
  - `set_event_callback` 把所有事件经 `loop.call_soon_threadsafe` 入队
  - `loop.run_in_executor(None, agent.run, req.message)` 异步跑 Agent
  - async generator 从 queue 取事件 yield 给 `EventSourceResponse`
  - 收到 `final_answer` 或 `error(recoverable=False)` 或 executor 完成 → 关流
  - `finally` 里 `set_event_callback(None)` 解绑（沿用 Step 1 单例 Agent，必须解绑避免泄漏到下一轮）

前端：

- `npm install @microsoft/fetch-event-source react-markdown`（markdown 顺带装上，正文渲染加分项）
- `npx shadcn@latest add collapsible`
- `src/types/chat.ts` 加 `AgentStreamEvent` discriminated union（10 种 type 各自对应 payload）
- `src/api/client.ts` 加 `streamChat(message, handlers, signal)` —— `fetchEventSource` POST + 按 type 派发到 handlers
- `src/components/chat/MessageBubble.tsx` —— 一条消息完整渲染：user 简版 / assistant 含 `ThinkingBlock` + `PlanBlock` + `ToolBlock[]` + 正文 markdown
- `src/components/chat/ThinkingBlock.tsx` —— 折叠展示 thinking 流，默认折叠，header 显示字数
- `src/components/chat/PlanBlock.tsx` —— plan checklist（每步带 status icon）
- `src/components/chat/ToolBlock.tsx` —— tool 调用卡片（name / args / status / preview，默认折叠）
- `src/components/chat/MessageList.tsx` —— 改用 `MessageBubble`；管理"用户滚动到底"状态做条件自动滚动
- `src/App.tsx` —— 改调 `streamChat`；维护"当前 in-flight assistant 消息"对象（含 thinking / plan / tools / content 子块）

**修改 / 新增列表**：

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `requirements.txt` | 加 `sse-starlette` |
| 修改 | `src/api/routes/chat.py` | 加 `POST /api/chat/stream` 端点；保留旧 `POST /api/chat` |
| 修改 | `src/api/schemas/chat.py` | 加 `ChatStreamEvent` Pydantic（仅 OpenAPI 文档化；实际 SSE 帧用 `EventSourceResponse` 手组装） |
| 新增 | `tests/test_api_chat_stream.py` | mock Agent，按序 publish 几种事件，断言 SSE 帧序列对得上 |
| 修改 | `frontend/package.json` | 加依赖：`@microsoft/fetch-event-source`、`react-markdown` |
| 新增 | `frontend/src/components/ui/collapsible.tsx` | `shadcn add collapsible` 生成 |
| 修改 | `frontend/src/types/chat.ts` | 加 `AgentStreamEvent` 类型 |
| 修改 | `frontend/src/api/client.ts` | 加 `streamChat`；保留 `postChat`（开发期 fallback） |
| 新增 | `frontend/src/components/chat/MessageBubble.tsx` | 一条消息的完整渲染 |
| 新增 | `frontend/src/components/chat/ThinkingBlock.tsx` | 思考折叠块 |
| 新增 | `frontend/src/components/chat/PlanBlock.tsx` | plan checklist |
| 新增 | `frontend/src/components/chat/ToolBlock.tsx` | tool 调用卡片 |
| 修改 | `frontend/src/components/chat/MessageList.tsx` | 改用 `MessageBubble`；条件自动滚动 |
| 修改 | `frontend/src/App.tsx` | 改调 `streamChat`；维护 in-flight assistant 消息子块状态 |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| 后端 | `tests/test_api_chat_stream.py`：用 `dependency_overrides` 注入 `FakeAgent`（`run` 内同步连发几个 `events.publish(...)` 再返回 final_answer）。`TestClient` 的 `stream("POST", "/api/chat/stream", json=...)` 读 SSE 帧、解析 data 字段、断言 type 序列匹配 |
| 后端 | 错误路径：FakeAgent 直接 publish `error(recoverable=False)` → 断言客户端收到 error 帧 + 流随后关闭 |
| 后端 | 取消路径：客户端主动 close → 服务端日志可见、`set_event_callback(None)` 已解绑（下一轮 Agent 调用不触发上一轮 handler） |
| 前端 | 不写 UT（前端 UT 整个 iter 不上） |

**人工验收步骤**：

1. 后端 + 前端两进程都在跑（沿用 Step 0 命令；首次跑前 `pip install -r requirements.txt` 装 `sse-starlette`）
2. 浏览器开 `http://localhost:5173/`，输入 `用 3 句话讲一下牛顿三定律` → 正文 token **逐字浮现**（不再等 5-10 秒一次性出现）
3. **F12 → Network → 找到 `POST /api/chat/stream`**：状态 200，Type 列 `eventsource`；点 `EventStream` 标签能看到 10+ 帧（`token_chunk` 一连串 + 最后一个 `final_answer`）
4. 问一个需要工具的问题（前提：Step 2 范围内 Agent 默认已加载部分 builtin tool，没有也可以发 `调用 file_read 工具读 README.md` 触发）→ 正文上方先冒出 **🔧 工具调用** 卡片（默认折叠），点开看 name / args；几秒后状态变 ✓ + preview 出现
5. 问一个会触发 plan 的问题（例：`帮我设计一份 4 周的『Rust 入门 → 写一个小项目』学习计划`）→ 上方出现 **📋 Plan** checklist；每步状态从 ⏳ 实时翻 ✓
6. **滚动行为**：长回答打字到一半，手指往上滚看历史 → 新 token 不再强行把页面拉到底；再手动滚到底 → 恢复自动跟随
7. **断流测试**：长回答中途**关闭当前浏览器 tab** → 后端 uvicorn 日志可见 `disconnected`；后端 `agent.set_event_callback(None)` 已解绑（新开 tab 发新消息流式正常，不会收到上一轮的残留事件）
8. `pytest -q tests/test_api_chat_stream.py` 全过

通过以上 7-8 条 = Step 2 完成。

**风险点 / 已知限制**（不影响本 Step 验收）：

| 项 | 说明 |
|---|---|
| 取消按钮"假停" | 后端 Agent 仍跑完整轮（真正中止涉及 Agent core 改造，未做）。**Step 7 review 时补了前端主动 abort**（`App.tsx` 用 `AbortController`，session 切换时断流），前端体验改善；后端单轮跑完后才释放锁，资源占用接受 |
| `asyncio.Queue` 无大小限制 | Agent 比前端消费快的极端情况下内存涨；本 Step 接受（实测一轮事件数 ≤ 几百） |
| ~~单例 Agent + 并发请求~~ **（Step 7 review 已修）** | 原文档以为"Step 3 session 隔离后自然解决"——**错的**，Step 3 不解决。Step 7 review 时在 `src/api/routes/chat.py` 加 `_AGENT_LOCK = threading.Lock()` 串行化 `agent.run` + `set_event_callback`，并发请求按到达顺序排队执行，不再覆盖 `session_id`。**单用户工具 scope 下牺牲并发换 thread-safety 可接受**；多用户场景需要换 per-request Agent 实例 |
| `thinking` 体量大可能比正文还长 | 默认折叠（header 显示字数 + "展开" 按钮） |
| 浏览器 6 个 HTTP/1.1 同域并发上限 | 本期单 tab 单流不踩；生产部署用 HTTP/2 / 反代解决 |
| **`token_chunk` 颗粒度依赖 provider，不是统一逐 token** | 实测 3 家行为差异巨大：**kimi** 真 token 级（约 200 chunks）/ **qwen** 半流式大块（约 7 chunks）/ **glm** 几乎非流式（2 chunks）。详 [knowlege.md §10](./knowlege.md#10-llm-streaming--tool-call-行为差异)。AgentA 不做客户端均匀化（无意义且增加假打字延迟）—— 流式打字体验依赖 provider 实际能力 |
| **GLM + 计划类 query 触发 plan 自适应死循环**（已知 backlog） | 用 glm 问"制定一个 X 学习计划"类 query，LLM 反复调 `make_plan` refine 直到 8 轮上限。表现：UI 看到 `make_plan(steps=[...])` 像伪文本 + 后端日志 `工具调用已达上限 8 轮`。**跟 streaming 无关**（LLM 决策层问题）；切 kimi / qwen 不复现。独立 task 跟进 |

---

### 6.4.4 Step 3 - Session 管理

**目标**：左侧栏显示所有历史会话，支持新建 / 切换 / 重命名 / 删除；刷新页面或重启后端历史不丢。完成后体验对齐 ChatGPT / Claude Web 的左侧 Recents 列表（带可折叠标签，折叠状态 `localStorage` 持久化）。

**本 Step 不做**：

| 项 | 留给 |
|---|---|
| 多 tab 并发的 session_id 互相覆盖问题 | 接受为已知风险（Step 2 已列出），单用户场景实际不踩 |
| 文件夹 / 标签 / 收藏 等高级组织 | 无计划 |
| LLM 自动起标题 | 暂不做（默认显示 `first_user_msg` 预览或 `id 前 8 位`） |
| 跨设备同步 | 无计划 |
| 软删除 / 撤销 | 暂不做（DELETE 直接级联清掉 messages + sessions 两表） |
| Citation / 引用 | Step 5 |

**对接现有代码的策略**：

- 复用 [`ChatHistoryStore`](../src/memory/chat_history.py) —— 已有 `list_sessions / load / delete_session / append`，只补一个 `rename_session(session_id, title) -> bool`
- `Agent.session_id` 是 mutable 字段，每次 `Agent.run()` 内重新构造 `HistoryManager / MemoryManager`（[agent.py:413+](../src/agent/agent.py)），改单例 Agent 的 session_id 不破坏不变量
- session 标题字段复用 `sessions.first_user_msg` 列（不动 schema）—— 这个字段承担"自动从首条用户消息生成预览" + "用户手动改名"双语义；改名后用户看不到原始预览，但聊天历史里有原文，不损失信息
- API 路径按本文档 §5.1.10 / §6.2 既有规划：`GET/POST/PATCH/DELETE /api/sessions` + `GET /api/sessions/{id}/messages`

**API 设计**：

| Method | Path | Request Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/sessions` | - | `{sessions: [{id, title, created_at, msg_count}]}` | 全量列表，按 `created_at` 倒序 |
| `POST` | `/api/sessions` | `{title?: str}` | `{id, title, created_at, msg_count: 0}` | 新建空 session（后端 `uuid.uuid4()` 生成 id） |
| `PATCH` | `/api/sessions/{id}` | `{title: str}` | `{id, title, ...}` | 重命名 |
| `DELETE` | `/api/sessions/{id}` | - | `{deleted: bool}` | 硬删 |
| `GET` | `/api/sessions/{id}/messages` | - | `{messages: [{role, content, tool_calls?, tool_call_id?}, ...]}` | 拉某 session 完整历史（OpenAI messages 格式，含 tool 调用） |
| `POST` | `/api/chat/stream` | `{message, session_id}` | SSE | **修改**：加 `session_id` 字段；服务端 `agent.session_id = req.session_id` 后再 `agent.run` |

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| session_id 在哪生成 | 后端 `uuid.uuid4()`，`POST /api/sessions` 返回 | 单源真理；防客户端冲突 |
| "新建会话"按钮行为 | 前端点击 → `POST /api/sessions` 立刻创建空 session → 切换到新 id | 跟 ChatGPT / Claude Web 一致；列表立刻看到新 session |
| 标题字段 | 复用 `sessions.first_user_msg`（不动 schema） | 简洁 > 全面；语义略偏可接受 |
| Session 切换怎么传 | 请求 body 加 `session_id`；服务端按需覆盖 `agent.session_id` | stateless；Agent 内每次 `run()` 都 fresh 构造 history/memory manager |
| 删除策略 | 硬删（级联清 messages + sessions） | 简洁；用户预期"删了就是删了"，软删 + 回收站属于 Step 6 / 后续 |
| 第一次启动无 session 时 | 前端首屏 `GET /api/sessions`，空则立刻 `POST` 新建一个 | 用户进来就能直接发消息 |
| Delete 时若删的是当前 active | 前端自动切到列表第一个；列表空则再创建一个 | 永远保证 active session 存在 |
| 重命名 UI | shadcn `Dialog` + 输入框 + 确定/取消 | 复用 shadcn 风格；轻于 inline edit |
| 列表项菜单 | hover 显示 `⋯` 按钮 → shadcn `DropdownMenu`（重命名 / 删除） | 节省屏幕宽度；ChatGPT 同款 |
| 列表项标题 | 优先 `first_user_msg`（首 60 字截断），fallback `session_id 前 8 位` | 首次新建未发消息时 `id 前 8 位` 也比"未命名"易识别 |

**实现内容**：

后端：

- `src/memory/chat_history.py` 加 `rename_session(session_id, title) -> bool` 方法（UPDATE `sessions.first_user_msg`；返回是否找到记录）
- `src/api/schemas/session.py` 新建：`SessionInfo` / `SessionCreateRequest` / `SessionRenameRequest` / `SessionListResponse` / `SessionMessagesResponse`
- `src/api/routes/sessions.py` 新建：5 个 endpoint，全部 thin handler 转 `ChatHistoryStore`
- `src/api/main.py` 注册 sessions router
- `src/api/schemas/chat.py` `ChatRequest` 加 `session_id: str | None = None`（None 时不动 `agent.session_id`，保兼容）
- `src/api/routes/chat.py` `/api/chat/stream` & `/api/chat` 都加 `if req.session_id: agent.session_id = req.session_id` 一行
- `src/api/deps.py` 加 `get_chat_history() -> ChatHistoryStore` 单例依赖（用 `lru_cache`，跟 `get_agent` 共用 `Agent._chat_history` 实例—— 同一 SQLite 文件就行）

前端：

- `npx shadcn@latest add dialog dropdown-menu`
- `src/types/session.ts` 新建：`Session` 类型
- `src/api/client.ts` 加：`listSessions / createSession / renameSession / deleteSession / loadSessionMessages`
- `src/components/sidebar/Sidebar.tsx` 新建：左侧栏容器（含"新建会话"按钮 + `SessionList`）
- `src/components/sidebar/SessionList.tsx` 新建：列表（active 高亮 + hover `⋯` 菜单）
- `src/components/sidebar/SessionItem.tsx` 新建：单条 list item（标题 + 菜单触发器）
- `src/components/sidebar/RenameDialog.tsx` 新建：shadcn Dialog 包输入框
- `src/components/sidebar/DeleteConfirm.tsx` 新建：shadcn AlertDialog 确认（也用 `npx shadcn@latest add alert-dialog`）
- `src/App.tsx` 改：
  - 首屏拉 session list；空则 `createSession` 自动建一个
  - 维护 `activeSessionId` state
  - 切换 session 时调 `loadSessionMessages` 拉历史 + 替换 `messages` state
  - `streamChat` 调用时带 `session_id: activeSessionId`
- 主布局：原 `App.tsx` 单列改成左右分栏（左 Sidebar 固定 ~260px，右 chat 区 flex-1）

**修改 / 新增列表**：

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/memory/chat_history.py` | 加 `rename_session` 方法 |
| 新增 | `src/api/schemas/session.py` | Pydantic 5 个 schema |
| 新增 | `src/api/routes/sessions.py` | 5 个 endpoint |
| 修改 | `src/api/main.py` | 注册 sessions router |
| 修改 | `src/api/schemas/chat.py` | `ChatRequest` 加 `session_id: str \| None = None` |
| 修改 | `src/api/routes/chat.py` | `/api/chat` + `/api/chat/stream` 都加按需切 session_id 逻辑 |
| 修改 | `src/api/deps.py` | 加 `get_chat_history` 依赖 |
| 新增 | `tests/test_api_sessions.py` | 5 endpoint × 各种场景 UT |
| 新增 | `tests/test_chat_history_rename.py` | `rename_session` 单独 UT（也可合并进 `test_chat_history.py` 如果存在） |
| 修改 | `frontend/package.json` | 加 shadcn 依赖（自动） |
| 新增 | `frontend/src/components/ui/dialog.tsx` | shadcn 生成 |
| 新增 | `frontend/src/components/ui/dropdown-menu.tsx` | shadcn 生成 |
| 新增 | `frontend/src/components/ui/alert-dialog.tsx` | shadcn 生成 |
| 新增 | `frontend/src/types/session.ts` | Session 类型 |
| 修改 | `frontend/src/api/client.ts` | 加 5 个 API client function |
| 新增 | `frontend/src/components/sidebar/Sidebar.tsx` | 左侧栏容器 |
| 新增 | `frontend/src/components/sidebar/SessionList.tsx` | 列表 |
| 新增 | `frontend/src/components/sidebar/SessionItem.tsx` | 单条 item |
| 新增 | `frontend/src/components/sidebar/RenameDialog.tsx` | 重命名 dialog |
| 新增 | `frontend/src/components/sidebar/DeleteConfirm.tsx` | 删除确认 |
| 修改 | `frontend/src/App.tsx` | 左右分栏 + activeSessionId 状态 + 切换拉历史 |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| 后端 (`test_api_sessions.py`) | 用 `TestClient` + 临时 `ChatHistoryStore`（tmp_path SQLite）：列表空 / 创建 / 列表非空 / 重命名 / 重命名不存在的 / 删除 / 删除不存在的 / 拉某 session messages / messages 空 |
| 后端（chat_history） | `rename_session` 单独 1-2 个 UT（更新成功 / session 不存在返回 False） |
| 后端 | `/api/chat/stream` 带 session_id 时切换正确（mock Agent，验证 `agent.session_id` 被设置） |
| 前端 | 不写 UT（前端 UT 整个 iter 不上） |

**人工验收步骤**：

1. 后端 + 前端两进程都在跑；首屏左侧栏出现 **1 个空 session**（自动创建），右侧聊天区空
2. 发条消息（如"你好"）→ 等响应完成；left list 上该 session 标题变成 `你好`（首条消息预览）
3. 点 **"新建会话"** 按钮 → list 顶部多一个 session（标题为 id 前 8 位），自动切换到它；右侧聊天区清空
4. 在新 session 里发"再问个问题" → 该 session 标题变成 `再问个问题`；切回老 session → 历史 `你好` + AI 回复都还在
5. hover 任意 session → 出现 `⋯` → 点 **重命名** → 弹 Dialog 改成"测试会话" → 列表立刻更新
6. hover 任意 session → 点 **删除** → 弹确认 → 确认后列表移除；若删的是当前 active 自动切到列表第一个
7. **关浏览器**重新打开 / **重启 uvicorn** 后重开 → session 列表还在、消息不丢
8. 逐个删完所有 session（每条从 ⋯ 菜单单删，没有"清空"按钮）后，删到 0 条时前端**自动新建一个**空 session（永远保持至少一个 active）
9. `pytest -q tests/test_api_sessions.py tests/test_chat_history_rename.py` 全过

通过以上 8-9 条 = Step 3 完成。

**风险点 / 已知限制**：

| 项 | 说明 |
|---|---|
| ~~多 tab 并发的 session_id 互相覆盖~~ **（Step 7 review 已修）** | Step 3 当时确实没解决（原列为已知限制）。Step 7 review 时在 chat route 加 `threading.Lock` 串行化，已彻底闭合，详见 [Step 2 风险点](#643-step-2---流式输出--agent-状态) |
| 重命名复用 `first_user_msg` 列 | 改名后看不到原始首条预览（但聊天历史里有原文）。简化代价可接受 |
| session 创建未发消息 | 标题是 `id 前 8 位`，不友好。后续 Step 可加"LLM 自动起标题"或允许新建时手动命名 |

---

### 6.4.5 Step 4 - 知识库 + 拖拽入库

**目标**：用户能在 Web UI 里看到当前已入库的文档列表，**拖文件**到上传区即可入库（自动 parse / chunk / embed / upsert），不再要求开终端跑 `python tools/rag_cli.py ingest`。完成后体验对齐 Notion / Claude Project 的知识库。

**本 Step 不做**：

| 项 | 留给 |
|---|---|
| 入库进度细化（每个 chunk 实时 percent） | 后续 Step / SSE 化（同步阻塞 + 处理中 spinner 够用） |
| 多 collection / 多 embedding 模型切换 | 后续 Step（默认走 `config.DEFAULT_EMBEDDING_ALIAS`） |
| 清库（一键删全部） | 后续 Step（防误操作；用户可一条条删） |
| 文档预览 / chunks 详情查看 | 后续 Step（点文档名展开看 chunks 不在本期） |
| 引用源 hover 预览（chat 里点 sources 跳转知识库） | Step 5 |
| 后台异步 ingest 任务队列 | 后续；本期同步阻塞 |
| 重新索引（rebuild）按钮 | 不做（`ingest_all` 已经幂等增量，**等价于"重新上传相同文件"**） |

**对接现有代码的策略**：

- 复用 [`src/rag/ingest.py`](../src/rag/ingest.py) 的 `ingest_all(docs_dir, model)` —— **不开新底层函数**，落盘到子目录后调它
- 复用 [`src/rag/parser.py`](../src/rag/parser.py) 的 `SUPPORTED_EXTENSIONS`（`.md/.txt/.html/.htm/.pdf/.docx/.pptx/.xlsx`）做服务端校验
- 文档查询：直接 query Chroma collection 的 chunks 按 `doc_id` 聚合 —— 不另设 doc registry 表
- 删除：`collection.delete(where={"doc_id": ...})` + 同步删 BM25 索引中该 `doc_id` 的 chunks（复用 `BM25Index.delete_by_doc_id`）+ 删 `web_uploads/` 下的物理文件

**API 设计**：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/kb/documents` | - | `{documents: [{doc_id, filename, ext, lang, chunks, total_chars, mtime}]}` | 列出当前默认 collection 的所有文档（按 doc_id 聚合 chunks） |
| `POST` | `/api/kb/upload` | `multipart/form-data` field=`file` | `{doc_id, filename, chunks, status, message}` | 上传一个文件 + 同步 ingest（一次只传一个；前端循环传多个） |
| `DELETE` | `/api/kb/documents/{doc_id}` | - | `{deleted: bool, chunks_removed: int}` | 删除单文档（Chroma + BM25 + 物理文件） |

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 文件落盘位置 | `datasets/web_uploads/<原始 filename>` | 现有 `doc_id` 算法基于 `rel_path` SHA1，必须落盘才能稳定 doc_id；独立子目录避免污染 git tracked `datasets/data_*` |
| 默认 collection | `config.DEFAULT_EMBEDDING_ALIAS`（通常 `en` / `m3`） | 简洁；不让用户选模型 |
| 重名上传 | 直接覆盖物理文件；ingest 走 content_sha1 幂等 | 用户可"上传同名文件刷新内容"；doc_id 不变，chunks 自动 re-embed |
| ingest 调用 | 把上传文件存到 `web_uploads/`，调 `ingest_all(docs_dir=web_uploads_dir, model=default_alias)` | 不引新底层函数；扫描整个目录的代价就是扫一次幂等表 —— 实测 ms 级 |
| 上传进度 | 前端"上传中 / 处理中 / 完成 / 失败"四态 spinner；不细化 percent | 后端是同步阻塞 ingest，"上传"快、"embedding"慢；细化进度涉及 SSE，本期不做 |
| 文件大小上限 | 默认 10 MB（`WEB_MAX_UPLOAD_MB` 配置项）；超限返回 413 | 防 OOM / 单次 embed 时长爆炸 |
| 支持的扩展名 | 跟 `SUPPORTED_EXTENSIONS` 一致 | 复用已有；服务端 + 前端 `accept` 都列上 |
| 文档列表数据源 | Chroma collection 的 chunks metadata 聚合 | 不引新表；`source` / `filename` / `lang` / `mtime` / `doc_id` 都在 metadata 上 |
| 删除时是否清 BM25 | **同步清** —— `BM25Index.delete_by_doc_id` + `save_index` | 不清的话 BM25 召回会出"已删的 chunk"造成检索 ghost |
| 删除时是否删物理文件 | 同步删 `web_uploads/<filename>`（如存在） | 否则下次 ingest 又会扫到、又入库进来 |
| 是否暴露 collection / model 选择 | 不暴露 | 简洁；多模型切换属于 Step 6 系统配置范围 |
| 入口 | Sidebar 顶部加 "📚 知识库" 按钮 → 主区 view 切到 KB | 不引入 react-router；用 state 切 view 最简 |
| 主区 view 切换 | App.tsx 加 `activeView: 'chat' \| 'kb'` state | 简洁；后续 Step 5/6/7 沿用这套切换 |
| 拖拽实现 | HTML5 native drag & drop（`onDragOver` + `onDrop`），不引入 dropzone 库 | 浏览器内置；功能足够（drag highlight + 多文件循环上传）|
| 多文件拖拽 | 前端循环串行调用 POST，逐个等返回 | 同步逐个：第 N 个失败不阻塞已成功的 N-1 个 |

**实现内容**：

后端：

- `src/config.py` 加 `WEB_UPLOAD_DIR`（默认 `./datasets/web_uploads`）、`WEB_MAX_UPLOAD_MB`（默认 `10`）配置项（同步 `.env.example` + `.env`）
- `src/rag/ingest.py` 加 `list_kb_documents(model) -> list[dict]` 辅助函数 —— 聚合 chunks metadata 按 doc_id 返回
- `src/rag/ingest.py` 加 `delete_kb_document(doc_id, model) -> tuple[bool, int]` —— Chroma + BM25 + 物理文件 一并清
- `src/api/schemas/kb.py` 新建：`KBDocument` / `KBDocumentListResponse` / `KBUploadResponse` / `KBDeleteResponse`
- `src/api/routes/kb.py` 新建：3 个 endpoint
- `src/api/main.py` 注册 kb router

前端：

- `src/types/kb.ts` 新建：`KBDocument` 类型
- `src/api/client.ts` 加：`listKBDocuments / uploadKBFile / deleteKBDocument`
- `src/components/sidebar/Sidebar.tsx` 改：顶部加 "📚 知识库" / "💬 聊天" 切换按钮（控制 `activeView`）；高亮当前 view
- `src/components/kb/KnowledgeBaseView.tsx` 新建：主面板（拖拽区 + 列表 + 删除按钮 + toast）
- `src/components/kb/DropZone.tsx` 新建：拖拽上传组件（HTML5 native）
- `src/components/kb/DocumentList.tsx` 新建：文档列表（每行：文件名 / chunks / lang / ext / mtime / 删除 icon）
- `src/App.tsx` 改：加 `activeView` state；条件渲染 `<ChatView>` / `<KnowledgeBaseView>`（ChatView 需要从现有 App 主区抽出来）

**修改 / 新增列表**：

| 操作 | 文件 | 说明 |
|---|---|---|
| 修改 | `src/config.py` | 加 `WEB_UPLOAD_DIR` / `WEB_MAX_UPLOAD_MB` |
| 修改 | `.env.example` + `.env` | 三处同步（公约 §2.4） |
| 修改 | `src/rag/ingest.py` | 加 `list_kb_documents` / `delete_kb_document` 辅助函数 |
| 新增 | `src/api/schemas/kb.py` | Pydantic 4 个 schema |
| 新增 | `src/api/routes/kb.py` | 3 个 endpoint |
| 修改 | `src/api/main.py` | 注册 kb router |
| 新增 | `tests/test_api_kb.py` | 上传 / 列表 / 删除 / 大小超限 / 扩展名拒绝 |
| 新增 | `frontend/src/types/kb.ts` | KBDocument 类型 |
| 修改 | `frontend/src/api/client.ts` | 加 3 个 KB API client |
| 修改 | `frontend/src/components/sidebar/Sidebar.tsx` | 顶部加 view 切换 |
| 新增 | `frontend/src/components/kb/KnowledgeBaseView.tsx` | KB 主面板 |
| 新增 | `frontend/src/components/kb/DropZone.tsx` | 拖拽上传 |
| 新增 | `frontend/src/components/kb/DocumentList.tsx` | 文档列表 |
| 修改 | `frontend/src/App.tsx` | activeView state + ChatView 抽出 |
| 新增 | `frontend/src/components/chat/ChatView.tsx` | 把 App.tsx 里 chat 主区抽出去（解耦） |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| 后端（API） | `tests/test_api_kb.py`：构造临时 `WEB_UPLOAD_DIR` + mock `ingest_all`（避免真跑 embedding）+ mock chroma client/collection；测 list 空 / list 含 N 个 / upload 成功 / upload 不支持的扩展名 → 415 / upload 超限 → 413 / delete 成功 / delete 不存在的 → 200 + deleted=False |
| 后端（list/delete 辅助函数） | 单测 `list_kb_documents` / `delete_kb_document`：构造 fake collection / fake BM25Index，验证聚合 + 级联清理 |
| 后端（ingest 真集成测试） | **不在本期 UT 跑**：embedding 太重；放 `tools/agent_eval/` 或本地手动 |
| 前端 | 不写 UT（前端 UT 整个 iter 不上） |

**人工验收步骤**：

1. 启动后端 + 前端；左侧 Sidebar 顶部出现 "💬 聊天" / "📚 知识库" 两个 view 切换按钮
2. 点 "📚 知识库" → 主区切到 KB 面板：上方是拖拽区（带"拖文件到这里 或 点击选择"），下方是文档列表（首次启动可能为空 或 列出已 ingest 的 `data_en` 文档）
3. 拖一个 `.md` 文件到拖拽区 → 区域高亮 → 松开 → 按钮变 spinner "处理中..." → 几秒后变 ✓ + 列表出现新文档 + toast "已入库，N chunks"
4. 同一份 `.md` 再拖一次（同名）→ 提示"内容未变化，已跳过"或后端日志 `跳过（内容未变化）`
5. 改一下本地 `.md` 内容、再拖 → 列表对应文档的 chunks 数 / mtime 更新（content_sha1 变了，重 embed）
6. 拖一个 `.exe` 之类不支持的文件 → 前端 toast "不支持的格式"，不发请求（或后端 415）
7. 拖一个 >10 MB 的文件 → 后端 413 → 前端 toast "文件过大"
8. 列表里点某文档的删除 icon → 弹 AlertDialog 确认 → 确认后该行消失 + toast "已删除，X chunks 移除"
9. 切回 "💬 聊天" view，新建 session 问"我刚上传的 X 文档讲了什么？" → Agent 调 `search_knowledge` 工具能命中（验证上传后的内容真的进了向量库）
10. `pytest -q tests/test_api_kb.py` 全过

通过以上 9-10 条 = Step 4 完成。

**风险点 / 已知限制**：

| 项 | 说明 |
|---|---|
| 上传期间 LLM embedding 阻塞 uvicorn worker | 单用户场景接受；多并发上传请求会排队。后续可加任务队列 |
| 默认 collection 跟 `data_*/` 共用 | 上传文档跟 git tracked 的 data 在同一个 collection 里；列表会一起显示。这是**有意为之**（用户能看到完整的知识库），但分类显示留给后续 |
| BM25 索引同步删除依赖 `BM25_ENABLED` | 若运行环境关了 BM25，删文档只清 Chroma；下次有人开 BM25 重新 ingest 自动重建 |
| 大文件 / PDF 处理慢 | 同步阻塞 endpoint 可能十几秒；用户看到"处理中..."loading 但没 percent。可接受 |
| 同名文件覆盖会丢历史 | 没有版本管理；按"用户上传 = 当前最新"语义。简化代价可接受 |
| 重启后 `web_uploads/` 仍在磁盘 | 物理文件保留是预期行为（重启 = 重 ingest 走幂等，跳过）；删 doc 时才真删 |

---

### 6.4.6 Step 5 - 其他资源管理（Memory / Rules / Skills / MCP）

**目标**：把 Agent 的 4 类"非会话型资源"暴露到 UI 上，让用户不用开 CLI 也能：

- 看 / 改 / 删 LLM 自动学到的**用户记忆**（`UserMemoryStore`）
- 看 / 改**项目级 rules**（`.agenta/rules.md`）
- 看当前加载的 **Skills** 清单 + 失败原因
- 看 **MCP server** 健康状态 + 暴露的工具列表

完成后 Sidebar 多出一个**资源菜单区**，跟 "聊天" / "知识库" 并列。

**本 Step 不做**：

| 项 | 留给 / 不做 |
|---|---|
| Skills 在 UI 里编辑 / 启停 | 不做：用户实际改 `.agenta/skills/*/SKILL.md` 文件更直接；UI 编辑 markdown frontmatter 收益不高 |
| MCP server 在 UI 里增删 / 重启 | 不做：MCP server config 在 `.agenta/mcp.json`，重启 uvicorn 即生效；UI reload 涉及 manager lifecycle 改造 |
| Memory 手动新增条目 | 不做：手动新增基本没价值（自然语言对话自动提取就行）；只暴露 list / update value / delete / clear |
| Rules 改完热加载 | 不做：`load_project_rules` 进程内只读一次；Web UI 改完提示 "下次新 session 生效" |
| 跨 session 的 memory 可见性 | 不做：memory 本来就跨 session 共享（这是 design.md §5.3 的设计） |
| Memory 按类别筛选 / 搜索 | 不做：当前总量小（数十条级），先全列；筛选留给后续 |

**对接现有代码的策略**：

- **Memory**：复用 [`UserMemoryStore`](../src/memory/user_memory.py) 的 `load_all` / `update_value` / `delete` / `clear` / `upsert`，零改动
- **Rules**：复用 [`load_project_rules`](../src/agent/core/rules_loader.py) 读；写直接 `path.write_text(...)`（路径取 `config.USER_RULES_FILE`）
- **Skills**：复用 [`scan_skills`](../src/cli/skill_loader.py) 的 `ScanResult.loaded / failed`；返回 dataclass → dict
- **MCP**：复用 [`MCPManager.status`](../src/agent/core/mcp_manager.py) + `list_tools`；从 `get_agent()` 拿到 manager（Agent 实例持有引用）

**API 设计**：

Memory（5 个）：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/memory` | - | `{memories: [{id, category, key, value, source, created_at, accessed_at}]}` | 全量 list |
| `POST` | `/api/memory` | `{category, key, value, source?}` | `SAME as item` | upsert（手动添加 / 修改 key 入口）|
| `PATCH` | `/api/memory/{id}` | `{value}` | `{updated: bool}` | 只改 value（保留 category/key/source）|
| `DELETE` | `/api/memory/{id}` | - | `{deleted: bool}` | 删单条 |
| `DELETE` | `/api/memory` | - | `{cleared: int}` | 清空全部（需前端确认）|

Rules（2 个）：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/rules` | - | `{text: str, path: str, exists: bool}` | 读 `.agenta/rules.md`；不存在 → text="" + exists=False |
| `PUT` | `/api/rules` | `{text: str}` | `{path, length, restart_required: true}` | 写文件；提醒重启或新 session 生效 |

Skills（6 个）：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/skills` | - | `{loaded: [{name, description, location, body, frontmatter_extra}], disabled: [...], failed: [{path, reason}]}` | 扫描结果含 body + disabled 数组 + frontmatter passthrough 字段 |
| `POST` | `/api/skills/reload` | - | `{loaded_count, disabled_count, failed_count}` | 重新扫盘 + 清 Agent 单例缓存（免重启 uvicorn）|
| `POST` | `/api/skills` | `{name, description, body, frontmatter_extra?}` | `SkillItem` (201) | 新建 skill：创建 `.agenta/skills/{name}/SKILL.md` |
| `PUT`  | `/api/skills/{name}` | `{description, body, frontmatter_extra?}` | `SkillItem` | 更新 SKILL.md（name 不可改，走 rename）。`frontmatter_extra=null` 保留磁盘原值；`{}` 清空；非空 dict 整体替换 |
| `POST` | `/api/skills/{name}/rename` | `{new_name}` | `SkillItem` | 改名：移动目录 + 同步 frontmatter `name:` 字段 + 迁移 disabled list 状态 |
| `DELETE` | `/api/skills/{name}` | - | 204 | 递归删除 `.agenta/skills/{name}/` 整个目录 |
| `POST` | `/api/skills/{name}/toggle` | `{enabled: bool}` | `{name, enabled}` | 启用 / 禁用（写 `.agenta/skills/disabled.json`，SKILL.md 不动）|

CRUD / toggle 后会自动 `cache_clear()` Agent 单例，**下一轮新对话立即生效**；当前对话因 system prompt 已下发不可撤回。

**禁用状态持久化**（详 design.md §3.5.5）：走"状态分离"模式 —— 禁用名单存独立的 `.agenta/skills/disabled.json`（JSON 数组），原子写（temp + rename）防并发交错，启动 scan 时自动清理已被删除的孤儿条目。SKILL.md 本身保持纯净（仅 name / description / 标准字段），可跨 agent 移植到 Claude.ai / VS Code / Cursor。

MCP（8 个）：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET`    | `/api/mcp/servers`              | - | `{servers: [{name, status, enabled, tool_count, error, command, args, env}]}` | server 列表（合并 config.json + disabled.json + 运行时状态）|
| `GET`    | `/api/mcp/tools`                | - | `{tools: [{name, description, inputSchema, server}]}` | 已连接 server 合流的工具清单 |
| `POST`   | `/api/mcp/servers`              | `{name, command, args, env}` | `MCPServer` | 新建 server 写 config.json + 实时 `start_one` |
| `PUT`    | `/api/mcp/servers/{name}`       | `{command, args, env}` | `MCPServer` | 更新（name 不可改，走 rename）；启用中的 server 自动 stop+start 让新配置生效 |
| `POST`   | `/api/mcp/servers/{name}/rename`| `{new_name}` | `MCPServer` | 改名：JSON key + disabled list 同步迁移；运行中先 stop_one 再 start_one |
| `DELETE` | `/api/mcp/servers/{name}`       | - | 204 | 从 config.json 移除 + `stop_one` + 清理 disabled 孤儿 |
| `POST`   | `/api/mcp/servers/{name}/toggle`| `{enabled}` | `{name, enabled}` | 启停：仅修改 disabled.json + 实时 `start_one` / `stop_one`，不动 config.json |
| `POST`   | `/api/mcp/reload`               | - | `{total, enabled, connected, failed}` | 重读 config.json + disabled.json，按差异 diff 启停 server |

**实时生效**：CRUD / toggle / reload 通过 `MCPManager.start_one` / `stop_one` 立即作用到运行子进程，不重启 uvicorn。
已发给 LLM 的 system prompt 不可撤回，但 tool 列表每轮 chat 重新拉取，下一轮立即看到新 server / 新 tool。

**禁用状态持久化**（与 Skills 同模式）：禁用名单存独立的 `.agenta/mcp/disabled.json`（JSON 数组），原子写防并发交错；
`config.json` 本身保持纯净（仅 server 字段），可跨客户端移植到 Cursor / Claude Desktop / VS Code Copilot。

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| Memory 是否允许新增 | 允许（POST upsert） | UI 偶尔想"手动加一条偏好"；upsert 内部已限制 category 必须在 `MEMORY_CATEGORIES`，安全 |
| Rules 写入是否热加载 | 不热加载 | `load_project_rules` 设计是启动一次；Web UI 写完 toast 提示 "重启或新 session 生效"，符合 rules_loader 既有约束 |
| Skills UI 是只读还是完整 CRUD | **完整 CRUD + 改名 + 启停 toggle + 一键 reload + 搜索 / 排序 / 批量启停**（对齐 Claude.ai / Cursor / VS Code Copilot 业内主流）| 早期只读版要"切到 IDE 改文件再重启 uvicorn"体验断裂；做完 CRUD 后 web UI 是完整闭环。disabled 状态用独立 JSON 持久化（SKILL.md 保持纯净）；编辑器用 CodeMirror 6 提供 markdown 语法高亮 + 三态预览（Edit / Split / Preview）|
| MCP servers UI 形态 | **完整 CRUD + 改名 + 启停 toggle + 一键 reload + 搜索 / 排序 + 三段分组**（对齐 Cursor / VS Code Copilot 主流） | 只读版要"切到 IDE 改 JSON 再重启 uvicorn"体验断裂；做完 CRUD 后 web UI 是完整入口。disabled 状态用独立 JSON（`.agenta/mcp/disabled.json`）持久化；编辑用结构化字段（`name / command / args[] / env{}`）+ ${VAR} 提示；`MCPManager.start_one` / `stop_one` / `reload` 让改动实时生效（无需重启进程） |
| 资源菜单区放哪 | Sidebar `[+ 新建会话]` + view 切换块 下方，sessions 列表 上方 | 跟需求文档 §4.2 布局对齐：资源菜单在会话列表上方 |
| 4 套资源的入口形态 | 4 个固定 icon-text 行（不可折叠） | 简洁；后续 Step 加更多资源时再考虑分组 |
| Memory category 标签 | 用现有 `CATEGORY_LABELS` 翻译 | 比 raw category id（如 `pref_style`）更友好 |
| 选中某资源时主区切换 | `activeView: 'chat' \| 'kb' \| 'memory' \| 'rules' \| 'skills' \| 'mcp'` | 6 种 view，沿用 Step 4 的 view 切换模式 |
| 4 个 panel 用统一容器壳 | 都用 `<ResourcePage title="..." subtitle="...">` 包一层 | 视觉一致；少重复代码 |

**实现内容**：

后端：

- `src/api/schemas/memory.py` 新建：Pydantic 4 个 schema
- `src/api/schemas/rules.py` 新建：2 个 schema
- `src/api/schemas/skills.py` 新建：3 个 schema（`SkillItem` / `SkillFailure` / `SkillsResponse`）
- `src/api/schemas/mcp.py` 新建：3 个 schema（`MCPServer` / `MCPTool` / 两个 list response）
- `src/api/routes/memory.py` 新建：5 个 endpoint
- `src/api/routes/rules.py` 新建：2 个 endpoint
- `src/api/routes/skills.py` 新建：1 个 endpoint
- `src/api/routes/mcp.py` 新建：2 个 endpoint
- `src/api/deps.py` 加 `get_user_memory_store` 依赖（从 Agent 拿 `_user_memory` 字段引用）
- `src/api/main.py` 注册 4 个新 router

前端：

- `src/types/resources.ts` 新建：Memory / Rules / Skills / MCP 共用类型
- `src/api/client.ts` 加：4 套资源对应的 client function
- `src/components/sidebar/Sidebar.tsx` 改：加资源菜单区（4 个固定行）+ `activeView` 扩展为 6 种
- `src/components/resources/ResourcePage.tsx` 新建：统一容器壳
- `src/components/resources/MemoryView.tsx` 新建：列表 + 编辑 value Dialog + 删除 / 清空
- `src/components/resources/RulesView.tsx` 新建：textarea + 保存按钮 + 重启提示
- `src/components/resources/SkillsView.tsx` 新建：loaded 列表 + failed 列表
- `src/components/resources/MCPView.tsx` 新建：servers 列表 + 各 server 工具数 + 全工具列表
- `src/App.tsx` 改：`activeView` 类型扩展 + 6 种 view 条件渲染

**修改 / 新增列表**：

| 操作 | 文件 |
|---|---|
| 新增 | `src/api/schemas/memory.py` / `rules.py` / `skills.py` / `mcp.py` |
| 新增 | `src/api/routes/memory.py` / `rules.py` / `skills.py` / `mcp.py` |
| 修改 | `src/api/deps.py` / `src/api/main.py` |
| 新增 | `tests/test_api_memory.py` / `test_api_rules.py` / `test_api_skills.py` / `test_api_mcp.py` |
| 新增 | `frontend/src/types/resources.ts` |
| 修改 | `frontend/src/api/client.ts` |
| 修改 | `frontend/src/components/sidebar/Sidebar.tsx` |
| 新增 | `frontend/src/components/resources/ResourcePage.tsx` |
| 新增 | `frontend/src/components/resources/MemoryView.tsx` |
| 新增 | `frontend/src/components/resources/RulesView.tsx` |
| 新增 | `frontend/src/components/resources/SkillsView.tsx` |
| 新增 | `frontend/src/components/resources/MCPView.tsx` |
| 修改 | `frontend/src/App.tsx` |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| 后端 Memory | mock `UserMemoryStore` 或用 tmp_path 真实例；测 list / upsert / patch / delete / clear / 404 不存在 |
| 后端 Rules | 用 tmp_path + monkeypatch `USER_RULES_FILE`；测 read 不存在 / read 已有 / write 创建 / write 覆盖 |
| 后端 Skills | 用 tmp_path 构造 SKILL.md 文件结构，monkeypatch `DEFAULT_SKILLS_DIR`；测 loaded / failed 分类 |
| 后端 MCP | mock `MCPManager.status` / `list_tools` 返回固定数据；测 list servers / list tools |
| 前端 | 不写 UT |

**人工验收步骤**：

1. 启动后端 + 前端；Sidebar 中间出现 4 个资源入口（记忆 / 规则 / Skills / MCP），跟"聊天" / "知识库"并列
2. 点 **记忆** → 主区列表显示当前所有 user_memory（之前 LLM 自动提取的应该有几十条）；hover 一行点 ✏️ → Dialog 改 value → 保存后列表更新；点 🗑️ → 确认 → 该行消失
3. 点 **规则** → textarea 显示 `.agenta/rules.md` 内容（若文件不存在则空）；改完点保存 → toast "已保存，新 session 生效"；切回聊天问一句新 session 应该按新 rules 行为
4. 点 **Skills** → 列表显示 `.agenta/skills/` 下扫到的 skills（loaded 部分含 name / description / location）+ failed 部分（reason）
5. 点 **MCP** → 列表显示 `.agenta/mcp.json` 里配置的 servers + 状态（connected / failed / connecting）+ tool_count + 错误信息；下方"工具清单"展示所有合流后的 tool
6. 切回聊天 → 在 chat session 里发"我喜欢什么颜色？" → LLM 应该能引用 memory 给答案
7. `pytest -q tests/test_api_memory.py tests/test_api_rules.py tests/test_api_skills.py tests/test_api_mcp.py` 全过

通过以上 6-7 条 = Step 5 完成。

**风险点 / 已知限制**：

| 项 | 说明 |
|---|---|
| Memory upsert 后 Agent 不立刻看到新条目 | Agent `MemoryManager.build_system_prompt` 在每次 `run` 开头重新加载 memory，所以**实际上立刻看到**。无风险 |
| Rules 写完不热加载 | `load_project_rules` 进程内只读一次。新 session 会重新走 `Agent.__init__` → 应能加载（取决于 `get_agent` 是 lru_cache 单例 → **实际上需要重启 uvicorn**）。UI toast 明示 |
| Skills 失败列表的 reason 是英文 prefix | 直接展示 `read_failed: xxx` / `yaml_parse_error: xxx`，用户可自行 google；不做翻译 |
| MCP server 实时状态可能 stale | `status` 返回的是 `_handles` 里的内存快照；server 在 web UI 查询瞬间挂了不会即时反映。下次发请求会重试时更新 |
| 多用户场景 memory 是全局共享 | 设计上 AgentA 是单用户工具；多用户隔离不在本期 scope |

**顺手 fix 的 pre-existing 问题**（Step 5 暴露 + 修复）：

| 问题 | 根因 | 修复 |
|---|---|---|
| `USER_MEMORY_ENABLED` 等 env var 在 uvicorn 进程里永远拿默认值 | `src/api/main.py` 没 `load_dotenv`；CLI 入口 `main.py` 有；Step 1~4 因为 KB 走 `ingest.py`（里面有 load_dotenv）侥幸没暴露 | `src/api/main.py` 顶部加 `load_dotenv(override=True)`，必须在 `import src.config` 之前 |
| MCP server 在 uvicorn 进程里**从未被启动** | `_bootstrap_mcp()` 只在 CLI `main.py` 启动时调；uvicorn 启动时没有等价 hook | 复制 `_bootstrap_mcp` 逻辑到 `src/api/main.py` 的 FastAPI `lifespan` async context manager |
| `UserMemoryStore.upsert` 返回值无法定位新插入条目 | 原签名返回 `None`；API 路由 `upsert_memory` 不得不复制 store 的 key 清洗逻辑去反查刚插入的条目，紧耦合 | `upsert` 改成显式 SELECT-then-UPDATE/INSERT 路径并返回 `id: int \| None`；API 路由直接用返回值查回创建后的条目 |
| `patch_memory` 路由复用了 `MemoryDeleteResponse` | 历史遗留，名字误导（patch 返回的不是删除信息） | 新增 `MemoryPatchResponse(updated: bool)`，路由 / 前端 / UT 三处同步修正 |

---

### 6.4.7 Step 6 - 系统配置 + 主题 + 反馈

**目标**：把"运行时的整体状态可见性"和"前端用户体验细节"补齐：

- 用户能看到当前 Agent 在用哪个 LLM provider / model、RAG 参数、各 feature flag（**只读**）
- 用户能切换暗色 / 浅色 / 跟系统主题
- 全局统一 toast 反馈系统，把 Step 4 / Step 5 各自抽的 toast 模板归一

完成后 Sidebar 多 1 个"设置"入口；Sidebar 底部右侧有主题切换按钮；所有"操作反馈"统一走 sonner toast。

**本 Step 不做**：

| 项 | 留给 / 不做 |
|---|---|
| **LLM 参数 runtime 编辑**（在 UI 改 provider / temperature / max_tokens） | 不做：要清 `get_agent` lru_cache + 写 `.env` + 处理在跑 session 的 race。改 `.env` + 重启 uvicorn 更可靠 |
| **日志查看**（前端实时 tail `logs/agenta.log`） | 不做：开发者向功能；需要第二条 SSE 流 + tail 滚动 / 暂停 / 过滤；用 `Get-Content -Wait` / `tail -f` 替代 |
| **错误 Boundary**（React 整页崩溃后的 fallback UI） | 不做：Step 1~5 没出过整页崩；遇到再加 |
| **API key 在 UI 显示**（即使脱敏） | 不做：直接不返回，避免任何泄漏路径 |
| **配置写入端点**（PUT /api/config） | 不做：跟 LLM 参数 runtime 编辑一起留给后续；只读视图 |

**对接现有代码的策略**：

- **后端**：新加 `GET /api/config` 直接从 `src.config` 模块拿 scalar，按分组打包；`api_key` 一律剔除
- **前端主题**：用 Tailwind 自带 `dark:` class variant + `class` 模式；CSS 已存在的 `.dark` 选择器都会自动 work，**无须改任何 view 组件**
- **前端 toast**：装 [sonner](https://sonner.emilkowal.ski/)（shadcn 推荐的 toast 库），抽 `lib/toast.ts` 提供 `toast.success / toast.error` helper；改 KB/Memory/Rules 用 sonner 替代各自 inline notice

**API 设计**：

| Method | Path | Body | Response | 含义 |
|---|---|---|---|---|
| `GET` | `/api/config` | - | 见下 | 只读：分组的当前配置摘要 |

返回结构（JSON）：

```json
{
  "llm": {
    "active_provider": "kimi",
    "model": "kimi-k2.5",
    "force_temperature": 0.6,
    "thinking_enabled": false,
    "thinking_budget": 8000,
    "available_providers": ["kimi", "qwen", "glm", "deepseek", "openai", ...]
  },
  "rag": {
    "top_k": 8,
    "k_per_source": 3,
    "active_embeddings": ["en", "zh"],
    "default_embedding": "en",
    "reranker_enabled": true,
    "reranker_model": "BAAI/bge-reranker-base",
    "query_rewrite_enabled": true,
    "ocr_fallback_enabled": true,
    "chunk_size": 600,
    "chunk_overlap": 100
  },
  "memory": {
    "enabled": true,
    "auto_extract": false,
    "max_chars": 1500
  },
  "rules": {
    "enabled": true,
    "file": ".agenta/rules.md",
    "max_chars": 4000
  },
  "mcp": {
    "enabled": true,
    "config_file": ".agenta/mcp/config.json",
    "connect_timeout_sec": 10,
    "call_timeout_sec": 30
  },
  "security": {
    "mode": "normal",
    "plan_permission_mode": false
  },
  "web": {
    "upload_dir": "./datasets/web_uploads",
    "max_upload_mb": 10
  },
  "log": {
    "level": "INFO"
  }
}
```

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 配置是否允许 UI 编辑 | 否 | 改 `.env` 重启更可靠；UI 写文件 + 清 cache 复杂度高 |
| API key 是否返回（脱敏后） | 不返回 | 即使脱敏（`sk-...xxx`）也是泄漏路径；用户自己看 `.env` 即可 |
| 主题用什么实现 | Tailwind `class` + 加 `.dark` class 到 `<html>` | shadcn 默认机制；现有所有组件 already 用 `dark:` variant 写好 |
| 主题存哪 | `localStorage` key `agenta-theme` | 不上后端；纯前端偏好 |
| 主题选项 | `light` / `dark` / `system` | `system` 用 `prefers-color-scheme` media query；默认 `system` |
| Toast 库 | sonner | shadcn 官方推荐；体积小（10KB）；支持 promise + dismiss + position |
| Toast 在哪挂载 | App.tsx 根；`<Toaster />` 一次 | 全局生效；视图组件直接 import `toast` 用 |
| KB / Memory / Rules 是否重构 | 重构 | KB 自抽的 toast 数组、Memory / Rules 自抽的 inline notice 都改成 sonner；统一交互体验 |
| 设置入口放哪 | Sidebar 资源菜单区底部 | 跟"记忆 / 规则 / Skills / MCP"并列；"⚙️ 设置"图标 |

**实现内容**：

后端：

- `src/api/schemas/config.py` 新建：嵌套 Pydantic 模型（按上面 JSON 分组）
- `src/api/routes/config.py` 新建：1 个 endpoint `GET /api/config`，逻辑直接拼 `src.config` 模块常量
- `src/api/main.py` 注册 config router

前端：

- `package.json` 加 `sonner`
- `src/types/config.ts` 新建：跟后端 schema 同构的 TS 类型
- `src/api/client.ts` 加 `getConfig()`
- `src/lib/toast.ts` 新建：sonner 的轻封装，统一 `success / error / info` API
- `src/lib/theme.ts` 新建：theme state hook + localStorage + apply class
- `src/components/settings/ThemeToggle.tsx` 新建：Sidebar 底部右侧按钮（3 态切换）
- `src/components/settings/SettingsView.tsx` 新建：只读分组展示
- `src/components/sidebar/Sidebar.tsx` 改：加 ⚙️ 设置 入口，`ViewKind` 加 `settings`
- `src/App.tsx` 改：根挂 `<Toaster />`；初始化 theme；条件渲染 SettingsView
- `src/components/chat/ChatView.tsx` 改：顶部加 ThemeToggle（或放 Sidebar 底部，按视觉决定）
- `src/components/kb/KnowledgeBaseView.tsx` 改：删自抽 toast 数组，改 `toast.success / toast.error`
- `src/components/resources/MemoryView.tsx` 改：error inline notice 改 toast（保留 loading / empty 内联文案）
- `src/components/resources/RulesView.tsx` 改：success / error notice 改 toast

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| `GET /api/config` | 测返回结构对齐（用 monkeypatch 改几个 config 常量后看 response 反映）；测 API key 字段**不出现**在响应里 |
| 主题 hook | 不写 UT；前端目测验收 |
| toast | 不写 UT；前端目测验收 |

**人工验收步骤**：

1. 启动后端 + 前端
2. Sidebar 底部应该有"⚙️ 设置"入口
3. 点击"⚙️ 设置" → 主区显示 LLM / RAG / Memory / Rules / MCP / Security / Web / Log 8 个分组；每组展示当前值；**确认看不到任何 API key**
4. Sidebar 底部右侧主题切换按钮（图标 Sun / Moon / Monitor）→ 点一次切到 dark；点第二次切到 light；点第三次切到 system
5. 在 dark 模式下逐个 View 切一遍（chat / KB / memory / rules / skills / mcp / settings），UI 全部正确显示成深色
6. 刷新浏览器 → 主题选择保留
7. 上传一个文档到 KB → 右下角弹 sonner toast（不再是 Step 4 自抽的 box）
8. 在 Memory view 改一条 value 保存 → toast 而不是 inline notice
9. 在 Rules view 改 rules 保存 → toast 提示"已保存，重启 uvicorn 或新 session 生效"
10. `pytest -q tests/test_api_config.py` 全过

通过以上 = Step 6 完成。

**风险点 / 已知限制**：

| 项 | 说明 |
|---|---|
| 主题在第一帧可能闪烁 | `useEffect` 内才 apply class；为减闪烁，在 `index.html` 加 inline script 提前 apply。可选优化 |
| sonner Toaster 渲染层级跟 shadcn Dialog 冲突 | 实测：sonner z-index 高于 dialog backdrop，二者并存 ok；如有问题加 `position="top-right"` 错位 |
| 后端 `available_providers` 字段从 `PROVIDER_CONFIGS.keys()` 拿 | 顺序非确定（dict 在 Python 3.7+ 保插入序）；前端按字母重排避免 UI 抖动 |
| `force_temperature` 可能是 `None` | TS 类型用 `number \| null`；显示成"—" |
| ~~`useTheme` 跨组件状态不同步~~ **（Step 7 review 已修）** | 首版 `useTheme` 用普通 hook 模式，`App.tsx` 跟 `ThemeToggle.tsx` 各持一份状态：切主题后 Toaster（在 App 渲染）颜色不跟随，需刷新页面。Step 7 review 改用 React Context（`src/lib/theme.tsx` 暴露 `ThemeProvider` + `useTheme`），`main.tsx` 顶层包一层 Provider，全应用共享主题状态 |

---

### 6.4.8 Step 7 - 业务面板（学习计划 / Quiz / SRS）

**目标**：把 Agent 已经在跑的 3 套业务数据**只读**展示到 UI，让用户不用问 LLM 就能直接看：

- **学习计划**：当前 active plan + 历史 plan 列表 + 每个 plan 的 tasks 完成进度
- **Quiz**：历史 quiz 列表 + 每张 quiz 的题目 / 用户答案 / 批改反馈
- **SRS**：到期 due 卡片队列 + 全部 cards 列表 + 单卡详情

完成后 Sidebar 多 3 个业务入口，跟"记忆 / 规则 / Skills / MCP / 设置"并列。

**本 Step 不做**：

| 项 | 留给 / 不做 |
|---|---|
| 在 UI 新建 plan / 出 quiz / 加 SRS 卡 | 留给 chat：LLM 已有 `create_study_plan` / `create_quiz` / `add_to_srs` 工具，自然语言触发更顺 |
| 在 UI 答 quiz / 复习 SRS（4 档评分） | 留给 chat：答题 / 复习是**多轮对话型**任务，UI 表单做不出反馈节奏 |
| 在 UI 把 plan abandon / quiz archive | 留给 chat：低频操作；chat 让 LLM 调对应工具即可 |
| 编辑 task status | 留给 chat：`update_study_progress` 工具已存在 |
| 跨 plan 切 active | 留给 chat |

**对接现有代码的策略**：

- **LearningPlan**：复用 `LearningPlanStore.list_plans` / `get_active` / `get_plan_with_tasks`
- **Quiz**：复用 `QuizStore.list_quiz_sets` / `get_quiz_with_questions`
- **SRS**：复用 `SRSStore.list_cards` / `list_due` / `get_card`
- 3 套 store 已经有 `get_shared_store()` 模块级单例 helper；API deps 直接用，避免 API 层另起 connection

**API 设计**：

学习计划（3 个）：

| Method | Path | Response | 含义 |
|---|---|---|---|
| `GET` | `/api/plans` | `{plans: [PlanSummary]}` | 列全部非 abandoned plan（带 task_count / done_count）|
| `GET` | `/api/plans/active` | `Plan \| null` | 当前 active plan（含 tasks） |
| `GET` | `/api/plans/{plan_id}` | `Plan` | 单 plan + 全 tasks；404 不存在 |

Quiz（2 个）：

| Method | Path | Response | 含义 |
|---|---|---|---|
| `GET` | `/api/quizzes` | `{quizzes: [QuizSetSummary]}` | 列非 archived quiz_set（按时间倒序） |
| `GET` | `/api/quizzes/{quiz_set_id}` | `QuizSet` | quiz_set + 全 questions（含 user_answer / score / feedback） |

SRS（3 个）：

| Method | Path | Response | 含义 |
|---|---|---|---|
| `GET` | `/api/srs/due` | `{cards: [Card]}` | 到期 due 队列（按 next_review_at 升序） |
| `GET` | `/api/srs/cards` | `{cards: [Card]}` | 全 cards（非 archived） |
| `GET` | `/api/srs/cards/{card_id}` | `Card` | 单卡详情 |

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 是否允许 UI 修改业务数据 | 否 | 创建 / 完成 / 评分都依赖 LLM 推理（出题、批改、SRS 间隔计算）；UI 直接调底层 store 绕过这套推理，反而打破语义 |
| Plan 列表是否含 abandoned | 否 | 跟 `list_plans` 默认行为一致；UI 简洁 |
| Quiz 列表是否含 archived | 否 | 同上 |
| SRS 列表是否含 archived | 否 | 同上 |
| Due 队列上限 | 走 store 默认（`SRS_DEFAULT_DUE_QUERY_LIMIT=20`） | 跟 CLI `/srs due` 一致 |
| 是否在 list response 里附带 question / task 全文 | 否 | list 只返摘要；详情走 `/{id}` —— 减少首屏 payload |
| Sidebar 业务入口顺序 | 学习计划 → Quiz → SRS | 用户学习闭环：计划 → 出题 → 复习 |
| 业务入口放哪 | 跟资源菜单区合并 | 当前 sidebar 顶部只有 chat / KB / 资源 / 设置；业务跟资源同属"非聊天"功能，放一起；插在 MCP 和 设置 之间 |

**实现内容**：

后端：

- `src/api/schemas/plan.py` 新建：`PlanTask` / `PlanSummary` / `Plan` / `PlanListResponse`
- `src/api/schemas/quiz.py` 新建：`QuizQuestion` / `QuizSetSummary` / `QuizSet` / `QuizListResponse`
- `src/api/schemas/srs.py` 新建：`SRSCard` / `SRSCardListResponse`
- `src/api/routes/plans.py` 新建：3 个 endpoint
- `src/api/routes/quizzes.py` 新建：2 个 endpoint
- `src/api/routes/srs.py` 新建：3 个 endpoint
- `src/api/deps.py` 加 `get_plan_store` / `get_quiz_store` / `get_srs_store`（用各自的 `get_shared_store()`）
- `src/api/main.py` 注册 3 个新 router

前端：

- `src/types/business.ts` 新建：3 套业务的 TS 类型
- `src/api/client.ts` 加 8 个 API 函数
- `src/components/business/PlansView.tsx` 新建：左侧 plan list，右侧 detail（tasks 按 stage 分组）
- `src/components/business/QuizzesView.tsx` 新建：左侧 quiz list，右侧 detail（questions 含答案对比）
- `src/components/business/SRSView.tsx` 新建：上方 due 队列，下方全卡列表；点卡进 detail
- `src/components/sidebar/Sidebar.tsx` 改：`ViewKind` 加 `plans` / `quizzes` / `srs`；3 个入口
- `src/App.tsx` 改：3 个新 view 条件渲染

**修改 / 新增列表**：

| 操作 | 文件 |
|---|---|
| 新增 | `src/api/schemas/plan.py` / `quiz.py` / `srs.py` |
| 新增 | `src/api/routes/plans.py` / `quizzes.py` / `srs.py` |
| 修改 | `src/api/deps.py` / `src/api/main.py` |
| 新增 | `tests/test_api_plans.py` / `test_api_quizzes.py` / `test_api_srs.py` |
| 新增 | `frontend/src/types/business.ts` |
| 修改 | `frontend/src/api/client.ts` |
| 新增 | `frontend/src/components/business/PlansView.tsx` / `QuizzesView.tsx` / `SRSView.tsx` |
| 修改 | `frontend/src/components/sidebar/Sidebar.tsx` / `src/App.tsx` |

**UT 策略**：

| 层 | 怎么测 |
|---|---|
| Plan API | 用 tmp_path 真实 SQLite + LearningPlanStore；create_plan + add_tasks 注数据；测 list / active / detail / 404 |
| Quiz API | 用 tmp_path + QuizStore；create_quiz_set + add_questions 注数据；测 list / detail / 404 |
| SRS API | 用 tmp_path + SRSStore；add_card 注数据；测 due 队列（含时间过滤）/ list / detail / 404 |
| 前端 | 不写 UT；目测验收 |

**人工验收步骤**：

1. 启动后端 + 前端；Sidebar 资源区出现 **学习计划 / Quiz / SRS** 3 个新入口（在 MCP 和 设置 之间）
2. 在 chat 里发一句"做一份 ML 学习计划"让 LLM 调 `create_study_plan` 工具创建 plan
3. 切到 **学习计划** view → 左侧出现新 plan；点进去右侧显示 stages + tasks，active plan 应该高亮
4. 在 chat 里发"考我 5 道 attention 机制的题"让 LLM 调 `create_quiz` 工具
5. 切到 **Quiz** view → 列表出现新 quiz；点进去显示所有 question + 当前未答状态
6. 回 chat 答题，让 LLM 调 `grade_quiz` 批改；再切回 Quiz view，应该看到 user_answer / score / feedback
7. 在 chat 里发"把刚才错的题进 SRS"或手动 `add_to_srs`
8. 切到 **SRS** view → 上方"到期"队列；下方全卡列表；点卡详情看 SM-2 字段
9. `pytest -q tests/test_api_plans.py tests/test_api_quizzes.py tests/test_api_srs.py` 全过

通过以上 = Step 7 完成 → **整个 iter_4_UI 收尾**。

**风险点 / 已知限制**：

| 项 | 说明 |
|---|---|
| `LearningPlanStore.get_shared_store()` 等 3 个单例 manager 复用进程内连接 | API 跟 Agent 用同一 store；多线程下 SQLite 已有 lock；OK |
| Quiz 的 grading_summary 字段未在 API 返回 | `_row_to_quiz_set` 已含 `total_score`；前端按需展示足够 |
| SRS 的 `next_review_at` 是 ISO 字符串 + 本地时区 | 前端不做时区转换，直接展示；跟 CLI 行为对齐 |
| SRS 的 `source_ref` 是 int（quiz_question id 或 NULL） | schema 用 `int \| None`；前端 `number \| null`。首版误写成 str，smoke 时被 500 抓到已修 |
| Plan 的 tasks 按 stage_idx + order_idx 排序 | API 直接信任 store 顺序；前端按 stage_idx 分组渲染 |
| 数据为空时 UI 显示 | 各 view 提供 "暂无 X，去 chat 里问 LLM 创建" 引导文案 |

---