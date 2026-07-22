"""
AgentAPI —— 表现层与 Agent core 之间的对外契约。

调用方（CLI / Web API / SDK）只依赖本 Protocol，不绑定 Agent / LangChainAgent /AutoGPTAgent 任一具体类。
三种实现并列、无共同父类，故用 typing.Protocol +runtime_checkable 而非抽象基类；
契约破坏由 tests/agent/test_agent_protocol.py 的 isinstance 断言在 CI 报错。

命名：历史上叫 BaseAgent，易与 loop 层内部契约混淆，故改为 AgentAPI。
文件放 src/agent/ 顶层而非 core/：core 是 helper 实现层，本文件是包入口契约。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from src.agent.core.event_bus import AgentEvent, EventBus


@runtime_checkable
class AgentAPI(Protocol):
    """
    三种 Agent 实现共享的最小对外契约。

    可读实例属性：
        session_id:   会话 ID（uuid 字符串）
        last_usage:   最近一次 run() 的 token 统计，未运行过为 None
        thinking_cfg: Extended Thinking 配置，未启用为 None
        events:       EventBus；按事件类型细粒度订阅用 events.subscribe，
                      一般场景用 set_event_callback 即可

    不暴露 _session_store / _llm / _tools 等内部字段，避免表现层耦合实现细节。
    """

    session_id: str
    last_usage: Any  # TokenUsage | None；用 Any 避免 Protocol 与具体 NamedTuple 解耦
    thinking_cfg: Any  # ThinkingConfig | None；同上
    events: EventBus

    def run(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        event_callback: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        """执行一轮推理，返回 LLM 最终回答。失败时返回 'Error: <msg>' 而非抛异常。

        session_id / event_callback 为可选 per-run 入参：传入时只作用于本次调用、不写回
        实例字段，使进程级单例 Agent 能被多请求并发调用而互不串台（默认 Python Agent
        实现真正隔离；CLI 等单实例场景可不传，沿用实例的 session_id / events）。
        """
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
