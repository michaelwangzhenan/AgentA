"""Chat 端点请求 / 响应模型"""

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import src.config as config


def assert_message_within_limit(message: str) -> None:
    """校验聊天消息 UTF-8 字节数；超限抛 413。"""
    nbytes = len(message.encode("utf-8"))
    if nbytes > config.CHAT_MESSAGE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"消息过长（{nbytes} 字节，上限 {config.CHAT_MESSAGE_MAX_BYTES} 字节）"
            ),
        )


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入文本")
    session_id: str | None = Field(
        None,
        description="目标 session id；不传则用 Agent 当前 session_id（兼容 Step 2 行为）",
    )
    mode: Literal["chat", "deep_research"] | None = Field(
        None,
        description="对话模式；deep_research 走 ResearchEngine 深度研究，缺省 / chat 为普通对话",
    )
    skip_cache: bool = Field(
        False,
        description="是否跳过语义缓存（前端「重新生成」勾选）：不查也不写缓存，用当前选定模型重答",
    )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Agent 最终回答")
    session_id: str = Field(..., description="当前 Agent 实例的 session id")
    model: str = Field("", description="本次实际应答的模型 id；缓存命中时为空")
    cached: bool = Field(False, description="本次回答是否直接来自语义缓存")
