# 1. 缩写表

按领域分类整理 AgentA 项目中用到的英文缩写 / 简称。

## 1.1. RAG / 检索 / Embedding

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **KB** | Knowledge Base | 知识库 | 向量库 + 关键词索引 |
| **RAG** | Retrieval-Augmented Generation | 检索增强生成 | 先检索资料再让 LLM 生成答案 |
| **BM25** | Best Matching 25 | 最佳匹配 25 | 经典关键词检索算法 |
| **RRF** | Reciprocal Rank Fusion | 倒数排名融合 | 合并多路召回排序 |
| **HyDE** | Hypothetical Document Embeddings | 假设性文档嵌入 | 让 LLM 先编一段答案再检索 |
| **LLM** | Large Language Model | 大语言模型 | 对话、改写、出题、评委、Agent 推理的核心 |
| **BGE** | BAAI General Embedding | 智源通用嵌入 | 智源开源的 embedding / reranker 模型族（bge-m3、bge-reranker-base 等）|
| **BAAI** | Beijing Academy of Artificial Intelligence | 北京智源人工智能研究院 | BGE 模型出品方，模型 ID 前缀 `BAAI/` |
| **MTEB** | Massive Text Embedding Benchmark | 大规模文本嵌入评测 | embedding 选型参考榜单 |
| **ANN** | Approximate Nearest Neighbor | 近似最近邻 | 向量检索的目标 |
| **HNSW** | Hierarchical Navigable Small World | 分层可导航小世界图 | ChromaDB 默认向量索引算法 |
| **TF-IDF** | Term Frequency–Inverse Document Frequency | 词频-逆文档频率 | BM25 的理论基础 |
| **IDF** | Inverse Document Frequency | 逆文档频率 | TF-IDF / BM25 的组成项 |
| **OCR** | Optical Character Recognition | 光学字符识别 | 扫描版 PDF 无文本层时的兜底 |
| **ONNX** | Open Neural Network Exchange | 开放神经网络交换格式 | RapidOCR 运行时依赖 |
| **SHA1** | Secure Hash Algorithm 1 | 安全散列算法 1 | `content_sha1` 驱动入库幂等跳过 |
| **LRU** | Least Recently Used | 最近最少使用 | query 改写等进程级缓存淘汰策略 |
| **HF** | Hugging Face | （原名，无中译） | 模型托管平台；模型下载与缓存（`HF_ENDPOINT`）|

## 1.2. 评估指标

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **MRR** | Mean Reciprocal Rank | 平均倒数排名 | 正确结果排名倒数的平均 |
| **nDCG** | normalized Discounted Cumulative Gain | 归一化折损累积增益 | 更细的排序评估指标 |
| **hit@k** | Hit rate at k | 命中率@k | 前 k 条是否含正确 chunk |
| **recall@k** | Recall at k | 召回率@k | 前 k 条覆盖的相关 chunk 比例 |

## 1.3. Agent / LLM 框架

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **ReAct** | Reasoning + Acting | 推理-行动 | 推理与工具调用交织的 Agent 主循环范式（默认 Agent）|
| **CoT** | Chain of Thought | 思维链 | 让 LLM 分步推理的提示范式 |
| **GPT** | Generative Pre-trained Transformer | 生成式预训练变换器 | OpenAI 模型系列 |
| **GLM** | General Language Model | 通用语言模型 | 智谱模型系列（glm-4.6 等）|

## 1.4. 学习业务（复习 / 测验）

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **SRS** | Spaced Repetition System | 间隔重复复习系统 | 按记忆曲线安排复习时间 |
| **SM-2** | SuperMemo 2 | SuperMemo 2 算法 | SRS 调度算法，决定下次复习间隔 |
| **FSRS** | Free Spaced Repetition Scheduler | 自由间隔重复调度器 | 更高级的 SRS 算法（MVP 未采用，文档对比用）|
| **MCQ** | Multiple Choice Question | 选择题 | 测验题型（单选 / 多选本地判分）|

## 1.5. Web / API / 前端

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **API** | Application Programming Interface | 应用编程接口 | FastAPI `/api/*` 路由、前后端契约 |
| **REST** | Representational State Transfer | 表述性状态转移 | HTTP 接口风格 |
| **CLI** | Command Line Interface | 命令行界面 | 无 GUI 调试入口 |
| **UI** | User Interface | 用户界面 | React Web 前端 |
| **HTTP / HTTPS** | HyperText Transfer Protocol (Secure) | 超文本传输协议（安全版）| Web 通信基础协议 |
| **CORS** | Cross-Origin Resource Sharing | 跨域资源共享 | dev 期允许前端跨域调 API |
| **ASGI** | Asynchronous Server Gateway Interface | 异步服务网关接口 | uvicorn 运行 FastAPI 的标准 |
| **DOM** | Document Object Model | 文档对象模型 | 浏览器页面结构 |
| **JSX / TSX** | JavaScript / TypeScript XML | JS / TS 内嵌 XML | React 组件语法（TS 版为 TSX）|
| **JSON** | JavaScript Object Notation | JS 对象表示法 | 轻量数据交换格式；API body、tool schema |
| **TS** | TypeScript | （原名，无中译） | JavaScript 超集 + 静态类型；编译产物仍是 JS |
| **CSS** | Cascading Style Sheets | 层叠样式表 | Tailwind CSS 样式工具链 |
| **WCAG** | Web Content Accessibility Guidelines | 网页内容无障碍指南 | 对比度 AA 验收标准 |
| **a11y** | accessibility | 无障碍 | a + 中间 11 个字母 + y 的简写 |
| **STT** | Speech To Text | 语音转文字 | 麦克风听写（backlog）|
| **TTS** | Text To Speech | 文字转语音 | 朗读功能（backlog）|

## 1.6. 数据库 / 存储 / 缓存

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **DB** | Database | 数据库 | chroma / sqlite / bm25 存储布局 |
| **SQL** | Structured Query Language | 结构化查询语言 | 关系库查询语言 |
| **RDBMS** | Relational Database Management System | 关系型数据库管理系统 | 如 SQLite |
| **CRUD** | Create, Read, Update, Delete | 增删改查 | Store 层职责划分 |
| **UUID** | Universally Unique Identifier | 通用唯一标识符 | session_id 等 |
| **TTL** | Time To Live | 存活时间 | 缓存 / session 过期 |
| **FK** | Foreign Key | 外键 | 表间级联关系 |

## 1.7. 安全 / 认证 / 联网

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **OWASP** | Open Worldwide Application Security Project | 开放式全球应用安全项目 | 发布 LLM Top 10 等安全规范 |
| **LLM01** | OWASP LLM Top 10 第 01 项 | LLM 风险第 01 项 | Prompt Injection（LLM 应用首要风险）|
| **SSRF** | Server-Side Request Forgery | 服务端请求伪造 | 诱导 server 访问内网 / file:// 等 |
| **XSS** | Cross-Site Scripting | 跨站脚本攻击 | session cookie 设 HttpOnly 防读取 |
| **TLS** | Transport Layer Security | 传输层安全协议 | HTTPS 底层 |
| **OAuth** | Open Authorization | 开放授权 | MCP 远程鉴权方案 |
| **Serper** | (Google Search API 服务名) | （服务名，无中译） | 联网搜索服务；`web_search` 调 `google.serper.dev` |

## 1.8. MCP / 进程通信

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **MCP** | Model Context Protocol | 模型上下文协议 | Anthropic 2024-11 开源标准，LLM 应用 ↔ 外部能力的通用接口 |
| **JSON-RPC** | JSON Remote Procedure Call | JSON 远程过程调用 | MCP 底层消息格式 |
| **SSE** | Server-Sent Events | 服务器推送事件 | HTTP 单向流；Web 聊天流式输出 + MCP 旧 transport |
| **stdio** | Standard Input/Output | 标准输入输出 | 本地 MCP server 默认通信方式 |
| **IPC** | Inter-Process Communication | 进程间通信 | MCP host 与 server 跨进程 |

## 1.9. 工程 / 测试 / 工具链

| 缩写 | 全称 | 中文名 | 含义 |
|---|---|---|---|
| **SDK** | Software Development Kit | 软件开发工具包 | 封装好的开发库 / 工具 |
| **UT** | Unit Test | 单元测试 | `tests/` 下 pytest 用例 |
| **CI** | Continuous Integration | 持续集成 | GitHub Actions 回归门禁 |
| **CD** | Continuous Delivery / Deployment | 持续交付 / 部署 | 自动化发布流程 |
| **PR** | Pull Request | 合并请求 | 代码评审与合并 |
| **P0 / P1 / P2** | Priority 0 / 1 / 2 | 优先级 0 / 1 / 2 | 优先级分级（P0 最高）|
| **env** | Environment（.env） | 环境变量 | 全局配置源，与 `config.py`、UI overrides 同步 |
| **Node.js** | (无缩写，原名) | （原名，无中译） | JavaScript / TypeScript 服务端运行时（基于 V8 引擎），跟 Python 解释器同生态位 |
| **npm** | Node Package Manager | Node 包管理器 | Node.js 官方包管理器 + 包仓库（pypi.org 的对应物）|
| **npx** | Node Package eXecute | Node 包执行器 | npm 5.2+ 自带的"装包 + 跑命令"一步走工具（类比 Python 的 `pipx run`）|
| **WSL** | Windows Subsystem for Linux | Windows 的 Linux 子系统 | Windows 内置的 Linux 子系统（v2 是完整内核）|
