"""General-purpose data analysis through one request-scoped Deep Agent."""

from __future__ import annotations

import json
import os
from builtins import BaseExceptionGroup
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from data_intelligence_sdk.core.types import (
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    UserQuery,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.deep_agent_backend import DeepAgentSandboxBackend
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolError
from data_intelligence_sdk.runtime.skills import render_workspace_skills
from data_intelligence_sdk.tools import (
    create_execute_python_tool,
    create_internal_memory_tools,
    create_mcp_tools,
)


class AgentInvoker(Protocol):
    def invoke(self, payload: dict[str, Any]) -> Any: ...

    def stream(
        self,
        payload: dict[str, Any],
        *,
        stream_mode: list[str],
    ) -> Iterator[Any]: ...


class LLMInvoker(Protocol):
    def invoke(self, messages: list[Any]) -> Any: ...


AgentFactory = Callable[..., AgentInvoker]

_HIDDEN_DEEP_AGENT_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "write_file",
    }
)


@dataclass(frozen=True, slots=True)
class _AgentStreamResult:
    value: object


@wrap_tool_call(name="RecoverToolErrorsMiddleware")
def _recover_tool_errors(
    request: Any,
    handler: Callable[[Any], Any],
) -> Any:
    try:
        return handler(request)
    except Exception as exc:
        primary_error = _primary_tool_error(exc)
        tool_call = request.tool_call
        payload = {
            "success": False,
            "tool": tool_call.get("name", "unknown"),
            "error_type": type(primary_error).__name__,
            "error": str(primary_error),
            "instruction": (
                "Do not repeat the identical failing call. Correct its arguments, "
                "choose another tool, or continue with the available evidence."
            ),
        }
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=str(tool_call.get("id", "unknown")),
            name=tool_call.get("name"),
            status="error",
        )


def _primary_tool_error(exc: BaseException) -> BaseException:
    leaves = _exception_leaves(exc)
    return next(
        (error for error in leaves if isinstance(error, MCPToolError)),
        leaves[0],
    )


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for nested in exc.exceptions for leaf in _exception_leaves(nested)]
    return [exc]


class GeneralPurposeEngine:
    """Analyze staged data with one Deep Agent and one execution tool."""

    name = "general"
    description = (
        "General-purpose agent for exploratory data analysis, code execution, "
        "question answering, and tasks that do not require a structured report."
    )

    def __init__(
        self,
        llm: LLMInvoker | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        agent_factory: AgentFactory = create_deep_agent,
        allow_method_generation: bool = True,
    ) -> None:
        del allow_method_generation
        self.llm = llm or self._build_openrouter_llm(
            model=model,
            api_key=api_key,
            config_path=config_path,
            config_manager=config_manager,
        )
        self.agent_factory = agent_factory

    def _build_openrouter_llm(
        self,
        *,
        model: str | None,
        api_key: str | None,
        config_path: str | Path | None,
        config_manager: ConfigManager | None,
    ) -> LLMInvoker:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        resolved_key = (
            api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when no api_key is passed."
            )
        resolved_model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_model:
            raise ValueError("LLM_MODEL_NAME is required when no model is passed.")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=resolved_key,
            base_url=settings.base_url,
            model=resolved_model,
            streaming=True,
        )

    def run(
        self,
        input: EngineInput,
    ) -> EngineOutput:
        spec = input.spec
        runtime = input.runtime
        user_context = input.user_context
        del user_context
        if runtime.sandbox is None:
            raise RuntimeError(
                "GeneralPurposeEngine requires a request-scoped sandbox."
            )

        agent = self._build_agent(input)
        result = agent.invoke(
            {
                "messages": self._conversation_messages(
                    input.query,
                    current_text=spec.objective,
                )
            }
        )
        answer = _last_message_text(result).strip()
        grounding = _latest_successful_grounding(runtime)
        if not answer:
            runtime.run_context.record_step(
                "deep_agent_retry_started",
                inputs={
                    "blank_answer": True,
                    "has_successful_grounding": grounding is not None,
                },
            )
            result = agent.invoke(
                {
                    "messages": _retry_messages(result, spec.objective),
                }
            )
            answer = _last_message_text(result).strip()
            grounding = _latest_successful_grounding(runtime)
            runtime.run_context.record_step(
                "deep_agent_retry_completed",
                outputs={
                    "has_answer": bool(answer),
                    "has_successful_grounding": grounding is not None,
                },
            )
        if not answer and grounding is not None:
            runtime.run_context.record_step(
                "deep_agent_fallback_synthesis",
                inputs={"objective": spec.objective},
            )
            answer = self._synthesize_execution_answer(spec, grounding).strip()
        if not answer:
            answer = _render_execution_result(grounding).strip()
        if not answer:
            raise RuntimeError("GeneralPurposeEngine produced no usable answer.")
        return self._build_output(
            spec,
            runtime,
            answer,
        )

    def stream(
        self,
        input: EngineInput,
    ) -> Iterator[str | EngineOutput]:
        """Stream final model text while retaining the normal engine result."""

        spec = input.spec
        runtime = input.runtime
        if runtime.sandbox is None:
            raise RuntimeError(
                "GeneralPurposeEngine requires a request-scoped sandbox."
            )

        agent = self._build_agent(input)
        result: object | None = None
        for event in self._stream_agent_attempt(
            agent,
            self._conversation_messages(
                input.query,
                current_text=spec.objective,
            ),
        ):
            if isinstance(event, _AgentStreamResult):
                result = event.value
            else:
                yield event
        if result is None:
            raise RuntimeError("Deep Agent returned no streamed result.")

        answer = _last_message_text(result).strip()
        grounding = _latest_successful_grounding(runtime)
        if not answer:
            runtime.run_context.record_step(
                "deep_agent_retry_started",
                inputs={
                    "blank_answer": True,
                    "has_successful_grounding": grounding is not None,
                },
            )
            previous_result = result
            result = None
            for event in self._stream_agent_attempt(
                agent,
                _retry_messages(previous_result, spec.objective),
            ):
                if isinstance(event, _AgentStreamResult):
                    result = event.value
                else:
                    yield event
            if result is None:
                raise RuntimeError("Deep Agent retry returned no streamed result.")
            answer = _last_message_text(result).strip()
            grounding = _latest_successful_grounding(runtime)
            runtime.run_context.record_step(
                "deep_agent_retry_completed",
                outputs={
                    "has_answer": bool(answer),
                    "has_successful_grounding": grounding is not None,
                },
            )
        if not answer and grounding is not None:
            runtime.run_context.record_step(
                "deep_agent_fallback_synthesis",
                inputs={"objective": spec.objective},
            )
            answer = self._synthesize_execution_answer(spec, grounding).strip()
        if not answer:
            answer = _render_execution_result(grounding).strip()
        if not answer:
            raise RuntimeError("GeneralPurposeEngine produced no usable answer.")
        yield self._build_output(spec, runtime, answer)

    def _build_agent(self, input: EngineInput) -> AgentInvoker:
        spec = input.spec
        runtime = input.runtime
        if runtime.sandbox is None:
            raise RuntimeError(
                "GeneralPurposeEngine requires a request-scoped sandbox."
            )
        runtime.run_context.record_step(
            "deep_agent_started",
            inputs={"objective": spec.objective},
        )
        execute_python = create_execute_python_tool(runtime)
        mcp_tools = create_mcp_tools(runtime)
        internal_memory_tools = create_internal_memory_tools(
            runtime,
            include_session_history=False,
        )
        self._register_minimal_profile()
        return self.agent_factory(
            model=self.llm,
            tools=[*mcp_tools, *internal_memory_tools, execute_python],
            middleware=[_recover_tool_errors],
            system_prompt=self._system_prompt(spec, runtime, input.query),
            backend=DeepAgentSandboxBackend(runtime.sandbox),
            subagents=[],
            name="general-purpose",
        )

    def _stream_agent_attempt(
        self,
        agent: AgentInvoker,
        messages: list[Any],
    ) -> Iterator[str | _AgentStreamResult]:
        stream = getattr(agent, "stream", None)
        if not callable(stream):
            yield _AgentStreamResult(agent.invoke({"messages": messages}))
            return
        result: object | None = None
        for item in stream(
            {"messages": messages},
            stream_mode=["messages", "values"],
        ):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            mode, payload = item
            if mode == "messages":
                delta = _stream_message_text(payload)
                if delta:
                    yield delta
            elif mode == "values":
                result = payload
        if result is None:
            raise RuntimeError("Deep Agent stream did not produce a final state.")
        yield _AgentStreamResult(result)

    def _build_output(
        self,
        spec: ExecutionSpec,
        runtime: EngineRuntimeContext,
        answer: str,
    ) -> EngineOutput:
        runtime.run_context.record_step(
            "deep_agent_completed",
            outputs={"answer": answer},
        )
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=answer,
            metadata={
                "objective": spec.objective,
            },
        )

    def _conversation_messages(
        self,
        query: UserQuery,
        *,
        current_text: str | None = None,
    ) -> list[dict[str, str]]:
        """Build chronological prior-turn messages plus the current request."""

        raw_history = query.metadata.get("history", [])
        messages: list[dict[str, str]] = []
        if isinstance(raw_history, list):
            for item in raw_history[-10:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role not in {"user", "assistant"}:
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                messages.append({"role": role, "content": content.strip()})
        messages.append(
            {
                "role": "user",
                "content": (
                    current_text if current_text is not None else query.text
                ).strip(),
            }
        )
        return messages

    def _synthesize_execution_answer(
        self,
        spec: ExecutionSpec,
        execution: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "objective": spec.objective,
                "execution_result": _execution_value(execution),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        response = self.llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Write a concise final answer using only the supplied "
                        "successful execution result. Do not invent facts."
                    )
                ),
                HumanMessage(content=payload[:16_000]),
            ]
        )
        return _message_text(response)

    def _register_minimal_profile(self) -> None:
        identifier = get_model_identifier(self.llm)
        provider = get_model_provider(self.llm)
        if not identifier:
            raise RuntimeError(
                "Deep Agent tool restriction requires a model with a resolvable "
                "model identifier."
            )
        if ":" in identifier:
            profile_key = identifier
        elif provider:
            profile_key = f"{provider}:{identifier}"
        else:
            raise RuntimeError(
                "Deep Agent tool restriction requires a model with a resolvable "
                "provider."
            )
        register_harness_profile(
            profile_key,
            HarnessProfile(
                excluded_tools=_HIDDEN_DEEP_AGENT_TOOLS,
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )

    def _system_prompt(
        self,
        spec: ExecutionSpec,
        runtime: EngineRuntimeContext,
        query: UserQuery,
    ) -> str:
        method_hub_enabled = runtime.has_mcp_tools
        method_hub_instructions = (
            "Method Hub is enabled. Call the matching Method Hub tool directly. "
            "Use execute_python only for local file inspection, calculations, "
            "or transformations that do not require Method Hub access. Never "
            "attempt HTTP or Method Hub access from generated code. For question answering "
            "over a specific document, prefer `corpus_retrieve_context` and "
            "answer from its returned chunks. For question answering across the "
            "indexed corpus, prefer `corpus_retrieve_context` and answer from "
            "its returned chunks. Use retrieval tools instead of previewing an "
            "entire dataset when only relevant context is needed.\n\n"
            if method_hub_enabled
            else (
                "Method Hub is disabled. Use execute_python when the request "
                "requires data inspection, calculation, or code execution, and "
                "assign its final JSON-serializable value to `result`. For a "
                "request that does not need tools, answer directly.\n\n"
            )
        )
        uploaded_files = _uploaded_file_names(query)
        uploaded_file_instructions = (
            "Uploaded files are staged in `/workspace`. Use these filenames "
            f"directly when reading files: {json.dumps(uploaded_files)}.\n\n"
            if uploaded_files
            else ""
        )
        selected_file_instructions = _selected_file_instructions(runtime)
        workspace_scope_instructions = _workspace_scope_instructions(runtime)
        workspace_skill_instructions = render_workspace_skills(runtime.workspace_skills)
        internal_memory_instructions = (
            "Internal memory is available. The USER.md and MEMORY.md sections "
            "below are a frozen snapshot for this request. Use `memory` only for "
            "durable, high-value facts: "
            "write stable user preferences to `user`, and durable agent/project "
            "knowledge to `memory`. Do not save transient task state, raw logs, "
            "or duplicate facts. `replace` and `remove` require the exact existing "
            "entry in `match`.\n\n"
            if runtime.internal_memory_client is not None
            else ""
        )
        rendered_internal_memory = runtime.internal_memory_context.render()
        internal_memory_context = (
            f"{rendered_internal_memory}\n\n" if rendered_internal_memory else ""
        )
        return (
            "You are the only analysis agent for this request. Use the "
            "available tools to answer the objective.\n\n"
            f"{method_hub_instructions}"
            f"{uploaded_file_instructions}"
            f"{workspace_scope_instructions}"
            f"{workspace_skill_instructions}"
            f"{selected_file_instructions}"
            f"{internal_memory_instructions}"
            f"{internal_memory_context}"
            "When using tools, base the final answer only on "
            "successful tool or sandbox output and never invent data. If an "
            "execution fails, inspect the structured error and correct the next "
            "attempt.\n\n"
            f"Objective: {spec.objective}\n"
            f"Constraints: {json.dumps(spec.constraints, default=str)}"
        )


def _message_text(message: object) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                text_parts.append(block)
        return "\n".join(text_parts)
    return "" if content is None else str(content)


def _stream_message_text(payload: object) -> str:
    """Extract visible text from a LangGraph message stream item."""

    if not isinstance(payload, tuple) or len(payload) != 2:
        return ""
    message = payload[0]
    if getattr(message, "type", None) not in {"AIMessage", "AIMessageChunk"}:
        return ""
    if any(
        getattr(message, attribute, None)
        for attribute in ("tool_calls", "tool_call_chunks", "invalid_tool_calls")
    ):
        return ""
    return _message_text(message)


def _uploaded_file_names(query: UserQuery) -> list[str]:
    raw_files = query.metadata.get("uploaded_files", [])
    if not isinstance(raw_files, list):
        return []
    names: list[str] = []
    for item in raw_files:
        filename = item.get("filename") if isinstance(item, dict) else item
        if not isinstance(filename, str):
            continue
        normalized = Path(filename).name
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def _selected_file_instructions(runtime: EngineRuntimeContext) -> str:
    scope = runtime.selected_files_scope
    if scope is None:
        return ""
    if not scope.document_ids:
        return (
            "No workspace files are selected for this run. Do not call workspace "
            "retrieval tools or ask the user for a local file path.\n\n"
        )
    return (
        "This run is limited to the selected workspace document IDs: "
        f"{json.dumps(list(scope.document_ids))}. Use the retrieval tools; the "
        "runtime automatically applies this document scope. Do not search the "
        "local filesystem for workspace files and do not ask the user for a local "
        "path.\n\n"
    )


def _workspace_scope_instructions(runtime: EngineRuntimeContext) -> str:
    if not runtime.workspace_id:
        return ""
    return (
        f"This run is scoped to workspace {runtime.workspace_id!r}. The runtime "
        "automatically adds workspace_id to each Method Hub call. Do not ask the "
        "user for a workspace_id or select another workspace.\n\n"
    )


def _last_message_text(result: object) -> str:
    if not isinstance(result, dict):
        return _message_text(result)
    messages = result.get("messages") or []
    if not messages:
        raise RuntimeError("Deep Agent returned no messages.")
    return _message_text(messages[-1])


def _retry_messages(result: object, objective: str) -> list[object]:
    previous = result.get("messages") if isinstance(result, dict) else None
    messages = list(previous) if isinstance(previous, list) else []
    if not messages:
        messages.append({"role": "user", "content": objective})
    messages.append(
        {
            "role": "user",
            "content": (
                "The previous attempt did not produce a usable answer. Use a "
                "direct Method Hub tool or execute_python when the request needs "
                "tools; otherwise answer directly. Return a non-empty final answer."
            ),
        }
    )
    return messages


def _latest_successful_grounding(
    runtime: EngineRuntimeContext,
) -> dict[str, Any] | None:
    for call in reversed(runtime.run_context.trace.method_calls):
        if call.status != "completed":
            continue
        if call.method_name == "execute_python":
            if call.outputs.get("success") is True:
                return call.outputs
            continue
        return {
            "success": True,
            "result": call.outputs.get("result", call.outputs),
            "stdout": "",
            "stderr": "",
            "method_name": call.method_name,
        }
    return None


def _execution_value(execution: dict[str, Any] | None) -> object | None:
    if execution is None:
        return None
    result = execution.get("result")
    if result is not None and result != "":
        return result
    stdout = str(execution.get("stdout") or "").strip()
    return stdout or None


def _render_execution_result(execution: dict[str, Any] | None) -> str:
    value = _execution_value(execution)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)
