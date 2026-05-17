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
## UT 测试
使用 . t.sh 脚本

## RAG评估

把"是否真的命中"用数字说话，避免调参靠直觉。流程是 **黄金集 + 离线评估**：人工写 20~50 条「问题-期望命中」对照表，脚本自动跑检索并统计 4 项指标（hit_source@k / hit_keyword@k / hit_either@k / MRR）。

### 1. 准备黄金集

参考 `evaluation/rag/golden.example.json` 改成自己的 `evaluation/rag/golden.json`（私有数据，已 gitignore）。每条 item：

| 字段 | 必填 | 说明 |
|------|------|------|
| `query` | ✓ | 用户实际会问的问题 |
| `expected_keywords` | – | OR 关系：任一在命中 chunk 里出现即记 keyword_hit |
| `expected_source` / `expected_source_contains` | – | 精确 / 子串匹配 hit.source |
| `note` | – | 自描述备注，不参与评估 |

### 2. 一行起跑

```powershell
# 默认：清库 → 双语种 ingest → eval → 落 JSON 报告
python -m evaluation.rag.run_eval --label v1
```

默认 ingest 路径（可用 `--en-dir` / `--zh-dir` 覆盖）：

| 模型 | 目录 | collection |
|------|------|------------|
| en   | `../pursue`         | `kb_en` |
| zh   | `../pursue/resume`  | `kb_zh` |

报告输出到 `reports/v1-<时间戳>.json`，控制台末尾打印一行：

```
RESULT[v1] items=35 k=8 hit_source@k=51.43% hit_keyword@k=68.57% hit_either@k=74.29% MRR=0.4231
```

### 3. 多 commit baseline 对比（量化各 Iter 增量）

```powershell
git stash
git checkout 1fe5582; python -m evaluation.rag.run_eval --label iter0   # 未优化基线
git checkout 50f19b1; python -m evaluation.rag.run_eval --label iter1
git checkout 3bc21ac; python -m evaluation.rag.run_eval --label iter2
git checkout 6fbb30d; python -m evaluation.rag.run_eval --label iter3
git checkout 944f52d; python -m evaluation.rag.run_eval --label iter4
git checkout 35813bc; python -m evaluation.rag.run_eval --label iter5
git checkout main; git stash pop
```

每次切 commit 后必须重新 ingest（脚本默认会清库），因为切分策略 / metadata schema / embedding 维度 / BM25 索引格式在不同 Iter 下不同。

### 4. 消融实验（库已就绪，跳过 ingest 节省时间）

```powershell
python -m evaluation.rag.run_eval --skip-ingest --no-rewriter --label no-rewriter   # 关 query 改写
python -m evaluation.rag.run_eval --skip-ingest --no-rerank   --label no-rerank     # 关 reranker
python -m evaluation.rag.run_eval --skip-ingest --k 5         --label k5            # 看 top_k 曲线
```

### 5. m3 单库 vs en/zh 双库（Iter-5 引入）

```powershell
$env:RAG_ACTIVE_EMBEDDINGS = "m3"
python -m evaluation.rag.run_eval `
    --en-dir ../pursue --zh-dir ../pursue/resume `
    --en-model m3 --zh-model m3 --label m3-single
$env:RAG_ACTIVE_EMBEDDINGS = "en,zh"      # 跑完恢复
```

### 6. 结果解读速查

| 现象 | 可能原因 / 下一步 |
|------|-------------------|
| `hit_source@k` 低、`hit_keyword@k` 还行 | 召回到了相关内容但不是期望文件 → 同名覆盖 / 分块碎裂 |
| 两者都低，集中在某类问题（如表格 / 跨文档对比） | 解析层（parser/splitter）短板 |
| `hit_either@k` 高、MRR 低 | 命中了但排序差 → 调 reranker / score 阈值 / RRF k |
| 英文 query miss 多 | Iter-5 翻译轴未生效 → 检查 LLM provider / `RAG_TRANSLATE_QUERY_ENABLED` |

逐条 `RESULT` 详情看 `reports/<label>-<ts>.json` 的 `cases[]` 字段；每条带 `top_sources`，可直接定位 retriever 误召回了哪些文件。

### 其他实用参数

```powershell
python -m evaluation.rag.run_eval --help        # 查看完整参数
python -m evaluation.rag.run_eval --dry-run     # 只打印将执行的命令，不实际跑
python -m evaluation.rag.run_eval --no-clean    # 不清空 chroma_db / bm25_index
```

更详细的方法论与背景见 `todo.txt` 的 "43. RAG 效果评估" 章节。
