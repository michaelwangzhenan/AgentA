# iter_8~13 多轮聊天验证报告

> 数据来源：`logs/uvicorn.log`（截至 2026-06-10 08:35）。
> 验证方式：对照 iter_8~iter_13 的实现目标，从日志里找证据，逐项核对是否按设计跑通。
> 本次重点测试段：**2026-06-10 08:12~08:35**，单用户、session `17fee44d`，glm-4.7-flash，连续 ~15 轮对话（含知识库检索、make_plan、并行 web_search、自动记忆提取）。

## 1. 结论速览

| iter | 主题 | 日志验证结果 |
|---|---|---|
| iter_8 | 日志体系（格式 / 前缀 / session 关联） | 通过；并行工具 + 后台记忆线程曾丢 `s:`/`r:` 上下文，**已修复** |
| iter_9 | 主题皮肤 | 纯前端，日志无关，本次不覆盖 |
| iter_10 | 多用户并发 / RAG 提速 / 工具并行 | 通过：短 query 跳改写、精排不再召回归零、一轮内并行 web_search、plan 自适应放大轮次上限 |
| iter_11 | Token 用量统计 | 部分可证：`usage.db` 已初始化；**每轮落库条数日志里看不到，需在「用量」页核对**（P2） |
| iter_12 | API Key / rules / 记忆节流 | 通过：rules 长度上限配置在位；记忆节流按 `EVERY_N` 触发 |
| iter_13 | 记忆重构（扁平自然语言 + LLM 合并） | 通过：ADD/UPDATE 操作、source 标记、后台异步、fail-fast 均按设计；fail-fast 曾以 500 暴露给前端，**已修复（降级 503）** |

本次测试段（08:12~08:35）**全程无 ERROR、无 500、无 traceback**。下面列到的报错均来自 06-09 的开发调试段，多为外部原因（余额 / 限流）或一次性迁移现象，非本次回归。

## 2. 逐项核对（带证据）

### 2.1 iter_8 日志体系

做对的：

- 格式统一：`时间 [APP]/[ACCESS] [级别] [s:.. r:..] 文件:行 - 正文`，与 iter_8 §3.2 一致。
- 业务 / 访问分流：`[APP]` 与 `[ACCESS]` 前缀清晰可过滤。
- session 关联在**主链路有效**：检索、agent 推理日志都带 `s:17fee44d...`（如 L2858、L2861 串行 `search_knowledge`）。

发现的问题：

| 级别 | 现象 | 证据 | 说明 |
|---|---|---|---|
| P2 | 并行工具的日志丢 session/request（`s:- r:-`） | L2948~2958 三个并行 `web_search` 全是 `s:-`，同会话串行的 `search_knowledge`（L2859）却是 `s:17fee...` | iter_10 的并行执行（`tool_call_engine._run_parallel`）用 `ThreadPoolExecutor`，worker 线程只 set 了 `tool_progress` contextvar，没把 logging 的 session/request contextvar 带进子线程 |
| P2 | 后台记忆线程的日志丢 session/request | L2776、L2962、L3000 记忆更新均为 `s:- r:-` | iter_13 后台 daemon 线程同理：显式下传了 `user_id`（功能正确），但 logging contextvar 没传 |

影响：**仅日志可追溯性**——功能完全正常，但并发多 session 时无法只靠 `s:<id>` 把某次对话的工具/记忆动作串成一条链。

> ✅ **已修复**：`tool_call_engine._run_parallel` 改为每个任务在主线程 `contextvars.copy_context()` 后用 `ctx.run` 提交到线程池；`memory_manager.try_extract` 的后台线程同样用 `copy_context` 包裹。session/request（及 user/llm_prefs）contextvar 随之带进子线程，日志不再丢成 `s:-`。新增回归测试 `test_parallel_workers_inherit_logging_context` 锁住该行为。

### 2.2 iter_10 并发 / 提速 / 工具并行

| 优化点 | 日志证据 | 结论 |
|---|---|---|
| 短 query 跳过改写（`RAG_REWRITE_MIN_QUERY_LEN`） | 所有检索都是 `n_q=1`（L2747 等），未触发 multi-query | ✅ 生效 |
| 关 RAG critic（`HARNESS_RAG_ENABLED=false`） | 检索链路无 critic 采点日志 | ✅ 生效 |
| 精排候选数=2 不再召回归零 | `RERANKER_RECALL_MULTIPLIER=2`，L2756 `dedupe 16→10`、L2757 `truncate→8`，结果非 0 | ✅ iter_10 的 min_score bug 已治本 |
| 一轮内多工具并行 | L2948~2958：08:28:22 同一时刻发出 3 个 `web_search`，08:28:27 又 2 个并行 | ✅ 并行路径生效（代价是 §2.1 的 `s:-`） |
| plan 自适应放大轮次上限 | `make_plan`（L2898）后 caps 从 `(tool=10, total=16)` 升到 `(tool=18, total=22)`（L2899），plan 收尾后回落（L2943） | ✅ |
| 空内容去 tools 重试一轮（iter_10 项4） | L1007 `去 tools 重试一轮`、L1015 重试后仍空提前退出 | ✅ 兜底逻辑在（06-09 段） |

观察（非缺陷）：`search_knowledge` 仍偏慢——单 query 暖态编码 ~8s（L2861→L2862），冷态首次叠加 reranker 加载共 ~22s（L2747→L2754）。瓶颈是 CPU 上 `bge-m3` 编码，属 iter_10 标注「GPU 留待后续」的 P2 未做项，符合预期。

### 2.3 iter_11 Token 用量

- `usage.db` 已正常初始化（L2735、L2874 `UsageStore 初始化完成`）。
- 06-09 段有 `/api/usage/series`、`/api/usage/events` 等接口被正常调用并返 200（L538、L952~953）。

待确认（P2）：`record_usage` 是静默 insert，日志里看不到每轮落库的条数，本次无法从日志断言「run 次数 == usage 条数」。建议在「用量」页核对本测试段对话次数是否对得上（约 15 条），以验证 iter_11 §6 的 U1/U3。

### 2.4 iter_12 rules / 记忆节流

- `USER_RULES_MAX_CHARS=4000` 在 `.env` 在位（iter_12 §3 长度上限）。
- 06-10 段 `GET /api/rules` 正常 200（L2969~2970）。
- 记忆节流：自动提取按累计消息数触发，08:18/08:29/08:34 共 3 次，与 `EXTRACT_EVERY_N=5` 在 ~15 轮里的预期次数吻合（iter_12 §2.2 无状态节流）。

### 2.5 iter_13 记忆重构

| 设计点 | 日志证据 | 结论 |
|---|---|---|
| 扁平自然语言 + LLM 合并操作（ADD/UPDATE/DELETE） | L2776 `已改写 id=2`、L2962 `已改写 id=1`、L3000 `已新增 id=3`，均无类别标签 | ✅ ADD/UPDATE 已验证；本次未触发 DELETE |
| source 标记 + 应用操作统计 | L2778/2964 `记忆已更新 (source=auto): +0 ~1 -0`、L3002 `+1 ~0 -0` | ✅ 与 iter_13 §11.2 `apply_ops` 一致 |
| 后台异步、写入滞后于回答 | 08:17:51 本轮回答结束，记忆更新晚到 08:18:46 | ✅ 符合 §11.4「写入可能滞后」 |
| LLM 失败静默兜底 | L2266、L2598 `LLM 提取合并失败: 429` 仅 WARNING，未中断主链路 | ✅ 符合 §9 风险缓解 |
| 旧 schema fail-fast | L1789~1793 `RuntimeError: user_memory.db schema 已过期...请删除...重启` | ⚠️ 见下 |

发现的问题：

| 级别 | 现象 | 证据 | 说明 |
|---|---|---|---|
| P2 | fail-fast 以 500 + 完整 traceback 暴露给前端 | L1707、L1794：`GET /api/memory` 返 500，附 ASGI traceback | iter_13 的 fail-fast 设计本身对（旧库需删重建），但前端只收到 500，体验不友好。属一次性迁移现象——删库重建后 `GET /api/memory` 已恢复 200 |

> ✅ **已修复**：`deps.get_user_memory_store` 捕获旧 schema 的 `RuntimeError`，转成带操作指引的 **503**（detail 即"请删除 ./sqlite_db/user_memory.db 后重启"），不再裸抛 500 + traceback。`lru_cache` 不缓存异常，删库重建后下次调用自动恢复。memory / admin 两条消费路径统一覆盖。

## 3. 配置说明（避免误读）

测试时 UI 运行时覆盖（`.agenta/config_overrides.json`）优先于 `.env`，二者有差异属正常：

| 项 | `.env` | `config_overrides.json`（实际生效） |
|---|---|---|
| `USER_MEMORY_AUTO_EXTRACT` | false | **true** ← 所以日志里有 `source=auto` |
| `MAX_TOOL_ROUNDS` / `MAX_TOTAL_ROUNDS` | 8 / — | **10 / 16** ← 与日志 `caps=(tool=10, total=16)` 吻合 |
| `THINKING_ENABLED` | false | true（本测试段未观察到实际开思考） |
| `ACTIVE_MODEL` | — | glm-4.7-flash |

## 4. 待办建议（按优先级）

| 级别 | 事项 | 状态 |
|---|---|---|
| P2 | 并行工具 / 后台记忆线程补传 logging 的 session/request contextvar（修 §2.1 的 `s:-`），恢复并发链路可追溯 | ✅ 已修复（`copy_context`，含回归测试） |
| P2 | memory 接口捕获旧 schema 的 `RuntimeError`，把 500 降级为带指引的友好响应（§2.5） | ✅ 已修复（`deps.get_user_memory_store` 转 503） |
| P2 | 在「用量」页核对本测试段 usage 条数 == 对话轮数，补齐 iter_11 的人工验收（§2.3） | 待人工核对 |
| — | `search_knowledge` 暖态 ~8s/冷态 ~22s，若体感慢可走 iter_10 标注的 GPU / 托管 embedding 方向（本期不做） | 不做 |

> 修复回归：触达文件 `src/agent/core/tool_call_engine.py`、`src/agent/core/memory_manager.py`、`src/api/deps.py`、`tests/test_tool_call_engine.py`；全量 `pytest -q` **1413 passed, 133 deselected**，无回归。
