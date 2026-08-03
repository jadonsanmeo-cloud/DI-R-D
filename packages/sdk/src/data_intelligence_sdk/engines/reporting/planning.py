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

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
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
from data_intelligence_sdk.engines.reporting.contracts import (
    AUTO_EXECUTION_CLASS,
    DETERMINISTIC_TRANSFORM_CLASS,
    EXECUTION_CLASSES,
    EXECUTION_MODES,
    SEMANTIC_INFERENCE_CLASS,
    SOURCE_OPERATION_CLASS,
    execution_class_for_capability,
    execution_class_for_step,
)
from data_intelligence_sdk.engines.reporting.corpus import (
    CORPUS_MATERIALIZE_OPERATION,
)
from data_intelligence_sdk.engines.reporting.policies import (
    CONTENT_ROLES,
    DEFAULT_SOURCE_MATERIALIZATION_REGISTRY,
    REPORT_BLOCK_TYPES,
    SourceMaterializationRegistry,
    TemplateSelectionPolicy,
    legacy_content_role,
    normalize_content_role,
)
from data_intelligence_sdk.engines.reporting.prompts import (
    PLAN_AGENT_PROMPT,
    TEMPLATE_AGENT_PROMPT,
    TEMPLATE_POOL_PACKAGE,
)
from data_intelligence_sdk.engines.reporting.utils import (
    _STEP_OUTPUT_REF,
    _bind_dependency_inputs,
    _compatible_plan_outputs,
    _execution_spec_payload,
    _int_value,
    _is_downstream_owned_step,
    _list_value,
    _normalize_plan_inputs,
    _normalize_plan_outputs,
    _safe_id,
    _scope_from_spec,
    _scoped_corpus_payload,
    _semantic_role_groups,
    _table_columns,
)


def _ingested_document_metadata(
    corpus_package: DataCorpusPackage,
    document_id: str,
) -> dict[str, Any] | None:
    documents = corpus_package.metadata.get("ingested_documents", [])
    if not isinstance(documents, list):
        return None
    return next(
        (
            item
            for item in documents
            if isinstance(item, dict)
            and str(item.get("document_id")) == str(document_id)
        ),
        None,
    )


def _ingested_document_source_ref(
    corpus_package: DataCorpusPackage,
    document_id: str,
) -> str:
    metadata = _ingested_document_metadata(corpus_package, document_id)
    if metadata is not None and metadata.get("source_ref"):
        return str(metadata["source_ref"])
    return f"corpus://{document_id}"


class PlanAgent(_PromptAgent):
    def __init__(
        self,
        llm: object | None,
        source_registry: SourceMaterializationRegistry | None = None,
    ) -> None:
        super().__init__("plan_agent", PLAN_AGENT_PROMPT, llm)
        self.source_registry = (
            source_registry or DEFAULT_SOURCE_MATERIALIZATION_REGISTRY
        )

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        previous_plan: dict[str, Any] | None = None,
        template_feedback: list[dict[str, Any]] | None = None,
        validation_feedback: list[str] | None = None,
    ) -> dict[str, Any]:
        scoped_corpus = _scoped_corpus_payload(spec, corpus_package)
        payload = self._invoke_json(
            execution_spec=_execution_spec_payload(spec),
            corpus_package=scoped_corpus,
            previous_plan=previous_plan,
            template_feedback=template_feedback or [],
            validation_feedback=validation_feedback or [],
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
        allowed_documents = set(scope["documents"])
        document_handler = next(
            (
                handler
                for handler in self.source_registry.handlers
                if handler.source_kind == "document" and not handler.extensions
            ),
            None,
        )
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
            documents = [
                str(item) for item in _list_value(required_data.get("documents"))
            ]
            raw_execution_class = (
                str(
                    raw_operation.get("execution_class")
                    or (
                        operation_kind
                        if operation_kind in EXECUTION_CLASSES
                        else ""
                    )
                ).lower()
                if isinstance(raw_operation, dict)
                else AUTO_EXECUTION_CLASS
            )
            unambiguous_document_scope = bool(
                scope["documents"]
                and not scope["tables"]
                and not scope["vector_collections"]
            )
            if (
                raw_execution_class == SOURCE_OPERATION_CLASS
                and not tables
                and not vectors
                and not documents
                and unambiguous_document_scope
            ):
                documents = list(scope["documents"])
            document_materialization = bool(
                operation_kind == CORPUS_MATERIALIZE_OPERATION
                or (
                    raw_execution_class == SOURCE_OPERATION_CLASS
                    and bool(documents)
                )
                or (
                    document_handler is not None
                    and document_handler.accepts_operation(operation_kind)
                )
            )
            if document_materialization and not documents:
                documents = list(scope["documents"])
            if scope["explicit"] and (
                any(item not in allowed_tables for item in tables)
                or any(item not in allowed_vectors for item in vectors)
                or any(item not in allowed_documents for item in documents)
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
            if documents and document_materialization:
                document_metadata = _ingested_document_metadata(
                    corpus_package,
                    documents[0],
                )
                if document_metadata is not None:
                    parameters: dict[str, Any] = {
                        "document_ids": documents,
                        "mode": "all",
                    }
                    if len(documents) == 1:
                        parameters["document_id"] = documents[0]
                        parameters["organization_id"] = document_metadata.get(
                            "organization_id"
                        )
                    operation = {
                        "kind": CORPUS_MATERIALIZE_OPERATION,
                        "parameters": parameters,
                    }
                    description = (
                        "Materialize extracted contents and indexed chunks from "
                        "the selected ingested document set."
                    )
                    for output in outputs:
                        output["type"] = "array"
                        output["shape"] = "table"
                        roles = [
                            str(role)
                            for role in _list_value(output.get("semantic_roles"))
                            if str(role) != "goal_evidence"
                        ]
                        output["semantic_roles"] = list(
                            dict.fromkeys(
                                roles + ["source_content"]
                            )
                        )
            local_sources = [
                str(source)
                for source in scope["sources"]
                if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(source))
                and Path(str(source)).suffix
            ]
            source_materialization_step = (
                bool(local_sources)
                and not _list_value(raw_step.get("depends_on"))
                and any(
                    handler.matches_source(source)
                    and (
                        handler.accepts_operation(operation_kind)
                        or str(
                            (
                                operation
                                if isinstance(operation, dict)
                                else {}
                            ).get("capability")
                            or ""
                        )
                        == handler.capability_id
                    )
                    for source in local_sources
                    for handler in self.source_registry.handlers
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
                        str(role)
                        for role in _list_value(output.get("semantic_roles"))
                        if str(role) != "goal_evidence"
                    ]
                    output["semantic_roles"] = list(
                        dict.fromkeys(roles + ["source_content"])
                    )
            source_registration_step = operation_kind.startswith(
                ("register", "upload")
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
            operation = self._normalize_operation_contract(operation)
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
                        "documents": documents,
                        "columns": columns,
                    },
                    "operation": operation,
                    "outputs": outputs,
                    "fallback": fallback,
                }
            )
        normalized_steps = self._ensure_ingested_materialization_steps(
            normalized_steps,
            scope,
            corpus_package,
        )
        valid_step_ids = {step["step_id"] for step in normalized_steps}
        for step in normalized_steps:
            step["depends_on"] = [
                dependency
                for dependency in step["depends_on"]
                if dependency in valid_step_ids
            ]
        _bind_dependency_inputs(normalized_steps)
        self._align_execution_modes_with_lineage(normalized_steps)
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
    def _align_execution_modes_with_lineage(
        steps: list[dict[str, Any]],
    ) -> None:
        """Make the route agree with each step's declared execution contract.

        No task category is inferred from prose, filenames, field names, or a
        template layout. The Plan Agent declares the execution class; this
        normalization only prevents an internally contradictory route.
        """

        steps_by_id = {
            str(step.get("step_id")): step
            for step in steps
            if isinstance(step, dict) and step.get("step_id")
        }
        for step in steps:
            operation = step.get("operation", {})
            operation = operation if isinstance(operation, dict) else {}
            execution_class = execution_class_for_step(step)
            operation["execution_class"] = execution_class
            execution_mode = str(
                operation.get("execution_mode") or "auto"
            ).lower()
            if execution_class == SOURCE_OPERATION_CLASS:
                operation["execution_mode"] = "method_hub"
                operation["mode_alignment_reason"] = (
                    "Source operations require a registered runtime capability."
                )
            elif execution_class == SEMANTIC_INFERENCE_CLASS:
                operation["execution_mode"] = "semantic_analysis"
                operation["mode_alignment_reason"] = (
                    "The execution contract requires evidence-grounded "
                    "language reasoning."
                )
            elif execution_class == DETERMINISTIC_TRANSFORM_CLASS:
                deterministic_spec = operation.get("deterministic_spec")
                procedure = (
                    str(deterministic_spec.get("procedure") or "").strip()
                    if isinstance(deterministic_spec, dict)
                    else ""
                )
                expression = (
                    str(deterministic_spec.get("expression") or "").strip()
                    if isinstance(deterministic_spec, dict)
                    else ""
                )
                structured_steps = (
                    _list_value(deterministic_spec.get("steps"))
                    if isinstance(deterministic_spec, dict)
                    else []
                )
                operation_parameters = operation.get("parameters")
                reproducible = bool(
                    expression
                    or structured_steps
                    or procedure
                    or isinstance(operation_parameters, dict)
                    and bool(operation_parameters)
                )
                if not reproducible:
                    operation["execution_class"] = SEMANTIC_INFERENCE_CLASS
                    operation["execution_mode"] = "semantic_analysis"
                    operation["mode_alignment_reason"] = (
                        "The step declares no reproducible procedure, expression, "
                        "structured steps, or operation parameters, so it requires "
                        "evidence-grounded semantic execution instead of guessed code."
                    )
                    continue
                if execution_mode == "semantic_analysis":
                    operation["execution_mode"] = "auto"
                    operation["mode_alignment_reason"] = (
                        "A deterministic transform may use a compatible method or "
                        "validated generated code, but not semantic execution."
                    )
            elif execution_class == AUTO_EXECUTION_CLASS:
                upstream_steps = []
                for input_contract in _list_value(step.get("inputs")):
                    if not isinstance(input_contract, dict):
                        continue
                    match = _STEP_OUTPUT_REF.match(
                        str(input_contract.get("ref") or "")
                    )
                    if match and match.group(1) in steps_by_id:
                        upstream_steps.append(steps_by_id[match.group(1)])
                reads_ingested_document = any(
                    str(upstream.get("operation", {}).get("kind") or "")
                    == CORPUS_MATERIALIZE_OPERATION
                    for upstream in upstream_steps
                    if isinstance(upstream.get("operation"), dict)
                )
                deterministic_spec = operation.get("deterministic_spec")
                has_deterministic_contract = isinstance(
                    deterministic_spec, dict
                ) and any(
                    deterministic_spec.get(field)
                    for field in ("procedure", "expression", "steps")
                )
                if reads_ingested_document and not has_deterministic_contract:
                    operation["execution_class"] = SEMANTIC_INFERENCE_CLASS
                    operation["execution_mode"] = "semantic_analysis"
                    operation["mode_alignment_reason"] = (
                        "An unresolved analysis request consumes materialized "
                        "document content without a deterministic transform "
                        "contract, so it requires semantic inference."
                    )

    @staticmethod
    def _normalize_operation_contract(
        operation: Any,
    ) -> dict[str, Any]:
        normalized = (
            deepcopy(operation)
            if isinstance(operation, dict)
            else {"kind": str(operation or "analyze")}
        )
        kind = str(normalized.get("kind") or "analyze")
        normalized["kind"] = kind
        normalized["capability"] = str(
            normalized.get("capability") or kind
        )
        mode = str(normalized.get("execution_mode") or "auto").lower()
        if kind == CORPUS_MATERIALIZE_OPERATION:
            mode = "method_hub"
        normalized["execution_mode"] = (
            mode if mode in EXECUTION_MODES else "auto"
        )
        execution_class = str(
            normalized.get("execution_class") or ""
        ).lower()
        if kind == CORPUS_MATERIALIZE_OPERATION:
            execution_class = SOURCE_OPERATION_CLASS
        elif execution_class_for_capability(normalized):
            execution_class = execution_class_for_capability(normalized) or execution_class
        elif kind.lower() in EXECUTION_CLASSES:
            execution_class = kind.lower()
        elif execution_class not in EXECUTION_CLASSES:
            execution_class = {
                "method_hub": SOURCE_OPERATION_CLASS,
                "generated_code": DETERMINISTIC_TRANSFORM_CLASS,
                "semantic_analysis": SEMANTIC_INFERENCE_CLASS,
            }.get(normalized["execution_mode"], AUTO_EXECUTION_CLASS)
        normalized["execution_class"] = execution_class
        parameters = normalized.get("parameters")
        normalized["parameters"] = (
            deepcopy(parameters) if isinstance(parameters, dict) else {}
        )
        return normalized

    @staticmethod
    def _ensure_ingested_materialization_steps(
        steps: list[dict[str, Any]],
        scope: dict[str, Any],
        corpus_package: DataCorpusPackage,
    ) -> list[dict[str, Any]]:
        selected_documents = list(
            dict.fromkeys(
                str(item) for item in scope.get("documents", []) if str(item)
            )
        )
        if not selected_documents:
            return steps

        materialization_steps = [
            step
            for step in steps
            if str(step.get("operation", {}).get("kind") or "")
            == CORPUS_MATERIALIZE_OPERATION
        ]
        covered_documents = {
            str(document_id)
            for step in materialization_steps
            for document_id in step.get("required_data", {}).get("documents", [])
            if str(document_id)
        }
        missing_documents = [
            document_id
            for document_id in selected_documents
            if document_id not in covered_documents
        ]
        if missing_documents:
            metadata = _ingested_document_metadata(
                corpus_package,
                missing_documents[0],
            )
            parameters: dict[str, Any] = {
                "document_ids": missing_documents,
                "mode": "all",
            }
            if len(missing_documents) == 1:
                parameters["document_id"] = missing_documents[0]
                if metadata is not None:
                    parameters["organization_id"] = metadata.get("organization_id")
            step_id = "materialize-selected-documents"
            existing_ids = {str(step.get("step_id")) for step in steps}
            suffix = 2
            while step_id in existing_ids:
                step_id = f"materialize-selected-documents-{suffix}"
                suffix += 1
            materialization_step = {
                "step_id": step_id,
                "description": (
                    "Materialize complete extracted contents and indexed chunks "
                    "for the selected ingested document set."
                ),
                "required": True,
                "inputs": [
                    {
                        "ref": _ingested_document_source_ref(
                            corpus_package,
                            document_id,
                        ),
                        "kind": "corpus_source",
                        "required": True,
                    }
                    for document_id in missing_documents
                ],
                "depends_on": [],
                "required_data": {
                    "tables": [],
                    "vector_collections": [],
                    "documents": missing_documents,
                    "columns": [],
                },
                "operation": {
                    "kind": CORPUS_MATERIALIZE_OPERATION,
                    "capability": CORPUS_MATERIALIZE_OPERATION,
                    "execution_mode": "method_hub",
                    "execution_class": SOURCE_OPERATION_CLASS,
                    "parameters": parameters,
                },
                "outputs": [
                    {
                        "name": "ingested-document-content",
                        "type": "array",
                        "shape": "table",
                        "semantic_roles": ["source_content"],
                        "consumer_hints": ["analysis", "report"],
                    }
                ],
                "fallback": {
                    "action": "complete_no_data",
                    "message": "The selected document set has no readable content.",
                },
            }
            steps = [materialization_step, *steps]
            materialization_steps.append(materialization_step)

        source_step_ids = [str(step["step_id"]) for step in materialization_steps]
        for step in steps:
            if step in materialization_steps or step.get("depends_on"):
                continue
            step["depends_on"] = list(source_step_ids)
        return steps

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
            compatible_outputs = _compatible_plan_outputs(request, all_outputs)
            compatible_refs = {
                f"step-output://{step.get('step_id')}/{output.get('name')}"
                for step, output in compatible_outputs
            }
            output_refs = []
            for ref in _list_value(raw.get("output_refs")):
                if isinstance(ref, dict):
                    rendered = (
                        f"step-output://{ref.get('step_id')}/"
                        f"{ref.get('output_name')}"
                    )
                else:
                    rendered = str(ref)
                if (
                    rendered in valid_refs
                    and rendered in compatible_refs
                    and rendered not in output_refs
                ):
                    output_refs.append(rendered)
            if not output_refs:
                output_refs = [
                    f"step-output://{step.get('step_id')}/{output.get('name')}"
                    for step, output in compatible_outputs
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
        vector_handler = next(
            (
                handler
                for handler in self.source_registry.handlers
                if handler.source_kind == "vector_collection"
                and not handler.extensions
            ),
            None,
        )
        for collection in scope["vector_collections"]:
            metadata = corpus_package.schemas.get("vector_collections", {}).get(
                collection, {}
            )
            steps.append(
                {
                    "step_id": f"retrieve-{_safe_id(collection)}",
                    "description": (
                        f"Retrieve objective-relevant content from vector "
                        f"collection `{collection}`."
                    ),
                    "required": bool(scope["explicit"]),
                    "inputs": [
                        {
                            "ref": f"corpus://{collection}",
                            "kind": "corpus_source",
                            "required": bool(scope["explicit"]),
                        }
                    ],
                    "depends_on": [],
                    "required_data": {
                        "tables": [],
                        "vector_collections": [collection],
                        "documents": [],
                        "columns": _table_columns(metadata),
                    },
                    "operation": {
                        "kind": (
                            vector_handler.capability_id
                            if vector_handler is not None
                            else "retrieve_vector_content"
                        )
                    },
                    "outputs": [
                        {
                            "name": f"{_safe_id(collection)}-content",
                            "shape": "table",
                            "semantic_roles": ["source_content"],
                            "consumer_hints": ["analysis", "report"],
                        }
                    ],
                    "fallback": {
                        "action": (
                            "complete_no_data" if scope["explicit"] else "omit"
                        ),
                        "message": "No vector content was available.",
                    },
                }
            )
        document_handler = next(
            (
                handler
                for handler in self.source_registry.handlers
                if handler.source_kind == "document" and not handler.extensions
            ),
            None,
        )
        for document in scope["documents"]:
            document_metadata = _ingested_document_metadata(
                corpus_package,
                document,
            )
            source_ref = (
                str(document_metadata.get("source_ref"))
                if document_metadata is not None
                and document_metadata.get("source_ref")
                else f"corpus://{document}"
            )
            operation = (
                {
                    "kind": CORPUS_MATERIALIZE_OPERATION,
                    "parameters": {
                        "document_id": document,
                        "organization_id": document_metadata.get("organization_id"),
                        "mode": "all",
                    },
                }
                if document_metadata is not None
                else {
                    "kind": (
                        document_handler.capability_id
                        if document_handler is not None
                        else "retrieve_document_content"
                    )
                }
            )
            steps.append(
                {
                    "step_id": f"retrieve-{_safe_id(document)}",
                    "description": (
                        "Materialize extracted contents and indexed chunks from "
                        f"ingested document `{document}`."
                    ),
                    "required": True,
                    "inputs": [
                        {
                            "ref": source_ref,
                            "kind": "corpus_source",
                            "required": True,
                        }
                    ],
                    "depends_on": [],
                    "required_data": {
                        "tables": [],
                        "vector_collections": [],
                        "documents": [document],
                        "columns": [],
                    },
                    "operation": operation,
                    "outputs": [
                        {
                            "name": f"{_safe_id(document)}-content",
                            "shape": "table",
                            "semantic_roles": ["source_content"],
                            "consumer_hints": ["analysis", "report"],
                        }
                    ],
                    "fallback": {
                        "action": "complete_no_data",
                        "message": "The selected document has no readable content.",
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
            scope["sources"]
            or scope["tables"]
            or scope["vector_collections"]
            or scope["documents"]
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
                        "vector_collections": scope["vector_collections"],
                        "documents": scope["documents"],
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
        steps = self._ensure_ingested_materialization_steps(
            steps,
            scope,
            corpus_package,
        )
        for step in steps:
            step["operation"] = self._normalize_operation_contract(
                step.get("operation")
            )
            step["outputs"] = _normalize_plan_outputs(
                step.get("outputs"),
                str(step.get("step_id") or "step"),
            )
        _bind_dependency_inputs(steps)
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

    def selection_policy(self) -> TemplateSelectionPolicy:
        return TemplateSelectionPolicy.from_manifest(self.manifest())

    def selection_candidates(self) -> list[dict[str, Any]]:
        """Return LLM-facing descriptors and legal reusable archetypes."""

        candidates = []
        for descriptor in self.list_templates():
            if descriptor.get("llm_candidate") is not True:
                continue
            definition = self.get(str(descriptor.get("template_id")))
            section_archetypes = []
            for section in definition.get("sections", []):
                section_archetypes.append(
                    {
                        "section_id": section.get("section_id"),
                        "title": section.get("title"),
                        "purpose": section.get("purpose"),
                        "blocks": [
                            {
                                "archetype_ref": block.get("block_id"),
                                "type": block.get("type"),
                                "content_role": block.get("content_role"),
                                "required": bool(block.get("required", False)),
                                "data_requirement_refs": deepcopy(
                                    block.get("data_requirement_refs", [])
                                ),
                                "instructions": deepcopy(
                                    block.get("instructions", [])
                                ),
                            }
                            for block in section.get("blocks", [])
                        ],
                    }
                )
            candidates.append(
                {
                    "template_id": descriptor.get("template_id"),
                    "version": descriptor.get("version"),
                    "name": descriptor.get("name") or definition.get("name"),
                    "description": descriptor.get("description")
                    or definition.get("description"),
                    "domain": descriptor.get("domain", "cross-domain"),
                    "tags": deepcopy(descriptor.get("tags", [])),
                    "source_extensions": deepcopy(
                        descriptor.get("source_extensions", [])
                    ),
                    "selection_hint": descriptor.get("selection_hint"),
                    "selection": deepcopy(definition.get("selection", {})),
                    "adaptation": deepcopy(definition.get("adaptation", {})),
                    "section_archetypes": section_archetypes,
                }
            )
        return candidates

    def get(
        self,
        template_id: str,
        version: str | None = None,
        _lineage: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if template_id in _lineage:
            raise ValueError(
                "Circular report-template inheritance: "
                + " -> ".join((*_lineage, template_id))
            )
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
            parent_id = payload.get("extends")
            if parent_id:
                parent = self.get(
                    str(parent_id),
                    _lineage=(*_lineage, template_id),
                )
                payload = self._merge_definition(parent, payload)
            self._apply_legacy_content_roles(payload)
            self._validate_definition(payload, descriptor)
            self._validate_content_roles(payload)
            return payload
        raise KeyError(f"Unknown report template: {template_id!r} version={version!r}")

    @staticmethod
    def _merge_definition(
        parent: dict[str, Any],
        child: dict[str, Any],
    ) -> dict[str, Any]:
        merged = deepcopy(parent)
        for key, value in child.items():
            if key == "extends":
                continue
            if key in {"defaults", "selection", "adaptation"} and isinstance(
                value, dict
            ):
                merged[key] = {
                    **deepcopy(merged.get(key, {})),
                    **deepcopy(value),
                }
            else:
                merged[key] = deepcopy(value)
        return merged

    @classmethod
    def _apply_legacy_content_roles(cls, template: dict[str, Any]) -> None:
        """Upgrade legacy templates at the pool boundary, not during composition."""

        for section in template.get("sections", []):
            for block in section.get("blocks", []):
                if not block.get("content_role"):
                    block["content_role"] = legacy_content_role(block)

    @staticmethod
    def _legacy_content_role(block: dict[str, Any]) -> str:
        return legacy_content_role(block)

    @staticmethod
    def _validate_content_roles(template: dict[str, Any]) -> None:
        for section in template.get("sections", []):
            for block in section.get("blocks", []):
                role = str(block.get("content_role", ""))
                if role not in CONTENT_ROLES:
                    raise ValueError(
                        f"Invalid content_role {role!r} in block "
                        f"{block.get('block_id')!r}."
                    )

    def _validate_definition(
        self,
        template: dict[str, Any],
        descriptor: dict[str, Any],
    ) -> None:
        """Validate the fully inherited definition at the pool boundary."""

        descriptor_id = str(descriptor.get("template_id"))
        descriptor_version = str(descriptor.get("version"))
        if str(template.get("template_id")) != descriptor_id:
            raise ValueError(
                f"Template descriptor {descriptor_id!r} loaded a definition "
                f"with template_id {template.get('template_id')!r}."
            )
        if str(template.get("version")) != descriptor_version:
            raise ValueError(
                f"Template {descriptor_id!r} manifest version "
                f"{descriptor_version!r} does not match definition version "
                f"{template.get('version')!r}."
            )
        schema_path = str(
            self.manifest().get("template_schema") or "template.schema.json"
        )
        schema = json.loads(
            self._root().joinpath(schema_path).read_text(encoding="utf-8")
        )
        try:
            Draft202012Validator(schema).validate(template)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path)
            suffix = f" at {location}" if location else ""
            raise ValueError(
                f"Invalid report template {descriptor_id!r}{suffix}: "
                f"{exc.message}"
            ) from exc


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
        candidates = self.template_pool.selection_candidates()
        policy = self.template_pool.selection_policy()
        scoped = _scoped_corpus_payload(spec, corpus_package)
        scoped["content_preview"] = self._content_preview(
            scoped.get("sources", []),
            policy,
            scoped.get("metadata", {}).get("ingested_documents", []),
        )
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
        candidate_ids = {str(item.get("template_id")) for item in candidates}
        valid_ids = {
            str(item.get("template_id"))
            for item in self.template_pool.list_templates()
        }
        proposed_id = payload.get("template_id") if isinstance(payload, dict) else None
        proposed_confidence = self._confidence(
            payload.get("confidence") if isinstance(payload, dict) else None
        )
        proposed_is_valid = (
            proposed_id in candidate_ids
            and proposed_confidence >= policy.minimum_confidence
        )
        previous_provenance = (
            previous_instance.get("provenance", {})
            if isinstance(previous_instance, dict)
            else {}
        )
        previous_confidence = self._confidence(
            previous_provenance.get("selection_confidence")
            if isinstance(previous_provenance, dict)
            else None
        )
        selected_id = None
        confidence = 0.0
        selection_mode = "deterministic_fallback"
        selection_reason = "Selected from objective, plan, and source signals."
        if requested_id:
            selected_id = requested_id
            confidence = 1.0
            selection_mode = "explicit"
            selection_reason = (
                "The template was explicitly requested by the execution spec."
            )
        elif proposed_is_valid:
            selected_id = proposed_id
            confidence = proposed_confidence
            selection_mode = (
                "llm_revision"
                if previous_id and str(previous_id) != str(proposed_id)
                else "llm"
            )
            selection_reason = str(
                payload.get("selection_reason", "Selected by TemplateAgent.")
            )
        elif previous_id in valid_ids:
            selected_id = previous_id
            confidence = previous_confidence
            selection_mode = "previous_instance"
            selection_reason = (
                "The revised evidence did not support a different template "
                "above the configured confidence policy, so the prior "
                "run-local selection was preserved."
            )
        elif proposed_id in candidate_ids:
            confidence = proposed_confidence
            selection_reason = (
                f"TemplateAgent confidence {confidence:.2f} was below the "
                f"pool threshold {policy.minimum_confidence:.2f}; the "
                "manifest-declared fallback was selected."
            )
        if selected_id not in valid_ids:
            selected_id = policy.fallback_template_id
            selection_mode = "deterministic_fallback"
            if selected_id not in valid_ids:
                raise ValueError(
                    "The template pool fallback_template_id is not in the manifest."
                )
            if not selection_reason:
                selection_reason = (
                    "No validated content-aware selection was available; the "
                    "template pool raw fallback was selected."
                )
        definition = self.template_pool.get(str(selected_id))
        # A blueprint is meaningful only for the definition whose archetypes
        # the model selected. Never apply a rejected, low-confidence, or
        # different-domain blueprint to an explicit/fallback template.
        adaptation_payload = (
            payload
            if isinstance(payload, dict)
            and str(payload.get("template_id")) == str(selected_id)
            else {}
        )
        sections = self._adapt_sections(
            definition,
            adaptation_payload,
        )
        design_issues = self._instance_design_issues(sections)
        if self.llm is not None and design_issues:
            repaired_payload = self._invoke_json(
                user_goal=spec.objective,
                plan=plan,
                corpus_summary=scoped,
                candidate_templates=candidates,
                previous_instance=previous_instance,
                previous_candidate=payload,
                validation_feedback=design_issues,
            )
            if (
                isinstance(repaired_payload, dict)
                and str(repaired_payload.get("template_id")) == str(selected_id)
            ):
                repaired_sections = self._adapt_sections(
                    definition,
                    repaired_payload,
                )
                repaired_issues = self._instance_design_issues(repaired_sections)
                if len(repaired_issues) < len(design_issues):
                    payload = repaired_payload
                    adaptation_payload = repaired_payload
                    sections = repaired_sections
                    design_issues = repaired_issues
        requested_blueprint = (
            adaptation_payload.get("instance_blueprint", {}).get("sections")
            if isinstance(adaptation_payload.get("instance_blueprint"), dict)
            else None
        )
        llm_blueprint_applied = bool(
            isinstance(requested_blueprint, list)
            and requested_blueprint
            and sections != definition.get("sections", [])
        )
        design_source = (
            "llm_blueprint" if llm_blueprint_applied else "canonical_template"
        )

        # A high-confidence domain or vocabulary match must not force the
        # canonical page skeleton when the model did not provide a usable
        # instance design. Reuse the manifest-declared neutral fallback only
        # when its data requirements are compatible with the selected contract.
        # Explicit template requests intentionally retain their canonical layout.
        role_definition = definition
        if (
            (not llm_blueprint_applied or design_issues)
            and selection_mode != "explicit"
            and str(selected_id) != policy.fallback_template_id
        ):
            neutral = self.template_pool.get(policy.fallback_template_id)
            available_requirements = {
                str(item.get("requirement_id"))
                for item in definition.get("data_requirements", [])
                if item.get("requirement_id")
            }
            neutral_requirements = {
                str(ref)
                for section in neutral.get("sections", [])
                for block in section.get("blocks", [])
                for ref in block.get("data_requirement_refs", [])
                if str(ref)
            }
            if neutral_requirements.issubset(available_requirements):
                sections = deepcopy(neutral.get("sections", []))
                design_source = "adaptive_fallback"
                role_definition = neutral
        agent_requested_roles = self._resolve_presentation_contract(
            spec,
            plan,
            scoped,
            role_definition,
            adaptation_payload,
            previous_instance,
        )
        sections = self._ensure_requested_content_roles(
            sections,
            role_definition,
            spec,
            agent_requested_roles,
        )
        return self._materialize_instance(
            definition,
            plan,
            previous_instance,
            selection_reason,
            sections=sections,
            confidence=confidence,
            content_profile=(
                deepcopy(payload.get("content_profile", {}))
                if isinstance(payload, dict)
                and isinstance(payload.get("content_profile"), dict)
                else {}
            ),
            title_strategy=(
                str(payload.get("title_strategy", "")).strip()
                if isinstance(payload, dict)
                else ""
            ),
            selection_mode=selection_mode,
            design_source=design_source,
            requested_content_roles=agent_requested_roles,
        )

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _instance_design_issues(
        sections: list[dict[str, Any]],
    ) -> list[str]:
        """Validate analytical jobs without imposing a page skeleton or domain."""

        required_development = [
            block
            for section in sections
            if isinstance(section, dict)
            for block in section.get("blocks", [])
            if isinstance(block, dict)
            and bool(block.get("required", False))
            and normalize_content_role(block.get("content_role"))
            in {"narrative", "implication"}
        ]
        if required_development:
            return []
        return [
            "The run-local instance has no required analytical-development block "
            "beyond orientation. Add at least one required narrative or implication "
            "block whose purpose develops evidence, interpretation, drivers, context, "
            "trade-offs, or consequences appropriate to the objective."
        ]

    def _resolve_presentation_contract(
        self,
        spec: ExecutionSpec,
        plan: dict[str, Any],
        scoped: dict[str, Any],
        definition: dict[str, Any],
        adaptation_payload: dict[str, Any],
        previous_instance: dict[str, Any] | None,
    ) -> list[str]:
        """Resolve explicit presentation capabilities at the Markdown boundary.

        Structured callers publish ``report_content_roles`` directly. The web
        confirmation flow currently preserves a human-readable Markdown spec,
        so TemplateAgent performs one focused semantic resolution and records
        the result in instance provenance for later negotiation passes.
        """

        roles = set(self._requested_content_roles(spec))
        roles.update(
            str(role)
            for role in _list_value(
                adaptation_payload.get("requested_content_roles")
            )
            if str(role) in CONTENT_ROLES
        )
        previous_provenance = (
            previous_instance.get("provenance", {})
            if isinstance(previous_instance, dict)
            else {}
        )
        prior_roles = {
            str(role)
            for role in _list_value(
                previous_provenance.get("requested_content_roles")
                if isinstance(previous_provenance, dict)
                else None
            )
            if str(role) in CONTENT_ROLES
        }
        roles.update(prior_roles)
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        confirmed_markdown = str(
            constraints.get("confirmed_spec_markdown") or ""
        ).strip()
        if (
            self.llm is not None
            and confirmed_markdown
            and not prior_roles
            and not self._requested_content_roles(spec)
        ):
            advertised_roles = sorted(
                {
                    str(block.get("content_role"))
                    for section in definition.get("sections", [])
                    for block in section.get("blocks", [])
                    if str(block.get("content_role")) in CONTENT_ROLES
                }
            )
            resolution = self._invoke_json(
                task="resolve_presentation_contract",
                user_goal=spec.objective,
                confirmed_spec_markdown=confirmed_markdown,
                plan=plan,
                corpus_summary=scoped,
                advertised_content_roles=advertised_roles,
            )
            roles.update(
                str(role)
                for role in _list_value(
                    resolution.get("requested_content_roles")
                    if isinstance(resolution, dict)
                    else None
                )
                if str(role) in advertised_roles
            )
        return sorted(roles)

    @staticmethod
    def _ensure_requested_content_roles(
        sections: list[dict[str, Any]],
        definition: dict[str, Any],
        spec: ExecutionSpec,
        agent_requested_roles: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Restore content capabilities declared in the structured spec.

        The report workflow does not scan the user's prose for keywords. The
        Spec Builder owns language interpretation and publishes normalized
        content roles; the Template Agent applies those roles to any compatible
        blueprint shape.
        """

        requested_roles = {
            *TemplateAgent._requested_content_roles(spec),
            *(
                str(role)
                for role in _list_value(agent_requested_roles)
                if str(role) in CONTENT_ROLES
            ),
        }
        if not requested_roles:
            return sections

        restored = deepcopy(sections)
        required_requested_roles = requested_roles - {"chart"}
        for section in restored:
            for block in section.get("blocks", []):
                if (
                    isinstance(block, dict)
                    and str(block.get("content_role")) in required_requested_roles
                ):
                    block["required"] = True
        sections_by_id = {
            str(section.get("section_id")): section
            for section in restored
            if section.get("section_id")
        }
        present_roles = {
            str(block.get("content_role"))
            for section in restored
            for block in section.get("blocks", [])
            if isinstance(block, dict)
        }
        for canonical_section in definition.get("sections", []):
            missing_blocks = [
                deepcopy(block)
                for block in canonical_section.get("blocks", [])
                if str(block.get("content_role")) in requested_roles
                and str(block.get("content_role")) not in present_roles
            ]
            if not missing_blocks:
                continue
            for block in missing_blocks:
                if str(block.get("content_role")) in required_requested_roles:
                    block["required"] = True
            section_id = str(canonical_section.get("section_id") or "")
            target = sections_by_id.get(section_id)
            if target is None:
                target = {
                    key: deepcopy(value)
                    for key, value in canonical_section.items()
                    if key != "blocks"
                }
                target["blocks"] = []
                restored.append(target)
                if section_id:
                    sections_by_id[section_id] = target
            target.setdefault("blocks", []).extend(missing_blocks)
            present_roles.update(
                str(block.get("content_role")) for block in missing_blocks
            )
        return restored

    @staticmethod
    def _requested_content_roles(spec: ExecutionSpec) -> set[str]:
        constraints = (
            spec.constraints if isinstance(spec.constraints, dict) else {}
        )
        candidates: list[Any] = [constraints.get("report_content_roles")]
        output_requirements = constraints.get("output_requirements")
        if isinstance(output_requirements, dict):
            candidates.append(output_requirements.get("content_roles"))
        for requirement in spec.capability_requirements:
            for container in (
                requirement.constraints,
                requirement.metadata,
                requirement.output_schema,
            ):
                if isinstance(container, dict):
                    candidates.append(container.get("report_content_roles"))
                    candidates.append(container.get("content_roles"))
        return {
            str(role)
            for candidate in candidates
            for role in _list_value(candidate)
            if str(role) in CONTENT_ROLES
        }

    @staticmethod
    def _content_preview(
        sources: list[str],
        policy: TemplateSelectionPolicy,
        ingested_documents: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        remaining = policy.max_preview_characters
        previews = []
        ingested_documents = (
            ingested_documents if isinstance(ingested_documents, list) else []
        )
        ingested_by_source = {
            str(item.get("source_ref")): item
            for item in ingested_documents
            if isinstance(item, dict) and item.get("source_ref")
        }
        for source in sources:
            ingested = ingested_by_source.get(str(source))
            if ingested is not None:
                item = {
                    "source": str(source),
                    "name": str(
                        ingested.get("file_name")
                        or ingested.get("document_id")
                        or source
                    ),
                    "extension": Path(
                        str(ingested.get("file_name") or "")
                    ).suffix.lower(),
                    "document_id": ingested.get("document_id"),
                    "content_types": deepcopy(
                        ingested.get("content_types", [])
                    ),
                    "content_profile": deepcopy(
                        ingested.get("content_profile", {})
                    ),
                    "artifact_ref": ingested.get("artifact_ref"),
                }
                source_previews = ingested.get("previews")
                source_previews = (
                    source_previews if isinstance(source_previews, dict) else {}
                )
                content = "\n\n".join(
                    f"[{content_type}]\n{text}"
                    for content_type, text in source_previews.items()
                    if str(text).strip()
                )
                sample = content[:remaining]
                if sample:
                    item["content"] = sample
                    item["preview_truncated"] = len(content) > len(sample)
                    remaining -= len(sample)
                previews.append(item)
                continue
            path = Path(str(source))
            item: dict[str, Any] = {
                "source": str(source),
                "name": path.name,
                "extension": path.suffix.lower(),
            }
            if path.is_file():
                item["size_bytes"] = path.stat().st_size
            if (
                remaining > 0
                and path.is_file()
                and path.suffix.lower() in policy.text_extensions
            ):
                try:
                    sample = path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )[:remaining]
                except OSError as exc:
                    item["preview_error"] = type(exc).__name__
                else:
                    item["content"] = sample
                    item["preview_truncated"] = path.stat().st_size > len(
                        sample.encode("utf-8")
                    )
                    remaining -= len(sample)
            previews.append(item)
        return previews

    def _source_template(
        self,
        corpus_package: DataCorpusPackage,
    ) -> str | None:
        """Compatibility helper backed by manifest source signals."""

        extensions = {
            Path(str(source)).suffix.lower()
            for source in corpus_package.sources
            if Path(str(source)).suffix
        }
        matches = [
            str(descriptor.get("template_id"))
            for descriptor in self.template_pool.list_templates()
            if extensions
            & {
                str(item).lower()
                for item in descriptor.get("source_extensions", [])
            }
        ]
        return matches[0] if matches else None

    def _fallback_selection(
        self,
        spec: ExecutionSpec,
        plan: dict[str, Any],
        corpus_package: DataCorpusPackage,
    ) -> tuple[str, str]:
        del spec, plan, corpus_package
        fallback_id = self.template_pool.selection_policy().fallback_template_id
        return (
            fallback_id,
            "No LLM selection passed the configured confidence threshold; "
            "the manifest-declared raw fallback was selected.",
        )

    def _adapt_sections(
        self,
        definition: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blueprint = payload.get("instance_blueprint")
        if not isinstance(blueprint, dict):
            return deepcopy(definition.get("sections", []))
        requested_sections = blueprint.get("sections")
        if not isinstance(requested_sections, list) or not requested_sections:
            return deepcopy(definition.get("sections", []))
        adaptation = definition.get("adaptation", {})
        min_sections = _int_value(adaptation.get("min_sections"), 1)
        max_sections = _int_value(
            adaptation.get("max_sections"),
            max(min_sections, len(definition.get("sections", []))),
        )
        if not min_sections <= len(requested_sections) <= max_sections:
            return deepcopy(definition.get("sections", []))
        allowed_roles = {
            str(item)
            for item in adaptation.get("allowed_content_roles", CONTENT_ROLES)
        }
        required_roles = {
            str(item)
            for item in adaptation.get("required_content_roles", [])
        }
        archetypes: dict[str, list[dict[str, Any]]] = {}
        archetypes_by_id: dict[str, dict[str, Any]] = {}
        for section in definition.get("sections", []):
            for block in section.get("blocks", []):
                archetypes.setdefault(str(block.get("content_role")), []).append(block)
                archetypes_by_id[str(block.get("block_id"))] = block
        sections = []
        used_ids: set[str] = set()
        used_section_ids: set[str] = set()
        produced_roles: set[str] = set()
        for section_index, requested_section in enumerate(
            requested_sections,
            start=1,
        ):
            if not isinstance(requested_section, dict):
                return deepcopy(definition.get("sections", []))
            blocks = []
            for block_index, requested_block in enumerate(
                requested_section.get("blocks", []),
                start=1,
            ):
                if not isinstance(requested_block, dict):
                    continue
                role = str(requested_block.get("content_role", ""))
                archetype_ref = str(
                    requested_block.get("archetype_ref") or ""
                ).strip()
                candidates = archetypes.get(role, [])
                if role not in allowed_roles:
                    continue
                if archetype_ref:
                    selected_archetype = archetypes_by_id.get(archetype_ref)
                    if (
                        selected_archetype is None
                        or str(selected_archetype.get("content_role")) != role
                    ):
                        continue
                elif candidates:
                    # Compatibility for saved blueprints created before
                    # archetype_ref became part of the contract.
                    selected_archetype = candidates[
                        (block_index - 1) % len(candidates)
                    ]
                else:
                    continue
                block = deepcopy(selected_archetype)
                block_id = _safe_id(
                    requested_block.get("block_id")
                    or f"{role}-{section_index}-{block_index}"
                )
                while block_id in used_ids:
                    block_id = f"{block_id}-{block_index}"
                used_ids.add(block_id)
                block["block_id"] = block_id
                block["title"] = str(
                    requested_block.get("title") or block.get("title") or ""
                )
                # Requiredness is owned by the run-local adaptation contract,
                # not by the page position of a canonical archetype. This lets
                # the architect omit domain-shaped filler while retaining the
                # evidence roles the selected contract actually requires.
                # Optional visuals remain optional.
                block["required"] = (
                    False
                    if role == "chart"
                    else bool(
                        role in required_roles
                        or requested_block.get(
                            "required", block.get("required", False)
                        )
                    )
                )
                if isinstance(requested_block.get("layout"), dict):
                    block["layout"] = {
                        **deepcopy(block.get("layout", {})),
                        **deepcopy(requested_block["layout"]),
                    }
                if isinstance(requested_block.get("instructions"), list):
                    canonical_instructions = [
                        str(item).strip()
                        for item in block.get("instructions", [])
                        if str(item).strip()
                    ]
                    run_instructions = [
                        str(item).strip()
                        for item in requested_block["instructions"]
                        if str(item).strip()
                    ]
                    block["instructions"] = list(
                        dict.fromkeys(canonical_instructions + run_instructions)
                    )
                if block.get("type") not in REPORT_BLOCK_TYPES:
                    continue
                if isinstance(block.get("chart_slot"), dict):
                    block["chart_slot"]["chart_slot_id"] = block_id
                blocks.append(block)
                produced_roles.add(role)
            if not blocks:
                continue
            section_id = _safe_id(
                requested_section.get("section_id") or f"section-{section_index}"
            )
            if section_id in used_section_ids:
                section_id = f"{section_id}-{section_index}"
            used_section_ids.add(section_id)
            section_layout = requested_section.get("layout")
            if not isinstance(section_layout, dict):
                section_layout = {}
            columns = min(
                12,
                max(1, _int_value(section_layout.get("columns"), 12)),
            )
            density = str(
                section_layout.get("density") or "comfortable"
            ).lower()
            if density not in {"compact", "comfortable", "detailed"}:
                density = "comfortable"
            sections.append(
                {
                    "section_id": section_id,
                    "title": str(
                        requested_section.get("title")
                        or f"Section {section_index}"
                    ),
                    "purpose": str(
                        requested_section.get("purpose")
                        or "Analyze the available evidence."
                    ),
                    "required": (
                        bool(requested_section.get("required", False))
                        or any(
                            bool(block.get("required", False))
                            for block in blocks
                        )
                    ),
                    "layout": {
                        "columns": columns,
                        "density": density,
                    },
                    "blocks": blocks,
                }
            )
        if (
            len(sections) < min_sections
            or not required_roles.issubset(produced_roles)
        ):
            return deepcopy(definition.get("sections", []))
        return sections[:max_sections]

    def _materialize_instance(
        self,
        definition: dict[str, Any],
        plan: dict[str, Any],
        previous_instance: dict[str, Any] | None,
        reason: str,
        *,
        sections: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
        content_profile: dict[str, Any] | None = None,
        title_strategy: str = "",
        selection_mode: str = "deterministic_fallback",
        design_source: str = "canonical_template",
        requested_content_roles: list[str] | None = None,
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
        instance_sections = deepcopy(
            sections if sections is not None else definition.get("sections", [])
        )
        for section in instance_sections:
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
                "confidence": confidence,
                "content_profile": deepcopy(content_profile or {}),
                "mode": selection_mode,
            },
            "template_instance": {
                "instance_id": f"template-instance-{_safe_id(definition.get('template_id'))}",
                "template_id": definition.get("template_id"),
                "template_version": definition.get("version"),
                "revision": revision,
                "status": "draft" if required_missing else status,
                "bindings": bindings,
                "sections": instance_sections,
                "applied_fallbacks": applied_fallbacks,
                "title_strategy": title_strategy,
                "provenance": {
                    "selection_mode": selection_mode,
                    "selection_confidence": confidence,
                    "design_source": design_source,
                    "llm_blueprint_applied": design_source == "llm_blueprint",
                    "canonical_template_id": definition.get("template_id"),
                    "canonical_template_version": definition.get("version"),
                    "requested_content_roles": sorted(
                        {
                            str(role)
                            for role in _list_value(requested_content_roles)
                            if str(role) in CONTENT_ROLES
                        }
                    ),
                },
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
