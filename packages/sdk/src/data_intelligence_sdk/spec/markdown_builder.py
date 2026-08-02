"""LLM-backed direct Markdown spec generation."""

from __future__ import annotations

import json
import re

from data_intelligence_sdk.core.types import IntentAnalysis, UserQuery
from data_intelligence_sdk.runtime.llm_client import LLMClient

_REQUIRED_MARKERS = (
    "# Interactive Execution Spec",
    "## User Request",
    "## Intent",
    "## Preparation Guidance",
    "## Execution Instructions",
    "## Expected Output",
)

_PRESENTATION_CONTRACT_HEADING = "## Presentation Contract"
_REPORT_CONTENT_ROLES = {
    "data_profile",
    "executive_summary",
    "key_findings",
    "supporting_evidence",
    "implication",
    "limitation",
    "recommendation",
    "narrative",
    "metrics",
    "chart",
    "table",
}


class LLMMarkdownSpecBuilder:
    def __init__(self, llm_client: LLMClient, *, max_validation_retries: int = 2) -> None:
        self.llm_client = llm_client
        self.max_validation_retries = max_validation_retries

    def build(self, query: UserQuery, analysis: IntentAnalysis) -> str:
        messages = _build_messages(query, analysis)
        current_messages = list(messages)
        for attempt in range(self.max_validation_retries + 1):
            markdown = self.llm_client.complete_text(
                current_messages,
                stage="markdown-spec-builder",
            )
            try:
                validated = validate_spec_markdown(markdown)
                extract_presentation_contract(validated, required=True)
                return validated
            except ValueError as exc:
                if attempt >= self.max_validation_retries:
                    raise
                current_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "The previous Markdown was structurally invalid: "
                            f"{type(exc).__name__}. Return the complete Markdown spec "
                            "with every required heading and a valid Presentation "
                            "Contract JSON code fence."
                        ),
                    },
                ]
        raise RuntimeError("Markdown spec validation retry loop exhausted.")


def validate_spec_markdown(markdown: str, *, max_characters: int = 100_000) -> str:
    normalized = markdown.strip()
    if not normalized:
        raise ValueError("Markdown spec must not be empty.")
    if len(normalized) > max_characters:
        raise ValueError("Markdown spec exceeds the size limit.")
    missing = [marker for marker in _REQUIRED_MARKERS if marker not in normalized]
    if missing:
        raise ValueError("Markdown spec is missing required headings.")
    return normalized + "\n"


def extract_presentation_contract(
    markdown: str,
    *,
    required: bool = False,
) -> dict[str, list[str]]:
    """Read the machine-readable presentation capability contract from Markdown."""

    heading_match = re.search(
        rf"(?m)^{re.escape(_PRESENTATION_CONTRACT_HEADING)}\s*$",
        str(markdown),
    )
    if heading_match is None:
        if required:
            raise ValueError("Markdown spec is missing Presentation Contract.")
        return {"report_content_roles": []}
    section = str(markdown)[heading_match.end() :]
    next_heading = re.search(r"(?m)^##\s+", section)
    if next_heading is not None:
        section = section[: next_heading.start()]
    fence = re.search(r"```json\s*(\{.*?\})\s*```", section, flags=re.DOTALL)
    if fence is None:
        raise ValueError("Presentation Contract must contain one JSON object.")
    try:
        payload = json.loads(fence.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Presentation Contract JSON is invalid.") from exc
    if not isinstance(payload, dict) or set(payload) != {"report_content_roles"}:
        raise ValueError(
            "Presentation Contract must contain only report_content_roles."
        )
    raw_roles = payload.get("report_content_roles")
    if not isinstance(raw_roles, list) or any(
        not isinstance(role, str) or role not in _REPORT_CONTENT_ROLES
        for role in raw_roles
    ):
        raise ValueError("Presentation Contract contains an invalid content role.")
    return {"report_content_roles": list(dict.fromkeys(raw_roles))}


def _build_messages(
    query: UserQuery,
    analysis: IntentAnalysis,
) -> list[dict[str, str]]:
    context = {
        "query": query.text,
        "normalized_intent": analysis.intent,
        "catalog_intent": analysis.catalog_intent_id,
        "preprocessing_guidance": [
            {
                "order": step.order,
                "type": step.step_type,
                "description": step.description,
            }
            for step in sorted(
                analysis.preprocessing_steps,
                key=lambda item: (item.order, item.name),
            )
        ],
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=True, sort_keys=True),
        },
    ]


_SYSTEM_PROMPT = """Create a Markdown execution spec for a Report Engine.

Return Markdown only, beginning with `# Interactive Execution Spec` and using
these sections in order: User Request, Intent, Preparation Guidance, Execution
Instructions, Expected Output, and Presentation Contract.

The Execution Instructions must tell the engine to retrieve every document
needed to answer the request from its configured organization corpus, judge
relevance from content and retrieval scores, and cite every document used.

The Presentation Contract must be the final section and contain exactly one
`json` code fence with this object:
`{"report_content_roles": [...]}`. Normalize only presentation capabilities
explicitly requested by the user. Allowed values are `data_profile`,
`executive_summary`, `key_findings`, `supporting_evidence`, `implication`,
`limitation`, `recommendation`, `narrative`, `metrics`, `chart`, and `table`.
This list declares semantic capabilities only; it must not prescribe section
names, ordering, counts, layout, a domain, or particular metrics.

Do not return a top-level JSON document. Do not mention capability_requirements,
data_requirements, ExecutionSpec, engine selection, confirmation tokens, or
credentials.
"""
