from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from data_intelligence_api.application.chat_service import ChatService
from data_intelligence_api.http.schemas.chat import ChatCompletionRequest


def create_chat_router(chat_service: ChatService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/chat/dialogue/new")
    async def new_dialogue(
        chat_mode: str = Query("chat_normal"),
        model_name: str = Query(""),
        user_id: str = Header("default", alias="user-id"),
    ) -> JSONResponse:
        conversation = chat_service.create_conversation(
            chat_mode=chat_mode,
            model_name=model_name,
            user_name=user_id or "default",
        )
        return _ok(_conversation_payload(conversation))

    @router.get("/api/v1/chat/dialogue/list")
    async def list_dialogues() -> JSONResponse:
        return _ok(
            [
                _conversation_payload(conversation)
                for conversation in chat_service.list_conversations()
            ]
        )

    @router.get("/api/v1/chat/dialogue/messages/history")
    async def get_history(con_uid: str = Query(...)) -> JSONResponse:
        try:
            messages = chat_service.get_history(con_uid)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _ok([_message_payload(message) for message in messages])

    @router.post("/api/v1/chat/dialogue/delete")
    async def delete_dialogue(con_uid: str = Query(...)) -> JSONResponse:
        try:
            deleted = chat_service.delete_conversation(con_uid)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _ok({"deleted": deleted})

    @router.post("/api/v1/chat/dialogue/clear")
    async def clear_dialogue(con_uid: str = Query(...)) -> JSONResponse:
        try:
            conversation = chat_service.clear_conversation(con_uid)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _ok({"cleared": conversation is not None})

    @router.post("/api/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        user_id: str = Header("default", alias="user-id"),
    ) -> StreamingResponse:
        user_input = _extract_user_input_text(request.user_input)
        model_name = request.model_name or ""

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for content in chat_service.complete_chat(
                    conv_uid=request.conv_uid,
                    chat_mode=request.chat_mode,
                    model_name=model_name,
                    user_input=user_input,
                    user_name=user_id or "default",
                    app_code=request.app_code,
                ):
                    yield _sse_data(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": content,
                                    }
                                }
                            ]
                        }
                    )
                yield "data: [DONE]\n\n"
            except Exception as error:
                yield f"data: [ERROR]{str(error)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return router


def _ok(data: Any) -> JSONResponse:
    return JSONResponse(
        {
            "success": True,
            "err_code": None,
            "err_msg": None,
            "data": data,
        }
    )


def _conversation_payload(conversation: object) -> dict[str, Any]:
    payload = asdict(conversation)
    payload.pop("messages", None)
    return payload


def _message_payload(message: object) -> dict[str, Any]:
    payload = asdict(message)
    return {key: value for key, value in payload.items() if value is not None}


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _extract_user_input_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return " ".join(parts)
    return str(value)
