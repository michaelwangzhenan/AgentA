#!/usr/bin/env bash
# t.sh —— 快速运行 UT 的辅助脚本
# 用法：bash t.sh [参数]  或  chmod +x t.sh && ./t.sh [参数]

PYTEST=".venv/Scripts/python -m pytest"

usage() {
    cat <<EOF
用法: bash t.sh [参数]

参数:
  -h          显示本帮助信息
  -all        运行全部测试
  -not        运行非集成测试（排除 @pytest.mark.integration）
  -llm        LLM 配置 & Provider         (tests/test_llm.py)
  -parser     文档解析（7 种格式）         (tests/test_parser.py)
  -rag        分块 & 双语检索 & Reranker   (tests/test_rag.py)
  -tools      工具层（search/fetch）        (tests/test_tools.py)
  -agent      Agent ReAct 循环             (tests/test_agent.py)
  -memory     对话记忆持久化               (tests/test_memory.py)
  -prompt     自定义 Prompt 加载           (tests/test_prompt_loader.py)
  -skill      Skills 加载与激活            (tests/test_skill_loader.py)
  -save       对话导出 _save_history       (tests/test_save_history.py)
EOF
}

case "$1" in
    -h|--help|"")
        usage ;;
    -all)
        $PYTEST ;;
    -not)
        $PYTEST -m "not integration" -v ;;
    -llm)
        $PYTEST tests/test_llm.py -v ;;
    -parser)
        $PYTEST tests/test_parser.py -v ;;
    -rag)
        $PYTEST tests/test_rag.py -v ;;
    -tools)
        $PYTEST tests/test_tools.py -v ;;
    -agent)
        $PYTEST tests/test_agent.py -v ;;
    -memory)
        $PYTEST tests/test_memory.py -v ;;
    -prompt)
        $PYTEST tests/test_prompt_loader.py -v ;;
    -skill)
        $PYTEST tests/test_skill_loader.py -v ;;
    -save)
        $PYTEST tests/test_save_history.py -v ;;
    *)
        echo "❌ 未知参数: $1"
        echo ""
        usage
        exit 1 ;;
esac
