# AgentA

本地运行的私有知识库 Agent，支持多格式文档解析、双语向量化存储与自然语言问答，LLM 可配置。

## **功能特性**

- 解析多种格式文档：MD / TXT / HTML / PDF / DOCX / PPTX / XLSX
- 本地向量化存储（ChromaDB），数据不出本地
- **双语 Embedding**：英文用 `all-MiniLM-L6-v2`（kb_en），中文用 `BAAI/bge-small-zh`（kb_zh），各自独立 collection，检索时 round-robin 交错合并，避免跨模型距离不可比
- **Cross-Encoder 二阶段精排**：召回阶段取 `top_k × RERANKER_RECALL_MULTIPLIER` 条候选，再由 `cross-encoder/ms-marco-MiniLM-L-6-v2` 重新打分排序，显著提升 Top-K 精度；可通过 `RERANKER_ENABLED=false` 关闭（向后兼容）
- 自然语言提问，ReAct Agent 自动检索相关文档片段后调用 LLM 生成答案
- 支持网页内容实时抓取（`fetch_url` 工具）作为知识库补充
- 支持 9 个 LLM Provider ：Kimi / DeepSeek / Qwen / MiniMax / GLM / Ollama / OpenAI / Grok / Claude
- 国外 Provider（OpenAI / Grok / Claude）支持通过 `LLM_PROXY` 配置 HTTP 代理

---

## 快速开始

### 1. 环境准备

```bash
# 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 安装所有依赖（包含 sentence-transformers、chromadb、openai、httpx 等）
pip install -r requirements.txt
```

### 2. 配置环境变量

直接编辑 `.env`，填入对应的 API Key：

```ini
# 当前激活的 LLM（可选：kimi/deepseek/qwen/minimax/glm/ollama/openai/grok/claude）
LLM_PROVIDER=kimi

# 各 Provider API Key（填写你实际使用的即可）
MOONSHOT_API_KEY=sk-...       # Kimi
DEEPSEEK_API_KEY=sk-...       # DeepSeek
QWEN_API_KEY=sk-...           # 通义千问 Qwen
MINIMAX_API_KEY=sk-api-...    # MiniMax
GLM_API_KEY=...               # 智谱 GLM
OPENAI_API_KEY=sk-...         # OpenAI
GROK_API_KEY1=xai-...         # xAI Grok
ANTHROPIC_API_KEY=sk-ant-...  # Anthropic Claude

# HTTP 代理（国外 Provider 使用：openai / grok / claude）
LLM_PROXY=http://你的代理IP:PORT

# Embedding 模型（en=英文；zh=中文）
EMBEDDING_MODEL=en

# 首次下载模型后开启，防止每次启动联网
TRANSFORMERS_OFFLINE=1
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

# Cross-Encoder 精排模型（~23MB，中英文双语）
.venv\Scripts\python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2'); print('OK')"
```

下载完成后将 `.env` 中 `TRANSFORMERS_OFFLINE=1` 恢复即可完全离线运行。
Embedding 模型别名：
| 别名 | 模型 | ChromaDB collection | 适用场景 |
|------|------|---------------------|----------|
| `en` | `all-MiniLM-L6-v2` | `kb_en` | 英文 / 多语言 |
| `zh` | `BAAI/bge-small-zh` | `kb_zh` | 中文文档 |

检索流程（两阶段）：
1. **召回**：每个 collection 各取 `top_k × RERANKER_RECALL_MULTIPLIER` 条，round-robin 交错合并为候选集
2. **精排**：Cross-Encoder 对候选集重新打分，降序截取最终 `top_k` 条；设 `RERANKER_ENABLED=false` 可跳过精排

---

## 启动问答

```bash
.venv\Scripts\python main.py
```

CLI 内置命令：
```
/help                      查看帮助
```

---

## 切换 LLM

只需修改 `.env` 中的 `LLM_PROVIDER`，无需改动任何业务代码：

```ini
# 国内直连（无需代理）
LLM_PROVIDER=kimi       # Moonshot Kimi
LLM_PROVIDER=deepseek   # DeepSeek
LLM_PROVIDER=qwen       # 阿里云通义千问 Qwen
LLM_PROVIDER=minimax    # MiniMax
LLM_PROVIDER=glm        # 智谱 AI GLM
LLM_PROVIDER=ollama     # 本地 Ollama（完全离线）

# 国外需要代理（在 .env 中配置 LLM_PROXY）
LLM_PROVIDER=openai     # OpenAI GPT-4o
LLM_PROVIDER=grok       # xAI Grok
LLM_PROVIDER=claude     # Anthropic Claude
```

> **代理配置**：国外 Provider 需在 `.env` 中设置 `LLM_PROXY=http://ip:port`，国内 Provider 自动直连，无需改动其他代码。

---


## 测试

```bash
# 快速单元测试
.venv\Scripts\python -m pytest -m "not integration" -v

# 完整测试（含真实 API 调用和 ChromaDB 检索，需先完成入库）
.venv\Scripts\python -m pytest

# 按模块测试
.venv\Scripts\python -m pytest tests/test_llm.py           # LLM 配置 & Provider
.venv\Scripts\python -m pytest tests/test_parser.py        # 文档解析（7 种格式）
.venv\Scripts\python -m pytest tests/test_rag.py           # 分块 & 双语检索 & Reranker
.venv\Scripts\python -m pytest tests/test_tools.py         # 工具层（search/fetch）
.venv\Scripts\python -m pytest tests/test_agent.py         # Agent ReAct 循环
.venv\Scripts\python -m pytest tests/test_memory.py        # 对话记忆持久化
.venv\Scripts\python -m pytest tests/test_prompt_loader.py # 自定义 Prompt 加载
```