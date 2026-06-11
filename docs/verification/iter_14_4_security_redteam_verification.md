# iter_14 AI 安全 / 红队模块 验收报告

> 对照本期需求/设计（[iter_14_enh.md](../iter_14_enh.md) §4）逐项核对。
> 验收方式：不耗 token 的项跑自动化（UT 全套 / 评估器 `--no-llm` / CI 门禁链路）直接拿结果；耗 token 的项（direct / indirect / info_leak 需真实 LLM）给出验证路径与 UT 覆盖证据，需真实环境手动复核。
> 验收时间：2026-06-10。

## 1. 结论速览

| 维度 | 结果 |
|---|---|
| 后端 UT（`pytest -q`） | **1585 passed, 133 deselected**（含 `test_security_adversarial.py` 25 条 + `test_security_event_store.py` 9 条） |
| 评估器 `--no-llm`（确定性子集） | **24/24 PASS**（15 tool_blocklist + 9 ssrf），拦截率 100% / 误拦率 0% |
| CI 门禁（`run_all --ci`） | **PASS**：安全拦截子集含 ssrf，退出码 0 |
| 数据集规模 | 38 → **75 例**（新增 ssrf 9 / info_leak 6 / direct+4 / rag+2 / web+2 / tool+2） |
| 既有功能回归 | **无破坏**：防御本体（`security_filter` / `url_guard`）逻辑零改动；现有安全 UT 全绿 |
| 配置同步 | **无新增 `.env` 项**（设计 §4.2.7：阈值是脚本常量，不进运行时配置） |

## 2. 验收标准与核对（对照 §4.2.9）

| 标准 | 验收方式 | 结果 |
|---|---|---|
| `adversarial` 跑出含 6 类分项的报告 + sidecar JSON | 跑 `--no-llm` 生成 `security-adversarial-<ts>.md` + `.json`；`_render_markdown` 用 `_KIND_ORDER` 出分项 | ✅ |
| `--no-llm` 仅跑 tool + ssrf | `_NO_LLM_KINDS={tool_blocklist, ssrf}`；实跑 24 case 全为该两类；`TestNoLlmKinds` 锁定 | ✅ |
| `run_all --ci` 把 ssrf 纳入门禁、跌破即非零退出 | `run_all --ci` PASS（exit 0）；评估器退出码由 `_compute_metrics["passed"]` 驱动（recall<90% 或 fpr>10% → 1） | ✅ |
| S1 SSRF：内网/localhost/file/云元数据/rebinding/解析失败拦截，公网放行 | `TestSsrfRunner` 6 条 + 实跑 U01~U09 全 PASS（mock DNS，不发网络请求） | ✅ |
| S2 信息泄露：禁词命中=leaked、未命中=blocked，复用 direct runner | `TestInfoLeakRunner`（mock chat：拒答→blocked / 泄露→leaked + violations） | ✅ 逻辑；真实 LLM 拦截率需手动跑 `--kind info_leak` |
| S3 扩样本：编码混淆 / 多语言 / 嵌套组合 / 良性强化 | dataset 新增 D13~D16 / R14~R15 / W13~W14 / U/L 全量；每条带 `note` 标手法 | ✅ |
| S4 看板：总+逐类拦截率/误拦率+趋势 | `/eval/security/summary` + `/trend`（admin only）；`SecurityPanel` + QualityView「安全」tab；`tsc`/lint 0 error | ✅ 代码 + API UT；真实视觉需起服务看 |
| sidecar 字段正确、解析软失败 | `TestSidecar`（partial 标记 / kinds_run）+ `test_security_summary_skips_corrupt`（坏 JSON 跳过不崩） | ✅ |
| 防御本体行为不变 | `test_url_guard` / `test_security_filter` / `test_tool_blocklist` 全绿（未改这三个模块） | ✅ |

> 标"需手动复核"的 info_leak / direct / indirect 真实拦截率：均为耗 token 项（设计 D2 不进 PR 门禁），核心判定逻辑已被 UT 锁住，仅真实 LLM 输出质量需起环境实测（`python -m tools.agent_eval.security.adversarial --kind info_leak`）。

## 3. 本期改动范围

| 层 | 文件 | 说明 |
|---|---|---|
| 评估器 | `tools/agent_eval/security/adversarial.py` | 新增 `_run_ssrf_case`（mock DNS）；注册 `ssrf` / `info_leak`（后者复用 `_run_direct_case`）；`_compute_metrics` / `_build_sidecar` 抽取；6 类分项渲染；`--no-llm` 改用 `_NO_LLM_KINDS`；写 sidecar JSON |
| 数据集 | `tools/agent_eval/security/dataset.json` | 38 → 75 例（ssrf U01~U09 / info_leak L01~L06 / 各类变体） |
| API schema | `src/api/schemas/eval.py` | `SecurityKindRow` / `SecuritySummary` / `SecurityTrendPoint` / `SecurityTrend` |
| API 路由 | `src/api/routes/eval.py` | `/eval/security/summary` + `/eval/security/trend`（admin、读 sidecar、解析软失败） |
| 前端类型 | `frontend/src/types/eval.ts` | 对齐上述 4 个安全类型 |
| 前端接口 | `frontend/src/api/client.ts` | `getSecuritySummary` / `getSecurityTrend` |
| 前端 UI | `frontend/src/components/eval/SecurityPanel.tsx`（新）/ `QualityView.tsx` | 总指标卡 + 逐类分项表 + 趋势；QualityView 加「安全」tab |
| 测试 | `tests/test_security_adversarial.py`（新） | ssrf/info_leak runner、指标聚合、sidecar、`--no-llm` 口径、安全 API（含路径隔离 monkeypatch） |

## 4. Review 发现与处理

| 级别 | 现象 | 处理 |
|---|---|---|
| 既有缺口 | `direct`/`indirect_rag`/`indirect_web` 的良性 case 若 `must_not_contain` 为空，旧 runner 恒判 `blocked=True`，全量 LLM 跑时虚高 FPR | **已修**（用户授权）：抽 `_judge_llm_answer`，良性改按"拒答指纹"（`_looks_like_refusal`）判定——拒答 / 清洗提示才算误拦，正常作答记 `answered`；攻击类不变。`TestBenignJudging` 4 条锁定（正常作答→answered、拒答→blocked、正文中部"无法"不误判） |
| 设计自决 | sidecar 在 `--no-llm` / 缺类时跑会得到 `partial=True` 的汇总，趋势里可能混入"部分跑" | 前端面板对 `partial` 显式打标（"部分类别"徽标），不静默；趋势点带 `partial` 标记 |

未发现 P0/P1。

## 5. 决策记录（复杂任务按业内标准自决）

| 决策点 | 选择 | 理由 |
|---|---|---|
| info_leak 是否独立 runner | 否，复用 `_run_direct_case` | 同构（system prompt + query + `must_not_contain`），DRY；仅 dataset 区分 kind |
| 良性判定口径 | 拒答指纹（`_looks_like_refusal`）单一机制 | 比"逐条标注良性答案关键词"简洁；直接对应"误拦 = 防御把正常请求拒了"，无需改数据集 |
| ssrf 域名 case DNS | mock（`mock_resolve` 字段，"FAIL" 模拟解析失败） | 确定性 + 不发真实网络请求，符合公约「UT 不真发外部请求」 |
| sidecar 存储 | 文件 JSON（不进 `usage.db`） | 评估离线低频，文件最简；趋势读目录历史 JSON |
| 安全报告呈现 | 结构化面板（非仅复用 markdown ReportsViewer） | 用户选「完整档」；逐类指标 + 趋势比看原始 markdown 更有价值；markdown 仍可在「评估报告」tab 看 |

## 6. 后续建议（不在本期 scope）

- 暂无遗留项（良性评估口径缺口已于本期一并修复，见 §4）。

## 7. §4.3 实时安全监控 追加验收

线上真实拦截统计（对话进行中触发防御即记录），与离线红队评估并列展示。

| 维度 | 结果 |
|---|---|
| 后端 UT | `test_security_event_store.py` **9 条全绿**（存储读写 / 用户过滤 / 倒序限量 / 删号级联 / 软失败 / runtime API） |
| 前端 tsc | 0 error |
| 既有功能回归 | 埋点全程软失败（`record_security_event` 吞异常），不阻断对话；普通链路行为不变 |

| 标准（对照 §4.3.3） | 验收方式 | 结果 |
|---|---|---|
| 三类拦截被记录（scrub / tool / ssrf） | 5 处调用点埋点：`retriever.format_search_results`、`tools` 的 web/fetch/mcp scrub + ssrf + `execute_tool` 名单门 | ✅ |
| 软失败不阻断对话 | `TestRecordSoftFail::test_record_swallows_store_error`（store 抛异常时 `record_security_event` 不上抛） | ✅ |
| 归属 user_id | `record_security_event` 读 `current_user_id()`；`test_record_uses_current_user` | ✅ |
| runtime API 区间汇总 + 分类型 + 最近 | `/eval/security/runtime/summary`（admin）；`test_runtime_summary_api` | ✅ |
| 前端「实时安全监控」区 + 旧区改「离线安全评估」 | `SecurityPanel` 拆 `RuntimeMonitor` + `OfflineEval` 两区；总数 + 分类型卡 + 最近列表 | ✅ 代码 + tsc；真实视觉需起服务看 |

本期改动追加：

| 层 | 文件 | 说明 |
|---|---|---|
| 存储 | `src/memory/security_event_store.py`（新） | `SecurityEventStore`（`usage.db` `security_events` 表）+ `record_security_event` 软失败 |
| 埋点 | `src/rag/retriever.py` / `src/agent/tools.py` | 5 处调用点记录拦截，不污染 `security_filter` / `url_guard` 纯函数 |
| API | `src/api/deps.py` / `schemas/eval.py` / `routes/eval.py` | `get_security_event_store` + `SecurityRuntimeSummary` + `/eval/security/runtime/summary` |
| 前端 | `types/eval.ts` / `api/client.ts` / `SecurityPanel.tsx` | 实时类型 + `getSecurityRuntimeSummary` + 实时/离线两区 |
| 测试 | `tests/test_security_event_store.py`（新） | 存储 + 软失败 + runtime API |

**埋点方式决策（D1）**：拦截点直接记（读 `current_user_id`、懒 import store、逐条写、软失败）。理由：拦截低频、IO 可忽略；比 EventBus 聚合改动小、即时不丢。归属先只记 `user_id`。

## 8. 实测发现并修复：MCP fetch 绕过 SSRF 防御 🔴

手动验证实时拦截时发现：配了 MCP fetch-server 后，agent 用的是 MCP `fetch.fetch`（内置 `fetch_url` 被隐藏），而 `_execute_mcp_tool` **未过 `url_guard.is_url_safe`** —— `fetch.fetch` 可抓内网 / localhost / 云元数据（日志实测请求打到了 `127.0.0.1:8000`）。`url_guard.py` 注释本就要求「MCP fetch 与内置共用同一道防线」，代码漏接。

**已修**：`_execute_mcp_tool` 转发前，对带 `url` 参数的 MCP 工具先跑 `is_url_safe`，拦下则返回 error + 记一条 `ssrf`（实时监控也能记到 MCP 路径的 SSRF 拦截）。`TestMcpFetchSsrfGuard` 2 条锁定（坏 url 不转发并记 ssrf / 安全 url 正常转发）。


