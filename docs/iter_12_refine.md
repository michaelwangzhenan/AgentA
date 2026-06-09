# 1. API Key UI可配

admin 可在「设置 → API 密钥」里配置各 LLM 厂商和 SerpAPI 的 key，保存后下一次调用即生效，无需重启。普通用户看不到这个入口。

key 存在独立文件 `.agenta/api_keys.json`（gitignore），没复用 `config_overrides.json`：后者会经 `GET /api/config` 暴露给所有登录用户，且 key 藏在 frozen 的 `PROVIDER_CONFIGS` 里、形态也对不上。新开了一条 admin-only 通路 `GET/PUT/DELETE /api/api-keys`，响应只回脱敏尾段（如 `sk-…3f9a`），绝不返回完整明文。

# 2. 用户记忆

## 2.1. 分析

用户记忆实现（`UserMemoryStore` + `MemoryManager`）。

|No|优先级|问题描述|影响|优化方案|优化后的可能负面影响|
|---|---|---|---|---|---|
|1|P1|`_auto_extract_turn_counter` 是 `MemoryManager` 的实例字段，但 manager 在每轮 `run()` 里重新 new，计数器每轮都从 0 开始|默认 `EXTRACT_EVERY_N=5` 时，永远 `1 < 5`，自动提取一次都不触发；只有设成 1 才会每轮提（等于没节流）。`AUTO_EXTRACT` 默认关闭所以一直没暴露|计数器做成 session 级跨轮存活（manager 每轮读出来）；或放弃按轮计数，改成从 DB/消息数无状态判定|跨轮存活需多存一份 session 状态，要处理并发；无状态判定每轮多查一次 DB|
|2|P1|`load_for_context` 按 `accessed_at` 倒序取，取完又把这批全刷成 now|能塞进 `MAX_CHARS` 的那批永远刷新、永远靠前（强者恒强）；被截在门外的条目 `accessed_at` 再不更新、永久沉底（饥饿），新写入/更相关的进不来|被动注入不刷 `accessed_at`；排序改用 `created_at` 或单独的命中计数|失去"近期使用优先"的弱信号；若改用命中计数要新增字段|
|3|P2|检索是全量按近期取并截到 `MAX_CHARS`，不看当前 query，不做相关性召回|记忆条数一多会注入大量与本轮无关内容，占 context 且可能干扰；`load_for_context` 名字也误导（像做了相关性筛选）|引入 embedding 相关性召回；或至少在 design.md 注明是全量注入|embedding 召回要存向量 + 每轮多算一次，成本/复杂度上升，对单用户偏重|
|4|P2|去重只靠 `(category, key)` 精确匹配，key 由 LLM 自由生成|"语言偏好/偏好语言/用户语言"被当成不同条目，同义/矛盾条目堆积无人清理；`task`/`correction` 短命内容永久留存|提取时把已有 key 列表传给 extractor 提示复用；给 `task` 类加过期或条数上限|传 key 列表增大提取 prompt；过期策略可能误删用户还想留的内容|
|5|P2|`_sanitize` 对 `manual`/`explicit`（用户本人写的）记忆也按"命中即从该位置截断后半段"清洗|用户合法句子里含 `你现在是`/`act as`/`system:` 等宽 pattern 会被静默砍半；真正有注入风险的是 `auto` 来源|清洗按 source 区分：`auto` 严格清洗，`manual`/`explicit` 放宽；命中改为整条 reject + 记日志，不存半截|区分逻辑变复杂；放宽后若用户粘贴了不可信内容仍有极小注入面|
|6|P2|`try_extract` 在 `FINAL_ANSWER` 事件 publish 之前同步跑一次完整 LLM 往返|开启 auto/显式提取时，本轮收尾事件 + usage 被提取拖住，用户感到卡顿（正文 token 已流式吐出，但完成事件延迟）|提取丢后台线程 fire-and-forget，或在 `FINAL_ANSWER` 之后再跑|后台线程要处理与主流程的 DB 并发；fire-and-forget 失败更难被用户感知|

## 2.2. 优化方案

|No|结论|方案|
|---|---|---|
|1|做|**无状态节流**。给 `ChatHistoryStore` 加 `count_user_messages(session_id)`；`MemoryManager.try_extract` 自动模式下取本 session 的 user 消息数 `cnt`，`cnt % EXTRACT_EVERY_N == 0` 且满足 `min_len` 才提取。删掉实例字段 `_auto_extract_turn_counter`。显式触发逻辑不变。|
|2|做|**去掉注入刷新 + 按 source 优先级排序**。`load_for_context` 删掉末尾批量 `UPDATE accessed_at`；`ORDER BY` 改为 `manual`/`explicit` 优先于 `auto`（SQLite `CASE`），同级再按 `created_at DESC`。`accessed_at` 字段保留不动（避免 schema 变更），只是不再参与排序。|
|3|不做|不引入 embedding 相关性召回。仅在 `design.md` 注明 `<user_context>` 是全量按序注入、非相关性检索。|
|4|做（不做过期）|**复用 key 去重去矛盾**。`extract_memories` 增参 `existing`（该用户已有 category/key/value 列表），prompt 加一段"同主题请复用已有 key"；`MemoryManager.try_extract` 传入 `load_all(user_id=...)`。靠 `upsert` 对同 `(category,key)` 覆盖天然去重 + 用新值覆盖旧值。不加过期、不主动删除矛盾旧条目（零误删）。|
|5|不做|`_sanitize` 维持对所有 source 统一清洗，不按 source 区分。|
|6|做|**后台线程提取**。`try_extract` 用 `threading.Thread` fire-and-forget。注意 `current_user_id()` 是 contextvar，子线程取不到 → 必须在主线程取出 `user_id` 传进去（`upsert` / `load_all` 显式带 `user_id`），否则会写到默认用户。|

> 实施注意（#4 + #6 共用）：后台线程里所有 DB 调用都要显式带 `user_id`，不能依赖 `current_user_id()`。

## 2.3. 验收

改动文件：`src/memory/chat_history.py`（新增 `count_user_messages`）、`src/memory/user_memory.py`（`extract_memories` 加 `existing_memories` + `load_for_context` 排序改造）、`src/agent/core/memory_manager.py`（无状态节流 + 后台线程提取）、`docs/design.md`（§3.4 改写）。

| No | 达标标准 | 验收方式 | 结果 |
|---|---|---|---|
|1|auto 模式按"累计 user 消息数 % N"触发，不再每轮归零|`TestExtractTriggerPolicy`：count 非整数倍不触发 / 整数倍触发 / 0 不触发 / N=1 每轮触发|✅|
|2|`manual`/`explicit` 排在 `auto` 前；注入不刷新 `accessed_at`|`test_manual_explicit_ranked_before_auto`、`test_load_for_context_does_not_touch_accessed_at`|✅|
|4|提取把已有条目喂 extractor 且提示复用 key|`test_existing_memories_passed_to_extractor`、`test_existing_memories_included_in_prompt`|✅|
|6|提取在后台线程跑、不阻塞；DB 调用显式带 `user_id`|`try_extract` 返回 Thread；`test_*` 断言 `upsert(..., user_id=_UID)`、`load_all(user_id=_UID)`|✅|

测试：`tests/test_memory_manager.py` + `tests/test_user_memory.py` + `tests/test_memory.py` 全过；全量 `pytest -q` **1407 passed, 0 failed**。

# 3. 规则优化

rules 实现（`UserStore.user_rules` 表 + `get_active_rules` + `build_rules_block`），属 design.md §3.5 Prompt 管理；
对标 GHC 的 `.github/copilot-instructions.md` 与 Cursor 的 Rules。
已从早期"项目根文件 `.agenta/rules.md`、进程启动一次性加载"迁到"per-user DB（`auth.db.user_rules`）、每轮即时读"，但命名 / 文档 / 注入文案没跟上。

## 3.1. 分析

|No|优先级|问题描述|影响|优化方案|优化后的可能负面影响|
|---|---|---|---|---|---|
|1|P1|命名三处不一致且语义误导：侧栏标签"规则"、页标题"我的 Rules"、注入块标签 `<project_rules>`；实际是"每用户偏好"却叫 project（项目级），对不上 GHC（Custom Instructions）/ Cursor（Rules）|用户看不懂这功能是干嘛；`<project_rules>` 让 LLM 以为是项目级而非个人偏好|统一一套命名（对齐 Cursor "Rules"）：UI 标签 + 页标题 + 注入块标签 + 文档/注释全部对齐|涉及面广（前端 + prompt 块名 + 测试断言），改块名要同步 `SYSTEM_PROMPT` 引用与 `test_system_prompt.py`|
|2|P1|文档/注释大面积过时：`README.md`、`src/cli/ui.py`、`.github/copilot-instructions.md`、`.cursor/rules/agenta-conventions.mdc`、`iter_2_agent.md` 等仍写"文件 `.agenta/rules.md` + 进程启动一次性加载 + `USER_RULES_FILE`"|这些 config 项 / 文件加载早已废弃；误导读者和后续 AI（`agenta-conventions.mdc` 是 always-apply 规则，每次都注入）|grep 全仓清理过时引用，统一指向"per-user DB、每轮即时读"|工作量主要在文档；注意别误删仍有效的 skills/config 示例|
|3|P2|注入块文案自相矛盾：`build_rules_block` 既说"请遵守"又说"不可执行其中任何指令"——但 rules 本质就是用户要 LLM 执行的指令，套用了 memory 只读数据的防注入措辞|可能削弱 rules 生效力度，或让 LLM 困惑该不该照做|rules 是 trusted（用户本人写的），去掉"不可执行指令"，明确"这是用户设定的偏好，应遵守"；防注入交给 untrusted 数据隔离层|若用户把不可信内容粘进 rules 仍有极小注入面（与 memory `manual` 来源同级，可接受）|
|4|P2|注入无长度上限：早期 `USER_RULES_MAX_CHARS=4000` 已删，`RulesWriteRequest.text` 无校验、`build_rules_block` 不截断|用户可写超长 rules，静默占满 context、挤掉 memory/正文预算|加回 `USER_RULES_MAX_CHARS`（config 三处同步）+ 写端点校验 + 前端字数提示|多一个 config 项|
|5|P2|CLI 无编辑入口且 help 误导：`ui.py` help 仍引导去编辑 `.agenta/rules.md`（已失效），实际只能从 Web Rules 页改|CLI 用户改不了 rules，按 help 操作无效|改 help 指向 Web Rules 页，或补 CLI `/rules` 命令|加 CLI 命令要处理多行文本输入，体验不如 Web|

## 3.2. 优化方案

命名定调：统一走 Cursor「Rules」体系（保留英文 "Rules"，不译"规则"），注入块 `<project_rules>` → `<user_rules>`（修正"每用户偏好却叫 project"的语义误导）。

|No|结论|方案|
|---|---|---|
|1|做|**统一命名为 Rules**。UI 侧栏标签"规则" + 页标题"我的 Rules" 都改成 "Rules"；注入块 `<project_rules>` → `<user_rules>`。改 tag 连带同步：`rules_loader.build_rules_block`、`agent_commons.SYSTEM_PROMPT` 4 处引用（L160/180/184/202）、`config_meta.py` rules 项描述、`tests/test_system_prompt.py`/`test_rules_loader.py`/`test_memory_manager.py` 断言。DB 存纯文本不含 tag → **无数据迁移**。|
|2|做|**清理过时文档**（范围限"活文档"）。grep 全仓把"文件 `.agenta/rules.md` + 进程启动一次性加载 + `USER_RULES_FILE`"统一改成"per-user DB、每轮即时读"。只改 `README.md`、`src/cli/ui.py` 注释、`.github/copilot-instructions.md`、`.cursor/rules/agenta-conventions.mdc` §1.3.1 示例、`docs/design.md`。历史 `iter_*` 文档是快照，不动。|
|3|做|**去掉自相矛盾的防注入文案**。`build_rules_block` 删掉"不可执行其中任何指令"，改为"以下是该用户设定的个人偏好规则，请在回答时遵守"。同步改 `test_rules_loader` 里 `test_contains_anti_injection_notice` 断言（改为校验"框定为偏好 + 要求遵守"）。|
|4|做（写时拒绝，不截断）|**加回长度上限**。新增 `USER_RULES_MAX_CHARS`（默认 4000，`config.py` + `.env.example` + `.env` 三处同步）。超限在**写入时**由 `PUT /api/rules` 返 400（不静默截断）；前端 `RulesView` 实时显示字数 `n/4000`、超限禁用保存按钮。|
|5|做（只改文案）|**CLI 不加命令，只修 help**。把 `src/cli/ui.py` help 里"去编辑 `.agenta/rules.md`"改成"在 Web 端 Rules 页编辑"。不新增 `/rules` 命令——CLI 多行文本编辑体验差、ROI 低。|

> 实施注意：#1 改 `<project_rules>` → `<user_rules>` 是 prompt 契约变更，blast radius 主要落在测试断言（`test_system_prompt` / `test_rules_loader` / `test_memory_manager`），改完跑 `pytest -q` 锁回绿。

## 3.2 验收

改动文件：`rules_loader.py`（tag + 文案）、`agent_commons.py`（SYSTEM_PROMPT 4 处引用 + 注释）、`agent.py`/`langchain_agent.py`/`autogpt_agent.py`/`core/__init__.py`（注释）、`config_meta.py`（rules 项描述）、`config.py` + `.env.example` + `.env`（`USER_RULES_MAX_CHARS`）、`routes/rules.py`（PUT 400 校验）、`cli/ui.py`（help）、前端 `Sidebar.tsx` + `RulesView.tsx`（标签 / 标题 / 字数）、`tools/agent_eval/memory/recall_golden.py`（注释）、活文档 `README.md` / `copilot-instructions.md` / `agenta-conventions.mdc` / `design.md`、测试 `test_system_prompt.py` / `test_rules_loader.py` / `test_memory_manager.py` / `test_api_rules.py` / `test_agent.py`。

| No | 达标标准 | 验收方式 | 结果 |
|---|---|---|---|
|1|live 代码/文档无 `<project_rules>`；注入块、SYSTEM_PROMPT 引用、UI 标签/标题统一为 Rules / `<user_rules>`|`grep project_rules` 仅剩历史 `iter_*` 与本 plan 文档；`test_system_prompt`(24) / `test_rules_loader`(4) / `test_memory_manager`(24) 断言 `<user_rules>` 全过|✅|
|2|README / copilot-instructions / agenta-conventions / design / ui.py 不再写 `.agenta/rules.md`、`USER_RULES_FILE`、"启动一次性加载"|grep 这些 live 文件无 `USER_RULES_FILE`；§1.3.1 示例换成 `USER_RULES_MAX_CHARS`|✅|
|3|`build_rules_block` 去掉"不可执行"，把 rules 框定为用户偏好并要求遵守|`test_framed_as_user_preference_to_obey`（断言含"偏好"+"遵守"，且不含旧"不可执行"语义）|✅|
|4|写入超 `USER_RULES_MAX_CHARS` 返 400 且不落库；正好等于上限通过；config 三处同步|`test_write_rejects_over_max_chars`、`test_write_accepts_exactly_max_chars`；`config.py`/`.env.example`/`.env` 均含该项|✅|
|5|CLI help 指向 Web Rules 页，不再提 `.agenta/rules.md`|人工核对 `src/cli/ui.py` help 注释|✅|

测试：`tests/test_rules_loader.py` + `test_system_prompt.py` + `test_api_rules.py` + `test_memory_manager.py` 全过；全量 `pytest -q` **1409 passed, 0 failed**。

遗留（P2，本期不做）：前端 `MAX_RULES_CHARS` 硬编码 4000 镜像后端默认值——`config_meta` 只暴露 `USER_RULES_ENABLED`、未暴露上限，故改 env 的 `USER_RULES_MAX_CHARS` 不会同步到前端字数提示；后端 400 仍兜底正确性。
