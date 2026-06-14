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


## 3.2. 离线评估
agent_eval/run_all.py

### 3.2.1. 总体设计

把质量看板的「离线安全评估」+「综合评估报告」合并成一个 **「离线评估」**；会话监控 / 实时安全监控 / Golden 管理保持不动。

**信息架构**：「离线评估」内左侧竖向导航列出各 eval，点一个进它的子页。子页结构：
- **说明卡片**（顶部可折叠）：**卡片头即 eval 名**（不再另起标题，标题与说明合并），点头展开 / 收起；默认**首次展开、之后按 eval key 记住折叠状态**（localStorage）。节序：目的 / 如何评估（**每步一行**）/ 参数说明（LLM 以外的选项，如类别 / target / ci）/ 工作原理（数据集、调不调 LLM、判定方式）/ 指标解读（**每指标一行**，指标 + 阈值 + 判定含义）/ 耗时·成本 / 如何看结果（卡片=结论、报告=诊断详情，**通用话术**）/ 数据来源（dataset · golden 路径与规模）。各 eval 在前端 `EvalTaskConfig.intro` 写静态文案，字段支持 `string` 或 `string[]`（数组逐行展示）。
- **摘要卡片**（通用组件）：最近一次结果（核心指标 + 阈值 + PASS/FAIL + 时间 / git）。安全那份逐类作详情扩展。
- **测试模型下拉**（统一标准，仅涉及 LLM 的 eval）：候选来自 `GET /routing/pool` 的可用模型（已配 api_key）；**默认选中系统当前 `ACTIVE_MODEL`**（`GET /config/models` 的 `active`，列表里标「（当前）」）。支持无 LLM 模式的 eval 在下拉里加 **「None（不调用 LLM）」** 选项 = 只跑不调用 LLM 的 case（取代单独的 `--no-llm` 复选框）。
- **选项 UI 化**：各 eval 的命令行选项都做成可操作控件（开关用复选框、枚举用下拉、计数用 number 输入；`--no-llm` 归入模型下拉「None」）。复选框用**正向语义**（勾选=开，可设 `default`），避免"关 xx"这种反逻辑；选项值统一进请求 `options` 字典，后端按 task 白名单拼参。需要副模型（如 RAG 评委模型）时可加第二个模型下拉。
- **阈值 UI 可调**：有判定阈值的 eval（如拦截率 / 误拦率 / 通过率 / 结构分）把阈值做成 UI 输入控件，默认填脚本现有默认值；跑评估时作为参数传入（不持久化，遵循上面"不改系统配置"）。卡片与 markdown 报告都**记录本次所用阈值**，便于复盘"是按什么线判的"。各 eval 具体可调哪些阈值，实现该 eval 时按其脚本定。
- **历史报告 / 详情**：该 eval 的历史报告列表，**从新到旧排列**。**点某行 = 摘要卡片切到那次快照**（高亮「当前卡片」跟随；卡片经 `/eval/summary?report=<name>` 读该报告配对的 sidecar JSON，缺失则显示"无结构化摘要"）；每行有**「查看源文档」按钮**点开 markdown 正文。进页 / 跑完默认选中最新一份。卡片管"是否过线"，markdown 管"挂在哪 / 怎么改"。不再单列顶级「综合评估报告」tab。
- **运行控制区布局**：所有控件（测试模型 / 选项 / 阈值）一组自动换行；**「开始评估」/「取消」单独成一行、右对齐**（不与控件挤同一行）。运行中在控制区下方显示 spinner + 日志末尾。
- 趋势图可选（安全已有，后补到通用）。

**触发机制**：单任务全局锁（同时只跑一个）+ 后台子进程跑 `python -m tools.<eval> [opts]`（输出落 `logs/eval_runs/`）+ **轮询 status**（运行中 / 末尾日志 / 退出码），可 cancel。取轮询而非 SSE：长任务跨页面存活、重连即恢复（详见 §3.2.2）。

**不改动系统现有配置**：选中的测试模型仅注入**子进程**的 `ACTIVE_MODEL` env，**不写 `.env` / 不改运行时 `config_overrides` / 不动模型路由池 / 不改父进程配置**。子进程退出即失效——天然"还原"，无需显式恢复。其它 UI 选项（`--no-llm` 等）同理只作为该次命令行参数，不落任何持久配置。

**卡片数据**：现仅安全有结构化 JSON sidecar。每个 eval 需补一份**标准化 summary JSON**（统一 schema：`name / timestamp / git / metrics:[{label,value,threshold,ok}] / passed / partial`），UI 用一个通用卡片组件渲染。

**eval 清单（怎么测 / 卡片指标 / 判定）**：

| eval | 命令 | 耗 token | 卡片指标 | 判定 |
|---|---|---|---|---|
| RAG 检索 | `rag_eval.runner` | 默认否 | recall@k / MRR | 阈值 |
| 安全红队 | `security.adversarial [--no-llm]` | 可选 | 拦截率 / 误拦率 + 逐类 | recall≥阈 且 fpr≤阈 |
| 性能 | `perf_eval --target …` | 否 | 各操作中位耗时(ms) | 无硬阈（基准 / 趋势） |
| 记忆召回 | `memory.recall_golden` | 是 | 通过率 | 阈值 |
| Skill 路由 | `skills.recall_skill` | 是 | 识别通过率(pos/neg) | 阈值 |
| Plan | `plan.eval_plan` | 是 | 识别率 + 结构分 | ≥80% 且 ≥3.5 |
| 学习计划 | `plan_business.eval_learning_plan` | 是 | 触发识别 + 质量分 | 阈值 |
| Quiz | `quiz.eval_quiz` | 是 | 触发识别 + 质量分 | 阈值 |
| SRS | `srs.eval_srs` | 是 | 触发识别率 | 阈值 |
| Harness | `harness.eval_harness` | 是 | critic 判准率 | 阈值 |
| MCP | `mcp.eval_mcp [--no-llm]` | 可选 | 验收 ①-⑦ 通过 | 全过 |

特殊：性能无 pass/fail（卡片为数字 + 趋势）。

**不做综合页**：`run_all` 不进 UI（每页参数各异，一页塞不下）；需要一键全量按预设跑时走 CLI `python -m tools.agent_eval.run_all`。

**实现顺序（按功能纵切，每步含 触发 + 报告 + 卡片）**：
第 1 步单列**框架**任务（共享基建），用**安全红队**做端到端验证（已有 summary JSON、可 `--no-llm` 快跑、并入现有"离线安全评估"卡片）。
之后各 eval 薄切：RAG → 记忆 → Skills → MCP → 性能 →（安全红队框架已含）→ Plan → Harness → 学习计划 → Quiz → SRS。

### 3.2.2. 框架（含安全红队验证）
搭「离线评估」共享基建，安全红队作活体验证。
- 信息架构：合并「离线安全评估」+「综合评估报告」为「离线评估」，左导航各 eval 子页。
- 后端：job runner（单任务全局锁，后台子进程，输出落 `logs/eval_runs/`）+ **轮询 status**（运行中 / 末尾日志 / 退出码）+ cancel。模型经子进程 `ACTIVE_MODEL` env 注入（`.env` 未定义该项，`load_dotenv(override)` 不会覆盖）。
  - 取轮询而非 SSE：eval 是分钟级长任务，后台 job + 轮询能跨页面存活、重连即恢复，比"SSE 绑请求（断连即杀）"稳健、比"SSE attach 后台 job"简单。
- 前端：通用卡片组件 + 通用子页骨架（选项复选 / 下拉、阈值输入、历史报告列表复用 `ReportsViewer`）+ 模型下拉（`/routing/pool`）。
- 约定：统一 summary JSON schema（`name / timestamp / git / metrics[] / passed / partial`）。
- 验证：接入安全红队，触发 → 轮询 → 报告 → 卡片全链路跑通。

产出：后端 `src/eval_runner.py`（单任务 runner）+ `/eval/run`、`/eval/run/status`、`/eval/run/cancel`、`/eval/summary`（通用摘要，映射安全 sidecar）；前端 `eval/OfflineEvalView`（左导航）+ `eval/EvalRunner`（模型下拉 + 选项 + 阈值 + 运行/取消/轮询 + 通用卡片 + 按 eval 过滤的历史报告/查看 + 可折叠说明卡片）；质量看板 tab 合并为「离线评估」（旧「离线安全评估」「综合评估报告」下沉，`SecurityPanel.OfflineEval` / `ReportsViewer` 暂留待清）。

**接入一个新 eval 的步骤（后续各 eval 照此模板）**：
1. 脚本：报告改用 `reports_dir("<eval>")` 落到 `tools/reports/<eval>/`；若有判定阈值，加对应 CLI 参数（如 `--xxx-threshold`），并把阈值记进 markdown + summary JSON。
2. 脚本：每次跑产出**与 .md 同名配对的 summary JSON**（统一 schema，供"点报告回看卡片"），后端 `_SUMMARY_BUILDERS` 加该 eval 的 sidecar→卡片映射（支持按 report 名读配对 JSON）。
3. 后端：`eval_runner.EVAL_MODULES` 注册 `<eval> → 模块路径`；`_build_eval_args` 加该 eval 的选项 / 阈值 → 命令行参数（白名单）。
4. 前端：`OfflineEvalView` 的 `EVAL_TASKS` 加一项 `EvalTaskConfig`——`key/label/usesLlm/noneOption?/reportMatch/options/thresholds?/intro`（intro 8 节文案）。
5. UT：路由层"选项 / 阈值 → 参数""非法值拒绝"；脚本核心逻辑按需补。

### 3.2.3. reports 目录调整（已确认）

把 `tools/agent_eval/reports` 和 `tools/rag_eval/reports` 合并到 **`tools/reports/<eval>/`**，按 eval 建子目录。**单独一步统一做**（避免新旧双布局过渡）。

- **新布局**：`tools/reports/{rag,security,perf,memory,skills,plan,learning_plan,quiz,srs,harness,mcp,run_all}/`。
- **共享 helper**：`tools/eval_common/report_paths.py` 的 `reports_dir(name)` → `tools/reports/<name>`（自动建目录），各脚本统一调用。
- **代码只扫新目录**：后端报告接口改成单根 `tools/reports/` 递归扫，report `name` = 相对路径（如 `security/security-adversarial-…md`）；安全 sidecar 读 `tools/reports/security/`。
- **旧报告整理不删**：把旧 `agent_eval/reports`、`rag_eval/reports` 里的文件按文件名前缀移到对应子目录（security/recall→memory/perf-→perf/… ），不删除内容。
- 前端无需改：`reportMatch` 文件名子串仍命中（`name` 含文件名）。


### 3.2.4. RAG 检索

按统一标准接入（task=`rag`，模块 `tools.rag_eval.runner`）。

- **测试模型下拉**：默认 **None（只评检索，不耗 token）**；选具体模型 = 额外评答案质量（`--llm`，回答用所选模型）。
- **评委模型下拉**（仅选了测试模型时显示）：给答案质量打分的模型，「（系统默认）」= 用 `EVAL_JUDGE_MODEL`，否则传 `--judge-model`。注：`.env` 定义了 `EVAL_JUDGE_MODEL`，env 注入会被 `load_dotenv(override)` 覆盖，故评委模型走 **CLI 参数** 而非 env 注入。
- **选项**：`query 改写` / `精排` 两个**正向**复选框（默认勾选=开，取消勾选才传 `--no-rewriter`/`--no-rerank`）+ `评委评测样本数`（number，默认 10、0=全部，仅选模型时生效）。
- **卡片**：纯展示数字、无 pass/fail——命中率@1/@3/@k、MRR（选模型时加 faithfulness / 相关度）。
- **报告**：runner 不自带目录，后端给 `-o tools/reports/rag/rag-<ts>.md`；新增**配对 summary JSON**（runner 落 `<out>.json`），卡片读它。
- **前置**：需先入库知识库 + `rag_golden.db` 有 approved golden。
- 产出：`runner._build_summary` + JSON 落盘；后端 `_rag_summary` / `EVAL_MODULES['rag']` / `_build_eval_args` rag 分支；通用请求改为 `options` 字典（各 eval 自有选项）+ 选项加 `number` 类型 + `defaultModelNone`。

### 3.2.5. 记忆召回

按统一标准接入（task=`memory`，模块 `tools.agent_eval.memory.recall_golden`）。

- **始终调 LLM**：模型下拉无 None、默认 ACTIVE_MODEL（记忆遵循需真实 LLM 回答）。
- **阈值**：`通过率阈值(≥)`（默认 0.8），脚本加 `--pass-threshold`，记入报告 + sidecar，卡片据此判 pass/fail。
- **卡片**：一条「通过率」指标（passed/total + 阈值 + 判定）。
- 产出：脚本加 `--pass-threshold` + 配对 summary JSON；后端新增**通用"通过率"型卡片工厂** `_passrate_summary`（记忆 / 后续 skill / srs 等复用）+ `EVAL_MODULES['memory']` + memory 阈值分支。

### 3.2.6. Skills

按统一标准接入（task=`skills`，模块 `tools.agent_eval.skills.recall_skill`），与记忆同为"通过率"型：

- 始终调 LLM（无 None、默认 ACTIVE_MODEL）；`通过率阈值(≥)` 默认 0.8（`--pass-threshold` + 配对 summary JSON）。
- 卡片复用 `_passrate_summary("skills", …, "识别通过率")`；后端 memory/skills 共用 `--pass-threshold` 分支。

### 3.2.7. MCP

按统一标准接入（task=`mcp`，模块 `tools.agent_eval.mcp.eval_mcp`），属"全过"判定型（非阈值型）：

- **模型下拉带 None、默认 None**：None = 只跑 structural（真启 MCP server 子进程、不调 LLM、不耗 token）；选模型 = 额外跑 llm-e2e（`--no-llm` 仅在 None 时传）。无额外 options、无阈值。
- **判定**：验收①-⑦无 failed 即"通过"；`--no-llm` 模式下被跳过的 llm-e2e case 不算失败。卡片一条「通过」指标（passed/total（+跳过 N）、阈值"全过"）。
- 产出：脚本写报告时配对输出 summary JSON（含 total/passed/skipped/failed/ok）；后端新增 `_mcp_summary`（"全过"型，部分通过标 partial）+ `EVAL_MODULES['mcp']` + mcp 分支（None→`--no-llm`）。

### 3.2.8. 性能

接入（task=`perf`，模块 `tools.agent_eval.perf_eval`），判定型（判据 PASS/FAIL）：

- **不调 LLM**：`usesLlm:false`，无模型下拉。后端固定传 `--target all`——**session + memory 一起跑、合并一份报告**（不再分别选 target）。
- **新增 `text` 选项类型**：UI 暴露「数据档位」文本框（逗号分隔正整数，留空=默认 10,100,1000）；后端白名单校验（仅数字+逗号）后传 `--sizes`。
- **合并报告 + 配对 JSON**：脚本改为把跑过的 target 合并成单份 `perf-<ts>.md` + `perf-<ts>.json`（含各 target 的判据 + 整体 `passed`）。
- **卡片**：判定型——各 target 判据逐条展开为 metric（如「会话·查询类<50ms」+ 实测值 + ok），全部 PASS 才判「通过」，部分通过标 partial。后端新增 `_perf_summary`。

### 3.2.9. 安全红队（框架任务已含，本节留细化 / 复核）

### 3.2.10. Plan
### 3.2.11. Harness

### 3.2.12. 学习计划
### 3.2.13. Quiz
### 3.2.14. SRS
### 3.2.15. 清理旧页面
### 3.2.16. 提交代码
按公约提交代码




## 3.3. Golden
“质量看板->Golden管理” 与 
"数据库 -> SQLite -> rag_golden.db" or "知识库 -> 入库" 整合
