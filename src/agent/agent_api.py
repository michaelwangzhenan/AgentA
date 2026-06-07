"""
AgentAPI —— 表现层 ↔ Agent core 的对外契约

设计意图：
- 表现层（CLI / 未来 Web UI / SDK）调用 Agent 只依赖本契约
- 三种 Agent 实现（Python / LangChain / AutoGPT）通过 duck typing 都满足此 Protocol
- 任一实现破坏契约会在 `tests/test_agent_protocol.py` 的 `isinstance` 断言上 CI 红出来
- 用 `typing.Protocol + runtime_checkable` 而非抽象基类 —— 三种 Agent 是并列实现,
  不存在父类关系；runtime_checkable 让 isinstance 校验可用

命名说明：
- 此处的 `AgentAPI` 即架构图里"表现层 ↔ Agent core"边界上的那个节点
- 历史上叫过 `BaseAgent`，但容易与"loop ↔ 公共层"的内部契约混淆，故改名为 `AgentAPI`
- 文件名 `agent_api.py` 与项目内 `*_agent.py / *_history.py / *_provider.py`
  命名 pattern 一致，避免与笼统的 `api.py` 混淆

为什么不放 `src/agent/core/`：core/ 是"helper 实现"层（HistoryManager 等），
`AgentAPI` 是"对外契约"层；放 `src/agent/` 顶层更符合"包入口契约"的语义。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from src.agent.core.event_bus import AgentEvent, EventBus


@runtime_checkable
class AgentAPI(Protocol):
    """
    三种 Agent 实现（Python / LangChain / AutoGPT）共享的最小对外契约。

    实例属性（运行时必须可读）：
        session_id:    会话 ID（uuid 字符串）
        last_usage:    最近一次 run() 的 token 统计；从未运行时为 None
        thinking_cfg:  Extended Thinking 配置（None 表示未启用）
        events:        `EventBus` 实例 —— 高级订阅者直接 `agent.events.subscribe(...)`
                       即可订阅特定事件类型；普通用例用 `set_event_callback` 即可

    Note: 不在此 Protocol 中暴露 `_chat_history / _llm / _tools` 等实现内部字段，
    避免把表现层耦合到具体实现。
    """

    session_id: str
    last_usage: Any  # TokenUsage | None；用 Any 避免 Protocol 与具体 NamedTuple 解耦
    thinking_cfg: Any  # ThinkingConfig | None；同上
    events: EventBus

    def run(self, user_input: str) -> str:
        """执行一轮推理，返回 LLM 最终回答。失败时返回 'Error: <msg>' 而非抛异常。"""
        ...

    def activate_skill(self, name: str, body: str) -> bool:
        """注入 Skill 到 system_prompt。返回 True=新激活，False=已激活过。"""
        ...

    def set_event_callback(
        self, callback: Callable[[AgentEvent], None] | None
    ) -> None:
        """
        设置统一事件回调（覆盖语义）：传 None 清空。

        实现层会自动订阅所有事件类型，并把底层 EventBus 的事件包装成 `AgentEvent`
        实例转发给 callback；高级订阅者请直接用 `agent.events.subscribe(event_type, fn)`
        以便按事件类型 fine-grained 订阅。
        """
        ...
