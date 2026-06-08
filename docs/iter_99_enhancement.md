# 1. 优化
## 1.1. 性能优化：！！search_knwoledge 特别慢

根因：慢在 CPU 推理（无 GPU 放大），不是向量检索本身。耗时大头排序：
1. **Cross-Encoder 精排**：`bge-reranker-base` 对 `recall_mult(3)×top_k(8)≈24` 个候选逐对打分 —— 最大头。
2. **Query 改写**：`RAG_QUERY_REWRITE_ENABLED` 默认开，每次多 1 次 LLM 调用 + 改写条数×多路检索。
3. **Query embedding**：SentenceTransformer 在 CPU 前向，按改写条数翻倍。
4. dense / BM25 检索本身很快，非瓶颈。

优化方向（具体留单独 task）：
- 感知延迟：SSE 加阶段状态（检索中 / 精排中 / 生成中）。
- 精排：`RERANKER_RECALL_MULTIPLIER` 3→2 / 换更小或 ONNX 量化 reranker / 条件性精排（候选少时跳过）。
- 改写：按需触发，别每次都改。
- embedding：小模型 / ONNX / 查询级缓存；叠加语义缓存。
- 釜底抽薪：embedding+rerank 移到托管 API 或 GPU 机（provider 抽象已具备条件）。
- 方法论：先埋点量各阶段耗时，确认大头再调。

## 1.2. API 可配置
## 1.3. Token 统计（每轮 / 累计）
## 1.4. 用户记忆：不限于固定格式
## 1.5. 文档更新：design, readme

# 2. 新功能
## 2.1. [新 Feature](iter_7_retro.md#24-选定feature)

## 2.2. 皮肤/主题切换
## 2.3. 新业务
## 2.4. workflow



# 3. TBD
多用户并行
多语言
skill os