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
    EngineOutput,
    ExecutionSpec,
    InterfaceDefinition,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult

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
7. Every report plan must materialize evidence that answers the user's actual objective.
   Structural metadata such as row, column, page, chunk, word, or character counts is
   supporting context and is never sufficient on its own.
8. For documents, preserve analyzable text and locations needed to identify subjects,
   claims, arguments, sections, relationships, and supporting excerpts. For tabular
   data, preserve values needed for objective-relevant distributions, comparisons,
   relationships, anomalies, and limitations.
9. Add filters only when they are explicitly requested or confirmed by the execution
   spec. Never invent a filter merely to make an analysis step appear specific.
10. Use semantic roles such as `goal_evidence`, `headline_metric`,
   `comparison_dimension`, and `primary_measure` on named outputs so template
   requirements can bind to them. Select headline metrics dynamically and include
   no more than four only when they materially help answer the objective.
   Preserve the `required` flag from template feedback and never promote an
   optional KPI or chart request to a required step.
11. Plan only data retrieval and computation. Do not add steps that write narratives,
   describe already-materialized results, generate charts, or render HTML/Markdown;
   downstream DataScience, Chart, Report, and Renderer components own those tasks.
12. Do not generate code or chart presentation options.
13. Local files are already staged and allowed. Never plan upload, registration, or
   catalog-mutation steps. Start by materializing each required source into
   analysis-ready JSON records while preserving its natural structure, such as
   table rows, spreadsheet rows, document pages, or text chunks.
14. Do not add a pass-through extraction step when an upstream materialization
    already contains readable values or text with source locations. Bind that
    materialized output directly as `goal_evidence`.

# OUTPUT
Return only a JSON object with `schema_version`, `plan_id`, `revision`, `objective`,
`scope`, `steps`, `request_resolutions`, and `warnings`. Each step must contain
`step_id`, `description`, `required`, `inputs`, `depends_on`, `required_data`,
`operation`, `outputs`, and `fallback`. Return exactly one request resolution for
each template feedback item, with `decision` set to `added`, `existing`, or
`rejected`, plus validated `output_refs` or a rejection `reason`.
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
3. Bind every requirement to one or more real named plan outputs whose shape and
   semantic roles satisfy the complete requirement.
4. Emit missing data requests for unresolved requirements.
5. Apply declared fallbacks to optional requirements that cannot be satisfied.
6. Prefer requirements that answer the user goal from actual content or values.
   Do not treat structural counts as a substitute for substantive evidence.
7. Headline metrics and charts are optional unless the selected data supports
   meaningful, objective-relevant choices. Never require fixed file-type KPIs.
8. Do not query data, execute tools, or create ECharts options.

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
2. `schema_catalog`: The scoped schema, catalog, and `resolved_inputs` contracts.
   It also declares the `sandbox_environment` and available Python packages.
   Anything absent is forbidden.
3. `error_logs`: Sandbox errors from the previous attempt.
4. `validation_feedback`: Validator feedback from the previous attempt.

# RULES
1. Never invent a table or column.
2. Honor filters, grouping, metrics, named outputs, and upstream input references.
   Preserve the content and value fields declared as objective evidence; do not
   replace them with a structural profile or introduce an undeclared filter.
3. Return an empty list only after the source was read successfully and contains
   no records. Never convert a missing dependency, unsupported format, file I/O
   error, or parser exception into an empty result; let that error propagate.
4. Include type hints and a complete docstring.
5. Do not perform network access outside the supplied data connection.
6. For Method Hub composition, import `call_tool` from `axiom_method_hub`.
7. Never open HTTP sockets or embed an MCP endpoint or token.
8. For every resolved upstream input, declare a matching parameter. Prefer the
   provided `argument_name`; when `sandbox_path` exists, declare an
   `<argument_name>_path` string parameter and load the JSON artifact from that path.
   Use `json_type`, `schema`, and `profile` exactly as declared; table artifacts
   are JSON arrays of row objects.
9. Put only source-file arguments in `execution_arguments`; the runtime binds
   upstream step outputs deterministically.
10. Return only JSON-serializable values. Never return raw bytes, open file handles,
   iterators, dataframes, or library-specific objects.
   For `table`, `time_series`, or `category_series` outputs, return a JSON array
   of row objects directly. Do not serialize rows into a JSON string nested inside
   another object.
11. Treat source and upstream files as read-only. Do not write files, launch
   subprocesses, mutate external state, or rely on nondeterministic side effects.
12. `tool_name` must be a valid Python identifier, and `source_code` must define
    one top-level function with exactly that name.
13. Import only standard-library modules or packages declared by
    `sandbox_environment.available_packages`. Never invent a package alias.

# OUTPUT
Return only JSON with `tool_name`, `parameters_schema`, `output_schema`,
`source_code`, and `execution_arguments`.
""".strip()

VALIDATOR_AGENT_PROMPT = """
You are the Validation Agent. Validate generated code against sandbox evidence
and the original PlanStep.

# INPUT
1. `step_description`: The complete validated PlanStep JSON.
2. `source_code`: Generated source code.
3. `sandbox_logs`: Syntax/runtime status.
4. `sample_data`: Bounded validation output.

# RULES
1. Fail syntax or runtime errors.
2. Check the output shape and fields.
3. Check that the code uses only allowed data.
4. Check that grouping and metrics match the PlanStep.
5. Give actionable feedback for a retry.
6. Do not rewrite the code. CodeAgent owns every revised generation attempt.
7. Do not demand a filter, calculation, field, or predicate absent from the
   PlanStep operation. A pass-through is valid when the PlanStep defines no more
   specific transformation.
8. Semantic roles describe the purpose of a named plan output. They are not
   required row-field names unless the PlanStep explicitly declares those fields.
9. Fail code that catches source loading/parsing errors and returns an empty
   collection. A read failure is not a valid no-data result.

# OUTPUT
Return only JSON with `status` (`Pass`, `Fail`, or `NeedsRevision`) and `feedback`.
""".strip()

DATASCIENCE_AGENT_PROMPT = """
You are the Data Science Agent. Interpret one materialized PlanStep result and
produce evidence-backed analysis for report and chart consumers.

# INPUT
1. `user_goal`: The confirmed report objective.
2. `step`: The validated PlanStep.
3. `materialized_result`: Artifact reference, deterministic schema, profile, and bounded sample.
4. `upstream_step_results`: Results declared as dependencies.
5. `template_requirements`: Template requirements bound to this step.

# RULES
1. Distinguish execution failure from a valid no-data result.
2. Never claim a cause that is not supported by evidence.
3. Do not recompute large datasets in the prompt.
4. Use authoritative tool results for metrics.
5. Interpret the actual content or values in the stratified sample against the
   user's objective and the bound template requirements. Lead with subjects,
   claims, comparisons, relationships, anomalies, or limitations that matter to
   that objective. Structural counts are secondary context, not the analysis.
6. Return detailed analysis, normalized metrics, and one chart dataset.
   Put only meaningful scalar headline metrics in `aggregated_data`, in desired
   display order, with no more than four per step. Do not manufacture a metric
   when a qualitative finding is more appropriate. Use explicit metric names
   and units; represent rates, shares, and coverage as percentages rather than
   ambiguous zero-to-one values.
7. Keep `analysis_summary` report-facing and insight-led. Support important
   observations with evidence references or source locations available in the
   sample. Never mention template IDs, output
   contracts, artifact names, downstream processing, or how a chart dataset
   could be constructed.
8. For a `goal_evidence` result, populate `report_content` with distinct content:
   - `executive_summary`: four to six sentences directly answering the objective.
   - `key_findings`: five to eight objects with `title`, a specific one-to-two
     sentence `statement`, and available `evidence_refs`.
   - `supporting_evidence`: four to eight objects with a specific `statement`
     and available `source_location` or `evidence_refs`.
   - `implications`: three to five objects with `title` and `statement` explaining
     why the findings matter without inventing recommendations.
   - `limitations`: only genuine coverage, extraction, or evidence limitations.
   Each section must add new information; never reuse a sentence across fields.
9. Produce `chart_data` whenever the evidence permits an honest comparison,
   distribution, trend, or ranked view. It must contain a concise `title` and
   two to forty `rows`, each using the normalized fields `category` and `value`.
   `value` must be numeric. For documents, prefer objective-relevant concepts,
   sections, entities, or evidence categories over characters by page. For
   tables, prefer a meaningful distribution, comparison, or trend from values.
   State bounded-sample coverage in `coverage`; do not present estimates as full
   corpus measurements.
10. State material sampling, extraction, or data-quality limits separately in
    warnings. Do not repeat one generic sentence across observations.

# OUTPUT
Return only JSON with `status`, `analysis_summary`, `observations`,
`aggregated_data`, `report_content`, `chart_data`, and optional `warnings`.
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
6. Answer the user's objective with substantive findings from actual content or
   values. Lead with the report's subject and most consequential insights.
   Treat row, column, page, chunk, word, and character counts as optional context;
   never let them replace content analysis or repeat them across blocks.
7. Use the supplied overview metrics and chart dataset. They are derived
   dynamically from the analyzed evidence and are not fixed template fields.
8. Write for report readers. Do not expose step IDs, artifact references,
   template contracts, agent workflow details, or downstream processing.
9. Respect each block's narrative-length instructions and avoid repetition.
10. Use the full observations, warnings, and source locations, not only
    `analysis_summary`. Give each block a distinct job: executive summary answers
    the objective, key findings enumerate separate insights, supporting evidence
    grounds those insights, and limitations state only genuine caveats or open
    questions. Never reuse the same sentence in more than one block and never
    write "the extracted goal evidence."

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
                    runtime.sandbox.sandbox.write(
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
                payload,
                spec,
                corpus_package,
                previous_plan,
                template_feedback or [],
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
        template_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        scope = _scope_from_spec(spec, corpus_package)
        allowed_tables = set(scope["tables"])
        allowed_vectors = set(scope["vector_collections"])
        normalized_steps = []
        ignored_steps = []
        seen: set[str] = set()
        for index, raw_step in enumerate(payload.get("steps", []), start=1):
            if not isinstance(raw_step, dict):
                continue
            raw_operation = raw_step.get("operation")
            operation_kind = str(
                raw_operation.get("kind")
                if isinstance(raw_operation, dict)
                else raw_operation or "analyze"
            ).lower()
            if _is_downstream_owned_step(raw_step, operation_kind):
                ignored_steps.append(str(raw_step.get("step_id", f"step-{index}")))
                continue
            required_data = raw_step.get("required_data", {})
            if not isinstance(required_data, dict):
                required_data = {}
            tables = [str(item) for item in _list_value(required_data.get("tables"))]
            vectors = [
                str(item)
                for item in _list_value(required_data.get("vector_collections"))
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
            columns = [str(item) for item in _list_value(required_data.get("columns"))]
            if tables and scope["columns"].get(tables[0]):
                columns = [
                    item for item in columns if item in scope["columns"][tables[0]]
                ]
            outputs = _normalize_plan_outputs(raw_step.get("outputs"), step_id)
            operation = raw_operation
            if not isinstance(operation, dict):
                operation = {"kind": str(operation or "analyze")}
            description = str(raw_step.get("description", ""))
            local_sources = [
                str(source)
                for source in scope["sources"]
                if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(source))
                and Path(str(source)).suffix
            ]
            source_materialization_step = (
                bool(local_sources)
                and not _list_value(raw_step.get("depends_on"))
                and (
                    operation_kind
                    in {
                        "inspect",
                        "inspect_data",
                        "materialize",
                        "read",
                        "read_source_content",
                    }
                    or any(
                        signal in description.lower()
                        for signal in (
                            "materialize",
                            "read source",
                            "read the source",
                            "load source",
                            "load the source",
                        )
                    )
                )
            )
            if source_materialization_step:
                for output in outputs:
                    if output.get("shape") not in {
                        "table",
                        "time_series",
                        "category_series",
                    }:
                        continue
                    roles = [
                        str(role) for role in _list_value(output.get("semantic_roles"))
                    ]
                    output["semantic_roles"] = list(
                        dict.fromkeys(roles + ["source_content", "goal_evidence"])
                    )
            source_registration_step = operation_kind.startswith(
                ("register", "upload")
            ) or any(
                signal in description.lower()
                for signal in ("register the", "register source", "upload")
            )
            if (
                local_sources
                and not _list_value(raw_step.get("depends_on"))
                and source_registration_step
            ):
                description = (
                    "Read and materialize analysis-ready content from the staged "
                    "local source. Preserve source-specific structure as JSON "
                    "records for downstream analysis."
                )
                operation = {
                    "kind": "read_source_content",
                    "parameters": {
                        "sources": local_sources,
                        "source_extensions": [
                            Path(source).suffix.lower() for source in local_sources
                        ],
                        "materialization": "json_records",
                    },
                }
            fallback = raw_step.get("fallback")
            if not isinstance(fallback, dict):
                fallback = {
                    "action": "complete_no_data",
                    "message": str(fallback or "No matching data was found."),
                }
            normalized_steps.append(
                {
                    **raw_step,
                    "step_id": step_id,
                    "description": description,
                    "required": bool(raw_step.get("required", True)),
                    "inputs": _normalize_plan_inputs(raw_step.get("inputs")),
                    "depends_on": [
                        str(item) for item in _list_value(raw_step.get("depends_on"))
                    ],
                    "required_data": {
                        **required_data,
                        "tables": tables,
                        "vector_collections": vectors,
                        "columns": columns,
                    },
                    "operation": operation,
                    "outputs": outputs,
                    "fallback": fallback,
                }
            )
        valid_step_ids = {step["step_id"] for step in normalized_steps}
        for step in normalized_steps:
            step["depends_on"] = [
                dependency
                for dependency in step["depends_on"]
                if dependency in valid_step_ids
            ]
        _bind_dependency_inputs(normalized_steps)
        warnings = [str(item) for item in _list_value(payload.get("warnings"))]
        if ignored_steps:
            warnings.append(
                "Ignored downstream-owned presentation steps: "
                + ", ".join(ignored_steps)
            )
        default_revision = (
            _int_value(
                (previous_plan or {}).get("revision"),
                0,
            )
            + 1
        )
        revision = _int_value(
            payload.get("revision"),
            default_revision,
        )
        request_resolutions = self._normalize_request_resolutions(
            payload.get("request_resolutions"),
            template_feedback or [],
            normalized_steps,
        )
        self._preserve_feedback_optionality(
            normalized_steps,
            request_resolutions,
            template_feedback or [],
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
            "request_resolutions": request_resolutions,
            "warnings": warnings,
        }

    @staticmethod
    def _preserve_feedback_optionality(
        steps: list[dict[str, Any]],
        resolutions: list[dict[str, Any]],
        feedback: list[dict[str, Any]],
    ) -> None:
        required_by_request = {
            str(item.get("request_id")): bool(item.get("required", False))
            for item in feedback
        }
        required_step_ids: set[str] = set()
        optional_step_ids: set[str] = set()
        for resolution in resolutions:
            target = (
                required_step_ids
                if required_by_request.get(str(resolution.get("request_id")))
                else optional_step_ids
            )
            for output_ref in resolution.get("output_refs", []):
                match = _STEP_OUTPUT_REF.match(str(output_ref))
                if match:
                    target.add(match.group(1))
        for step in steps:
            step_id = str(step.get("step_id"))
            if step_id in optional_step_ids and step_id not in required_step_ids:
                step["required"] = False

    @staticmethod
    def _normalize_request_resolutions(
        value: Any,
        feedback: list[dict[str, Any]],
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw_by_id = {
            str(item.get("request_id")): item
            for item in _list_value(value)
            if isinstance(item, dict) and item.get("request_id")
        }
        all_outputs = [
            (step, output)
            for step in steps
            for output in step.get("outputs", [])
            if isinstance(output, dict)
        ]
        valid_refs = {
            f"step-output://{step.get('step_id')}/{output.get('name')}"
            for step, output in all_outputs
        }
        resolutions = []
        for request in feedback:
            request_id = str(request.get("request_id", ""))
            raw = raw_by_id.get(request_id, {})
            output_refs = []
            for ref in _list_value(raw.get("output_refs")):
                if isinstance(ref, dict):
                    rendered = (
                        f"step-output://{ref.get('step_id')}/"
                        f"{ref.get('output_name')}"
                    )
                else:
                    rendered = str(ref)
                if rendered in valid_refs and rendered not in output_refs:
                    output_refs.append(rendered)
            if not output_refs:
                matches = _compatible_plan_outputs(request, all_outputs)
                output_refs = [
                    f"step-output://{step.get('step_id')}/{output.get('name')}"
                    for step, output in matches
                ]
            decision = str(raw.get("decision", "")).lower()
            if output_refs:
                decision = decision if decision in {"added", "existing"} else "added"
            else:
                decision = "rejected"
            resolutions.append(
                {
                    "request_id": request_id,
                    "requirement_ref": request.get("requirement_ref"),
                    "decision": decision,
                    "output_refs": output_refs,
                    "reason": (
                        None
                        if output_refs
                        else str(
                            raw.get("reason")
                            or "No compatible output can be produced from the allowed data."
                        )
                    ),
                }
            )
        return resolutions

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
        if not steps and scope["sources"]:
            source_extensions = [
                Path(str(source)).suffix.lower()
                for source in scope["sources"]
                if Path(str(source)).suffix
            ]
            steps.append(
                {
                    "step_id": "read-source-content",
                    "description": (
                        "Read and materialize analysis-ready content from the "
                        "selected source."
                    ),
                    "required": True,
                    "inputs": [
                        {
                            "ref": str(source),
                            "kind": "corpus_source",
                            "required": True,
                        }
                        for source in scope["sources"]
                    ],
                    "depends_on": [],
                    "required_data": {
                        "tables": [],
                        "vector_collections": [],
                        "columns": [],
                    },
                    "operation": {
                        "kind": "read_source_content",
                        "parameters": {
                            "sources": scope["sources"],
                            "source_extensions": source_extensions,
                            "materialization": "json_records",
                        },
                    },
                    "outputs": [
                        {
                            "name": "source-records",
                            "shape": "table",
                            "semantic_roles": [
                                "source_content",
                                "goal_evidence",
                            ],
                            "consumer_hints": ["analysis", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "complete_no_data",
                        "message": "The selected source contains no readable data.",
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
        request_resolutions = []
        has_available_data = bool(
            scope["sources"] or scope["tables"] or scope["vector_collections"]
        )
        for feedback in template_feedback:
            request_id = _safe_id(feedback.get("request_id", "template-data"))
            if request_id in existing_ids:
                step = next(item for item in steps if item["step_id"] == request_id)
                request_resolutions.append(
                    {
                        "request_id": feedback.get("request_id"),
                        "requirement_ref": feedback.get("requirement_ref"),
                        "decision": "existing",
                        "output_refs": [
                            f"step-output://{request_id}/{output.get('name')}"
                            for output in step.get("outputs", [])
                        ],
                        "reason": None,
                    }
                )
                continue
            if not has_available_data:
                request_resolutions.append(
                    {
                        "request_id": feedback.get("request_id"),
                        "requirement_ref": feedback.get("requirement_ref"),
                        "decision": "rejected",
                        "output_refs": [],
                        "reason": "No allowed source is available for this requirement.",
                    }
                )
                continue
            expected = feedback.get("expected_output", {})
            expected = expected if isinstance(expected, dict) else {}
            semantic = feedback.get("semantic_roles", {})
            output_roles = [
                str(role)
                for group in (
                    semantic.values() if isinstance(semantic, dict) else [semantic]
                )
                for role in _list_value(group)
            ]
            steps.append(
                {
                    "step_id": request_id,
                    "description": str(
                        feedback.get(
                            "description", "Prepare data requested by the template."
                        )
                    ),
                    "required": bool(feedback.get("required", False)),
                    "inputs": [
                        {
                            "ref": str(source),
                            "kind": "corpus_source",
                            "required": True,
                        }
                        for source in scope["sources"]
                    ],
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
                            "shape": expected.get("shape", "table"),
                            "semantic_roles": output_roles,
                            "consumer_hints": ["analysis", "chart", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "omit",
                        "message": "The template data request is unavailable.",
                    },
                }
            )
            existing_ids.add(request_id)
            request_resolutions.append(
                {
                    "request_id": feedback.get("request_id"),
                    "requirement_ref": feedback.get("requirement_ref"),
                    "decision": "added",
                    "output_refs": [f"step-output://{request_id}/{request_id}"],
                    "reason": None,
                }
            )
        return {
            "schema_version": "1.0",
            "plan_id": (previous_plan or {}).get("plan_id", "report-plan"),
            "revision": int((previous_plan or {}).get("revision", 0)) + 1,
            "objective": spec.objective,
            "scope": scope,
            "steps": steps,
            "request_resolutions": request_resolutions,
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
        previous_id = (
            previous_instance.get("template_id")
            if isinstance(previous_instance, dict)
            else None
        )
        source_template = self._source_template(corpus_package)
        selected_id = requested_id or previous_id or source_template
        selection_reason = (
            "The template was explicitly requested by the execution spec."
            if requested_id
            else (
                "The existing run-local template selection was preserved."
                if previous_id
                else "The template matches the selected source format."
            )
        )
        if selected_id is None and isinstance(payload, dict):
            selected_id = payload.get("template_id")
            selection_reason = str(
                payload.get("selection_reason", "Selected by TemplateAgent.")
            )
        valid_ids = {str(item.get("template_id")) for item in candidates}
        if selected_id not in valid_ids:
            selected_id, selection_reason = self._fallback_selection(
                spec, plan, corpus_package
            )
        definition = self.template_pool.get(str(selected_id))
        return self._materialize_instance(
            definition, plan, previous_instance, selection_reason
        )

    @staticmethod
    def _source_template(
        corpus_package: DataCorpusPackage,
    ) -> str | None:
        extensions = {
            Path(str(source)).suffix.lower()
            for source in corpus_package.sources
            if Path(str(source)).suffix
        }
        document_extensions = {
            ".pdf",
            ".txt",
            ".md",
            ".markdown",
            ".doc",
            ".docx",
            ".rtf",
        }
        tabular_extensions = {
            ".csv",
            ".tsv",
            ".xlsx",
            ".xls",
            ".parquet",
            ".jsonl",
        }
        if extensions & document_extensions:
            return "document-analysis"
        if extensions & tabular_extensions:
            return "data-profile"
        return None

    def _fallback_selection(
        self,
        spec: ExecutionSpec,
        plan: dict[str, Any],
        corpus_package: DataCorpusPackage,
    ) -> tuple[str, str]:
        source_template = self._source_template(corpus_package)
        if source_template == "document-analysis":
            return (
                source_template,
                "The selected source is a document-oriented file.",
            )
        if source_template == "data-profile":
            return (
                source_template,
                "The selected source is a tabular data file.",
            )
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
            matches = self._match_outputs(requirement, outputs)
            requirement_id = str(requirement.get("requirement_id"))
            if matches:
                output_refs = [
                    f"step-output://{step['step_id']}/{output['name']}"
                    for step, output in matches
                ]
                bindings.append(
                    {
                        "requirement_ref": requirement_id,
                        "plan_output_ref": output_refs[0],
                        "plan_output_refs": output_refs,
                        "status": "resolved",
                        "expected_output": deepcopy(
                            requirement.get("expected_output", {})
                        ),
                        "semantic_roles": deepcopy(
                            requirement.get("semantic_roles", {})
                        ),
                    }
                )
            else:
                expected = deepcopy(requirement.get("expected_output", {}))
                expected["semantic_roles"] = [
                    role
                    for group in _semantic_role_groups(requirement)
                    for role in sorted(group)
                ]
                missing.append(
                    {
                        "request_id": f"provide-{requirement_id}",
                        "requirement_ref": requirement_id,
                        "required": bool(requirement.get("required", False)),
                        "description": requirement.get(
                            "description", "Provide required template data."
                        ),
                        "expected_output": expected,
                        "semantic_roles": deepcopy(
                            requirement.get("semantic_roles", {})
                        ),
                        "calculation_hints": deepcopy(
                            requirement.get("calculation_hints", [])
                        ),
                        "reason": "No compatible named plan output exists.",
                    }
                )
                if not requirement.get("required", False):
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
        required_missing = any(item.get("required") for item in missing)
        status = "needs_plan_revision" if required_missing else "accepted"
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
                "status": "draft" if required_missing else status,
                "bindings": bindings,
                "sections": sections,
                "applied_fallbacks": applied_fallbacks,
                "warnings": [],
            },
            "missing_data_requests": missing,
            "warnings": [],
        }

    def _match_outputs(
        self,
        requirement: dict[str, Any],
        outputs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        return _compatible_plan_outputs(requirement, outputs)


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
                return self._normalize_route(
                    payload,
                    step_request,
                    method_hub,
                    sources,
                )
        return self._fallback_route(step_request, method_hub, sources)

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
        spreadsheet_source = _first_source_with_suffixes(
            sources,
            (".xls", ".xlsx", ".xlsm", ".xltx", ".xltm"),
        )
        spreadsheet_tool = next(
            (
                tool
                for tool in method_hub
                if tool["tool_name"] == "materialize_spreadsheet"
            ),
            None,
        )
        spreadsheet_operation_kinds = {
            "inspect",
            "inspect_data",
            "inspect_schema",
            "load",
            "load_excel",
            "load_spreadsheet",
            "materialize_excel",
            "materialize_source",
            "materialize_spreadsheet",
            "profile",
            "read",
            "read_excel",
            "read_spreadsheet",
        }
        if (
            spreadsheet_source
            and spreadsheet_tool is not None
            and operation_kind in spreadsheet_operation_kinds
        ):
            return {
                "route": "existing_tool",
                "tool_name": "materialize_spreadsheet",
                "arguments": {"path": spreadsheet_source},
                "reason": (
                    "The selected Excel workbook can be read by the built-in "
                    "spreadsheet materializer."
                ),
            }
        pdf_source = _first_source(sources, ".pdf")
        pdf_tool = next(
            (tool for tool in method_hub if tool["tool_name"] == "extract_pdf_text"),
            None,
        )
        pdf_operation_kinds = {
            "extract_document_text",
            "extract_pdf_text",
            "extract_text",
            "inspect_document",
            "load_document",
            "load_pdf",
            "parse_document",
            "parse_pdf_to_text",
            "read_document",
            "read_pdf",
            "segment_by_page",
            "split_pages",
        }
        if (
            pdf_source
            and pdf_tool is not None
            and operation_kind in pdf_operation_kinds
        ):
            return {
                "route": "existing_tool",
                "tool_name": "extract_pdf_text",
                "arguments": {"path": pdf_source},
                "reason": "The selected PDF can be read by the built-in PDF extractor.",
            }
        csv_source = _first_source(sources, ".csv")
        csv_inspection_kinds = {
            "inspect",
            "inspect_csv",
            "inspect_data",
            "inspect_schema",
            "load",
            "load_csv",
            "profile",
            "profile_csv",
            "read",
            "read_csv",
        }
        if (
            route.get("route") == "generate_tool"
            and csv_source
            and operation_kind in csv_inspection_kinds
        ):
            return self._fallback_route(step_request, method_hub, sources)
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
        if sources and "path" in properties:
            arguments["path"] = self._allowed_source(
                arguments.get("path"),
                sources,
            )
        if sources and "data_root" in properties and "data_root" in arguments:
            arguments["data_root"] = str(Path(sources[0]).parent)
        route["arguments"] = arguments
        return route

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
        spreadsheet_source = _first_source_with_suffixes(
            sources,
            (".xls", ".xlsx", ".xlsm", ".xltx", ".xltm"),
        )
        spreadsheet_tool = next(
            (
                tool
                for tool in method_hub
                if tool["tool_name"] == "materialize_spreadsheet"
            ),
            None,
        )
        if (
            spreadsheet_source
            and spreadsheet_tool is not None
            and operation_kind
            in {
                "inspect",
                "inspect_data",
                "inspect_schema",
                "load",
                "load_excel",
                "load_spreadsheet",
                "materialize_excel",
                "materialize_source",
                "materialize_spreadsheet",
                "profile",
                "read",
                "read_excel",
                "read_spreadsheet",
            }
        ):
            return {
                "route": "existing_tool",
                "tool_name": "materialize_spreadsheet",
                "arguments": {"path": spreadsheet_source},
                "reason": (
                    "The selected Excel workbook can be read by the built-in "
                    "spreadsheet materializer."
                ),
            }
        pdf_source = _first_source(sources, ".pdf")
        if pdf_source:
            pdf_tool = next(
                (
                    tool
                    for tool in method_hub
                    if tool["tool_name"] == "extract_pdf_text"
                ),
                None,
            )
            if pdf_tool is not None:
                return {
                    "route": "existing_tool",
                    "tool_name": "extract_pdf_text",
                    "arguments": {"path": pdf_source},
                    "reason": "The selected PDF can be read by the built-in PDF extractor.",
                }
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


class ChartAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("chart_agent", CHART_AGENT_PROMPT, llm)

    def run(self, chart_request: dict[str, Any]) -> dict[str, Any]:
        payload = self._invoke_json(chart_request=chart_request)
        if (
            isinstance(payload, dict)
            and payload.get("chart_id") == chart_request.get("chart_id")
            and isinstance(payload.get("option"), dict)
            and bool(payload.get("option"))
        ):
            if str(payload.get("status", "")).lower() in {
                "",
                "success",
                "completed",
            }:
                payload["status"] = "ready"
            payload.setdefault("library", "echarts")
            payload.setdefault("warnings", [])
            payload["option"] = self._polish_option(payload["option"])
            return payload
        return self._fallback_chart(chart_request)

    def _fallback_chart(self, request: dict[str, Any]) -> dict[str, Any]:
        datasets = [
            item
            for item in _list_value(request.get("datasets"))
            if isinstance(item, dict) and item.get("data")
        ]
        dataset = request.get("dataset", {})
        if not datasets and isinstance(dataset, dict) and dataset.get("data"):
            datasets = [dataset]
        dataset = datasets[0] if datasets else {}
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
        echarts_type = "bar" if chart_type == "stacked_bar" else chart_type
        encoding = request.get("encoding_requirements", {})
        x_role = str(encoding.get("x_role", ""))
        y_roles = [str(item) for item in _list_value(encoding.get("y_roles"))]
        series = []
        for index, source in enumerate(datasets):
            fields = [
                field.get("name")
                for field in source.get("schema", {}).get("fields", [])
            ]
            x_field = self._field_for_role(fields, x_role) or (
                fields[0] if fields else "category"
            )
            y_field = self._field_for_role(
                fields,
                y_roles[0] if y_roles else "",
                excluded={x_field},
            ) or (
                next((field for field in fields if field != x_field), x_field)
                if fields
                else "value"
            )
            item: dict[str, Any] = {
                "type": echarts_type,
                "datasetIndex": index,
                "name": str(source.get("dataset_id", f"series-{index + 1}")),
                "encode": {"x": x_field, "y": y_field},
                "itemStyle": {"color": "#137c8b"},
            }
            if chart_type in {"line", "area"}:
                item["smooth"] = True
                item["symbolSize"] = 7
                item["lineStyle"] = {"width": 3, "color": "#137c8b"}
            if chart_type == "area":
                item["type"] = "line"
                item["areaStyle"] = {"color": "rgba(19, 124, 139, 0.16)"}
            if chart_type == "stacked_bar":
                item["stack"] = "total"
            series.append(item)
        presentation = request.get("presentation", {})
        return {
            "schema_version": "1.0",
            "status": "ready",
            "chart_id": request.get("chart_id"),
            "library": "echarts",
            "selected_type": chart_type,
            "selection_reason": "The deterministic fallback used the template suggestion.",
            "dataset_refs": [item.get("artifact_ref") for item in datasets],
            "option": {
                "title": {
                    "text": presentation.get("title", request.get("intent", "Chart")),
                    "left": 0,
                    "textStyle": {
                        "color": "#182033",
                        "fontSize": 15,
                        "fontWeight": 600,
                    },
                },
                "tooltip": {
                    "trigger": "axis",
                    "backgroundColor": "#182033",
                    "borderWidth": 0,
                    "textStyle": {"color": "#ffffff"},
                },
                "grid": {
                    "left": 52,
                    "right": 24,
                    "top": 62,
                    "bottom": 48,
                    "containLabel": True,
                },
                "dataset": [{"source": item.get("data", [])} for item in datasets],
                "xAxis": {
                    "type": "category",
                    "name": presentation.get("x_axis_label", ""),
                    "axisLine": {"lineStyle": {"color": "#ccd3df"}},
                    "axisTick": {"show": False},
                    "axisLabel": {"color": "#697386"},
                },
                "yAxis": {
                    "type": "value",
                    "name": presentation.get("y_axis_label", ""),
                    "splitLine": {"lineStyle": {"color": "#e8ecf2"}},
                    "axisLabel": {"color": "#697386"},
                },
                "series": series,
            },
            "accessibility": {"summary": request.get("intent", "Data chart")},
            "warnings": [],
        }

    @staticmethod
    def _polish_option(option: dict[str, Any]) -> dict[str, Any]:
        polished = deepcopy(option)
        grid = polished.get("grid")
        if not isinstance(grid, dict):
            grid = {}
            polished["grid"] = grid
        grid.update(
            {
                "left": max(56, _int_value(grid.get("left"), 56)),
                "right": max(28, _int_value(grid.get("right"), 28)),
                "bottom": max(58, _int_value(grid.get("bottom"), 58)),
                "top": max(58, _int_value(grid.get("top"), 58)),
                "containLabel": True,
            }
        )
        polished.setdefault(
            "color",
            ["#137c8b", "#2f855a", "#c98518", "#c65d4b", "#526fc7"],
        )
        title = polished.get("title")
        if isinstance(title, dict):
            title.setdefault("left", 0)
            text_style = title.setdefault("textStyle", {})
            if isinstance(text_style, dict):
                text_style.update(
                    {"color": "#182033", "fontSize": 15, "fontWeight": 600}
                )
        tooltip = polished.setdefault("tooltip", {"trigger": "axis"})
        if isinstance(tooltip, dict):
            tooltip.setdefault("trigger", "axis")
        for axis_name in ("xAxis", "yAxis"):
            axes = polished.get(axis_name)
            axis_items = axes if isinstance(axes, list) else [axes]
            for axis in axis_items:
                if not isinstance(axis, dict):
                    continue
                axis.setdefault("nameLocation", "middle")
                axis.setdefault("nameGap", 34 if axis_name == "xAxis" else 46)
                axis_label = axis.setdefault("axisLabel", {})
                if isinstance(axis_label, dict):
                    axis_label.setdefault("color", "#697386")
                    categories = axis.get("data", [])
                    if (
                        axis_name == "xAxis"
                        and isinstance(categories, list)
                        and (
                            len(categories) > 6
                            or any(len(str(item)) > 14 for item in categories)
                        )
                    ):
                        axis_label["rotate"] = max(
                            28,
                            _int_value(axis_label.get("rotate"), 0),
                        )
                        polished["grid"]["bottom"] = max(
                            96,
                            _int_value(polished["grid"].get("bottom"), 58),
                        )
                    formatter = axis_label.get("formatter")
                    if isinstance(formatter, str) and (
                        "compactnumber" in formatter.lower()
                        or formatter.startswith("{value|")
                    ):
                        axis_label.pop("formatter", None)
        series_items = polished.get("series", [])
        if isinstance(series_items, dict):
            series_items = [series_items]
        for series in series_items if isinstance(series_items, list) else []:
            if not isinstance(series, dict):
                continue
            data = series.get("data", [])
            if isinstance(data, list) and len(data) > 15:
                label = series.setdefault("label", {})
                if isinstance(label, dict):
                    label["show"] = False
        return polished

    @staticmethod
    def _field_for_role(
        fields: list[Any],
        role: str,
        excluded: set[Any] | None = None,
    ) -> Any:
        excluded = excluded or set()
        normalized_role = re.sub(r"[^a-z0-9]+", "", role.lower())
        if not normalized_role:
            return None
        aliases = {
            "documentunit": {"documentunit", "page", "pagenumber", "chunk", "chunkid"},
            "charactercount": {"charactercount", "characters", "textlength", "length"},
            "fieldname": {"fieldname", "column", "columnname", "field"},
            "missingcount": {"missingcount", "nullcount", "missing", "nulls"},
        }
        accepted = aliases.get(normalized_role, {normalized_role})
        for field in fields:
            if field in excluded:
                continue
            normalized = re.sub(r"[^a-z0-9]+", "", str(field).lower())
            if normalized in accepted:
                return field
        return None


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
        fallback = self._fallback_structured(
            spec,
            template_instance,
            data_step_results,
            chart_results,
            scoped_payload,
        )
        payload = self._invoke_json_with_prompt(
            STRUCTURED_REPORT_AGENT_PROMPT,
            user_goal=spec.objective,
            template_instance=template_instance,
            data_step_results=data_step_results,
            chart_results=chart_results,
            source_summary={"sources": scoped_payload.get("sources", [])},
        )
        if isinstance(payload, dict) and isinstance(payload.get("sections"), list):
            return self._align_structured_payload(payload, fallback)
        return fallback

    @staticmethod
    def _align_structured_payload(
        payload: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        aligned = deepcopy(fallback)
        for key in ("title", "summary"):
            if payload.get(key) is not None:
                aligned[key] = payload[key]
        generated_sections = {
            str(section.get("section_id")): section
            for section in payload.get("sections", [])
            if isinstance(section, dict) and section.get("section_id")
        }
        used_generated_text: set[str] = set()
        for section in aligned.get("sections", []):
            generated = generated_sections.get(str(section.get("section_id")))
            if not generated:
                continue
            generated_blocks = {
                str(block.get("block_id")): block
                for block in generated.get("blocks", [])
                if isinstance(block, dict) and block.get("block_id")
            }
            for block in section.get("blocks", []):
                candidate = generated_blocks.get(str(block.get("block_id")))
                if not candidate or candidate.get("type") != block.get("type"):
                    continue
                if block.get("type") not in {"narrative", "recommendations"}:
                    continue
                block_title = str(block.get("title", "")).lower()
                if block.get("type") == "recommendations" or any(
                    token in block_title
                    for token in ("evidence", "limitation", "caveat")
                ):
                    continue
                content = candidate.get("content")
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    text = content["text"].strip()
                    normalized_text = re.sub(r"\s+", " ", text).lower()
                    if not text or normalized_text in used_generated_text:
                        continue
                    used_generated_text.add(normalized_text)
                    block["content"] = {"text": text}
                    block["status"] = str(candidate.get("status", block["status"]))
        aligned["warnings"] = ReportAgent._deduplicate_messages(
            [
                warning
                for warning in aligned.get("warnings", [])
                if ReportAgent._is_material_warning(warning)
            ]
        )
        return aligned

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
        binding_refs = {
            str(binding.get("requirement_ref")): [
                str(ref)
                for ref in (
                    _list_value(binding.get("plan_output_refs"))
                    or _list_value(binding.get("plan_output_ref"))
                )
                if str(ref)
            ]
            for binding in template_instance.get("bindings", [])
            if binding.get("status") == "resolved"
        }
        chart_by_id = {str(item.get("chart_id")): item for item in chart_results}
        sections = []
        for section in template_instance.get("sections", []):
            blocks = []
            for block in section.get("blocks", []):
                block_type = block.get("type")
                block_results = self._bound_results(
                    block,
                    binding_refs,
                    analysis_by_step,
                )
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
                    content = {"metrics": self._collect_metrics(block_results)}
                    if not content["metrics"]:
                        status = "no_data"
                elif block_type in {"narrative", "recommendations"}:
                    block_warnings = [
                        str(warning)
                        for item in block_results
                        for warning in item.get("warnings", [])
                        if self._is_material_warning(warning)
                    ]
                    text = self._report_text_for_block(
                        block,
                        block_results,
                        block_warnings,
                    )
                    content = {"text": text}
                    if not text:
                        status = "no_data"
                else:
                    content = {
                        "rows": [
                            item.get("aggregated_data", {}) for item in block_results
                        ]
                    }
                blocks.append(
                    {
                        "block_id": block.get("block_id"),
                        "type": block_type,
                        "title": block.get("title"),
                        "required": bool(block.get("required", False)),
                        "layout": deepcopy(block.get("layout", {})),
                        "status": status,
                        "content": content,
                        "evidence_refs": [
                            item.get("step_result_artifact", {}).get("artifact_ref")
                            for item in block_results
                            if item.get("step_result_artifact", {}).get("artifact_ref")
                        ],
                    }
                )
            sections.append(
                {
                    "section_id": section.get("section_id"),
                    "title": section.get("title"),
                    "purpose": section.get("purpose"),
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
        bound_step_ids = {
            match.group(1)
            for output_refs in binding_refs.values()
            for output_ref in output_refs
            if (match := _STEP_OUTPUT_REF.match(output_ref))
        }
        report_status = (
            "partial"
            if (
                template_instance.get("status") == "partial"
                or "failed" in statuses
                or "partial" in statuses
            )
            else "completed"
        )
        return {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": report_status,
            "title": spec.objective,
            "summary": " ".join(
                dict.fromkeys(
                    str(item.get("analysis_summary"))
                    for item in data_step_results
                    if item.get("analysis_summary")
                )
            )
            or "No data matched the confirmed scope.",
            "template": self._template_ref(template_instance),
            "sections": sections,
            "metrics": self._collect_metrics(data_step_results),
            "charts": chart_results,
            "sources": scoped_payload.get("sources", []),
            "data_scope": scoped_payload.get("scope", {}),
            "warnings": self._deduplicate_messages(
                [
                    warning
                    for item in data_step_results
                    if str(item.get("step_id")) in bound_step_ids
                    and item.get("status") not in {"completed_no_data", "failed"}
                    for warning in item.get("warnings", [])
                    if self._is_material_warning(warning)
                ]
            ),
        }

    @classmethod
    def _report_text_for_block(
        cls,
        block: dict[str, Any],
        results: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        block_id = str(block.get("block_id", "")).lower()
        title = str(block.get("title", "")).lower()
        identity = f"{block_id} {title}"
        contents = []
        for item in results:
            content = item.get("report_content") or item.get("analysis", {}).get(
                "report_content"
            )
            contents.append(content if isinstance(content, dict) else {})

        if any(token in identity for token in ("limitation", "caveat", "next")):
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("limitations"))
            ]
            if not values:
                values.extend(warnings)
            return cls._format_report_items(values)
        if any(token in identity for token in ("supporting-evidence", "evidence")):
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("supporting_evidence"))
            ]
            return cls._format_report_items(values, include_location=True)
        if any(
            token in identity
            for token in ("interpretation", "implication", "why-it-matters")
        ):
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("implications"))
            ]
            return cls._format_report_items(values)
        if any(token in identity for token in ("finding", "takeaway")):
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("key_findings"))
            ]
            if not values:
                values = [
                    observation
                    for item in results
                    for observation in item.get("analysis", {}).get("observations", [])
                ]
            return cls._format_report_items(values)

        summaries = [
            str(
                content.get("executive_summary") or result.get("analysis_summary") or ""
            ).strip()
            for result, content in zip(results, contents)
        ]
        summaries = [text for text in dict.fromkeys(summaries) if text]
        return "\n\n".join(summaries)

    @staticmethod
    def _format_report_items(
        values: list[Any],
        include_location: bool = False,
    ) -> str:
        lines = []
        normalized: set[str] = set()
        for value in values:
            if isinstance(value, str):
                text = value.strip()
            elif isinstance(value, dict):
                title = str(value.get("title") or "").strip()
                statement = str(
                    value.get("statement")
                    or value.get("text")
                    or value.get("content")
                    or ""
                ).strip()
                text = (
                    f"{title}: {statement}"
                    if title and statement
                    else (statement or title)
                )
                if include_location and text:
                    location = value.get("source_location")
                    if not location:
                        refs = [
                            str(ref)
                            for ref in _list_value(value.get("evidence_refs"))
                            if ref
                            and not str(ref).startswith(("artifact://", "memory://"))
                        ]
                        location = ", ".join(refs)
                    if location:
                        text = f"{text} Source: {location}."
            else:
                continue
            key = re.sub(r"\s+", " ", text).strip().lower()
            if not key or key in normalized:
                continue
            normalized.add(key)
            lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def _deduplicate_messages(values: list[Any]) -> list[str]:
        selected: list[str] = []
        token_sets: list[set[str]] = []
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if not text:
                continue
            tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", text.lower())
                if len(token) > 2
            }
            duplicate = False
            for existing, existing_tokens in zip(selected, token_sets):
                if text.lower() == existing.lower():
                    duplicate = True
                    break
                union = tokens | existing_tokens
                similarity = len(tokens & existing_tokens) / len(union) if union else 0
                if similarity >= 0.72:
                    duplicate = True
                    break
            if not duplicate:
                selected.append(text)
                token_sets.append(tokens)
        return selected

    @staticmethod
    def _bound_results(
        block: dict[str, Any],
        binding_refs: dict[str, list[str]],
        analysis_by_step: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        step_ids = []
        for requirement_ref in block.get("data_requirement_refs", []):
            for output_ref in binding_refs.get(str(requirement_ref), []):
                match = _STEP_OUTPUT_REF.match(output_ref)
                if match and match.group(1) not in step_ids:
                    step_ids.append(match.group(1))
        return [
            analysis_by_step[step_id]
            for step_id in step_ids
            if step_id in analysis_by_step
        ]

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
        candidates = [
            deepcopy(metric)
            for item in data_step_results
            for metric in item.get("aggregated_metrics", [])
            if self._is_display_metric(metric)
        ]
        deduplicated: dict[str, dict[str, Any]] = {}
        for metric in candidates:
            name = str(metric.get("name", ""))
            key = re.sub(r"[^a-z0-9]+", "", name.lower())
            if key not in deduplicated:
                deduplicated[key] = metric
        return list(deduplicated.values())[:8]

    @staticmethod
    def _is_display_metric(metric: Any) -> bool:
        if not isinstance(metric, dict):
            return False
        name = str(metric.get("name", "")).strip()
        value = metric.get("value")
        if not name or isinstance(value, (dict, list, tuple, set)):
            return False
        if isinstance(value, str) and len(value) > 80:
            return False
        lowered = name.lower()
        return not any(
            token in lowered
            for token in ("artifact", "source_path", "derived_from", "structure")
        )

    @staticmethod
    def _is_material_warning(value: Any) -> bool:
        warning = str(value or "").strip()
        if not warning:
            return False
        lowered = warning.lower()
        if any(
            token in lowered
            for token in (
                "error",
                "fail",
                "incomplete",
                "missing",
                "skipped",
                "truncat",
                "unavailable",
            )
        ):
            return True
        return not any(
            token in lowered
            for token in (
                "all pages are represented",
                "complete representation",
                "no missing data",
                "no null fields",
                "sample is complete",
                "success",
                "unresolved template requirements",
            )
        )

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
        try:
            if runtime.mcp_client is None:
                raise RuntimeError("Method Hub MCP client is unavailable.")
            result = runtime.mcp_client.call_tool(tool_name, arguments)
            status = "completed_no_data" if not _normalize_rows(result) else "completed"
            runtime.run_context.record_method_call(
                tool_name,
                status="completed",
                inputs=arguments,
                outputs={
                    "result": result,
                    "result_summary": self._result_summary(result),
                    "provider": "mcp",
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
        output_registry: _StepOutputRegistry,
        template_requirements: list[dict[str, Any]],
        upstream_step_results: list[dict[str, Any]],
        user_goal: str | None = None,
    ) -> dict[str, Any]:
        raw_data = execution_result.get("raw_result")
        rows = _normalize_rows(raw_data)
        step_id = str(step.get("step_id", "step"))
        output_descriptors: list[dict[str, Any]] = []
        materialization_warnings: list[str] = []
        if execution_result.get("status") in {"completed", "completed_no_data"}:
            output_descriptors, materialization_warnings = output_registry.register(
                step,
                raw_data,
                runtime,
            )
        artifact_ref = (
            str(output_descriptors[0]["artifact_ref"])
            if output_descriptors
            else f"memory://report/{_safe_id(step_id)}"
        )
        materialized = {
            "artifact_ref": artifact_ref,
            "outputs": output_descriptors,
            "schema": _infer_schema(rows),
            "profile": _profile_rows(rows, raw_data),
            "sample": self._analysis_sample(rows),
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
            user_goal,
        )
        decision = self._normalize_trusted_analysis(
            decision,
            execution_result,
            rows,
            raw_data,
            artifact_ref,
        )
        decision["report_content"] = self._normalize_report_content(decision)
        decision["aggregated_data"] = self._overview_aggregated_data(
            decision.get("aggregated_data"),
            rows,
        )
        if execution_result.get("status") == "failed":
            decision["status"] = "failed"
            decision.setdefault("warnings", []).append(
                str(execution_result.get("error"))
            )
        decision.setdefault("warnings", []).extend(materialization_warnings)
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
            chart_data = self._chart_dataset(decision, rows)
            chart_datasets.append(
                {
                    "dataset_id": f"{step_id}-chart-data",
                    "for_chart_ids": chart_ids,
                    "shape": "category_series",
                    "artifact_ref": f"{artifact_ref}/chart-data",
                    "title": chart_data["title"],
                    "coverage": chart_data["coverage"],
                    "semantic_roles": {
                        "comparison_dimension": "category",
                        "primary_measure": "value",
                    },
                    "schema": _infer_schema(chart_data["rows"]),
                    "profile": _profile_rows(
                        chart_data["rows"],
                        chart_data["rows"],
                    ),
                    "data": deepcopy(chart_data["rows"][: self.max_inline_chart_rows]),
                    "truncated": (len(chart_data["rows"]) > self.max_inline_chart_rows),
                }
            )
        result = {
            "schema_version": "1.0",
            "status": decision.get("status", "completed"),
            "step_id": step_id,
            "step_result_artifact": materialized,
            "data_outputs": output_descriptors,
            "analysis": {
                "summary": decision.get("analysis_summary"),
                "observations": decision.get("observations", []),
                "report_content": decision.get("report_content", {}),
            },
            "analysis_summary": decision.get("analysis_summary"),
            "report_content": decision.get("report_content", {}),
            "aggregated_data": aggregated,
            "aggregated_metrics": metrics,
            "chart_datasets": chart_datasets,
            "warnings": decision.get("warnings", []),
            "lineage": {
                "source_refs": [
                    item.get("ref")
                    for item in _normalize_plan_inputs(step.get("inputs"))
                    if item.get("ref")
                ],
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

    @staticmethod
    def _normalize_report_content(decision: dict[str, Any]) -> dict[str, Any]:
        supplied = decision.get("report_content")
        content = dict(supplied) if isinstance(supplied, dict) else {}
        observations = [
            item
            for item in _list_value(decision.get("observations"))
            if isinstance(item, dict) and item.get("statement")
        ]
        categorized: dict[str, list[dict[str, Any]]] = {
            "finding": [],
            "evidence": [],
            "implication": [],
            "limitation": [],
        }
        for observation in observations:
            category = str(
                observation.get("category") or observation.get("type") or "finding"
            ).lower()
            target = next(
                (name for name in categorized if name in category),
                "finding",
            )
            categorized[target].append(deepcopy(observation))

        def items(name: str, fallback: list[dict[str, Any]]) -> list[Any]:
            value = content.get(name)
            if isinstance(value, list):
                return [
                    deepcopy(item)
                    for item in value
                    if isinstance(item, (dict, str)) and str(item).strip()
                ]
            return fallback

        summary_value = (
            content.get("executive_summary") or decision.get("analysis_summary") or ""
        )
        if isinstance(summary_value, list):
            summary = " ".join(
                (
                    str(
                        item.get("statement")
                        or item.get("text")
                        or item.get("content")
                        or ""
                    ).strip()
                    if isinstance(item, dict)
                    else str(item).strip()
                )
                for item in summary_value
                if str(item).strip()
            )
        else:
            summary = str(summary_value).strip()
        return {
            "executive_summary": summary,
            "key_findings": items("key_findings", categorized["finding"]),
            "supporting_evidence": items(
                "supporting_evidence",
                categorized["evidence"],
            ),
            "implications": items("implications", categorized["implication"]),
            "limitations": items("limitations", categorized["limitation"]),
        }

    @classmethod
    def _overview_aggregated_data(
        cls,
        supplied: Any,
        rows: list[Any],
    ) -> dict[str, Any]:
        aggregated = supplied if isinstance(supplied, dict) else {}
        source_context = deepcopy(aggregated.get("source_context", {}))
        structural = cls._structural_overview_metrics(rows)
        selected: dict[str, Any] = {}
        normalized_names: set[str] = set()

        def add(name: str, value: Any) -> None:
            if len(selected) >= 4:
                return
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                return
            if isinstance(value, str) and (not value.strip() or len(value) > 80):
                return
            key = re.sub(r"[^a-z0-9]+", "", str(name).lower())
            if not key or key in normalized_names:
                return
            display_name = str(name)
            if (
                isinstance(value, (int, float))
                and 0 <= value <= 1
                and any(
                    token in str(name).lower()
                    for token in ("coverage", "rate", "ratio", "share")
                )
            ):
                display_name = (
                    str(name) if "percent" in str(name).lower() else f"{name}_percent"
                )
                value = value * 100
                key = re.sub(r"[^a-z0-9]+", "", display_name.lower())
            selected[display_name] = value
            normalized_names.add(key)

        for name, value in aggregated.items():
            if name == "source_context":
                continue
            trusted_value = structural.get(str(name), value)
            add(str(name), trusted_value)
        for name, value in structural.items():
            add(name, value)
        selected["source_context"] = source_context
        return selected

    @staticmethod
    def _structural_overview_metrics(rows: list[Any]) -> dict[str, int]:
        fields = {str(key) for row in rows if isinstance(row, dict) for key in row}
        text_values: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                text_values.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        for row in rows:
            collect(row)
        return {
            "record_count": len(rows),
            "field_count": len(fields),
            "word_count": sum(
                len(re.findall(r"\b[\w'-]+\b", value, flags=re.UNICODE))
                for value in text_values
            ),
            "character_count": sum(len(value) for value in text_values),
        }

    @classmethod
    def _chart_dataset(
        cls,
        decision: dict[str, Any],
        rows: list[Any],
    ) -> dict[str, Any]:
        chart_data = decision.get("chart_data")
        chart_data = chart_data if isinstance(chart_data, dict) else {}
        normalized_rows = []
        for row in _list_value(chart_data.get("rows")):
            if not isinstance(row, dict):
                continue
            category = row.get("category")
            value = row.get("value")
            if (
                category is None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                continue
            normalized_rows.append(
                {
                    "category": str(category)[:80],
                    "value": value,
                }
            )
        if len(normalized_rows) < 2:
            normalized_rows = cls._default_chart_rows(rows)
        return {
            "title": str(chart_data.get("title") or "Evidence distribution")[:120],
            "coverage": str(chart_data.get("coverage") or "materialized_result")[:200],
            "rows": normalized_rows[:40],
        }

    @classmethod
    def _default_chart_rows(cls, rows: list[Any]) -> list[dict[str, Any]]:
        dict_rows = [row for row in rows if isinstance(row, dict)]
        scalar_numbers = [
            value
            for value in rows
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if len(scalar_numbers) >= 2:
            return [
                {"category": str(index + 1), "value": value}
                for index, value in enumerate(scalar_numbers[:40])
            ]
        fields = list(dict.fromkeys(str(key) for row in dict_rows for key in row))
        numeric_fields = [
            field
            for field in fields
            if any(
                isinstance(row.get(field), (int, float))
                and not isinstance(row.get(field), bool)
                for row in dict_rows
            )
        ]
        dimension_fields = [
            field
            for field in numeric_fields
            if any(
                token in field.lower()
                for token in ("page", "index", "position", "year", "month", "day")
            )
        ]
        if dimension_fields and len(numeric_fields) > 1:
            dimension = dimension_fields[0]
            measure = next(field for field in numeric_fields if field != dimension)
            return [
                {
                    "category": str(row.get(dimension)),
                    "value": row.get(measure),
                }
                for row in dict_rows[:40]
                if row.get(dimension) is not None
                and isinstance(row.get(measure), (int, float))
                and not isinstance(row.get(measure), bool)
            ]

        short_text_fields = [
            field
            for field in fields
            if field not in numeric_fields
            and any(isinstance(row.get(field), str) for row in dict_rows)
            and (
                sum(
                    len(str(row.get(field, "")))
                    for row in dict_rows
                    if isinstance(row.get(field), str)
                )
                / max(
                    1,
                    sum(isinstance(row.get(field), str) for row in dict_rows),
                )
            )
            <= 80
        ]
        preferred_dimensions = [
            field
            for field in short_text_fields
            if not field.lower().endswith("id") and "_id" not in field.lower()
        ] or short_text_fields
        if numeric_fields and preferred_dimensions:
            dimension = preferred_dimensions[0]
            measure = numeric_fields[0]
            grouped: dict[str, float] = {}
            for row in dict_rows:
                category = row.get(dimension)
                value = row.get(measure)
                if (
                    category is not None
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    key = str(category)[:80]
                    grouped[key] = grouped.get(key, 0.0) + float(value)
            if grouped:
                return [
                    {"category": category, "value": value}
                    for category, value in sorted(
                        grouped.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:12]
                ]
        if preferred_dimensions:
            frequencies = Counter(
                str(row.get(preferred_dimensions[0]))[:80]
                for row in dict_rows
                if row.get(preferred_dimensions[0]) not in (None, "")
            )
            if len(frequencies) >= 2:
                return [
                    {"category": category, "value": count}
                    for category, count in frequencies.most_common(12)
                ]
        return cls._term_frequency_chart_rows(rows)

    @staticmethod
    def _term_frequency_chart_rows(rows: list[Any]) -> list[dict[str, Any]]:
        stopwords = {
            "about",
            "after",
            "also",
            "and",
            "are",
            "been",
            "being",
            "between",
            "can",
            "could",
            "data",
            "does",
            "each",
            "for",
            "from",
            "had",
            "has",
            "have",
            "into",
            "its",
            "more",
            "not",
            "only",
            "other",
            "should",
            "such",
            "than",
            "that",
            "the",
            "their",
            "then",
            "there",
            "these",
            "they",
            "this",
            "those",
            "through",
            "using",
            "was",
            "were",
            "which",
            "with",
            "would",
        }
        texts: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        collect(rows)
        tokens = Counter(
            token
            for text in texts
            for token in re.findall(r"[^\W\d_]{4,}", text.lower(), re.UNICODE)
            if token not in stopwords
        )
        return [
            {"category": token, "value": count}
            for token, count in tokens.most_common(10)
        ]

    @staticmethod
    def _analysis_sample(
        rows: list[Any],
        max_rows: int = 12,
        max_string_chars: int = 6000,
    ) -> list[Any]:
        if not rows:
            return []
        if len(rows) <= max_rows:
            selected = rows
        elif max_rows == 1:
            selected = [rows[0]]
        else:
            indices = {
                round(index * (len(rows) - 1) / (max_rows - 1))
                for index in range(max_rows)
            }
            selected = [rows[index] for index in sorted(indices)]

        def bounded(value: Any) -> Any:
            if isinstance(value, str) and len(value) > max_string_chars:
                if max_string_chars < 600:
                    return value[:max_string_chars] + "... [sample truncated]"
                segment_count = 6
                segment_size = max_string_chars // segment_count
                starts = {
                    round(index * (len(value) - segment_size) / (segment_count - 1))
                    for index in range(segment_count)
                }
                return "\n... [sample gap] ...\n".join(
                    value[start : start + segment_size] for start in sorted(starts)
                )
            if isinstance(value, dict):
                return {str(key): bounded(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                if len(value) <= 50:
                    selected_items = value
                else:
                    indices = {
                        round(index * (len(value) - 1) / 49) for index in range(50)
                    }
                    selected_items = [value[index] for index in sorted(indices)]
                return [bounded(item) for item in selected_items]
            return deepcopy(value)

        return [bounded(row) for row in selected]

    @staticmethod
    def _normalize_trusted_analysis(
        decision: dict[str, Any],
        execution_result: dict[str, Any],
        rows: list[Any],
        raw_data: Any,
        artifact_ref: str,
    ) -> dict[str, Any]:
        normalized = dict(decision)
        normalized["status"] = "completed" if rows else "completed_no_data"
        aggregated = normalized.get("aggregated_data", {})
        if not isinstance(aggregated, dict):
            aggregated = {}
        source_context = {"record_count": len(rows)}
        if isinstance(raw_data, dict):
            source_context.update(
                {
                    str(key): value
                    for key, value in raw_data.items()
                    if isinstance(value, (int, float, bool, str))
                    and len(str(value)) <= 200
                }
            )
        truncated_record_count = sum(
            row.get("truncated") is True for row in rows if isinstance(row, dict)
        )
        if any(isinstance(row, dict) and "truncated" in row for row in rows):
            source_context["truncated_record_count"] = truncated_record_count
        aggregated["source_context"] = source_context
        normalized["aggregated_data"] = aggregated

        warnings = [
            str(warning)
            for warning in _list_value(normalized.get("warnings"))
            if str(warning)
            and not ("truncat" in str(warning).lower() and truncated_record_count == 0)
        ]
        if truncated_record_count:
            warnings.append(
                f"{truncated_record_count} source records reached an extraction limit."
            )
        normalized["warnings"] = list(dict.fromkeys(warnings))

        observations = [
            item
            for item in _list_value(normalized.get("observations"))
            if isinstance(item, dict)
        ]
        if truncated_record_count:
            observations.append(
                {
                    "observation_id": "source-truncation-count",
                    "statement": (
                        f"{truncated_record_count} source records reached an "
                        "extraction limit."
                    ),
                    "evidence_refs": [artifact_ref],
                }
            )
        normalized["observations"] = observations
        return normalized


class ChartInputAssembler:
    def prepare(
        self,
        template_instance: dict[str, Any],
        data_step_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        bindings = {}
        for item in template_instance.get("bindings", []):
            if item.get("status") != "resolved":
                continue
            refs = [
                str(ref)
                for ref in (
                    _list_value(item.get("plan_output_refs"))
                    or _list_value(item.get("plan_output_ref"))
                )
                if str(ref)
            ]
            bindings[str(item.get("requirement_ref"))] = refs
        results_by_step = {str(item.get("step_id")): item for item in data_step_results}
        ready = []
        fallbacks = []
        for section in template_instance.get("sections", []):
            for block in section.get("blocks", []):
                slot = block.get("chart_slot")
                if not isinstance(slot, dict):
                    continue
                chart_id = str(slot.get("chart_id"))
                requirement_refs = [
                    str(item) for item in slot.get("data_requirement_refs", [])
                ]
                unresolved_requirements = [
                    item for item in requirement_refs if not bindings.get(item)
                ]
                output_refs = list(
                    dict.fromkeys(
                        output_ref
                        for requirement_ref in requirement_refs
                        for output_ref in bindings.get(requirement_ref, [])
                    )
                )
                datasets = []
                results = []
                unresolved_outputs = []
                empty_outputs = []
                for output_ref in output_refs:
                    match = _STEP_OUTPUT_REF.match(output_ref)
                    result = results_by_step.get(match.group(1)) if match else None
                    if result is None:
                        unresolved_outputs.append(output_ref)
                        continue
                    dataset = self._dataset_for_output(
                        result,
                        output_ref,
                        chart_id,
                    )
                    if not dataset:
                        unresolved_outputs.append(output_ref)
                        continue
                    if not dataset.get("data"):
                        empty_outputs.append(output_ref)
                        continue
                    if dataset.get("artifact_ref") not in {
                        item.get("artifact_ref") for item in datasets
                    }:
                        datasets.append(dataset)
                    if result not in results:
                        results.append(result)
                complete = (
                    not unresolved_requirements
                    and not unresolved_outputs
                    and not empty_outputs
                    and bool(datasets)
                )
                presentation = deepcopy(slot.get("presentation", {}))
                if datasets and datasets[0].get("title"):
                    presentation["title"] = datasets[0]["title"]
                request = {
                    "schema_version": "1.0",
                    "status": "ready" if complete else "insufficient_data",
                    "chart_id": chart_id,
                    "intent": slot.get("intent"),
                    "suggested_type": slot.get("suggested_type"),
                    "allowed_types": slot.get("allowed_types", []),
                    "encoding_requirements": slot.get("encoding", {}),
                    "presentation": presentation,
                    "constraints": slot.get("constraints", {}),
                    "dataset": datasets[0] if datasets else {},
                    "datasets": datasets,
                    "dataset_refs": [item.get("artifact_ref") for item in datasets],
                    "aggregated_metrics": [
                        metric
                        for result in results
                        for metric in result.get("aggregated_metrics", [])
                    ],
                    "fallback": slot.get("fallback", {"action": "table"}),
                    "warnings": [
                        *(
                            [
                                "Unresolved template requirements: "
                                + ", ".join(unresolved_requirements)
                            ]
                            if unresolved_requirements
                            else []
                        ),
                        *(
                            [
                                "Unavailable plan outputs: "
                                + ", ".join(unresolved_outputs)
                            ]
                            if unresolved_outputs
                            else []
                        ),
                        *(
                            [
                                "Plan outputs contain no chartable rows: "
                                + ", ".join(empty_outputs)
                            ]
                            if empty_outputs
                            else []
                        ),
                    ],
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
                            "warnings": request["warnings"]
                            or ["No compatible chart dataset is available."],
                        }
                    )
        return ready, fallbacks

    @staticmethod
    def _dataset_for_output(
        result: dict[str, Any],
        output_ref: str,
        chart_id: str,
    ) -> dict[str, Any] | None:
        chart_dataset = next(
            (
                item
                for item in result.get("chart_datasets", [])
                if chart_id in item.get("for_chart_ids", [])
            ),
            None,
        )
        if chart_dataset is not None:
            return deepcopy(chart_dataset)
        match = _STEP_OUTPUT_REF.match(output_ref)
        output_name = match.group(2) if match else None
        descriptor = next(
            (
                item
                for item in result.get("data_outputs", [])
                if item.get("output_name") == output_name
            ),
            {},
        )
        artifact = result.get("step_result_artifact")
        if not isinstance(artifact, dict):
            return None
        return {
            "dataset_id": (
                f"{result.get('step_id')}-{_safe_id(output_name or 'data')}"
            ),
            "output_ref": output_ref,
            "artifact_ref": descriptor.get(
                "artifact_ref", artifact.get("artifact_ref")
            ),
            "schema": descriptor.get("schema", artifact.get("schema", {})),
            "profile": descriptor.get("profile", artifact.get("profile", {})),
            "data": deepcopy(artifact.get("sample", [])),
        }


class ReportRenderer:
    def render(
        self,
        structured_report: dict[str, Any],
        legacy_markdown: str | None = None,
    ) -> list[dict[str, Any]]:
        markdown = legacy_markdown or self._markdown(structured_report)
        css = self._css()
        javascript = self._javascript()
        html = self._html(structured_report, css, javascript)
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
                "format": "css",
                "media_type": "text/css",
                "content": css,
                "warnings": [],
            },
            {
                "schema_version": "1.0",
                "status": "completed",
                "format": "javascript",
                "media_type": "application/javascript",
                "content": javascript,
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

    def _html(
        self,
        report: dict[str, Any],
        css: str,
        javascript: str,
    ) -> str:
        title = self._escape(str(report.get("title", "Data Intelligence Report")))
        template = report.get("template", {})
        template_name = self._display_name(
            str(template.get("template_id") or "data intelligence")
        )
        status = self._display_name(str(report.get("status", "completed")))
        summary = self._compact_text(str(report.get("summary", "")))
        body = [
            '<main class="report-shell">',
            '<header class="report-header">',
            '<div class="report-meta">',
            f'<span class="report-type">{self._escape(template_name)} report</span>',
            f'<span class="status-pill">{self._escape(status)}</span>',
            "</div>",
            f"<h1>{title}</h1>",
            f'<p class="report-summary">{self._escape(summary)}</p>',
            "</header>",
        ]
        for section in report.get("sections", []):
            rendered_blocks = []
            for block in section.get("blocks", []):
                if block.get("status") == "no_data" and not block.get(
                    "required", False
                ):
                    continue
                content = block.get("content", {})
                block_type = str(block.get("type", "content"))
                title_text = block.get("title")
                layout = block.get("layout", {})
                span = _int_value(
                    layout.get("span"),
                    12 if block_type in {"kpi_group", "table"} else 6,
                )
                span = min(12, max(1, span))
                emphasis = _safe_id(layout.get("emphasis", "standard"))
                block_body = []
                if title_text:
                    block_body.append(
                        '<div class="block-heading">'
                        f"<h3>{self._escape(str(title_text))}</h3>"
                        "</div>"
                    )
                if block_type == "narrative":
                    text = self._compact_text(
                        str(content.get("text", "")),
                        max_sentences=8,
                        max_chars=1800,
                    )
                    if not text:
                        continue
                    for paragraph in self._paragraphs(text):
                        block_body.append(f"<p>{self._escape(paragraph)}</p>")
                elif block_type == "recommendations":
                    raw_text = str(content.get("text", ""))
                    line_items = [
                        re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
                        for line in raw_text.splitlines()
                        if line.strip()
                    ]
                    items = (
                        line_items[:8]
                        if len(line_items) > 1
                        else self._sentences(raw_text)[:6]
                    )
                    if not items:
                        continue
                    block_body.append('<ul class="takeaway-list">')
                    for item in items:
                        block_body.append(
                            '<li><span class="takeaway-marker"></span>'
                            f"<span>{self._escape(item)}</span></li>"
                        )
                    block_body.append("</ul>")
                elif block_type == "kpi_group":
                    metrics = content.get("metrics", [])[:4]
                    if not metrics:
                        continue
                    block_body.append('<dl class="kpi-grid">')
                    for index, metric in enumerate(metrics):
                        block_body.extend(
                            [
                                f'<div class="kpi-item kpi-accent-{(index % 4) + 1}">',
                                "<dt>"
                                + self._escape(
                                    self._display_name(
                                        str(metric.get("name", "Metric"))
                                    )
                                )
                                + "</dt>",
                                "<dd>"
                                + self._escape(
                                    self._format_metric_value(
                                        metric.get("value"),
                                        str(metric.get("name", "")),
                                    )
                                )
                                + "</dd>",
                                "</div>",
                            ]
                        )
                    block_body.append("</dl>")
                elif block_type == "table":
                    rows = content.get("rows", [])
                    block_body.append(self._table_html(rows))
                elif block_type == "chart" and content.get("chart"):
                    chart_id = _safe_id(content.get("chart_id"))
                    chart = content["chart"]
                    if chart.get("option"):
                        option = json.dumps(
                            chart.get("option", {}), ensure_ascii=False
                        ).replace("</", "<\\/")
                        block_body.append(
                            f'<div id="{chart_id}" class="echarts-chart" '
                            'role="img" aria-label="Data chart"></div>'
                        )
                        block_body.append(
                            '<script type="application/json" '
                            f'data-chart-id="{chart_id}">{option}</script>'
                        )
                    else:
                        fallback = chart.get("fallback") or content.get("fallback", {})
                        if fallback.get("action") == "omit" and not block.get(
                            "required", False
                        ):
                            continue
                        block_body.append(
                            '<p class="chart-fallback">'
                            + self._escape(
                                str(
                                    fallback.get("message")
                                    or "Chart data is unavailable."
                                )
                            )
                            + "</p>"
                        )
                if not block_body:
                    continue
                rendered_blocks.append(
                    '<div class="report-block '
                    f"report-block-{_safe_id(block_type)} "
                    f'block-{emphasis}" style="--block-span:{span}">'
                    + "".join(block_body)
                    + "</div>"
                )
            if not rendered_blocks:
                continue
            density = _safe_id(section.get("layout", {}).get("density", "comfortable"))
            body.extend(
                [
                    f'<section class="report-section density-{density}">',
                    '<div class="section-heading">',
                    f"<h2>{self._escape(str(section.get('title', 'Section')))}</h2>",
                    (
                        f"<p>{self._escape(str(section.get('purpose')))}</p>"
                        if section.get("purpose")
                        else ""
                    ),
                    "</div>",
                    '<div class="section-grid">',
                    *rendered_blocks,
                    "</div>",
                    "</section>",
                ]
            )
        warnings = report.get("warnings", [])
        if warnings:
            body.append(
                '<aside class="report-warnings"><details>'
                f"<summary>Data notes ({len(warnings)})</summary><ul>"
            )
            for warning in warnings:
                body.append(f"<li>{self._escape(str(warning))}</li>")
            body.append("</ul></details></aside>")
        source_count = len(_list_value(report.get("sources")))
        body.extend(
            [
                '<footer class="report-footer">',
                "<span>Data Intelligence Report</span>",
                f"<span>{source_count} source{'s' if source_count != 1 else ''}</span>",
                "</footer>",
                "</main>",
            ]
        )
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>"
            + title
            + "</title><style>"
            + css
            + "</style></head><body>"
            + "".join(body)
            + '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>'
            + "<script>"
            + javascript.replace("</script", "<\\/script")
            + "</script></body></html>"
        )

    @staticmethod
    def _sentences(value: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return []
        return [
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+", normalized)
            if item.strip()
        ]

    @classmethod
    def _compact_text(
        cls,
        value: str,
        max_sentences: int = 3,
        max_chars: int = 520,
    ) -> str:
        internal_phrases = (
            "template requirement",
            "template contract",
            "chart dataset",
            "downstream",
            "artifact",
            "max_rows",
            "semantic role",
        )
        sentences = [
            sentence
            for sentence in cls._sentences(value)
            if not any(phrase in sentence.lower() for phrase in internal_phrases)
        ]
        selected = []
        for sentence in sentences:
            candidate = " ".join(selected + [sentence])
            if selected and len(candidate) > max_chars:
                break
            selected.append(sentence)
            if len(selected) >= max_sentences:
                break
        rendered = " ".join(selected).strip()
        if len(rendered) <= max_chars:
            return rendered
        shortened = rendered[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")
        return shortened + "."

    @classmethod
    def _paragraphs(cls, value: str) -> list[str]:
        sentences = cls._sentences(value)
        return [
            " ".join(sentences[index : index + 2])
            for index in range(0, len(sentences), 2)
        ]

    @staticmethod
    def _display_name(value: str) -> str:
        overrides = {
            "pdf": "PDF",
            "csv": "CSV",
            "kpi": "KPI",
        }
        words = re.sub(r"[_\-.]+", " ", value).split()
        return " ".join(
            overrides.get(word.lower(), word.capitalize()) for word in words
        )

    @staticmethod
    def _format_metric_value(value: Any, name: str) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            if "rate" in name.lower() or "percent" in name.lower():
                percent = value * 100 if abs(value) <= 1 else value
                return f"{percent:,.1f}%"
            if value.is_integer():
                return f"{int(value):,}"
            return f"{value:,.1f}"
        return str(value)

    def _table_html(self, rows: Any) -> str:
        normalized = [item for item in _list_value(rows) if isinstance(item, dict)]
        if not normalized:
            return '<p class="no-data">No table data is available.</p>'
        columns = list(dict.fromkeys(key for row in normalized for key in row))
        head = "".join(f"<th>{self._escape(str(key))}</th>" for key in columns)
        body = []
        for row in normalized[:100]:
            cells = "".join(
                f"<td>{self._escape(str(row.get(key, '')))}</td>" for key in columns
            )
            body.append(f"<tr>{cells}</tr>")
        return (
            '<div class="table-wrap"><table><thead><tr>'
            + head
            + "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table></div>"
        )

    @staticmethod
    def _css() -> str:
        return """
:root {
  color-scheme: light;
  --ink: #182033;
  --muted: #697386;
  --line: #dce2eb;
  --surface: #ffffff;
  --soft: #f4f6fb;
  --accent: #137c8b;
  --green: #2f855a;
  --amber: #c98518;
  --coral: #c65d4b;
  --danger: #aa3848;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  overflow-x: hidden;
  background: var(--soft);
  color: var(--ink);
  font-family: Inter, "Segoe UI", Arial, sans-serif;
  line-height: 1.6;
}
.report-shell {
  width: min(1180px, calc(100% - 40px));
  margin: 0 auto 64px;
}
.report-header {
  padding: 64px 40px 40px;
}
.report-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
}
.report-type {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}
.status-pill {
  padding: 3px 9px;
  border: 1px solid #b9d8d9;
  border-radius: 999px;
  background: #edf8f7;
  color: #176b70;
  font-size: 11px;
  font-weight: 700;
}
h1, h2, h3 {
  margin-top: 0;
  letter-spacing: 0;
  line-height: 1.2;
}
h1 {
  max-width: 900px;
  margin-bottom: 18px;
  font-size: 44px;
  font-weight: 750;
}
h2 { margin-bottom: 8px; font-size: 25px; }
h3 { margin-bottom: 0; font-size: 17px; }
.report-summary {
  max-width: 820px;
  margin: 0;
  color: var(--muted);
  font-size: 17px;
}
.report-section {
  padding: 24px 40px 36px;
}
.report-section + .report-section {
  padding-top: 36px;
  border-top: 1px solid var(--line);
}
.section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}
.section-heading p {
  max-width: 520px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}
.section-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 20px;
  align-items: stretch;
}
.report-block {
  grid-column: span var(--block-span);
  min-width: 0;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: 0 8px 24px rgba(28, 39, 60, 0.05);
}
.report-block-supporting {
  background: #f9fbfd;
}
.report-block-kpi_group {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}
.block-heading {
  display: flex;
  align-items: center;
  min-height: 28px;
  margin-bottom: 18px;
}
.report-block p {
  margin: 0;
  color: #3f4a5d;
}
.report-block p + p {
  margin-top: 14px;
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
  margin: 0;
}
.kpi-item {
  position: relative;
  min-width: 0;
  min-height: 142px;
  padding: 26px 24px 22px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: 0 8px 24px rgba(28, 39, 60, 0.05);
}
.kpi-item::before {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 4px;
  content: "";
  background: var(--accent);
}
.kpi-accent-2::before { background: var(--green); }
.kpi-accent-3::before { background: var(--amber); }
.kpi-accent-4::before { background: var(--coral); }
.kpi-item dt {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
}
.kpi-item dd {
  margin: 10px 0 0;
  color: var(--ink);
  font-size: 31px;
  font-weight: 750;
  line-height: 1.12;
  overflow-wrap: anywhere;
}
.echarts-chart {
  width: 100%;
  min-height: 360px;
}
.echarts-chart.chart-error { display: grid; place-items: center; color: var(--danger); background: #fff8f7; }
.chart-fallback, .no-data {
  padding: 16px;
  color: var(--muted);
  background: var(--soft);
  border-left: 3px solid var(--amber);
}
.takeaway-list {
  display: grid;
  gap: 15px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.takeaway-list li {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr);
  gap: 11px;
  color: #3f4a5d;
}
.takeaway-marker {
  width: 8px;
  height: 8px;
  margin-top: 8px;
  border-radius: 50%;
  background: var(--accent);
}
.table-wrap { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: var(--soft); }
.report-warnings {
  margin: 12px 40px 32px;
  padding: 14px 18px;
  border: 1px solid #ead7ad;
  border-radius: 8px;
  background: #fffaf0;
  color: #76571c;
}
.report-warnings summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}
.report-warnings ul {
  margin: 12px 0 0;
  padding-left: 20px;
  color: #6f6044;
  font-size: 13px;
}
.report-footer {
  display: flex;
  justify-content: space-between;
  margin: 8px 40px 0;
  padding: 20px 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}
@media (max-width: 900px) {
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .report-block { grid-column: span 12; }
  .section-heading { display: block; }
  .section-heading p { margin-top: 6px; text-align: left; }
}
@media (max-width: 640px) {
  .report-shell { width: 100%; margin-bottom: 24px; }
  .report-header { padding: 38px 20px 26px; }
  .report-section { padding: 24px 20px 30px; }
  h1 { font-size: 34px; }
  h2 { font-size: 22px; }
  .report-summary { font-size: 15px; }
  .section-grid, .kpi-grid { grid-template-columns: 1fr; }
  .report-block { grid-column: 1 / -1; }
  .kpi-item { min-height: 124px; }
  .report-warnings { margin-right: 20px; margin-left: 20px; }
  .report-footer { margin-right: 20px; margin-left: 20px; }
  .echarts-chart { min-height: 300px; }
}
@media print {
  body { background: #ffffff; }
  .report-shell { width: 100%; margin: 0; }
  .report-block, .kpi-item { box-shadow: none; break-inside: avoid; }
}
""".strip()

    @staticmethod
    def _javascript() -> str:
        return """
(function () {
  function renderCharts() {
    var configs = document.querySelectorAll(
      'script[type="application/json"][data-chart-id]'
    );
    var charts = [];
    configs.forEach(function (config) {
      var chartId = config.getAttribute("data-chart-id");
      var target = document.getElementById(chartId);
      if (!target) return;
      if (!window.echarts) {
        target.classList.add("chart-error");
        target.textContent = "The ECharts runtime could not be loaded.";
        return;
      }
      try {
        var option = JSON.parse(config.textContent || "{}");
        var chart = window.echarts.init(target, null, { renderer: "canvas" });
        chart.setOption(option, { notMerge: true });
        charts.push(chart);
      } catch (error) {
        target.classList.add("chart-error");
        target.textContent = "This chart configuration is invalid.";
      }
    });
    window.addEventListener("resize", function () {
      charts.forEach(function (chart) { chart.resize(); });
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderCharts);
  } else {
    renderCharts();
  }
}());
""".strip()

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
    output_registry: _StepOutputRegistry
    template_requirements: list[dict[str, Any]]
    upstream_step_results: list[dict[str, Any]]
    resolved_inputs: list[dict[str, Any]]
    input_resolution_errors: list[str]
    route: dict[str, Any]
    attempt: int
    error_logs: str | None
    validation_feedback: str | None
    code_spec: dict[str, Any]
    interface: InterfaceDefinition
    sandbox_result: SandboxRunResult
    contract_errors: list[str]
    validation: dict[str, Any]
    execution_result: dict[str, Any]
    data_step_result: dict[str, Any]


class _ReportGraphState(TypedDict, total=False):
    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    runtime: EngineRuntimeContext
    output_registry: _StepOutputRegistry
    user_context: UserContext | None
    plan: dict[str, Any]
    template_proposal: dict[str, Any]
    template_instance: dict[str, Any]
    previous_template_instance: dict[str, Any] | None
    template_feedback: list[dict[str, Any]]
    negotiation_iteration: int
    negotiation_status: str
    negotiation_revision_hash: str
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
        max_generation_attempts: int = 4,
        max_negotiation_iterations: int = 3,
        max_data_concurrency: int = 4,
        max_chart_concurrency: int = 6,
        fallback_to_generation_on_tool_error: bool = True,
        force_code_agent: bool = False,
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
        self.force_code_agent = bool(force_code_agent)
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
        self.input_resolver = _StepInputResolver()
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
            "output_registry": _StepOutputRegistry(),
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
        graph.add_node("negotiation_failed", self._graph_negotiation_failed)
        graph.add_node("schedule_data", self._graph_schedule_data)
        graph.add_node("run_data_step", self._graph_run_data_step)
        graph.add_node("prepare_charts", self._graph_prepare_charts)
        graph.add_node("run_chart", self._graph_run_chart)
        graph.add_node("compose_report", self._graph_compose_report)
        graph.add_node("render", self._graph_render)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "template")
        graph.add_edge("template", "negotiate")
        graph.add_conditional_edges(
            "negotiate",
            self._negotiation_route,
            {
                "revise": "plan",
                "execute": "schedule_data",
                "failed": "negotiation_failed",
            },
        )
        graph.add_edge("negotiation_failed", "render")
        graph.add_conditional_edges("schedule_data", self._dispatch_ready_steps)
        graph.add_edge("run_data_step", "schedule_data")
        graph.add_conditional_edges("prepare_charts", self._dispatch_chart_tasks)
        graph.add_edge("run_chart", "compose_report")
        graph.add_edge("compose_report", "render")
        graph.add_edge("render", END)
        return graph.compile()

    def _build_data_step_graph(self) -> Any:
        graph = StateGraph(_DataStepState)
        graph.add_node("resolve_inputs", self._data_resolve_inputs)
        graph.add_node(
            "input_resolution_failed",
            self._data_input_resolution_failed,
        )
        graph.add_node("route", self._data_route)
        graph.add_node("execute_existing", self._data_execute_existing)
        graph.add_node("generate_code", self._data_generate_code)
        graph.add_node("validate_code", self._data_validate_code)
        graph.add_node("execute_generated", self._data_execute_generated)
        graph.add_node("generation_failed", self._data_generation_failed)
        graph.add_node("analyze", self._data_analyze)
        graph.add_edge(START, "resolve_inputs")
        graph.add_conditional_edges(
            "resolve_inputs",
            self._input_resolution_choice,
            {
                "ready": "route",
                "failed": "input_resolution_failed",
            },
        )
        graph.add_edge("input_resolution_failed", "analyze")
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
        execution_plan = (
            self._plan_for_template(
                state["plan"],
                proposal["template_instance"],
            )
            if proposal.get("status") in {"accepted", "partial"}
            else state["plan"]
        )
        state["runtime"].run_context.record_step(
            "template_agent",
            inputs={"plan_revision": state["plan"].get("revision")},
            outputs={
                "status": proposal.get("status"),
                "selection": proposal.get("selection"),
                "missing_data_requests": proposal.get("missing_data_requests", []),
                "scheduled_step_ids": [
                    step.get("step_id") for step in execution_plan.get("steps", [])
                ],
            },
        )
        return {
            "plan": execution_plan,
            "template_proposal": proposal,
            "template_instance": proposal["template_instance"],
            "previous_template_instance": proposal["template_instance"],
        }

    @staticmethod
    def _plan_for_template(
        plan: dict[str, Any],
        template_instance: dict[str, Any],
    ) -> dict[str, Any]:
        steps = [
            step
            for step in plan.get("steps", [])
            if isinstance(step, dict) and step.get("step_id")
        ]
        steps_by_id = {str(step["step_id"]): step for step in steps}
        required_step_ids = {
            match.group(1)
            for binding in template_instance.get("bindings", [])
            if binding.get("status") == "resolved"
            for output_ref in (
                _list_value(binding.get("plan_output_refs"))
                or _list_value(binding.get("plan_output_ref"))
            )
            if (match := _STEP_OUTPUT_REF.match(str(output_ref)))
        }
        if not required_step_ids:
            return plan
        pending = list(required_step_ids)
        while pending:
            step_id = pending.pop()
            step = steps_by_id.get(step_id)
            if not step:
                continue
            dependencies = {
                str(dependency) for dependency in _list_value(step.get("depends_on"))
            }
            dependencies.update(
                dependency
                for item in _normalize_plan_inputs(step.get("inputs"))
                if (dependency := _step_id_from_input_ref(item.get("ref")))
                in steps_by_id
            )
            for dependency in dependencies:
                if dependency not in required_step_ids:
                    required_step_ids.add(dependency)
                    pending.append(dependency)
        execution_plan = deepcopy(plan)
        execution_plan["steps"] = [
            step for step in steps if str(step.get("step_id")) in required_step_ids
        ]
        omitted = [
            str(step.get("step_id"))
            for step in steps
            if str(step.get("step_id")) not in required_step_ids
        ]
        if omitted:
            execution_plan["warnings"] = list(
                dict.fromkeys(
                    [
                        *map(str, _list_value(execution_plan.get("warnings"))),
                        (
                            "Skipped plan steps with no resolved template "
                            "consumer: " + ", ".join(omitted)
                        ),
                    ]
                )
            )
        return execution_plan

    def _graph_negotiate(self, state: _ReportGraphState) -> dict[str, Any]:
        iteration = int(state.get("negotiation_iteration", 0)) + 1
        proposal_status = str(state["template_proposal"].get("status", "failed"))
        missing = state["template_proposal"].get("missing_data_requests", [])
        required_missing = [item for item in missing if item.get("required")]
        resolutions = {
            str(item.get("request_id")): item
            for item in state.get("plan", {}).get("request_resolutions", [])
            if isinstance(item, dict)
        }
        rejected_required = [
            item
            for item in required_missing
            if resolutions.get(str(item.get("request_id")), {}).get("decision")
            == "rejected"
        ]
        revision_hash = _negotiation_hash(
            state.get("plan", {}),
            state.get("template_proposal", {}),
        )
        stalled = (
            proposal_status == "needs_plan_revision"
            and state.get("negotiation_revision_hash") == revision_hash
        )
        if proposal_status == "needs_plan_revision" and rejected_required:
            status = "required_data_rejected"
        elif stalled:
            status = "no_negotiation_progress"
        elif (
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
                if status
                in {
                    "failed",
                    "iteration_limit_reached",
                    "no_negotiation_progress",
                    "required_data_rejected",
                }
                else "completed"
            ),
            inputs={"iteration": iteration},
            outputs={
                "status": status,
                "missing_data_requests": missing,
                "request_resolutions": list(resolutions.values()),
                "revision_hash": revision_hash,
            },
        )
        return {
            "negotiation_iteration": iteration,
            "negotiation_status": status,
            "negotiation_revision_hash": revision_hash,
            "template_feedback": missing,
        }

    def _negotiation_route(self, state: _ReportGraphState) -> str:
        if (
            state.get("negotiation_status") == "needs_plan_revision"
            and int(state.get("negotiation_iteration", 0))
            < self.max_negotiation_iterations
        ):
            return "revise"
        if state.get("negotiation_status") in {"accepted", "partial"}:
            return "execute"
        return "failed"

    def _graph_negotiation_failed(
        self,
        state: _ReportGraphState,
    ) -> dict[str, Any]:
        missing = state.get("template_proposal", {}).get("missing_data_requests", [])
        warnings = [
            str(item.get("reason") or item.get("description"))
            for item in missing
            if item.get("required")
        ]
        status = str(state.get("negotiation_status", "failed"))
        structured = {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": "failed",
            "title": state["spec"].objective,
            "summary": (
                "The report template could not be bound to the required data "
                f"within the negotiation policy ({status})."
            ),
            "template": self.report_agent._template_ref(
                state.get("template_instance", {})
            ),
            "sections": [],
            "metrics": [],
            "charts": [],
            "sources": _scope_from_spec(state["spec"], state["corpus_package"])[
                "sources"
            ],
            "warnings": warnings,
        }
        return {"structured_report": structured, "legacy_markdown": None}

    def _graph_schedule_data(self, state: _ReportGraphState) -> dict[str, Any]:
        steps = state.get("plan", {}).get("steps", [])
        completed = set(state.get("completed_step_ids", []))
        results_by_step = {
            str(item.get("step_id")): item
            for item in state.get("data_step_results", [])
        }
        remaining = [
            step for step in steps if str(step.get("step_id")) not in completed
        ]
        ready = []
        warnings: list[str] = []
        skipped_results: list[dict[str, Any]] = []
        skipped_ids: list[str] = []
        failed_dependency_skips: list[str] = []
        for step in remaining:
            dependencies = set(map(str, step.get("depends_on", [])))
            if not dependencies.issubset(completed):
                continue
            failed_dependencies = [
                dependency
                for dependency in sorted(dependencies)
                if self._dependency_required(step, dependency)
                and self._dependency_failed(results_by_step.get(dependency))
            ]
            if failed_dependencies:
                step_id = str(step.get("step_id"))
                skipped_ids.append(step_id)
                failed_dependency_skips.append(step_id)
                skipped_results.append(
                    self._skipped_step_result(
                        step_id,
                        (
                            "Required dependencies failed or were skipped: "
                            + ", ".join(failed_dependencies)
                        ),
                    )
                )
                continue
            ready.append(step)

        unresolved = [
            step
            for step in remaining
            if str(step.get("step_id")) not in skipped_ids and step not in ready
        ]
        if unresolved and not ready and not skipped_ids:
            for step in unresolved:
                step_id = str(step.get("step_id"))
                skipped_ids.append(step_id)
                skipped_results.append(
                    self._skipped_step_result(
                        step_id,
                        "Unresolved or cyclic dependency.",
                    )
                )
            warnings.append(
                "The scheduler skipped steps with unresolved or cyclic dependencies."
            )
        if failed_dependency_skips:
            warnings.append(
                "The scheduler skipped steps whose required dependencies failed."
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
            planned_ids = {
                str(step.get("step_id"))
                for step in state.get("plan", {}).get("steps", [])
            }
            completed_ids = set(state.get("completed_step_ids", []))
            if planned_ids - completed_ids:
                return "schedule_data"
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
                        "output_registry": state["output_registry"],
                        "step": step,
                        "upstream_step_results": upstream,
                        "template_requirements": self._template_requirements_for_step(
                            template_instance, step
                        ),
                    },
                )
            )
        return sends

    @staticmethod
    def _dependency_required(step: dict[str, Any], dependency: str) -> bool:
        matching_inputs = [
            item
            for item in _normalize_plan_inputs(step.get("inputs"))
            if _step_id_from_input_ref(item.get("ref")) == dependency
        ]
        if not matching_inputs:
            return True
        return any(bool(item.get("required", True)) for item in matching_inputs)

    @staticmethod
    def _dependency_failed(result: dict[str, Any] | None) -> bool:
        if result is None:
            return True
        return str(result.get("status", "")).lower() in {
            "blocked",
            "failed",
            "skipped",
        }

    @staticmethod
    def _skipped_step_result(
        step_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "status": "skipped",
            "step_id": step_id,
            "analysis_summary": (
                "The step was skipped because required upstream data was unavailable."
            ),
            "aggregated_data": {},
            "aggregated_metrics": [],
            "chart_datasets": [],
            "warnings": [reason],
            "lineage": {
                "source_refs": [],
                "upstream_step_refs": [],
                "tool_name": None,
            },
        }

    def _graph_run_data_step(self, state: _ReportGraphState) -> dict[str, Any]:
        with self._data_semaphore:
            result = self._data_step_graph.invoke(
                {
                    "step": state["step"],
                    "spec": state["spec"],
                    "corpus_package": state["corpus_package"],
                    "runtime": state["runtime"],
                    "output_registry": state["output_registry"],
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
        return {"structured_report": structured, "legacy_markdown": None}

    def _graph_render(self, state: _ReportGraphState) -> dict[str, Any]:
        rendered = self.renderer.render(
            state["structured_report"], state.get("legacy_markdown")
        )
        rendered_artifact_refs = []
        if state["runtime"].run_artifact is not None:
            for item in rendered:
                artifact = state["runtime"].run_artifact.record_rendered_report(
                    str(item.get("format", "report")),
                    str(item.get("media_type", "text/plain")),
                    str(item.get("content", "")),
                )
                item["artifact_ref"] = artifact.artifact_ref
                rendered_artifact_refs.append(artifact.artifact_ref)
                state["runtime"].run_context.add_artifact_ref(artifact.artifact_ref)
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
            outputs={
                "rendered_formats": [item["format"] for item in rendered],
                "artifact_refs": rendered_artifact_refs,
            },
            artifact_refs=rendered_artifact_refs,
        )
        return {"rendered_reports": rendered, "final_result": final_result}

    def _template_requirements_for_step(
        self, template_instance: dict[str, Any], step: dict[str, Any]
    ) -> list[dict[str, Any]]:
        step_id = str(step.get("step_id"))
        matching_bindings = [
            binding
            for binding in template_instance.get("bindings", [])
            if any(
                str(output_ref).startswith(f"step-output://{step_id}/")
                for output_ref in (
                    _list_value(binding.get("plan_output_refs"))
                    or _list_value(binding.get("plan_output_ref"))
                )
            )
        ]
        requirement_ids = {
            str(binding.get("requirement_ref")) for binding in matching_bindings
        }
        binding_by_requirement = {
            str(binding.get("requirement_ref")): binding
            for binding in matching_bindings
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
                "expected_output": deepcopy(
                    binding_by_requirement.get(requirement_id, {}).get(
                        "expected_output", {}
                    )
                ),
                "semantic_roles": deepcopy(
                    binding_by_requirement.get(requirement_id, {}).get(
                        "semantic_roles", {}
                    )
                ),
            }
            for requirement_id in sorted(requirement_ids)
        ]

    def _data_resolve_inputs(self, state: _DataStepState) -> dict[str, Any]:
        resolved, missing = self.input_resolver.resolve(
            state["step"],
            state["output_registry"],
        )
        state["runtime"].run_context.record_step(
            "input_resolver",
            status="failed" if missing else "completed",
            inputs={
                "step_id": state["step"].get("step_id"),
                "declared_inputs": state["step"].get("inputs", []),
            },
            outputs={
                "resolved_inputs": self.input_resolver.contract_payload(resolved),
                "missing_required_inputs": missing,
            },
            artifact_refs=[
                str(item["artifact_ref"])
                for item in resolved
                if item.get("artifact_ref")
            ],
        )
        return {
            "resolved_inputs": resolved,
            "input_resolution_errors": missing,
        }

    def _input_resolution_choice(self, state: _DataStepState) -> str:
        return "failed" if state.get("input_resolution_errors") else "ready"

    def _data_input_resolution_failed(
        self,
        state: _DataStepState,
    ) -> dict[str, Any]:
        missing = ", ".join(state.get("input_resolution_errors", []))
        return {
            "execution_result": {
                "schema_version": "1.0",
                "status": "failed",
                "tool_name": None,
                "arguments": {},
                "raw_result": None,
                "error": f"Required upstream inputs could not be resolved: {missing}",
            }
        }

    def _data_route(self, state: _DataStepState) -> dict[str, Any]:
        scope = _scope_from_spec(state["spec"], state["corpus_package"])
        inventory = _method_hub_payload(state["runtime"])
        if self.force_code_agent:
            route = {
                "route": "generate_tool",
                "tool_name": None,
                "arguments": {},
                "reason": (
                    "ReportEngine force_code_agent mode bypasses existing methods."
                ),
            }
        else:
            route = self.router_agent.run(state["step"], inventory, scope["sources"])
        if route.get("route") == "existing_tool":
            tool = next(
                (
                    item
                    for item in inventory
                    if item.get("tool_name") == route.get("tool_name")
                ),
                {},
            )
            route["arguments"] = self.input_resolver.merge_arguments(
                route.get("arguments"),
                tool.get("parameters_schema", {}),
                state.get("resolved_inputs", []),
                sandbox=False,
            )
        state["runtime"].run_context.record_step(
            "router_agent",
            inputs={
                "step_request": state["step"],
                "method_hub": inventory,
                "force_code_agent": self.force_code_agent,
            },
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
        scoped["resolved_inputs"] = self.input_resolver.contract_payload(
            state.get("resolved_inputs", [])
        )
        scoped["sandbox_environment"] = {
            "python_version": "3.11",
            "available_packages": [
                "matplotlib",
                "openpyxl",
                "pandas",
                "pyarrow",
                "pypdf",
                "xlrd",
            ],
            "network_access": False,
            "source_access": "read_only",
            "materialization_contract": {
                "format": "json_serializable",
                "natural_record_shapes": [
                    "table_rows",
                    "spreadsheet_rows",
                    "document_pages",
                    "text_chunks",
                    "metadata_records",
                ],
            },
        }
        code_spec = self.code_agent.run(
            state["step"],
            scoped,
            error_logs=state.get("error_logs"),
            validation_feedback=state.get("validation_feedback"),
        )
        code_spec = self._align_generated_parameter_schema(code_spec)
        code_spec["execution_arguments"] = CodeAgent._normalize_execution_arguments(
            code_spec.get("execution_arguments"),
            code_spec.get("parameters_schema", {}),
            scoped.get("sources", []),
        )
        code_spec["execution_arguments"] = self.input_resolver.merge_arguments(
            code_spec.get("execution_arguments"),
            code_spec.get("parameters_schema", {}),
            state.get("resolved_inputs", []),
            sandbox=True,
        )
        state["runtime"].run_context.record_step(
            "code_agent",
            inputs={
                "step_request": state["step"],
                "attempt": attempt,
                "error_logs": state.get("error_logs"),
                "validation_feedback": state.get("validation_feedback"),
            },
            outputs={
                "tool_name": code_spec.get("tool_name"),
                "language": "python",
                "source_code": code_spec.get("source_code", ""),
            },
        )
        return {"attempt": attempt, "code_spec": code_spec}

    @staticmethod
    def _align_generated_parameter_schema(
        code_spec: dict[str, Any],
    ) -> dict[str, Any]:
        aligned = deepcopy(code_spec)
        source = str(aligned.get("source_code") or "")
        tool_name = str(aligned.get("tool_name") or "")
        try:
            syntax_tree = ast.parse(source)
        except SyntaxError:
            return aligned
        function_node = next(
            (
                node
                for node in syntax_tree.body
                if isinstance(node, ast.FunctionDef) and node.name == tool_name
            ),
            None,
        )
        if function_node is None:
            return aligned

        positional = function_node.args.posonlyargs + function_node.args.args
        parameters = positional + function_node.args.kwonlyargs
        parameter_names = [argument.arg for argument in parameters]
        default_count = len(function_node.args.defaults)
        required_positional = positional[: len(positional) - default_count]
        required_keyword_only = [
            argument
            for argument, default in zip(
                function_node.args.kwonlyargs,
                function_node.args.kw_defaults,
            )
            if default is None
        ]
        required_names = [
            argument.arg for argument in required_positional + required_keyword_only
        ]

        raw_schema = aligned.get("parameters_schema")
        schema = deepcopy(raw_schema) if isinstance(raw_schema, dict) else {}
        raw_properties = schema.get("properties")
        properties = (
            deepcopy(raw_properties) if isinstance(raw_properties, dict) else {}
        )
        if function_node.args.kwarg is None:
            properties = {
                name: value
                for name, value in properties.items()
                if name in parameter_names
            }
        for argument in parameters:
            properties.setdefault(
                argument.arg,
                {
                    "type": ReportEngine._annotation_json_type(
                        argument.annotation,
                        argument.arg,
                    )
                },
            )
        schema.update(
            {
                "type": "object",
                "properties": properties,
                "required": required_names,
            }
        )
        aligned["parameters_schema"] = schema
        execution_arguments = aligned.get("execution_arguments")
        if isinstance(execution_arguments, dict) and function_node.args.kwarg is None:
            aligned["execution_arguments"] = {
                name: value
                for name, value in execution_arguments.items()
                if name in parameter_names
            }
        return aligned

    @staticmethod
    def _annotation_json_type(
        annotation: ast.expr | None,
        parameter_name: str,
    ) -> str:
        if parameter_name == "path" or parameter_name.endswith("_path"):
            return "string"
        rendered = ast.unparse(annotation).lower() if annotation is not None else ""
        if "list" in rendered or "sequence" in rendered or "tuple" in rendered:
            return "array"
        if "dict" in rendered or "mapping" in rendered:
            return "object"
        if "bool" in rendered:
            return "boolean"
        if "int" in rendered:
            return "integer"
        if "float" in rendered or "number" in rendered:
            return "number"
        return "string" if "str" in rendered else "object"

    def _data_validate_code(self, state: _DataStepState) -> dict[str, Any]:
        interface = self._build_generated_interface(state["step"], state["code_spec"])
        validation_inputs = state["code_spec"].get("execution_arguments", {})
        if not isinstance(validation_inputs, dict):
            validation_inputs = {}
        argument_errors = self._execution_argument_errors(
            state["code_spec"],
            validation_inputs,
            state["step"],
        )
        if argument_errors:
            sandbox_result = SandboxRunResult(
                status="failed",
                error="; ".join(argument_errors),
            )
        else:
            sandbox_result = self._validate_in_sandbox(
                interface,
                state["runtime"],
                validation_inputs,
            )
        contract_errors = list(argument_errors)
        if sandbox_result.status == "completed":
            contract_errors.extend(
                self._output_contract_errors(
                    sandbox_result.result,
                    interface.output_schema,
                )
            )
        state["runtime"].run_context.record_step(
            "sandbox_validate",
            status=(
                "failed"
                if sandbox_result.status != "completed" or contract_errors
                else "completed"
            ),
            inputs={"interface": interface.name},
            outputs={
                "result": sandbox_result.result,
                "error": sandbox_result.error,
                "contract_errors": contract_errors,
            },
            artifact_refs=sandbox_result.artifact_refs,
            log_refs=sandbox_result.log_refs,
        )
        sandbox_logs = self._sandbox_logs(sandbox_result)
        if contract_errors:
            sandbox_logs = f"{sandbox_logs}\nContract errors: " + "; ".join(
                contract_errors
            )
        validation = self.validator_agent.run(
            _json_dumps(state["step"]),
            str(state["code_spec"].get("source_code", "")),
            sandbox_logs,
            sandbox_result.result,
        )
        validator_passed = str(validation.get("status", "")).lower() == "pass"
        effective_pass = (
            sandbox_result.status == "completed"
            and not contract_errors
            and validator_passed
        )
        state["runtime"].run_context.record_step(
            "validator_agent",
            status="completed" if effective_pass else "failed",
            inputs={"step_description": state["step"].get("description", "")},
            outputs={
                "validation": validation,
                "sandbox_status": sandbox_result.status,
                "contract_errors": contract_errors,
                "effective_pass": effective_pass,
            },
        )
        return {
            "interface": interface,
            "sandbox_result": sandbox_result,
            "contract_errors": contract_errors,
            "validation": validation,
            "error_logs": sandbox_logs,
            "validation_feedback": str(validation.get("feedback", "")),
        }

    def _validation_choice(self, state: _DataStepState) -> str:
        sandbox_passed = (
            state.get("sandbox_result") is not None
            and state["sandbox_result"].status == "completed"
        )
        validator_passed = (
            str(state.get("validation", {}).get("status", "")).lower() == "pass"
        )
        if sandbox_passed and validator_passed and not state.get("contract_errors"):
            return "execute"
        if int(state.get("attempt", 0)) < self.max_generation_attempts:
            return "retry"
        return "failed"

    def _data_execute_generated(self, state: _DataStepState) -> dict[str, Any]:
        result = self.tool_executor.execute_generated(
            state["interface"],
            state["code_spec"],
            state["runtime"],
            state["sandbox_result"],
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
                "error": state.get("error_logs")
                or state.get("validation_feedback")
                or "Generated tool validation failed.",
            }
        }

    def _data_analyze(self, state: _DataStepState) -> dict[str, Any]:
        result = self.datascience_processor.process(
            state["step"],
            state["execution_result"],
            state["runtime"],
            state["output_registry"],
            state.get("template_requirements", []),
            state.get("upstream_step_results", []),
            state["spec"].objective,
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
            output_schema=self._step_output_schema(step, code_spec),
            implementation_ref=str(code_spec.get("source_code", "")),
            source="generated",
            trust_level="generated_unvalidated",
            metadata={
                "capability_names": [GENERATED_TOOL_CAPABILITY],
                "source_code": code_spec.get("source_code", ""),
                "step_request": step,
            },
        )

    @staticmethod
    def _step_output_schema(
        step: dict[str, Any],
        code_spec: dict[str, Any],
    ) -> dict[str, Any]:
        outputs = [
            item for item in _list_value(step.get("outputs")) if isinstance(item, dict)
        ]
        declared = code_spec.get("output_schema")
        declared = deepcopy(declared) if isinstance(declared, dict) else {}
        if len(outputs) != 1:
            return declared or {"type": "object"}

        shape = str(outputs[0].get("shape", "table"))
        expected_type = (
            "array"
            if shape in {"table", "time_series", "category_series"}
            else "object" if shape == "record" else None
        )
        if expected_type is None:
            return declared or {}
        if declared.get("type") == expected_type:
            return declared
        if expected_type == "array":
            return {"type": "array", "items": {"type": "object"}}
        return {"type": "object"}

    def _validate_in_sandbox(
        self,
        interface: InterfaceDefinition,
        runtime: EngineRuntimeContext,
        validation_inputs: dict[str, Any],
    ) -> SandboxRunResult:
        if runtime.sandbox_executor is None:
            return SandboxRunResult(
                status="failed", error="Sandbox executor is not configured."
            )
        try:
            return runtime.sandbox_executor.validate(
                interface,
                validation_inputs,
                None,
            )
        except Exception as exc:
            return SandboxRunResult(status="failed", error=str(exc))

    def _sandbox_logs(self, sandbox_result: SandboxRunResult) -> str:
        if sandbox_result.status == "completed":
            return "Success"
        return f"Error: {sandbox_result.error or 'Sandbox validation failed.'}"

    @staticmethod
    def _execution_argument_errors(
        code_spec: dict[str, Any],
        arguments: dict[str, Any],
        step: dict[str, Any] | None = None,
    ) -> list[str]:
        generation_error = str(code_spec.get("generation_error") or "").strip()
        if generation_error:
            return [generation_error]
        source_code = str(code_spec.get("source_code") or "").strip()
        if not source_code:
            return ["Generated source_code cannot be empty."]
        try:
            syntax_tree = ast.parse(source_code)
        except SyntaxError as exc:
            return [
                "Generated source_code is invalid Python: "
                f"{exc.msg} at line {exc.lineno}."
            ]
        operation = step.get("operation", {}) if isinstance(step, dict) else {}
        operation_kind = str(
            operation.get("kind") if isinstance(operation, dict) else operation or ""
        ).lower()
        if operation_kind in {
            "load_excel",
            "load_spreadsheet",
            "materialize_excel",
            "materialize_source",
            "materialize_spreadsheet",
            "read_excel",
            "read_spreadsheet",
        }:
            masks_read_failure = False
            for handler in (
                node
                for node in ast.walk(syntax_tree)
                if isinstance(node, ast.ExceptHandler)
            ):
                for node in handler.body:
                    for nested in ast.walk(node):
                        if not isinstance(nested, ast.Return):
                            continue
                        value = nested.value
                        if (
                            value is None
                            or isinstance(value, ast.Constant)
                            and value.value is None
                            or isinstance(value, ast.List)
                            and not value.elts
                            or isinstance(value, ast.Dict)
                            and not value.keys
                        ):
                            masks_read_failure = True
                            break
                    if masks_read_failure:
                        break
                if masks_read_failure:
                    break
            if masks_read_failure:
                return [
                    "Generated source materialization must not catch read or "
                    "parser errors and return an empty result. Let ingestion "
                    "errors propagate; return an empty collection only after a "
                    "successful read."
                ]
        tool_name = str(code_spec.get("tool_name") or "").strip()
        if not tool_name.isidentifier():
            return ["Generated tool_name must be a valid Python identifier."]
        function_node = next(
            (
                node
                for node in syntax_tree.body
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and node.name == tool_name
            ),
            None,
        )
        if function_node is None:
            return [
                f"Generated source_code must define a top-level function named "
                f"{tool_name}."
            ]
        if isinstance(function_node, ast.AsyncFunctionDef):
            return ["Generated report tools must be synchronous functions."]
        schema = code_spec.get("parameters_schema")
        if not isinstance(schema, dict):
            return ["Generated parameters_schema must be an object."]
        required = schema.get("required", [])
        if not isinstance(required, list):
            return ["parameters_schema.required must be an array."]
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return ["parameters_schema.properties must be an object."]
        errors = [
            f"Missing required execution argument: {name}"
            for name in map(str, required)
            if name not in arguments
        ]
        positional = function_node.args.posonlyargs + function_node.args.args
        default_count = len(function_node.args.defaults)
        required_positional = positional[: len(positional) - default_count]
        required_keyword_only = [
            argument
            for argument, default in zip(
                function_node.args.kwonlyargs,
                function_node.args.kw_defaults,
            )
            if default is None
        ]
        function_required = {
            argument.arg for argument in required_positional + required_keyword_only
        }
        function_parameters = {
            argument.arg for argument in positional + function_node.args.kwonlyargs
        }
        for name in sorted(function_required):
            if name not in properties:
                errors.append(
                    f"parameters_schema must declare required function argument: {name}"
                )
            if name not in arguments:
                errors.append(f"Missing required function execution argument: {name}")
        if function_node.args.kwarg is None:
            errors.extend(
                f"Unexpected execution argument for generated function: {name}"
                for name in arguments
                if name not in function_parameters
            )
        if function_node.args.posonlyargs:
            errors.append(
                "Generated report tools cannot declare positional-only arguments."
            )
        return list(dict.fromkeys(errors))

    @classmethod
    def _output_contract_errors(
        cls,
        result: Any,
        output_schema: Any,
    ) -> list[str]:
        if not isinstance(output_schema, dict) or not output_schema:
            return []
        expected_type = output_schema.get("type")
        if isinstance(expected_type, list):
            expected_types = [str(item) for item in expected_type]
        elif expected_type:
            expected_types = [str(expected_type)]
        else:
            expected_types = []
        if expected_types and not any(
            cls._matches_json_type(result, item) for item in expected_types
        ):
            rendered = ", ".join(expected_types)
            return [
                f"Generated output must have JSON type {rendered}; "
                f"received {type(result).__name__}."
            ]

        errors: list[str] = []
        if isinstance(result, dict):
            errors.extend(cls._required_field_errors(result, output_schema))
        if isinstance(result, list):
            item_schema = output_schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(result[:100]):
                    if not cls._matches_declared_schema(item, item_schema):
                        errors.append(
                            f"Generated output item {index} does not match its "
                            "declared JSON type."
                        )
                        continue
                    if isinstance(item, dict):
                        errors.extend(
                            f"Generated output item {index}: {error}"
                            for error in cls._required_field_errors(item, item_schema)
                        )
        return errors

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str) -> bool:
        match expected_type:
            case "array":
                return isinstance(value, list)
            case "object":
                return isinstance(value, dict)
            case "string":
                return isinstance(value, str)
            case "number":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            case "integer":
                return isinstance(value, int) and not isinstance(value, bool)
            case "boolean":
                return isinstance(value, bool)
            case "null":
                return value is None
            case _:
                return True

    @classmethod
    def _matches_declared_schema(
        cls,
        value: Any,
        schema: dict[str, Any],
    ) -> bool:
        expected_type = schema.get("type")
        if not expected_type:
            return True
        if isinstance(expected_type, list):
            return any(
                cls._matches_json_type(value, str(item)) for item in expected_type
            )
        return cls._matches_json_type(value, str(expected_type))

    @staticmethod
    def _required_field_errors(
        value: dict[str, Any],
        schema: dict[str, Any],
    ) -> list[str]:
        required = schema.get("required", [])
        if not isinstance(required, list):
            return ["output_schema.required must be an array."]
        return [
            f"missing required field {name}"
            for name in map(str, required)
            if name not in value
        ]
