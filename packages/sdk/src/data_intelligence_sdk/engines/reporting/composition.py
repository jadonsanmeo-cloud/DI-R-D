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
    ReportPresentationPolicy,
    legacy_content_role,
    normalize_content_role,
)
from data_intelligence_sdk.engines.reporting.prompts import (
    CHART_AGENT_PROMPT,
    REPORT_AGENT_PROMPT,
    STRUCTURED_REPORT_AGENT_PROMPT,
)
from data_intelligence_sdk.engines.reporting.utils import (
    _STEP_OUTPUT_REF,
    _dataset_summary,
    _int_value,
    _json_dumps,
    _list_value,
    _safe_id,
    _schema_summary,
    _source_summary,
)

class ChartAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("chart_agent", CHART_AGENT_PROMPT, llm)

    def run(self, chart_request: dict[str, Any]) -> dict[str, Any]:
        quality_issue = self._quality_issue(chart_request)
        if quality_issue:
            fallback = self._fallback_chart({**chart_request, "datasets": []})
            fallback["selection_reason"] = quality_issue
            fallback["warnings"] = list(
                dict.fromkeys([quality_issue, *fallback.get("warnings", [])])
            )
            return fallback
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
            for field in ("analytical_purpose", "evidence_claim", "coverage"):
                if chart_request.get(field) is not None:
                    payload.setdefault(field, deepcopy(chart_request.get(field)))
            accessibility = payload.setdefault("accessibility", {})
            if isinstance(accessibility, dict):
                accessibility.setdefault(
                    "description",
                    str(chart_request.get("evidence_claim") or "").strip(),
                )
            payload["option"] = self._polish_option(payload["option"])
            payload["option"] = self._align_option_to_dataset(
                payload["option"], chart_request
            )
            grounded_claim = self._grounded_chart_claim(
                payload["option"], chart_request
            )
            if grounded_claim:
                payload["evidence_claim"] = grounded_claim
                if isinstance(accessibility, dict):
                    accessibility["description"] = grounded_claim
            return payload
        return self._fallback_chart(chart_request)

    @staticmethod
    def _quality_issue(request: dict[str, Any]) -> str | None:
        purpose = str(request.get("analytical_purpose") or "").strip()
        claim = str(request.get("evidence_claim") or "").strip()
        if not purpose or not claim:
            return (
                "The chart was omitted because no explicit analytical purpose "
                "and evidence claim were supplied."
            )
        datasets = [
            item
            for item in _list_value(request.get("datasets"))
            if isinstance(item, dict)
        ]
        rows = [
            row
            for dataset in datasets
            for row in _list_value(dataset.get("data"))
            if isinstance(row, dict)
        ]
        if len(rows) < 2:
            return "The chart was omitted because fewer than two data points exist."
        return None

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
        allowed = [
            str(item)
            for item in _list_value(request.get("allowed_types"))
            if str(item)
        ]
        if not allowed:
            return {
                "schema_version": "1.0",
                "status": "fallback",
                "chart_id": request.get("chart_id"),
                "library": "echarts",
                "selected_type": None,
                "selection_reason": "No chart types are allowed by the template.",
                "option": {},
                "fallback": request.get("fallback", {"action": "table"}),
                "warnings": ["Chart allowed_types is empty."],
            }
        if chart_type not in allowed:
            chart_type = str(allowed[0])
        echarts_type = "bar" if chart_type == "stacked_bar" else chart_type
        encoding = request.get("encoding_requirements", {})
        x_role = str(encoding.get("x_role", ""))
        y_roles = [str(item) for item in _list_value(encoding.get("y_roles"))]
        series = []
        for index, source in enumerate(datasets):
            field_definitions = [
                field
                for field in source.get("schema", {}).get("fields", [])
                if isinstance(field, dict) and field.get("name")
            ]
            fields = [field.get("name") for field in field_definitions]
            semantic_fields = source.get("semantic_roles")
            semantic_fields = (
                semantic_fields if isinstance(semantic_fields, dict) else {}
            )
            source_encoding = source.get("encoding")
            source_encoding = source_encoding if isinstance(source_encoding, dict) else {}
            x_field = (
                semantic_fields.get(x_role)
                or source_encoding.get("dimension")
                or self._field_for_role(fields, x_role)
            ) or (
                fields[0] if fields else "category"
            )
            y_fields = [
                str(semantic_fields.get(role))
                for role in y_roles
                if semantic_fields.get(role) in fields
            ]
            y_fields.extend(
                str(field)
                for field in _list_value(source_encoding.get("measures"))
                if field in fields and str(field) not in y_fields
            )
            if not y_fields:
                y_fields = [
                    str(field.get("name"))
                    for field in field_definitions
                    if field.get("name") != x_field
                    and str(field.get("type") or "").lower()
                    in {"integer", "number"}
                ]
            if not y_fields:
                fallback_y = next(
                    (field for field in fields if field != x_field),
                    "value",
                )
                y_fields = [str(fallback_y)]
            measure_specs = {
                str(item.get("field")): item
                for item in _list_value(source.get("measures"))
                if isinstance(item, dict) and item.get("field")
            }
            colors = ["#137c8b", "#526fc7", "#c98518", "#2f855a", "#c65d4b"]
            for measure_index, y_field in enumerate(dict.fromkeys(y_fields)):
                spec = measure_specs.get(y_field, {})
                color = colors[(len(series) + measure_index) % len(colors)]
                item: dict[str, Any] = {
                    "type": echarts_type,
                    "datasetIndex": index,
                    "name": str(
                        spec.get("label")
                        or (
                            source.get("measure")
                            if len(y_fields) == 1
                            else y_field
                        )
                        or source.get("dataset_id", f"series-{index + 1}")
                    ),
                    "encode": {"x": x_field, "y": y_field},
                    "itemStyle": {"color": color},
                }
                if chart_type in {"line", "area"}:
                    item["smooth"] = True
                    item["symbolSize"] = 7
                    item["lineStyle"] = {"width": 3, "color": color}
                if chart_type == "area":
                    item["type"] = "line"
                    item["areaStyle"] = {"opacity": 0.14}
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
            "analytical_purpose": request.get("analytical_purpose"),
            "evidence_claim": request.get("evidence_claim"),
            "coverage": request.get("coverage") or dataset.get("coverage"),
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
                    "name": self._measure_label(dataset)
                    or presentation.get("y_axis_label", ""),
                    "splitLine": {"lineStyle": {"color": "#e8ecf2"}},
                    "axisLabel": {"color": "#697386"},
                },
                "series": series,
            },
            "accessibility": {
                "summary": request.get("intent", "Data chart"),
                "description": request.get("evidence_claim", ""),
            },
            "warnings": [],
        }

    @classmethod
    def _align_option_to_dataset(
        cls,
        option: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind generated series and axes to the validated chart-data contract."""
        aligned = deepcopy(option)
        datasets = [
            item
            for item in _list_value(request.get("datasets"))
            if isinstance(item, dict) and item.get("data")
        ]
        if not datasets:
            dataset = request.get("dataset")
            if isinstance(dataset, dict) and dataset.get("data"):
                datasets = [dataset]
        if not datasets:
            return aligned

        series_items = _list_value(aligned.get("series"))
        series_offsets: dict[int, int] = {}
        used_fields: set[tuple[int, str]] = set()
        bound_series: list[dict[str, Any]] = []
        series_metadata: list[dict[str, Any]] = []
        series_labels: list[str] = []
        for index, item in enumerate(series_items):
            if not isinstance(item, dict):
                continue
            dataset_index = item.get("datasetIndex", index)
            try:
                source = datasets[int(dataset_index)]
            except (IndexError, TypeError, ValueError):
                dataset_index = 0
                source = datasets[0]
            else:
                dataset_index = int(dataset_index)
            position = series_offsets.get(dataset_index, 0)
            series_offsets[dataset_index] = position + 1
            encoding = source.get("encoding")
            encoding = encoding if isinstance(encoding, dict) else {}
            declared_fields = [
                str(field)
                for field in _list_value(encoding.get("measures"))
                if str(field)
            ]
            measure_specs = [
                spec
                for spec in _list_value(source.get("measures"))
                if isinstance(spec, dict)
            ]
            if not declared_fields:
                declared_fields = [
                    str(spec.get("field"))
                    for spec in measure_specs
                    if str(spec.get("field") or "")
                ]
            rows = [
                row
                for row in _list_value(source.get("data"))
                if isinstance(row, dict)
            ]
            dimension = str(encoding.get("dimension") or "").strip()
            if not dimension and rows:
                dimension = next(
                    (
                        field
                        for field in rows[0]
                        if any(row.get(field) is not None for row in rows)
                        and not any(
                            isinstance(row.get(field), (int, float))
                            and not isinstance(row.get(field), bool)
                            for row in rows
                        )
                    ),
                    "",
                )
            if not declared_fields and rows:
                declared_fields = [
                    field
                    for field in rows[0]
                    if field != dimension
                    and any(
                        isinstance(row.get(field), (int, float))
                        and not isinstance(row.get(field), bool)
                        for row in rows
                    )
                ]
            encode = item.get("encode")
            encode = deepcopy(encode) if isinstance(encode, dict) else {}
            y_field = str(encode.get("y") or "").strip()
            if y_field not in declared_fields and position < len(declared_fields):
                y_field = declared_fields[position]
            if not y_field or y_field not in declared_fields:
                continue
            field_key = (dataset_index, y_field)
            if field_key in used_fields:
                continue
            used_fields.add(field_key)
            matching_spec = next(
                (
                    spec
                    for spec in measure_specs
                    if str(spec.get("field") or "") == y_field
                ),
                measure_specs[position] if position < len(measure_specs) else {},
            )
            label = str(
                matching_spec.get("label")
                or y_field
                or source.get("measure")
                or ""
            ).strip()
            if len(declared_fields) == 1 and source.get("measure"):
                label = str(source.get("measure")).strip()
            unit = str(
                matching_spec.get("unit")
                or (source.get("unit") if len(measure_specs) <= 1 else "")
                or ""
            ).strip()
            display_label = cls._measure_label({"measure": label, "unit": unit})
            if not display_label:
                continue
            bound = deepcopy(item)
            encode["y"] = y_field
            if dimension:
                encode["x"] = dimension
            bound["encode"] = encode
            bound["datasetIndex"] = dataset_index
            bound["name"] = label
            bound_series.append(bound)
            series_labels.append(label)
            series_metadata.append(
                {
                    "series": bound,
                    "field": y_field,
                    "label": label,
                    "axis_label": display_label,
                    "unit": unit,
                }
            )

        aligned["series"] = bound_series
        axis_groups: dict[str, list[dict[str, Any]]] = {}
        for metadata in series_metadata:
            axis_key = re.sub(
                r"\s+", " ", str(metadata.get("unit") or metadata["field"])
            ).casefold().strip()
            axis_groups.setdefault(axis_key, []).append(metadata)
        original_axes = _list_value(aligned.get("yAxis"))
        y_axes: list[dict[str, Any]] = []
        for axis_index, grouped in enumerate(axis_groups.values()):
            original = (
                original_axes[axis_index]
                if axis_index < len(original_axes)
                and isinstance(original_axes[axis_index], dict)
                else {}
            )
            axis = deepcopy(original)
            axis["type"] = "value"
            axis["name"] = " / ".join(
                dict.fromkeys(str(item["axis_label"]) for item in grouped)
            )
            axis["position"] = "left" if axis_index == 0 else "right"
            if axis_index > 1:
                axis["offset"] = 58 * (axis_index - 1)
            else:
                axis.pop("offset", None)
            split_line = axis.setdefault("splitLine", {})
            if isinstance(split_line, dict):
                split_line["show"] = axis_index == 0
            axis.pop("title", None)
            y_axes.append(axis)
            for metadata in grouped:
                metadata["series"]["yAxisIndex"] = axis_index
        if len(y_axes) > 1:
            for item in bound_series:
                item.pop("stack", None)
        if y_axes:
            aligned["yAxis"] = y_axes[0] if len(y_axes) == 1 else y_axes

        first_series_encoding = (
            bound_series[0].get("encode") if bound_series else {}
        )
        first_series_encoding = (
            first_series_encoding if isinstance(first_series_encoding, dict) else {}
        )
        dimension = str(first_series_encoding.get("x") or "").strip()
        if dimension and series_labels:
            title = aligned.get("title")
            title_items = title if isinstance(title, list) else [title]
            trusted_title = str(datasets[0].get("title") or "").strip()
            chart_title = trusted_title if len(series_labels) == 1 and trusted_title else (
                " / ".join(series_labels)
                + " by "
                + cls._humanize_chart_field(dimension)
            )
            for item in title_items:
                if isinstance(item, dict):
                    item["text"] = chart_title
        grid = aligned.get("grid")
        if isinstance(grid, dict) and len(y_axes) > 2:
            grid["right"] = max(
                _int_value(grid.get("right"), 28),
                28 + (len(y_axes) - 2) * 58,
            )
        legend = aligned.get("legend")
        if isinstance(legend, dict):
            legend["data"] = series_labels
        return aligned

    @classmethod
    def _grounded_chart_claim(
        cls,
        option: dict[str, Any],
        request: dict[str, Any],
    ) -> str:
        """Summarize only the dimensions and measures the chart actually renders."""

        datasets = [
            item
            for item in _list_value(request.get("datasets"))
            if isinstance(item, dict) and item.get("data")
        ]
        if not datasets:
            dataset = request.get("dataset")
            if isinstance(dataset, dict) and dataset.get("data"):
                datasets = [dataset]
        if not datasets:
            return ""
        series_items = [
            item
            for item in _list_value(option.get("series"))
            if isinstance(item, dict)
        ]
        statements: list[str] = []
        coverage_start = ""
        coverage_end = ""
        for item in series_items:
            try:
                dataset_index = int(item.get("datasetIndex", 0))
                source = datasets[dataset_index]
            except (IndexError, TypeError, ValueError):
                continue
            rows = [row for row in _list_value(source.get("data")) if isinstance(row, dict)]
            encoding = source.get("encoding")
            encoding = encoding if isinstance(encoding, dict) else {}
            item_encoding = item.get("encode")
            item_encoding = item_encoding if isinstance(item_encoding, dict) else {}
            dimension = str(item_encoding.get("x") or encoding.get("dimension") or "")
            measure = str(item_encoding.get("y") or "")
            valid_rows = [
                row
                for row in rows
                if row.get(dimension) not in (None, "")
                and isinstance(row.get(measure), (int, float))
                and not isinstance(row.get(measure), bool)
            ]
            if len(valid_rows) < 2:
                continue
            first, last = valid_rows[0], valid_rows[-1]
            coverage_start = coverage_start or str(first.get(dimension))
            coverage_end = str(last.get(dimension)) or coverage_end
            first_value = first.get(measure)
            last_value = last.get(measure)
            direction = (
                "increased"
                if last_value > first_value
                else "decreased" if last_value < first_value else "remained stable"
            )
            measure_spec = next(
                (
                    spec
                    for spec in _list_value(source.get("measures"))
                    if isinstance(spec, dict)
                    and str(spec.get("field") or "") == measure
                ),
                {},
            )
            label = cls._measure_label(
                {
                    "measure": str(
                        measure_spec.get("label")
                        or item.get("name")
                        or cls._humanize_chart_field(measure)
                    ),
                    "unit": str(measure_spec.get("unit") or ""),
                }
            )
            if direction == "remained stable":
                statements.append(
                    f"{label} remained stable at {cls._format_chart_value(last_value)}"
                )
            else:
                statements.append(
                    f"{label} {direction} from "
                    f"{cls._format_chart_value(first_value)} to "
                    f"{cls._format_chart_value(last_value)}"
                )
        if not statements:
            return ""
        coverage = (
            f"Across {coverage_start} to {coverage_end}, "
            if coverage_start and coverage_end
            else ""
        )
        return coverage + "; ".join(statements) + "."

    @staticmethod
    def _humanize_chart_field(value: str) -> str:
        text = re.sub(r"[_\-.]+", " ", str(value)).strip()
        return text[:1].upper() + text[1:] if text else "Measure"

    @staticmethod
    def _format_chart_value(value: Any) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{value:,.4g}"
        return str(value)

    @staticmethod
    def _measure_label(dataset: dict[str, Any]) -> str:
        measure = str(dataset.get("measure") or "").strip()
        unit = str(dataset.get("unit") or "").strip()
        if measure and unit:
            measure_tokens = re.findall(r"[^\W_]+", measure.casefold())
            unit_tokens = re.findall(r"[^\W_]+", unit.casefold())
            already_labeled = bool(unit_tokens) and any(
                measure_tokens[index : index + len(unit_tokens)] == unit_tokens
                for index in range(len(measure_tokens) - len(unit_tokens) + 1)
            )
            if already_labeled:
                return measure
            return f"{measure} ({unit})"
        return measure

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
    def __init__(
        self,
        llm: object | None,
        *,
        presentation_policy: ReportPresentationPolicy | None = None,
    ) -> None:
        super().__init__("report_agent", REPORT_AGENT_PROMPT, llm)
        self.presentation_policy = presentation_policy or ReportPresentationPolicy()

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
            source_summary={
                "sources": scoped_payload.get("sources", []),
                "source_descriptors": scoped_payload.get(
                    "source_descriptors", []
                ),
            },
        )
        if isinstance(payload, dict) and isinstance(payload.get("sections"), list):
            report = self._align_structured_payload(payload, fallback)
        else:
            report = fallback
        issues = self._structured_report_issues(report, template_instance)
        if self.llm is not None and issues:
            repair_instance = self._template_instance_for_block_repair(
                template_instance,
                {str(issue["block_id"]) for issue in issues},
            )
            repaired_payload = self._invoke_json_with_prompt(
                STRUCTURED_REPORT_AGENT_PROMPT,
                user_goal=spec.objective,
                template_instance=repair_instance,
                data_step_results=data_step_results,
                chart_results=chart_results,
                source_summary={
                    "sources": scoped_payload.get("sources", []),
                    "source_descriptors": scoped_payload.get(
                        "source_descriptors", []
                    ),
                },
                draft_report=report,
                validation_feedback=issues,
            )
            if isinstance(repaired_payload, dict) and isinstance(
                repaired_payload.get("sections"), list
            ):
                repaired_report = self._align_structured_payload(
                    repaired_payload,
                    report,
                )
                repaired_issues = self._structured_report_issues(
                    repaired_report,
                    template_instance,
                )
                if self._prefer_structured_repair(
                    report,
                    issues,
                    repaired_report,
                    repaired_issues,
                ):
                    report = repaired_report
        return self._finalize_structured_report(report)

    def _structured_report_issues(
        self,
        report: dict[str, Any],
        template_instance: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Describe missing or shallow required run-local analytical blocks."""

        rendered_blocks = {
            str(block.get("block_id")): block
            for section in _list_value(report.get("sections"))
            if isinstance(section, dict)
            for block in _list_value(section.get("blocks"))
            if isinstance(block, dict) and block.get("block_id")
        }
        issues: list[dict[str, str]] = []
        processor_owned = {"chart", "profile", "kpi_group", "table"}
        for section in _list_value(template_instance.get("sections")):
            if not isinstance(section, dict):
                continue
            for block in _list_value(section.get("blocks")):
                if (
                    not isinstance(block, dict)
                    or not block.get("required")
                    or str(block.get("type") or "") in processor_owned
                ):
                    continue
                block_id = str(block.get("block_id") or "").strip()
                rendered = rendered_blocks.get(block_id)
                content = (
                    rendered.get("content")
                    if isinstance(rendered, dict)
                    and isinstance(rendered.get("content"), dict)
                    else {}
                )
                block_type = str(block.get("type") or "")
                if block_type in {"narrative", "recommendations"}:
                    text = str(content.get("text") or "").strip()
                    if not text:
                        issues.append(
                            {
                                "block_id": block_id,
                                "reason": "required analytical text is missing",
                            }
                        )
                        continue
                    role = normalize_content_role(block.get("content_role"))
                    minimum = (
                        self.presentation_policy.min_executive_summary_characters
                        if role == "executive_summary"
                        else self.presentation_policy.min_analytical_narrative_characters
                    )
                    if len(re.sub(r"\s+", " ", text)) < minimum:
                        issues.append(
                            {
                                "block_id": block_id,
                                "reason": (
                                    "required analytical text is too shallow for "
                                    "the block purpose and instructions"
                                ),
                            }
                        )
                    elif self._looks_like_metric_transcript(text):
                        issues.append(
                            {
                                "block_id": block_id,
                                "reason": (
                                    "analytical narrative is only a label-value/KPI "
                                    "transcript and lacks interpretation, drivers, "
                                    "context, consequences, or uncertainty"
                                ),
                            }
                        )
                elif block_type in {
                    "insight_grid",
                    "evidence_list",
                    "process_flow",
                } and not _list_value(content.get("items")):
                    issues.append(
                        {
                            "block_id": block_id,
                            "reason": "required evidence items are missing",
                        }
                    )
        return issues

    @staticmethod
    def _looks_like_metric_transcript(text: str) -> bool:
        sentences = [
            item.strip()
            for item in re.split(
                r"(?:\r?\n)+|(?<=[.!?])\s+",
                str(text).strip(),
            )
            if item.strip()
        ]
        if len(sentences) < 2:
            return False
        label_value_sentences = [
            sentence
            for sentence in sentences
            if re.match(r"^[^:]{1,60}:\s*\S+", sentence)
            and re.search(r"\d", sentence)
        ]
        return len(label_value_sentences) / len(sentences) >= 0.75

    @staticmethod
    def _template_instance_for_block_repair(
        template_instance: dict[str, Any],
        block_ids: set[str],
    ) -> dict[str, Any]:
        focused = deepcopy(template_instance)
        sections = []
        for section in _list_value(focused.get("sections")):
            if not isinstance(section, dict):
                continue
            blocks = [
                deepcopy(block)
                for block in _list_value(section.get("blocks"))
                if isinstance(block, dict)
                and str(block.get("block_id") or "") in block_ids
            ]
            if not blocks:
                continue
            section_copy = deepcopy(section)
            section_copy["blocks"] = blocks
            sections.append(section_copy)
        focused["sections"] = sections
        return focused

    @staticmethod
    def _prefer_structured_repair(
        original: dict[str, Any],
        original_issues: list[dict[str, str]],
        repaired: dict[str, Any],
        repaired_issues: list[dict[str, str]],
    ) -> bool:
        if len(repaired_issues) != len(original_issues):
            return len(repaired_issues) < len(original_issues)

        def content_size(value: dict[str, Any]) -> int:
            return sum(
                len(str(content_value))
                for section in _list_value(value.get("sections"))
                if isinstance(section, dict)
                for block in _list_value(section.get("blocks"))
                if isinstance(block, dict)
                for content_value in (
                    block.get("content", {}).values()
                    if isinstance(block.get("content"), dict)
                    else []
                )
            )

        return content_size(repaired) > content_size(original)

    @staticmethod
    def _align_structured_payload(
        payload: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        aligned = deepcopy(fallback)
        if ReportAgent._meaningful_title(payload.get("title")):
            aligned["title"] = str(payload["title"]).strip()
        if payload.get("summary") is not None:
            aligned["summary"] = payload["summary"]
        generated_blocks = {
            str(block.get("block_id")): block
            for section in payload.get("sections", [])
            if isinstance(section, dict)
            for block in _list_value(section.get("blocks"))
            if isinstance(block, dict) and block.get("block_id")
        }
        used_generated_text: set[str] = set()
        for section in aligned.get("sections", []):
            for block in section.get("blocks", []):
                candidate = generated_blocks.get(str(block.get("block_id")))
                if not candidate or (
                    candidate.get("type")
                    and candidate.get("type") != block.get("type")
                ):
                    continue
                content = candidate.get("content")
                block_type = str(block.get("type") or "")
                if (
                    block_type in {"narrative", "recommendations"}
                    and isinstance(content, dict)
                ):
                    text = ReportAgent._generated_text_content(
                        block_type,
                        content,
                    )
                    normalized_text = re.sub(r"\s+", " ", text).lower()
                    if not text or normalized_text in used_generated_text:
                        continue
                    used_generated_text.add(normalized_text)
                    block["content"] = {"text": text}
                    block["status"] = str(candidate.get("status") or "completed")
                elif block_type in {
                    "insight_grid",
                    "evidence_list",
                    "process_flow",
                } and isinstance(content, dict):
                    items = ReportAgent._normalize_generated_items(content.get("items"))
                    signatures = {
                        re.sub(r"\s+", " ", str(item.get("text") or "")).casefold()
                        for item in items
                    }
                    items = [
                        item
                        for item in items
                        if re.sub(
                            r"\s+", " ", str(item.get("text") or "")
                        ).casefold()
                        not in used_generated_text
                    ]
                    if not items:
                        continue
                    used_generated_text.update(signatures)
                    block["content"] = {"items": items}
                    block["status"] = str(candidate.get("status") or "completed")
        aligned["warnings"] = ReportAgent._deduplicate_messages(
            [
                warning
                for warning in aligned.get("warnings", [])
                if ReportAgent._is_material_warning(warning)
            ]
        )
        executive_summary = ReportAgent._executive_summary_from_sections(
            aligned.get("sections", [])
        )
        if executive_summary:
            aligned["summary"] = executive_summary
        return aligned

    @classmethod
    def _finalize_structured_report(cls, report: dict[str, Any]) -> dict[str, Any]:
        """Prune unsupported blocks and reflow the surviving run-local layout."""

        finalized = deepcopy(report)
        seen_content: set[str] = set()
        seen_claims: set[str] = set()
        sections = []
        for section in _list_value(finalized.get("sections")):
            if not isinstance(section, dict):
                continue
            blocks = []
            for block in _list_value(section.get("blocks")):
                if not isinstance(block, dict) or not cls._block_has_content(block):
                    continue
                candidate = cls._without_seen_claims(block, seen_claims)
                if not cls._block_has_content(candidate):
                    continue
                signature = cls._block_content_signature(candidate)
                if signature and signature in seen_content:
                    continue
                claims = cls._block_claim_signatures(candidate)
                if claims and claims.issubset(seen_claims):
                    continue
                if signature:
                    seen_content.add(signature)
                seen_claims.update(claims)
                blocks.append(candidate)
            if not blocks:
                continue
            section_copy = deepcopy(section)
            section_layout = section_copy.get("layout")
            section_layout = section_layout if isinstance(section_layout, dict) else {}
            section_copy["blocks"] = cls._reflow_blocks(
                blocks,
                _int_value(section_layout.get("columns"), 12),
            )
            section_copy["status"] = "completed"
            sections.append(section_copy)
        finalized["sections"] = sections
        finalized["warnings"] = [
            warning
            for warning in _list_value(finalized.get("warnings"))
            if cls._normalize_claim_signature(str(warning)) not in seen_claims
        ]
        return finalized

    @classmethod
    def _without_seen_claims(
        cls,
        block: dict[str, Any],
        seen_claims: set[str],
    ) -> dict[str, Any]:
        """Remove repeated atomic items while preserving novel block content."""

        candidate = deepcopy(block)
        block_type = str(candidate.get("type") or "")
        if block_type not in {"insight_grid", "evidence_list", "process_flow"}:
            return candidate
        content = candidate.get("content")
        content = content if isinstance(content, dict) else {}
        items = []
        for item in _list_value(content.get("items")):
            if not isinstance(item, dict):
                continue
            signature = cls._item_claim_signature(item)
            if signature and signature in seen_claims:
                continue
            items.append(deepcopy(item))
        candidate["content"] = {**content, "items": items}
        return candidate

    @staticmethod
    def _block_has_content(block: dict[str, Any]) -> bool:
        if str(block.get("status") or "") == "no_data":
            return False
        content = block.get("content")
        if not isinstance(content, dict):
            return False
        block_type = str(block.get("type") or "")
        if block_type in {"narrative", "recommendations"}:
            return bool(str(content.get("text") or "").strip())
        if block_type in {"profile", "insight_grid", "evidence_list", "process_flow"}:
            return bool(_list_value(content.get("items")))
        if block_type == "kpi_group":
            return bool(_list_value(content.get("metrics")))
        if block_type == "table":
            return bool(_list_value(content.get("rows")))
        if block_type == "chart":
            chart = content.get("chart")
            return isinstance(chart, dict) and bool(
                chart.get("option") or chart.get("fallback")
            )
        return bool(content)

    @staticmethod
    def _block_content_signature(block: dict[str, Any]) -> str:
        content = block.get("content")
        content = content if isinstance(content, dict) else {}
        block_type = str(block.get("type") or "")
        if block_type in {"narrative", "recommendations"}:
            value = str(content.get("text") or "")
        elif block_type in {"insight_grid", "evidence_list", "process_flow"}:
            value = " ".join(
                str(item.get("text") or item.get("statement") or "")
                for item in _list_value(content.get("items"))
                if isinstance(item, dict)
            )
        else:
            return ""
        return re.sub(r"\s+", " ", value).casefold().strip()

    @staticmethod
    def _block_claim_signatures(block: dict[str, Any]) -> set[str]:
        content = block.get("content")
        content = content if isinstance(content, dict) else {}
        block_type = str(block.get("type") or "")
        values: list[str] = []
        if block_type in {"narrative", "recommendations"}:
            text = str(content.get("text") or "")
            values = [
                value
                for value in (
                    text.splitlines()
                    if len([line for line in text.splitlines() if line.strip()]) > 1
                    else re.split(r"(?<=[.!?])\s+", text)
                )
                if value.strip()
            ]
        elif block_type in {"insight_grid", "evidence_list", "process_flow"}:
            values = [
                " ".join(
                    part
                    for part in (
                        str(item.get("title") or "").strip(),
                        str(item.get("text") or item.get("statement") or "").strip(),
                    )
                    if part
                )
                for item in _list_value(content.get("items"))
                if isinstance(item, dict)
            ]
        return {
            signature
            for value in values
            if (signature := ReportAgent._normalize_claim_signature(value))
        }

    @staticmethod
    def _item_claim_signature(item: dict[str, Any]) -> str:
        return ReportAgent._normalize_claim_signature(
            " ".join(
                part
                for part in (
                    str(item.get("title") or "").strip(),
                    str(item.get("text") or item.get("statement") or "").strip(),
                )
                if part
            )
        )

    @staticmethod
    def _normalize_claim_signature(value: str) -> str:
        return (
            re.sub(r"[^\w%+.-]+", " ", str(value), flags=re.UNICODE)
            .casefold()
            .strip()
            .rstrip(".")
        )

    @staticmethod
    def _reflow_blocks(
        blocks: list[dict[str, Any]],
        columns: int,
    ) -> list[dict[str, Any]]:
        columns = max(1, columns)
        rows: list[list[tuple[dict[str, Any], int]]] = []
        row: list[tuple[dict[str, Any], int]] = []
        used = 0
        for block in blocks:
            layout = block.get("layout")
            layout = layout if isinstance(layout, dict) else {}
            span = min(columns, max(1, _int_value(layout.get("span"), columns)))
            if row and used + span > columns:
                rows.append(row)
                row = []
                used = 0
            row.append((block, span))
            used += span
            if used == columns:
                rows.append(row)
                row = []
                used = 0
        if row:
            rows.append(row)

        reflowed: list[dict[str, Any]] = []
        for current in rows:
            weights = [span for _, span in current]
            total = sum(weights)
            exact = [columns * weight / total for weight in weights]
            spans = [max(1, int(value)) for value in exact]
            while sum(spans) < columns:
                index = max(
                    range(len(spans)),
                    key=lambda item: exact[item] - spans[item],
                )
                spans[index] += 1
            while sum(spans) > columns:
                index = max(
                    (item for item in range(len(spans)) if spans[item] > 1),
                    key=lambda item: spans[item] - exact[item],
                )
                spans[index] -= 1
            for (block, _), span in zip(current, spans):
                block_copy = deepcopy(block)
                layout = block_copy.get("layout")
                layout = deepcopy(layout) if isinstance(layout, dict) else {}
                layout["span"] = span
                block_copy["layout"] = layout
                reflowed.append(block_copy)
        return reflowed

    @staticmethod
    def reconcile_template_instance(
        template_instance: dict[str, Any],
        structured_report: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose the evidence-adapted instance that was actually rendered."""

        reconciled = deepcopy(template_instance)
        report_sections = {
            str(section.get("section_id")): section
            for section in _list_value(structured_report.get("sections"))
            if isinstance(section, dict) and section.get("section_id")
        }
        sections = []
        for section in _list_value(reconciled.get("sections")):
            if not isinstance(section, dict):
                continue
            rendered = report_sections.get(str(section.get("section_id")))
            if not rendered:
                continue
            rendered_blocks = {
                str(block.get("block_id")): block
                for block in _list_value(rendered.get("blocks"))
                if isinstance(block, dict) and block.get("block_id")
            }
            blocks = []
            for block in _list_value(section.get("blocks")):
                if not isinstance(block, dict):
                    continue
                rendered_block = rendered_blocks.get(str(block.get("block_id")))
                if not rendered_block:
                    continue
                block_copy = deepcopy(block)
                block_copy["layout"] = deepcopy(rendered_block.get("layout", {}))
                blocks.append(block_copy)
            if blocks:
                section_copy = deepcopy(section)
                section_copy["blocks"] = blocks
                sections.append(section_copy)
        reconciled["sections"] = sections
        reconciled["status"] = "accepted"
        return reconciled

    @staticmethod
    def _normalize_generated_items(values: Any) -> list[dict[str, str]]:
        items = []
        seen: set[str] = set()
        for value in _list_value(values):
            if isinstance(value, dict):
                title = str(value.get("title") or "").strip()
                text = str(
                    value.get("text")
                    or value.get("statement")
                    or value.get("content")
                    or ""
                ).strip()
                meta = str(
                    value.get("meta")
                    or value.get("source_location")
                    or ""
                ).strip()
            else:
                title = ""
                text = str(value).strip()
                meta = ""
            signature = re.sub(r"\s+", " ", f"{title} {text}").casefold()
            if not text or signature in seen:
                continue
            seen.add(signature)
            item = {"title": title, "text": text}
            if meta and not meta.startswith(
                ("artifact://", "memory://", "step-output://")
            ):
                item["meta"] = meta
            items.append(item)
        return items

    @staticmethod
    def _executive_summary_from_sections(sections: Any) -> str:
        for section in _list_value(sections):
            if not isinstance(section, dict):
                continue
            for block in _list_value(section.get("blocks")):
                if (
                    not isinstance(block, dict)
                    or str(block.get("content_role") or "")
                    != "executive_summary"
                    or str(block.get("status") or "completed") == "no_data"
                ):
                    continue
                content = block.get("content")
                content = content if isinstance(content, dict) else {}
                text = str(content.get("text") or "").strip()
                if text:
                    return text
                statements = [
                    str(item.get("statement") or item.get("text") or "").strip()
                    for item in _list_value(content.get("items"))
                    if isinstance(item, dict)
                ]
                rendered = " ".join(item for item in statements if item)
                if rendered:
                    return rendered
        return ""

    @staticmethod
    def _meaningful_title(value: Any) -> bool:
        title = re.sub(r"\s+", " ", str(value or "")).strip()
        normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        generic = {
            "analysis",
            "analysis report",
            "data analysis",
            "data report",
            "report",
            "report analysis",
        }
        opaque_prefix = re.match(
            r"^(?:[0-9a-f]{12,64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
            r"\s*[:\-]\s+",
            title,
            flags=re.IGNORECASE,
        )
        return (
            len(title) >= 8
            and normalized not in generic
            and opaque_prefix is None
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
                    content = {
                        "chart_id": chart_id,
                        "chart": chart,
                        "insight": self._chart_insight(chart),
                    }
                    if chart is None or chart.get("status") not in {
                        "ready",
                        "completed",
                    }:
                        status = "fallback"
                        content["fallback"] = block.get("chart_slot", {}).get(
                            "fallback", {"action": "table"}
                        )
                elif block_type == "kpi_group":
                    block_payload = self._block_specific_payload(block, block_results)
                    metrics = self._normalize_block_metrics(
                        block_payload.get("metrics")
                    )
                    content = {
                        "metrics": (
                            metrics
                            if metrics
                            else self._collect_metrics(block_results)
                        )
                    }
                    if not content["metrics"]:
                        status = "no_data"
                elif block_type == "profile":
                    block_payload = self._block_specific_payload(block, block_results)
                    supplied_items = block_payload.get("items")
                    content = {
                        "items": (
                            deepcopy(supplied_items)
                            if isinstance(supplied_items, list)
                            else self._profile_items(
                                block_results,
                                scoped_payload.get("sources", []),
                                scoped_payload.get("source_descriptors", []),
                            )
                        )
                    }
                    if not content["items"]:
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
                elif block_type in {
                    "insight_grid",
                    "evidence_list",
                    "process_flow",
                }:
                    content = {
                        "items": self._visual_items_for_block(
                            block,
                            block_results,
                            max_items=self._visual_item_limit(block),
                        )
                    }
                    if not content["items"]:
                        status = "no_data"
                elif block_type == "table":
                    block_payload = self._block_specific_payload(block, block_results)
                    supplied_rows = block_payload.get("rows")
                    content = {
                        "rows": (
                            deepcopy(supplied_rows)
                            if isinstance(supplied_rows, list)
                            else [
                                item.get("aggregated_data", {})
                                for item in block_results
                                if item.get("aggregated_data")
                            ]
                        )
                    }
                    if not content["rows"]:
                        status = "no_data"
                else:
                    content = {}
                    status = "no_data"
                blocks.append(
                    {
                        "block_id": block.get("block_id"),
                        "type": block_type,
                        "content_role": block.get("content_role"),
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
        fallback_summary = " ".join(
            dict.fromkeys(
                str(item.get("analysis_summary"))
                for item in data_step_results
                if item.get("analysis_summary")
            )
        )
        return {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": report_status,
            "title": self._fallback_title(
                spec,
                template_instance,
                scoped_payload,
            ),
            "summary": self._executive_summary_from_sections(sections)
            or fallback_summary
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

    @staticmethod
    def _chart_insight(chart: Any) -> dict[str, str]:
        if not isinstance(chart, dict):
            return {}
        accessibility = chart.get("accessibility")
        accessibility = accessibility if isinstance(accessibility, dict) else {}
        values = {
            "claim": chart.get("evidence_claim"),
            "purpose": chart.get("analytical_purpose"),
            "coverage": chart.get("coverage"),
            "description": accessibility.get("description")
            or accessibility.get("summary"),
        }
        return {
            key: str(value).strip()
            for key, value in values.items()
            if value is not None and str(value).strip()
        }

    @staticmethod
    def _fallback_title(
        spec: ExecutionSpec,
        template_instance: dict[str, Any],
        scoped_payload: dict[str, Any],
    ) -> str:
        strategy = str(template_instance.get("title_strategy") or "").strip()
        sources = _list_value(scoped_payload.get("sources"))
        descriptors = [
            item
            for item in _list_value(scoped_payload.get("source_descriptors"))
            if isinstance(item, dict)
        ]
        friendly_source = next(
            (
                str(
                    item.get("file_name")
                    or item.get("object_key")
                    or item.get("source_uri")
                    or ""
                ).strip()
                for item in descriptors
                if (
                    item.get("file_name")
                    or item.get("object_key")
                    or item.get("source_uri")
                )
            ),
            "",
        )
        source_subject = (
            re.sub(
                r"[_\-]+",
                " ",
                Path(friendly_source or str(sources[0])).stem,
            ).strip()
            if (friendly_source or sources)
            else ""
        )
        objective = str(spec.objective or "").strip()
        if source_subject and objective:
            return f"{source_subject}: {objective}"
        if source_subject:
            return source_subject
        if objective:
            return objective
        return strategy or "Evidence-led intelligence brief"

    @staticmethod
    def _profile_items(
        results: list[dict[str, Any]],
        sources: list[Any],
        source_descriptors: list[Any] | None = None,
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        descriptors = [
            item
            for item in _list_value(source_descriptors)
            if isinstance(item, dict)
        ]
        friendly_source = next(
            (
                str(
                    item.get("file_name")
                    or item.get("object_key")
                    or item.get("source_uri")
                    or ""
                ).strip()
                for item in descriptors
                if (
                    item.get("file_name")
                    or item.get("object_key")
                    or item.get("source_uri")
                )
            ),
            "",
        )
        if friendly_source or sources:
            source = Path(friendly_source or str(sources[0]))
            items.append({"label": "Source", "value": source.name or str(source)})
            if source.suffix:
                items.append(
                    {
                        "label": "File type",
                        "value": source.suffix.lstrip(".").upper(),
                    }
                )
            try:
                if source.is_file():
                    items.append(
                        {
                            "label": "File size",
                            "value": f"{source.stat().st_size:,} bytes",
                        }
                    )
            except OSError:
                pass
        profiles = [
            item.get("step_result_artifact", {}).get("profile", {})
            for item in results
        ]
        row_count = sum(
            int(profile.get("row_count", 0))
            for profile in profiles
            if isinstance(profile, dict)
        )
        if profiles:
            items.append({"label": "Materialized records", "value": f"{row_count:,}"})
        field_names = {
            str(field.get("name"))
            for item in results
            for field in item.get("step_result_artifact", {})
            .get("schema", {})
            .get("fields", [])
            if isinstance(field, dict) and field.get("name")
        }
        if field_names:
            items.append({"label": "Detected fields", "value": f"{len(field_names):,}"})
        missing = sum(
            int(count)
            for profile in profiles
            if isinstance(profile, dict)
            for count in (
                profile.get("null_counts", {}).values()
                if isinstance(profile.get("null_counts"), dict)
                else []
            )
            if isinstance(count, (int, float)) and not isinstance(count, bool)
        )
        if profiles:
            items.append({"label": "Missing values in profile", "value": f"{missing:,}"})
        return items

    @staticmethod
    def _block_specific_payload(
        block: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge content explicitly assigned to this run-local block.

        Block IDs are generated by TemplateAgent for the current report instance.
        Looking content up by that contract keeps composition independent of
        domains, titles, filenames, and a global fixed report skeleton.
        """

        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            return {}
        merged: dict[str, Any] = {}
        for result in results:
            content = result.get("report_content") or result.get("analysis", {}).get(
                "report_content", {}
            )
            if not isinstance(content, dict):
                continue
            assignments = content.get("block_content")
            if not isinstance(assignments, dict):
                continue
            payload = assignments.get(block_id)
            if not isinstance(payload, dict):
                continue
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                existing = str(merged.get("text") or "").strip()
                merged["text"] = "\n\n".join(
                    item for item in (existing, text.strip()) if item
                )
            for field in ("items", "metrics", "rows"):
                values = payload.get(field)
                if isinstance(values, list):
                    merged.setdefault(field, []).extend(deepcopy(values))
        return merged

    @staticmethod
    def _evidence_items_for_role(
        content: dict[str, Any],
        role: str,
    ) -> list[Any]:
        normalized_role = normalize_content_role(role)
        selected = []
        for item in _list_value(content.get("evidence_items")):
            if not isinstance(item, dict):
                continue
            roles = {
                normalize_content_role(value)
                for value in _list_value(item.get("content_roles"))
                if str(value)
            }
            kind = normalize_content_role(
                item.get("kind") or item.get("category") or ""
            )
            if normalized_role in roles or normalized_role == kind:
                selected.append(item)
        return selected

    @staticmethod
    def _visual_items_for_block(
        block: dict[str, Any],
        results: list[dict[str, Any]],
        *,
        max_items: int | None = None,
    ) -> list[dict[str, str]]:
        assigned = ReportAgent._block_specific_payload(block, results).get("items")
        if isinstance(assigned, list):
            items = []
            seen: set[str] = set()
            for value in assigned:
                if isinstance(value, dict):
                    title = str(value.get("title") or "").strip()
                    text = str(
                        value.get("statement")
                        or value.get("text")
                        or value.get("content")
                        or ""
                    ).strip()
                    location = str(
                        value.get("source_location")
                        or value.get("location")
                        or ""
                    ).strip()
                else:
                    title = ""
                    text = str(value).strip()
                    location = ""
                key = re.sub(r"\s+", " ", f"{title} {text}").casefold()
                if not text or key in seen:
                    continue
                seen.add(key)
                item = {"title": title, "text": text}
                if location and not location.startswith(
                    ("artifact://", "memory://", "step-output://")
                ):
                    item["meta"] = location
                items.append(item)
            if items:
                return items
        role = normalize_content_role(
            block.get("content_role") or legacy_content_role(block)
        )
        role_keys = {
            "key_findings": "key_findings",
            "supporting_evidence": "supporting_evidence",
            "implication": "implications",
            "limitation": "limitations",
            "recommendation": "recommendations",
        }
        key = role_keys.get(role, "key_findings")
        items = []
        seen: set[str] = set()
        for result in results:
            content = result.get("report_content") or result.get("analysis", {}).get(
                "report_content", {}
            )
            if not isinstance(content, dict):
                continue
            role_items = ReportAgent._evidence_items_for_role(content, role)
            for value in role_items or _list_value(content.get(key)):
                if isinstance(value, dict):
                    title = str(value.get("title") or "").strip()
                    text = str(
                        value.get("statement")
                        or value.get("text")
                        or value.get("content")
                        or ""
                    ).strip()
                    location = str(
                        value.get("source_location")
                        or value.get("location")
                        or ""
                    ).strip()
                else:
                    title = ""
                    text = str(value).strip()
                    location = ""
                normalized = re.sub(r"\s+", " ", f"{title} {text}").lower()
                if not text or normalized in seen:
                    continue
                seen.add(normalized)
                item = {"title": title, "text": text}
                if location and not location.startswith(
                    ("artifact://", "memory://", "step-output://")
                ):
                    item["meta"] = location
                items.append(item)
        limit = max_items or ReportPresentationPolicy().max_insight_items
        return items[:limit]

    def _visual_item_limit(self, block: dict[str, Any]) -> int:
        return {
            "evidence_list": self.presentation_policy.max_evidence_items,
            "process_flow": self.presentation_policy.max_process_items,
        }.get(
            str(block.get("type") or ""),
            self.presentation_policy.max_insight_items,
        )

    @classmethod
    def _report_text_for_block(
        cls,
        block: dict[str, Any],
        results: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        assigned = cls._block_specific_payload(block, results)
        assigned_text = assigned.get("text")
        if isinstance(assigned_text, str) and assigned_text.strip():
            return assigned_text.strip()
        assigned_items = assigned.get("items")
        if isinstance(assigned_items, list) and assigned_items:
            return cls._format_report_items(
                assigned_items,
                include_location=str(block.get("content_role") or "")
                in {"supporting_evidence", "recommendation"},
            )
        content_role = normalize_content_role(
            block.get("content_role") or legacy_content_role(block)
        )
        contents = []
        for item in results:
            content = item.get("report_content") or item.get("analysis", {}).get(
                "report_content"
            )
            contents.append(content if isinstance(content, dict) else {})

        def role_values(role: str, legacy_field: str) -> list[Any]:
            values = [
                entry
                for content in contents
                for entry in cls._evidence_items_for_role(content, role)
            ]
            if values:
                return values
            return [
                entry
                for content in contents
                for entry in _list_value(content.get(legacy_field))
            ]

        if content_role == "limitation":
            values = role_values("limitation", "limitations")
            if not values:
                values.extend(warnings)
            return cls._format_report_items(values)
        if content_role == "supporting_evidence":
            values = role_values("supporting_evidence", "supporting_evidence")
            return cls._format_report_items(values, include_location=True)
        if content_role == "implication":
            values = role_values("implication", "implications")
            return cls._format_report_items(values)
        if content_role == "recommendation":
            values = role_values("recommendation", "recommendations")
            return cls._format_report_items(values, include_location=True)
        if content_role == "key_findings":
            values = role_values("key_findings", "key_findings")
            if not values:
                values = [
                    observation
                    for item in results
                    for observation in item.get("analysis", {}).get("observations", [])
                ]
            return cls._format_report_items(values)
        if content_role == "narrative":
            summaries = [
                str(result.get("analysis_summary") or "").strip()
                for result in results
            ]
            return "\n\n".join(text for text in dict.fromkeys(summaries) if text)

        summaries = [
            str(
                content.get("executive_summary") or result.get("analysis_summary") or ""
            ).strip()
            for result, content in zip(results, contents)
        ]
        summaries = [text for text in dict.fromkeys(summaries) if text]
        return "\n\n".join(summaries)

    @classmethod
    def _normalize_generated_text(cls, value: Any) -> str:
        """Turn accidentally serialized structured content into readable prose."""

        text = str(value or "").strip()
        if not text or text[:1] not in {"{", "["}:
            return text
        parsed: Any = None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (TypeError, ValueError, SyntaxError, json.JSONDecodeError):
                continue
            break
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            formatted = cls._format_report_items(parsed, include_location=True)
            if formatted:
                return formatted
        return text

    @classmethod
    def _generated_text_content(
        cls,
        block_type: str,
        content: dict[str, Any],
    ) -> str:
        """Accept equivalent structured LLM shapes for prose-owned blocks."""

        if isinstance(content.get("text"), str):
            return cls._normalize_generated_text(content["text"])
        aliases = (
            ("items", "recommendations", "actions", "options")
            if block_type == "recommendations"
            else ("paragraphs", "items")
        )
        for alias in aliases:
            values = content.get(alias)
            if not isinstance(values, list):
                continue
            formatted = cls._format_report_items(
                values,
                include_location=block_type == "recommendations",
            )
            if formatted:
                return formatted
        return ""

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
                    or value.get("action")
                    or value.get("recommendation")
                    or value.get("description")
                    or ""
                ).strip()
                text = (
                    f"{title}: {statement}"
                    if title and statement
                    else (statement or title)
                )
                details = []
                for field, label in (
                    ("rationale", "Rationale"),
                    ("prerequisite", "Prerequisite"),
                    ("prerequisites", "Prerequisites"),
                    ("risk", "Risk"),
                    ("tradeoff", "Trade-off"),
                    ("validation_signal", "Validation signal"),
                    ("success_metric", "Success metric"),
                    ("expected_outcome", "Expected outcome"),
                ):
                    detail = value.get(field)
                    if isinstance(detail, list):
                        detail = "; ".join(
                            str(item).strip()
                            for item in detail
                            if str(item).strip()
                        )
                    detail_text = str(detail or "").strip()
                    if detail_text:
                        details.append(f"{label}: {detail_text}")
                if text and details:
                    text = f"{text} {' '.join(details)}"
                if include_location and text:
                    location = value.get("source_location")
                    if str(location or "").startswith(
                        ("artifact://", "memory://", "step-output://")
                    ):
                        location = None
                    if not location:
                        refs = [
                            str(ref)
                            for ref in _list_value(value.get("evidence_refs"))
                            if ref
                            and not str(ref).startswith(
                                ("artifact://", "memory://", "step-output://")
                            )
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

    @classmethod
    def _normalize_block_metrics(cls, values: Any) -> list[dict[str, Any]]:
        """Normalize processor metrics without assuming a domain-specific schema."""

        if not isinstance(values, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            name = next(
                (
                    str(value.get(alias) or "").strip()
                    for alias in ("name", "label", "metric", "metric_name", "title")
                    if str(value.get(alias) or "").strip()
                ),
                "",
            )
            metric_value = value.get("value", value.get("metric_value"))
            metric = {**deepcopy(value), "name": name, "value": metric_value}
            if not cls._is_display_metric(metric):
                continue
            signature = re.sub(r"[^a-z0-9]+", "", name.casefold())
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(metric)
        return normalized

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
        return list(deduplicated.values())[
            : self.presentation_policy.max_kpi_items
        ]

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
