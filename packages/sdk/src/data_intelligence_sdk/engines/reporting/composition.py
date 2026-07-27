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
from data_intelligence_sdk.engines.reporting.policies import legacy_content_role
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
            payload["option"] = self._polish_option(payload["option"])
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
        if ReportAgent._meaningful_title(payload.get("title")):
            aligned["title"] = str(payload["title"]).strip()
        if payload.get("summary") is not None:
            aligned["summary"] = payload["summary"]
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
                content_role = str(block.get("content_role", ""))
                if block.get("type") == "recommendations" or content_role in {
                    "supporting_evidence",
                    "limitation",
                }:
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
        return len(title) >= 8 and normalized not in generic

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
                elif block_type == "profile":
                    content = {
                        "items": self._profile_items(
                            block_results,
                            scoped_payload.get("sources", []),
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
                        )
                    }
                    if not content["items"]:
                        status = "no_data"
                elif block_type == "table":
                    content = {
                        "rows": [
                            item.get("aggregated_data", {}) for item in block_results
                            if item.get("aggregated_data")
                        ]
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
        return {
            "schema_version": "1.0",
            "report_id": "structured-report",
            "status": report_status,
            "title": self._fallback_title(
                spec,
                template_instance,
                scoped_payload,
            ),
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

    @staticmethod
    def _fallback_title(
        spec: ExecutionSpec,
        template_instance: dict[str, Any],
        scoped_payload: dict[str, Any],
    ) -> str:
        strategy = str(template_instance.get("title_strategy") or "").strip()
        sources = _list_value(scoped_payload.get("sources"))
        source_subject = (
            re.sub(r"[_\-]+", " ", Path(str(sources[0])).stem).strip()
            if sources
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
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if sources:
            source = Path(str(sources[0]))
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
    def _visual_items_for_block(
        block: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        role = str(block.get("content_role") or legacy_content_role(block))
        role_keys = {
            "key_findings": "key_findings",
            "supporting_evidence": "supporting_evidence",
            "implication": "implications",
            "limitation": "limitations",
            "recommendation": "implications",
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
            for value in _list_value(content.get(key)):
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
                if location:
                    item["meta"] = location
                items.append(item)
        return items[:8]

    @classmethod
    def _report_text_for_block(
        cls,
        block: dict[str, Any],
        results: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        content_role = str(block.get("content_role") or legacy_content_role(block))
        contents = []
        for item in results:
            content = item.get("report_content") or item.get("analysis", {}).get(
                "report_content"
            )
            contents.append(content if isinstance(content, dict) else {})

        if content_role == "limitation":
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("limitations"))
            ]
            if not values:
                values.extend(warnings)
            return cls._format_report_items(values)
        if content_role == "supporting_evidence":
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("supporting_evidence"))
            ]
            return cls._format_report_items(values, include_location=True)
        if content_role == "implication":
            values = [
                entry
                for content in contents
                for entry in _list_value(content.get("implications"))
            ]
            return cls._format_report_items(values)
        if content_role == "key_findings":
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
