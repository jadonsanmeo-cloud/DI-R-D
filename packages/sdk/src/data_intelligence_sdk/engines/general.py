"""General-purpose data analysis through one request-scoped Deep Agent."""

from __future__ import annotations

import json
import os
from builtins import BaseExceptionGroup
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
from data_intelligence_sdk.tools import (
    create_execute_python_tool,
    create_mcp_tools,
)


class AgentInvoker(Protocol):
    def invoke(self, payload: dict[str, Any]) -> Any: ...


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

        runtime.run_context.record_step(
            "deep_agent_started",
            inputs={
                "objective": spec.objective,
            },
        )
        execute_python = create_execute_python_tool(runtime)
        mcp_tools = create_mcp_tools(runtime)
        self._register_minimal_profile()
        agent = self.agent_factory(
            model=self.llm,
            tools=[*mcp_tools, execute_python],
            middleware=[_recover_tool_errors],
            system_prompt=self._system_prompt(spec, runtime, input.query),
            backend=DeepAgentSandboxBackend(runtime.sandbox),
            subagents=[],
            name="general-purpose",
        )
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": spec.objective,
                    }
                ]
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
            "Method Hub is enabled. For one tool operation, call the matching "
            "Method Hub tool directly. For multiple tool calls or any "
            "transformation that combines or modifies tool results, call "
            "execute_python and import `call_tool` from `axiom_method_hub`; "
            "assign the final JSON-serializable value to `result`. Never "
            "attempt HTTP access from generated code. For question answering "
            "over a specific document, prefer `document_retrieve_context` and "
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
        return (
            "You are the only analysis agent for this request. Use the "
            "available tools to answer the objective.\n\n"
            f"{method_hub_instructions}"
            f"{uploaded_file_instructions}"
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
