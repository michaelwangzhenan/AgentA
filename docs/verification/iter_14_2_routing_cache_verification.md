# iter_14 模型路由 + 语义缓存 + 降本 验收报告

> 对照 [iter_14_enh §2.2.9](iter_14_enh.md) 的验收标准逐项核对。
> 验收方式：不耗 token 的项跑自动化（UT 全套 / 前端 typecheck）直接拿结果；耗 token / 需真实环境的项给出验证路径 + UT 覆盖证据，需真实环境手动复核。
> 验收时间：2026-06-10。

## 1. 结论速览

| 维度 | 结果 |
|---|---|
| 后端 UT（`pytest -q`） | **1494 passed, 133 deselected**（含本期新增 39 条） |
| 本期新增 UT | model_router 13 + semantic_cache 7 + savings_store 4 + chat_routing 15 = **39 passed** |
| 前端 typecheck（`tsc --noEmit`） | **0 error** |
| 后端 / 全 app import 冒烟 | **PASS**（无循环依赖） |
| config 三处同步 | **达标**：7 项均在 `config.py` / `.env.example` / `.env` |
| P0/P1 review | **无遗留**（详 §4） |

## 2. 验收标准逐项核对

对照 §2.2.9「验收标准」7 条：

| 编号 | 标准 | 验收方式 | 结果 |
|---|---|---|---|
| V1 | auto 档下简单问路由到更便宜模型、复杂问不降级 | `test_model_router`：`test_auto_baseline_is_pool_top` / `test_easy_query_downgrades_to_cheapest` / `test_hard_query_keeps_baseline` | ✅ UT 锁定规则映射 + 向下约束 |
| V2 | 手选模型遇瞬时错误回退到自选模型 | `test_chat_routing` 瞬时错误判定 + `chat.py` fallback 编排（fresh + downgraded + transient → 回退 `decision.baseline`） | ✅ 判定逻辑 UT 覆盖；端到端回退需真实 LLM 触发 429/5xx 手动复核 |
| V3 | 相近问法命中缓存、延迟显著下降 | `test_semantic_cache`：`test_put_then_hit` / `test_miss_below_threshold`（阈值边界） | ✅ 存取 + 阈值逻辑 UT 覆盖；真实相似度命中需起服务 + 真实 embedding 手动复核 |
| V4 | KB 更新后相关缓存失效、不返回过期答案 | `ingest.py` 三处写盘后调 `invalidate_all_soft`（全量作废）+ `test_semantic_cache` 过期惰性删除 | ✅ 失效钩子接入 + 过期过滤 UT 覆盖 |
| V5 | 跨用户查不到彼此缓存 | `test_semantic_cache::test_user_isolation`（`where={"user_id"}` 过滤） | ✅ UT 锁定按 `user_id` 隔离 |
| V6 | 「降本」面板能看命中率 / 估算节省 / 趋势 | `test_savings_store`（汇总 / 趋势 / 删号级联）+ `usage.py` 只读端点 + 前端 `SavingsPanel.tsx` | ✅ 后端聚合 UT + 端点 + 前端组件齐备 |
| V7 | 全程 log 可还原路由 + 缓存决策 | `model_router` / `semantic_cache` / `chat.py` 各环节 `logger.info`（路由 reason / 命中 sim / 写入 id / 失效） | ✅ 按 §2.2.5 日志方案埋点 |

> V2/V3 标"需手动复核"的部分：均为**需真实 LLM / 真实 embedding**项，核心代码路径（瞬时错误判定、回退编排、阈值/过期/隔离判定）已被 UT 锁住，逻辑正确性有保证，仅真实环境行为需起服务实测。

## 3. 本期改动范围

| 层 | 文件 | 说明 |
|---|---|---|
| 配置 | `src/config.py` / `.env.example` / `.env` | 7 项：`MODEL_ROUTING_ENABLED/MODE/CLASSIFIER_MODEL` + `SEMANTIC_CACHE_ENABLED/COLLECTION/THRESHOLD/TTL_DAYS` |
| 路由 | `src/llm/model_router.py`（新） | 规则/分类器/hybrid 难度判定 + 向下约束 + 候选池持久化（`.agenta/routing_pool.json`）+ 软失败 |
| 缓存 | `src/memory/semantic_cache.py`（新） | 独立 ChromaDB collection；命中/写入/失效 + `user_id` 隔离 + 过期 + 软失败入口 |
| 降本采集 | `src/memory/usage_store.py` | 加 `saving_events` 表 + `record_saving` / `aggregate_savings` / `savings_series`；删号级联清 |
| 失效钩子 | `src/rag/ingest.py` | `ingest_one` / `delete_kb_document` / `delete_all_kb_documents` 写盘后全量作废缓存 |
| agent | `src/agent/agent.py` | `final_answer` trace 透传 `used_tools` / `personalized`（供缓存可写判定） |
| 编排 | `src/api/routes/chat.py` | 双端点：查缓存→路由→fallback→写缓存→记节省；流式首轮 error 帧暂存 |
| API | `src/api/routes/routing.py`（新）/ `usage.py` / `auth.py` / `admin.py` / `main.py` | 候选池 GET/PUT（admin）；降本看板只读端点；`llm-prefs` 放行 `auto`；删号清缓存 |
| 前端 | `Composer.tsx`（auto 选项）/ `useComposerSettings.ts` / `usage/SavingsPanel.tsx`（新）/ `UsageView.tsx` / `settings/RoutingPoolConfig.tsx`（新）/ `ApiKeysConfig.tsx` / `api/client.ts` / `types/{usage,routing}.ts` | 模型选择加「自动」；用量页加「降本」面板；API key 页加候选池勾选 |
| 测试 | `tests/test_{model_router,semantic_cache,savings_store,chat_routing}.py` | 4 个新测试文件，共 39 条 |

## 4. Review 发现与处理

| 级别 | 现象 | 处理 |
|---|---|---|
| — | 流式 fallback 时首轮 LLM error 帧会先到前端，再出正常答案 | 设计阶段规避：fresh+downgraded 时暂存 error 帧，成功回退则丢弃，真失败才 flush（`chat.py` `_state["held_errors"]`） |
| — | fallback 重试会重复 append 用户消息 → 历史脏 | 回退前 `history.clear`（仅 fresh 会话，只一条 user 消息），再重跑 |
| — | `auto` 哨兵若漏到 `get_active_model` 会因不在 `MODEL_CONFIGS` 报错 | `chat.py` 在 `use_llm_prefs` 前总把 `auto` 经 `route()` 解析成具体模型；CLI 不走该路径 |
| P2（已知限制） | 同一 query 重复写入会在 collection 累积多条 | TTL 过期 + KB 变更全量作废兜底；高频去重留后续 |
| P2（已知限制） | 运行中改 `EMBEDDING_MODEL` 致缓存向量维度不一致 | 查询/写入软失败回落正常流程，不阻断；KB 变更不改 embedding 模型 |

未发现 P0 / P1。

## 5. 验收命令

```bash
# 后端全量（fast 集）
pytest -q
# 本期新增
pytest -q tests/test_model_router.py tests/test_semantic_cache.py tests/test_savings_store.py tests/test_chat_routing.py
# 前端类型
cd frontend && npx tsc --noEmit -p tsconfig.json
```
