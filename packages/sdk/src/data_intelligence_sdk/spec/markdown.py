"""Deterministic Markdown presentation for prepared execution specs."""

from __future__ import annotations

import json
from typing import Any

from data_intelligence_sdk.core.types import ExecutionSpec, IntentAnalysis


def render_spec_markdown(
    spec: ExecutionSpec,
    intent_analysis: IntentAnalysis | None = None,
) -> str:
    """Render a one-way human-readable view of an execution spec."""

    state = "Confirmed" if spec.confirmed else "Draft"
    catalog_intent = (
        intent_analysis.catalog_intent_id if intent_analysis is not None else None
    )
    lines = [
        f"# Execution Spec ({state})",
        "",
        "## Objective",
        "",
        spec.objective,
        "",
        "## Intents",
        "",
        f"- Normalized: `{spec.intent}`",
        f"- Catalog: `{catalog_intent}`" if catalog_intent else "- Catalog: _None._",
        "",
        "## Data Requirements",
        "",
    ]
    lines.extend(_bullet_list(spec.data_requirements))
    lines.extend(["", "## Preprocessing Steps", ""])
    if spec.preprocessing_steps:
        for index, step in enumerate(
            sorted(spec.preprocessing_steps, key=lambda item: (item.order, item.name)),
            start=1,
        ):
            lines.append(f"{index}. **{step.name}** (`{step.step_type}`)")
            if step.description:
                lines.append(f"   {step.description}")
            lines.append(f"   - Order: `{step.order}`")
            lines.append(f"   - Required: `{str(step.required).lower()}`")
            if step.capability:
                lines.append(f"   - Capability: `{step.capability}`")
            dependencies = ", ".join(f"`{item}`" for item in step.depends_on)
            lines.append(f"   - Depends on: {dependencies or '_None._'}")
    else:
        lines.append("_None._")

    lines.extend(["", "## Capability Requirements", ""])
    if spec.capability_requirements:
        for capability in spec.capability_requirements:
            lines.append(f"### {capability.name}")
            lines.append("")
            if capability.description:
                lines.append(capability.description)
                lines.append("")
            _append_mapping(lines, "Input Schema", capability.input_schema)
            _append_mapping(lines, "Output Schema", capability.output_schema)
            _append_mapping(lines, "Constraints", capability.constraints)
            _append_mapping(lines, "Metadata", capability.metadata)
    else:
        lines.append("_None._")

    lines.extend(["", "## Constraints", ""])
    _append_mapping_body(lines, spec.constraints)
    lines.extend(["", "## Engine Hint", ""])
    lines.append(f"- `{spec.engine_hint}`" if spec.engine_hint else "_None._")
    return "\n".join(lines).rstrip() + "\n"


def _bullet_list(values: list[str]) -> list[str]:
    return [f"- `{value}`" for value in values] if values else ["_None._"]


def _append_mapping(lines: list[str], title: str, value: dict[str, Any]) -> None:
    lines.extend([f"#### {title}", ""])
    _append_mapping_body(lines, value)
    lines.append("")


def _append_mapping_body(lines: list[str], value: dict[str, Any]) -> None:
    if not value:
        lines.append("_None._")
        return
    nested: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, (dict, list)):
            nested[key] = item
        else:
            lines.append(f"- {key}: `{_scalar_text(item)}`")
    if nested:
        lines.extend(
            [
                "```json",
                json.dumps(nested, ensure_ascii=True, indent=2, sort_keys=True),
                "```",
            ]
        )


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
