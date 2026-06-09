# 1. API Key UI可配

admin 可在「设置 → API 密钥」里配置各 LLM 厂商和 SerpAPI 的 key，保存后下一次调用即生效，无需重启。普通用户看不到这个入口。

key 存在独立文件 `.agenta/api_keys.json`（gitignore），没复用 `config_overrides.json`：后者会经 `GET /api/config` 暴露给所有登录用户，且 key 藏在 frozen 的 `PROVIDER_CONFIGS` 里、形态也对不上。新开了一条 admin-only 通路 `GET/PUT/DELETE /api/api-keys`，响应只回脱敏尾段（如 `sk-…3f9a`），绝不返回完整明文。

# 2. 用户记忆

用户记忆实现（`UserMemoryStore` + `MemoryManager`）。

|No|优先级|问题描述|影响|优化方案|优化后的可能负面影响|
|---|---|---|---|---|---|
|1|P1|`_auto_extract_turn_counter` 是 `MemoryManager` 的实例字段，但 manager 在每轮 `run()` 里重新 new，计数器每轮都从 0 开始|默认 `EXTRACT_EVERY_N=5` 时，永远 `1 < 5`，自动提取一次都不触发；只有设成 1 才会每轮提（等于没节流）。`AUTO_EXTRACT` 默认关闭所以一直没暴露|计数器做成 session 级跨轮存活（manager 每轮读出来）；或放弃按轮计数，改成从 DB/消息数无状态判定|跨轮存活需多存一份 session 状态，要处理并发；无状态判定每轮多查一次 DB|
|2|P1|`load_for_context` 按 `accessed_at` 倒序取，取完又把这批全刷成 now|能塞进 `MAX_CHARS` 的那批永远刷新、永远靠前（强者恒强）；被截在门外的条目 `accessed_at` 再不更新、永久沉底（饥饿），新写入/更相关的进不来|被动注入不刷 `accessed_at`；排序改用 `created_at` 或单独的命中计数|失去"近期使用优先"的弱信号；若改用命中计数要新增字段|
|3|P2|检索是全量按近期取并截到 `MAX_CHARS`，不看当前 query，不做相关性召回|记忆条数一多会注入大量与本轮无关内容，占 context 且可能干扰；`load_for_context` 名字也误导（像做了相关性筛选）|引入 embedding 相关性召回；或至少在 design.md 注明是全量注入|embedding 召回要存向量 + 每轮多算一次，成本/复杂度上升，对单用户偏重|
|4|P2|去重只靠 `(category, key)` 精确匹配，key 由 LLM 自由生成|"语言偏好/偏好语言/用户语言"被当成不同条目，同义/矛盾条目堆积无人清理；`task`/`correction` 短命内容永久留存|提取时把已有 key 列表传给 extractor 提示复用；给 `task` 类加过期或条数上限|传 key 列表增大提取 prompt；过期策略可能误删用户还想留的内容|
|5|P2|`_sanitize` 对 `manual`/`explicit`（用户本人写的）记忆也按"命中即从该位置截断后半段"清洗|用户合法句子里含 `你现在是`/`act as`/`system:` 等宽 pattern 会被静默砍半；真正有注入风险的是 `auto` 来源|清洗按 source 区分：`auto` 严格清洗，`manual`/`explicit` 放宽；命中改为整条 reject + 记日志，不存半截|区分逻辑变复杂；放宽后若用户粘贴了不可信内容仍有极小注入面|
|6|P2|`try_extract` 在 `FINAL_ANSWER` 事件 publish 之前同步跑一次完整 LLM 往返|开启 auto/显式提取时，本轮收尾事件 + usage 被提取拖住，用户感到卡顿（正文 token 已流式吐出，但完成事件延迟）|提取丢后台线程 fire-and-forget，或在 `FINAL_ANSWER` 之后再跑|后台线程要处理与主流程的 DB 并发；fire-and-forget 失败更难被用户感知|

## 2.1 优化方案（已定）

|No|结论|方案|
|---|---|---|
|1|做|**无状态节流**。给 `ChatHistoryStore` 加 `count_user_messages(session_id)`；`MemoryManager.try_extract` 自动模式下取本 session 的 user 消息数 `cnt`，`cnt % EXTRACT_EVERY_N == 0` 且满足 `min_len` 才提取。删掉实例字段 `_auto_extract_turn_counter`。显式触发逻辑不变。|
|2|做|**去掉注入刷新 + 按 source 优先级排序**。`load_for_context` 删掉末尾批量 `UPDATE accessed_at`；`ORDER BY` 改为 `manual`/`explicit` 优先于 `auto`（SQLite `CASE`），同级再按 `created_at DESC`。`accessed_at` 字段保留不动（避免 schema 变更），只是不再参与排序。|
|3|不做|不引入 embedding 相关性召回。仅在 `design.md` 注明 `<user_context>` 是全量按序注入、非相关性检索。|
|4|做（不做过期）|**复用 key 去重去矛盾**。`extract_memories` 增参 `existing`（该用户已有 category/key/value 列表），prompt 加一段"同主题请复用已有 key"；`MemoryManager.try_extract` 传入 `load_all(user_id=...)`。靠 `upsert` 对同 `(category,key)` 覆盖天然去重 + 用新值覆盖旧值。不加过期、不主动删除矛盾旧条目（零误删）。|
|5|不做|`_sanitize` 维持对所有 source 统一清洗，不按 source 区分。|
|6|做|**后台线程提取**。`try_extract` 用 `threading.Thread` fire-and-forget。注意 `current_user_id()` 是 contextvar，子线程取不到 → 必须在主线程取出 `user_id` 传进去（`upsert` / `load_all` 显式带 `user_id`），否则会写到默认用户。|

> 实施注意（#4 + #6 共用）：后台线程里所有 DB 调用都要显式带 `user_id`，不能依赖 `current_user_id()`。

# 3. 规则优化
AgentA "规则" 属于 design.md 的 3.5 Prompt 管理 一部分。

./.agenta/rules/rules.md 本意的是对齐 GHC 的 ./github/copilot-instructionss.md 和 Cursor 的 ./cursor/ruls/agenta-conventions.mdc 的功能。

1. Review 现有实现，给出评估结果（格式参考 “# 2. 用户记忆”）
2. 名字叫"规则”用户很难理解，也对齐不到 GHC / Cursor， 要重命名


# override 文件
