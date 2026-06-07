"""Skills 子系统：SKILL.md 的发现 / 解析 / CRUD / catalog 渲染。

独立成层，供 Agent（注入 catalog）、API（CRUD）、CLI（启动扫描）共同向下依赖，
避免 Agent core 反向依赖 `src/cli/`。
"""
