#!/usr/bin/env bash

# 清空当前库
# python tools/ingestion.py clear

# 英文模型入库
# python tools/ingestion.py ingest -d ../pursue/ -m en
# python tools/ingestion.py ingest -d ./docs -m en

# 中文模型入库
# python tools/ingestion.py ingest -d ../pursue/resume -m zh
# python tools/ingestion.py ingest -d ./docs_zh -m zh

# m3混合模型入库
# python tools/ingestion.py ingest -d ../pursue/ -m m3
# python tools/ingestion.py ingest -d ./docs -m m3
# python tools/ingestion.py ingest -d ./docs_zh -m m3


# 评估中英文双语模型
# EMBEDDING_MODEL=en
# RAG_ACTIVE_EMBEDDINGS=en,zh
python -m tools.rag_eval.eval -o tools/rag_eval/reports/en_zh.md
python -m tools.rag_eval.eval --no-rewriter -o tools/rag_eval/reports/en_zh-no-rewriter.md
python -m tools.rag_eval.eval --no-rerank -o tools/rag_eval/reports/en_zh-no-rerank.md

# 评估m3混合模型
# EMBEDDING_MODEL=m3
# RAG_ACTIVE_EMBEDDINGS=m3
# python -m tools.rag_eval.eval -o tools/rag_eval/reports/m3.md
# python -m tools.rag_eval.eval --no-rewriter -o tools/rag_eval/reports/m3-no-rewriter.md
# python -m tools.rag_eval.eval --no-rerank -o tools/rag_eval/reports/m3-no-rerank.md



