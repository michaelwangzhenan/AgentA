#!/usr/bin/env bash
# tools/ut.sh — 快速运行 UT 的辅助脚本
# 用法：bash tools/ut.sh [档位 | 模块]

PYTEST=".venv/Scripts/python -m pytest"

usage() {
    cat <<EOF
用法: bash tools/ut.sh [档位 | 模块]

档位（横切 marker 过滤，与 pytest.ini 对齐）:
  -fast       默认套件（最常用）：kimi+qwen + helper + RAG + parser + tools …
              跳过 integration / langchain / autogpt / extended_providers
  -ext        默认 + extended_providers（kimi/qwen 以外 7 个 LLM provider）
  -int        仅 integration（真实 API/网络，需配置 .env 中相应 key）
  -lc         仅 langchain 标记的用例（用 langchain 0.3 的 AgentExecutor + create_tool_calling_agent）
  -auto       仅 autogpt 标记的用例
  -all        全部，含所有 marker
  -helper     重构安全, 6 个文件（history / memory / event / events / fmt / proto）

按模块（单文件，调试用）:
  -llm        LLM 配置 & Provider             (tests/test_llm.py)
  -parser     文档解析                         (tests/test_parser.py)
  -rag        分块 & 双语检索 & Reranker       (tests/test_rag.py)
  -tools      工具层（search/fetch）            (tests/test_tools.py)
  -agent      Agent ReAct 循环                 (tests/test_agent.py)
  -memory     ChatHistoryStore CRUD            (tests/test_memory.py)
  -prompt     自定义 Prompt 加载               (tests/test_prompt_loader.py)
  -skill      Skills 加载与激活                (tests/test_skill_loader.py)
  -save       对话导出 _save_history           (tests/test_save_history.py)
  -history    HistoryManager 行为基线           (tests/test_history_manager.py)
  -mem        MemoryManager 注入/抽取           (tests/test_memory_manager.py)
  -event      EventBus 行为契约                  (tests/test_event_bus.py)
  -events     Agent loop 事件流契约              (tests/test_agent_events.py)
  -fmt        format_search_results            (tests/test_format_search_results.py)
  -proto      AgentAPI 一致性                    (tests/test_agent_protocol.py)
EOF
}

case "$1" in
    -h|--help|"")
        usage ;;
    # ── 档位 ───────────────────────────────────────────────
    -fast)   $PYTEST ;;
    -ext)    $PYTEST -m "not integration and not langchain and not autogpt" ;;
    -int)    $PYTEST -m "integration" ;;
    -lc)     $PYTEST -m "langchain" ;;
    -auto)   $PYTEST -m "autogpt" ;;
    -all)    $PYTEST -m "" ;;
    -helper) $PYTEST tests/test_history_manager.py tests/test_memory_manager.py \
                     tests/test_event_bus.py tests/test_agent_events.py \
                     tests/test_format_search_results.py tests/test_agent_protocol.py ;;
    # ── 按模块 ─────────────────────────────────────────────
    -llm)     $PYTEST tests/test_llm.py ;;
    -parser)  $PYTEST tests/test_parser.py ;;
    -rag)     $PYTEST tests/test_rag.py ;;
    -tools)   $PYTEST tests/test_tools.py ;;
    -agent)   $PYTEST tests/test_agent.py ;;
    -memory)  $PYTEST tests/test_memory.py ;;
    -prompt)  $PYTEST tests/test_prompt_loader.py ;;
    -skill)   $PYTEST tests/test_skill_loader.py ;;
    -save)    $PYTEST tests/test_save_history.py ;;
    -history) $PYTEST tests/test_history_manager.py ;;
    -mem)     $PYTEST tests/test_memory_manager.py ;;
    -event)   $PYTEST tests/test_event_bus.py ;;
    -events)  $PYTEST tests/test_agent_events.py ;;
    -fmt)     $PYTEST tests/test_format_search_results.py ;;
    -proto)   $PYTEST tests/test_agent_protocol.py ;;
    *)
        echo "❌ 未知参数: $1"
        echo ""
        usage
        exit 1 ;;
esac
