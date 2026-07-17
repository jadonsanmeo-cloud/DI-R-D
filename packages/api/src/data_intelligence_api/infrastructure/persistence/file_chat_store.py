from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_intelligence_api.domain.chat import ChatConversation, ChatMessage

SAFE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class FileChatStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.conversations_dir = self.root / "conversations"
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        chat_mode: str,
        model_name: str,
        user_name: str = "default",
        app_code: str = "",
    ) -> ChatConversation:
        now = utc_now()
        return ChatConversation(
            conv_uid=f"conv_{uuid.uuid4().hex}",
            user_name=user_name,
            chat_mode=chat_mode or "chat_normal",
            app_code=app_code,
            model_name=model_name,
            gmt_created=now,
            gmt_modified=now,
        )

    def get(self, conv_uid: str) -> ChatConversation | None:
        path = self._path_for(conv_uid)
        if not path.exists():
            return None

        payload = json.loads(path.read_text(encoding="utf-8"))
        return conversation_from_payload(payload)

    def list(self) -> list[ChatConversation]:
        conversations: list[ChatConversation] = []

        for path in self.conversations_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                conversations.append(conversation_from_payload(payload))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                # A bad local file should not break the whole chat list.
                continue

        return sorted(
            conversations,
            key=lambda item: item.gmt_modified or item.gmt_created,
            reverse=True,
        )

    def save(self, conversation: ChatConversation) -> None:
        self._validate_conv_uid(conversation.conv_uid)
        conversation.gmt_modified = utc_now()

        path = self._path_for(conversation.conv_uid)
        payload = json.dumps(asdict(conversation), ensure_ascii=True, indent=2)

        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)

    def delete(self, conv_uid: str) -> bool:
        path = self._path_for(conv_uid)
        if not path.exists():
            return False

        path.unlink()
        return True

    def clear(self, conv_uid: str) -> ChatConversation | None:
        conversation = self.get(conv_uid)
        if conversation is None:
            return None

        conversation.messages.clear()
        self.save(conversation)
        return conversation

    def _path_for(self, conv_uid: str) -> Path:
        self._validate_conv_uid(conv_uid)
        return self.conversations_dir / f"{conv_uid}.json"

    @staticmethod
    def _validate_conv_uid(conv_uid: str) -> None:
        if not SAFE_CONVERSATION_ID.fullmatch(conv_uid):
            raise ValueError(f"Invalid conversation id: {conv_uid}")


def current_millis() -> int:
    return int(time.time() * 1000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def conversation_from_payload(payload: dict[str, Any]) -> ChatConversation:
    messages = [
        ChatMessage(**message)
        for message in payload.get("messages", [])
        if isinstance(message, dict)
    ]

    return ChatConversation(
        conv_uid=str(payload["conv_uid"]),
        user_input=str(payload.get("user_input", "")),
        user_name=str(payload.get("user_name", "default")),
        chat_mode=str(payload.get("chat_mode", "chat_normal")),
        select_param=str(payload.get("select_param", "")),
        app_code=str(payload.get("app_code", "")),
        model_name=str(payload.get("model_name", "")),
        gmt_created=str(payload.get("gmt_created", "")),
        gmt_modified=str(payload.get("gmt_modified", "")),
        messages=messages,
    )