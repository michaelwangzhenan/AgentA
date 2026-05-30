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
name: ci

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

jobs:
  test:
    name: fast UT
    runs-on: ubuntu-latest
    timeout-minutes: 10

    env:
      # 让 test_provider_api_key_not_empty 等"非空校验"型 UT 通过
      # 这里只要求字符串非空，不真发 LLM 请求；故用明文 dummy，无需 Secrets
      MOONSHOT_API_KEY: ci-dummy
      QWEN_API_KEY: ci-dummy
      # 关掉 HF 镜像 / 离线开关，避免 transformers 启动时尝试连接镜像
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

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run fast UT
        # pytest.ini 默认 addopts 已 deselect integration/langchain/autogpt/extended_providers
        # 直接 pytest 即可，无需重复加 -m
        run: pytest -q
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

## 3.5. 实施步骤（按顺序）

| # | 动作 | 验证方式 |
|---|---|---|
| 1 | 新建 `.github/workflows/` 目录，写入 §3.3 的 `ci.yml` | 文件存在 |
| 2 | commit + push 到一个临时分支（不要直接动 main） | GH Actions tab 看到 run 起来 |
| 3 | 等 run 跑完，确认绿 | run 详情显示 `passed`，pytest 输出含 `0 failed` |
| 4 | （可选）开 PR，确认 PR 页面底部 "checks" 出现 | PR 上看到 ci check |
| 5 | merge 到 main 后，再 push 一次空 commit 验证 main 触发正常 | Actions tab 出现 main 的 run |
| 6 | （可选）`Settings` → `Branches` → 给 main 加 branch protection rule，要求 ci 必须绿 | 之后红的 PR 不可 merge |
| 7 | （可选）README 顶部加 badge | README 主页显示绿勾 |

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
