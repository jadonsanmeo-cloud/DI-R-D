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
from data_intelligence_sdk.engines.reporting.policies import (
    AnalysisSamplingPolicy,
    ChartPolicy,
    LocalePolicy,
    ReportPresentationPolicy,
    normalize_content_role,
)
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


_NON_ANALYTICAL_CHART_FIELDS = frozenset(
    {
        "relevance_score",
        "retrieval_score",
        "similarity_score",
        "vector_distance",
        "retrieval_rank",
        "chunk_index",
        "source_index",
    }
)

class DataScienceProcessor:
    def __init__(
        self,
        agent: DataScienceAgent,
        max_inline_chart_rows: int | None = None,
        *,
        chart_policy: ChartPolicy | None = None,
        sampling_policy: AnalysisSamplingPolicy | None = None,
        presentation_policy: ReportPresentationPolicy | None = None,
        locale_policy: LocalePolicy | None = None,
    ) -> None:
        self.agent = agent
        self.chart_policy = chart_policy or ChartPolicy()
        if max_inline_chart_rows is not None:
            self.chart_policy = ChartPolicy(
                max_inline_rows=max_inline_chart_rows,
                max_dataset_rows=self.chart_policy.max_dataset_rows,
                max_categories=self.chart_policy.max_categories,
                max_measures=self.chart_policy.max_measures,
            )
        self.locale_policy = locale_policy or LocalePolicy.for_locale("en")
        self.sampling_policy = sampling_policy or AnalysisSamplingPolicy()
        self.presentation_policy = presentation_policy or ReportPresentationPolicy()

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
        raw_data, derived_warnings = self._normalize_derived_metrics(
            execution_result.get("raw_result")
        )
        rows = self._analysis_rows(step, raw_data)
        step_id = str(step.get("step_id", "step"))
        output_descriptors: list[dict[str, Any]] = []
        materialization_warnings: list[str] = list(derived_warnings)
        if execution_result.get("status") in {"completed", "completed_no_data"}:
            output_descriptors, registry_warnings = output_registry.register(
                step,
                raw_data,
                runtime,
            )
            materialization_warnings.extend(registry_warnings)
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
            "sample": self._analysis_sample(
                rows,
                max_rows=self.sampling_policy.max_records,
                max_string_chars=self.sampling_policy.max_string_characters,
                max_nested_items=self.sampling_policy.max_nested_items,
                string_segments=self.sampling_policy.string_segments,
            ),
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
        if self._analysis_has_unsupported_numbers(decision, raw_data):
            fallback = self.agent._fallback_analysis(
                step,
                materialized,
                raw_data,
                template_requirements,
            )
            decision = self._ground_report_content(decision, fallback, raw_data)
        decision = self._normalize_trusted_analysis(
            decision,
            execution_result,
            rows,
            raw_data,
            artifact_ref,
        )
        decision["report_content"] = self._normalize_report_content(
            decision,
            template_requirements,
            max_block_items=self.presentation_policy.max_insight_items,
        )
        decision["aggregated_data"] = self._overview_aggregated_data(
            decision.get("aggregated_data"),
            rows,
            max_metrics=self.presentation_policy.max_kpi_items,
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
                    "measure",
                    "unit",
                    "encoding",
                    "measures",
                )
            }
            if chart_data["render"] and len(chart_data["rows"]) >= 2:
                chart_datasets.append(
                    {
                        "dataset_id": f"{step_id}-chart-data",
                        "for_chart_ids": chart_ids,
                        "shape": "records",
                        "artifact_ref": f"{artifact_ref}/chart-data",
                        "title": chart_data["title"],
                        "coverage": chart_data["coverage"],
                        "analytical_purpose": chart_data["analytical_purpose"],
                        "evidence_claim": chart_data["evidence_claim"],
                        "recommended_types": chart_data["recommended_types"],
                        "measure": chart_data["measure"],
                        "unit": chart_data["unit"],
                        "semantic_roles": deepcopy(
                            chart_data.get("semantic_roles", {})
                        ),
                        "encoding": deepcopy(chart_data.get("encoding", {})),
                        "measures": deepcopy(chart_data.get("measures", [])),
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

    @classmethod
    def _normalize_derived_metrics(cls, value: Any) -> tuple[Any, list[str]]:
        """Recalculate explicitly declared relative percentages from row values."""

        warnings: list[str] = []

        def normalize(item: Any) -> Any:
            if isinstance(item, list):
                return [normalize(child) for child in item]
            if not isinstance(item, dict):
                return deepcopy(item)
            normalized = {str(key): normalize(child) for key, child in item.items()}
            percent_fields = [
                key
                for key, child in normalized.items()
                if isinstance(child, (int, float))
                and not isinstance(child, bool)
                and "percent" in key.casefold()
                and "change" in key.casefold()
            ]
            comparison_fields = [
                key
                for key, child in normalized.items()
                if isinstance(child, (int, float))
                and not isinstance(child, bool)
                and any(
                    token in key.casefold()
                    for token in ("comparison", "baseline", "previous", "reference")
                )
            ]
            value_fields = [
                key
                for key, child in normalized.items()
                if isinstance(child, (int, float))
                and not isinstance(child, bool)
                and key not in percent_fields
                and key not in comparison_fields
                and key.casefold() in {"value", "metric_value", "current_value"}
            ]
            if not percent_fields or not comparison_fields or not value_fields:
                return normalized
            current = float(normalized[value_fields[0]])
            comparison = float(normalized[comparison_fields[0]])
            if comparison == 0:
                return normalized
            expected = (current - comparison) / abs(comparison) * 100
            for field in percent_fields:
                supplied = float(normalized[field])
                tolerance = max(0.1, abs(expected) * 0.005)
                if abs(supplied - expected) > tolerance:
                    normalized[field] = round(expected, 1)
                    warnings.append(
                        "A derived percentage was recalculated from its declared "
                        "value and comparison fields."
                    )
            return normalized

        return normalize(value), list(dict.fromkeys(warnings))

    @classmethod
    def _analysis_has_unsupported_numbers(
        cls,
        decision: dict[str, Any],
        raw_data: Any,
    ) -> bool:
        trusted = cls._numeric_evidence_values(raw_data)
        report_values = [
            decision.get("analysis_summary"),
            decision.get("observations"),
            decision.get("report_content"),
            decision.get("chart_data", {}).get("evidence_claim")
            if isinstance(decision.get("chart_data"), dict)
            else None,
        ]
        return any(not cls._value_is_numerically_grounded(value, trusted) for value in report_values)

    @classmethod
    def _ground_report_content(
        cls,
        decision: dict[str, Any],
        fallback: dict[str, Any],
        raw_data: Any,
    ) -> dict[str, Any]:
        grounded = deepcopy(decision)
        trusted = cls._numeric_evidence_values(raw_data)
        if not cls._value_is_numerically_grounded(
            grounded.get("analysis_summary"), trusted
        ):
            grounded["analysis_summary"] = fallback.get("analysis_summary")
        grounded["observations"] = [
            item
            for item in _list_value(grounded.get("observations"))
            if cls._value_is_numerically_grounded(item, trusted)
        ] or deepcopy(_list_value(fallback.get("observations")))

        supplied = grounded.get("report_content")
        supplied = deepcopy(supplied) if isinstance(supplied, dict) else {}
        fallback_content = fallback.get("report_content")
        fallback_content = (
            fallback_content if isinstance(fallback_content, dict) else {}
        )
        for field in (
            "executive_summary",
            "key_findings",
            "supporting_evidence",
            "implications",
            "recommendations",
            "limitations",
            "evidence_items",
        ):
            value = supplied.get(field)
            if isinstance(value, list):
                filtered = [
                    item
                    for item in value
                    if cls._value_is_numerically_grounded(item, trusted)
                ]
                supplied[field] = filtered or deepcopy(
                    _list_value(fallback_content.get(field))
                )
            elif not cls._value_is_numerically_grounded(value, trusted):
                supplied[field] = deepcopy(fallback_content.get(field))
        block_content = supplied.get("block_content")
        if isinstance(block_content, dict):
            supplied["block_content"] = {
                str(block_id): payload
                for block_id, payload in block_content.items()
                if cls._value_is_numerically_grounded(payload, trusted)
            }
        grounded["report_content"] = supplied
        chart_data = grounded.get("chart_data")
        if isinstance(chart_data, dict) and not cls._value_is_numerically_grounded(
            chart_data.get("evidence_claim"), trusted
        ):
            replacement_claim = cls._chart_claim_from_grounded_rows(
                chart_data,
                trusted,
            )
            if replacement_claim:
                chart_data = deepcopy(chart_data)
                chart_data["evidence_claim"] = replacement_claim
                grounded["chart_data"] = chart_data
            else:
                grounded["chart_data"] = deepcopy(fallback.get("chart_data", {}))
        return grounded

    @classmethod
    def _chart_claim_from_grounded_rows(
        cls,
        chart_data: dict[str, Any],
        trusted: list[float],
    ) -> str:
        rows = [
            row
            for row in _list_value(chart_data.get("rows"))
            if isinstance(row, dict)
        ]
        encoding = chart_data.get("encoding")
        encoding = encoding if isinstance(encoding, dict) else {}
        dimension = str(
            encoding.get("dimension")
            or encoding.get("x")
            or encoding.get("x_field")
            or ""
        ).strip()
        measures = [
            str(field)
            for field in (
                _list_value(encoding.get("measures"))
                or _list_value(encoding.get("measure"))
                or _list_value(encoding.get("y"))
            )
            if str(field)
        ]
        if len(rows) < 2 or not dimension or not measures:
            return ""
        measure = measures[0]
        valid_rows = [
            row
            for row in rows
            if row.get(dimension) not in (None, "")
            and isinstance(row.get(measure), (int, float))
            and not isinstance(row.get(measure), bool)
            and cls._value_is_numerically_grounded(str(row.get(measure)), trusted)
        ]
        if len(valid_rows) < 2:
            return ""
        first = valid_rows[0]
        last = valid_rows[-1]
        label = re.sub(r"[_\-.]+", " ", measure).strip()
        label = label[:1].upper() + label[1:] if label else "Measure"
        return (
            f"Across {first.get(dimension)} to {last.get(dimension)}, "
            f"{label} changed from {first.get(measure)} to {last.get(measure)}."
        )

    @classmethod
    def _numeric_evidence_values(cls, value: Any) -> list[float]:
        values: list[float] = []

        def collect(item: Any) -> None:
            if isinstance(item, bool):
                return
            if isinstance(item, (int, float)):
                values.append(float(item))
            elif isinstance(item, str):
                for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", item):
                    try:
                        values.append(float(token.replace(",", "")))
                    except ValueError:
                        continue
            elif isinstance(item, dict):
                for child in item.values():
                    collect(child)
            elif isinstance(item, (list, tuple, set)):
                for child in item:
                    collect(child)

        collect(value)
        return values

    @classmethod
    def _value_is_numerically_grounded(
        cls,
        value: Any,
        trusted: list[float],
    ) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            candidates = cls._numeric_evidence_values(value)
            return all(
                any(
                    min(
                        abs(candidate - evidence),
                        abs(abs(candidate) - abs(evidence)),
                    )
                    <= max(0.05, abs(evidence) * 0.005)
                    for evidence in trusted
                )
                for candidate in candidates
            )
        if isinstance(value, dict):
            return all(
                cls._value_is_numerically_grounded(child, trusted)
                for child in value.values()
            )
        if isinstance(value, (list, tuple, set)):
            return all(
                cls._value_is_numerically_grounded(child, trusted)
                for child in value
            )
        return True

    @staticmethod
    def _analysis_rows(step: dict[str, Any], raw_data: Any) -> list[Any]:
        """Flatten declared named outputs for profiling and bounded analysis.

        A multi-output semantic step returns an object keyed by output name. Treating
        that object as one record hides every contained table from analysis.
        """

        outputs = [
            str(output.get("name") or "").strip()
            for output in _list_value(step.get("outputs"))
            if isinstance(output, dict) and str(output.get("name") or "").strip()
        ]
        if len(outputs) < 2 or not isinstance(raw_data, dict):
            return _normalize_rows(raw_data)
        matched = [name for name in outputs if name in raw_data]
        if len(matched) < 2:
            return _normalize_rows(raw_data)
        rows: list[Any] = []
        for name in matched:
            for value in _normalize_rows(raw_data.get(name)):
                if isinstance(value, dict):
                    rows.append({"output_name": name, **deepcopy(value)})
                else:
                    rows.append({"output_name": name, "value": deepcopy(value)})
        return rows

    @staticmethod
    def _normalize_report_content(
        decision: dict[str, Any],
        template_requirements: list[dict[str, Any]] | None = None,
        *,
        max_block_items: int | None = None,
    ) -> dict[str, Any]:
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
            "recommendation": [],
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
        normalized = {
            "executive_summary": summary,
            "key_findings": items("key_findings", categorized["finding"]),
            "supporting_evidence": items(
                "supporting_evidence",
                categorized["evidence"],
            ),
            "implications": items("implications", categorized["implication"]),
            "recommendations": items(
                "recommendations",
                categorized["recommendation"],
            ),
            "limitations": items("limitations", categorized["limitation"]),
        }
        evidence_items = [
            deepcopy(item)
            for item in _list_value(content.get("evidence_items"))
            if isinstance(item, dict)
            and str(
                item.get("statement")
                or item.get("text")
                or item.get("content")
                or ""
            ).strip()
        ]
        if not evidence_items:
            legacy_roles = {
                "key_findings": "key_findings",
                "supporting_evidence": "supporting_evidence",
                "implications": "implication",
                "recommendations": "recommendation",
                "limitations": "limitation",
            }
            for field, role in legacy_roles.items():
                for value in normalized[field]:
                    if isinstance(value, dict):
                        item = deepcopy(value)
                    else:
                        item = {"statement": str(value)}
                    item.setdefault("kind", role)
                    item["content_roles"] = list(
                        dict.fromkeys(
                            [
                                str(existing)
                                for existing in _list_value(
                                    item.get("content_roles")
                                )
                                if str(existing)
                            ]
                            + [role]
                        )
                    )
                    evidence_items.append(item)

        for item in evidence_items:
            roles = [
                normalize_content_role(value)
                for value in _list_value(item.get("content_roles"))
                if normalize_content_role(value)
            ]
            kind = normalize_content_role(
                item.get("kind") or item.get("category") or ""
            )
            if kind in {
                "key_findings",
                "supporting_evidence",
                "implication",
                "recommendation",
                "limitation",
                "narrative",
            }:
                roles.append(kind)
            item["content_roles"] = list(dict.fromkeys(roles))

        role_fields = {
            "key_findings": "key_findings",
            "supporting_evidence": "supporting_evidence",
            "implication": "implications",
            "recommendation": "recommendations",
            "limitation": "limitations",
        }
        for role, field in role_fields.items():
            if normalized[field]:
                continue
            normalized[field] = [
                deepcopy(item)
                for item in evidence_items
                if role in _list_value(item.get("content_roles"))
            ]
        normalized["evidence_items"] = evidence_items

        block_content: dict[str, dict[str, Any]] = {}
        supplied_blocks = content.get("block_content")
        if isinstance(supplied_blocks, dict):
            for block_id, payload in supplied_blocks.items():
                normalized_id = str(block_id).strip()
                if not normalized_id or not isinstance(payload, dict):
                    continue
                block_payload: dict[str, Any] = {}
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    block_payload["text"] = text.strip()
                for field in ("items", "metrics", "rows"):
                    values = payload.get(field)
                    if isinstance(values, list):
                        block_payload[field] = deepcopy(values)
                if block_payload:
                    block_content[normalized_id] = block_payload

        def matching_items(role: str) -> list[dict[str, Any]]:
            canonical = normalize_content_role(role)
            values = [
                deepcopy(item)
                for item in evidence_items
                if canonical in _list_value(item.get("content_roles"))
            ]
            limit = max_block_items or ReportPresentationPolicy().max_insight_items
            return values[:limit]

        for requirement in template_requirements or []:
            for block in _list_value(requirement.get("consumer_blocks")):
                if not isinstance(block, dict):
                    continue
                block_id = str(block.get("block_id") or "").strip()
                if not block_id or block_id in block_content:
                    continue
                block_type = str(block.get("type") or "").strip()
                role = normalize_content_role(block.get("content_role"))
                selected = matching_items(role)
                if block_type in {"insight_grid", "evidence_list", "process_flow"}:
                    if selected:
                        block_content[block_id] = {"items": selected}
                elif block_type == "recommendations":
                    if selected:
                        block_content[block_id] = {"items": selected}
                elif block_type == "narrative":
                    if role == "executive_summary" and summary:
                        block_content[block_id] = {"text": summary}
                    else:
                        if not selected and role == "narrative":
                            selected = (
                                matching_items("key_findings")
                                or matching_items("implication")
                                or matching_items("supporting_evidence")
                            )
                            normalized_summary = re.sub(
                                r"\s+", " ", summary
                            ).casefold()
                            selected = [
                                item
                                for item in selected
                                if re.sub(
                                    r"\s+",
                                    " ",
                                    ": ".join(
                                        value
                                        for value in (
                                            str(item.get("title") or "").strip(),
                                            str(
                                                item.get("statement")
                                                or item.get("text")
                                                or ""
                                            ).strip(),
                                        )
                                        if value
                                    ),
                                )
                                .casefold()
                                .strip()
                                not in normalized_summary
                            ]
                        if selected:
                            block_content[block_id] = {"items": selected}
        normalized["block_content"] = block_content
        return normalized

    @classmethod
    def _overview_aggregated_data(
        cls,
        supplied: Any,
        rows: list[Any],
        *,
        max_metrics: int | None = None,
    ) -> dict[str, Any]:
        metric_limit = max_metrics or ReportPresentationPolicy().max_kpi_items
        aggregated = supplied if isinstance(supplied, dict) else {}
        source_context = deepcopy(aggregated.get("source_context", {}))
        structural = cls._structural_overview_metrics(rows)
        selected: dict[str, Any] = {}
        normalized_names: set[str] = set()

        def add(name: str, value: Any) -> None:
            if len(selected) >= metric_limit:
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
            normalized = {}
            for raw_name, value in row.items():
                name = str(raw_name).strip()
                if not name or isinstance(value, (dict, list, tuple, set)):
                    continue
                normalized[name] = value[:200] if isinstance(value, str) else value
            if not normalized:
                continue
            normalized_rows.append(normalized)

        fields = list(
            dict.fromkeys(name for row in normalized_rows for name in row)
        )
        encoding = chart_data.get("encoding")
        encoding = dict(encoding) if isinstance(encoding, dict) else {}

        def declared_fields(*names: str) -> list[str]:
            values = []
            for name in names:
                values.extend(str(item) for item in _list_value(encoding.get(name)))
            return [item for item in dict.fromkeys(values) if item in fields]

        dimensions = declared_fields("dimension", "dimensions", "x", "x_field")
        measure_fields = declared_fields("measure", "measures", "y", "y_fields")
        measure_fields = [
            field
            for field in measure_fields
            if cls._is_analytical_chart_field(field)
        ]
        measure_specs = [
            deepcopy(item)
            for item in _list_value(chart_data.get("measures"))
            if isinstance(item, dict) and str(item.get("field") or "") in fields
            and cls._is_analytical_chart_field(item.get("field"))
        ]
        measure_fields.extend(
            str(item.get("field"))
            for item in measure_specs
            if str(item.get("field")) not in measure_fields
            and cls._is_analytical_chart_field(item.get("field"))
        )
        if not dimensions and "category" in fields:
            dimensions = ["category"]
        if not measure_fields and "value" in fields:
            measure_fields = ["value"]
        if not measure_fields:
            measure_fields = [
                field
                for field in fields
                if cls._is_analytical_chart_field(field)
                if any(
                    isinstance(row.get(field), (int, float))
                    and not isinstance(row.get(field), bool)
                    for row in normalized_rows
                )
            ]
        if not dimensions:
            dimensions = [
                field
                for field in fields
                if field not in measure_fields
                and any(row.get(field) is not None for row in normalized_rows)
            ][:1]
        valid_measures = [
            field
            for field in measure_fields
            if any(
                isinstance(row.get(field), (int, float))
                and not isinstance(row.get(field), bool)
                for row in normalized_rows
            )
        ][: policy.max_measures]
        measure_specs = [
            item
            for item in measure_specs
            if str(item.get("field") or "") in valid_measures
        ]
        dimension = dimensions[0] if dimensions else ""
        chart_rows = [
            row
            for row in normalized_rows
            if dimension
            and row.get(dimension) is not None
            and any(
                isinstance(row.get(field), (int, float))
                and not isinstance(row.get(field), bool)
                for field in valid_measures
            )
        ]
        render = requested and len(chart_rows) >= 2 and bool(valid_measures)
        reason = str(chart_data.get("reason") or "").strip()
        primary_spec = next(
            (
                item
                for item in measure_specs
                if str(item.get("field")) == (valid_measures[0] if valid_measures else "")
            ),
            {},
        )
        measure = str(
            chart_data.get("measure")
            or primary_spec.get("label")
            or (valid_measures[0] if valid_measures else "")
        ).strip()[:120]
        unit = str(chart_data.get("unit") or primary_spec.get("unit") or "").strip()[:80]
        if render and not measure:
            render = False
            reason = (
                "The chart was omitted because its numeric measure was not "
                "declared explicitly."
            )
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
            "measure": measure,
            "unit": unit,
            "title": str(chart_data.get("title") or "Evidence distribution")[:120],
            "coverage": str(chart_data.get("coverage") or "materialized_result")[:200],
            "encoding": {
                "dimension": dimension,
                "measures": valid_measures,
                **(
                    {"series": str(encoding.get("series"))}
                    if str(encoding.get("series") or "") in fields
                    else {}
                ),
            },
            "measures": measure_specs,
            "semantic_roles": {
                "comparison_dimension": dimension,
                "primary_measure": valid_measures[0] if valid_measures else "",
                **{
                    f"measure_{index + 1}": field
                    for index, field in enumerate(valid_measures)
                },
            },
            "truncated": len(chart_rows) > policy.max_dataset_rows,
            "source_row_count": len(chart_rows),
            "rows": chart_rows[: policy.max_dataset_rows],
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
            if cls._is_analytical_chart_field(field)
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
        max_nested_items: int = 50,
        string_segments: int = 6,
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
                segment_count = string_segments
                segment_size = max(1, max_string_chars // segment_count)
                starts = (
                    {0}
                    if segment_count == 1
                    else {
                        round(
                            index
                            * (len(value) - segment_size)
                            / (segment_count - 1)
                        )
                        for index in range(segment_count)
                    }
                )
                return "\n... [sample gap] ...\n".join(
                    value[start : start + segment_size] for start in sorted(starts)
                )
            if isinstance(value, dict):
                return {str(key): bounded(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                if len(value) <= max_nested_items:
                    selected_items = value
                elif max_nested_items == 1:
                    selected_items = [value[0]]
                else:
                    indices = {
                        round(
                            index * (len(value) - 1) / (max_nested_items - 1)
                        )
                        for index in range(max_nested_items)
                    }
                    selected_items = [value[index] for index in sorted(indices)]
                return [bounded(item) for item in selected_items]
            return deepcopy(value)

        return [bounded(row) for row in selected]

    @staticmethod
    def _is_analytical_chart_field(field: Any) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(field).casefold()).strip("_")
        return bool(normalized) and normalized not in _NON_ANALYTICAL_CHART_FIELDS

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
