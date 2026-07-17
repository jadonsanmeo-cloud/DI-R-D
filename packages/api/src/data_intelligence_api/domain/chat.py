from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChatRole = Literal["human", "view", "system", "ai"]


@dataclass(slots=True)
class ChatMessage:
    role: ChatRole
    context: str
    order: int
    time_stamp: int
    model_name: str
    retry: bool | None = None
    thinking: bool | None = None
    outing: bool | None = None
    feedback: dict | None = None


@dataclass(slots=True)
class ChatConversation:
    conv_uid: str
    user_input: str = ""
    user_name: str = "default"
    chat_mode: str = "chat_normal"
    select_param: str = ""
    app_code: str = ""
    model_name: str = ""
    gmt_created: str = ""
    gmt_modified: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
