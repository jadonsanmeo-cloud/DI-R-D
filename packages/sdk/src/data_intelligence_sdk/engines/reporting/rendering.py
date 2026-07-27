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

from data_intelligence_sdk.engines.reporting.policies import (
    LocalePolicy,
    ReportAssetPolicy,
)
from data_intelligence_sdk.engines.reporting.utils import (
    _int_value,
    _list_value,
    _safe_id,
)

class ReportRenderer:
    def __init__(
        self,
        *,
        asset_policy: ReportAssetPolicy | None = None,
    ) -> None:
        self.asset_policy = asset_policy or ReportAssetPolicy()

    def render(
        self,
        structured_report: dict[str, Any],
        legacy_markdown: str | None = None,
        *,
        locale_policy: LocalePolicy | None = None,
    ) -> list[dict[str, Any]]:
        locale = locale_policy or LocalePolicy.for_locale("en")
        markdown = legacy_markdown or self._markdown(structured_report, locale)
        css = self._css()
        javascript = self._javascript()
        html = self._html(structured_report, css, javascript, locale)
        asset_warnings = (
            []
            if self.asset_policy.echarts_script_url
            else ["ECharts asset is disabled; chart containers use table fallback."]
        )
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
                "warnings": asset_warnings,
            },
        ]

    def _markdown(
        self,
        report: dict[str, Any],
        locale_policy: LocalePolicy | None = None,
    ) -> str:
        locale = locale_policy or LocalePolicy.for_locale("en")
        lines = [
            f"# {report.get('title', locale.report_label)}",
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
                elif block.get("type") == "profile":
                    for item in content.get("items", []):
                        lines.append(
                            f"- **{item.get('label', 'Item')}:** {item.get('value', '')}"
                        )
                    lines.append("")
                elif block.get("type") in {
                    "insight_grid",
                    "evidence_list",
                    "process_flow",
                }:
                    for item in content.get("items", []):
                        title_text = str(item.get("title") or "").strip()
                        prefix = f"**{title_text}:** " if title_text else ""
                        lines.append(f"- {prefix}{item.get('text', '')}")
                    lines.append("")
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
        locale_policy: LocalePolicy | None = None,
    ) -> str:
        locale = locale_policy or LocalePolicy.for_locale("en")
        title = self._escape(str(report.get("title", locale.report_label)))
        template = report.get("template", {})
        template_name = self._display_name(
            str(template.get("template_id") or "data intelligence")
        )
        status = self._display_name(str(report.get("status", "completed")))
        summary = self._compact_text(
            str(report.get("summary", "")),
            max_sentences=6,
            max_chars=1200,
        )
        sections = [
            item
            for item in report.get("sections", [])
            if isinstance(item, dict)
        ]
        source_count = len(_list_value(report.get("sources")))
        nav_links = "".join(
            '<a href="#'
            + _safe_id(section.get("section_id") or f"section-{index}")
            + '">'
            + f"{index:02d}"
            + "</a>"
            for index, section in enumerate(sections[:8], start=1)
        )
        body = [
            '<nav class="report-nav">',
            '<a class="nav-brand" href="#report-top"><span class="brand-mark"></span>',
            f"<span>{self._escape(template_name)}</span></a>",
            f'<div class="nav-links">{nav_links}</div>',
            '<button class="theme-toggle" type="button" aria-label="Toggle color theme">◐</button>',
            "</nav>",
            '<main class="report-shell">',
            '<header class="report-header" id="report-top">',
            '<div class="hero-copy">',
            '<div class="report-meta">',
            f'<span class="report-type">Intelligence brief / {self._escape(template_name)}</span>',
            f'<span class="status-pill">{self._escape(status)}</span>',
            "</div>",
            f"<h1>{title}</h1>",
            f'<p class="report-summary">{self._escape(summary)}</p>',
            '<div class="hero-rule"></div>',
            "</div>",
            '<aside class="document-profile">',
            '<span class="profile-eyebrow">Document profile</span>',
            '<dl class="hero-profile-grid">',
            f"<div><dt>Sources</dt><dd>{source_count}</dd></div>",
            f"<div><dt>Sections</dt><dd>{len(sections)}</dd></div>",
            f"<div><dt>Template</dt><dd>{self._escape(template_name)}</dd></div>",
            f"<div><dt>Status</dt><dd>{self._escape(status)}</dd></div>",
            "</dl>",
            "</aside>",
            "</header>",
        ]
        for section_index, section in enumerate(sections, start=1):
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
                if not isinstance(layout, dict):
                    layout = {}
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
                        max_sentences=18,
                        max_chars=4800,
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
                elif block_type == "profile":
                    items = [
                        item
                        for item in content.get("items", [])
                        if isinstance(item, dict) and item.get("value") is not None
                    ]
                    if not items:
                        continue
                    block_body.append('<dl class="profile-list">')
                    for item in items:
                        block_body.append(
                            "<div><dt>"
                            + self._escape(str(item.get("label", "Item")))
                            + "</dt><dd>"
                            + self._escape(str(item.get("value", "")))
                            + "</dd></div>"
                        )
                    block_body.append("</dl>")
                elif block_type == "insight_grid":
                    items = [
                        item
                        for item in content.get("items", [])
                        if isinstance(item, dict) and item.get("text")
                    ][:8]
                    if not items:
                        continue
                    block_body.append('<div class="insight-grid">')
                    for index, item in enumerate(items, start=1):
                        block_body.append(
                            '<article class="insight-card">'
                            f'<span class="insight-index">{index:02d}</span>'
                            + (
                                f"<h4>{self._escape(str(item.get('title')))}</h4>"
                                if item.get("title")
                                else ""
                            )
                            + f"<p>{self._escape(str(item.get('text')))}</p>"
                            + "</article>"
                        )
                    block_body.append("</div>")
                elif block_type == "evidence_list":
                    items = [
                        item
                        for item in content.get("items", [])
                        if isinstance(item, dict) and item.get("text")
                    ][:8]
                    if not items:
                        continue
                    block_body.append('<ol class="evidence-list">')
                    for item in items:
                        block_body.append(
                            "<li><div>"
                            + (
                                f"<strong>{self._escape(str(item.get('title')))}</strong>"
                                if item.get("title")
                                else ""
                            )
                            + f"<p>{self._escape(str(item.get('text')))}</p>"
                            + (
                                f"<small>{self._escape(str(item.get('meta')))}</small>"
                                if item.get("meta")
                                else ""
                            )
                            + "</div></li>"
                        )
                    block_body.append("</ol>")
                elif block_type == "process_flow":
                    items = [
                        item
                        for item in content.get("items", [])
                        if isinstance(item, dict) and item.get("text")
                    ][:6]
                    if not items:
                        continue
                    block_body.append('<ol class="process-flow">')
                    for index, item in enumerate(items, start=1):
                        block_body.append(
                            f'<li><span>{index:02d}</span><div>'
                            + (
                                f"<strong>{self._escape(str(item.get('title')))}</strong>"
                                if item.get("title")
                                else ""
                            )
                            + f"<p>{self._escape(str(item.get('text')))}</p>"
                            + "</div></li>"
                        )
                    block_body.append("</ol>")
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
                    table_html = self._table_html(rows)
                    if not table_html:
                        continue
                    block_body.append(table_html)
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
                    f'block-{emphasis}" data-content-role="'
                    f'{self._escape(str(block.get("content_role") or ""))}" '
                    f'style="--block-span:{span}">'
                    + "".join(block_body)
                    + "</div>"
                )
            if not rendered_blocks:
                continue
            section_layout = section.get("layout", {})
            if not isinstance(section_layout, dict):
                section_layout = {}
            density = _safe_id(section_layout.get("density", "comfortable"))
            section_id = _safe_id(
                section.get("section_id") or f"section-{section_index}"
            )
            body.extend(
                [
                    f'<section id="{section_id}" class="report-section density-{density}">',
                    '<div class="section-heading">',
                    '<div class="section-title-group">',
                    f'<span class="section-kicker">{section_index:02d} / Analysis</span>',
                    f"<h2>{self._escape(str(section.get('title', 'Section')))}</h2>",
                    "</div>",
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
                f"<summary>{self._escape(locale.data_notes_label)} "
                f"({len(warnings)})</summary><ul>"
            )
            for warning in warnings:
                body.append(f"<li>{self._escape(str(warning))}</li>")
            body.append("</ul></details></aside>")
        body.extend(
            [
                '<footer class="report-footer">',
                f"<span>{self._escape(locale.report_label)}</span>",
                (
                    f"<span>{source_count} "
                    f"{self._escape(locale.source_singular if source_count == 1 else locale.source_plural)}</span>"
                ),
                "</footer>",
                "</main>",
            ]
        )
        asset_tag = (
            f'<script src="{self._escape(self.asset_policy.echarts_script_url)}"></script>'
            if self.asset_policy.echarts_script_url
            else ""
        )
        return (
            f'<!doctype html><html lang="{self._escape(locale.html_lang)}">'
            '<head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>"
            + title
            + "</title><style>"
            + css
            + "</style></head><body>"
            + "".join(body)
            + asset_tag
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
            return ""
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

/* Technology editorial theme: restrained color, high contrast, deep-reading layout. */
:root {
  color-scheme: dark;
  --ink: #e8f2ff;
  --muted: #94a9bf;
  --line: rgba(148, 180, 216, 0.16);
  --surface: rgba(13, 29, 49, 0.82);
  --soft: #07111f;
  --accent: #67e8f9;
  --green: #5eead4;
  --amber: #fbbf24;
  --coral: #fb7185;
  --danger: #fb7185;
  --violet: #a78bfa;
  --page: #050b14;
}
html { scroll-behavior: smooth; }
html[data-theme="light"] {
  color-scheme: light;
  --ink: #102038;
  --muted: #5e7188;
  --line: rgba(41, 72, 108, 0.16);
  --surface: rgba(255, 255, 255, 0.92);
  --soft: #edf4fb;
  --page: #f7fbff;
}
body {
  background:
    radial-gradient(circle at 10% 0%, rgba(103, 232, 249, 0.08), transparent 28rem),
    radial-gradient(circle at 90% 6%, rgba(167, 139, 250, 0.07), transparent 32rem),
    var(--page);
  color: var(--ink);
}
body::before {
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  content: "";
  opacity: .2;
  background-image:
    linear-gradient(rgba(103, 232, 249, .08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(103, 232, 249, .08) 1px, transparent 1px);
  background-size: 64px 64px;
  mask-image: linear-gradient(to bottom, #000, transparent 72%);
}
.report-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 58px;
  padding: 0 max(24px, calc((100vw - 1180px) / 2));
  border-bottom: 1px solid var(--line);
  background: rgba(5, 11, 20, .76);
  backdrop-filter: blur(18px);
}
html[data-theme="light"] .report-nav { background: rgba(247, 251, 255, .82); }
.nav-brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .09em;
  text-decoration: none;
  text-transform: uppercase;
}
.brand-mark {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  background: var(--accent);
  box-shadow: 0 0 18px rgba(103, 232, 249, .75);
}
.nav-links { display: flex; gap: 4px; margin-left: auto; margin-right: 16px; }
.nav-links a, .theme-toggle {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 11px;
  text-decoration: none;
  cursor: pointer;
}
.nav-links a:hover, .theme-toggle:hover {
  border-color: var(--line);
  color: var(--accent);
  background: rgba(103, 232, 249, .06);
}
.report-shell { width: min(1180px, calc(100% - 40px)); }
.report-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  gap: 56px;
  align-items: end;
  padding: 92px 40px 76px;
}
.report-meta { flex-wrap: wrap; }
.report-type { color: var(--accent); letter-spacing: .12em; }
.status-pill {
  border-color: rgba(94, 234, 212, .28);
  background: rgba(94, 234, 212, .08);
  color: var(--green);
}
h1 {
  max-width: 940px;
  margin-bottom: 24px;
  font-size: clamp(44px, 6.5vw, 82px);
  font-weight: 780;
  letter-spacing: -.045em;
  line-height: .98;
  text-wrap: balance;
}
.report-summary {
  max-width: 850px;
  color: var(--muted);
  font-size: 18px;
  line-height: 1.75;
}
.hero-rule {
  width: min(260px, 55%);
  height: 3px;
  margin-top: 30px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent), var(--violet), #fb7185, #fbbf24);
}
.document-profile {
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: linear-gradient(145deg, rgba(103, 232, 249, .08), rgba(13, 29, 49, .72));
  box-shadow: 0 26px 80px rgba(0, 0, 0, .25);
}
html[data-theme="light"] .document-profile {
  background: linear-gradient(145deg, rgba(103, 232, 249, .14), rgba(255, 255, 255, .9));
}
.profile-eyebrow, .section-kicker {
  color: var(--accent);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}
.hero-profile-grid { display: grid; gap: 0; margin: 16px 0 0; }
.hero-profile-grid div {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.hero-profile-grid dt { color: var(--muted); font-size: 11px; text-transform: uppercase; }
.hero-profile-grid dd {
  margin: 0;
  color: var(--ink);
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.report-section { padding: 66px 40px 72px; }
.report-section + .report-section { padding-top: 72px; border-top-color: var(--line); }
.section-heading { align-items: start; margin-bottom: 30px; }
.section-title-group { max-width: 690px; }
.section-kicker { display: block; margin-bottom: 10px; }
.section-heading h2 {
  margin: 0;
  font-size: clamp(30px, 4vw, 48px);
  letter-spacing: -.035em;
  text-wrap: balance;
}
.section-heading p { color: var(--muted); line-height: 1.7; }
.section-grid { gap: 22px; }
.report-block {
  border-color: var(--line);
  border-radius: 14px;
  background: var(--surface);
  box-shadow: 0 18px 60px rgba(0, 0, 0, .16);
}
.report-block.block-featured {
  background:
    linear-gradient(145deg, rgba(103, 232, 249, .055), transparent 45%),
    var(--surface);
}
.report-block-supporting { background: rgba(13, 29, 49, .52); }
html[data-theme="light"] .report-block-supporting { background: rgba(255, 255, 255, .65); }
.block-heading h3 {
  font-size: 15px;
  letter-spacing: .02em;
  text-transform: uppercase;
}
.report-block p { color: var(--muted); font-size: 15px; line-height: 1.78; }
.report-block p + p { margin-top: 18px; }
.kpi-item {
  border-color: var(--line);
  border-radius: 14px;
  background: var(--surface);
  box-shadow: 0 18px 60px rgba(0, 0, 0, .14);
}
.kpi-item::before { height: 2px; box-shadow: 0 0 18px currentColor; }
.kpi-item dt { color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.kpi-item dd { color: var(--ink); }
.profile-list { display: grid; gap: 0; margin: 0; }
.profile-list div {
  display: grid;
  grid-template-columns: minmax(120px, .9fr) minmax(0, 1.1fr);
  gap: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.profile-list div:first-child { border-top: 0; }
.profile-list dt { color: var(--muted); font-size: 12px; }
.profile-list dd { margin: 0; color: var(--ink); font-size: 13px; font-weight: 700; text-align: right; }
.insight-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.insight-card {
  position: relative;
  min-height: 150px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(103, 232, 249, .025);
}
.insight-index { color: var(--accent); font: 800 10px/1 monospace; letter-spacing: .12em; }
.insight-card h4 { margin: 14px 0 8px; color: var(--ink); font-size: 16px; }
.insight-card p { font-size: 14px; }
.evidence-list, .process-flow { display: grid; gap: 14px; margin: 0; padding: 0; list-style: none; }
.evidence-list { counter-reset: evidence; }
.evidence-list li {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 12px;
}
.evidence-list li::before {
  counter-increment: evidence;
  content: counter(evidence, decimal-leading-zero);
  color: var(--accent);
  font: 800 10px/1.7 monospace;
}
.evidence-list strong, .process-flow strong { display: block; margin-bottom: 4px; color: var(--ink); }
.evidence-list small { display: block; margin-top: 7px; color: var(--accent); }
.process-flow li { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 14px; align-items: start; }
.process-flow li > span {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border: 1px solid rgba(103, 232, 249, .3);
  border-radius: 10px;
  color: var(--accent);
  background: rgba(103, 232, 249, .06);
  font: 800 10px/1 monospace;
}
.takeaway-list li { color: var(--muted); }
.table-wrap { border: 1px solid var(--line); border-radius: 10px; }
th, td { border-bottom-color: var(--line); }
th { color: var(--accent); background: rgba(103, 232, 249, .06); }
.chart-fallback, .no-data {
  color: var(--muted);
  background: rgba(251, 191, 36, .05);
}
.report-warnings {
  border-color: rgba(251, 191, 36, .22);
  background: rgba(251, 191, 36, .05);
  color: var(--amber);
}
.report-warnings ul { color: var(--muted); }
.report-footer { border-top-color: var(--line); }
@media (max-width: 900px) {
  .report-header { grid-template-columns: 1fr; gap: 36px; padding-top: 68px; }
  .document-profile { max-width: 520px; }
  .nav-links { display: none; }
}
@media (max-width: 640px) {
  .report-nav { padding: 0 18px; }
  .report-header { padding: 54px 20px 44px; }
  .report-section { padding: 48px 20px 52px; }
  .section-heading h2 { font-size: 32px; }
  .insight-grid { grid-template-columns: 1fr; }
  .profile-list div { grid-template-columns: 1fr; gap: 4px; }
  .profile-list dd { text-align: left; }
}
@media print {
  :root { color-scheme: light; --ink: #102038; --muted: #5e7188; --line: #dce2eb; --surface: #fff; --soft: #f4f6fb; --page: #fff; }
  body { background: #ffffff; }
  body::before, .report-nav, .theme-toggle { display: none; }
  .report-shell { width: 100%; margin: 0; }
  .report-block, .kpi-item { box-shadow: none; break-inside: avoid; }
  .report-header { padding-top: 36px; }
}
""".strip()

    @staticmethod
    def _javascript() -> str:
        return """
(function () {
  function initializeTheme() {
    var root = document.documentElement;
    var toggle = document.querySelector(".theme-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem("report-theme", next); } catch (error) {}
    });
    try {
      var saved = window.localStorage.getItem("report-theme");
      if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
    } catch (error) {}
  }
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
    document.addEventListener("DOMContentLoaded", function () {
      initializeTheme();
      renderCharts();
    });
  } else {
    initializeTheme();
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
