# 0. 目的
当前 AgentA 项目已经一定规模，并包含了 UT 和 评估工具。需要一个 CI 流程，来保证代码质量。
代码现在是在 GitHub(GH)上存储的，想要一个最经济且方便的CI 方案并实施。

# 1. 情况摸底

## 1.1. GH 自带工具：GitHub Actions

GH 自带的 CI 工具叫 **GitHub Actions（GHA）**，原生集成在每个 GH 仓库里：

| 维度 | 现状 |
|---|---|
| 入口 | 仓库顶部 `Actions` tab；workflow 文件放在 `.github/workflows/*.yml` |
| 触发 | push / pull_request / 定时 / 手动 / 标签 / 发布 等都能触发 |
| 运行环境 | GH 托管的 ubuntu / windows / macOS runner，按需起虚拟机；也可挂自己的 self-hosted |
| 生态 | Marketplace 上一堆官方/社区 action（`actions/checkout`、`actions/setup-python` 等都是官方维护） |

无需额外注册第三方 CI 服务（不用 Travis / CircleCI / Jenkins），仓库里加 yml 文件即开即用。

## 1.2. 是否免费

对本项目（个人仓库 + 学习用途）**完全够用且免费**：

| 仓库类型 | 免费额度 | 我们的情况 |
|---|---|---|
| 公开仓库 | 标准 runner **完全无限免费** | 公开仓库走这条 |
| 私有仓库 | Free 套餐每月 2000 分钟 ubuntu runner | 私有也够用：单次 fast UT 跑完 1-2 分钟，每月 1000+ 次 push 才打满 |

ubuntu runner **比 windows 便宜 10 倍**（私有仓库计费时 windows × 2、macOS × 10），所以默认只用 ubuntu。

## 1.3. 搭建 effort

实测是个**小时级**任务，不是天级：

| 步骤 | 预估时间 |
|---|---|
| 写 `.github/workflows/ci.yml`（下面给完整模板） | 10 分钟 |
| 仓库 Settings → Secrets 加 dummy 占位（仅 2 个非空 key 校验需要） | 5 分钟 |
| push 后看 Actions 跑通、修红 | 15-30 分钟 |
| 加 README badge + branch protection（可选） | 10 分钟 |

**没有付费、没有外部账号注册、没有自建 runner 的环节**。

# 2. GH CI 工作流

假设 §3 的 yml 已经合入仓库，日常开发流程是这样的：

## 2.1. 触发时机

| 事件 | 行为 |
|---|---|
| `git push` 到任何分支 | runner 自动起一个 job，跑 fast UT |
| 开 / 更新 PR | PR 页面底部出现 "checks" 区块，绿勾 = 通过、红叉 = 失败 |
| 改了 `.github/workflows/ci.yml` 本身 | 同样按 push 触发，验证 workflow 自身可用 |
| 手动 `workflow_dispatch` | 在 Actions tab 点 "Run workflow" 即可（用于重跑、改完不想 push 空 commit 时） |

## 2.2. 在哪看结果

| 视角 | 路径 |
|---|---|
| 仓库全局 | `Actions` tab → 看每次 run 的结果列表 |
| 单次 PR | PR 页面下方 "All checks have passed/failed" 区块，点进去直达对应 run |
| 失败定位 | run 详情页 → 点失败的 step → 展开日志（pytest 输出原样保留） |
| 历史趋势 | Actions tab 自带按 workflow 分组的历史，最近 90 天日志保留 |

## 2.3. 失败处理流程

CI 失败属于"红 → 修绿"的标准 loop，**没有线上事故压力**：

1. 点进失败 step 看 pytest 报错（行号 / assertion / traceback）
2. 本地复现：`pytest tests/test_xxx.py::TestY::test_z`
3. 修完 commit + push → 同一个 PR 自动重跑，绿了即可

## 2.4. 仓库防护：把 CI 当 merge 前置条件（可选）

`Settings` → `Branches` → `Branch protection rules` → 给 `main` 分支勾上 "Require status checks to pass before merging" + 选中 `ci` 这个 check 名 —— 之后任何 PR 都必须 CI 绿才能 merge，杜绝"红的 PR 直接合入"。

## 2.5. README 状态徽章（可选）

在 README 顶部加一行：

```markdown
![CI](https://github.com/<owner>/AgentA/actions/workflows/ci.yml/badge.svg)
```

效果：仓库首页就能看到当前 main 分支的 CI 是绿是红。

# 3. 如何搭建

## 3.1. 范围划定

**纳入 CI（每次 push / PR 必跑）**：

- 默认 fast UT 集：等价于 `pytest -q`（marker 配置已在 `pytest.ini` 里 deselect 掉 `integration` / `langchain` / `autogpt` / `extended_providers`）
- ✅ 用 mock，不发真实 LLM 请求；30 秒级跑完
- ✅ 覆盖 ~400 个 case，已是项目主力质量门

**不纳入 CI**：

| 排除项 | 原因 |
|---|---|
| `tools/agent_eval/**` 评估脚本 | 调真实 LLM，需 API key + 花钱 + 慢；按 [iter_2_agent.md §4.10](iter_2_agent.md#410-评估报告输出约定) 由人工触发 |
| `pytest -m integration` | 真实网络 / 真实 ChromaDB |
| `pytest -m langchain / autogpt / extended_providers` | 重依赖或额外凭据，本期不在主线 |
| Chainlit / main.py 端到端跑 | 需要本地知识库 + 模型缓存，不适合 CI |

## 3.2. 处理"非空 API key 校验"的 2 个 UT

fast UT 集里有 2 处会读 env：

| 测试 | 检查内容 | CI 应对 |
|---|---|---|
| `tests/test_llm.py::test_get_active_config_returns_provider_config` | `ACTIVE_PROVIDER`（默认 `kimi`）的 api_key 非空 | 设 `MOONSHOT_API_KEY=ci-dummy` |
| `tests/test_llm.py::test_provider_api_key_not_empty[kimi/qwen]` | `kimi` / `qwen` 两 provider api_key 非空 | 设 `MOONSHOT_API_KEY` + `QWEN_API_KEY` 为非空占位 |

这两个 UT **只校验字符串非空，不真调 API**。所以 CI 里直接写明文 dummy 值就够，不必走 GH Secrets：

```yaml
env:
  MOONSHOT_API_KEY: ci-dummy
  QWEN_API_KEY: ci-dummy
```

> 之所以不走 Secrets：Secrets 适合真敏感凭据；这里只要"非空字符串"，明文 dummy 反而让 CI 配置自洽、新人 fork 仓库也能直接跑。

## 3.3. workflow 文件：`.github/workflows/ci.yml`

完整可用版本，目录不存在要先建：

```yaml
name: AgentA CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

jobs:
  ut:
    name: fast UT
    runs-on: ubuntu-latest
    timeout-minutes: 10

    env:
      MOONSHOT_API_KEY: ci-dummy
      QWEN_API_KEY: ci-dummy
      TRANSFORMERS_OFFLINE: "1"
      HF_DATASETS_OFFLINE: "1"
      TOKENIZERS_PARALLELISM: "false"

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run fast UT
        run: pytest -q

  perf:
    name: perf regression
    runs-on: ubuntu-latest
    timeout-minutes: 10

    env:
      MOONSHOT_API_KEY: ci-dummy
      QWEN_API_KEY: ci-dummy
      TRANSFORMERS_OFFLINE: "1"
      HF_DATASETS_OFFLINE: "1"
      TOKENIZERS_PARALLELISM: "false"

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run perf_eval + grep gate
        # perf_eval 自身不退非 0；靠 grep FAIL 把"判据失败"翻成 step fail
        run: |
          python -m tools.agent_eval.perf_eval --target all --sizes 100,1000
          if grep -l 'FAIL' tools/agent_eval/reports/perf-*.md; then
            echo "::error::Perf judge failed; download 'perf-reports' artifact for details"
            exit 1
          fi

      - name: Upload perf reports
        if: always()  # gate fail 时最需要报告
        uses: actions/upload-artifact@v4
        with:
          name: perf-reports
          path: tools/agent_eval/reports/perf-*.md
          if-no-files-found: warn
          retention-days: 30
```

## 3.4. 关键设计点

| 决策 | 选项 | 选定 | 理由 |
|---|---|---|---|
| Runner OS | ubuntu / windows / macOS | **ubuntu-latest** | 私有仓库计费 1×（windows 2×、macOS 10×）；公开仓库虽然都免费，但 ubuntu 启动最快 |
| OS 矩阵 | 单 OS / 多 OS | **单 ubuntu** | 项目主用 windows 本地开发，但代码里没有 OS 特异路径 / 编码逻辑；多加 windows runner 边际收益低 |
| Python 版本 | 单一 / 矩阵 | **单 3.11** | 项目目标是个人学习，不发布 PyPI 包，无需多版本兼容矩阵 |
| 依赖缓存 | 不缓存 / pip cache / venv 缓存 | **pip cache**（setup-python 自带） | 一行 `cache: pip` 搞定，二次跑节省 ~2 分钟（chromadb / sentence-transformers / langchain 是大依赖） |
| Secrets 管理 | GH Secrets / 明文 dummy | **明文 dummy**（仅占位用） | 只校验非空，不真发请求；明文反而让配置自洽 |
| Lint | ruff / black / 不加 | **本期不加** | 项目优先级"简洁可读 > 全面"；ruff 留作后续叠加项（§3.6） |
| 覆盖率 | coverage 报告 / 不收集 | **不收集** | 个人项目，覆盖率数字目前不是决策依据；留作后续叠加项 |
| 触发分支 | 全分支 / 仅 main + PR | **main + PR** | 临时分支不必都跑；PR 必跑保住 merge 闸 |
| 失败超时 | 默认 6h / 自设 | **`timeout-minutes: 10`** | fast UT 跑完 1-2 分钟；卡死时 10 分钟自动断，不浪费免费额度 |
| Perf gate | 不跑 / snapshot only / **gate via grep FAIL** | **gate via grep FAIL** | `tools/agent_eval/perf_eval.py` 不调 LLM、纯 SQLite profiling，跑完 ~20s；本身不退非 0，CI 用 `grep` 报告里的 `FAIL` 字面量反向 fail step；报告 `actions/upload-artifact` 上传，diagnose 用。判据标记原本用 ✅/❌ emoji，CI 接入后统一改 `PASS/FAIL` 纯文本（见 `perf_eval.py` line 162/209）|
| Perf 数据规模 | size=5000（本地默认）/ size=100,1000 | **size=100,1000** | 比 5000 小，runner timing 抖动小；判据是绝对 ms（< 50 / < 200），1000 行依然能验证 SQL 路径不退化 |
| Perf artifact 上传时机 | 仅成功 / `if: always()` | **`if: always()`** | gate fail 时最需要看报告；不加这行，step 红了 artifact 不上传 |
| ut + perf 并行 / 串行 | 同 job 多 step（串行）/ 拆 2 job（并行）| **拆 2 job 并行** | 总耗时 = `max(ut, perf)` ≈ 1m20s，比串行 1m50s 快 30s；env 段重复一次（yml 没原生 anchor 复用），可读性 OK；checkout/setup/install 各跑一遍但 pip cache 命中开销小 |

## 3.5. 实施步骤（按顺序）

| # | 动作 | 验证方式 | 状态 |
|---|---|---|---|
| 1 | 新建 `.github/workflows/` 目录，写入 §3.3 的 `ci.yml` | 文件存在 | ✅ |
| 2 | commit + push 触发首跑 | GH Actions tab 看到 run 起来 | ✅ |
| 3 | 修首跑暴露的 fixture 缺失：`tests/test_parser.py` 依赖的 6 个 `datasets/data_en/test/test_sample.*` 被 `.gitignore` 屏蔽，逐层放行加白名单（`.gitignore:14-20`） | `git check-ignore -v` 命中 `!datasets/data_en/test/test_sample.*` 这一行 | ✅ |
| 4 | 再次 push，确认绿 | run 详情 `1057 passed, 3 skipped, 110 deselected`；首跑 3m10s（含 pip 装依赖 2m14s），后续 pip cache 命中可压到 1m 内 | ✅ |
| 5 | README 顶部加 CI 状态 badge（替换 `**Badges(TBD)**` 占位） | 仓库主页 README 顶部显示绿底徽章 | ✅ |
| 6 | `Settings → Branches → Add branch ruleset` 给 main 加规则：勾 "Require status checks to pass"，选 `fast UT` | 之后开 PR 时 ci 红则 merge 按钮变灰；裸 push 仍放行（这是个人项目刻意保留的弹性） | ⏳ 网页操作 |

## 3.6. 后续可叠加（不在本期）

按"看到痛点再加"的原则留 backlog：

| 后续项 | 触发条件 |
|---|---|
| `ruff check` 静态检查 | 团队增多 / 自己写错 import 频繁 |
| `pytest --cov` + Codecov badge | 想看模块级覆盖率分布时 |
| 多 Python 版本矩阵（3.11 + 3.12） | 准备发布 / 对外分发时 |
| Windows runner（用于 windows 编码 / 路径分支） | 出现"本地能过、Linux 不能过"或反之的 OS 特异 bug 时 |
| 定时跑 `integration` / `extended_providers` | 想长期监控真实 LLM 接口可用性时（每天一次，按月最多 30 × N 分钟） |
| 自动构建 chainlit 镜像 / 发布 release | 真要对外发布时 |

每项都是"现在不必加"，加上反而让 CI 又慢又脆。

# 4. GitHub Actions yml 语法与模型（参考）

§3 落地"具体怎么做"，本节补"为什么 yml 长这样"——后续要加 lint / nightly / release 等新 workflow 时回看这节。

## 4.1. 概念三层级：workflow / job / step

```
workflow（一个 yml 文件）
  └── jobs（一个 yml 里可以有多个 job）
        └── steps（一个 job 里多个顺序 step）
```

| 层级 | 数量关系 | 我们当前 |
|---|---|---|
| `.github/workflows/` 目录 | ≥ 1 个 yml 文件 | 1 个（`ci.yml`）|
| 一个 yml = 一个 workflow | 内部 ≥ 1 个 job | 1 个 job (`test`) |
| 一个 job | 内部 ≥ 1 个 step | 4 个 step（Checkout / Setup Python / Install deps / Run fast UT）|

**默认所有 job 并行**，加 `needs: [job_id]` 才串行。一个 yml 里多 job 的依赖图 GH 会自动画出（Actions 详情页可见）。

## 4.2. 文件位置与命名规则

| 元素 | 是否可改 | 说明 |
|---|---|---|
| 路径 `.github/workflows/` | ❌ 固定 | 必须放这个目录，GH 才扫得到 |
| 扩展名 `.yml` 或 `.yaml` | ❌ 固定二选一 | 都行 |
| **文件名** | ✅ 完全自由 | `ci.yml` / `lint.yml` / `release.yml` 都被平等对待；中文文件名也行但不推荐 |

**3 个名字可以全不一样**，注意区分：

| 名字 | 来源 | 用在哪 |
|---|---|---|
| 文件名 (`ci.yml`) | 文件本身 | **badge URL**（`actions/workflows/ci.yml/badge.svg`）认它 |
| `name:` 顶层字段（`ci`）| yml 第 1 行 | Actions tab 左侧菜单显示用它；不写则回退到文件名 |
| `jobs.<id>.name`（`fast UT`）| job 内字段 | **Branch protection 选 status check** 时认它 |

## 4.3. yml 顶层结构

```yaml
name: ci          # 1. 显示名（可省）

on:               # 2. 触发条件
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

jobs:             # 3. 干什么
  test:
    ...
```

3 个顶层 key 各管一件事：

| key | 作用 |
|---|---|
| `name` | UI 显示用（无逻辑影响） |
| `on` | **触发条件**——什么事件让 GH 起 runner |
| `jobs` | **要做什么**——一个 workflow 至少 1 个 job |

GH 把 yml 解析成"事件到来 → 看 `on` 是否匹配 → 匹配就调度 `jobs` 跑"。

## 4.4. `on:` 触发段

支持的事件覆盖了开发全生命周期，常用 4 种：

| 事件 | 触发时机 | 配置含义 |
|---|---|---|
| `push:` | git push 到远端 | `branches: [ main ]` 字面匹配；只在 main 分支 push 触发，feature/xxx push 不触发 |
| `pull_request:` | 开 PR / 改 PR | `branches: [ main ]` 这里是 **PR 目标分支**；任意分支 → main 都触发 |
| `workflow_dispatch:` | 手动从网页或 API 触发 | 给空对象（冒号后无内容）= 启用功能 |
| `schedule:` | 定时（cron 表达式） | `- cron: '0 19 * * *'` 每天 UTC 19 点；常用于 nightly integration |

**`[ main ]` 是 inline 数组**，等价于：

```yaml
branches:
  - main
```

可写多个：`[ main, develop ]`；通配：`[ '**' ]` 表示所有分支。

## 4.5. `jobs:` 与 `steps:` 细节

### 4.5.1. job 配置

```yaml
jobs:
  test:                       # ← job ID（任意起名）
    name: fast UT             # ← UI 显示名
    runs-on: ubuntu-latest    # ← runner
    timeout-minutes: 10       # ← 超时 kill
    env:                      # ← 环境变量（job 级）
      KEY: value
    steps:
      - ...
```

| 字段 | 含义 |
|---|---|
| `runs-on:` | runner 标签：`ubuntu-latest` / `windows-latest` / `macos-latest`；私有仓库计费 windows × 2、macOS × 10 |
| `timeout-minutes:` | 防卡死浪费免费额度；fast UT 给 10 分钟够用 |
| `needs:` | `[job_id, ...]` 串行依赖（默认并行）|

`env:` 三个层级——workflow / job / step——内层覆盖外层，按需选。

### 4.5.2. step 两种类型

**`uses:` 型——调现成 action**

```yaml
- name: Setup Python
  uses: actions/setup-python@v5
  with:
    python-version: "3.11"
    cache: pip
```

| 字段 | 含义 |
|---|---|
| `uses:` | 调已发布 action，格式 `<owner>/<repo>@<version>` |
| `with:` | 给 action 的入参（每个 action 自定义支持哪些参数，看 Marketplace 描述） |

`actions/checkout@v4` 几乎是所有 workflow 第一步——runner 是干净虚拟机，不 checkout 没你的代码。

**`run:` 型——跑 shell 命令**

```yaml
- name: Install deps
  run: |
    python -m pip install --upgrade pip
    pip install -r requirements.txt
```

| 元素 | 含义 |
|---|---|
| `run:` | 后面跟 shell 命令；ubuntu runner 默认 bash |
| `\|`（pipe） | yml 多行字符串语法，保留换行原样传 shell；多条命令用它 |

单行命令可省 `\|`：`run: pytest -q`。

## 4.6. yml 易错点

| 易错 | 现象 | 正确写法 |
|---|---|---|
| 缩进用 tab | `Workflow file invalid` | 强制 2 空格（GH yml 几乎都用 2 空格） |
| 冒号后无空格 | 解析错 | `name: ci` 不能写 `name:ci` |
| 数组 `-` 后无空格 | 解析错 | `- name: x` 不能写 `-name: x` |
| `python-version: 3.11` 不带引号 | 被当成浮点 3.11→3.1，装错版本 | `python-version: "3.11"` |
| `TRANSFORMERS_OFFLINE: 1` 不带引号 | env 值要求字符串，整数 1 部分 action 收不到 | `TRANSFORMERS_OFFLINE: "1"` |
| `branches: main`（漏方括号）| 部分情况能 work、部分场景不行 | `branches: [ main ]` 显式数组 |
| 一个 step 上一步失败、想继续跑 | 默认下游 step 跳过 | step 加 `if: always()` |

## 4.7. 多 job vs 多 workflow 取舍

未来要加 lint / nightly / release 时的判断标准：

| 场景 | 选择 | 理由 |
|---|---|---|
| 跑得**同源**（同事件触发，结果一起判定） | **同 yml 多 job** | UI 上是同一个 run，绿/红连带；branch protection 一次配齐 |
| 触发**不同**（cron / tag / manual 各异） | **不同 yml** | 比如 nightly 跟 ci 触发条件完全不同，混一个 yml 的 `on:` 段会很乱 |
| **关注点**完全独立 | **不同 yml** | lint 跟 UT 没必然关系，各管各方便单独 deselect / 重跑 |

按这个标准看本项目可能演化路径：

```
.github/workflows/
├── ci.yml          # 现状：push/PR 触发，跑 fast UT；后续叠 lint job 跟它一起跑
├── nightly.yml     # 待加：cron 触发，跑 integration / extended_providers
└── release.yml     # 待加：tag v* 触发，构建 chainlit 镜像 / 发版
```

每加一个新 workflow，就回 §3.4 那张决策表的同款风格，先把"决策 + 一句话理由"列清楚再写代码。

