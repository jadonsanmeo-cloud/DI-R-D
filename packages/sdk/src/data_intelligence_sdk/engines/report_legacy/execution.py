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

from data_intelligence_sdk.engines.report_legacy.base import _PromptAgent
from data_intelligence_sdk.engines.report_legacy.contracts import (
    AUTO_EXECUTION_CLASS,
    DETERMINISTIC_TRANSFORM_CLASS,
    EXECUTION_MODES,
    GENERATED_CODE_ROUTE,
    METHOD_HUB_ROUTE,
    ROUTE_KINDS,
    SEMANTIC_ANALYSIS_ROUTE,
    SEMANTIC_INFERENCE_CLASS,
    SOURCE_OPERATION_CLASS,
    ToolArgumentBinder,
    execution_class_for_step,
)
from data_intelligence_sdk.engines.report_legacy.corpus import (
    ingested_data_analysis_records,
    ingested_data_has_content,
    ingested_document_route,
    is_ingested_data_tool,
    unwrap_ingested_data_result,
)
from data_intelligence_sdk.engines.report_legacy.policies import (
    DEFAULT_SOURCE_MATERIALIZATION_REGISTRY,
    SourceMaterializationRegistry,
    normalize_content_role,
)
from data_intelligence_sdk.engines.report_legacy.prompts import (
    CODE_AGENT_PROMPT,
    DATASCIENCE_AGENT_PROMPT,
    ROUTER_AGENT_PROMPT,
    SEMANTIC_ANALYSIS_AGENT_PROMPT,
    VALIDATOR_AGENT_PROMPT,
)
from data_intelligence_sdk.engines.report_legacy.utils import (
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
        resolved_input_contracts: list[dict[str, Any]] | None = None,
        routing_feedback: list[str] | None = None,
        excluded_tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        runtime_context = (
            runtime if isinstance(runtime, EngineRuntimeContext) else None
        )
        method_hub = (
            _method_hub_payload(runtime_context)
            if runtime_context is not None
            else runtime
        )
        operation = step_request.get("operation", {})
        operation = operation if isinstance(operation, dict) else {}
        execution_mode = str(operation.get("execution_mode") or "auto").lower()
        execution_class = execution_class_for_step(step_request)
        if (
            execution_class == SEMANTIC_INFERENCE_CLASS
            or (
                execution_mode == SEMANTIC_ANALYSIS_ROUTE
                and execution_class not in {
                    DETERMINISTIC_TRANSFORM_CLASS,
                    SOURCE_OPERATION_CLASS,
                }
            )
        ):
            return {
                "route": SEMANTIC_ANALYSIS_ROUTE,
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "The validated PlanStep contract requires semantic "
                    "evidence analysis."
                ),
            }
        if (
            runtime_context is None
            and execution_mode == "generated_code"
            and execution_class != SOURCE_OPERATION_CLASS
        ):
            return {
                "route": GENERATED_CODE_ROUTE,
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "The validated PlanStep explicitly requires a deterministic "
                    "generated-code operation."
                ),
            }
        payload = (
            self._invoke_native_route(
                step_request,
                runtime_context,
                sources,
                resolved_input_contracts or [],
                routing_feedback or [],
                excluded_tool_names or [],
            )
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
            if payload.get("route") in ROUTE_KINDS:
                payload.setdefault("arguments", {})
                payload.setdefault("argument_bindings", {})
                payload.setdefault("reason", "Selected by Routing Agent.")
                payload = self._enforce_execution_class(
                    payload,
                    execution_class,
                )
                return self._normalize_route(
                    payload,
                    step_request,
                    method_hub,
                    sources,
                )
        return self._fallback_route(step_request, method_hub, sources)

    @staticmethod
    def _enforce_execution_class(
        route: dict[str, Any],
        execution_class: str,
    ) -> dict[str, Any]:
        """Reject a model route that contradicts the PlanStep contract."""

        route_kind = str(route.get("route") or "")
        if (
            execution_class == SEMANTIC_INFERENCE_CLASS
            and route_kind == GENERATED_CODE_ROUTE
        ):
            return {
                "route": SEMANTIC_ANALYSIS_ROUTE,
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "Generated code cannot satisfy a semantic-inference "
                    "contract; validated semantic execution takes precedence."
                ),
            }
        if (
            execution_class == SOURCE_OPERATION_CLASS
            and route_kind not in {METHOD_HUB_ROUTE, "unsupported"}
        ):
            return {
                "route": "unsupported",
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "A source operation requires a registered runtime capability; "
                    "it cannot be replaced by local inference or generated code."
                ),
            }
        return route

    def _invoke_native_route(
        self,
        step_request: dict[str, Any],
        runtime: EngineRuntimeContext,
        sources: list[str],
        resolved_input_contracts: list[dict[str, Any]],
        routing_feedback: list[str],
        excluded_tool_names: list[str],
    ) -> dict[str, Any] | None:
        if self.llm is None or not hasattr(self.llm, "bind_tools"):
            return None
        excluded = {str(name) for name in excluded_tool_names}
        tools = [
            tool
            for tool in create_mcp_tools(runtime)
            if str(getattr(tool, "name", "")) not in excluded
        ]
        if not tools:
            return None
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=self.system_prompt),
                    (
                        "user",
                        "step_request:\n{step_request}\n\n"
                        "available_sources:\n{available_sources}\n\n"
                        "resolved_input_contracts:\n{resolved_input_contracts}\n\n"
                        "routing_feedback:\n{routing_feedback}",
                    ),
                ]
            )
            response = self.llm.bind_tools(tools).invoke(
                prompt.invoke(
                    {
                        "step_request": _json_dumps(step_request),
                        "available_sources": _json_dumps(sources),
                        "resolved_input_contracts": _json_dumps(
                            resolved_input_contracts
                        ),
                        "routing_feedback": _json_dumps(routing_feedback),
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
                "argument_bindings": {},
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
        if route.get("route") != METHOD_HUB_ROUTE:
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
        route.setdefault("argument_bindings", {})
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
        required = {
            str(name) for name in parameters_schema.get("required", []) if str(name)
        }
        return required.issubset(arguments) and all(
            name not in arguments
            or cls._value_matches_schema(arguments[name], schema)
            for name, schema in properties.items()
        )

    @classmethod
    def _value_matches_schema(cls, value: Any, schema: Any) -> bool:
        return ToolArgumentBinder.value_matches_schema(value, schema)

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
        operation = step_request.get("operation", {})
        operation = operation if isinstance(operation, dict) else {}
        execution_mode = str(operation.get("execution_mode") or "auto").lower()
        if execution_mode not in EXECUTION_MODES:
            execution_mode = "auto"
        execution_class = execution_class_for_step(step_request)
        if (
            execution_class == SEMANTIC_INFERENCE_CLASS
            or (
                execution_class == AUTO_EXECUTION_CLASS
                and execution_mode == SEMANTIC_ANALYSIS_ROUTE
            )
        ):
            return {
                "route": SEMANTIC_ANALYSIS_ROUTE,
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": "The validated PlanStep requires semantic evidence analysis.",
            }
        if (
            execution_class == SOURCE_OPERATION_CLASS
            or (
                execution_class == AUTO_EXECUTION_CLASS
                and execution_mode == "method_hub"
            )
        ):
            return {
                "route": "unsupported",
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "The PlanStep requires Method Hub execution, but no registered "
                    "tool contract satisfies its capability."
                ),
            }
        if (
            execution_class == DETERMINISTIC_TRANSFORM_CLASS
            or (
                execution_class == AUTO_EXECUTION_CLASS
                and execution_mode == "generated_code"
            )
        ):
            return {
                "route": GENERATED_CODE_ROUTE,
                "tool_name": None,
                "arguments": {},
                "argument_bindings": {},
                "reason": (
                    "No registered Method Hub contract was selected; execute the "
                    "declared deterministic transform with validated generated code."
                ),
            }
        return {
            "route": "unsupported",
            "tool_name": None,
            "arguments": {},
            "argument_bindings": {},
            "reason": (
                "The auto execution contract could not be resolved without a "
                "validated model decision or compatible Method Hub capability."
            ),
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


class SemanticAnalysisAgent(_PromptAgent):
    """Execute semantic PlanSteps over the complete resolved evidence set."""

    def __init__(
        self,
        llm: object | None,
        *,
        max_batch_characters: int | None = None,
    ) -> None:
        super().__init__(
            "semantic_analysis_agent",
            SEMANTIC_ANALYSIS_AGENT_PROMPT,
            llm,
        )
        configured = max_batch_characters or _int_value(
            os.environ.get("REPORT_SEMANTIC_BATCH_CHARACTERS"),
            60_000,
        )
        self.max_batch_characters = max(4_000, configured)

    def run(
        self,
        step_request: dict[str, Any],
        resolved_inputs: list[dict[str, Any]],
        template_requirements: list[dict[str, Any]],
        user_goal: str,
        *,
        validation_feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        contracts = [
            {
                key: deepcopy(value)
                for key, value in item.items()
                if key not in {"value", "host_path"}
            }
            for item in resolved_inputs
            if isinstance(item, dict)
        ]
        batches = self._evidence_batches(resolved_inputs)
        if not batches:
            return {
                "status": "completed_no_data",
                "output": self._empty_output(step_request),
                "evidence_refs": [],
                "warnings": ["No resolved evidence was available."],
                "error": None,
                "batch_count": 0,
            }

        partials: list[Any] = []
        evidence_refs: list[str] = []
        warnings: list[str] = []
        for batch in batches:
            invocation = {
                "user_goal": user_goal,
                "step_request": step_request,
                "resolved_input_contracts": contracts,
                "evidence_batch": batch,
                "template_requirements": template_requirements,
                "partial_outputs": [],
                "analysis_stage": "extract",
                "validation_feedback": validation_feedback or [],
            }
            payload = self._invoke_json(**invocation)
            if payload is None:
                invocation["validation_feedback"] = [
                    *(validation_feedback or []),
                    (
                        "The prior response was empty or was not valid JSON. "
                        "Return only the exact semantic execution contract."
                    ),
                ]
                payload = self._invoke_json(**invocation)
            normalized = self._normalize_payload(payload, step_request)
            if normalized["status"] == "failed":
                return {
                    **normalized,
                    "batch_count": len(batches),
                }
            partials.append(normalized["output"])
            evidence_refs.extend(normalized["evidence_refs"])
            warnings.extend(normalized["warnings"])

        if len(partials) == 1:
            return {
                "status": "completed",
                "output": partials[0],
                "evidence_refs": list(dict.fromkeys(evidence_refs)),
                "warnings": list(dict.fromkeys(warnings)),
                "error": None,
                "batch_count": 1,
            }

        invocation = {
            "user_goal": user_goal,
            "step_request": step_request,
            "resolved_input_contracts": contracts,
            "evidence_batch": [],
            "template_requirements": template_requirements,
            "partial_outputs": partials,
            "analysis_stage": "consolidate",
            "validation_feedback": validation_feedback or [],
        }
        payload = self._invoke_json(**invocation)
        if payload is None:
            invocation["validation_feedback"] = [
                *(validation_feedback or []),
                (
                    "The prior response was empty or was not valid JSON. "
                    "Return only the exact semantic execution contract."
                ),
            ]
            payload = self._invoke_json(**invocation)
        normalized = self._normalize_payload(payload, step_request)
        normalized["evidence_refs"] = list(
            dict.fromkeys(evidence_refs + normalized["evidence_refs"])
        )
        normalized["warnings"] = list(
            dict.fromkeys(warnings + normalized["warnings"])
        )
        normalized["batch_count"] = len(batches)
        return normalized

    def _evidence_batches(
        self,
        resolved_inputs: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        evidence: list[dict[str, Any]] = []
        for binding in resolved_inputs:
            if not isinstance(binding, dict) or binding.get("value") is None:
                continue
            value = binding.get("value")
            values = value if isinstance(value, list) else [value]
            for item in values:
                evidence.append(
                    {
                        "input_ref": binding.get("ref"),
                        "artifact_ref": binding.get("artifact_ref"),
                        "semantic_roles": deepcopy(
                            binding.get("semantic_roles", [])
                        ),
                        "value": deepcopy(item),
                    }
                )
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_size = 0
        for item in evidence:
            item_size = len(
                json.dumps(item, ensure_ascii=False, default=str)
            )
            if current and current_size + item_size > self.max_batch_characters:
                batches.append(current)
                current = []
                current_size = 0
            current.append(item)
            current_size += item_size
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _normalize_payload(
        payload: Any,
        step_request: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(payload, list):
            return {
                "status": "completed",
                "output": payload,
                "evidence_refs": [],
                "warnings": [],
                "error": None,
            }
        if not isinstance(payload, dict):
            return {
                "status": "failed",
                "output": None,
                "evidence_refs": [],
                "warnings": [],
                "error": (
                    "SemanticAnalysisAgent did not return the structured "
                    "execution contract."
                ),
            }
        status = str(payload.get("status") or "completed").lower()
        if status not in {"completed", "completed_no_data", "failed"}:
            status = "failed"
        output = payload.get("output")
        declared = [
            item
            for item in step_request.get("outputs", [])
            if isinstance(item, dict) and item.get("name")
        ]
        if output is None and isinstance(payload.get("outputs"), dict):
            outputs = payload["outputs"]
            if len(declared) == 1:
                output = outputs.get(str(declared[0].get("name")))
            else:
                output = outputs
        if output is None and declared:
            declared_names = [str(item.get("name")) for item in declared]
            if len(declared_names) == 1 and declared_names[0] in payload:
                output = payload[declared_names[0]]
            elif all(name in payload for name in declared_names):
                output = {name: payload[name] for name in declared_names}
        if output is None and status == "completed_no_data":
            output = SemanticAnalysisAgent._empty_output(step_request)
        return {
            "status": status,
            "output": output,
            "evidence_refs": [
                str(item)
                for item in _list_value(payload.get("evidence_refs"))
                if str(item)
            ],
            "warnings": [
                str(item)
                for item in _list_value(payload.get("warnings"))
                if str(item)
            ],
            "error": (
                str(payload.get("error"))
                if payload.get("error") is not None
                else None
            ),
        }

    @staticmethod
    def _empty_output(step_request: dict[str, Any]) -> Any:
        outputs = [
            item
            for item in step_request.get("outputs", [])
            if isinstance(item, dict)
        ]
        if len(outputs) != 1:
            return {}
        shape = str(outputs[0].get("shape") or "table").lower()
        return (
            []
            if shape in {"array", "list", "table", "time_series", "category_series"}
            else {}
        )


class CodeAgent(_PromptAgent):
    _REQUIRED_SPEC_FIELDS = (
        "tool_name",
        "parameters_schema",
        "output_schema",
        "source_code",
        "execution_arguments",
    )

    def __init__(self, llm: object | None) -> None:
        super().__init__("code_agent", CODE_AGENT_PROMPT, llm)

    def run(
        self,
        step_request: dict[str, Any],
        schema_catalog: dict[str, Any],
        error_logs: str | None = None,
        validation_feedback: str | None = None,
    ) -> dict[str, Any]:
        response_text = self._invoke_text(
            step_request=step_request,
            schema_catalog=schema_catalog,
            error_logs=error_logs,
            validation_feedback=validation_feedback,
        )
        if response_text is None:
            return self._invalid_spec(
                step_request,
                "CodeAgent did not return a response.",
                "response_missing",
            )
        try:
            payload = _parse_json_payload(response_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._invalid_spec(
                step_request,
                f"CodeAgent response is not valid JSON: {type(exc).__name__}: {exc}",
                "response_parse_error",
            )
        if not isinstance(payload, dict):
            return self._invalid_spec(
                step_request,
                "CodeAgent response must be a JSON object.",
                "response_contract_error",
            )

        response_fields = sorted(str(key) for key in payload)
        missing_fields = [
            field for field in self._REQUIRED_SPEC_FIELDS if field not in payload
        ]
        if missing_fields:
            invalid = dict(payload)
            invalid.update(
                self._invalid_spec(
                    step_request,
                    "CodeAgent response is missing required fields: "
                    + ", ".join(missing_fields),
                    "response_contract_error",
                )
            )
            invalid["response_fields"] = response_fields
            return invalid

        normalized = dict(payload)
        normalized["source_code"] = _normalize_generated_source(
            str(payload.get("source_code") or "")
        )
        normalized["execution_arguments"] = self._normalize_execution_arguments(
            payload.get("execution_arguments"),
            payload.get("parameters_schema"),
            schema_catalog.get("sources", []),
        )
        normalized["response_fields"] = response_fields
        if not normalized["source_code"].strip():
            normalized["generation_error"] = (
                "CodeAgent returned an empty source_code field."
            )
            normalized["generation_error_kind"] = "response_contract_error"
        return normalized

    @classmethod
    def _invalid_spec(
        cls,
        step_request: dict[str, Any],
        message: str,
        error_kind: str,
    ) -> dict[str, Any]:
        return {
            "tool_name": cls._default_tool_name(step_request),
            "parameters_schema": {},
            "output_schema": {},
            "execution_arguments": {},
            "source_code": "",
            "response_fields": [],
            "generation_error": message,
            "generation_error_kind": error_kind,
        }

    @staticmethod
    def _default_tool_name(step_request: dict[str, Any]) -> str:
        step_id = _safe_id(step_request.get("step_id", "report_tool"))
        return f"generated_{step_id.replace('-', '_').replace('.', '_')}"

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
            status = str(payload.get("status") or "").strip().lower()
            if status in {"pass", "fail"}:
                return payload
        return {
            "status": "Fail",
            "feedback": (
                "ValidatorAgent did not return a valid Pass/Fail decision. "
                f"Sandbox evidence: {sandbox_logs}"
            ),
            "validated_code": None,
        }


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
            analysis_summary = self._analysis_summary(payload)
            required_block_ids = self._required_block_content_ids(
                template_requirements
            )
            supplied_block_ids = self._supplied_block_content_ids(payload)
            missing_block_ids = sorted(required_block_ids - supplied_block_ids)
            if self.llm is not None and (
                not analysis_summary or missing_block_ids
            ):
                missing_feedback = (
                    " The response also omitted content for these required, "
                    "run-local consumer block IDs: "
                    + ", ".join(missing_block_ids)
                    + ". Populate report_content.block_content using those exact "
                    "IDs and each supplied block's type, content_role, purpose, "
                    "and instructions."
                    if missing_block_ids
                    else ""
                )
                repaired = self._invoke_json(
                    user_goal=user_goal or step.get("description", ""),
                    step=step,
                    materialized_result=materialized_result,
                    upstream_step_results=upstream_step_results,
                    template_requirements=template_requirements,
                    validation_feedback=(
                        "The previous response omitted substantive report-facing "
                        "analysis. Return the complete JSON contract and ensure "
                        "analysis_summary or report_content.executive_summary "
                        "directly answers the confirmed objective."
                        + missing_feedback
                    ),
                )
                if isinstance(repaired, dict):
                    repaired_summary = self._analysis_summary(repaired)
                    repaired_blocks = self._supplied_block_content_ids(repaired)
                    if (
                        len(repaired_blocks & required_block_ids)
                        > len(supplied_block_ids & required_block_ids)
                        or (not analysis_summary and repaired_summary)
                    ):
                        payload = repaired
                        analysis_summary = repaired_summary
            if not analysis_summary:
                return self._fallback_analysis(
                    step,
                    materialized_result,
                    raw_data,
                    template_requirements,
                )
            payload.setdefault("status", "completed")
            payload["analysis_summary"] = analysis_summary
            payload.setdefault("observations", [])
            payload.setdefault("aggregated_data", {})
            payload.setdefault("report_content", {})
            payload.setdefault("chart_data", {})
            payload.setdefault("warnings", [])
            return payload
        return self._fallback_analysis(
            step,
            materialized_result,
            raw_data,
            template_requirements,
        )

    @staticmethod
    def _required_block_content_ids(
        template_requirements: list[dict[str, Any]],
    ) -> set[str]:
        processor_owned_types = {"chart", "profile", "kpi_group", "table"}
        return {
            str(block.get("block_id") or "").strip()
            for requirement in template_requirements
            for block in _list_value(requirement.get("consumer_blocks"))
            if isinstance(block, dict)
            and block.get("required")
            and str(block.get("type") or "") not in processor_owned_types
            and str(block.get("block_id") or "").strip()
        }

    @staticmethod
    def _supplied_block_content_ids(payload: dict[str, Any]) -> set[str]:
        report_content = payload.get("report_content")
        report_content = report_content if isinstance(report_content, dict) else {}
        block_content = report_content.get("block_content")
        block_content = block_content if isinstance(block_content, dict) else {}
        return {
            str(block_id).strip()
            for block_id, value in block_content.items()
            if str(block_id).strip()
            and isinstance(value, dict)
            and any(
                bool(value.get(field))
                for field in ("text", "items", "metrics", "rows")
            )
        }

    @staticmethod
    def _analysis_summary(payload: dict[str, Any]) -> str:
        summary = payload.get("analysis_summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        report_content = payload.get("report_content")
        if not isinstance(report_content, dict):
            return ""
        executive_summary = report_content.get("executive_summary")
        if isinstance(executive_summary, str):
            return executive_summary.strip()
        if isinstance(executive_summary, list):
            parts: list[str] = []
            for item in executive_summary:
                if isinstance(item, dict):
                    rendered = str(
                        item.get("statement")
                        or item.get("text")
                        or item.get("content")
                        or ""
                    ).strip()
                else:
                    rendered = str(item).strip()
                if rendered:
                    parts.append(rendered)
            return " ".join(parts)
        return ""

    def _fallback_analysis(
        self,
        step: dict[str, Any],
        materialized_result: dict[str, Any],
        raw_data: Any,
        template_requirements: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        records = self._fallback_records(step, raw_data)
        rows = [row for _, _, row in records]
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
                    "recommendations": [],
                    "limitations": [],
                },
                "chart_data": {},
                "warnings": [],
            }
        evidence_items = [
            item
            for output_name, role, row in records
            if (item := self._fallback_evidence_item(output_name, role, row))
        ]
        evidence_items = list(
            {
                re.sub(
                    r"\s+",
                    " ",
                    str(item.get("statement") or ""),
                ).casefold(): item
                for item in evidence_items
                if str(item.get("statement") or "").strip()
            }.values()
        )
        ordered_items = sorted(
            evidence_items,
            key=self._fallback_item_priority,
            reverse=True,
        )
        summary_parts = [
            ": ".join(
                value
                for value in (
                    str(item.get("title") or "").strip(),
                    str(item.get("statement") or "").strip(),
                )
                if value
            )
            for item in ordered_items[:3]
            if str(item.get("statement") or "").strip()
        ]
        summary = ". ".join(part.rstrip(". ") for part in summary_parts)
        if summary:
            summary = f"{summary}."
        if not summary:
            summary = (
                f"The materialized result contains {row_count} records relevant "
                "to the confirmed objective."
            )

        aggregated: dict[str, Any] = {"record_count": row_count}
        for key, values in self._numeric_values(rows).items():
            if values:
                aggregated[f"{key}_min"] = min(values)
                aggregated[f"{key}_max"] = max(values)
                aggregated[f"{key}_average"] = sum(values) / len(values)
        chart_requested = any(
            _list_value(requirement.get("consumer_chart_ids"))
            for requirement in template_requirements or []
            if isinstance(requirement, dict)
        )
        chart_data = (
            self._fallback_chart_data(records) if chart_requested else {}
        )
        return {
            "status": "completed",
            "analysis_summary": summary,
            "observations": [
                {
                    "statement": item.get("statement"),
                    "category": item.get("kind"),
                    "evidence_refs": [materialized_result.get("artifact_ref")],
                }
                for item in ordered_items
            ],
            "aggregated_data": aggregated,
            "report_content": {
                "executive_summary": summary,
                "key_findings": self._fallback_items_for_role(
                    evidence_items, "key_findings"
                ),
                "supporting_evidence": self._fallback_items_for_role(
                    evidence_items, "supporting_evidence"
                ),
                "implications": self._fallback_items_for_role(
                    evidence_items, "implication"
                ),
                "recommendations": self._fallback_items_for_role(
                    evidence_items, "recommendation"
                ),
                "limitations": self._fallback_items_for_role(
                    evidence_items, "limitation"
                ),
                "evidence_items": evidence_items,
                "block_content": {},
            },
            "chart_data": chart_data,
            "warnings": [],
        }

    @staticmethod
    def _fallback_records(
        step: dict[str, Any], raw_data: Any
    ) -> list[tuple[str, str, dict[str, Any]]]:
        outputs = [
            output
            for output in _list_value(step.get("outputs"))
            if isinstance(output, dict) and output.get("name")
        ]
        records: list[tuple[str, str, dict[str, Any]]] = []
        if isinstance(raw_data, dict) and any(
            str(output.get("name")) in raw_data for output in outputs
        ):
            for output in outputs:
                name = str(output.get("name"))
                roles = [
                    normalize_content_role(role)
                    for role in _list_value(output.get("semantic_roles"))
                ]
                role = next(
                    (
                        value
                        for value in roles
                        if value
                        in {
                            "key_findings",
                            "supporting_evidence",
                            "implication",
                            "recommendation",
                            "limitation",
                        }
                    ),
                    "supporting_evidence",
                )
                for row in _normalize_rows(raw_data.get(name)):
                    if isinstance(row, dict):
                        records.append((name, role, deepcopy(row)))
            return records
        role = "supporting_evidence"
        for row in _normalize_rows(raw_data):
            if isinstance(row, dict):
                records.append((str(step.get("step_id") or "result"), role, row))
        return records

    @staticmethod
    def _fallback_evidence_item(
        output_name: str,
        role: str,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        string_values = [
            (str(key), str(value).strip())
            for key, value in row.items()
            if isinstance(value, str) and value.strip()
        ]
        location_tokens = ("location", "page", "source_uri", "document_ref")
        locations = [
            value
            for key, value in string_values
            if any(token in key.casefold() for token in location_tokens)
        ]
        text_tokens = (
            "statement",
            "text",
            "summary",
            "description",
            "content",
            "evidence",
            "finding",
            "insight",
            "observation",
        )
        text_candidates = [
            (key, value)
            for key, value in string_values
            if any(token in key.casefold() for token in text_tokens)
            and value not in locations
        ]
        name_entry = next(
            (
                (key, value)
                for key, value in string_values
                if any(
                    token in key.casefold()
                    for token in ("name", "title", "category", "label")
                )
                and len(value) <= 160
            ),
            None,
        )
        value_entry = next(
            (
                (str(key), value)
                for key, value in row.items()
                if value is not None
                and not isinstance(value, (dict, list, tuple, set, bool))
                and "value" in str(key).casefold()
            ),
            None,
        )
        period_entry = next(
            (
                value
                for key, value in string_values
                if any(token in key.casefold() for token in ("period", "date", "time"))
            ),
            "",
        )
        context_entry = next(
            (
                value
                for key, value in string_values
                if "context" in key.casefold()
            ),
            "",
        )
        if name_entry and value_entry:
            title = name_entry[1]
            statement_parts = [str(value_entry[1])]
            if period_entry and period_entry.casefold() not in title.casefold():
                statement_parts.append(f"during {period_entry}")
            if context_entry:
                statement_parts.append(f"({context_entry})")
            statement = " ".join(statement_parts)
        elif text_candidates:
            _, statement = max(text_candidates, key=lambda item: len(item[1]))
            title = ""
        else:
            scalar_values = [
                (str(key), value)
                for key, value in row.items()
                if value is not None
                and not isinstance(value, (dict, list, tuple, set))
                and str(value).strip()
                and str(value).strip() not in locations
            ]
            statement = "; ".join(
                f"{DataScienceAgent._display_field(key)}: {value}"
                for key, value in scalar_values
            )
            title = ""
        statement = re.sub(r"\s+", " ", statement).strip()[:700]
        if not statement:
            return None
        title_tokens = ("title", "category", "name", "type", "label")
        if not title:
            title = next(
                (
                    value
                    for key, value in string_values
                    if any(token in key.casefold() for token in title_tokens)
                    and value != statement
                    and len(value) <= 120
                ),
                DataScienceAgent._display_field(output_name),
            )
        item = {
            "title": title,
            "statement": statement,
            "kind": role,
            "content_roles": [role],
        }
        if locations:
            item["source_location"] = locations[0]
        return item

    @staticmethod
    def _fallback_item_priority(item: dict[str, Any]) -> tuple[int, int, int]:
        title = str(item.get("title") or "").casefold()
        statement = str(item.get("statement") or "")
        analytical_tokens = (
            "total",
            "average",
            "change",
            "growth",
            "rate",
            "improvement",
            "reduction",
            "maximum",
            "minimum",
        )
        return (
            int("key_findings" in _list_value(item.get("content_roles"))),
            sum(token in title for token in analytical_tokens),
            int(bool(re.search(r"\d", statement))),
        )

    @staticmethod
    def _fallback_items_for_role(
        items: list[dict[str, Any]], role: str
    ) -> list[dict[str, Any]]:
        canonical = normalize_content_role(role)
        return [
            deepcopy(item)
            for item in items
            if canonical in _list_value(item.get("content_roles"))
        ]

    @staticmethod
    def _fallback_chart_data(
        records: list[tuple[str, str, dict[str, Any]]]
    ) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for output_name, _, row in records:
            grouped.setdefault(output_name, []).append(row)
        candidates = []
        for output_name, rows in grouped.items():
            if len(rows) < 2:
                continue
            fields = list(dict.fromkeys(key for row in rows for key in row))
            numeric_fields = [
                field
                for field in fields
                if sum(
                    isinstance(row.get(field), (int, float))
                    and not isinstance(row.get(field), bool)
                    for row in rows
                )
                >= max(2, int(len(rows) * 0.8))
            ]
            dimension = next(
                (
                    field
                    for field in fields
                    if field not in numeric_fields
                    and not any(
                        token in field.casefold()
                        for token in ("location", "ref", "text", "content", "description")
                    )
                    and len(
                        {
                            str(row.get(field))
                            for row in rows
                            if row.get(field) not in (None, "")
                        }
                    )
                    >= 2
                ),
                "",
            )
            if not dimension or not numeric_fields:
                continue
            long_form_name_value = (
                "name" in dimension.casefold()
                and any("value" in field.casefold() for field in numeric_fields)
                and any(
                    any(token in field.casefold() for token in ("period", "date", "time"))
                    for field in fields
                    if field != dimension
                )
            )
            if long_form_name_value:
                continue
            score = sum(
                row.get(field) is not None
                for row in rows
                for field in numeric_fields
            )
            candidates.append((score, output_name, rows, dimension, numeric_fields))
        if not candidates:
            return {}
        _, output_name, rows, dimension, measures = max(
            candidates, key=lambda item: item[0]
        )
        primary = measures[0]
        first = rows[0]
        last = rows[-1]
        dimension_label = DataScienceAgent._display_field(dimension)
        measure_label = DataScienceAgent._display_field(primary)
        claim = (
            f"Across {first.get(dimension)} to {last.get(dimension)}, "
            f"{measure_label} changed from {first.get(primary)} to {last.get(primary)}."
        )
        return {
            "render": True,
            "title": f"{measure_label} by {dimension_label}",
            "analytical_purpose": (
                f"Compare validated measures across {dimension_label}."
            ),
            "evidence_claim": claim,
            "recommended_types": ["line", "bar"],
            "encoding": {"dimension": dimension, "measures": measures},
            "measures": [
                {"field": field, "label": DataScienceAgent._display_field(field)}
                for field in measures
            ],
            "measure": measure_label,
            "coverage": f"{len(rows)} materialized records",
            "rows": rows,
            "source_output": output_name,
        }

    @staticmethod
    def _display_field(value: Any) -> str:
        rendered = re.sub(r"[_\-.]+", " ", str(value or "")).strip()
        return rendered[:1].upper() + rendered[1:] if rendered else "Evidence"

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
