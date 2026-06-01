# 私有知识库测试文档

## 项目简介

本项目是一个基于 RAG（检索增强生成）技术的私有知识库 Agent。

## 核心功能

- 支持多格式文档解析（MD、PDF、Word、Excel 等）
- 本地向量化存储，数据不出本地
- 自然语言提问，自动检索相关片段
- 可切换 LLM 提供商

## 技术栈

- Python 3.11
- ChromaDB 向量数据库
- sentence-transformers 嵌入模型
- OpenAI / Kimi / DeepSeek LLM
