from __future__ import annotations

import inspect
import json
import operator
import os
import re
import threading
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    InterfaceDefinition,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult
from data_intelligence_sdk.tools import record_sandbox_method_calls

PLAN_AGENT_PROMPT = """
You are the Data Planning Agent. Build a validated, data-aware DAG for a report.

# INPUT
1. `execution_spec`: The confirmed objective, capabilities, data requirements, and constraints.
2. `corpus_package`: Available sources, schemas, and catalog metadata.
3. `previous_plan`: The prior plan revision, when TemplateAgent requested more data.
4. `template_feedback`: Missing data requests or rejected template bindings.

# RULES
1. Treat `execution_spec.constraints.scope` and `selected_data_context` as strict allowlists.
2. Never use a table, column, vector collection, document, or source outside those allowlists.
3. Give every step stable `step_id`, `inputs`, `depends_on`, and named `outputs`.
4. Dependencies mean data lineage; an upstream dependency must be referenced by an input.
5. Use the requested `group_by`, `metrics`, filters, capabilities, and output format.
6. Add feasible steps requested by TemplateAgent. Mark infeasible requests with a reason.
7. Do not generate code or chart presentation options.

# OUTPUT
Return only a JSON object with `schema_version`, `plan_id`, `revision`, `objective`,
`scope`, `steps`, and `warnings`. Each step must contain `step_id`, `description`,
`required`, `inputs`, `depends_on`, `required_data`, `operation`, `outputs`, and `fallback`.
""".strip()

TEMPLATE_AGENT_PROMPT = """
You are the Report Template Agent. Select and adapt an immutable report template
to a validated ReportPlan.

# INPUT
1. `user_goal`: The report objective.
2. `plan`: Named plan outputs and their semantic roles.
3. `corpus_summary`: Only the data allowed by the confirmed execution spec.
4. `candidate_templates`: Built-in template descriptors.
5. `previous_instance`: The prior run-local template revision, if any.

# RULES
1. Select the template that best matches the report intent and available data shape.
2. Never mutate the canonical template; produce a run-local TemplateInstance.
3. Bind every requirement to a real named plan output.
4. Emit missing data requests for unresolved requirements.
5. Apply declared fallbacks to optional requirements that cannot be satisfied.
6. Do not query data, execute tools, or create ECharts options.

# OUTPUT
Return only JSON containing `template_id`, `version`, `selection_reason`, and
optional `binding_hints`. Deterministic code validates and materializes the instance.
""".strip()

ROUTER_AGENT_PROMPT = """
You are the Routing Agent. Choose how to execute one validated PlanStep.

# INPUT
1. `step_request`: The current PlanStep.
2. `method_hub`: Trusted and generated methods with schemas and capabilities.
3. `available_sources`: Sources permitted by the confirmed execution spec.

# RULES
1. Choose an existing tool only when its contract satisfies the step.
2. Map arguments to the tool parameter schema.
3. Otherwise request generated code.
4. Never invoke a tool yourself.

# OUTPUT
Return only JSON:
{
  "route": "existing_tool | generate_tool | unsupported",
  "tool_name": "tool_name_or_null",
  "arguments": {},
  "reason": "short explanation"
}
""".strip()

CODE_AGENT_PROMPT = """
You are the Data Programming Agent. Generate a single safe Python data tool for
one validated PlanStep.

# INPUT
1. `step_request`: The exact operation, allowed tables, columns, inputs, and outputs.
2. `schema_catalog`: The scoped schema and catalog. Anything absent is forbidden.
3. `error_logs`: Sandbox errors from the previous attempt.
4. `validation_feedback`: Validator feedback from the previous attempt.

# RULES
1. Never invent a table or column.
2. Honor filters, grouping, metrics, named outputs, and upstream input references.
3. Return an empty list for a valid no-data result.
4. Include type hints and a complete docstring.
5. Do not perform network access outside the supplied data connection.
6. For Method Hub composition, import `call_tool` from `axiom_method_hub`.
7. Never open HTTP sockets or embed an MCP endpoint or token.
8. Define the requested function and keep its return value JSON-serializable.

# OUTPUT
Return only JSON with `tool_name`, `parameters_schema`, `output_schema`,
`source_code`, and `execution_arguments`.
""".strip()

VALIDATOR_AGENT_PROMPT = """
You are the Validation Agent. Validate generated code against sandbox evidence
and the original PlanStep.

# INPUT
1. `step_description`: The business operation.
2. `source_code`: Generated source code.
3. `sandbox_logs`: Syntax/runtime status.
4. `sample_data`: Bounded validation output.

# RULES
1. Fail syntax or runtime errors.
2. Check the output shape and fields.
3. Check that the code uses only allowed data.
4. Check that grouping and metrics match the PlanStep.
5. Give actionable feedback for a retry.

# OUTPUT
Return only JSON with `status` (`Pass`, `Fail`, or `NeedsRevision`), `feedback`,
and `validated_code`.
""".strip()

DATASCIENCE_AGENT_PROMPT = """
You are the Data Science Agent. Interpret one materialized PlanStep result and
produce evidence-backed analysis for report and chart consumers.

# INPUT
1. `step`: The validated PlanStep.
2. `materialized_result`: Artifact reference, deterministic schema, profile, and bounded sample.
3. `upstream_step_results`: Results declared as dependencies.
4. `template_requirements`: Template requirements bound to this step.

# RULES
1. Distinguish execution failure from a valid no-data result.
2. Never claim a cause that is not supported by evidence.
3. Do not recompute large datasets in the prompt.
4. Use authoritative tool results for metrics.
5. Return concise analysis, normalized metrics, and chart dataset guidance.

# OUTPUT
Return only JSON with `status`, `analysis_summary`, `observations`,
`aggregated_data`, and optional `warnings`.
""".strip()

CHART_AGENT_PROMPT = """
You are the ECharts Chart Agent. Convert one validated ChartRequest into a safe,
JSON-serializable ECharts specification.

# INPUT
1. `chart_request`: Chart intent, allowed types, encoding roles, presentation metadata, and bounded data.

# RULES
1. Preserve `chart_id` and choose only an allowed chart type.
2. Reference only fields present in the dataset schema.
3. Do not recompute business metrics.
4. Never emit JavaScript functions, executable strings, or unapproved URLs.
5. Return a fallback when data is insufficient.

# OUTPUT
Return only JSON with `status`, `chart_id`, `library`, `selected_type`,
`selection_reason`, `option`, `accessibility`, and `warnings`.
""".strip()

REPORT_AGENT_PROMPT = """
You are the Report Agent. Synthesize completed analysis blocks into a coherent
Markdown report while preserving evidence and avoiding unsupported claims.

# INPUT
1. `user_goal`: The overall objective.
2. `all_steps_data`: Completed step analysis and metrics.

# OUTPUT
Return the final Markdown report directly. Do not wrap it in JSON.
""".strip()

STRUCTURED_REPORT_AGENT_PROMPT = """
You are the Structured Report Agent. Compose a report JSON document from a
run-local TemplateInstance, DataStepResults, and ChartResults.

# RULES
1. Preserve template section and block order.
2. Insert content only into declared blocks.
3. Reference existing metric, chart, source, and evidence IDs.
4. Mark missing optional blocks and fallbacks explicitly.
5. Do not emit HTML or Markdown.

# OUTPUT
Return only JSON containing `schema_version`, `status`, `title`, `summary`,
`template`, `sections`, `metrics`, `charts`, `sources`, and `warnings`.
""".strip()

GENERATED_TOOL_CAPABILITY = "generated_report_data_tool"
TEMPLATE_POOL_PACKAGE = "data_intelligence_sdk.templates.pool"


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


def _safe_id(value: Any) -> str:
    rendered = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value)).strip("-.")
    return rendered.lower() or "item"


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


def _method_parameters_schema(method: object) -> dict[str, Any]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}, "required": []}
    properties: dict[str, Any] = {}
    required = []
    for name, parameter in signature.parameters.items():
        if name in {"self", "args", "kwargs"}:
            continue
        properties[name] = {
            "type": "string",
            "description": f"Argument `{name}` for the tool.",
        }
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _method_hub_payload(runtime: EngineRuntimeContext) -> list[dict[str, Any]]:
    local_methods = [
        {
            "tool_name": registered.name,
            "description": registered.metadata.get("description", ""),
            "parameters_schema": registered.metadata.get(
                "parameters_schema", _method_parameters_schema(registered.method)
            ),
            "output_schema": registered.metadata.get("output_schema", {}),
            "capability_names": registered.capability_names,
            "trust_level": registered.trust_level,
            "provider": "local",
        }
        for registered in runtime.method_hub.list_methods()
    ]
    remote_methods = [
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
    return [*local_methods, *remote_methods]


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


class _PromptAgent:
    def __init__(self, name: str, system_prompt: str, llm: object | None) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def _invoke_text_with_prompt(self, system_prompt: str, **inputs: Any) -> str | None:
        if self.llm is None or not hasattr(self.llm, "invoke"):
            return None
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=system_prompt),
                    ("user", "\n\n".join(f"{key}:\n{{{key}}}" for key in inputs)),
                ]
            )
            values = {
                key: _json_dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in inputs.items()
            }
            return _extract_message_content(self.llm.invoke(prompt.invoke(values)))
        except Exception:
            return None

    def _invoke_text(self, **inputs: Any) -> str | None:
        return self._invoke_text_with_prompt(self.system_prompt, **inputs)

    def _invoke_json_with_prompt(self, system_prompt: str, **inputs: Any) -> Any | None:
        text = self._invoke_text_with_prompt(system_prompt, **inputs)
        if text is None:
            return None
        try:
            return _parse_json_payload(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _invoke_json(self, **inputs: Any) -> Any | None:
        return self._invoke_json_with_prompt(self.system_prompt, **inputs)


class PlanAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("plan_agent", PLAN_AGENT_PROMPT, llm)

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        previous_plan: dict[str, Any] | None = None,
        template_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        scoped_corpus = _scoped_corpus_payload(spec, corpus_package)
        payload = self._invoke_json(
            execution_spec=_execution_spec_payload(spec),
            corpus_package=scoped_corpus,
            previous_plan=previous_plan,
            template_feedback=template_feedback or [],
        )
        if isinstance(payload, list):
            payload = {"steps": payload}
        if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
            normalized = self._normalize_plan(
                payload, spec, corpus_package, previous_plan
            )
            if normalized["steps"]:
                return normalized
        return self._fallback_plan(
            spec, corpus_package, previous_plan, template_feedback or []
        )

    def _normalize_plan(
        self,
        payload: dict[str, Any],
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        previous_plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        scope = _scope_from_spec(spec, corpus_package)
        allowed_tables = set(scope["tables"])
        allowed_vectors = set(scope["vector_collections"])
        normalized_steps = []
        seen: set[str] = set()
        for index, raw_step in enumerate(payload.get("steps", []), start=1):
            if not isinstance(raw_step, dict):
                continue
            required_data = raw_step.get("required_data", {})
            if not isinstance(required_data, dict):
                required_data = {}
            tables = [str(item) for item in required_data.get("tables", [])]
            vectors = [
                str(item) for item in required_data.get("vector_collections", [])
            ]
            if scope["explicit"] and (
                any(item not in allowed_tables for item in tables)
                or any(item not in allowed_vectors for item in vectors)
            ):
                continue
            step_id = _safe_id(raw_step.get("step_id", f"step-{index}"))
            if step_id in seen:
                step_id = f"{step_id}-{index}"
            seen.add(step_id)
            columns = [str(item) for item in required_data.get("columns", [])]
            if tables and scope["columns"].get(tables[0]):
                columns = [
                    item for item in columns if item in scope["columns"][tables[0]]
                ]
            outputs = raw_step.get("outputs")
            if not isinstance(outputs, list) or not outputs:
                outputs = [
                    {
                        "name": f"{step_id}-result",
                        "shape": "table",
                        "semantic_roles": ["analysis_data"],
                        "consumer_hints": ["analysis", "report"],
                    }
                ]
            normalized_steps.append(
                {
                    **raw_step,
                    "step_id": step_id,
                    "required": bool(raw_step.get("required", True)),
                    "inputs": raw_step.get("inputs", []),
                    "depends_on": [
                        str(item) for item in raw_step.get("depends_on", [])
                    ],
                    "required_data": {
                        **required_data,
                        "tables": tables,
                        "vector_collections": vectors,
                        "columns": columns,
                    },
                    "operation": raw_step.get("operation", {"kind": "analyze"}),
                    "outputs": outputs,
                    "fallback": raw_step.get(
                        "fallback",
                        {
                            "action": "complete_no_data",
                            "message": "No matching data was found.",
                        },
                    ),
                }
            )
        revision = int(
            payload.get("revision", (previous_plan or {}).get("revision", 0) + 1)
        )
        return {
            "schema_version": "1.0",
            "plan_id": str(
                payload.get(
                    "plan_id", (previous_plan or {}).get("plan_id", "report-plan")
                )
            ),
            "revision": revision,
            "objective": spec.objective,
            "scope": scope,
            "steps": normalized_steps,
            "warnings": payload.get("warnings", []),
        }

    def _fallback_plan(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        previous_plan: dict[str, Any] | None,
        template_feedback: list[dict[str, Any]],
    ) -> dict[str, Any]:
        scope = _scope_from_spec(spec, corpus_package)
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        group_by = [str(item) for item in constraints.get("group_by", [])]
        metrics = [
            item for item in constraints.get("metrics", []) if isinstance(item, dict)
        ]
        filters = (
            constraints.get("filters", {})
            if isinstance(constraints.get("filters", {}), dict)
            else {}
        )
        steps: list[dict[str, Any]] = []
        for table in scope["tables"]:
            columns = scope["columns"].get(table)
            if not columns:
                table_meta = corpus_package.schemas.get("tables", {}).get(table, {})
                columns = _table_columns(table_meta)
            inspect_id = f"inspect-{_safe_id(table)}"
            steps.append(
                {
                    "step_id": inspect_id,
                    "description": f"Inspect the selected columns and data quality of `{table}` for the report objective.",
                    "required": True,
                    "inputs": [
                        {
                            "ref": f"corpus://{table}",
                            "kind": "corpus_source",
                            "required": True,
                        }
                    ],
                    "depends_on": [],
                    "required_data": {
                        "tables": [table],
                        "vector_collections": [],
                        "columns": columns,
                    },
                    "operation": {
                        "kind": "inspect",
                        "parameters": {"columns": columns, "filters": filters},
                    },
                    "outputs": [
                        {
                            "name": f"{_safe_id(table)}-profile",
                            "shape": "record",
                            "semantic_roles": ["profile", "headline_metrics"],
                            "consumer_hints": ["analysis", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "complete_no_data",
                        "message": f"No `{table}` rows are available.",
                    },
                }
            )
            if metrics or group_by:
                metric_fields = [
                    str(item.get("field")) for item in metrics if item.get("field")
                ]
                aggregate_columns = list(dict.fromkeys(group_by + metric_fields))
                aggregate_id = f"aggregate-{_safe_id(table)}"
                steps.append(
                    {
                        "step_id": aggregate_id,
                        "description": f"Aggregate `{table}` by {group_by or ['all rows']} using the confirmed metrics.",
                        "required": True,
                        "inputs": [
                            {
                                "ref": f"corpus://{table}",
                                "kind": "corpus_source",
                                "required": True,
                            }
                        ],
                        "depends_on": [],
                        "required_data": {
                            "tables": [table],
                            "vector_collections": [],
                            "columns": aggregate_columns,
                        },
                        "operation": {
                            "kind": "aggregate",
                            "parameters": {
                                "group_by": group_by,
                                "metrics": metrics,
                                "filters": filters,
                            },
                        },
                        "outputs": [
                            {
                                "name": f"{_safe_id(table)}-aggregation",
                                "shape": "category_series" if group_by else "record",
                                "semantic_roles": [
                                    "primary_measure",
                                    (
                                        "comparison_dimension"
                                        if group_by
                                        else "headline_metrics"
                                    ),
                                ],
                                "fields": group_by
                                + [str(item.get("name")) for item in metrics],
                                "consumer_hints": ["analysis", "chart", "report"],
                            }
                        ],
                        "fallback": {
                            "action": "complete_no_data",
                            "message": f"No `{table}` rows match the confirmed aggregation scope.",
                        },
                    }
                )
        if not scope["explicit"]:
            for collection in scope["vector_collections"]:
                metadata = corpus_package.schemas.get("vector_collections", {}).get(
                    collection, {}
                )
                steps.append(
                    {
                        "step_id": f"inspect-{_safe_id(collection)}",
                        "description": f"Inspect vector collection `{collection}` for the report objective.",
                        "required": False,
                        "inputs": [
                            {
                                "ref": f"corpus://{collection}",
                                "kind": "corpus_source",
                                "required": False,
                            }
                        ],
                        "depends_on": [],
                        "required_data": {
                            "tables": [],
                            "vector_collections": [collection],
                            "columns": _table_columns(metadata),
                        },
                        "operation": {"kind": "inspect"},
                        "outputs": [
                            {
                                "name": f"{_safe_id(collection)}-profile",
                                "shape": "record",
                                "semantic_roles": ["profile"],
                                "consumer_hints": ["analysis", "report"],
                            }
                        ],
                        "fallback": {
                            "action": "omit",
                            "message": "No vector data was available.",
                        },
                    }
                )
        if not steps:
            steps.append(
                {
                    "step_id": "corpus-overview",
                    "description": f"Create a scoped corpus overview for: {spec.objective}",
                    "required": True,
                    "inputs": [],
                    "depends_on": [],
                    "required_data": {
                        "tables": [],
                        "vector_collections": [],
                        "columns": [],
                    },
                    "operation": {"kind": "inspect_metadata"},
                    "outputs": [
                        {
                            "name": "corpus-overview",
                            "shape": "record",
                            "semantic_roles": ["profile", "headline_metrics"],
                            "consumer_hints": ["analysis", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "message",
                        "message": "Only scoped metadata is available.",
                    },
                }
            )
        existing_ids = {step["step_id"] for step in steps}
        for feedback in template_feedback:
            request_id = _safe_id(feedback.get("request_id", "template-data"))
            if request_id in existing_ids:
                continue
            steps.append(
                {
                    "step_id": request_id,
                    "description": str(
                        feedback.get(
                            "description", "Prepare data requested by the template."
                        )
                    ),
                    "required": bool(feedback.get("required", False)),
                    "inputs": [],
                    "depends_on": [],
                    "required_data": {
                        "tables": scope["tables"],
                        "vector_collections": [],
                        "columns": [],
                    },
                    "operation": {
                        "kind": "template_data_request",
                        "parameters": feedback,
                    },
                    "outputs": [
                        {
                            "name": request_id,
                            "shape": feedback.get("expected_output", {}).get(
                                "shape", "table"
                            ),
                            "semantic_roles": feedback.get("expected_output", {}).get(
                                "semantic_roles", []
                            ),
                            "consumer_hints": ["analysis", "chart", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "omit",
                        "message": "The template data request is unavailable.",
                    },
                }
            )
        return {
            "schema_version": "1.0",
            "plan_id": (previous_plan or {}).get("plan_id", "report-plan"),
            "revision": int((previous_plan or {}).get("revision", 0)) + 1,
            "objective": spec.objective,
            "scope": scope,
            "steps": steps,
            "warnings": [],
        }


class TemplatePool:
    def __init__(self, package: str = TEMPLATE_POOL_PACKAGE) -> None:
        self.package = package

    def _root(self) -> Any:
        return resources.files(self.package)

    def manifest(self) -> dict[str, Any]:
        return json.loads(
            self._root().joinpath("manifest.json").read_text(encoding="utf-8")
        )

    def list_templates(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.manifest().get("templates", [])]

    def get(self, template_id: str, version: str | None = None) -> dict[str, Any]:
        for descriptor in self.manifest().get("templates", []):
            if descriptor.get("template_id") != template_id:
                continue
            if version is not None and descriptor.get("version") != version:
                continue
            payload = json.loads(
                self._root()
                .joinpath(str(descriptor["path"]))
                .read_text(encoding="utf-8")
            )
            return payload
        raise KeyError(f"Unknown report template: {template_id!r} version={version!r}")


class TemplateAgent(_PromptAgent):
    def __init__(self, llm: object | None, template_pool: TemplatePool) -> None:
        super().__init__("template_agent", TEMPLATE_AGENT_PROMPT, llm)
        self.template_pool = template_pool

    def run(
        self,
        spec: ExecutionSpec,
        plan: dict[str, Any],
        corpus_package: DataCorpusPackage,
        previous_instance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidates = self.template_pool.list_templates()
        scoped = _scoped_corpus_payload(spec, corpus_package)
        payload = self._invoke_json(
            user_goal=spec.objective,
            plan=plan,
            corpus_summary=scoped,
            candidate_templates=candidates,
            previous_instance=previous_instance,
        )
        requested_id = (
            spec.constraints.get("template_id")
            if isinstance(spec.constraints, dict)
            else None
        )
        selected_id = requested_id
        selection_reason = (
            "The template was explicitly requested by the execution spec."
        )
        if selected_id is None and isinstance(payload, dict):
            selected_id = payload.get("template_id")
            selection_reason = str(
                payload.get("selection_reason", "Selected by TemplateAgent.")
            )
        valid_ids = {str(item.get("template_id")) for item in candidates}
        if selected_id not in valid_ids:
            selected_id, selection_reason = self._fallback_selection(spec, plan)
        definition = self.template_pool.get(str(selected_id))
        return self._materialize_instance(
            definition, plan, previous_instance, selection_reason
        )

    def _fallback_selection(
        self, spec: ExecutionSpec, plan: dict[str, Any]
    ) -> tuple[str, str]:
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        group_by = constraints.get("group_by", [])
        text = spec.objective.lower()
        has_time = any(
            token in text for token in ("time", "trend", "month", "week", "day", "year")
        ) or any(
            "time" in role
            for step in plan.get("steps", [])
            for output in step.get("outputs", [])
            for role in output.get("semantic_roles", [])
        )
        if has_time:
            return (
                "time-series-analysis",
                "The goal or plan contains an ordered time dimension.",
            )
        if group_by:
            return (
                "segment-comparison",
                "The confirmed spec compares grouped categories.",
            )
        return "executive-overview", "The goal is best served by a concise overview."

    def _materialize_instance(
        self,
        definition: dict[str, Any],
        plan: dict[str, Any],
        previous_instance: dict[str, Any] | None,
        reason: str,
    ) -> dict[str, Any]:
        outputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for step in plan.get("steps", []):
            for output in step.get("outputs", []):
                outputs.append((step, output))
        bindings = []
        missing = []
        applied_fallbacks = []
        for requirement in definition.get("data_requirements", []):
            match = self._match_output(requirement, outputs)
            requirement_id = str(requirement.get("requirement_id"))
            if match is not None:
                step, output = match
                bindings.append(
                    {
                        "requirement_ref": requirement_id,
                        "plan_output_ref": f"step-output://{step['step_id']}/{output['name']}",
                        "status": "resolved",
                    }
                )
            elif requirement.get("required", False):
                missing.append(
                    {
                        "request_id": f"provide-{requirement_id}",
                        "requirement_ref": requirement_id,
                        "required": True,
                        "description": requirement.get(
                            "description", "Provide required template data."
                        ),
                        "expected_output": requirement.get("expected_output", {}),
                        "reason": "No compatible named plan output exists.",
                    }
                )
            else:
                applied_fallbacks.append(
                    {
                        "requirement_ref": requirement_id,
                        "fallback": requirement.get("fallback", {"action": "omit"}),
                    }
                )
        sections = deepcopy(definition.get("sections", []))
        for section in sections:
            for block in section.get("blocks", []):
                slot = block.get("chart_slot")
                if isinstance(slot, dict):
                    slot["chart_id"] = ".".join(
                        [
                            str(definition.get("template_id")),
                            str(section.get("section_id")),
                            str(slot.get("chart_slot_id")),
                        ]
                    )
        revision = int((previous_instance or {}).get("revision", 0)) + 1
        status = (
            "needs_plan_revision"
            if missing
            else ("partial" if applied_fallbacks else "accepted")
        )
        return {
            "schema_version": "1.0",
            "status": status,
            "selection": {
                "template_id": definition.get("template_id"),
                "version": definition.get("version"),
                "reason": reason,
            },
            "template_instance": {
                "instance_id": f"template-instance-{_safe_id(definition.get('template_id'))}",
                "template_id": definition.get("template_id"),
                "template_version": definition.get("version"),
                "revision": revision,
                "status": "draft" if missing else status,
                "bindings": bindings,
                "sections": sections,
                "applied_fallbacks": applied_fallbacks,
                "warnings": [],
            },
            "missing_data_requests": missing,
            "warnings": [],
        }

    def _match_output(
        self,
        requirement: dict[str, Any],
        outputs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        semantic = requirement.get("semantic_roles", {})
        requested = {
            str(role)
            for key in ("measures", "dimensions", "time_dimensions")
            for role in (semantic.get(key, []) if isinstance(semantic, dict) else [])
        }
        for step, output in outputs:
            roles = {str(role) for role in output.get("semantic_roles", [])}
            fields = {str(field) for field in output.get("fields", [])}
            if not requested or requested & roles or requested & fields:
                return step, output
        return outputs[0] if outputs else None


class RouterAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("router_agent", ROUTER_AGENT_PROMPT, llm)

    def run(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_request=step_request,
            method_hub=method_hub,
            available_sources=sources,
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
                return payload
        return self._fallback_route(step_request, method_hub, sources)

    def _fallback_route(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        csv_source = _first_source(sources, ".csv")
        if csv_source:
            scan_tool = next(
                (tool for tool in method_hub if tool["tool_name"] == "scan_csv"), None
            )
            if scan_tool is not None:
                return {
                    "route": "existing_tool",
                    "tool_name": "scan_csv",
                    "arguments": {"path": csv_source},
                    "reason": "The selected CSV can be inspected by the built-in scanner.",
                }
        description = str(step_request.get("description", "")).lower()
        tables = {
            str(item).lower()
            for item in step_request.get("required_data", {}).get("tables", [])
        }
        for tool in method_hub:
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
            payload.setdefault("source_code", "")
            payload.setdefault("execution_arguments", {})
            return payload
        name = f"generated_{_safe_id(step_request.get('step_id', 'report_tool')).replace('-', '_')}"
        return {
            "tool_name": name,
            "parameters_schema": {"type": "object", "properties": {}, "required": []},
            "output_schema": {"type": "array"},
            "execution_arguments": {},
            "source_code": (
                "from typing import Any\n\n"
                f"def {name}(**kwargs: Any) -> list[Any]:\n"
                '    """Return no rows when code generation is unavailable."""\n'
                "    return []\n"
            ),
        }


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
    ) -> dict[str, Any]:
        payload = self._invoke_json(
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


class ChartAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("chart_agent", CHART_AGENT_PROMPT, llm)

    def run(self, chart_request: dict[str, Any]) -> dict[str, Any]:
        payload = self._invoke_json(chart_request=chart_request)
        if isinstance(payload, dict) and payload.get("chart_id") == chart_request.get(
            "chart_id"
        ):
            payload.setdefault("status", "ready")
            payload.setdefault("library", "echarts")
            payload.setdefault("warnings", [])
            return payload
        return self._fallback_chart(chart_request)

    def _fallback_chart(self, request: dict[str, Any]) -> dict[str, Any]:
        dataset = request.get("dataset", {})
        data = dataset.get("data", [])
        if not data:
            return {
                "schema_version": "1.0",
                "status": "fallback",
                "chart_id": request.get("chart_id"),
                "library": "echarts",
                "selected_type": request.get("suggested_type", "bar"),
                "selection_reason": "No chartable rows were available.",
                "option": {},
                "fallback": request.get("fallback", {"action": "table"}),
                "warnings": ["Chart data is empty."],
            }
        chart_type = str(request.get("suggested_type", "bar"))
        allowed = request.get("allowed_types", [chart_type])
        if chart_type not in allowed:
            chart_type = str(allowed[0])
        fields = [
            field.get("name") for field in dataset.get("schema", {}).get("fields", [])
        ]
        x_field = fields[0] if fields else "category"
        y_field = fields[1] if len(fields) > 1 else (fields[0] if fields else "value")
        echarts_type = "bar" if chart_type == "stacked_bar" else chart_type
        series: dict[str, Any] = {
            "type": echarts_type,
            "encode": {"x": x_field, "y": y_field},
        }
        if chart_type == "stacked_bar":
            series["stack"] = "total"
        return {
            "schema_version": "1.0",
            "status": "ready",
            "chart_id": request.get("chart_id"),
            "library": "echarts",
            "selected_type": chart_type,
            "selection_reason": "The deterministic fallback used the template suggestion.",
            "dataset_refs": [dataset.get("artifact_ref")],
            "option": {
                "title": {
                    "text": request.get("presentation", {}).get(
                        "title", request.get("intent", "Chart")
                    )
                },
                "tooltip": {"trigger": "axis"},
                "dataset": {"source": data},
                "xAxis": {"type": "category"},
                "yAxis": {"type": "value"},
                "series": [series],
            },
            "accessibility": {"summary": request.get("intent", "Data chart")},
            "warnings": [],
        }


class ReportAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("report_agent", REPORT_AGENT_PROMPT, llm)

    def run_markdown(
        self,
        user_goal: str,
        all_steps_data: list[dict[str, Any]],
        corpus_package: DataCorpusPackage,
        scoped_payload: dict[str, Any],
    ) -> str:
        text = self._invoke_text(user_goal=user_goal, all_steps_data=all_steps_data)
        if text:
            return text
        return self._fallback_markdown(
            user_goal, all_steps_data, corpus_package, scoped_payload
        )

    def run_structured(
        self,
        spec: ExecutionSpec,
        template_instance: dict[str, Any],
        data_step_results: list[dict[str, Any]],
        chart_results: list[dict[str, Any]],
        scoped_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._invoke_json_with_prompt(
            STRUCTURED_REPORT_AGENT_PROMPT,
            user_goal=spec.objective,
            template_instance=template_instance,
            data_step_results=data_step_results,
            chart_results=chart_results,
            source_summary={"sources": scoped_payload.get("sources", [])},
        )
        if isinstance(payload, dict) and isinstance(payload.get("sections"), list):
            payload.setdefault("schema_version", "1.0")
            payload.setdefault("status", "completed")
            payload.setdefault("template", self._template_ref(template_instance))
            payload.setdefault("metrics", self._collect_metrics(data_step_results))
            payload.setdefault("charts", chart_results)
            payload.setdefault("sources", scoped_payload.get("sources", []))
            payload.setdefault("warnings", [])
            return payload
        return self._fallback_structured(
            spec, template_instance, data_step_results, chart_results, scoped_payload
        )

    def _fallback_structured(
        self,
        spec: ExecutionSpec,
        template_instance: dict[str, Any],
        data_step_results: list[dict[str, Any]],
        chart_results: list[dict[str, Any]],
        scoped_payload: dict[str, Any],
    ) -> dict[str, Any]:
        analysis_by_step = {
            str(item.get("step_id")): item for item in data_step_results
        }
        chart_by_id = {str(item.get("chart_id")): item for item in chart_results}
        sections = []
        for section in template_instance.get("sections", []):
            blocks = []
            for block in section.get("blocks", []):
                block_type = block.get("type")
                content: dict[str, Any]
                status = "completed"
                if block_type == "chart":
                    chart_id = block.get("chart_slot", {}).get("chart_id")
                    chart = chart_by_id.get(str(chart_id))
                    content = {"chart_id": chart_id, "chart": chart}
                    if chart is None or chart.get("status") not in {
                        "ready",
                        "completed",
                    }:
                        status = "fallback"
                        content["fallback"] = block.get("chart_slot", {}).get(
                            "fallback", {"action": "table"}
                        )
                elif block_type == "kpi_group":
                    content = {"metrics": self._collect_metrics(data_step_results)}
                    if not content["metrics"]:
                        status = "no_data"
                elif block_type in {"narrative", "recommendations"}:
                    summaries = [
                        item.get("analysis_summary")
                        for item in data_step_results
                        if item.get("analysis_summary")
                    ]
                    content = {
                        "text": " ".join(map(str, summaries))
                        or "No data matched the confirmed scope."
                    }
                    if not summaries:
                        status = "no_data"
                else:
                    content = {
                        "rows": [
                            item.get("aggregated_data", {})
                            for item in data_step_results
                        ]
                    }
                blocks.append(
                    {
                        "block_id": block.get("block_id"),
                        "type": block_type,
                        "title": block.get("title"),
                        "status": status,
                        "content": content,
                        "evidence_refs": [
                            item.get("step_result_artifact", {}).get("artifact_ref")
                            for item in analysis_by_step.values()
                            if item.get("step_result_artifact", {}).get("artifact_ref")
                        ],
                    }
                )
            sections.append(
                {
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "status": (
                        "completed"
                        if any(block["status"] == "completed" for block in blocks)
                        else "no_data"
                    ),
                    "layout": section.get("layout", {}),
                    "blocks": blocks,
                }
            )
        statuses = {item.get("status") for item in data_step_results}
        report_status = (
            "partial" if "failed" in statuses or "partial" in statuses else "completed"
        )
        return {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": report_status,
            "title": spec.objective,
            "summary": " ".join(
                str(item.get("analysis_summary"))
                for item in data_step_results
                if item.get("analysis_summary")
            )
            or "No data matched the confirmed scope.",
            "template": self._template_ref(template_instance),
            "sections": sections,
            "metrics": self._collect_metrics(data_step_results),
            "charts": chart_results,
            "sources": scoped_payload.get("sources", []),
            "data_scope": scoped_payload.get("scope", {}),
            "warnings": [
                warning
                for item in data_step_results
                for warning in item.get("warnings", [])
            ],
        }

    def _template_ref(self, instance: dict[str, Any]) -> dict[str, Any]:
        return {
            "template_id": instance.get("template_id"),
            "template_version": instance.get("template_version"),
            "template_instance_id": instance.get("instance_id"),
            "revision": instance.get("revision"),
        }

    def _collect_metrics(
        self, data_step_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            metric
            for item in data_step_results
            for metric in item.get("aggregated_metrics", [])
        ]

    def _fallback_markdown(
        self,
        user_goal: str,
        all_steps_data: list[dict[str, Any]],
        corpus_package: DataCorpusPackage,
        scoped_payload: dict[str, Any],
    ) -> str:
        catalog = scoped_payload.get("metadata", {}).get("catalog", {})
        if not isinstance(catalog, dict):
            catalog = {}
        lines = [
            "# Data Intelligence Report",
            "",
            "## Introduction",
            "",
            f"This report summarizes the available analysis for: {user_goal}.",
            "",
            "## Key Metrics",
            "",
        ]
        summary = catalog.get("summary") or corpus_package.metadata.get(
            "catalog", {}
        ).get("summary")
        if summary:
            lines[6:6] = [str(summary), ""]
        for step in all_steps_data:
            aggregated = step.get("aggregated_data", {})
            if isinstance(aggregated, dict) and aggregated:
                lines.extend(self._render_markdown_table(aggregated))
                lines.append("")
        lines.extend(["## Analysis Details", ""])
        for step in all_steps_data:
            lines.extend(
                [
                    f"### {step.get('step_id', 'step')}",
                    "",
                    str(
                        step.get(
                            "analysis_summary", "No analysis summary was produced."
                        )
                    ),
                    "",
                ]
            )
        lines.extend(
            [
                "## Conclusion",
                "",
                "The workflow completed the available analysis steps and synthesized them into this report.",
                "",
                "## Sources",
                "",
                _source_summary(scoped_payload.get("sources", [])),
                "",
                "## Datasets",
                "",
                _dataset_summary(catalog),
                "",
                "## Schema",
                "",
                _schema_summary(scoped_payload.get("schemas", {})),
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _render_markdown_table(self, data: dict[str, Any]) -> list[str]:
        lines = ["| Metric | Value |", "| --- | --- |"]
        for key, value in data.items():
            rendered = _json_dumps(value) if isinstance(value, (dict, list)) else value
            lines.append(f"| {key} | {str(rendered).replace(chr(10), '<br>')} |")
        return lines


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
        remote_names = {definition.name for definition in runtime.mcp_tools}
        try:
            if tool_name in remote_names:
                if runtime.mcp_client is None:
                    raise RuntimeError("Method Hub MCP client is unavailable.")
                result = runtime.mcp_client.call_tool(tool_name, arguments)
                provider = "mcp"
            else:
                method = runtime.method_hub.get(tool_name)
                result = method(**arguments)
                provider = "local"
            status = "completed_no_data" if not _normalize_rows(result) else "completed"
            runtime.run_context.record_method_call(
                tool_name,
                status="completed",
                inputs=arguments,
                outputs={
                    "result": result,
                    "result_summary": self._result_summary(result),
                    "provider": provider,
                },
            )
            return {
                "schema_version": "1.0",
                "status": status,
                "tool_name": tool_name,
                "arguments": arguments,
                "raw_result": result,
                "error": None,
            }
        except Exception as exc:
            provider = "mcp" if tool_name in remote_names else "local"
            runtime.run_context.record_method_call(
                tool_name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc), "provider": provider},
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
        sample_data: Any,
    ) -> dict[str, Any]:
        interface.trust_level = "generated_validated"
        if runtime.interface_registry is not None:
            runtime.interface_registry.register(interface)
        if runtime.sandbox is None or runtime.run_artifact is None:
            return {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": interface.name,
                "raw_result": sample_data,
                "error": "Request sandbox is unavailable.",
            }
        arguments = code_spec.get("execution_arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        source_code = str(
            code_spec.get("source_code")
            or interface.metadata.get("source_code", "")
        )
        serialized_arguments = json.dumps(arguments, default=str)
        program = (
            "import json\n"
            f"{source_code}\n"
            f"result = {interface.name}("
            f"**json.loads({json.dumps(serialized_arguments)}))\n"
        )
        try:
            observation = runtime.sandbox.execute_python(
                program,
                runtime.run_artifact,
            )
            record_sandbox_method_calls(runtime, observation)
            if not observation.get("success"):
                raise RuntimeError(
                    observation.get("stderr") or "Generated tool execution failed."
                )
            result = observation.get("result")
            status = "completed_no_data" if not _normalize_rows(result) else "completed"
            runtime.run_context.record_method_call(
                interface.name,
                status="completed",
                inputs=arguments,
                outputs={
                    "result_summary": self._result_summary(result),
                    "provider": "sandbox",
                    "command_id": observation.get("command_id"),
                },
            )
            return {
                "schema_version": "1.0",
                "status": status,
                "tool_name": interface.name,
                "arguments": arguments,
                "raw_result": result,
                "error": None,
            }
        except Exception as exc:
            runtime.run_context.record_method_call(
                interface.name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc)},
            )
            return {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": interface.name,
                "arguments": arguments,
                "raw_result": sample_data,
                "error": str(exc),
            }

    def _result_summary(self, result: Any) -> dict[str, Any]:
        rows = _normalize_rows(result)
        return {"row_count": len(rows), "type": type(result).__name__}


class DataScienceProcessor:
    def __init__(
        self, agent: DataScienceAgent, max_inline_chart_rows: int = 100
    ) -> None:
        self.agent = agent
        self.max_inline_chart_rows = max_inline_chart_rows

    def process(
        self,
        step: dict[str, Any],
        execution_result: dict[str, Any],
        runtime: EngineRuntimeContext,
        template_requirements: list[dict[str, Any]],
        upstream_step_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw_data = execution_result.get("raw_result")
        rows = _normalize_rows(raw_data)
        step_id = str(step.get("step_id", "step"))
        artifact_ref = f"memory://report/{_safe_id(step_id)}"
        materialized = {
            "artifact_ref": artifact_ref,
            "schema": _infer_schema(rows),
            "profile": _profile_rows(rows, raw_data),
            "sample": deepcopy(rows[:10]),
            "execution_status": execution_result.get("status"),
            "execution_error": execution_result.get("error"),
        }
        if execution_result.get("status") in {"completed", "completed_no_data"}:
            runtime.run_context.add_artifact_ref(artifact_ref)
            if runtime.artifact_store is not None:
                try:
                    runtime.artifact_store.add(artifact_ref)
                except NotImplementedError:
                    pass
        decision = self.agent.run(
            step,
            materialized,
            upstream_step_results,
            template_requirements,
            raw_data,
        )
        if execution_result.get("status") == "failed":
            decision["status"] = "failed"
            decision.setdefault("warnings", []).append(
                str(execution_result.get("error"))
            )
        aggregated = decision.get("aggregated_data", {})
        metrics = [
            {
                "metric_id": f"{step_id}.{_safe_id(name)}",
                "name": str(name),
                "value": value,
                "evidence_refs": [artifact_ref],
            }
            for name, value in (
                aggregated.items() if isinstance(aggregated, dict) else []
            )
        ]
        chart_ids = sorted(
            {
                str(chart_id)
                for requirement in template_requirements
                for chart_id in requirement.get("consumer_chart_ids", [])
            }
        )
        chart_datasets = []
        if chart_ids:
            chart_datasets.append(
                {
                    "dataset_id": f"{step_id}-chart-data",
                    "for_chart_ids": chart_ids,
                    "shape": step.get("outputs", [{}])[0].get("shape", "table"),
                    "artifact_ref": f"{artifact_ref}/chart-data",
                    "schema": materialized["schema"],
                    "profile": materialized["profile"],
                    "data": deepcopy(rows[: self.max_inline_chart_rows]),
                    "truncated": len(rows) > self.max_inline_chart_rows,
                }
            )
        result = {
            "schema_version": "1.0",
            "status": decision.get("status", "completed"),
            "step_id": step_id,
            "step_result_artifact": materialized,
            "analysis": {
                "summary": decision.get("analysis_summary"),
                "observations": decision.get("observations", []),
            },
            "analysis_summary": decision.get("analysis_summary"),
            "aggregated_data": aggregated,
            "aggregated_metrics": metrics,
            "chart_datasets": chart_datasets,
            "warnings": decision.get("warnings", []),
            "lineage": {
                "source_refs": [item.get("ref") for item in step.get("inputs", [])],
                "upstream_step_refs": [
                    item.get("step_id") for item in upstream_step_results
                ],
                "tool_name": execution_result.get("tool_name"),
            },
        }
        runtime.run_context.record_step(
            "datascience_agent",
            status="failed" if result["status"] == "failed" else "completed",
            inputs={
                "step_id": step_id,
                "artifact_ref": artifact_ref,
                "profile": materialized["profile"],
            },
            outputs={
                "status": result["status"],
                "metric_count": len(metrics),
                "chart_dataset_count": len(chart_datasets),
            },
            artifact_refs=(
                [artifact_ref] if execution_result.get("status") != "failed" else []
            ),
        )
        return result


class ChartInputAssembler:
    def prepare(
        self,
        template_instance: dict[str, Any],
        data_step_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        bindings = {
            str(item.get("requirement_ref")): str(item.get("plan_output_ref"))
            for item in template_instance.get("bindings", [])
            if item.get("status") == "resolved"
        }
        results_by_step = {str(item.get("step_id")): item for item in data_step_results}
        ready = []
        fallbacks = []
        for section in template_instance.get("sections", []):
            for block in section.get("blocks", []):
                slot = block.get("chart_slot")
                if not isinstance(slot, dict):
                    continue
                chart_id = str(slot.get("chart_id"))
                result = None
                for requirement_ref in slot.get("data_requirement_refs", []):
                    output_ref = bindings.get(str(requirement_ref), "")
                    match = re.match(r"step-output://([^/]+)/", output_ref)
                    if match:
                        result = results_by_step.get(match.group(1))
                        if result is not None:
                            break
                dataset = None
                if result is not None:
                    dataset = next(
                        (
                            item
                            for item in result.get("chart_datasets", [])
                            if chart_id in item.get("for_chart_ids", [])
                        ),
                        None,
                    )
                    if dataset is None and result.get("step_result_artifact"):
                        artifact = result["step_result_artifact"]
                        dataset = {
                            "dataset_id": f"{result.get('step_id')}-fallback-data",
                            "artifact_ref": artifact.get("artifact_ref"),
                            "schema": artifact.get("schema", {}),
                            "profile": artifact.get("profile", {}),
                            "data": artifact.get("sample", []),
                        }
                request = {
                    "schema_version": "1.0",
                    "status": (
                        "ready"
                        if dataset and dataset.get("data")
                        else "insufficient_data"
                    ),
                    "chart_id": chart_id,
                    "intent": slot.get("intent"),
                    "suggested_type": slot.get("suggested_type"),
                    "allowed_types": slot.get("allowed_types", []),
                    "encoding_requirements": slot.get("encoding", {}),
                    "presentation": slot.get("presentation", {}),
                    "constraints": slot.get("constraints", {}),
                    "dataset": dataset or {},
                    "aggregated_metrics": (
                        result.get("aggregated_metrics", []) if result else []
                    ),
                    "fallback": slot.get("fallback", {"action": "table"}),
                }
                if request["status"] == "ready":
                    ready.append(request)
                else:
                    fallbacks.append(
                        {
                            "schema_version": "1.0",
                            "status": "insufficient_data",
                            "chart_id": chart_id,
                            "library": "echarts",
                            "selected_type": slot.get("suggested_type"),
                            "option": {},
                            "fallback": request["fallback"],
                            "warnings": ["No compatible chart dataset is available."],
                        }
                    )
        return ready, fallbacks


class ReportRenderer:
    def render(
        self,
        structured_report: dict[str, Any],
        legacy_markdown: str | None = None,
    ) -> list[dict[str, Any]]:
        markdown = legacy_markdown or self._markdown(structured_report)
        html = self._html(structured_report)
        return [
            {
                "schema_version": "1.0",
                "status": "completed",
                "format": "markdown",
                "media_type": "text/markdown",
                "content": markdown,
                "warnings": [],
            },
            {
                "schema_version": "1.0",
                "status": "completed",
                "format": "html",
                "media_type": "text/html",
                "content": html,
                "warnings": [],
            },
        ]

    def _markdown(self, report: dict[str, Any]) -> str:
        lines = [
            f"# {report.get('title', 'Data Intelligence Report')}",
            "",
            str(report.get("summary", "")),
            "",
        ]
        for section in report.get("sections", []):
            lines.extend(
                [f"## {section.get('title', section.get('section_id', 'Section'))}", ""]
            )
            for block in section.get("blocks", []):
                title = block.get("title")
                if title:
                    lines.extend([f"### {title}", ""])
                content = block.get("content", {})
                if block.get("type") in {"narrative", "recommendations"}:
                    lines.extend([str(content.get("text", "")), ""])
                elif block.get("type") == "kpi_group":
                    lines.extend(["| Metric | Value |", "| --- | ---: |"])
                    for metric in content.get("metrics", []):
                        lines.append(
                            f"| {metric.get('name')} | {metric.get('value')} |"
                        )
                    lines.append("")
                elif block.get("type") == "chart":
                    chart = content.get("chart") or {}
                    lines.extend(
                        [
                            f"Chart `{content.get('chart_id')}`: {chart.get('selected_type', 'fallback')}",
                            "",
                        ]
                    )
        return "\n".join(lines).strip() + "\n"

    def _html(self, report: dict[str, Any]) -> str:
        title = self._escape(str(report.get("title", "Data Intelligence Report")))
        body = [
            f"<h1>{title}</h1>",
            f"<p>{self._escape(str(report.get('summary', '')))}</p>",
        ]
        for section in report.get("sections", []):
            body.append(
                f"<section><h2>{self._escape(str(section.get('title', 'Section')))}</h2>"
            )
            for block in section.get("blocks", []):
                content = block.get("content", {})
                if block.get("type") in {"narrative", "recommendations"}:
                    body.append(f"<p>{self._escape(str(content.get('text', '')))}</p>")
                elif block.get("type") == "chart" and content.get("chart"):
                    chart_id = _safe_id(content.get("chart_id"))
                    option = json.dumps(
                        content["chart"].get("option", {}), ensure_ascii=False
                    ).replace("</", "<\\/")
                    body.append(f'<div id="{chart_id}" class="echarts-chart"></div>')
                    body.append(
                        f'<script type="application/json" data-chart-id="{chart_id}">{option}</script>'
                    )
            body.append("</section>")
        return (
            '<!doctype html><html><head><meta charset="utf-8"><title>'
            + title
            + "</title></head><body>"
            + "".join(body)
            + "</body></html>"
        )

    def _escape(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


class _DataStepState(TypedDict, total=False):
    step: dict[str, Any]
    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    runtime: EngineRuntimeContext
    template_requirements: list[dict[str, Any]]
    upstream_step_results: list[dict[str, Any]]
    route: dict[str, Any]
    attempt: int
    error_logs: str | None
    validation_feedback: str | None
    code_spec: dict[str, Any]
    interface: InterfaceDefinition
    sandbox_result: SandboxRunResult
    validation: dict[str, Any]
    execution_result: dict[str, Any]
    data_step_result: dict[str, Any]


class _ReportGraphState(TypedDict, total=False):
    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    runtime: EngineRuntimeContext
    user_context: UserContext | None
    plan: dict[str, Any]
    template_proposal: dict[str, Any]
    template_instance: dict[str, Any]
    previous_template_instance: dict[str, Any] | None
    template_feedback: list[dict[str, Any]]
    negotiation_iteration: int
    negotiation_status: str
    ready_steps: list[dict[str, Any]]
    completed_step_ids: Annotated[list[str], operator.add]
    data_step_results: Annotated[list[dict[str, Any]], operator.add]
    scheduler_warnings: Annotated[list[str], operator.add]
    step: dict[str, Any]
    upstream_step_results: list[dict[str, Any]]
    template_requirements: list[dict[str, Any]]
    chart_requests: list[dict[str, Any]]
    chart_request: dict[str, Any]
    chart_results: Annotated[list[dict[str, Any]], operator.add]
    structured_report: dict[str, Any]
    legacy_markdown: str | None
    rendered_reports: list[dict[str, Any]]
    final_result: Any


class ReportEngine:
    """LangGraph multi-agent report workflow with scoped planning and rendering."""

    name = "report"
    description = (
        "Structured multi-step report generation with planning, templates, "
        "data analysis, charts, validation, and rendered report artifacts."
    )

    def __init__(
        self,
        llm: object | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        max_generation_attempts: int = 2,
        max_negotiation_iterations: int = 3,
        max_data_concurrency: int = 4,
        max_chart_concurrency: int = 6,
        fallback_to_generation_on_tool_error: bool = True,
        template_pool: TemplatePool | None = None,
    ) -> None:
        self.llm = llm
        if self.llm is None:
            self.llm = self._try_build_openrouter_llm(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=config_manager,
            )
        self.max_generation_attempts = max(1, max_generation_attempts)
        self.max_negotiation_iterations = max(1, max_negotiation_iterations)
        self.max_data_concurrency = max(1, max_data_concurrency)
        self.max_chart_concurrency = max(1, max_chart_concurrency)
        self._data_semaphore = threading.BoundedSemaphore(self.max_data_concurrency)
        self._chart_semaphore = threading.BoundedSemaphore(self.max_chart_concurrency)
        self.fallback_to_generation_on_tool_error = fallback_to_generation_on_tool_error
        self.template_pool = template_pool or TemplatePool()
        self.plan_agent = PlanAgent(self.llm)
        self.template_agent = TemplateAgent(self.llm, self.template_pool)
        self.router_agent = RouterAgent(self.llm)
        self.code_agent = CodeAgent(self.llm)
        self.validator_agent = ValidatorAgent(self.llm)
        self.datascience_agent = DataScienceAgent(self.llm)
        self.chart_agent = ChartAgent(self.llm)
        self.report_agent = ReportAgent(self.llm)
        self.tool_executor = ToolExecutor()
        self.datascience_processor = DataScienceProcessor(self.datascience_agent)
        self.chart_input_assembler = ChartInputAssembler()
        self.renderer = ReportRenderer()
        self._data_step_graph = self._build_data_step_graph()
        self._graph = self._build_graph()

    def _try_build_openrouter_llm(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
    ) -> object | None:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        resolved_api_key = (
            api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        )
        resolved_model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_api_key or not resolved_model:
            return None
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=resolved_api_key,
            base_url=settings.base_url,
            model=resolved_model,
        )

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.intent == "report"

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        runtime.run_context.record_step(
            "report_workflow_start",
            inputs={
                "objective": spec.objective,
                "sources": _scope_from_spec(spec, corpus_package)["sources"],
                "orchestration": "langgraph",
            },
        )
        initial: _ReportGraphState = {
            "spec": spec,
            "corpus_package": corpus_package,
            "runtime": runtime,
            "user_context": user_context,
            "template_feedback": [],
            "negotiation_iteration": 0,
            "completed_step_ids": [],
            "data_step_results": [],
            "scheduler_warnings": [],
            "chart_results": [],
        }
        state = self._graph.invoke(
            initial,
            config={
                "recursion_limit": 100,
                "max_concurrency": max(
                    self.max_data_concurrency, self.max_chart_concurrency
                ),
            },
        )
        generation_mode = "langchain" if self.llm is not None else "fallback"
        spec_format = (
            spec.constraints.get("output_format")
            if isinstance(spec.constraints, dict)
            else None
        )
        report_format = spec_format or "markdown"
        scope = _scope_from_spec(spec, corpus_package)
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=state.get("final_result"),
            metadata={
                "sources": scope["sources"],
                "report_format": report_format,
                "generation_mode": generation_mode,
                "orchestration": "langgraph",
                "plan": state.get("plan", {}),
                "template_instance": state.get("template_instance", {}),
                "negotiation_status": state.get("negotiation_status"),
                "all_steps_data": state.get("data_step_results", []),
                "chart_results": state.get("chart_results", []),
                "structured_report": state.get("structured_report", {}),
                "rendered_reports": state.get("rendered_reports", []),
                "scheduler_warnings": state.get("scheduler_warnings", []),
                "workflow": [
                    "Plan Agent",
                    "Template Agent",
                    "DAG Scheduler",
                    "Router Agent",
                    "Code Agent",
                    "Sandbox",
                    "Validator Agent",
                    "Tool Executor",
                    "DataScience Processor",
                    "Chart Input Assembler",
                    "Chart Agent",
                    "Report Agent",
                    "Renderer",
                ],
            },
        )

    def _build_graph(self) -> Any:
        graph = StateGraph(_ReportGraphState)
        graph.add_node("plan", self._graph_plan)
        graph.add_node("template", self._graph_template)
        graph.add_node("negotiate", self._graph_negotiate)
        graph.add_node("schedule_data", self._graph_schedule_data)
        graph.add_node("run_data_step", self._graph_run_data_step)
        graph.add_node("prepare_charts", self._graph_prepare_charts)
        graph.add_node("run_chart", self._graph_run_chart)
        graph.add_node("compose_report", self._graph_compose_report)
        graph.add_node("render", self._graph_render)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "template")
        graph.add_edge("template", "negotiate")
        graph.add_conditional_edges("negotiate", self._negotiation_route)
        graph.add_conditional_edges("schedule_data", self._dispatch_ready_steps)
        graph.add_edge("run_data_step", "schedule_data")
        graph.add_conditional_edges("prepare_charts", self._dispatch_chart_tasks)
        graph.add_edge("run_chart", "compose_report")
        graph.add_edge("compose_report", "render")
        graph.add_edge("render", END)
        return graph.compile()

    def _build_data_step_graph(self) -> Any:
        graph = StateGraph(_DataStepState)
        graph.add_node("route", self._data_route)
        graph.add_node("execute_existing", self._data_execute_existing)
        graph.add_node("generate_code", self._data_generate_code)
        graph.add_node("validate_code", self._data_validate_code)
        graph.add_node("execute_generated", self._data_execute_generated)
        graph.add_node("generation_failed", self._data_generation_failed)
        graph.add_node("analyze", self._data_analyze)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            self._data_route_choice,
            {"existing": "execute_existing", "generate": "generate_code"},
        )
        graph.add_conditional_edges(
            "execute_existing",
            self._existing_execution_choice,
            {"analyze": "analyze", "generate": "generate_code"},
        )
        graph.add_edge("generate_code", "validate_code")
        graph.add_conditional_edges(
            "validate_code",
            self._validation_choice,
            {
                "execute": "execute_generated",
                "retry": "generate_code",
                "failed": "generation_failed",
            },
        )
        graph.add_edge("execute_generated", "analyze")
        graph.add_edge("generation_failed", "analyze")
        graph.add_edge("analyze", END)
        return graph.compile()

    def _graph_plan(self, state: _ReportGraphState) -> dict[str, Any]:
        plan = self.plan_agent.run(
            state["spec"],
            state["corpus_package"],
            state.get("plan"),
            state.get("template_feedback", []),
        )
        state["runtime"].run_context.record_step(
            "plan_agent",
            inputs={
                "execution_spec": _execution_spec_payload(state["spec"]),
                "template_feedback": state.get("template_feedback", []),
            },
            outputs={"plan": plan},
        )
        return {"plan": plan}

    def _graph_template(self, state: _ReportGraphState) -> dict[str, Any]:
        proposal = self.template_agent.run(
            state["spec"],
            state["plan"],
            state["corpus_package"],
            state.get("previous_template_instance"),
        )
        state["runtime"].run_context.record_step(
            "template_agent",
            inputs={"plan_revision": state["plan"].get("revision")},
            outputs={
                "status": proposal.get("status"),
                "selection": proposal.get("selection"),
                "missing_data_requests": proposal.get("missing_data_requests", []),
            },
        )
        return {
            "template_proposal": proposal,
            "template_instance": proposal["template_instance"],
            "previous_template_instance": proposal["template_instance"],
        }

    def _graph_negotiate(self, state: _ReportGraphState) -> dict[str, Any]:
        iteration = int(state.get("negotiation_iteration", 0)) + 1
        proposal_status = str(state["template_proposal"].get("status", "failed"))
        missing = state["template_proposal"].get("missing_data_requests", [])
        if (
            proposal_status == "needs_plan_revision"
            and iteration >= self.max_negotiation_iterations
        ):
            status = "iteration_limit_reached"
        else:
            status = proposal_status
        state["runtime"].run_context.record_step(
            "plan_template_negotiation",
            status=(
                "failed"
                if status in {"failed", "iteration_limit_reached"}
                else "completed"
            ),
            inputs={"iteration": iteration},
            outputs={"status": status, "missing_data_requests": missing},
        )
        return {
            "negotiation_iteration": iteration,
            "negotiation_status": status,
            "template_feedback": missing,
        }

    def _negotiation_route(self, state: _ReportGraphState) -> str:
        if (
            state.get("negotiation_status") == "needs_plan_revision"
            and int(state.get("negotiation_iteration", 0))
            < self.max_negotiation_iterations
        ):
            return "plan"
        return "schedule_data"

    def _graph_schedule_data(self, state: _ReportGraphState) -> dict[str, Any]:
        steps = state.get("plan", {}).get("steps", [])
        completed = set(state.get("completed_step_ids", []))
        remaining = [
            step for step in steps if str(step.get("step_id")) not in completed
        ]
        ready = [
            step
            for step in remaining
            if set(map(str, step.get("depends_on", []))).issubset(completed)
        ]
        warnings: list[str] = []
        skipped_results: list[dict[str, Any]] = []
        skipped_ids: list[str] = []
        if remaining and not ready:
            for step in remaining:
                step_id = str(step.get("step_id"))
                skipped_ids.append(step_id)
                skipped_results.append(
                    {
                        "schema_version": "1.0",
                        "status": "skipped",
                        "step_id": step_id,
                        "analysis_summary": "The step was skipped because its dependencies could not be resolved.",
                        "aggregated_data": {},
                        "aggregated_metrics": [],
                        "chart_datasets": [],
                        "warnings": ["Unresolved or cyclic dependency."],
                    }
                )
            warnings.append(
                "The scheduler skipped steps with unresolved or cyclic dependencies."
            )
        state["runtime"].run_context.record_step(
            "dag_scheduler",
            inputs={"completed_step_ids": sorted(completed)},
            outputs={
                "ready_step_ids": [step.get("step_id") for step in ready],
                "remaining_count": len(remaining),
                "skipped_step_ids": skipped_ids,
            },
        )
        return {
            "ready_steps": ready,
            "completed_step_ids": skipped_ids,
            "data_step_results": skipped_results,
            "scheduler_warnings": warnings,
        }

    def _dispatch_ready_steps(self, state: _ReportGraphState) -> str | list[Send]:
        ready = state.get("ready_steps", [])
        if not ready:
            return "prepare_charts"
        existing_results = state.get("data_step_results", [])
        template_instance = state.get("template_instance", {})
        sends = []
        for step in ready:
            dependencies = set(map(str, step.get("depends_on", [])))
            upstream = [
                item
                for item in existing_results
                if str(item.get("step_id")) in dependencies
            ]
            sends.append(
                Send(
                    "run_data_step",
                    {
                        "spec": state["spec"],
                        "corpus_package": state["corpus_package"],
                        "runtime": state["runtime"],
                        "step": step,
                        "upstream_step_results": upstream,
                        "template_requirements": self._template_requirements_for_step(
                            template_instance, step
                        ),
                    },
                )
            )
        return sends

    def _graph_run_data_step(self, state: _ReportGraphState) -> dict[str, Any]:
        with self._data_semaphore:
            result = self._data_step_graph.invoke(
                {
                    "step": state["step"],
                    "spec": state["spec"],
                    "corpus_package": state["corpus_package"],
                    "runtime": state["runtime"],
                    "template_requirements": state.get("template_requirements", []),
                    "upstream_step_results": state.get("upstream_step_results", []),
                    "attempt": 0,
                },
                config={"recursion_limit": 30},
            )
        data_result = result["data_step_result"]
        return {
            "completed_step_ids": [str(state["step"].get("step_id"))],
            "data_step_results": [data_result],
        }

    def _graph_prepare_charts(self, state: _ReportGraphState) -> dict[str, Any]:
        ready, fallbacks = self.chart_input_assembler.prepare(
            state.get("template_instance", {}), state.get("data_step_results", [])
        )
        state["runtime"].run_context.record_step(
            "chart_input_assembler",
            inputs={"data_step_count": len(state.get("data_step_results", []))},
            outputs={
                "ready_chart_count": len(ready),
                "fallback_chart_count": len(fallbacks),
            },
        )
        return {"chart_requests": ready, "chart_results": fallbacks}

    def _dispatch_chart_tasks(self, state: _ReportGraphState) -> str | list[Send]:
        requests = state.get("chart_requests", [])
        if not requests:
            return "compose_report"
        return [
            Send(
                "run_chart",
                {"chart_request": request, "runtime": state["runtime"]},
            )
            for request in requests
        ]

    def _graph_run_chart(self, state: _ReportGraphState) -> dict[str, Any]:
        with self._chart_semaphore:
            result = self.chart_agent.run(state["chart_request"])
        state["runtime"].run_context.record_step(
            "chart_agent",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "chart_id": state["chart_request"].get("chart_id"),
                "dataset_ref": state["chart_request"]
                .get("dataset", {})
                .get("artifact_ref"),
            },
            outputs={
                "status": result.get("status"),
                "selected_type": result.get("selected_type"),
            },
        )
        return {"chart_results": [result]}

    def _graph_compose_report(self, state: _ReportGraphState) -> dict[str, Any]:
        scoped = _scoped_corpus_payload(state["spec"], state["corpus_package"])
        output_format = (
            state["spec"].constraints.get("output_format")
            if isinstance(state["spec"].constraints, dict)
            else None
        )
        legacy_markdown = None
        if output_format != "structured_report":
            legacy_markdown = self.report_agent.run_markdown(
                state["spec"].objective,
                state.get("data_step_results", []),
                state["corpus_package"],
                scoped,
            )
            structured = self.report_agent._fallback_structured(
                state["spec"],
                state.get("template_instance", {}),
                state.get("data_step_results", []),
                state.get("chart_results", []),
                scoped,
            )
        else:
            structured = self.report_agent.run_structured(
                state["spec"],
                state.get("template_instance", {}),
                state.get("data_step_results", []),
                state.get("chart_results", []),
                scoped,
            )
        state["runtime"].run_context.record_step(
            "report_agent",
            inputs={
                "user_goal": state["spec"].objective,
                "data_step_count": len(state.get("data_step_results", [])),
                "chart_count": len(state.get("chart_results", [])),
            },
            outputs={
                "status": structured.get("status"),
                "report_format": "structured_report",
            },
        )
        return {"structured_report": structured, "legacy_markdown": legacy_markdown}

    def _graph_render(self, state: _ReportGraphState) -> dict[str, Any]:
        rendered = self.renderer.render(
            state["structured_report"], state.get("legacy_markdown")
        )
        output_format = (
            state["spec"].constraints.get("output_format")
            if isinstance(state["spec"].constraints, dict)
            else None
        )
        if output_format == "structured_report":
            final_result: Any = state["structured_report"]
        elif output_format == "html":
            final_result = next(
                item["content"] for item in rendered if item["format"] == "html"
            )
        else:
            final_result = next(
                item["content"] for item in rendered if item["format"] == "markdown"
            )
        state["runtime"].run_context.record_step(
            "renderer",
            inputs={"requested_format": output_format or "markdown"},
            outputs={"rendered_formats": [item["format"] for item in rendered]},
        )
        return {"rendered_reports": rendered, "final_result": final_result}

    def _template_requirements_for_step(
        self, template_instance: dict[str, Any], step: dict[str, Any]
    ) -> list[dict[str, Any]]:
        step_id = str(step.get("step_id"))
        requirement_ids = {
            str(binding.get("requirement_ref"))
            for binding in template_instance.get("bindings", [])
            if str(binding.get("plan_output_ref", "")).startswith(
                f"step-output://{step_id}/"
            )
        }
        chart_consumers: dict[str, list[str]] = {item: [] for item in requirement_ids}
        for section in template_instance.get("sections", []):
            for block in section.get("blocks", []):
                slot = block.get("chart_slot")
                if not isinstance(slot, dict):
                    continue
                for requirement_ref in slot.get("data_requirement_refs", []):
                    if str(requirement_ref) in chart_consumers:
                        chart_consumers[str(requirement_ref)].append(
                            str(slot.get("chart_id"))
                        )
        return [
            {
                "requirement_ref": requirement_id,
                "consumer_chart_ids": chart_consumers.get(requirement_id, []),
            }
            for requirement_id in sorted(requirement_ids)
        ]

    def _data_route(self, state: _DataStepState) -> dict[str, Any]:
        scope = _scope_from_spec(state["spec"], state["corpus_package"])
        inventory = _method_hub_payload(state["runtime"])
        route = self.router_agent.run(state["step"], inventory, scope["sources"])
        state["runtime"].run_context.record_step(
            "router_agent",
            inputs={"step_request": state["step"], "method_hub": inventory},
            outputs={"route": route},
        )
        return {"route": route}

    def _data_route_choice(self, state: _DataStepState) -> str:
        return (
            "existing"
            if state.get("route", {}).get("route") == "existing_tool"
            else "generate"
        )

    def _data_execute_existing(self, state: _DataStepState) -> dict[str, Any]:
        result = self.tool_executor.execute_existing(state["route"], state["runtime"])
        state["runtime"].run_context.record_step(
            "tool_executor",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "tool_name": result.get("tool_name"),
            },
            outputs={"status": result.get("status"), "error": result.get("error")},
        )
        return {"execution_result": result}

    def _existing_execution_choice(self, state: _DataStepState) -> str:
        if (
            state.get("execution_result", {}).get("status") == "failed"
            and self.fallback_to_generation_on_tool_error
        ):
            return "generate"
        return "analyze"

    def _data_generate_code(self, state: _DataStepState) -> dict[str, Any]:
        attempt = int(state.get("attempt", 0)) + 1
        scoped = _scoped_corpus_payload(state["spec"], state["corpus_package"])
        code_spec = self.code_agent.run(
            state["step"],
            scoped,
            error_logs=state.get("error_logs"),
            validation_feedback=state.get("validation_feedback"),
        )
        state["runtime"].run_context.record_step(
            "code_agent",
            inputs={
                "step_request": state["step"],
                "attempt": attempt,
                "error_logs": state.get("error_logs"),
                "validation_feedback": state.get("validation_feedback"),
            },
            outputs={"tool_name": code_spec.get("tool_name")},
        )
        return {"attempt": attempt, "code_spec": code_spec}

    def _data_validate_code(self, state: _DataStepState) -> dict[str, Any]:
        interface = self._build_generated_interface(state["step"], state["code_spec"])
        sandbox_result = self._validate_in_sandbox(interface, state["runtime"])
        state["runtime"].run_context.record_step(
            "sandbox_validate",
            status=sandbox_result.status,
            inputs={"interface": interface.name},
            outputs={"result": sandbox_result.result, "error": sandbox_result.error},
            artifact_refs=sandbox_result.artifact_refs,
            log_refs=sandbox_result.log_refs,
        )
        sandbox_logs = self._sandbox_logs(sandbox_result)
        validation = self.validator_agent.run(
            str(state["step"].get("description", "")),
            str(state["code_spec"].get("source_code", "")),
            sandbox_logs,
            sandbox_result.result,
        )
        passed = str(validation.get("status", "")).lower() == "pass"
        state["runtime"].run_context.record_step(
            "validator_agent",
            status="completed" if passed else "failed",
            inputs={"step_description": state["step"].get("description", "")},
            outputs={"validation": validation},
        )
        return {
            "interface": interface,
            "sandbox_result": sandbox_result,
            "validation": validation,
            "error_logs": sandbox_logs,
            "validation_feedback": str(validation.get("feedback", "")),
        }

    def _validation_choice(self, state: _DataStepState) -> str:
        if str(state.get("validation", {}).get("status", "")).lower() == "pass":
            return "execute"
        if int(state.get("attempt", 0)) < self.max_generation_attempts:
            return "retry"
        return "failed"

    def _data_execute_generated(self, state: _DataStepState) -> dict[str, Any]:
        result = self.tool_executor.execute_generated(
            state["interface"],
            state["code_spec"],
            state["runtime"],
            state["sandbox_result"].result,
        )
        state["runtime"].run_context.record_step(
            "tool_executor",
            status="failed" if result.get("status") == "failed" else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "tool_name": result.get("tool_name"),
            },
            outputs={"status": result.get("status"), "error": result.get("error")},
        )
        return {"execution_result": result}

    def _data_generation_failed(self, state: _DataStepState) -> dict[str, Any]:
        return {
            "execution_result": {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": state.get("code_spec", {}).get("tool_name"),
                "raw_result": (
                    state.get("sandbox_result").result
                    if state.get("sandbox_result")
                    else None
                ),
                "error": state.get("validation_feedback")
                or state.get("error_logs")
                or "Generated tool validation failed.",
            }
        }

    def _data_analyze(self, state: _DataStepState) -> dict[str, Any]:
        result = self.datascience_processor.process(
            state["step"],
            state["execution_result"],
            state["runtime"],
            state.get("template_requirements", []),
            state.get("upstream_step_results", []),
        )
        return {"data_step_result": result}

    def _build_generated_interface(
        self, step: dict[str, Any], code_spec: dict[str, Any]
    ) -> InterfaceDefinition:
        return InterfaceDefinition(
            name=str(
                code_spec.get("tool_name")
                or f"generated_{_safe_id(step.get('step_id'))}"
            ),
            description=str(step.get("description", "")),
            input_schema=code_spec.get("parameters_schema", {}),
            output_schema=code_spec.get("output_schema", {"type": "array"}),
            implementation_ref=str(code_spec.get("source_code", "")),
            source="generated",
            trust_level="generated_unvalidated",
            metadata={
                "capability_names": [GENERATED_TOOL_CAPABILITY],
                "source_code": code_spec.get("source_code", ""),
                "step_request": step,
            },
        )

    def _validate_in_sandbox(
        self, interface: InterfaceDefinition, runtime: EngineRuntimeContext
    ) -> SandboxRunResult:
        if runtime.sandbox_executor is None:
            return SandboxRunResult(
                status="failed", error="Sandbox executor is not configured."
            )
        try:
            return runtime.sandbox_executor.validate(interface, {}, None)
        except Exception as exc:
            return SandboxRunResult(status="failed", error=str(exc))

    def _sandbox_logs(self, sandbox_result: SandboxRunResult) -> str:
        if sandbox_result.status == "completed":
            return "Success"
        return f"Error: {sandbox_result.error or 'Sandbox validation failed.'}"
