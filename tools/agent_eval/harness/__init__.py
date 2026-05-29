"""Phase 2.5 Harness 自检评估器（详 docs/iter_2_agent.md §4.9.10）。

评估的是 critic 自身判得准不准（验收 ⑤），不是主路径产出好不好；
critic prompt 文件 (`quiz_critic.txt` / `rag_critic.txt`) 与生产路径
[`HarnessManager`](../../../src/agent/core/harness_manager.py) 共享。
"""
