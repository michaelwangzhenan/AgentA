# AgentA

**Badges(TBD)**

一个从零实现的 **本地化私有知识库 Agent**，集成进阶 RAG 检索与 ReAct 工具调用循环，支持 CLI 与 WebUI 两种交互方式，可在 9 个国内外主流 LLM 之间切换。

**整体架构(TBD)**



## 1.功能特性

### 1.1.RAG

- **多模型 Embedding**：内置 `all-MiniLM-L6-v2`（英文）/ `bge-small-zh`（中文）/ `bge-m3`（多语言）三套别名，分库存储自动路由。
- **混合检索**：Dense 向量召回 + BM25 关键词召回，通过 **Reciprocal Rank Fusion** 融合排名，对术语/缩写/版本号场景显著优于纯向量。
- **二阶段精排**：使用 `bge-reranker-base` Cross-Encoder 对召回结果做精排，并按 per-model 阈值过滤低质 chunk。
- **Query 改写**：Multi-Query 同义改写、HyDE 假设性答案、跨语言翻译轴三档可独立开关。
- **多格式解析**：覆盖 PDF / DOCX / PPTX / XLSX / Markdown / HTML / TXT 七种格式，PDF 扫描件自动 OCR 兜底（rapidocr-onnxruntime）。
- **评估闭环**：`tools/rag_eval/eval.py` 内置黄金集 + `hit@k` / `MRR` 指标，每次调优结果可 JSON 留档对比。

### 1.2.Agent

- **手写 ReAct 循环**：原生 OpenAI Function Calling 协议，支持 `search_knowledge` / `web_search` / `fetch_url` 三件套工具，SPA 页面自动 fallback 到 Jina Reader。
- **多 Provider 切换**：通过 `.env` 中 `LLM_PROVIDER` 一行切换 Kimi / Qwen / DeepSeek / GLM / MiniMax / Ollama / OpenAI / Grok / Claude，业务代码零改动。
- **Extended Thinking**：支持 Claude / Qwen3 的深度思考模式，可手动设置 budget 或开启 Adaptive 模式按问题复杂度自动估算。
- **跨 Session 记忆**：独立 SQLite 存储用户偏好/事实，可自动提取也可通过"记住这个"指令即时触发。
- **Skills & Prompts**：兼容 agentskills.io 规范的 Skill 加载机制，配合自定义 Prompt 可一键切换"5G 专家""代码助手"等角色。
- **三套实现可选**：`PYTHON`（手写 ReAct，默认）/ `LANGCHAIN`（create_agent 驱动）/ `AUTOGPT`（Plan-Execute 双循环）。


## 2.快速开始

### 2.1.环境准备

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 2.2.配置环境变量

```bash
cp .env.example .env
# 至少填入：LLM_PROVIDER 以及对应的 *_API_KEY
```

### 2.3.下载 Embedding 模型（首次需联网，之后永久本地缓存）

```bash
python scripts/download_models.py        # 默认下载全部 5 个（已缓存自动跳过）
python scripts/download_models.py -l     # 仅查看清单与缓存状态
python scripts/download_models.py 3 4    # 仅下载指定编号
```

### 2.4.启动 AgentA

CLI 模式：

```bash
python main.py
# 进入后输入 /help 查看全部命令；首次使用先 /ingest 把 ./docs 入库
```

WebUI 模式（Chainlit）：

```bash
chainlit run chainlit_app.py --port 8000
# 浏览器打开 http://localhost:8000
```

## 3.主要配置

`.env` 中常用的关键项（完整说明见 `.env.example`）：

```ini
# —— LLM ——
LLM_PROVIDER=qwen                 # kimi / qwen / deepseek / glm / openai / claude ...
QWEN_API_KEY=sk-xxxxxxxx
LLM_PROXY=http://127.0.0.1:7890   # 仅 openai / grok / claude 需要

# —— RAG ——
EMBEDDING_MODEL=m3                # en / zh / m3
RAG_ACTIVE_EMBEDDINGS=m3          # 实际启用的 collection 列表
RAG_TOP_K=8                       # 送给 LLM 的最终片段数
RERANKER_ENABLED=true             # 二阶段 Cross-Encoder 精排
BM25_ENABLED=true                 # BM25 + RRF 混合检索
RAG_QUERY_REWRITE_ENABLED=true    # Multi-Query 同义改写

# —— Agent ——
IMP_METHOD=PYTHON                 # PYTHON / LANGCHAIN / AUTOGPT
THINKING_ENABLED=true             # Extended Thinking（Claude / Qwen3）
USER_MEMORY_ENABLED=true          # 跨 session 用户记忆

```

## 4.实用工具

### 4.1.RAG 入库

`tools/ingestion.py` 把 `./docs` 下文档灌入向量库 + BM25 索引（与 `/ingest` 等价），额外提供清库与状态查询：

```bash
python tools/ingestion.py status                  # 查看每个 collection 的当前状态
python tools/ingestion.py ingest                  # 幂等增量入库（默认 docs + 默认模型）
python tools/ingestion.py ingest -d ./docs_zh -m zh   # 指定目录 / 模型别名（en / zh / m3）
python tools/ingestion.py clear                   # 清空全部 collection + BM25（需 yes 确认）
python tools/ingestion.py clear -m m3             # 只清空指定 alias
```

### 4.2.RAG 评估

基于 `tools/rag_eval/golden.json` 黄金集计算 `hit@k` / `MRR`，结果默认落到 `tools/rag_eval/reports/`。

```bash
python -m tools.rag_eval.eval                                          # 当前配置基线
python -m tools.rag_eval.eval --no-rewriter                            # 关闭 Query 改写做消融对比
python -m tools.rag_eval.eval --no-rerank                              # 关闭精排做消融对比
python -m tools.rag_eval.eval --json tools/rag_eval/reports/x.json     # 保存 JSON 便于 diff
```

### 4.3.模型下载

`tools/download_models.py` 按编号下载所需 Embedding / Reranker，自带多镜像 fallback：

```bash
python tools/download_models.py        # 默认下载全部 5 个（已缓存自动跳过）
python tools/download_models.py -l     # 仅查看清单与缓存状态
python tools/download_models.py 3 4    # 仅下载指定编号
```

### 4.4.UT 测试

`tools/ut.sh` 按模块封装了 pytest 调用：

```bash
bash tools/ut.sh -h          # 查看全部分组
bash tools/ut.sh -not        # 跑除集成测试外的全部 UT
bash tools/ut.sh -rag        # 仅跑 RAG（分块 / 双语检索 / Reranker）
bash tools/ut.sh -agent      # 仅跑 Agent ReAct 循环
```

### 4.5.UI 调试

`tools/ui_debug.ps1`（Windows / PowerShell）一键拉起 Chainlit + cloudflared 隧道，自动把生成的临时公网 URL 复制到剪贴板，方便手机或外部设备调试：

```powershell
.\tools\ui_debug.ps1                 # 默认 8000 端口
.\tools\ui_debug.ps1 -Port 8080      # 自定义端口
```

# 5.缩写
| 缩写 | 全称 | 含义 |
|---|---|---|
| **KB** | Knowledge Base | 知识库（向量库 + 关键词索引）|
| **RAG** | Retrieval-Augmented Generation | 检索增强生成 |
| **BM25** | Best Matching 25 | 经典关键词检索算法 |
| **RRF** | Reciprocal Rank Fusion | 倒数排名融合（合并多路召回排序）|
| **HyDE** | Hypothetical Document Embeddings | 假设性文档嵌入（让 LLM 先编一段答案再检索）|
| **MRR** | Mean Reciprocal Rank | 平均倒数排名（评估指标）|
| **nDCG** | normalized Discounted Cumulative Gain | 归一化折损累积增益（更细的评估指标）|
