# iter_15 UT 精简与重构 验收报告

对照 `docs/iter_15_ut_refine.md` §7 人工测试方案逐项验收。

## 1. 验收结果总览

| 用例 | 验收标准 | 结果 |
|---|---|---|
| 默认套件全绿 | `pytest -q` 全 pass | ✅ **1578 passed, 33 deselected, 117.75s** |
| collection 提速 | 不 import langchain/autogpt，远 < 65s | ✅ **6.11s**（原 ~65s） |
| db 隔离生效 | 真实 db mtime 不变 | ✅ 冷库 learning/quiz/srs/user_memory 多次全量跑后 mtime 全未变 |
| optional 不默认跑 | 默认收集不含 92 个 | ✅ 默认收集 1611（不含 optional） |
| optional 可手动跑 | 能收集执行 | ✅ `pytest tests/optional -m "langchain or autogpt"` → 92 collected |
| slow 默认不跑 | 默认不含 slow case | ✅ slow 计入 33 deselected |
| UT 配置生效 | `resolve_ut_llm_model` 逻辑正确 | ✅ TestUTLLMModel 3 用例 pass |

## 2. 关键数据对比

| 维度 | 改造前 | 改造后 |
|---|---|---|
| 默认收集 case | 1720（1587 跑 + 133 deselect） | 1611（1578 跑 + 33 deselect） |
| 默认不收集（optional） | 0（langchain/autogpt 仍 import） | 92（--ignore，不 import） |
| collection 耗时 | ~65s | **6.11s** |
| 默认套件耗时 | ~125-150s | **117.75s** |
| 测试目录结构 | 80 文件平铺 | 9 子目录按 src 分包 |

## 3. 各需求/类别落地

### 需求0：db 隔离兜底 ✅
`tests/conftest.py` 扩展 autouse fixture，把 9 个业务 store 单例统一 reset 到独立 `:memory:` 实例。
- 修复根因：`Agent.run` 经 `build_active_study_plan_block` 只读真实 learning.db 的泄漏。
- 用 `:memory:` 而非 tmp 文件：实测每测试固定开销从 ~0.49s 降回 ~0.08s（4 用例 1.97s→0.33s），全量耗时未因兜底显著上升。

### 需求1：目录重组 ✅
79 个测试文件 `git mv` 到 `tests/{agent,api,cli,llm,memory,rag,skills,security,optional}/`（保留 git 历史）。
- 修正移动副作用：`test_rag_parser.py` / `test_skills_skill_loader.py` 里基于 `__file__` 的相对路径（多一级目录）已改 `parents[2]`。

### 需求2：UT 专用配置 ✅
新增 `UT_LLM_MODEL`（config.py + .env + .env.example）+ `resolve_ut_llm_model()` + conftest `ut_llm_model` fixture。

### 类别A：备用实现移出扫描 ✅
langchain/autogpt 测试移到 `tests/optional/`，`pytest.ini` 加 `--ignore=tests/optional`。collection 提速主因。

### 类别B：integration 删质量类 ✅
删 `test_agent.py` 的 2 个「回答长短/质量」类 integration（连通性/行为已被其它测试覆盖）。其余 integration 经审计均为连通性/功能性，保留。

### 类别C：extended_providers 收敛 ✅
`test_llm.py` 删 17 个 provider 配置参数化，改为遍历式完整性检查 + 新增 3 个 UT 配置测试（`TestUTLLMModel`）。

### 类别E：slow marker ✅
注册 `slow` marker（默认 deselect）。已标：parser Office 解析 3 类、mcp 超时 2 个、kb ingest 超时 1 个。

### 类别D：保守去重 ⚠️ 待你确认
**评估结论**：SRS/quiz/study/security 的「跨层重复」经逐层核对，是**有意的分层覆盖**（store 测 DB 行为、tool 测 execute_tool 入参、api 测 HTTP 串联、scheduler 测算法），各层断言层次不同，并非纯冗余。

激进删除这些分层测试会与硬约束「精简不能影响 UT 对工程质量的保护」冲突。按工程公约（冲突需用户同意），**本期未做激进跨层删除**，保留分层测试维持保护力。

如需进一步压缩，可选方向（需你确认，会牺牲部分集成覆盖）：
- API 层 review 用例只留状态码 + 持久化冒烟，去掉 SM-2 数值断言（数值已由 scheduler 层锁定）。

## 4. 结论

需求0/1/2 + 类别 A/B/C/E 全部落地并通过验收；类别 D 经评估保守处理，待你就「是否接受牺牲部分集成覆盖换更激进去重」拍板。核心主力路径保护一条未减。
