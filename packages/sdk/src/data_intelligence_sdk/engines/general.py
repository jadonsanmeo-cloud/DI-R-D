"""General-purpose data analysis through one request-scoped Deep Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from langchain_core.messages import HumanMessage, SystemMessage

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.tools import create_execute_python_tool

AgentFactory = Callable[..., object]

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


class GeneralPurposeEngine:
    """Analyze staged data with one Deep Agent and one execution tool."""

    name = "general_purpose"
    description = (
        "General-purpose agent for exploratory data analysis, code execution, "
        "question answering, and tasks that do not require a structured report."
    )

    def __init__(
        self,
        llm: object | None = None,
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
    ) -> object:
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

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.intent in {
            "reason",
            "general",
            "unknown",
        }

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del user_context
        if runtime.sandbox is None:
            raise RuntimeError(
                "GeneralPurposeEngine requires a request-scoped sandbox."
            )

        runtime.run_context.record_step(
            "deep_agent_started",
            inputs={
                "objective": spec.objective,
                "source_paths": runtime.sandbox.source_paths,
            },
        )
        execute_python = create_execute_python_tool(runtime)
        self._register_minimal_profile()
        agent = self.agent_factory(
            model=self.llm,
            tools=[execute_python],
            system_prompt=self._system_prompt(spec, corpus_package, runtime),
            backend=runtime.sandbox.backend,
            subagents=[],
            name="axiom-general-analysis",
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
        execution = _latest_successful_execution(runtime)
        if not answer or execution is None:
            runtime.run_context.record_step(
                "deep_agent_retry_started",
                inputs={
                    "blank_answer": not bool(answer),
                    "missing_successful_execution": execution is None,
                },
            )
            result = agent.invoke(
                {
                    "messages": _retry_messages(result, spec.objective),
                }
            )
            answer = _last_message_text(result).strip()
            execution = _latest_successful_execution(runtime)
            runtime.run_context.record_step(
                "deep_agent_retry_completed",
                outputs={
                    "has_answer": bool(answer),
                    "has_successful_execution": execution is not None,
                },
            )
        if execution is None:
            raise RuntimeError(
                "GeneralPurposeEngine did not produce a successful "
                "execute_python result."
            )
        if not answer:
            runtime.run_context.record_step(
                "deep_agent_fallback_synthesis",
                inputs={"objective": spec.objective},
            )
            answer = self._synthesize_execution_answer(spec, execution).strip()
        if not answer:
            answer = _render_execution_result(execution).strip()
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
                "sources": list(corpus_package.sources),
                "sandbox_sources": dict(runtime.sandbox.source_paths),
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
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str:
        if runtime.sandbox is None:
            raise RuntimeError("The request sandbox is unavailable.")
        source_payload = [
            {
                "original": source,
                "sandbox_path": runtime.sandbox.source_paths.get(source),
            }
            for source in corpus_package.sources
        ]
        return (
            "You are the only analysis agent for this request. Use the staged data "
            "to answer the objective.\n\n"
            "You have exactly one action: execute_python. Pass a complete Python "
            "analysis program in its code argument. The runtime persists every "
            "attempt as an artifact before execution. The program must read only "
            "the staged sandbox paths listed below, perform the analysis, and "
            "assign its final JSON-serializable value to a top-level variable "
            "named result. Always call execute_python. If execution fails, use "
            "stderr to correct the next code attempt. Base the final answer only "
            "on successful execution output; never invent data.\n\n"
            f"Objective: {spec.objective}\n"
            f"Constraints: {json.dumps(spec.constraints, default=str)}\n"
            "Staged sources:\n"
            f"{json.dumps(source_payload, indent=2, default=str)}"
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
                "The previous attempt did not produce a usable grounded answer. "
                "Call execute_python with a complete analysis of the staged data, "
                "then return a non-empty final answer based only on the successful "
                "execution result."
            ),
        }
    )
    return messages


def _latest_successful_execution(
    runtime: EngineRuntimeContext,
) -> dict[str, Any] | None:
    for call in reversed(runtime.run_context.trace.method_calls):
        if (
            call.method_name == "execute_python"
            and call.status == "completed"
            and call.outputs.get("success") is True
        ):
            return call.outputs
    return None


def _execution_value(execution: dict[str, Any]) -> object | None:
    result = execution.get("result")
    if result is not None and result != "":
        return result
    stdout = str(execution.get("stdout") or "").strip()
    return stdout or None


def _render_execution_result(execution: dict[str, Any]) -> str:
    value = _execution_value(execution)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)
