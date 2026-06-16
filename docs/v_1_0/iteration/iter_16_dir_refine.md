# iter_16：目录与命名工程审查（讨论稿）

**范围**：`AgentA/` 仓库内除 `docs/` 以外的路径（含 `src/`、`tests/`、`frontend/`、`tools/`、仓库根目录配置与数据目录等）。  
**目的**：从工程专业角度对照「业内常见做法、可读性、可维护与可扩展」三项给出结论与改进方向。  
**状态**：仅讨论与记录决策点；**待你下达指令后再改代码或移动文件**。

---

## 1. 总评（一句话）

整体是**成熟的全栈单体（Python `src/` 布局 + React 前端 + 独立工具脚本）**，后端按领域分包（`agent` / `api` / `memory` / `rag` / `llm` / `cli` / `skills`）与 FastAPI 常见习惯基本一致；**当前最大可维护性风险在 `tests/`：同一主题测试在根目录与子包内重复出现，命名与归属不统一**。其余多为「边界清晰化」与「命名收敛」级别问题。

---

## 2. 与业内常见做法的对照

| 区域 | 当前形态 | 业内常见做法 | 符合度 |
|------|-----------|--------------|--------|
| Python 包根 | `src/` 下多顶级包 | `src/<product>` 单包或少量包 | 中高：多顶级包在中型项目里可接受，依赖清晰即可 |
| API | `src/api/routes/` + `schemas/` + `deps.py` + `main.py` | FastAPI 按路由/模型拆分 | 高 |
| 前端 | `frontend/src/{components,hooks,api,types,lib}` | React 按领域分组件 + 集中 `api`/`types` | 高 |
| 脚本与评估 | `tools/agent_eval/`、`tools/rag_eval/`、`tools/eval_common/` | `tools/` 或 `scripts/` 放非库代码 | 高 |
| 运行期数据 | 根下 `sqlite_db/`、`chroma_db/`、`logs/` 等 | 常配合 `.gitignore` 与文档说明「非版本资产」 | 中高：建议文档或常量集中说明路径语义 |
| 入口 | 根目录 `main.py`（CLI）+ `src/api/run.py` 等 | 部分项目用 `python -m` 统一入口 | 中：双入口可接受，需在 README 写清主路径 |

---

## 3. 做得好的地方（保持）

1. **`src/api` 分层**：路由、Pydantic 模型、依赖注入分离，和多数 FastAPI 开源项目一致，新人能按「找接口 → 找模型」定位。
2. **`src/memory` 以 `*_store` 为主**：持久化边界相对清楚（个别文件名例外见下文）。
3. **`frontend/src/components` 按业务域分子目录**（如 `chat/`、`settings/`、`usage/`），利于扩展新页面而不挤在单目录。
4. **`tools/` 与产品库解耦**：评估、数据集、一次性脚本不污染 `src/`，符合「库代码 vs 运维/实验脚本」的常见划分。
5. **工程内已有命名表**（`.cursor/rules` 中的约定）：与代码实际大体一致时，可维护性会明显好于「仅靠口头习惯」的项目。

---

## 4. 主要问题与风险（按优先级）

### P0：`tests/` 结构重复、同一逻辑多文件并存

**现象**：大量用例既出现在 `tests/test_<topic>.py`，又出现在 `tests/<area>/test_<topic>.py`（例如 API、memory、agent、cli、rag、security 等域下与根目录并存）。同一文件名在两层出现的情况也存在（如聊天 API、usage_store、runner_answer_quality 等）。

**影响**：

- 改行为时容易只改其中一份，**另一份漂移成假绿或重复维护**。
- CI 与本地跑全量时**重复执行同类测试**，拉长反馈时间。
- 新人无法判断「权威位置」是根目录还是子包。

**业内倾向**：**单一归属**——要么「全 mirror `src/` 结构」，要么「全扁平 + 命名前缀」，不要长期双轨。

**iter_16 已落实（本仓库当前态）**：

- 已删除 `tests/security/`，其中用例按被测模块迁入：`tests/agent/`（`security_filter`、`url_guard`、tool 黑名单）、`tests/memory/`（`security_event_store`）、`tests/api/test_api_security_adversarial.py`（含路由与 `tools.agent_eval.security` runner）。
- `tests/` 根目录仅保留 `conftest.py`、包级 `__init__.py` 等与收集配置相关文件；**若其它工作区仍残留根级 `tests/test_*.py` 与上表子目录重复，应删除根级副本，只保留子目录内一份。**

---

### P1：`src/core` 与 `src/agent/core` 的泛名重叠（已处理）

**原现象**：顶层曾有 `src/core/`（仅 `user_context.py`），与 `src/agent/core/` 都叫 `core`，口头易混。

**处理**：删除 `src/core/`，将 **`user_context.py` 并入 `src/memory/`**（`src/memory/user_context.py`）。语义上「当前请求的用户 id」与「按用户隔离的 SQLite 存储」同属持久化隔离层；依赖方向为 `src/api` / `src/agent` → `src.memory.user_context`，无 `*Store` → `agent.core` 反转。

**保留**：`src/agent/core/` 命名不变（引用面大）。

---

### P1：`src/api` 根部的非路由模块（已处理）

**原现象**：`api_keys.py` 与 `routes/api_keys.py` 同名不同责；`config_*.py` 与路由混在 `src/api/` 根下，导航成本高。

**处理**：`api_keys.py`、`config_hooks.py`、`config_meta.py`、`config_overrides.py` 迁入 **`src/api/runtime/`**；HTTP 仍在 `routes/`。import 统一为 `src.api.runtime` / `src.api.runtime.config_meta`。

---

### P2：`tools` 下三套命名：`agent_eval`、`rag_eval`、`eval_common`

**现象**：都是评估相关，前缀与粒度不一致。

**影响**：可理解性尚可，但**扩展第四类评估时缺少命名规则**（继续 `xxx_eval` 还是统一 `eval/xxx`）。

**改进方向（讨论）**：二选一即可——要么统一为 `tools/eval/<agent|rag|common>/`，要么文档里写死「新评估子目录命名规则」，避免每人一种风格。

---

### P2：技能相关资产双轨 — `.agenta/skills/` 与 `src/skills/`

**现象**：仓库内存在 Cursor/工程侧技能目录与运行时加载逻辑目录（需结合 README 才能一眼分清「谁消费谁」）。

**影响**：**不属于结构错误**，但若不在文档或目录 README 里写清，迭代时容易改错树。

**改进方向（讨论）**：在各自根目录放简短 `README.md`（一两段）说明用途与同步关系；是否合并目录属于产品/工具链决策，不单是「对错」问题。

---

### P3：`src/memory` 内个别命名与 `*_store` 略不一致

**现象**：例如 `user_memory.py`、`chat_history.py` 与同目录大量 `*_store.py` 并存。

**影响**：新人会猜「这是不是不算存储层」；实际若都是持久化或领域服务，**命名统一成 store/repository 或统一不成后缀但写清职责**会更稳。

---

### P3：仓库根目录运行期目录较多

**现象**：`chroma_db/`、`sqlite_db/`、`history/`、`logs/`、`resources/` 等与源码并列。

**影响**：业内常见；主要风险是**新成员误把运行期目录当「可手工编辑的源码树」**。若长期迭代，可考虑默认数据根（例如 `var/` 或 `data/`）集中，**属体验优化而非必须**。

---

## 5. 可维护性 / 可扩展性 / 可迭代性归纳

| 维度 | 结论 |
|------|------|
| **可维护** | API 与前端分层良好；`tests/` 已与 `src` 对齐；若再出现根目录与子包双轨用例需及时合并。 |
| **易扩展** | 新路由、新 `memory` store、新前端领域目录都有明确「落点」；`tools` 需补一条命名约定以免继续分叉。 |
| **可迭代** | 小步演进友好；**`tests/`、`src/core`、`src/api` 运行时模块归位** 已推进；余下为 `tools` 命名与运行期目录约定等。 |

---

## 6. 建议实施顺序（供你拍板，未执行）

1. ~~**治理 `tests/`**~~：**已落实**（mirror `src`，见上文 P0）。
2. ~~**消解 `src/core` 与 `src/agent/core` 的泛名冲突**~~：**已落实**——`user_context.py` 迁入 `src/memory/`，删除 `src/core/`。
3. ~~**整理 `src/api` 根目录单文件职责**~~：**已落实**——`api_keys` 与 `config_*` 迁入 `src/api/runtime/`（见上文 P1）。
4. **`tools` 命名规则文档化或轻度重排**。
5. **（可选）运行期目录约定**：数据根变量 + `.gitignore` 说明即可，不必强行物理合并。

---

## 7. 待你确认的讨论题

以下路径**仍待拍板**（其余见上文 P0 / P1 已落实）：

1. **测试目录**：已按 mirror `src/` 落实（见上文 P0）。
2. **`src/core` 去留**：已并入 `src/memory/user_context.py`，顶层 `src/core` 已删除（见 P1）。
3. **`tools` 大挪移**：是否接受为统一命名而调整 import 路径与 CI/文档中的命令示例（改动面中等）？

---

## 8. 本文档维护

- 讨论结论、取舍与最终方案可追加到本节或新开「决策记录」小节。
- 动手修改代码或目录时，建议在别份迭代文档中写**迁移步骤与回归方式**（本文仍可作为背景说明引用）。
