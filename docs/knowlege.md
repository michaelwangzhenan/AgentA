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
