"""API 运行时配置：通用 config 覆盖、副作用 hook、元数据注册表、API key 覆盖。

与 `routes/` 中的 HTTP 路由分离；由 `main` 在挂载路由前 `apply_overrides()`。"""
