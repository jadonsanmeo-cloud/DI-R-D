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
from data_intelligence_sdk.engines.reporting.policies import (
    CONTENT_ROLES,
    DEFAULT_SOURCE_MATERIALIZATION_REGISTRY,
    REPORT_BLOCK_TYPES,
    SourceMaterializationRegistry,
    TemplateSelectionPolicy,
    legacy_content_role,
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
                            "semantic_roles": ["source_content", "goal_evidence"],
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
            steps.append(
                {
                    "step_id": f"retrieve-{_safe_id(document)}",
                    "description": (
                        f"Retrieve objective-relevant content from document "
                        f"`{document}`."
                    ),
                    "required": True,
                    "inputs": [
                        {
                            "ref": f"corpus://{document}",
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
                    "operation": {
                        "kind": (
                            document_handler.capability_id
                            if document_handler is not None
                            else "retrieve_document_content"
                        )
                    },
                    "outputs": [
                        {
                            "name": f"{_safe_id(document)}-content",
                            "shape": "table",
                            "semantic_roles": ["source_content", "goal_evidence"],
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
        """Return complete LLM-facing descriptors without exposing file paths."""

        candidates = []
        for descriptor in self.list_templates():
            if descriptor.get("llm_candidate") is not True:
                continue
            definition = self.get(str(descriptor.get("template_id")))
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
        selected_id = requested_id or previous_id
        confidence = 1.0 if selected_id else 0.0
        selection_reason = (
            "The template was explicitly requested by the execution spec."
            if requested_id
            else (
                "The existing run-local template selection was preserved."
                if previous_id
                else "Selected from objective, plan, and source signals."
            )
        )
        candidate_ids = {str(item.get("template_id")) for item in candidates}
        valid_ids = {
            str(item.get("template_id"))
            for item in self.template_pool.list_templates()
        }
        if selected_id is None and isinstance(payload, dict):
            proposed_id = payload.get("template_id")
            confidence = self._confidence(payload.get("confidence"))
            if (
                proposed_id in candidate_ids
                and confidence >= policy.minimum_confidence
            ):
                selected_id = proposed_id
                selection_reason = str(
                    payload.get("selection_reason", "Selected by TemplateAgent.")
                )
            elif proposed_id in candidate_ids:
                selection_reason = (
                    f"TemplateAgent confidence {confidence:.2f} was below the "
                    f"pool threshold {policy.minimum_confidence:.2f}; the "
                    "declared raw fallback was selected."
                )
        if selected_id not in valid_ids:
            selected_id = policy.fallback_template_id
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
        sections = self._adapt_sections(
            definition,
            payload if isinstance(payload, dict) else {},
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
        )

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _content_preview(
        sources: list[str],
        policy: TemplateSelectionPolicy,
    ) -> list[dict[str, Any]]:
        remaining = policy.max_preview_characters
        previews = []
        for source in sources:
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
        for section in definition.get("sections", []):
            for block in section.get("blocks", []):
                archetypes.setdefault(str(block.get("content_role")), []).append(block)
        sections = []
        used_ids: set[str] = set()
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
                candidates = archetypes.get(role, [])
                if role not in allowed_roles or not candidates:
                    continue
                block = deepcopy(candidates[(block_index - 1) % len(candidates)])
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
                block["required"] = bool(block.get("required")) and bool(
                    requested_block.get("required", block.get("required"))
                )
                if isinstance(requested_block.get("layout"), dict):
                    block["layout"] = {
                        **deepcopy(block.get("layout", {})),
                        **deepcopy(requested_block["layout"]),
                    }
                if isinstance(requested_block.get("instructions"), list):
                    block["instructions"] = [
                        str(item)
                        for item in requested_block["instructions"]
                        if str(item).strip()
                    ]
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
                    "required": bool(requested_section.get("required", False)),
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
