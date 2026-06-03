"""依赖注入：API 层向 Agent core 拿实例的统一入口。

Step 1 用最朴素的默认值实例化单例 Agent；不加载 skills / rules / 自定义 prompt。
Step 5 会把这套配置抽出 composition root 跟 CLI 共用。
"""

from functools import lru_cache

from src.agent.agent import Agent
from src.agent.agent_api import AgentAPI


@lru_cache(maxsize=1)
def get_agent() -> AgentAPI:
    """返回进程级单例 Agent。

    用 `lru_cache(maxsize=1)` 实现"首次调用时构造、之后复用"的语义。
    返回 `AgentAPI` 契约类型，调用方不绑定具体实现。
    """
    return Agent(verbose=False)
