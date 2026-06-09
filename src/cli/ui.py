"""CLI 显示常量 —— Banner 和帮助文本。"""

import src.config as config

BANNER = """
╔══════════════════════════════════════════════╗
║         私有知识库 Agent  v0.1               ║
║  LLM: {provider:<38} ║
║  实现: {method:<38} ║
║  输入 /help 查看命令列表                     ║
╚══════════════════════════════════════════════╝
""".format(provider=config.ACTIVE_MODEL, method=config.IMP_METHOD)

HELP_TEXT = """
可用命令：
  /help                      显示本帮助信息
  /clear                     清空当前 session 的对话历史并重置 Agent
  /history                   查看当前 session 的历史对话摘要
  /sessions                  列出所有历史 session（▶ 标记当前 session）
  /sessions <关键词>         按 session id 前缀或首问内容搜索
  /session <id>              切换到指定 session 并恢复历史
  /del-session <id>          彻底删除指定历史 session 的所有记录（不可恢复）
  /clean-session             清空所有历史 session 的记录（不可恢复）
  /reload-skills             重新扫描 .agenta/skills/ 目录，刷新 Skill 列表
  /<skill_name> [问题]       激活指定 Skill（注入 Skill 指令到当前会话），可附带首个问题
  /save <文件名>             导出当前 session 完整对话到 history/<文件名>.md
  /thinking                  查看 Extended Thinking 状态
  /thinking on/off           开启/关闭 Extended Thinking（claude/qwen/kimi/deepseek/glm/minimax 有效，其余降级）
  /thinking budget <N>       手动设置 thinking budget tokens（仅 claude/qwen 生效，默认 8000）
  /memory                    展示跨 session 用户记忆（扁平自然语言列表）
  /memory add <内容...>      手动追加一条记忆（一句自然语言）
  /memory edit <id> <新内容> 改写指定记忆条目的内容
  /memory del <id>           删除指定记忆条目
  /memory clear              清空全部用户记忆
  /study                     列出全部学习计划（▶ 标记 active）
  /study show [plan_id]      查看 active plan / 指定 plan 全貌
  /study switch <plan_id>    切换 active plan（改 DB is_active）
  /study load [plan_id]      把 plan（不传则 active）加载进当前会话 prompt
  /study abandon <plan_id>   放弃指定 plan（标记 abandoned，不删数据）
  /quiz                      列出最近的 quiz（不含 archived）
  /quiz list [plan <pid>]    同上 / 过滤某 plan 的 quiz
  /quiz show <quiz_set_id>   查看单个 quiz 详情（含题目 + 批改细节）
  /quiz del <quiz_set_id>    删除指定 quiz（不可恢复）
  /srs                       列出 active + suspended SRS 卡片（默认 limit 20）
  /srs list [active|suspended]  按状态过滤
  /srs due                   列今天 due 的卡片（next_review_at <= now）
  /srs show <card_id>        查看单卡完整详情（front + back + SM-2 字段）
  /srs stats                 SRS 队列统计（总数 / due / 平均 ease / mature）
  /srs del <card_id>         删除指定卡（不可恢复；推荐 archive 软删）
  /mcp                       列 MCP server 状态（同 /mcp list）
  /mcp list                  同上
  /mcp tools                 列所有 MCP tool（含来源 server）
  /quit                      退出程序
  /exit                      退出程序（同 /quit）

"""
# 模型别名：
#   en  →  all-MiniLM-L6-v2   英文/多语言
#   zh  →  BAAI/bge-small-zh   中文优化

# 个人偏好（Rules）：
#   每个用户一份，存数据库（auth.db.user_rules）；在 Web 端「Rules」页编辑。
#   每轮对话即时读当前用户的 rules，按 base → <user_rules> → <user_context> 顺序注入 system prompt，改完下一轮即生效。

# Skills：
#   在 .agenta/skills/<名称>/SKILL.md 放置符合 agentskills.io 规范的 Skill。
#   Agent 会自动发现并在合适时调用；也可用 /<skill_name> [问题] 手动激活。

# 直接输入问题即可开始对话。
