# AgentA 编码规范

> 私有知识库 ReAct Agent，核心栈：Python 3.10+ / ChromaDB / sentence-transformers / OpenAI-compatible LLM / SQLite 记忆 / prompt_toolkit CLI

## 通用
- **用中文**回复和注释
- Python 3.10+：match/case、`X | Y` 联合类型、f-string
- 全量类型注解；`-> None` 显式标注；可空用 `X | None`
- 异常捕获具体类型，禁止裸 `except`

## 命名
- 类 `PascalCase`，函数/变量 `snake_case`，私有成员 `_前缀`，常量 `UPPER_CASE`

## 设计
- 数据容器用 `@dataclass(frozen=True)` 或 `NamedTuple`
- 接口用 `Protocol`，优先组合而非继承
- 缓存用 `functools.cache` / `lru_cache`
- 资源管理用 `with` 语句
- 推荐 `collections.abc`（`Sequence`、`Mapping`、`Iterable`）做参数类型


## 如何制定实施计划
- 计划必须极度简洁，为了简洁，可以牺牲语法
- 每个计划结束时，列出尚未解决的问题列表（如果有的话）


## 项目约定
- 项目根目录：`README.md`、`.env.example`、`src/`、`datasets/`（按语种分 `data_en/` 与 `data_zh/`）
- 业务代码在 `src/`，入口 `main.py`, 功能实现要模块化

