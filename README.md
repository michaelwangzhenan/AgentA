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
- **评估闭环**：`tools/rag_eval/runner.py` 内置黄金集 + `hit@1，hit@3，hit@k` / `MRR` 指标，每次调优结果落盘 Markdown 报告（含 Miss 用例诊断），便于跨轮 diff。

### 1.2.Agent

- **手写 ReAct 循环**：原生 OpenAI Function Calling 协议，支持 `search_knowledge` / `web_search` / `fetch_url` 三件套工具，SPA 页面自动 fallback 到 Jina Reader。
- **多 Provider 切换**：通过 `.env` 中 `LLM_PROVIDER` 一行切换 Kimi / Qwen / DeepSeek / GLM / MiniMax / Ollama / OpenAI / Grok / Claude，业务代码零改动。
- **Extended Thinking**：支持 Claude / Qwen3 的深度思考模式，可手动设置 budget 或开启 Adaptive 模式按问题复杂度自动估算。
- **跨 Session 记忆**：独立 SQLite 存储用户偏好/事实，可自动提取也可通过"记住这个"指令即时触发。
- **项目偏好规则**：项目根放一份 `.agenta/rules.md`（参考 Cursor Rules / AGENTS.md），Agent 启动时自动注入到 system prompt，例如"始终用中文""引用要带页码"，不必每轮重申；可被会话中临时偏好覆写。
- **Skills 加载**：`.agenta/skills/<name>/SKILL.md` 兼容 agentskills.io 规范，Agent 自动发现并按需调用，也支持 `/<skill_name>` 手动激活。
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
# 填入：LLM_PROVIDER 以及对应的 *_API_KEY
```

### 2.3.下载 Embedding /Reranker 模型

参考 [4.3.模型下载](#43-模型下载)

### 2.4.启动 AgentA

首次使用先入库（详见 [4.1.RAG 入库](#41rag-入库)）：

```bash
python -m tools.rag_cli ingest -m m3
```

WebUI 模式（Chainlit，推荐）：

```bash
chainlit run chainlit_app.py --port 8000
# 浏览器打开 http://localhost:8000
```

CLI 模式（开发调试 / 无 GUI 场景）：

```bash
python main.py
# 进入后输入 /help 查看全部命令
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

`tools/rag_cli.py` 把 `./datasets/` 下文档灌入向量库 + BM25 索引，并提供清库与状态查询：

```bash
python -m tools.rag_cli status                                 # 查看每个 collection 的当前状态
python -m tools.rag_cli ingest                                 # 幂等增量入库（默认 datasets/data_en + 默认模型）
python -m tools.rag_cli ingest -d ./datasets/data_zh -m zh     # 指定目录 / 模型别名（en / zh / m3）
python -m tools.rag_cli clear                                  # 清空全部 collection + BM25（需 yes 确认）
python -m tools.rag_cli clear -m m3                            # 只清空指定 alias
```

### 4.2.RAG 评估

基于 `tools/rag_eval/golden.json` 黄金集计算 `hit@1，hit@3，hit@k` / `MRR`，结果默认落到 `tools/rag_eval/reports/`。

```bash
python -m tools.rag_eval.runner                                          # 当前配置基线
python -m tools.rag_eval.runner --no-rewriter                            # 关闭 Query 改写做消融对比
python -m tools.rag_eval.runner --no-rerank                              # 关闭精排做消融对比
python -m tools.rag_eval.runner -o tools/rag_eval/reports/x.md          # 落 Markdown 报告 + 同名 .log trace
```

<a id="43-模型下载"></a>

### 4.3.模型下载

`tools/download_models.py` 按编号下载所需 Embedding / Reranker，自带多镜像 fallback:

```bash
python -m tools.download_models      # 默认下载全部 5 个（已缓存自动跳过）
python -m tools.download_models -l   # 仅查看清单与缓存状态
python -m tools.download_models 3    # 下载指定模型(编号详见 -l 输出)

```

### 4.4.UT 测试

`tools/ut.sh` 封装了 pytest 调用，分两类：**档位**（按 marker 过滤）+ **模块**（按文件，调试用）。

```bash
bash tools/ut.sh -h          # 查看帮助
bash tools/ut.sh -fast       # 默认case，跳过 integration/langchain/autogpt/extended_providers
bash tools/ut.sh -ext        # 默认 + 其余 7 个 LLM provider
bash tools/ut.sh -int        # 仅集成测试（需 .env 中相应真实 API key）
bash tools/ut.sh -all        # 全部case

```

> Windows 用户也可直接 `python -m pytest tests/` ，效果等同 `-fast`（pytest.ini 已配 marker 过滤）。

### 4.5.UI 调试

`tools/ui_debug.ps1`（Windows / PowerShell）一键拉起 Chainlit + cloudflared 隧道，自动把生成的临时公网 URL 复制到剪贴板，方便手机或外部设备调试：

```powershell
.\tools\ui_debug.ps1                 # 默认 8000 端口
.\tools\ui_debug.ps1 -Port 8080      # 自定义端口
```

