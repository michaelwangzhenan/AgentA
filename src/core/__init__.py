"""跨层共享的基础原语（不依赖业务层）。

放在依赖图最底层，供 `src/memory/`、`src/agent/` 等共同向下依赖，
避免存储层反向感知 Agent helper 层。
"""
