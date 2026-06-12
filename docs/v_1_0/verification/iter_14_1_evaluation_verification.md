# iter_14 评估 + 可观测 验收报告

> 对照 [iter_14_enh §1.2.8](iter_14_enh.md) 的验收标准逐项核对。
> 验收方式：不耗 token 的项跑自动化（UT 全套 / CI eval / 前端 typecheck）直接拿结果；耗 token 的项（真实 LLM / 真实 KB）给出验证路径与 UT 覆盖证据，需真实环境手动复核。
> 验收时间：2026-06-10。

## 1. 结论速览

| 维度 | 结果 |
|---|---|
| 后端 UT（`pytest -q`） | **1455 passed, 133 deselected**（含本期新增 42 条） |
| CI eval job（`run_all --ci`） | **PASS**：安全拦截 1/1，exit=0，出总报告 |
| 前端 typecheck（`tsc --noEmit`） | **0 error** |
| config 三处同步 | **达标**：5 项均在 `config.py` / `.env.example` / `.env` |
| P0/P1 review | **无遗留**：修了 1 个 P1（后台任务被 GC 隐患） |

## 2. 验收标准逐项核对

对照 §1.2.8 的 7 条标准：

| 编号 | 标准 | 验收方式 | 结果 |
|---|---|---|---|
| A1 | `run_all` 一条命令出总报告 | 跑 `python -m tools.agent_eval.run_all --ci` | ✅ 生成 `reports/run-all-20260610-104102.md`，含每项 PASS/FAIL + 关键指标 |
| A2 | 新指标（faithfulness / answer-relevance）有分数 | `test_judge_rag_metrics`（mock LLM 返回 JSON，校验解析出 0-5 分 + 理由） | ✅ UT 覆盖；真实分数需带 API key 手动跑 |
| A3 | chat 后 `usage.db` 有 trace | `test_trace_store`（`TraceCollector` 从事件流建 span + `record_trace`/`record_trace_safe` 落库 + 查询） | ✅ UT 覆盖采集→落库→读取全链路；真实对话 trace 需起服务手动复核 |
| A4 | 入库后 golden 库出现 pending 候选 | `test_rag_golden_gen`（mock LLM 生成候选，写 `status=pending, source=ai`）+ `test_api_kb` 上传钩子 | ✅ UT 覆盖；真实入库生成需带 API key 手动跑 |
| A5 | admin 管理页可 CRUD + 审核 | `test_api_eval`（golden 增删改查 + 改状态 + 按状态筛选 + 非 admin 拒绝）+ 前端 `GoldenManager.tsx` | ✅ 后端 UT + 前端组件齐备 |
| A6 | 看板能看概览 / 瀑布 / 趋势 | `test_api_eval`（overview / series / list / detail 端点）+ 前端 `TraceDashboard.tsx` | ✅ 后端 UT + 前端组件齐备 |
| A7 | CI eval job 能拦回归 | `.github/workflows/AgentA_CI.yml` 新增 EVAL job，跑 `run_all --ci`、grep `FAIL` 失败即 exit 1、传 artifact | ✅ 沿用 perf 门禁模式 |

> A2/A3/A4 标"需手动复核"的部分：均为**耗 token / 需真实环境**项，按 §1.2.6（D3）本就不进 CI；核心代码路径已被 UT 锁住（采集、落库、解析、生成、写入状态），逻辑正确性有保证，仅"真实 LLM 输出质量"需起服务实测。

## 3. 本期改动范围

| 层 | 文件 | 说明 |
|---|---|---|
| 配置 | `src/config.py` / `.env.example` / `.env` | 5 项：`TRACE_ENABLED` / `RAG_GOLDEN_DB_PATH` / `EVAL_AUTO_GOLDEN_ENABLED` / `EVAL_AUTO_GOLDEN_MAX_Q` / `EVAL_GOLDEN_USE_PENDING` |
| 存储 | `src/memory/golden_store.py`（新）/ `src/memory/trace_store.py`（新） | `GoldenStore`（独立 `rag_golden.db`，CRUD + 状态）；`TraceStore` + `TraceCollector`（复用 `usage.db` 加 trace 表） |
| 埋点 | `src/agent/agent.py` / `src/api/routes/chat.py` | 每轮 LLM 计时入 `final_answer` payload；chat 双端点采集 + 软失败落库 |
| 离线评估 | `tools/agent_eval/judge/rag_metrics.py`（新）/ `tools/agent_eval/run_all.py`（新） | faithfulness / answer-relevance 评委；聚合入口出总报告 |
| 入库钩子 | `src/rag/golden_gen.py`（新）/ `src/api/routes/kb.py` | 入库后台任务调 LLM 生成 golden 候选 |
| API | `src/api/routes/eval.py`（新）/ `src/api/schemas/eval.py`（新）/ `src/api/deps.py` / `src/api/main.py` / `src/api/routes/admin.py` | golden CRUD（admin）+ trace / 报告只读；删用户级联清 trace |
| 前端 | `frontend/src/components/eval/`（4 个新组件）+ `types/eval.ts` / `api/client.ts` / `Sidebar.tsx` / `App.tsx` | 「质量看板」：概览 + 瀑布 + 趋势 + golden 管理页 |
| CI | `.github/workflows/AgentA_CI.yml` | 新增 EVAL job |
| 测试 | `tests/test_{golden_store,trace_store,golden_gen,judge_rag_metrics,api_eval}.py` | 5 个新测试文件，共 42 条 |

## 4. Review 发现与处理

| 级别 | 现象 | 处理 |
|---|---|---|
| P1 | `kb.py` 入库钩子用 `asyncio.create_task` fire-and-forget，无引用持有，任务可能在跑完前被 GC | **已修**：模块级 `_bg_tasks` 集合持强引用，`add_done_callback` 跑完移除 |
| — | trace 采集若把 LLM 计时做成独立 `EVENT_INFO`，会污染事件序列、破坏既有事件断言测试 | 设计阶段已规避：计时数据塞进 `final_answer` payload 的 `trace` 字段，不新增公共事件 |
| — | 报告浏览 API 路径穿越风险 | 已加 `resolve()` + `parents` 白名单校验，`test_api_eval` 覆盖 |

未发现 P0。
