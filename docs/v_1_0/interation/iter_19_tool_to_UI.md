# 1. 目标 
把 tools/下的工具同步到 Web UI

# 2. 摸底

## 2.1. tools/下的工具

按用途分两类：**运维类**（巡检 / 入库 / 备份 / 模型 / 开发）和**评估类**（离线跑各项 eval）。

### 2.1.1. 运维 / 开发类

| 工具 | 功能 | 底层复用 | 备注 |
|---|---|---|---|
| `tools/db_show.py` | 只读巡检 Chroma / SQLite / BM25 的落盘位置与规模（summary / chroma / sqlite / bm25 子命令） | `src/db_inspect.py`（与 `/admin/db` API 共用） | 纯只读 |
| `tools/backup.py` | 运行时数据备份 / 还原 / 列表，打成带时间戳的 zip（backup / restore / list） | `src/runtime_backup.py`（与 `/admin/backup` API 共用） | restore 为破坏性操作 |
| `tools/rag_cli.py` | RAG 知识库入库的三原语：status（只读）/ ingest（幂等增量写）/ clear（清空，需二次确认） | `src.rag.ingest.ingest_all` | clear 为破坏性操作 |
| `tools/download_models.py` | 一键下载项目用到的 5 个 Embedding / Reranker 模型，多镜像 fallback | huggingface_hub | 首次初始化 / 镜像被墙时用 |
| `tools/ui.ps1` | 管理前后端 dev server（uvicorn :8000 + vite :5173）的 start / stop / restart / logs / status | — | 纯本地开发脚本，不入 UI |
| `tools/ingest_eval.sh` | 入库 + RAG 评估的命令备忘（多为注释） | 调 `rag_cli` / `rag_eval.runner` | 备忘脚本，不入 UI |
| `tools/debug_function_names.py` | 一次性诊断：扫历史 messages 找非法 function name | `src.config` | 一次性脚本，不入 UI |

### 2.1.2. 评估类（`agent_eval/` + `rag_eval/`）

| 工具 | 评估什么 | 是否耗 LLM token | 报告落盘 |
|---|---|---|---|
| `agent_eval/run_all.py` | 离线评估统一入口，子进程聚合下列各项，按退出码判 PASS/FAIL；`--ci` 只跑不耗 token 子集 | 取决于子项 | `reports/run-all-<ts>.md` |
| `agent_eval/security/adversarial.py` | prompt injection 四层防御的拦截率 / 误拦率；`--no-llm` 仅跑名单门 | 可选（`--no-llm` 不耗） | `reports/security-adversarial-<ts>.md` |
| `agent_eval/perf_eval.py` | session / memory 列表与查询随数据量增长的耗时基准 | 否 | `reports/perf-*-<ts>.md` |
| `agent_eval/harness/eval_harness.py` | critic 自身（quiz_critic / rag_critic）判得准不准 | 是 | `reports/harness-eval-<ts>.md` |
| `agent_eval/memory/recall_golden.py` | 记忆 / rules / RAG 引用能否被注入并被 LLM 遵循 | 是 | `reports/recall-<ts>.md` |
| `agent_eval/skills/recall_skill.py` | LLM 能否从 catalog 主动识别该调哪个 skill | 是 | `reports/skill-recall-<ts>.md` |
| `agent_eval/plan/eval_plan.py` | make_plan 识别准确率 + plan 结构质量 | 是 | `reports/plan-eval-<ts>.md` |
| `agent_eval/plan_business/eval_learning_plan.py` | 学习计划触发识别 + 计划质量 | 是 | （同上目录） |
| `agent_eval/quiz/eval_quiz.py` | quiz 创建 / 历史触发识别 + 出题质量 | 是 | `reports/quiz-eval-<ts>.md` |
| `agent_eval/srs/eval_srs.py` | SRS 四 tool 触发识别 | 是 | `reports/srs-eval-<ts>.md` |
| `agent_eval/mcp/eval_mcp.py` | MCP 完整链路（接入 / 合流 / 调用 / 安全 / SSRF）；`--no-llm` 仅 structural | 可选 | `reports/mcp-<ts>.md` |
| `rag_eval/runner.py` | RAG 检索 recall@k / MRR（可选 `--llm` 评答案质量） | 检索否 / `--llm` 是 | `tools/rag_eval/reports/*.md` |
| `eval_common/llm_judge.py`、`rag_eval/rag_judge.py` | LLM judge 公共模块 | — | 非独立工具，被上面引用 |

## 2.2. Web UI 工具

Web UI 已对接的工具能力（侧边栏入口 → 子页）。除标注外，质量看板的"会话监控"全用户可见，其余评估 / 运维入口均 **admin only**。

| 侧边栏入口 | 子页 / 能力 | 对接的 tools / 后端逻辑 | 性质 | 权限 |
|---|---|---|---|---|
| 质量看板 | 会话监控（trace） | 在线 trace 可观测 | 只读展示 | 全用户 |
| 质量看板 | 实时安全监控 | `/admin/security` 运行期真实拦截统计 | 只读展示 | admin |
| 质量看板 | 离线安全评估 | 展示 `security/adversarial.py` 结果（拦截率 / 误拦率 / 逐类分项），**不在 UI 触发跑** | 只读展示 | admin |
| 质量看板 | Golden 管理 | `rag_golden.db` 的导入 / 审核 | 读写 | admin |
| 质量看板 | 综合评估报告 | 渲染 `reports/*.md`（按前缀分类：综合 / 安全 / 性能 / RAG / 业务等），**只看不跑** | 只读展示 | admin |
| 数据库 | Chroma / SQLite / BM25 巡检 | 与 `db_show.py` 共用 `src/db_inspect.py` | 只读 | admin |
| 备份与恢复 | 备份 / 还原 / 列表 | 与 `backup.py` 共用 `src/runtime_backup.py` | 读写 | admin |
| 知识库 | 文档上传 / 列出 / 删除 | RAG 入库相关接口 | 读写 | （见各页） |

**现状小结**：

- 运维类里，`db_show`、`backup` 已有对应 UI 页；`rag_cli`（入库）部分能力散在"知识库"页，但 status / clear 等运维原语未完整对应。
- 评估类目前 UI 只做**结果展示**（安全评估指标 + reports markdown 渲染），**没有从 UI 主动触发某项 eval 的入口**；跑 eval 仍走 CLI。
- `download_models` / `ui.ps1` / `ingest_eval.sh` / `debug_function_names` 属本地开发 / 初始化脚本，不适合进 UI。

## 2.3. 决策

### 2.3.1. CLI ↔ Web 映射与建议

判断标准：**面向运行期数据 / 全用户共享资源**且**操作可在秒级返回**的工具适合进 UI；**本地一次性 / 开发期脚本**或**分钟级长任务**留 CLI。

| CLI 工具 | 现有 Web 入口 | 建议 | 权限 | 理由 |
|---|---|---|---|---|
| `db_show.py` | 数据库 | ✅ 已完成，无需新做 | admin | 只读巡检，已复用同一后端 |
| `backup.py` | 备份与恢复 | ✅ 已完成，无需新做 | admin | 备份/还原已复用同一后端 |
| `rag_cli.py`（status/ingest/clear） | 部分散在"知识库"页 | ⭐ **建议补全**：把 status（只读状态）/ ingest（入库）/ clear（清空，二次确认）整合成完整的 RAG 运维入口 | admin | 写的是全用户共享知识库；秒级~分钟级，但已有 KB 页基础 |
| `agent_eval/*`、`rag_eval/runner` | 质量看板仅展示结果 | ❓ **待定**：是否加"从 UI 触发跑"按钮（见决策点 ②） | admin | 多数耗 token、分钟级长任务，UI 触发需进度/并发处理 |
| `download_models.py` | — | ❌ 不进 UI | — | 本地初始化脚本，依赖网络/镜像，长任务 |
| `ui.ps1` | — | ❌ 不进 UI | — | dev server 管理，开发期本地脚本 |
| `ingest_eval.sh` | — | ❌ 不进 UI | — | 命令备忘，非独立工具 |
| `debug_function_names.py` | — | ❌ 不进 UI | — | 一次性诊断脚本 |

### 2.3.2. 权限基调

与现状一致：**运维 / 评估类一律 admin only**，全用户页仅限聊天 / 知识库（自己的文档）/ 记忆 / Rules / 学而时习 / 用量 / 质量看板的「会话监控」。本期新增入口默认 admin only。

### 2.3.3. 决策结论（已确认）

- **① RAG 运维进 UI**：补全 `rag_cli` 的 status（只读状态）/ ingest（入库）/ clear（清空，二次确认），**整合进现有「知识库」页**（已有 KB 基础，用户认知统一）。
- **② eval UI 触发**：**全部 eval 都能从 UI 触发**（含耗 token 的 LLM 评估），跑完落报告，沿用现有「综合评估报告」展示。
- **③ 本期范围**：**①②一起做**。
- **权限**：两项新增能力均 **admin only**（沿用 §2.3.2 基调）。


# 3. 逐个实现

## 3.1. 知识库优化
CLI tool: tools/rag_cli.py

### 3.1.1. 库内容浏览
知识库页改成两层，参考「数据库」页 Chroma 面板（`ChromaList` → `ChromaItems`）。

- **L1 列哪些库**：列 `EMBEDDING_MODELS` 定义的全部 3 个（en/zh/m3）。
- **L1 每行字段**：库名(alias) + 模型名 + 文档数 + chunk 数（StatCard 样式，与 DB 页一致）。
- **L1 高亮**：在 L1 列表里高亮 `.env` 配置的默认入库模型（`EMBEDDING_MODEL` → `DEFAULT_EMBEDDING_ALIAS`）对应的库，标识"默认入库目标"。
- **L2 作用域**：点进某库后，L2（现有文档列表）的上传 / 删除 / 清空都作用于**所选库**；后端 `upload`/`delete`/`clear` 需补 `model`(alias) 参数（现 `list_kb_documents` 已支持 model，写操作硬编码默认库需改）。

涉及改动：
- 后端：新增 `GET /kb/collections`（L1 列表：alias / 模型 / 文档数 / chunk 数）；`upload`/`delete`/`clear` 增加 model 参数。
- 前端：知识库页拆 L1（库列表）+ L2（现有 `KnowledgeBaseView` 文档列表，按所选 alias 取数与写入）。

### 3.1.2. 第二层多页显示，导航栏
格式参考数据库L2显示

### 3.1.3. 第二层可以过滤，排序
先列出可以过滤/排序的字段（分别列），让用户选，确定后再实现
格式参考数据库L2显示

### 3.1.4. 入库优化
入库只对L2对应模型进行
可选多个目录/多个文件，也可以混合选，选中后不是立即开始入库，只显示列表，用户点 “开始入库”后才开始入库

### 3.1.5. 进度细节
添加显示更多入库过程，让用户看到更过细节，不会等的无聊
提示要合理

### 3.1.6. 删库
- 只在第二层操作，只对应L2对应模型，语义同入库
- 增加可多选批量删除
- 过滤后，也可多选批量删除
- 一键清空保留
- 单个删除，点删除后跳出确认框，回车等于点“删除”


### 3.1.7. web_uploads/  管理
- 文件分库管理，每个库对应一个文件夹
- 同名文件保留相对路径（避免同名互覆盖）

### 3.1.8. 孤儿段清理
- 问题：Chroma 删库只删 `chroma.sqlite3` catalog，不删磁盘 `<uuid>/` 向量段目录，残留的、不再被任何活跃 collection 引用的段目录。浪费磁盘。
- 维护页新增「孤儿段清理」面板：扫描（列 UUID+占用）→ 确认 → 清理。

### 3.1.9. 提交代码
按公约提交代码

## 3.2. 离线评估
agent_eval/run_all.py
agent_eval x 8 + rag_eval -> 和 综合评估报告 整合

## 3.3. Golden
“质量看板->Golden管理” 与 "数据库 -> SQLite -> rag_golden.db" 合并
