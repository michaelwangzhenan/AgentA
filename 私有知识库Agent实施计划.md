# 私有知识库 Agent 实施计划

> 使用 Python 从零搭建，VSCode + GitHub Copilot 辅助开发，支持多格式文档，LLM 可灵活切换。

---

## 一、项目概述

### 目标

构建一个本地运行的私有知识库 Agent，能够：

- 解析多种格式的私有文档（MD、TXT、PDF、Word、PPT、Excel、HTML、网页 URL等）
- 将文档内容向量化存储到本地数据库
- 接受自然语言提问，自动检索相关文档片段
- 调用 LLM API 结合检索结果生成准确答案
- 支持一键切换不同 LLM 提供商（免费模型 → 高性能模型）

### 核心理念

- **开发阶段**：使用Kimi(MOONSHOT_API_KEY)跑通全链路逻辑
- **生产阶段**：通过统一接口切换至 GPT-4o / Claude 等高性能模型

---

## 二、整体架构

本项目包含两个独立流程，需要先理解它们的关系：

### 流程一：离线预处理（提前一次性完成，文档有更新时重新运行）

```
私有文档（MD / TXT / PDF / DOCX / PPTX / XLSX / HTML）
         ↓ parser.py 解析
       纯文本字符串
         ↓ ingest.py 分块（Chunking）
       文本片段（每块约 600 字符）
         ↓ sentence-transformers 向量化（Embedding）
       向量数据
         ↓ 存储
       ChromaDB 本地向量数据库 ✅
```

### 流程二：实时问答（每次用户提问时触发）

```
用户输入（自然语言问题）
         ↓
  ┌──────────────────────────────────────┐
  │           Agent 主控层               │
  │  · 理解意图                          │
  │  · 规划工具调用                       │
  │  · 整合结果生成回答                   │
  │  · 驱动引擎：LLM（可切换）            │
  └──────────┬───────────────────────────┘
             │ Function Calling
   ┌──────────┼──────────────┐
   ▼          ▼              ▼
search_    fetch_url     (可扩展更多工具)
knowledge  (实时网页抓取)
   │
   ▼
┌─────────────────────────┐
│       RAG 检索层         │
│  · 向量化用户问题         │
│  · 相似度检索            │
│  · 返回 Top-K 文档片段    │
└──────────┬──────────────┘
           │
┌──────────▼──────────────┐
│  ChromaDB 本地向量数据库  │
│  （数据来自离线预处理，   │
│   查询时只读，不再解析）  │
└─────────────────────────┘
```

---

## 三、技术选型

### 3.1 LLM 接口层

| 提供商 | 用途 | 说明 |
|--------|------|------|
| Kimi | 开发/测试 | 免费额度大，速度快 |
| Ollama（本地） | 离线开发 | 完全免费，数据不出本地 |
| OpenAI GPT-5 | 生产环境 | 高性能，按量计费 |
| Anthropic Claude 4.6 | 生产环境 | 高性能，按量计费 |

> 所有 Provider 统一通过 OpenAI SDK 格式调用（Kimi / Ollama 均兼容），切换只需改一行配置。

### 3.2 向量数据库

| 工具 | 选型理由 |
|------|---------|
| **ChromaDB** | 纯本地，零配置，Python 原生，适合个人/小团队 |

### 3.3 嵌入模型（Embedding）

| 工具 | 说明 |
|------|------|
| `sentence-transformers` | 本地运行，完全免费，无需 API |
| `all-MiniLM-L6-v2` | 默认英文模型，轻量快速 |
| `BAAI/bge-small-zh` | 中文场景替换此模型 |

### 3.4 文档解析库

| 格式 | 库 |
|------|----|
| PDF | `pypdf` |
| Word (.docx) | `python-docx` |
| PPT (.pptx) | `python-pptx` |
| Excel (.xlsx) | `openpyxl` |
| HTML / 网页 | `beautifulsoup4` + `requests` |
| MD / TXT | 原生 Python |

### 3.5 开发工具

| 工具 | 用途 |
|------|------|
| VSCode | 主力编辑器 |
| GitHub Copilot | AI 编码辅助 |
| Python 3.10+ | 运行环境 |
| python-dotenv | 环境变量管理 |

---

## 四、项目目录结构

```
knowledge-agent/
│
├── .env                        # API Keys（不提交 Git）
├── .env.example                # 环境变量模板
├── .gitignore
├── requirements.txt            # 依赖清单
├── config.py                   # 全局配置（模型切换入口）
├── main.py                     # CLI 入口
│
├── llm/
│   └── provider.py             # LLM 统一调用接口
│
├── rag/
│   ├── parser.py               # 多格式文档解析
│   ├── ingest.py               # 文档入库（解析 → 分块 → 向量化 → 存储）
│   └── retriever.py            # 向量检索
│
├── agent/
│   ├── agent.py                # Agent 主控逻辑（ReAct 循环）
│   └── tools.py                # 工具定义与执行
│
├── docs/                       # 你的私有文档放这里
│   └── (your documents)
│
└── chroma_db/                  # ChromaDB 向量库（自动生成，不提交 Git）
```

---

## 五、关键模块说明

### 5.1 `config.py` — 模型切换核心

这是整个项目"可替换模型"设计的核心文件。通过修改 `.env` 中的 `LLM_PROVIDER` 变量，无需改动任何业务代码即可切换 LLM：

```
LLM_PROVIDER=gemini    # 开发阶段：免费
LLM_PROVIDER=ollama    # 离线阶段：本地
LLM_PROVIDER=openai    # 生产阶段：高性能
LLM_PROVIDER=claude    # 生产阶段：高性能
```

### 5.2 `llm/provider.py` — 统一接口

封装统一的 `chat()` 函数，上层业务代码只调用这一个函数，不感知底层 Provider 差异。支持传入 `tools` 参数以启用 Function Calling。

### 5.3 `rag/parser.py` — 文档解析

**仅在离线预处理阶段使用**，负责将各种格式的本地文档转换为纯文本字符串，供 `ingest.py` 后续分块和向量化使用。每种格式对应一个解析分支，易于扩展新格式。

注意：网页 URL 的实时抓取由 `agent/tools.py` 中的 `fetch_url` 工具负责，不经过此模块。`parser.py` 只处理本地文件。

### 5.4 `rag/ingest.py` — 文档入库流程

```
扫描 docs/ 目录
    ↓
逐文件解析为纯文本
    ↓
分块（Chunking）：每块 600 字符，重叠 100 字符
    ↓
向量化（Embedding）：本地 sentence-transformers
    ↓
存入 ChromaDB（upsert，支持重复运行）
```

### 5.5 `agent/agent.py` — ReAct 循环

Agent 的核心执行逻辑，遵循 **ReAct（Reason + Act）** 模式：

```
接收用户问题
    ↓
LLM 推理：需要调用哪个工具？
    ↓
执行工具，获取结果
    ↓
LLM 继续推理：结果是否足够？
    ↓
（循环，直到 LLM 决定无需再调用工具）
    ↓
生成最终回答
```

---

## 六、详细实现步骤

### Phase 0：环境准备

- [ ] 项目目录： 使用当前工程 AgentA
- [ ] 创建并激活虚拟环境
- [ ] 安装所有依赖
- [ ] 验证 `.env` 文件

---

### Phase 1：配置层

**目标**：建立可切换模型的配置体系

- [ ] 编写 `config.py`：定义各 Provider 的 base_url、model、api_key
- [ ] 编写 `llm/provider.py`：实现统一 `chat()` 函数
- [ ] 编写简单测试脚本，验证 LLM 使用 kimi api 调用成功
- [ ] 预留接口支持 OpenAI、Ollama 等其他 Provider 的快速切换

---

### Phase 2：文档解析层

**目标**：支持所有本地文件格式解析为纯文本（仅供离线入库使用）

- [ ] 编写 `rag/parser.py`：
  - [ ] 解析 `.md` / `.txt`（原生读取）
  - [ ] 解析 `.html`（BeautifulSoup 提取正文）
  - [ ] 解析 `.pdf`（pypdf）
  - [ ] 解析 `.docx`（python-docx）
  - [ ] 解析 `.pptx`（python-pptx，遍历所有 slide 的 shape）
  - [ ] 解析 `.xlsx`（openpyxl，每行转为 `列1 | 列2 | ...` 格式）
- [ ] 编写解析测试脚本，逐格式验证输出是否正常
- [ ] 该功能有独立命令执行，每次文档更新后运行一次即可，无需频繁调用

> **说明**：网页 URL 不在此处处理。`parser.py` 只负责本地文件。URL 的实时抓取是 Agent 运行时通过 `fetch_url` 工具完成的（见 Phase 4）。如需将网页内容提前入库，可在 `ingest.py` 中单独扩展一个 URL 列表批量导入功能。

---

### Phase 3：RAG 向量化入库

**目标**：将文档内容存入向量数据库，支持语义检索

- [ ] 编写 `rag/ingest.py`：
  - [ ] 实现 `chunk_text()` 函数  $env:HF_ENDPOINT="https://hf-mirror.com"
  .venv\Scripts\python -m rag.ingest：固定长度分块，带重叠
  - [ ] 实现 `ingest_all()` 函数：扫描 `docs/` 目录，逐文件解析 → 分块 → 向量化 → upsert
  - [ ] 使用文件路径 + 块序号的 MD5 作为唯一 ID，支持重复运行不重复入库
- [ ] 编写 `rag/retriever.py`：
  - [ ] 实现 `search(query, top_k=5)` 函数：向量化 query → 检索 → 格式化返回结果（含来源文件名）
- [ ] 将几份测试文档放入 `docs/`，运行 `python rag/ingest.py` 验证入库
- [ ] 手动调用 `search()` 验证检索结果是否相关

---

### Phase 4：工具层（Tools）

**目标**：定义 Agent 可调用的工具，遵循 OpenAI Function Calling 格式

- [ ] 编写 `agent/tools.py`：
  - [ ] 定义 `TOOLS` 列表（JSON Schema 格式）：
    - `search_knowledge`：搜索私有知识库
    - `fetch_url`：抓取网页内容
  - [ ] 实现 `execute_tool(name, args)` 函数：根据工具名路由执行
- [ ] 手动测试每个工具函数，确认返回格式正确

---

### Phase 5：Agent 主控逻辑

**目标**：实现完整的 ReAct Agent 循环

- [ ] 编写 `agent/agent.py`：
  - [ ] 定义 `SYSTEM_PROMPT`：指导 Agent 优先调用知识库，其次抓取网页
  - [ ] 实现 `run(user_input)` 函数：
    - 构建初始 messages
    - 循环调用 LLM
    - 检测是否有 `tool_calls`
    - 若有：执行工具，将结果追加到 messages，继续循环
    - 若无：输出最终回答，退出循环
  - [ ] 加入调试日志：打印每次工具调用的名称和参数

---

### Phase 6：入口与整合测试

- [ ] 编写 `main.py`：实现简单的 CLI 对话循环
- [ ] 端到端测试：
  - [ ] 提问知识库内有答案的问题，验证 RAG 检索生效
  - [ ] 提问知识库内没有的问题，验证 Agent 会调用 `fetch_url`
  - [ ] 提问需要多步推理的问题，观察 Agent 多轮工具调用行为
- [ ] 修复发现的 Bug

---

### Phase 7：模型切换验证

- [ ] 修改 `.env` 中 `LLM_PROVIDER=openai`，填入 OpenAI Key
- [ ] 重新运行，验证完全相同的功能正常工作
- [ ] （可选）部署 Ollama，切换为本地模型，验证离线运行

---

### Phase 8：优化与扩展（按需）

- [ ] **中文优化**：将嵌入模型替换为 `BAAI/bge-small-zh`
- [ ] **文档自动同步**：使用 `watchdog` 监听 `docs/` 目录变化，自动增量入库
- [ ] **对话记忆**：将 messages 历史持久化到 SQLite，支持多轮上下文
- [ ] **Web UI**：接入 Gradio 或 Streamlit，提供图形界面
- [ ] **URL 批量导入**：支持通过配置文件批量爬取指定网页入库
- [ ] **重排序（Reranking）**：在检索结果上增加 Cross-Encoder 重排，提升精度

---

## 七、开发建议（VSCode + Copilot 使用技巧）

### 写好注释让 Copilot 更懂你

在函数前写清楚注释，Copilot 补全质量会大幅提升：

```python
# 将文档文本按 size 字符分块，相邻块之间有 overlap 字符重叠
# 返回字符串列表
def chunk_text(text: str, size: int = 600, overlap: int = 100) -> list[str]:
```

### 推荐的开发顺序

每完成一个 Phase，先单独测试该模块，确认无误后再进入下一 Phase。不要一次性写完所有代码再统一测试。

---

## 八、依赖清单（ requirements.txt）

```
chromadb
sentence-transformers
openai
pypdf
python-docx
python-pptx
openpyxl
beautifulsoup4
lxml
requests
python-dotenv
```

> **说明**：
> - `lxml` 是 BeautifulSoup 解析 HTML 的推荐后端，比默认的 `html.parser` 更稳定，需显式安装。
> - 所有 LLM Provider（Gemini、OpenAI、Ollama）均通过 `openai` SDK 调用，因为它们兼容 OpenAI 接口格式。Claude 原生 API 格式与 OpenAI 不同，有两种接入方式：①通过 AWS Bedrock / Google Vertex AI 的 OpenAI 兼容接口调用；②直接安装 `anthropic` SDK 在 `provider.py` 中单独适配。本项目 Phase 7 采用方式②，需额外安装 `anthropic` 库。

---
