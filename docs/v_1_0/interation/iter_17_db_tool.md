
# 1. 背景

运行期数据落在 `db/chroma`、`db/sqlite`、`db/bm25`下，**没有统一的可视入口**，本工具可以查看各数据库结构和内容。

# 2. CLI

## 2.1. 命令和参数

入口：`python tools/db_show.py <子命令> [选项]`；`python tools/db_show.py -h` 打印帮助。

| 子命令 | 选项 | 功能 |
|--------|------|------|
| `summary` | — | 依次输出 Chroma、SQLite、BM25 三段摘要（统计为主，不含 Chroma 正文抽样）。 |
| `chroma` | `--sample N`（默认 `3`；`N=0` 表示仅统计、不抽样） | 列出各 collection 条数；`N>0` 时对每个 collection 抽样 `N` 条 chunk（正文截断 + 主要 metadata）。 |
| `chroma` | `--collection NAME`（可选，与上同行并用） | 只处理名为 `NAME` 的 collection；省略则处理持久化目录下**全部** collection。 |
| `sqlite` | — | 汇总「配置里各 `*_DB_PATH`」与 `db/sqlite/*.db`：每个文件下列表名及各表行数；敏感库不打印密钥类字段。 |
| `bm25` | — | 对每个 `bm25_*.pkl` 输出一条摘要（能加载则多报规模信息，失败则报原因 + 文件信息）。 |

## 2.2. 用法示例

```bash
python tools/db_show.py -h                              # 帮助
python tools/db_show.py summary                         # 三段统计摘要
python tools/db_show.py chroma                          # 各 collection 条数 + 默认抽样 3 条
python tools/db_show.py chroma --sample 0               # 仅统计，不抽样
python tools/db_show.py chroma --collection kb_zh       # 只看指定 collection
python tools/db_show.py sqlite                          # 各 .db 表名与行数
python tools/db_show.py bm25                            # 各 bm25_*.pkl 规模
```


## 2.3. 输出说明：各 SQLite 库的含义

`summary` / `sqlite` 会逐个列出「配置里各 `*_DB_PATH` + `db/sqlite/*.db`」每个文件下的表名与行数（只统计行数、不展开字段值，所以 `auth.db` 等敏感库不打印密钥 / 密码）。各库内容对照如下：

| 配置项 | 文件 | 主要表 | 装什么 |
|---|---|---|---|
| `MEMORY_DB_PATH` | `chat_history.db` | `sessions` / `messages` | 对话会话与逐条消息历史 |
| `AUTH_DB_PATH` | `auth.db` | `users` / `auth_sessions` / `user_settings` / `user_rules` | 用户账号、登录会话、个人设置、自定义规则 |
| `USAGE_DB_PATH` | `usage.db` | `usage_events` / `model_pricing` / `saving_events` / `cache_lookups` / `agent_traces` / `trace_spans` / `security_events` | 用量计费、模型价格、省钱事件、缓存命中、Agent 调用链路 trace、安全事件 |
| `RAG_GOLDEN_DB_PATH` | `rag_golden.db` | `rag_golden` | RAG 评估的 golden 问答集 |
| `USER_MEMORY_DB_PATH` | `user_memory.db` | `user_memories` | 跨会话的长期用户记忆 |
| `LEARNING_PLAN_DB_PATH` | `learning.db` | `learning_plans` / `learning_tasks` | 学习计划及其下属任务 |
| `QUIZ_DB_PATH` | `quiz.db` | `quiz_sets` / `quiz_questions` | 测验集与题目 |
| `SRS_DB_PATH` | `srs.db` | `srs_cards` | 间隔重复记忆（SRS, Spaced Repetition System）卡片 |

# 3. Web UI

## 需求
新增一页“DB 秀”，用于展示 各种后台数据的信息和内容
页面内，在左侧边栏分三页，分别是 Chroma, BM25, SQLite
每页内可通过点击逐层深入，查看DB结构和内容
如：
chroma 页面，初始页看到collection 列表，显示每个 collection基本信息
点击某个collecion, 展示该collection 内的信息列表
再点击某一条，显示该条存在的具体信息

其它2跟个 DB 思路也是这样