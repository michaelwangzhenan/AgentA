# AgentA 存储与检索知识笔记

本文整理自对 AgentA 中 **ChromaDB**、**SQLite**、**BM25（`.pkl`）** 等机制的问答与代码对照，便于日后查阅。

---

## 1. 工程内默认目录布局（`db/`）

运行期数据默认收拢到 **`db/`** 下（均可经 `.env` 覆盖），与源码分离、便于备份：

| 路径（默认） | 内容 |
|--------------|------|
| **`db/chroma/`** | Chroma 持久化：`chroma.sqlite3`、UUID 段目录、`ingest_history.json`（与向量同生命周期） |
| **`db/bm25/`** | `bm25_<collection>.pkl`（BM25 索引；配置项 `BM25_INDEX_DIR`；若留空则回落到与 `CHROMA_DB_PATH` 同目录） |
| **`db/sqlite/`** | 各业务 SQLite 文件（`MEMORY_DB_PATH`、`AUTH_DB_PATH` 等，见 `src/config.py`） |

**从旧版 `./chroma_db`、`./sqlite_db`、根下 BM25 pkl 迁移**：停进程后，把原 `chroma.sqlite3` 与 UUID 目录与 `ingest_history.json` 移入 `db/chroma/`；把 `bm25_*.pkl` 移入 `db/bm25/`；把各 `.db` 移入 `db/sqlite/`；再核对 `.env` 与 `src/config.py` 默认一致或显式写明路径。未移动文件前不要只改代码，否则会找不到库。

---

## 2. 两类落盘：向量库 vs SQLite（语义对照）

| 目录/形态 | 是什么 | 典型用途 |
|-----------|--------|----------|
| **`CHROMA_DB_PATH`（默认 `db/chroma`）** | Chroma 持久化根目录 | RAG：**文本分块 + 向量 + 元数据**，按语义相似度检索 |
| **各 `*_DB_PATH`（默认在 `db/sqlite/`）** | 独立 `.db` 文件 | **表格式业务数据**：会话、用户、用量、学习计划、quiz 等 |

**对比一句话**：Chroma 根目录面向**向量语义检索**；`db/sqlite` 下各文件面向 **SQL 表 + 行数据**；BM25 默认可单独占 `db/bm25`。备份迁移时按上表三类一起考虑。

---

## 3. 为何不像 MySQL 那样「先装一个数据库服务」？

- **SQLite**：引擎随 Python 发行版提供，用标准库 `sqlite3` 读写**文件**即可，无独立服务进程。
- **Chroma（本地持久化）**：同样是应用进程内通过 Python 包访问，数据落在工程目录；需要的是 **`pip` 安装 `chromadb`**，不是另装一个像 MySQL 的系统服务。

若将来改为远程 PostgreSQL、远程向量库，才会出现「单独部署的数据库服务」。

---

## 4. 常见数据库形态（与 SQLite / Chroma 的定位）

- **SQLite**：**关系型（RDBMS）**、**嵌入式**、**SQL** 访问，单文件常见。
- **Chroma**：**向量库**一类产品，按向量近邻 + 元数据过滤检索；语义检索需配合 **Embedding**。
- 其他大类（仅作地图）：键值（Redis）、文档（MongoDB）、图（Neo4j）、时序（InfluxDB）等。

---

## 5. Chroma 典型流水线

### 5.1 存

1. 大文本**切分**为多个 chunk。  
2. **Embedding 模型**为每个 chunk 生成**向量**。  
3. 将 **向量 + 原文 + 元数据**（如 `doc_id`、`source`、行号等）写入某个 **collection**。

### 5.2 查

1. **Embedding** 把用户问题编成**查询向量**。  
2. 调用 Chroma 的 `collection.query(...)`，在向量空间里做**近邻搜索**。  
3. 返回最相近的若干条（条数由调用方参数决定，见下文）。

### 5.3 相似度谁算？

- **Embedding**：只负责「把文字变成向量」。  
- **Chroma（及底层索引，如 HNSW）**：在向量空间算**距离/相似度**并排序；**不是**暴力「遍历全库每个向量」的 O(n) 线性扫，而是用 **ANN（近似最近邻）** 在索引上搜索。

### 5.4 Embedding 与 Chroma 的关系

做语义检索时：**二者配合**。Chroma 存/搜向量；向量由 Embedding 生成。Chroma 本身不替代「理解句子含义」的语言模型式能力。

---

## 6. HNSW、ANN、与「谁调谁」

- **ANN（Approximate Nearest Neighbor，近似最近邻）**：一类**目标**——大库上尽快找到近似最近邻，不必精确扫全表。  
- **HNSW（Hierarchical Navigable Small World）**：实现 ANN 的一种**具体算法**，来自论文；可被多个产品采用。  
- **Chroma**：内部选用这类索引做向量检索；**AgentA 不直接调 HNSW**，只调 Chroma 的 Python API（如 `PersistentClient`、`get_collection`、`collection.query`）。  
- **`_query_collection`**（`src/rag/retriever.py`）：AgentA **封装层**，负责准备 `query_embeddings`、`n_results` 等，再调用 `col.query`，不是 Chroma 自带的函数名。

**ANN 与数据规模**：库变大后，单次查询耗时**仍可能变长**，但通常比线性全表比对**缓得多**；具体与索引参数、数据量有关。

---

## 7. AgentA 里「Chroma 取多少条」和「最终返回多少条」

- **最终返回**：`search()` 流水线末尾截断到 **`top_k`**，默认来自配置 **`RAG_TOP_K`**（如 `src/config.py` 中默认 8）。  
- **过滤 / 精排 / 按 source 去重**：AgentA 在 Chroma（及 BM25）**召回候选之后**的逻辑，决定的是**最终列表**，与 Chroma 单次 `query` 的 `n_results` 不是同一个数。  
- **Chroma 单次 `n_results`**：在 `_query_collection` 里为 `min(传入的 top_k, collection.count)`；该「传入的 top_k」在 `search()` 里是 **`per_query_k`**，用于给 rerank、融合留候选。  
  - **`recall_k`**：开启精排时约为 `top_k × RERANKER_RECALL_MULTIPLIER`（默认倍数见配置）。  
  - **`per_query_k`**：`max(top_k, recall_k // n_q)`，`n_q` 为多 query 条数。

因此：**Chroma 返回条数由 AgentA 算好的 `per_query_k`（再与条数上限取 min）决定**；最终给用户的不超过 `RAG_TOP_K`。

---

## 8. SQLite 与 SQL

SQLite 的存取**主要就是用 SQL**（`SELECT` / `INSERT` / `UPDATE` 等），经 `sqlite3` 执行；与 Chroma 的 `query` API 是两套体系。

---

## 9. Chroma 的 collection 是什么？

**collection** = Chroma 里一个**命名的分区**：同一集合内向量**维度必须一致**（换 Embedding 模型通常要换 collection 或重建）。AgentA 按模型/别名分 **`kb_*`** 等 collection；语义缓存方案（见迭代文档）还可再挂**独立 collection** 名（如配置 `SEMANTIC_CACHE_COLLECTION`）。

---

## 10. Chroma 持久化根目录里常见内容（默认即 `db/chroma/`）

| 名称 | 含义 |
|------|------|
| **`chroma.sqlite3`** | Chroma 元数据与内部登记等 |
| **若干 UUID 子目录** | Chroma 为索引落盘生成的**内部数据目录**，勿手改 |
| **`ingest_history.json`** | 入库历史 sidecar（`tools/rag_cli.py`），与向量库同生命周期 |

BM25 的 **`bm25_*.pkl`** 默认在 **`db/bm25/`**（`BM25_INDEX_DIR`），不在 Chroma 根目录内。

---

## 11. `.pkl` 是什么？和 Chroma、SQLite 的差别
- **`.pkl`**：多为 Python **`pickle`** 序列化的**二进制快照**，把内存对象整块落盘。  
- **与 SQLite**：SQLite 是带 SQL 引擎的关系库；`.pkl` 无通用 SQL，主要靠**写它的代码** `load` 后使用。  
- **与 Chroma**：Chroma 提供向量 collection API；`.pkl` 这里是 **BM25 索引** 的存档，与向量库**并行**用于关键词一路召回。

---

## 12. BM25 与「倒排 / 统计结构」

### 12.1 BM25 是什么？

**BM25**：经典**关键词相关性**打分，依据词频、词在库中的稀有度、文档长度归一化等，**不做语义向量**。适合专有名词、型号、错误码等字面匹配；与向量检索互补。

### 12.2 倒排与统计结构

- **倒排索引**（inverted index）：从**词**映射到**出现该词的文档/chunk 列表**（及出现次数），查询时先定位候选，避免全文线性扫描。  
- **统计结构**：BM25 公式所需的**语料级计数**（如某词出现在多少篇/段、段长等），在建索引时算好并保存。

### 12.3 谁算？要模型吗？

**不要 Embedding / LLM**。建索引时由 **AgentA 入库代码**（如 `src/rag/ingest.py` 与 `src/rag/bm25_index.py` 链路）做分词、累计、打分结构构建，再**序列化**为 `.pkl`。查询时用同一套公式对已存统计量打分。

---

## 13. 文档侧补充：iter_14 与语义缓存 collection

在 `docs/v_1_0/interation/iter_14_enh.md` 的语义缓存设计中，明确采用与 **`kb_*` 知识库并列**的 **独立 Chroma collection** 存缓存条目，配置项 **`SEMANTIC_CACHE_COLLECTION`** 指定名称；是否已在代码中完全落地，以仓库当前实现为准。

---

## 14. 相关代码与配置入口（便于跳转）

- Chroma 路径：`src/config.py` → `CHROMA_DB_PATH`  
- SQLite 各库路径：`MEMORY_DB_PATH`、`AUTH_DB_PATH`、`USAGE_DB_PATH` 等  
- 检索流水线：`src/rag/retriever.py`（`search`、`_query_collection`、BM25 融合与截断）  
- BM25 实现：`src/rag/bm25_index.py`；索引目录：`BM25_INDEX_DIR`（默认 `./db/bm25`）  

---

*笔记日期：2026-06-12；目录布局 2026-06-12 更新为 `db/`*
