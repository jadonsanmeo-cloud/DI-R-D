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

from data_intelligence_sdk.engines.reporting.execution import DataScienceAgent
from data_intelligence_sdk.engines.reporting.policies import ChartPolicy, LocalePolicy
from data_intelligence_sdk.engines.reporting.utils import (
    _STEP_OUTPUT_REF,
    _StepOutputRegistry,
    _infer_schema,
    _list_value,
    _normalize_plan_inputs,
    _normalize_rows,
    _profile_rows,
    _safe_id,
)

class DataScienceProcessor:
    def __init__(
        self,
        agent: DataScienceAgent,
        max_inline_chart_rows: int | None = None,
        *,
        chart_policy: ChartPolicy | None = None,
        locale_policy: LocalePolicy | None = None,
    ) -> None:
        self.agent = agent
        self.chart_policy = chart_policy or ChartPolicy()
        if max_inline_chart_rows is not None:
            self.chart_policy = ChartPolicy(
                max_inline_rows=max_inline_chart_rows,
                max_dataset_rows=self.chart_policy.max_dataset_rows,
                max_categories=self.chart_policy.max_categories,
            )
        self.locale_policy = locale_policy or LocalePolicy.for_locale("en")

    def process(
        self,
        step: dict[str, Any],
        execution_result: dict[str, Any],
        runtime: EngineRuntimeContext,
        output_registry: _StepOutputRegistry,
        template_requirements: list[dict[str, Any]],
        upstream_step_results: list[dict[str, Any]],
        user_goal: str | None = None,
        locale_policy: LocalePolicy | None = None,
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
        chart_decision: dict[str, Any] = {
            "render": False,
            "reason": "No template chart consumes this analysis result.",
        }
        if chart_ids:
            chart_data = self._chart_dataset(
                decision,
                rows,
                self.chart_policy,
                locale_policy or self.locale_policy,
            )
            chart_decision = {
                key: deepcopy(chart_data.get(key))
                for key in (
                    "render",
                    "reason",
                    "analytical_purpose",
                    "evidence_claim",
                    "recommended_types",
                )
            }
            if chart_data["render"] and len(chart_data["rows"]) >= 2:
                chart_datasets.append(
                    {
                        "dataset_id": f"{step_id}-chart-data",
                        "for_chart_ids": chart_ids,
                        "shape": "category_series",
                        "artifact_ref": f"{artifact_ref}/chart-data",
                        "title": chart_data["title"],
                        "coverage": chart_data["coverage"],
                        "analytical_purpose": chart_data["analytical_purpose"],
                        "evidence_claim": chart_data["evidence_claim"],
                        "recommended_types": chart_data["recommended_types"],
                        "semantic_roles": {
                            "comparison_dimension": "category",
                            "primary_measure": "value",
                        },
                        "schema": _infer_schema(chart_data["rows"]),
                        "profile": _profile_rows(
                            chart_data["rows"],
                            chart_data["rows"],
                        ),
                        "data": deepcopy(
                            chart_data["rows"][: self.chart_policy.max_inline_rows]
                        ),
                        "truncated": bool(chart_data.get("truncated"))
                        or (
                            len(chart_data["rows"]) > self.chart_policy.max_inline_rows
                        ),
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
            "chart_decision": chart_decision,
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
        chart_policy: ChartPolicy | None = None,
        locale_policy: LocalePolicy | None = None,
    ) -> dict[str, Any]:
        policy = chart_policy or ChartPolicy()
        chart_data = decision.get("chart_data")
        chart_data = chart_data if isinstance(chart_data, dict) else {}
        requested = chart_data.get("render") is True
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
        render = requested and len(normalized_rows) >= 2
        reason = str(chart_data.get("reason") or "").strip()
        if requested and not render and not reason:
            reason = "Fewer than two valid objective-relevant chart rows were supplied."
        if not requested and not reason:
            reason = "The analysis did not identify a chart that would add evidence."
        return {
            "render": render,
            "reason": reason,
            "analytical_purpose": str(
                chart_data.get("analytical_purpose") or ""
            )[:300],
            "evidence_claim": str(chart_data.get("evidence_claim") or "")[:300],
            "recommended_types": [
                str(item)
                for item in _list_value(chart_data.get("recommended_types"))
                if str(item)
            ],
            "title": str(chart_data.get("title") or "Evidence distribution")[:120],
            "coverage": str(chart_data.get("coverage") or "materialized_result")[:200],
            "truncated": len(normalized_rows) > policy.max_dataset_rows,
            "source_row_count": len(normalized_rows),
            "rows": normalized_rows[: policy.max_dataset_rows],
        }

    @classmethod
    def _default_chart_rows(
        cls,
        rows: list[Any],
        chart_policy: ChartPolicy | None = None,
        locale_policy: LocalePolicy | None = None,
    ) -> list[dict[str, Any]]:
        policy = chart_policy or ChartPolicy()
        dict_rows = [row for row in rows if isinstance(row, dict)]
        scalar_numbers = [
            value
            for value in rows
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if len(scalar_numbers) >= 2:
            return [
                {"category": str(index + 1), "value": value}
                for index, value in enumerate(
                    scalar_numbers[: policy.max_dataset_rows]
                )
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
                for row in dict_rows[: policy.max_dataset_rows]
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
                    )[: policy.max_categories]
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
                    for category, count in frequencies.most_common(
                        policy.max_categories
                    )
                ]
        return cls._term_frequency_chart_rows(rows, policy, locale_policy)

    @staticmethod
    def _term_frequency_chart_rows(
        rows: list[Any],
        chart_policy: ChartPolicy | None = None,
        locale_policy: LocalePolicy | None = None,
    ) -> list[dict[str, Any]]:
        policy = chart_policy or ChartPolicy()
        stopwords = (locale_policy or LocalePolicy.for_locale("en")).stopwords
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
            for token, count in tokens.most_common(policy.max_categories)
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
                declined_outputs = []
                for output_ref in output_refs:
                    match = _STEP_OUTPUT_REF.match(output_ref)
                    result = results_by_step.get(match.group(1)) if match else None
                    if result is None:
                        unresolved_outputs.append(output_ref)
                        continue
                    chart_decision = result.get("chart_decision")
                    if (
                        isinstance(chart_decision, dict)
                        and chart_decision.get("render") is False
                    ):
                        declined_outputs.append(output_ref)
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
                    and not declined_outputs
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
                    "analytical_purpose": (
                        datasets[0].get("analytical_purpose") if datasets else None
                    ),
                    "evidence_claim": (
                        datasets[0].get("evidence_claim") if datasets else None
                    ),
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
                                "Analysis declined a non-material chart for: "
                                + ", ".join(declined_outputs)
                            ]
                            if declined_outputs
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
