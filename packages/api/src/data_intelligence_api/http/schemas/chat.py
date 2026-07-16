from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatCompletionRequest(BaseModel):
    conv_uid: str = Field(min_length=1)
    chat_mode: str = "chat_normal"
    model_name: str | None = None
    user_input: Any
    app_code: str = ""


class ApiEnvelope(BaseModel):
    success: bool = True
    err_code: str | None = None
    err_msg: str | None = None
    data: Any
