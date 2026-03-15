"""
CLI 入口 —— 私有知识库 Agent 对话界面

使用方式：
    python main.py

命令：
    输入问题后回车即可对话
    输入 /help              查看帮助
    输入 /ingest            重新扫描默认 docs/ 目录并入库（默认模型）
    输入 /ingest <目录> [-m en|zh]  扫描指定目录，可选指定模型
    输入 /clear             清空对话历史（重置 Agent）
    输入 /quit 或 /exit 或 Ctrl+C 退出
"""

import logging
import sys
import warnings

from dotenv import load_dotenv

# 消除 HuggingFace tokenizer 的 FutureWarning
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

load_dotenv(override=True)  # override=True 确保 .env 覆盖系统环境变量

# 设置日志：INFO 级别，只显示工具调用信息，不显示 DEBUG
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
# 关闭第三方库的冗余日志
for _noisy in ("httpx", "httpcore", "openai", "chromadb", "sentence_transformers"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from agent.agent import Agent
import config


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
  /clear                     清空对话历史，重置 Agent
  /quit                      退出程序
  /exit                      退出程序（同 /quit）

模型别名：
  en  →  all-MiniLM-L6-v2   英文/多语言
  zh  →  BAAI/bge-small-zh   中文优化

直接输入问题即可开始对话。
"""


def _run_ingest(docs_dir: str | None = None, model: str | None = None) -> None:
    """在 CLI 中触发文档入库，可指定文档目录和 embedding 模型。"""
    import os
    target_dir = docs_dir or config.DOCS_DIR
    target_model = model or config.DEFAULT_EMBEDDING_ALIAS
    # 路径校验
    if not os.path.exists(target_dir):
        print(f"❌ 目录不存在: {target_dir}\n")
        return
    if not os.path.isdir(target_dir):
        print(f"❌ 路径不是目录: {target_dir}\n")
        return
    model_name, collection_name = config.resolve_embedding(target_model)
    print(f"\n⏳ 正在扫描 {target_dir}")
    print(f"   Embedding 模型: {model_name}  →  collection: {collection_name}\n")
    try:
        from rag.ingest import ingest_all
        ingest_all(docs_dir=target_dir, model=target_model)
        print()
    except Exception as e:
        print(f"❌ 入库失败: {e}\n")


def main() -> None:
    """CLI 主循环。"""
    print(BANNER)

    agent = Agent(verbose=True)

    while True:
        try:
            user_input = input("你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 再见！")
            sys.exit(0)

        if not user_input:
            continue

        # ── 内置命令处理 ──────────────────────────────────────────────────────
        cmd_lower = user_input.lower()
        cmd_parts = user_input.split(maxsplit=1)  # 保留原始大小写用于路径
        match cmd_lower.split()[0] if cmd_lower.split() else "":
            case "/quit" | "/exit":
                print("👋 再见！")
                sys.exit(0)
            case "/help":
                print(HELP_TEXT)
                continue
            case "/ingest":
                # 解析 /ingest [<目录>] [-m <模型>]
                # 支持: /ingest  /ingest ./docs_zh  /ingest ./docs_zh -m zh  /ingest -m zh
                raw_args = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                docs_dir: str | None = None
                model_alias: str | None = None

                # 简单手动解析，避免引入 argparse
                tokens = raw_args.split()
                i = 0
                while i < len(tokens):
                    if tokens[i] in ("-m", "--model") and i + 1 < len(tokens):
                        model_alias = tokens[i + 1]
                        i += 2
                    elif not tokens[i].startswith("-"):
                        docs_dir = tokens[i]
                        i += 1
                    else:
                        i += 1  # 忽略未知标志

                _run_ingest(docs_dir=docs_dir, model=model_alias)
                continue
            case "/clear":
                agent = Agent(verbose=True)
                print("✅ 对话历史已清空，Agent 已重置。\n")
                continue

        # ── 正常问答 ──────────────────────────────────────────────────────────
        print()
        try:
            reply = agent.run(user_input)
            print(f"Agent: {reply}\n")
        except KeyboardInterrupt:
            print("\n⚠️  已中断当前回答。\n")
        except Exception as e:
            print(f"❌ 出错了: {e}\n")


if __name__ == "__main__":
    main()
