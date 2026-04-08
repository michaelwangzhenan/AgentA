"""CLI 显示常量 —— Banner 和帮助文本。"""

import src.config as config

BANNER = """
╔══════════════════════════════════════════════╗
║         私有知识库 Agent  v0.1               ║
║  LLM: {provider:<38} ║
║  输入 /help 查看命令列表                     ║
╚══════════════════════════════════════════════╝
""".format(provider=config.ACTIVE_PROVIDER)

HELP_TEXT = """
可用命令：
  /help                      显示本帮助信息
  /ingest                    扫描默认 docs/ 目录并入库（模型: .env EMBEDDING_MODEL）
  /ingest <目录>             扫描指定目录，例：/ingest D:/mydata
  /ingest <目录> -m zh       指定目录 + 中文模型（BAAI/bge-small-zh）
  /ingest <目录> -m en       指定目录 + 英文模型（all-MiniLM-L6-v2）
  /ingest -m zh              默认目录 + 中文模型
  /clear                     清空当前 session 的对话历史并重置 Agent
  /history                   查看当前 session 的历史对话摘要
  /session                   列出所有历史 session
  /session <id>              切换到指定 session 并恢复历史
  /del-session <id>          彻底删除指定历史 session 的所有记录（不可恢复）
  /clean-session             清空所有历史 session 的记录（不可恢复）
  /reload-prompts            重新扫描 advanced/prompts/ 目录，刷新自定义 Prompt 命令
  /reload-skills             重新扫描 advanced/skills/ 目录，刷新 Skill 列表
  /<prompt_name> [问题]      切换到指定自定义 Prompt 并重置 Agent，可附带首个问题
  /<skill_name> [问题]       激活指定 Skill（注入 Skill 指令到当前会话），可附带首个问题
  /save <文件名>             导出当前 session 完整对话到 history/<文件名>.md
  /thinking                  查看 Extended Thinking 状态
  /thinking on/off           开启/关闭 Extended Thinking（Claude / Qwen3 有效，其余降级）
  /thinking adaptive         开启 Adaptive Thinking：自动按问题复杂度估算 budget
  /thinking budget <N>       手动设置 thinking budget tokens（默认 8000，上限 32000）
  /memory                    展示跨 session 用户记忆
  /memory del <id>           删除指定记忆条目
  /memory clear              清空全部用户记忆
  /quit                      退出程序
  /exit                      退出程序（同 /quit）

"""
# 模型别名：
#   en  →  all-MiniLM-L6-v2   英文/多语言
#   zh  →  BAAI/bge-small-zh   中文优化

# 自定义 Prompt：
#   在 advanced/prompts/ 目录下放置 <名称>.prompt.md 文件即可。
#   文件名即命令名（如 5g-expert.prompt.md → /5g-expert），名称只允许字母、数字、- 和 _。

# Skills：
#   在 advanced/skills/<名称>/SKILL.md 放置符合 agentskills.io 规范的 Skill。
#   Agent 会自动发现并在合适时调用；也可用 /<skill_name> [问题] 手动激活。

# 直接输入问题即可开始对话。
