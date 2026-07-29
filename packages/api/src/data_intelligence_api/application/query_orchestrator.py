"""Route general questions before entering the data workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from data_intelligence_sdk.core.types import SessionContext, UserQuery
from data_intelligence_sdk.runtime.logger import RuntimeLogger


DELEGATION_TOOL_NAME = "delegate_to_data_flow"


@dataclass(frozen=True, slots=True)
class DirectGeneralAnswer:
    text: str


@dataclass(frozen=True, slots=True)
class DelegateToDataFlow:
    pass


@dataclass(frozen=True, slots=True)
class OrchestratorModelResponse:
    text: str | None = None
    tool_calls: tuple[str, ...] = ()


class QueryOrchestratorClient(Protocol):
    async def decide(
        self,
        *,
        messages: list[dict[str, str]],
        tool_name: str,
    ) -> OrchestratorModelResponse: ...


class GeneralQueryOrchestrator:
    def __init__(
        self,
        client: QueryOrchestratorClient,
        *,
        logger: RuntimeLogger | None = None,
    ) -> None:
        self.client = client
        self.logger = logger

    async def route(
        self,
        query: UserQuery,
        session_context: SessionContext | None = None,
    ) -> DirectGeneralAnswer | DelegateToDataFlow:
        self._log("orchestrator.started", {"query_character_count": len(query.text)})
        try:
            response = await self.client.decide(
                messages=_build_messages(query, session_context),
                tool_name=DELEGATION_TOOL_NAME,
            )
        except Exception as exc:
            self._log("orchestrator.failed", {"error_type": type(exc).__name__})
            raise

        if response.tool_calls:
            self._log(
                "orchestrator.data_flow.delegated",
                {
                    "tool_call_count": len(response.tool_calls),
                    "recognized_tool": DELEGATION_TOOL_NAME
                    in response.tool_calls,
                },
            )
            return DelegateToDataFlow()

        text = (response.text or "").strip()
        if not text:
            error = RuntimeError("Orchestrator returned an empty answer.")
            self._log("orchestrator.failed", {"error_type": type(error).__name__})
            raise error

        self._log(
            "orchestrator.direct_answer.selected",
            {"answer_character_count": len(text)},
        )
        return DirectGeneralAnswer(text)

    def _log(self, event: str, payload: dict[str, object]) -> None:
        if self.logger is not None:
            self.logger.log(event, payload)


def _build_messages(
    query: UserQuery,
    session_context: SessionContext | None,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if session_context is not None:
        for turn in session_context.turns:
            role = _normalize_role(turn.get("role"))
            content = str(turn.get("text", turn.get("content", ""))).strip()
            if role and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": query.text})
    return messages


def _normalize_role(value: object) -> str | None:
    normalized = str(value or "").lower()
    if normalized in {"user", "human"}:
        return "user"
    if normalized in {"assistant", "ai", "view"}:
        return "assistant"
    if normalized == "system":
        return "system"
    return None


_SYSTEM_PROMPT = """You are the routing orchestrator for a data intelligence API.

Answer the user directly only when the answer can be produced from general
knowledge or the supplied conversation context. You have no access to the
user's private documents, organization corpus, databases, files, Method Hub,
MCP tools, or sandbox.

Call `delegate_to_data_flow` whenever the request requires inspecting,
calculating, searching, summarizing, comparing, or verifying user- or
organization-specific data. Never guess private data. Delegate when uncertain.

Do not call the tool for general explanations, definitions, or guidance that
does not depend on private data.
"""
