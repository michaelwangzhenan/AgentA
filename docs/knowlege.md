# 1. Bi-Encoder vs Cross-Encoder

## 编码方式

**Bi-Encoder（分开编码）：**
```
Query → Encoder → q_vec ─┐
                           ├→ cosine → score
Chunk → Encoder → d_vec ─┘
```
Query 和 Chunk 各自独立编码成向量，打 cosine 相似度。
Chunk 向量可以**提前全部算好存库**，检索时只算一次 Query 向量，然后做向量近邻搜索——极快。

**Cross-Encoder（拼一起编码）：**
```
[CLS] Query [SEP] Chunk [SEP] → Transformer → score
       ↑               ↑
   query 的每个词都能 attend 到 chunk 的每个词
```
Query 和 Chunk 拼在一起送入 Transformer，两边 token 充分交互，模型能捕捉到
"这个词在 Query 里是什么语境、对应 Chunk 里哪句话回应了它"——精度高，但慢。

## 对比

| | Bi-Encoder | Cross-Encoder |
|--|--|--|
| 速度 | 快（Chunk 向量提前算好） | 慢（每对都要跑一次前向） |
| 精度 | 较低（两边无词级交互） | 高（两边 token 互相 attend） |
| 用途 | **召回**（从海量文档快速捞候选） | **精排**（对少量候选重新打分） |

## 在 RAG 里的分工

```
用户 Query
  ↓
Bi-Encoder 召回  →  top-K × 3 候选（快但粗）
  ↓
Cross-Encoder 精排  →  最终 top-K（慢但准）
  ↓
送给 LLM
```

对全量文档跑 Cross-Encoder 太慢，对少量候选跑则可接受。
**Bi-Encoder 负责"海里捞鱼"，Cross-Encoder 负责"鱼里挑好的"。**


# 2. BGE

## 是什么

**BGE** = **B**AAI **G**eneral **E**mbedding，智源研究院（Beijing Academy of Artificial Intelligence, BAAI）开源的嵌入模型家族。
覆盖两类：

- **Dense embedding**：把文本编码成向量，用于召回（Bi-Encoder 模式）
- **Cross-Encoder reranker**：对 (query, doc) 拼接打分，用于精排

是 MTEB（Massive Text Embedding Benchmark）中英文榜单长期前列的开源 SOTA 之一。

## 模型谱系

| 模型 | 用途 | 维度 / 大小 | 语种 |
|---|---|---|---|
| `bge-small-zh` | Dense embedding | 512 / ~96MB | 中文优化 |
| `bge-small-en` | Dense embedding | 384 / ~33MB | 英文 |
| `bge-base/large-zh-v1.5` | Dense embedding | 768 / 1024 | 中文（更大更准）|
| `bge-m3` | Dense embedding | 1024 / ~568MB | 多语言（推荐生产）|
| `bge-reranker-base` | Cross-Encoder 精排 | ~1.1GB | 中英双语 |
| `bge-reranker-v2-m3` | Cross-Encoder 精排 | 更大 | 多语种最佳 |

## 本项目用到哪些

| .env 配置 | 别名 | 实际模型 |
|---|---|---|
| `EMBEDDING_MODEL=zh` | zh | `BAAI/bge-small-zh` |
| `EMBEDDING_MODEL=m3` | m3 | `BAAI/bge-m3`（默认）|
| `RERANKER_MODEL` | — | `BAAI/bge-reranker-base`（默认）/ `BAAI/bge-reranker-v2-m3`（可选）|

> 英文别名 `en` 走的是 MiniLM（all-MiniLM-L6-v2，微软开源轻量英文 / 多语，384 维 / ~90MB），不是 BGE。

## 易踩坑：非对称 query prefix

BGE 早期版本（v1.0）训练时 query 和 doc 不对称，**query 必须加专属 prefix** 否则相似度对不齐：

```
中文：query 前面加 "为这个句子生成表示以用于检索相关文章："
英文：query 前面加 "Represent this sentence for searching relevant passages: "
doc 端原样不动
```

**v1.5 和 m3 已修复对称性，不再需要 prefix**。本项目在 `src/rag/retriever.py:42-57` 按模型名自动判断是否注入 prefix，调用方无感知。

## 为什么选 BGE

- **本地可跑** · 免费 · 无需 API Key（不像 OpenAI text-embedding-3）
- **中文场景质量 ≥ 商用闭源**（MTEB-zh 长期前列）
- **同一团队覆盖 embedding + reranker**，训练数据 / 分布一致，搭配使用比混搭第三方模型更稳
- **bge-m3 单模型覆盖中英**，省一份库


# 3. CRUD
CRUD = **Create / Read / Update / Delete**，数据存储最基础的 4 个操作，对应 SQL 里就是 `INSERT / SELECT / UPDATE / DELETE`。

说一个东西"只做 CRUD"，意思是它**只管把数据存进去、取出来、改、删**，不参与任何业务判断。

- `ChatHistoryStore` 只做 CRUD → 给我一条 message 我存下来、给我 session_id 我返回最近 N 条，**不关心** "这是不是 skill 触发的轮、要不要保护成对、要不要截断"。
- `HistoryManager` 才管这些"要不要、怎么截"的业务策略，背后调 `ChatHistoryStore` 的 CRUD 拿数据。



# A.缩写
| 缩写 | 全称 | 含义 |
|---|---|---|
| **KB** | Knowledge Base | 知识库（向量库 + 关键词索引）|
| **RAG** | Retrieval-Augmented Generation | 检索增强生成 |
| **BM25** | Best Matching 25 | 经典关键词检索算法 |
| **RRF** | Reciprocal Rank Fusion | 倒数排名融合（合并多路召回排序）|
| **HyDE** | Hypothetical Document Embeddings | 假设性文档嵌入（让 LLM 先编一段答案再检索）|
| **MRR** | Mean Reciprocal Rank | 平均倒数排名（评估指标）|
| **nDCG** | normalized Discounted Cumulative Gain | 归一化折损累积增益（更细的评估指标）|

