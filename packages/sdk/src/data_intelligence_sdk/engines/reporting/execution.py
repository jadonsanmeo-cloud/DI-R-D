"""Internal report engine implementation module."""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import re
import threading
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineInput,
    EngineOutput,
    ExecutionSpec,
    InterfaceDefinition,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult
from data_intelligence_sdk.tools import create_mcp_tools

from data_intelligence_sdk.engines.reporting.base import _PromptAgent
from data_intelligence_sdk.engines.reporting.corpus import (
    ingested_data_analysis_records,
    ingested_data_has_content,
    ingested_document_route,
    is_ingested_data_tool,
    unwrap_ingested_data_result,
)
from data_intelligence_sdk.engines.reporting.policies import (
    DEFAULT_SOURCE_MATERIALIZATION_REGISTRY,
    SourceMaterializationRegistry,
)
from data_intelligence_sdk.engines.reporting.prompts import (
    CODE_AGENT_PROMPT,
    DATASCIENCE_AGENT_PROMPT,
    ROUTER_AGENT_PROMPT,
    VALIDATOR_AGENT_PROMPT,
)
from data_intelligence_sdk.engines.reporting.utils import (
    _extract_message_content,
    _first_source,
    _first_source_with_suffixes,
    _int_value,
    _json_dumps,
    _list_value,
    _method_hub_payload,
    _normalize_generated_source,
    _normalize_rows,
    _parse_json_payload,
    _safe_id,
)

class RouterAgent(_PromptAgent):
    def __init__(
        self,
        llm: object | None,
        source_registry: SourceMaterializationRegistry | None = None,
    ) -> None:
        super().__init__("router_agent", ROUTER_AGENT_PROMPT, llm)
        self.source_registry = (
            source_registry or DEFAULT_SOURCE_MATERIALIZATION_REGISTRY
        )

    def run(
        self,
        step_request: dict[str, Any],
        runtime: EngineRuntimeContext | list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        runtime_context = (
            runtime if isinstance(runtime, EngineRuntimeContext) else None
        )
        method_hub = (
            _method_hub_payload(runtime_context)
            if runtime_context is not None
            else runtime
        )
        payload = (
            self._invoke_native_route(step_request, runtime_context, sources)
            if runtime_context is not None
            else None
        )
        if isinstance(payload, dict):
            if "use_existing_tool" in payload:
                payload["route"] = (
                    "existing_tool"
                    if payload.get("use_existing_tool")
                    else "generate_tool"
                )
            if payload.get("route") in {
                "existing_tool",
                "generate_tool",
                "unsupported",
            }:
                payload.setdefault("arguments", {})
                payload.setdefault("reason", "Selected by Routing Agent.")
                return self._normalize_route(
                    payload,
                    step_request,
                    method_hub,
                    sources,
                )
        return self._fallback_route(step_request, method_hub, sources)

    def _invoke_native_route(
        self,
        step_request: dict[str, Any],
        runtime: EngineRuntimeContext,
        sources: list[str],
    ) -> dict[str, Any] | None:
        if self.llm is None or not hasattr(self.llm, "bind_tools"):
            return None
        tools = create_mcp_tools(runtime)
        if not tools:
            return None
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=self.system_prompt),
                    (
                        "user",
                        "step_request:\n{step_request}\n\n"
                        "available_sources:\n{available_sources}",
                    ),
                ]
            )
            response = self.llm.bind_tools(tools).invoke(
                prompt.invoke(
                    {
                        "step_request": _json_dumps(step_request),
                        "available_sources": _json_dumps(sources),
                    }
                )
            )
        except Exception:
            return None

        tool_calls = getattr(response, "tool_calls", None) or []
        if tool_calls:
            tool_call = tool_calls[0]
            if isinstance(tool_call, dict):
                tool_name = tool_call.get("name")
                arguments = tool_call.get("args", {})
            else:
                tool_name = getattr(tool_call, "name", None)
                arguments = getattr(tool_call, "args", {})
            return {
                "route": "existing_tool",
                "tool_name": str(tool_name or ""),
                "arguments": arguments if isinstance(arguments, dict) else {},
                "reason": "Selected through native MCP tool calling.",
            }

        text = _extract_message_content(response)
        try:
            payload = _parse_json_payload(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _normalize_route(
        self,
        route: dict[str, Any],
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        operation = step_request.get("operation", {})
        operation_kind = str(
            operation.get("kind") if isinstance(operation, dict) else operation or ""
        ).lower()
        registered = self._registered_route(
            step_request,
            method_hub,
            sources,
            operation_kind,
        )
        if registered is not None:
            return registered
        if (
            self.source_registry.resolve_source(sources, operation_kind) is not None
            or self.source_registry.resolve_operation(operation_kind) is not None
        ):
            return {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "No trusted MethodHub tool is registered for the source "
                    f"materialization capability required by {operation_kind!r}."
                ),
            }
        if route.get("route") != "existing_tool":
            return route
        tool_name = str(route.get("tool_name") or "")
        tool = next(
            (item for item in method_hub if item.get("tool_name") == tool_name),
            None,
        )
        if tool is None:
            return self._fallback_route(step_request, method_hub, sources)

        arguments = route.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        arguments = dict(arguments)
        parameters = tool.get("parameters_schema", {})
        properties = (
            parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        )
        if not self.arguments_match_schema(arguments, parameters):
            return {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "The selected MethodHub tool arguments do not satisfy its "
                    "parameter schema; generated code must handle this step."
                ),
            }
        content_parameters = {
            str(name)
            for name in properties
            if str(name) in {"content", "text"}
            or str(name).endswith(("_content", "_text"))
        }
        if any(
            self._is_source_reference(arguments.get(name), sources)
            for name in content_parameters
        ):
            return {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "An existing tool content parameter cannot be bound to a "
                    "local source path; generated code must read the staged source."
                ),
            }
        if sources and "path" in properties:
            arguments["path"] = self._allowed_source(
                arguments.get("path"),
                sources,
            )
        if sources and "data_root" in properties and "data_root" in arguments:
            arguments["data_root"] = str(Path(sources[0]).parent)
        route["arguments"] = arguments
        return route

    @classmethod
    def arguments_match_schema(
        cls,
        arguments: Any,
        parameters_schema: Any,
    ) -> bool:
        if not isinstance(arguments, dict) or not isinstance(parameters_schema, dict):
            return isinstance(arguments, dict)
        properties = parameters_schema.get("properties", {})
        if not isinstance(properties, dict):
            return True
        return all(
            name not in arguments
            or cls._value_matches_schema(arguments[name], schema)
            for name, schema in properties.items()
        )

    @classmethod
    def _value_matches_schema(cls, value: Any, schema: Any) -> bool:
        if not isinstance(schema, dict):
            return True
        alternatives = schema.get("anyOf") or schema.get("oneOf")
        if isinstance(alternatives, list):
            return any(
                cls._value_matches_schema(value, alternative)
                for alternative in alternatives
            )
        expected = schema.get("type")
        if isinstance(expected, list):
            return any(
                cls._value_matches_schema(value, {**schema, "type": item})
                for item in expected
            )
        checks = {
            "array": lambda item: isinstance(item, list),
            "boolean": lambda item: isinstance(item, bool),
            "integer": lambda item: (
                isinstance(item, int) and not isinstance(item, bool)
            ),
            "null": lambda item: item is None,
            "number": lambda item: (
                isinstance(item, (int, float)) and not isinstance(item, bool)
            ),
            "object": lambda item: isinstance(item, dict),
            "string": lambda item: isinstance(item, str),
        }
        return checks.get(str(expected), lambda _item: True)(value)

    @staticmethod
    def _allowed_source(value: Any, sources: list[str]) -> str:
        rendered = str(value or "")
        if rendered in sources:
            return rendered
        collapsed = rendered
        while "\\\\" in collapsed:
            collapsed = collapsed.replace("\\\\", "\\")
        if collapsed in sources:
            return collapsed
        requested_name = Path(collapsed).name.lower()
        for source in sources:
            if Path(source).name.lower() == requested_name:
                return source
        return sources[0]

    @staticmethod
    def _is_source_reference(value: Any, sources: list[str]) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        collapsed = value
        while "\\\\" in collapsed:
            collapsed = collapsed.replace("\\\\", "\\")
        if collapsed in sources:
            return True
        requested_name = Path(collapsed).name.lower()
        return any(
            requested_name == Path(source).name.lower()
            for source in sources
        )

    def _fallback_route(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        operation = step_request.get("operation", {})
        operation_kind = str(
            operation.get("kind") if isinstance(operation, dict) else operation or ""
        ).lower()
        registered = self._registered_route(
            step_request,
            method_hub,
            sources,
            operation_kind,
        )
        if registered is not None:
            return registered
        if (
            self.source_registry.resolve_source(sources, operation_kind) is not None
            or self.source_registry.resolve_operation(operation_kind) is not None
        ):
            return {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "No trusted MethodHub tool is registered for the source "
                    f"materialization capability required by {operation_kind!r}."
                ),
            }
        description = str(step_request.get("description", "")).lower()
        tables = {
            str(item).lower()
            for item in step_request.get("required_data", {}).get("tables", [])
        }
        incompatible_source_tools = {
            str(tool.get("tool_name"))
            for handler in self.source_registry.handlers
            if any(handler.matches_source(source) for source in sources)
            and not handler.accepts_operation(operation_kind)
            if (tool := self.source_registry.resolve_tool(method_hub, handler))
            is not None
        }
        for tool in method_hub:
            if str(tool.get("tool_name")) in incompatible_source_tools:
                continue
            haystack = " ".join(
                [
                    str(tool.get("tool_name", "")),
                    str(tool.get("description", "")),
                    " ".join(map(str, tool.get("capability_names", []))),
                ]
            ).lower()
            if any(table in haystack for table in tables) or any(
                word in haystack for word in description.split() if len(word) > 4
            ):
                return {
                    "route": "existing_tool",
                    "tool_name": tool["tool_name"],
                    "arguments": {},
                    "reason": "A MethodHub tool matches the step description or selected table.",
                }
        return {
            "route": "generate_tool",
            "tool_name": None,
            "arguments": {},
            "reason": "No existing MethodHub tool satisfies the PlanStep.",
        }

    def _registered_route(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
        operation_kind: str,
    ) -> dict[str, Any] | None:
        corpus_route = ingested_document_route(step_request, method_hub)
        if corpus_route is not None:
            return corpus_route
        resolved = self.source_registry.resolve_source(sources, operation_kind)
        handler = resolved[0] if resolved is not None else None
        source = resolved[1] if resolved is not None else None
        if handler is None:
            handler = self.source_registry.resolve_operation(operation_kind)
        if handler is None:
            return None
        tool = self.source_registry.resolve_tool(method_hub, handler)
        if tool is None:
            return None
        argument_value: str | None = source
        if argument_value is None:
            required_data = step_request.get("required_data", {})
            values = (
                required_data.get("documents", [])
                if handler.source_kind == "document"
                else required_data.get("vector_collections", [])
            )
            argument_value = str(values[0]) if values else None
        arguments = (
            {handler.argument_name: argument_value}
            if argument_value is not None
            else {}
        )
        return {
            "route": "existing_tool",
            "tool_name": tool["tool_name"],
            "capability_id": handler.capability_id,
            "arguments": arguments,
            "reason": (
                f"Resolved by source capability {handler.capability_id!r} "
                f"for operation {operation_kind!r}."
            ),
        }


class CodeAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("code_agent", CODE_AGENT_PROMPT, llm)

    def run(
        self,
        step_request: dict[str, Any],
        schema_catalog: dict[str, Any],
        error_logs: str | None = None,
        validation_feedback: str | None = None,
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_request=step_request,
            schema_catalog=schema_catalog,
            error_logs=error_logs,
            validation_feedback=validation_feedback,
        )
        if isinstance(payload, dict):
            payload.setdefault(
                "tool_name", f"generated_{_safe_id(step_request.get('step_id'))}"
            )
            payload.setdefault(
                "parameters_schema",
                {"type": "object", "properties": {}, "required": []},
            )
            payload.setdefault("output_schema", {"type": "array"})
            payload["source_code"] = _normalize_generated_source(
                payload.get("source_code", "")
            )
            payload["execution_arguments"] = self._normalize_execution_arguments(
                payload.get("execution_arguments"),
                payload.get("parameters_schema"),
                schema_catalog.get("sources", []),
            )
            return payload
        return {
            "tool_name": (
                f"generated_"
                f"{_safe_id(step_request.get('step_id', 'report_tool')).replace('-', '_')}"
            ),
            "parameters_schema": {"type": "object", "properties": {}, "required": []},
            "output_schema": {"type": "array"},
            "execution_arguments": {},
            "source_code": "",
            "generation_error": (
                "CodeAgent did not return a valid structured code specification."
            ),
        }

    @staticmethod
    def _normalize_execution_arguments(
        value: Any,
        parameters_schema: Any,
        sources: Any,
    ) -> dict[str, Any]:
        arguments = dict(value) if isinstance(value, dict) else {}
        allowed_sources = [str(item) for item in _list_value(sources) if item]

        def canonicalize(item: Any) -> Any:
            if isinstance(item, list):
                return [canonicalize(value) for value in item]
            if isinstance(item, tuple):
                return [canonicalize(value) for value in item]
            if isinstance(item, dict):
                return {key: canonicalize(value) for key, value in item.items()}
            if not isinstance(item, str):
                return item
            collapsed = item
            while "\\\\" in collapsed:
                collapsed = collapsed.replace("\\\\", "\\")
            for source in allowed_sources:
                if (
                    collapsed == source
                    or Path(collapsed).name.lower() == Path(source).name.lower()
                ):
                    return source
            return item

        arguments = {key: canonicalize(item) for key, item in arguments.items()}
        properties = (
            parameters_schema.get("properties", {})
            if isinstance(parameters_schema, dict)
            else {}
        )
        if allowed_sources and isinstance(properties, dict):
            path_parameters = [
                str(name)
                for name in properties
                if (
                    str(name)
                    in {
                        "file",
                        "file_path",
                        "input_file",
                        "input_path",
                        "path",
                        "source",
                        "source_file",
                        "source_path",
                    }
                    or str(name).endswith(("_file", "_path"))
                )
                and not str(name).startswith(("output_", "destination_"))
            ]
            path_list_parameters = [
                str(name)
                for name in properties
                if str(name) in {"files", "input_files", "paths", "sources"}
                or str(name).endswith(("_files", "_paths"))
            ]
            for name in path_parameters:
                value = arguments.get(name)
                rendered = str(value or "").strip().lower()
                placeholder = (
                    not rendered
                    or rendered
                    in {
                        "file",
                        "file_path",
                        "path",
                        "source",
                        "source_path",
                        "string",
                    }
                    or (rendered.startswith("<") and rendered.endswith(">"))
                )
                if placeholder:
                    arguments[name] = allowed_sources[0]
                elif len(allowed_sources) == 1 and value not in allowed_sources:
                    arguments[name] = allowed_sources[0]
            for name in path_list_parameters:
                value = arguments.get(name)
                if not isinstance(value, list) or not value:
                    arguments[name] = list(allowed_sources)
                    continue
                allowed = [
                    item
                    for item in value
                    if isinstance(item, str) and item in allowed_sources
                ]
                arguments[name] = allowed or list(allowed_sources)
        return arguments


class ValidatorAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("validator_agent", VALIDATOR_AGENT_PROMPT, llm)

    def run(
        self,
        step_description: str,
        source_code: str,
        sandbox_logs: str,
        sample_data: Any,
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_description=step_description,
            source_code=source_code,
            sandbox_logs=sandbox_logs,
            sample_data=sample_data,
        )
        if isinstance(payload, dict) and "status" in payload:
            return payload
        if "failed" in sandbox_logs.lower() or "error" in sandbox_logs.lower():
            return {"status": "Fail", "feedback": sandbox_logs, "validated_code": None}
        return {"status": "Pass", "feedback": None, "validated_code": source_code}


class DataScienceAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("datascience_agent", DATASCIENCE_AGENT_PROMPT, llm)

    def run(
        self,
        step: dict[str, Any],
        materialized_result: dict[str, Any],
        upstream_step_results: list[dict[str, Any]],
        template_requirements: list[dict[str, Any]],
        raw_data: Any,
        user_goal: str | None = None,
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            user_goal=user_goal or step.get("description", ""),
            step=step,
            materialized_result=materialized_result,
            upstream_step_results=upstream_step_results,
            template_requirements=template_requirements,
        )
        if isinstance(payload, dict):
            payload.setdefault("status", "completed")
            payload.setdefault("analysis_summary", "No analysis summary was produced.")
            payload.setdefault("observations", [])
            payload.setdefault("aggregated_data", {})
            payload.setdefault("report_content", {})
            payload.setdefault("chart_data", {})
            payload.setdefault("warnings", [])
            return payload
        return self._fallback_analysis(step, materialized_result, raw_data)

    def _fallback_analysis(
        self,
        step: dict[str, Any],
        materialized_result: dict[str, Any],
        raw_data: Any,
    ) -> dict[str, Any]:
        rows = _normalize_rows(raw_data)
        profile = materialized_result.get("profile", {})
        row_count = int(profile.get("row_count", len(rows)) or 0)
        if not rows:
            return {
                "status": "completed_no_data",
                "analysis_summary": "No data matched the confirmed scope and conditions.",
                "observations": [],
                "aggregated_data": {"record_count": row_count},
                "report_content": {
                    "executive_summary": (
                        "No data matched the confirmed scope and conditions."
                    ),
                    "key_findings": [],
                    "supporting_evidence": [],
                    "implications": [],
                    "limitations": [],
                },
                "chart_data": {},
                "warnings": [],
            }
        aggregated: dict[str, Any] = {"record_count": row_count}
        for key, values in self._numeric_values(rows).items():
            if values:
                aggregated[f"{key}_min"] = min(values)
                aggregated[f"{key}_max"] = max(values)
                aggregated[f"{key}_average"] = sum(values) / len(values)
        step_id = str(step.get("step_id", "step"))
        return {
            "status": "completed",
            "analysis_summary": (
                f"The step `{step_id}` processed {row_count} records for: "
                f"{step.get('description', '')}"
            ),
            "observations": [
                {
                    "observation_id": f"{step_id}-record-count",
                    "statement": f"The materialized result contains {row_count} records.",
                    "evidence_refs": [materialized_result.get("artifact_ref")],
                }
            ],
            "aggregated_data": aggregated,
            "report_content": {
                "executive_summary": (
                    f"The analysis processed {row_count} records for "
                    f"{step.get('description', '')}."
                ),
                "key_findings": [],
                "supporting_evidence": [],
                "implications": [],
                "limitations": [],
            },
            "chart_data": {},
            "warnings": [],
        }

    def _numeric_values(self, rows: list[Any]) -> dict[str, list[float]]:
        values: dict[str, list[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.setdefault(str(key), []).append(float(value))
        return values

class ToolExecutor:
    def execute_existing(
        self,
        route: dict[str, Any],
        runtime: EngineRuntimeContext,
    ) -> dict[str, Any]:
        tool_name = str(route.get("tool_name"))
        arguments = route.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            if runtime.mcp_client is None:
                raise RuntimeError("Method Hub MCP client is unavailable.")
            result = runtime.mcp_client.call_tool(tool_name, arguments)
            raw_result = result
            if is_ingested_data_tool(tool_name):
                payload = unwrap_ingested_data_result(result)
                if payload.get("error"):
                    raise RuntimeError(
                        str(payload.get("message") or payload.get("error"))
                    )
                raw_result = ingested_data_analysis_records(payload)
                status = (
                    "completed"
                    if ingested_data_has_content(payload)
                    else "completed_no_data"
                )
                recorded_outputs = {
                    "result_summary": {
                        "record_count": len(raw_result),
                        "content_count": len(payload.get("contents", [])),
                        "chunk_count": len(payload.get("chunks", [])),
                        "type": "ingested_document",
                    },
                    "provider": "mcp",
                }
            else:
                status = (
                    "completed_no_data"
                    if not _normalize_rows(result)
                    else "completed"
                )
                recorded_outputs = {
                    "result": result,
                    "result_summary": self._result_summary(result),
                    "provider": "mcp",
                }
            runtime.run_context.record_method_call(
                tool_name,
                status="completed",
                inputs=arguments,
                outputs=recorded_outputs,
            )
            return {
                "schema_version": "1.0",
                "status": status,
                "tool_name": tool_name,
                "arguments": arguments,
                "raw_result": raw_result,
                "error": None,
            }
        except Exception as exc:
            runtime.run_context.record_method_call(
                tool_name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc), "provider": "mcp"},
            )
            return {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": tool_name,
                "arguments": arguments,
                "raw_result": None,
                "error": str(exc),
            }

    def execute_generated(
        self,
        interface: InterfaceDefinition,
        code_spec: dict[str, Any],
        runtime: EngineRuntimeContext,
        sandbox_result: SandboxRunResult,
    ) -> dict[str, Any]:
        interface.trust_level = "generated_validated"
        if runtime.interface_registry is not None:
            runtime.interface_registry.register(interface)
        arguments = code_spec.get("execution_arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        if sandbox_result.status != "completed":
            runtime.run_context.record_method_call(
                interface.name,
                status="failed",
                inputs=arguments,
                outputs={"error": sandbox_result.error},
            )
            return {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": interface.name,
                "arguments": arguments,
                "raw_result": None,
                "error": sandbox_result.error or "Generated code execution failed.",
            }
        result = sandbox_result.result
        status = "completed_no_data" if not _normalize_rows(result) else "completed"
        runtime.run_context.record_method_call(
            interface.name,
            status="completed",
            inputs=arguments,
            outputs={"result_summary": self._result_summary(result)},
        )
        return {
            "schema_version": "1.0",
            "status": status,
            "tool_name": interface.name,
            "arguments": arguments,
            "raw_result": result,
            "error": None,
        }

    def _result_summary(self, result: Any) -> dict[str, Any]:
        rows = _normalize_rows(result)
        return {"row_count": len(rows), "type": type(result).__name__}
