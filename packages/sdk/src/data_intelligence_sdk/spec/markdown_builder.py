"""LLM-backed direct Markdown spec generation."""

from __future__ import annotations

import json

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
                return validate_spec_markdown(markdown)
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
                            "with every required heading and no JSON."
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
Instructions, and Expected Output.

The Execution Instructions must tell the engine to retrieve every document
needed to answer the request from its configured organization corpus, judge
relevance from content and retrieval scores, and cite every document used.

Do not return JSON. Do not mention capability_requirements, data_requirements,
ExecutionSpec, engine selection, confirmation tokens, or credentials.
"""
