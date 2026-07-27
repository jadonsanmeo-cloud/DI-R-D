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

def _json_dumps(value: Any) -> str:
    return json.dumps(_to_jsonable(value), ensure_ascii=False, indent=2, default=str)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _extract_message_content(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    if isinstance(response, dict):
        if "content" in response:
            return str(response["content"])
        if "output" in response:
            return str(response["output"])
        messages = response.get("messages")
        if messages:
            return _extract_message_content(messages[-1])
    return str(response)


def _parse_json_payload(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    return json.loads(stripped)


def _normalize_generated_source(value: Any) -> str:
    source = str(value or "").strip()
    fenced = re.fullmatch(r"```(?:python)?\s*(.*?)```", source, flags=re.DOTALL)
    if fenced:
        source = fenced.group(1).strip()
    try:
        ast.parse(source)
        return source
    except SyntaxError:
        pass

    # Some models double-escape the complete JSON source string. Only decode this
    # shape when the payload has no real line structure, preserving valid "\n"
    # string literals in ordinary generated Python.
    if "\\n" in source and source.count("\n") <= 1:
        repaired = (
            source.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\'", "'")
        )
        try:
            ast.parse(repaired)
            return repaired
        except SyntaxError:
            pass
    return source


def _safe_id(value: Any) -> str:
    rendered = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-.")
    return rendered.lower() or "item"


def _python_argument_name(value: Any) -> str:
    rendered = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_")
    if not rendered:
        return "input_data"
    if rendered[0].isdigit():
        return f"input_{rendered}"
    return rendered


def _json_structure(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, list):
        return {
            "type": "array",
            "item": (_json_structure(value[0], depth + 1) if value else "unknown"),
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "fields": {
                str(key): _json_structure(item, depth + 1)
                for key, item in list(value.items())[:20]
            },
        }
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _int_value(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.fullmatch(r"[vV](\d+)", str(value).strip())
        return int(match.group(1)) if match else default


def _normalize_plan_inputs(value: Any) -> list[dict[str, Any]]:
    inputs = []
    for item in _list_value(value):
        if isinstance(item, dict):
            normalized = dict(item)
            normalized["ref"] = str(
                normalized.get("ref")
                or normalized.get("step_output_ref")
                or normalized.get("artifact_ref")
                or normalized.get("source_path")
                or normalized.get("source")
                or ""
            )
            if not normalized.get("name"):
                reserved = {
                    "artifact_ref",
                    "kind",
                    "ref",
                    "required",
                    "source",
                    "source_path",
                    "step_output_ref",
                    "type",
                }
                custom_keys = [key for key in normalized if key not in reserved]
                if len(custom_keys) == 1:
                    normalized["name"] = str(custom_keys[0])
            normalized.setdefault("kind", "data_reference")
            normalized.setdefault("required", True)
            inputs.append(normalized)
        elif item is not None and str(item).strip():
            inputs.append(
                {
                    "ref": str(item),
                    "kind": "data_reference",
                    "required": True,
                }
            )
    return inputs


def _normalize_plan_outputs(value: Any, step_id: str) -> list[dict[str, Any]]:
    outputs = []
    for index, item in enumerate(_list_value(value), start=1):
        if isinstance(item, dict):
            normalized = dict(item)
            normalized["name"] = str(
                normalized.get("name") or f"{step_id}-result-{index}"
            )
            normalized.setdefault("shape", "table")
            normalized.setdefault("semantic_roles", ["analysis_data"])
            normalized.setdefault("consumer_hints", ["analysis", "report"])
            outputs.append(normalized)
        elif item is not None and str(item).strip():
            outputs.append(
                {
                    "name": str(item),
                    "shape": "table",
                    "semantic_roles": ["analysis_data"],
                    "consumer_hints": ["analysis", "report"],
                }
            )
    return outputs or [
        {
            "name": f"{step_id}-result",
            "shape": "table",
            "semantic_roles": ["analysis_data"],
            "consumer_hints": ["analysis", "report"],
        }
    ]


def _semantic_role_groups(requirement: dict[str, Any]) -> list[set[str]]:
    semantic = requirement.get("semantic_roles", {})
    if not isinstance(semantic, dict):
        roles = {str(item) for item in _list_value(semantic) if str(item)}
        return [roles] if roles else []
    return [
        {str(item) for item in _list_value(semantic.get(group)) if str(item)}
        for group in ("measures", "dimensions", "time_dimensions")
        if _list_value(semantic.get(group))
    ]


def _shape_compatible(expected: Any, actual: Any) -> bool:
    expected_shape = str(expected or "").strip()
    actual_shape = str(actual or "").strip()
    if not expected_shape:
        return True
    if expected_shape == actual_shape:
        return True
    compatible_aliases = {
        "scalar": {"record"},
        "record": {"scalar"},
        "table": {"time_series", "category_series"},
    }
    return actual_shape in compatible_aliases.get(expected_shape, set())


def _compatible_plan_outputs(
    requirement: dict[str, Any],
    outputs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    expected = requirement.get("expected_output", {})
    expected_shape = expected.get("shape") if isinstance(expected, dict) else None
    candidates = [
        (step, output)
        for step, output in outputs
        if _shape_compatible(expected_shape, output.get("shape"))
    ]
    groups = _semantic_role_groups(requirement)
    if not groups:
        return candidates[:1]

    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    remaining = list(groups)
    available = list(candidates)
    while remaining and available:
        scored = []
        for candidate in available:
            output = candidate[1]
            capabilities = {
                str(item)
                for item in (
                    _list_value(output.get("semantic_roles"))
                    + _list_value(output.get("fields"))
                )
                if str(item)
            }
            covered = [group for group in remaining if group & capabilities]
            scored.append((len(covered), candidate, covered))
        score, candidate, covered = max(
            scored,
            key=lambda item: (
                item[0],
                -outputs.index(item[1]),
            ),
        )
        if score == 0:
            break
        selected.append(candidate)
        remaining = [group for group in remaining if group not in covered]
        available.remove(candidate)
    return selected if not remaining else []


def _negotiation_hash(
    plan: dict[str, Any],
    proposal: dict[str, Any],
) -> str:
    instance = proposal.get("template_instance", {})
    payload = {
        "steps": plan.get("steps", []),
        "request_resolutions": plan.get("request_resolutions", []),
        "template_id": instance.get("template_id"),
        "template_version": instance.get("template_version"),
        "bindings": instance.get("bindings", []),
        "missing_data_requests": proposal.get("missing_data_requests", []),
        "applied_fallbacks": instance.get("applied_fallbacks", []),
    }
    canonical = json.dumps(
        _to_jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_STEP_OUTPUT_REF = re.compile(r"^step-output://([^/]+)/(.+)$")


def _step_id_from_input_ref(value: Any) -> str | None:
    rendered = str(value or "")
    match = _STEP_OUTPUT_REF.match(rendered)
    if match:
        return match.group(1)
    if rendered.startswith("step://"):
        return rendered.removeprefix("step://").split("/", 1)[0]
    return rendered or None


def _bind_dependency_inputs(steps: list[dict[str, Any]]) -> None:
    steps_by_id = {str(step.get("step_id")): step for step in steps}
    for step in steps:
        inputs = _normalize_plan_inputs(step.get("inputs"))
        for dependency in map(str, step.get("depends_on", [])):
            upstream = steps_by_id.get(dependency, {})
            outputs = upstream.get("outputs", [])
            output_name = str(
                outputs[0].get("name")
                if outputs and isinstance(outputs[0], dict)
                else f"{dependency}-result"
            )
            existing_binding = next(
                (
                    item
                    for item in inputs
                    if _step_id_from_input_ref(item.get("ref")) == dependency
                    or str(item.get("ref", "")) == output_name
                ),
                None,
            )
            if existing_binding is not None:
                existing_binding.setdefault("name", output_name)
                existing_binding["ref"] = f"step-output://{dependency}/{output_name}"
                continue
            empty_input = next(
                (item for item in inputs if not str(item.get("ref", "")).strip()),
                None,
            )
            binding = empty_input if empty_input is not None else {}
            binding.setdefault("name", output_name)
            binding.update(
                {
                    "ref": f"step-output://{dependency}/{output_name}",
                    "kind": "data_reference",
                    "required": bool(binding.get("required", True)),
                }
            )
            if empty_input is None:
                inputs.append(binding)
        step["inputs"] = inputs


@dataclass(frozen=True, slots=True)
class _StepOutputRecord:
    step_id: str
    output_name: str
    value: Any
    artifact_ref: str
    host_path: str | None = None
    sandbox_path: str | None = None
    schema: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    json_type: str = "null"


class _StepOutputRegistry:
    """Request-local raw output registry used only for downstream execution."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], _StepOutputRecord] = {}
        self._primary_outputs: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(
        self,
        step: dict[str, Any],
        raw_data: Any,
        runtime: EngineRuntimeContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        step_id = str(step.get("step_id", "step"))
        outputs = _normalize_plan_outputs(step.get("outputs"), step_id)
        descriptors = []
        warnings = []
        for output in outputs:
            output_name = str(output.get("name"))
            value = self._output_value(raw_data, output_name, output)
            value_rows = _normalize_rows(value)
            schema = _infer_schema(value_rows)
            profile = _profile_rows(value_rows, value)
            json_type = (
                "array"
                if isinstance(value, list)
                else ("object" if isinstance(value, dict) else type(value).__name__)
            )
            artifact_ref = (
                f"memory://report/{_safe_id(step_id)}/{_safe_id(output_name)}"
            )
            host_path = None
            if runtime.run_artifact is not None:
                try:
                    artifact = runtime.run_artifact.record_data_output(
                        step_id,
                        output_name,
                        value,
                    )
                    artifact_ref = artifact.artifact_ref
                    host_path = str(artifact.path)
                except Exception as exc:
                    warnings.append(
                        f"Could not persist output {step_id}/{output_name}: {exc}"
                    )

            sandbox_path = None
            if runtime.sandbox is not None:
                relative_path = (
                    f"intermediate/{_safe_id(step_id)}/{_safe_id(output_name)}.json"
                )
                try:
                    runtime.sandbox.write(
                        relative_path,
                        json.dumps(value, ensure_ascii=False, default=str),
                    )
                    sandbox_path = f"/workspace/{relative_path}"
                except Exception as exc:
                    warnings.append(
                        f"Could not stage output {step_id}/{output_name}: {exc}"
                    )

            record = _StepOutputRecord(
                step_id=step_id,
                output_name=output_name,
                value=value,
                artifact_ref=artifact_ref,
                host_path=host_path,
                sandbox_path=sandbox_path,
                schema=schema,
                profile=profile,
                json_type=json_type,
            )
            with self._lock:
                self._records[(step_id, output_name)] = record
                self._primary_outputs.setdefault(step_id, output_name)
            descriptors.append(
                {
                    "output_name": output_name,
                    "artifact_ref": artifact_ref,
                    "shape": output.get("shape", "table"),
                    "schema": schema,
                    "profile": profile,
                    "json_type": json_type,
                }
            )
        return descriptors, warnings

    @staticmethod
    def _output_value(
        raw_data: Any,
        output_name: str,
        output: dict[str, Any],
    ) -> Any:
        if isinstance(raw_data, dict) and output_name in raw_data:
            return raw_data[output_name]
        shape = str(output.get("shape", "")).lower()
        if shape in {"array", "list", "table", "time_series"}:
            if isinstance(raw_data, dict) and len(raw_data) == 1:
                only_value = next(iter(raw_data.values()))
                if isinstance(only_value, list):
                    return only_value
            return _normalize_rows(raw_data)
        return raw_data

    def resolve(
        self,
        ref: str | None = None,
        *,
        step_id: str | None = None,
    ) -> _StepOutputRecord | None:
        output_name = None
        if ref:
            match = _STEP_OUTPUT_REF.match(ref)
            if match:
                step_id, output_name = match.groups()
            elif ref.startswith("step://"):
                step_id = ref.removeprefix("step://").split("/", 1)[0]
            elif step_id is None:
                step_id = ref
        if not step_id:
            return None
        with self._lock:
            if output_name is None:
                output_name = self._primary_outputs.get(step_id)
            return self._records.get((step_id, str(output_name)))


class _StepInputResolver:
    def resolve(
        self,
        step: dict[str, Any],
        registry: _StepOutputRegistry,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        inputs = _normalize_plan_inputs(step.get("inputs"))
        dependencies = list(map(str, step.get("depends_on", [])))
        resolved = []
        missing = []
        claimed_dependencies: set[str] = set()
        for item in inputs:
            ref = str(item.get("ref", ""))
            dependency = _step_id_from_input_ref(ref)
            if dependency not in dependencies:
                continue
            claimed_dependencies.add(dependency)
            record = registry.resolve(ref, step_id=dependency)
            if record is None:
                if item.get("required", True):
                    missing.append(ref or dependency)
                continue
            resolved.append(self._binding(item, record))
        for dependency in dependencies:
            if dependency in claimed_dependencies:
                continue
            record = registry.resolve(step_id=dependency)
            if record is None:
                missing.append(dependency)
                continue
            resolved.append(self._binding({}, record))
        return resolved, missing

    def merge_arguments(
        self,
        arguments: Any,
        parameters_schema: Any,
        resolved_inputs: list[dict[str, Any]],
        *,
        sandbox: bool,
    ) -> dict[str, Any]:
        merged = dict(arguments) if isinstance(arguments, dict) else {}
        properties = (
            parameters_schema.get("properties", {})
            if isinstance(parameters_schema, dict)
            else {}
        )
        if not isinstance(properties, dict):
            properties = {}
        assigned: set[str] = set()
        for binding in resolved_inputs:
            raw_base_names = [
                str(binding.get("argument_name") or ""),
                str(binding.get("output_name") or ""),
                str(binding.get("source_step_id") or ""),
            ]
            base_names = [
                name
                for raw_name in raw_base_names
                if raw_name
                for name in (raw_name, _python_argument_name(raw_name))
            ]
            candidates = list(dict.fromkeys(base_names))
            candidates.extend(
                f"{name}_path"
                for name in list(candidates)
                if not name.endswith("_path")
            )
            parameter_names = [
                name
                for name in dict.fromkeys(candidates)
                if name in properties and name not in assigned
            ]
            if not parameter_names:
                available = [str(name) for name in properties if name not in assigned]
                if len(resolved_inputs) == 1 and len(available) == 1:
                    parameter_names = [available[0]]
            if not parameter_names:
                continue
            for parameter_name in parameter_names:
                merged[parameter_name] = self._argument_value(
                    binding,
                    parameter_name,
                    sandbox=sandbox,
                )
                assigned.add(parameter_name)
        return merged

    @staticmethod
    def contract_payload(resolved_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key not in {"value", "host_path"}
                },
                "structure": _json_structure(item.get("value")),
            }
            for item in resolved_inputs
        ]

    @staticmethod
    def _binding(
        item: dict[str, Any],
        record: _StepOutputRecord,
    ) -> dict[str, Any]:
        return {
            "argument_name": _python_argument_name(
                item.get("name") or item.get("argument_name") or record.output_name
            ),
            "ref": f"step-output://{record.step_id}/{record.output_name}",
            "required": bool(item.get("required", True)),
            "source_step_id": record.step_id,
            "output_name": record.output_name,
            "artifact_ref": record.artifact_ref,
            "host_path": record.host_path,
            "sandbox_path": record.sandbox_path,
            "schema": record.schema or {},
            "profile": record.profile or {},
            "json_type": record.json_type,
            "value": record.value,
        }

    @staticmethod
    def _argument_value(
        binding: dict[str, Any],
        parameter_name: str,
        *,
        sandbox: bool,
    ) -> Any:
        if parameter_name == "path" or parameter_name.endswith("_path"):
            path = binding.get("sandbox_path") if sandbox else binding.get("host_path")
            if path:
                return path
        return binding.get("value")


_DOWNSTREAM_OWNED_OPERATIONS = {
    "build_report_payload",
    "compile_report_payload",
    "construct_report_payload",
    "create_chart",
    "describe_data",
    "describe_dataframe",
    "generate_chart",
    "generate_description",
    "generate_report",
    "format_report_payload",
    "prepare_report_payload",
    "render_chart",
    "render_html",
    "render_markdown",
    "render_report",
    "summarize_result",
    "write_report",
}


def _is_downstream_owned_step(
    raw_step: dict[str, Any],
    operation_kind: str,
) -> bool:
    if operation_kind in _DOWNSTREAM_OWNED_OPERATIONS:
        return True
    step_id = str(raw_step.get("step_id", "")).lower()
    description = str(raw_step.get("description", "")).lower()
    output_text = _json_dumps(raw_step.get("outputs", [])).lower()
    report_delivery_signal = any(
        signal in " ".join((step_id, description, output_text))
        for signal in (
            "downstream reporting",
            "html report",
            "markdown report",
            "report payload",
            "report_payload",
        )
    )
    return report_delivery_signal and operation_kind.startswith(
        ("build", "compile", "construct", "format", "prepare", "render", "write")
    )


def _source_summary(sources: list[str]) -> str:
    if not sources:
        return "No sources were provided."
    return "\n".join(f"- {source}" for source in sources)


def _dataset_summary(
    catalog: dict[str, Any], allowed_names: set[str] | None = None
) -> str:
    datasets = catalog.get("datasets", [])
    if not datasets:
        return "No catalog datasets were provided."
    lines = []
    for dataset in datasets:
        name = str(dataset.get("name", "unnamed"))
        if allowed_names is not None and name not in allowed_names:
            continue
        kind = dataset.get("kind", "dataset")
        description = dataset.get("description", "No description provided.")
        lines.append(f"- {name} ({kind}): {description}")
    return "\n".join(lines) if lines else "No selected catalog datasets were provided."


def _schema_summary(schemas: dict[str, Any]) -> str:
    lines = []
    for table_name, table in schemas.get("tables", {}).items():
        columns = ", ".join(_table_columns(table)) or "no columns listed"
        lines.append(f"- table {table_name}: {columns}")
    for collection_name, collection in schemas.get("vector_collections", {}).items():
        columns = ", ".join(_table_columns(collection)) or "no columns listed"
        lines.append(f"- vector collection {collection_name}: {columns}")
    return "\n".join(lines) if lines else "No schema metadata was provided."


def _table_columns(table: dict[str, Any]) -> list[str]:
    columns = table.get("columns", [])
    if isinstance(columns, dict):
        return [str(name) for name in columns]
    if isinstance(columns, list):
        return [str(column) for column in columns]
    return []


def _first_source(sources: list[str], suffix: str | None = None) -> str | None:
    for source in sources:
        if suffix is None or str(source).lower().endswith(suffix):
            return str(source)
    return str(sources[0]) if sources else None


def _first_source_with_suffixes(
    sources: list[str], suffixes: tuple[str, ...]
) -> str | None:
    normalized = tuple(item.lower() for item in suffixes)
    return next(
        (str(source) for source in sources if str(source).lower().endswith(normalized)),
        None,
    )


def _method_hub_payload(runtime: EngineRuntimeContext) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": definition.name,
            "description": definition.description,
            "parameters_schema": definition.input_schema,
            "output_schema": definition.metadata.get("output_schema", {}),
            "capability_names": list(definition.capability_names),
            "trust_level": "platform_remote",
            "provider": "mcp",
        }
        for definition in runtime.mcp_tools
    ]


def _execution_spec_payload(spec: ExecutionSpec) -> dict[str, Any]:
    return {
        "intent": spec.intent,
        "objective": spec.objective,
        "data_requirements": list(spec.data_requirements),
        "capability_requirements": [
            _to_jsonable(item) for item in spec.capability_requirements
        ],
        "constraints": deepcopy(spec.constraints),
        "confirmed": spec.confirmed,
        "engine_hint": spec.engine_hint,
    }


def _scope_from_spec(
    spec: ExecutionSpec, corpus_package: DataCorpusPackage
) -> dict[str, Any]:
    constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
    scope = (
        constraints.get("scope", {})
        if isinstance(constraints.get("scope", {}), dict)
        else {}
    )
    selected = constraints.get("selected_data_context", {})
    if not isinstance(selected, dict):
        selected = {}
    selected_sources = (
        selected.get("selected_sources")
        or scope.get("sources")
        or spec.data_requirements
    )
    if not selected_sources:
        selected_sources = corpus_package.sources
    selected_tables = selected.get("selected_tables") or scope.get("tables")
    selected_vectors = selected.get("selected_vector_collections")
    if selected_vectors is None:
        selected_vectors = scope.get("vector_collections")
    selected_documents = selected.get("selected_documents")
    if selected_documents is None:
        selected_documents = scope.get("documents")
    columns = selected.get("selected_columns") or constraints.get("columns") or {}
    has_explicit_scope = bool(scope or selected)
    if selected_tables is None:
        selected_tables = list(corpus_package.schemas.get("tables", {}))
    if selected_vectors is None:
        selected_vectors = (
            []
            if has_explicit_scope
            else list(corpus_package.schemas.get("vector_collections", {}))
        )
    if selected_documents is None:
        selected_documents = []
    return {
        "sources": [str(item) for item in selected_sources or []],
        "tables": [str(item) for item in selected_tables or []],
        "vector_collections": [str(item) for item in selected_vectors or []],
        "documents": [str(item) for item in selected_documents or []],
        "columns": {
            str(name): [str(column) for column in values]
            for name, values in (columns.items() if isinstance(columns, dict) else [])
        },
        "explicit": has_explicit_scope,
    }


def _scoped_corpus_payload(
    spec: ExecutionSpec, corpus_package: DataCorpusPackage
) -> dict[str, Any]:
    scope = _scope_from_spec(spec, corpus_package)
    tables = corpus_package.schemas.get("tables", {})
    vectors = corpus_package.schemas.get("vector_collections", {})
    scoped_tables: dict[str, Any] = {}
    for name in scope["tables"]:
        table = deepcopy(tables.get(name, {})) if isinstance(tables, dict) else {}
        allowed_columns = scope["columns"].get(name)
        if allowed_columns:
            original = table.get("columns", []) if isinstance(table, dict) else []
            if isinstance(original, dict):
                table["columns"] = {
                    column: definition
                    for column, definition in original.items()
                    if column in allowed_columns
                }
            else:
                table["columns"] = [column for column in allowed_columns]
        scoped_tables[name] = table
    scoped_vectors = {
        name: deepcopy(vectors.get(name, {}))
        for name in scope["vector_collections"]
        if isinstance(vectors, dict) and name in vectors
    }
    catalog = deepcopy(corpus_package.metadata.get("catalog", {}))
    if isinstance(catalog, dict) and isinstance(catalog.get("datasets"), list):
        allowed = set(
            scope["tables"] + scope["vector_collections"] + scope["documents"]
        )
        catalog["datasets"] = [
            item for item in catalog["datasets"] if str(item.get("name")) in allowed
        ]
    return {
        "sources": scope["sources"],
        "schemas": {"tables": scoped_tables, "vector_collections": scoped_vectors},
        "metadata": {**deepcopy(corpus_package.metadata), "catalog": catalog},
        "scope": scope,
    }


def _normalize_rows(raw_data: Any) -> list[Any]:
    if raw_data is None:
        return []
    if isinstance(raw_data, list):
        return raw_data
    if isinstance(raw_data, dict):
        for key in ("rows", "sample_rows", "data", "result"):
            value = raw_data.get(key)
            if isinstance(value, list):
                return value
        return [raw_data]
    return [raw_data]


def _infer_schema(rows: list[Any]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        for name, value in row.items():
            if value is None:
                value_type = fields.get(str(name), {}).get("type", "null")
            elif isinstance(value, bool):
                value_type = "boolean"
            elif isinstance(value, (int, float)):
                value_type = "number"
            elif isinstance(value, (dict, list)):
                value_type = "object" if isinstance(value, dict) else "array"
            else:
                value_type = "string"
            fields[str(name)] = {
                "name": str(name),
                "type": value_type,
                "nullable": fields.get(str(name), {}).get("nullable", False)
                or value is None,
            }
    return {"shape": "table", "fields": list(fields.values())}


def _profile_rows(rows: list[Any], raw_data: Any) -> dict[str, Any]:
    declared_count = raw_data.get("row_count") if isinstance(raw_data, dict) else None
    row_count = declared_count if isinstance(declared_count, int) else len(rows)
    null_counts: dict[str, int] = {}
    numeric_values: dict[str, list[float]] = {}
    cardinality: dict[str, set[str]] = {}
    for row in rows[:1000]:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            name = str(key)
            if value is None:
                null_counts[name] = null_counts.get(name, 0) + 1
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_values.setdefault(name, []).append(float(value))
            bucket = cardinality.setdefault(name, set())
            if len(bucket) < 100:
                bucket.add(str(value))
    return {
        "row_count": row_count,
        "sampled_profile_rows": min(len(rows), 1000),
        "null_counts": null_counts,
        "cardinality": {key: len(values) for key, values in cardinality.items()},
        "numeric_stats": {
            key: {
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }
            for key, values in numeric_values.items()
            if values
        },
    }
