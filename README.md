# AgentA

本地运行的私有知识库 Agent，支持多格式文档解析、双语向量化存储与自然语言问答，LLM 可一键切换。

## 功能特性

- 解析多种格式文档：MD / TXT / HTML / PDF / DOCX / PPTX / XLSX
- 本地向量化存储（ChromaDB），数据不出本地
- **双语 Embedding**：英文用 `all-MiniLM-L6-v2`，中文用 `BAAI/bge-small-zh`，各自独立 collection，检索时自动合并排序
- 自然语言提问，ReAct Agent 自动检索相关文档片段后调用 LLM 生成答案
- 支持网页内容实时抓取（`fetch_url` 工具）作为知识库补充
- 支持一键切换 LLM：Kimi / OpenAI / DeepSeek / Grok / Ollama / Claude

## 当前进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 0 | 环境准备（Python 3.11、venv、依赖） | ✅ 完成 |
| 1 | 配置层（6 个 LLM Provider 切换） | ✅ 完成 |
| 2 | 文档解析（7 种格式） | ✅ 完成 |
| 3 | RAG 向量入库 + 检索（双语 collection） | ✅ 完成 |
| 4 | 工具层（search_knowledge / fetch_url） | ✅ 完成 |
| 5 | Agent ReAct 主循环 | ✅ 完成 |
| 6 | CLI 入口（main.py）+ 完整测试套件 | ✅ 完成 |
| 7 | 多 Provider 切换验证（OpenAI / Ollama） | ⬜ 待验证 |
| 8 | 优化扩展（见下方待优化项） | 🔄 进行中 |

---

## 快速开始

### 1. 环境准备

```bash
# 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 安装依赖
pip install -r requirements.txt

# 安装 Embedding 模型依赖（体积约 2GB，首次安装较慢）
pip install sentence-transformers
```

### 2. 配置环境变量

直接编辑 `.env`，填入对应的 API Key：

```ini
LLM_PROVIDER=kimi          # 切换 LLM：kimi / openai / deepseek / grok / ollama / claude
MOONSHOT_API_KEY=sk-...
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
EMBEDDING_MODEL=en         # 默认英文模型；中文文档改为 zh
TRANSFORMERS_OFFLINE=1     # 模型缓存后保持离线，防止每次启动联网
HF_ENDPOINT=https://hf-mirror.com  # 国内 HF 镜像（首次下载模型时使用）
```

### 3. 下载 Embedding 模型（首次需联网，之后永久本地缓存）

模型文件缓存在 `~/.cache/huggingface/hub/`，**不在 `.venv` 里**，这是 HuggingFace 的标准缓存机制。

```powershell
# 英文模型（~90MB）
$env:TRANSFORMERS_OFFLINE="0"; $env:HF_ENDPOINT="https://hf-mirror.com"
.venv\Scripts\python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); print('OK')"

# 中文模型（~96MB，可选）
.venv\Scripts\python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh'); print('OK')"
```

下载完成后将 `.env` 中 `TRANSFORMERS_OFFLINE=1` 恢复即可完全离线运行。

---

## 常用命令

### 文档入库

将文档向量化并存入 ChromaDB。**每次添加或更新文档后执行一次。**

```bash
# 英文文档 → kb_en collection（all-MiniLM-L6-v2，384维）
.venv\Scripts\python -m rag.ingest -d ./docs -m en

# 中文文档 → kb_zh collection（BAAI/bge-small-zh，512维）
.venv\Scripts\python -m rag.ingest -d ./docs_zh -m zh

# 默认目录 + .env 中配置的默认模型
.venv\Scripts\python -m rag.ingest
```

模型别名：

| 别名 | 模型 | ChromaDB collection | 适用场景 |
|------|------|---------------------|----------|
| `en` | `all-MiniLM-L6-v2` | `kb_en` | 英文 / 多语言 |
| `zh` | `BAAI/bge-small-zh` | `kb_zh` | 中文文档 |

检索时自动查询所有已有 collection，结果按相似度全局合并排序。

输出示例：
```
10:00:01 [INFO] Embedding 模型: BAAI/bge-small-zh  →  collection: kb_zh
10:00:03 [INFO] 发现 1 个文档，开始入库...
10:00:06 [INFO]   入库: AI_Agent_学习计划_8周.docx → 20 块
10:00:06 [INFO] 入库完成，共写入 20 个文本块，collection 当前总量: 20 块
```

### 启动问答

```bash
.venv\Scripts\python main.py
```

CLI 内置命令：

```
/help                      查看帮助
/ingest                    扫描默认 docs/ 目录并入库
/ingest <目录> -m zh       指定目录 + 中文模型入库
/ingest <目录> -m en       指定目录 + 英文模型入库
/clear                     清空对话历史，重置 Agent
/quit                      退出
```

---

## 测试

```bash
# 快速单元测试（不调用 API，3 秒内完成，53 个用例）
.venv\Scripts\python -m pytest -m "not integration" -v

# 完整测试（含真实 API 调用和 ChromaDB 检索，需先完成入库）
.venv\Scripts\python -m pytest

# 按模块测试
.venv\Scripts\python -m pytest tests/test_phase1_llm.py    # LLM 配置 & Provider
.venv\Scripts\python -m pytest tests/test_phase2_parser.py # 文档解析（7 种格式）
.venv\Scripts\python -m pytest tests/test_phase3_rag.py    # 分块 & 双语检索
.venv\Scripts\python -m pytest tests/test_phase4_tools.py  # 工具层（search/fetch）
.venv\Scripts\python -m pytest tests/test_phase5_agent.py  # Agent ReAct 循环
```

---

## 项目结构

```
AgentA/
├── .env                    # API Keys 与配置（不提交 Git）
├── config.py               # 全局配置：LLM 切换 + Embedding 多模型注册
├── main.py                 # CLI 问答入口
├── requirements.txt        # 依赖清单
│
├── llm/
│   └── provider.py         # LLM 统一调用接口（Function Calling + Claude 适配）
│
├── rag/
│   ├── parser.py           # 多格式文档解析（MD/TXT/HTML/PDF/DOCX/PPTX/XLSX）
│   ├── ingest.py           # 文档入库：解析→分块→向量化→ChromaDB（支持 -m 模型选择）
│   └── retriever.py        # 向量检索：跨 collection 合并排序
│
├── agent/
│   ├── agent.py            # Agent 主控（ReAct 循环，最大 10 轮迭代）
│   └── tools.py            # 工具定义（search_knowledge / fetch_url）
│
├── docs/                   # 英文/多语言文档（不提交 Git）
├── docs_zh/                # 中文文档（不提交 Git）
├── chroma_db/              # ChromaDB 向量库（自动生成，不提交 Git）
│   ├── chroma.sqlite3      # 元数据索引（collection name ↔ UUID 映射）
│   ├── <uuid>/             # kb_en 向量索引（HNSW）
│   └── <uuid>/             # kb_zh 向量索引（HNSW）
│
└── tests/
    ├── test_phase1_llm.py
    ├── test_phase2_parser.py
    ├── test_phase3_rag.py
    ├── test_phase4_tools.py
    ├── test_phase5_agent.py
    └── manual_test.ipynb   # 交互式测试 Notebook
```

---

## 切换 LLM

只需修改 `.env` 中的 `LLM_PROVIDER`，无需改动任何业务代码：

```ini
LLM_PROVIDER=kimi       # Moonshot Kimi（开发/测试，免费额度大）
LLM_PROVIDER=deepseek   # DeepSeek（高性价比）
LLM_PROVIDER=qwen       # 阿里云通义千问 Qwen
LLM_PROVIDER=minimax    # MiniMax
LLM_PROVIDER=glm        # 智谱 AI GLM
LLM_PROVIDER=ollama     # 本地 Ollama（完全离线）
LLM_PROVIDER=openai     # OpenAI GPT-4o（需代理）
LLM_PROVIDER=grok       # xAI Grok（需代理）
LLM_PROVIDER=claude     # Anthropic Claude（需代理，需 pip install anthropic）
```

---

## 待优化项（Phase 8）

> 按优先级排序，高优先级对日常使用影响最大。

| 优先级 | 优化项 | 说明 |
|--------|--------|------|
| 🔴 高 | **对话记忆** | 将 messages 历史持久化到 SQLite，支持多轮上下文跨会话 |
| 🔴 高 | **重排序（Reranking）** | 在向量检索结果上加 Cross-Encoder 二次排序，提升精度 |
| 🟡 中 | **URL 批量导入** | 支持通过配置文件批量爬取指定网页并入库 |
| 🟡 中 | **文档自动同步** | 用 `watchdog` 监听 `docs/` 变化，自动增量入库 |
| 🟢 低 | **Web UI** | 接入 Gradio 或 Streamlit，提供图形界面 |
