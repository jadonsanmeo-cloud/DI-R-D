"""Typed policies shared by report planning, execution, and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

CONTENT_ROLES = frozenset(
    {
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
)

REPORT_BLOCK_TYPES = frozenset(
    {
        "narrative",
        "kpi_group",
        "chart",
        "table",
        "recommendations",
        "profile",
        "insight_grid",
        "evidence_list",
        "process_flow",
    }
)


class ReportFormat(StrEnum):
    """Output formats supported by the report workflow."""

    MARKDOWN = "markdown"
    HTML = "html"
    STRUCTURED_REPORT = "structured_report"


@dataclass(frozen=True, slots=True)
class ReportFormatRegistry:
    """Validate formats and select the matching rendered result."""

    artifact_formats: dict[ReportFormat, str | None] = field(
        default_factory=lambda: {
            ReportFormat.MARKDOWN: "markdown",
            ReportFormat.HTML: "html",
            ReportFormat.STRUCTURED_REPORT: None,
        }
    )

    def resolve(self, value: Any) -> ReportFormat:
        normalized = str(value or ReportFormat.MARKDOWN).strip().lower()
        try:
            report_format = ReportFormat(normalized)
        except ValueError as exc:
            supported = ", ".join(item.value for item in self.artifact_formats)
            raise ValueError(
                f"Unsupported report output format {normalized!r}; "
                f"supported formats: {supported}."
            ) from exc
        if report_format not in self.artifact_formats:
            raise ValueError(f"No renderer is registered for {report_format.value!r}.")
        return report_format

    def select(
        self,
        report_format: ReportFormat,
        structured_report: dict[str, Any],
        rendered_reports: list[dict[str, Any]],
    ) -> Any:
        artifact_format = self.artifact_formats[report_format]
        if artifact_format is None:
            return structured_report
        for rendered in rendered_reports:
            if rendered.get("format") == artifact_format:
                return rendered.get("content")
        raise ValueError(
            f"Renderer did not produce registered format {artifact_format!r}."
        )


@dataclass(frozen=True, slots=True)
class ChartPolicy:
    """Limits applied consistently to report-facing chart datasets."""

    max_inline_rows: int = 100
    max_dataset_rows: int = 40
    max_categories: int = 12

    def __post_init__(self) -> None:
        for name in ("max_inline_rows", "max_dataset_rows", "max_categories"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1.")


@dataclass(frozen=True, slots=True)
class TemplateSelectionPolicy:
    """Manifest-backed controls for content-aware template selection."""

    fallback_template_id: str
    minimum_confidence: float
    max_preview_characters: int
    text_extensions: tuple[str, ...]

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "TemplateSelectionPolicy":
        raw = manifest.get("selection_policy", {})
        fallback_template_id = str(manifest.get("fallback_template_id") or "").strip()
        if not fallback_template_id:
            raise ValueError("Template manifest must declare fallback_template_id.")
        minimum_confidence = float(raw.get("minimum_confidence"))
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("Template minimum_confidence must be between 0 and 1.")
        max_preview_characters = int(raw.get("max_preview_characters"))
        if max_preview_characters < 1:
            raise ValueError("Template max_preview_characters must be positive.")
        extensions = tuple(
            str(item).lower()
            for item in raw.get("text_extensions", [])
            if str(item).startswith(".")
        )
        if not extensions:
            raise ValueError("Template selection_policy.text_extensions is required.")
        return cls(
            fallback_template_id=fallback_template_id,
            minimum_confidence=minimum_confidence,
            max_preview_characters=max_preview_characters,
            text_extensions=extensions,
        )


@dataclass(frozen=True, slots=True)
class ReportAssetPolicy:
    """Explicit external asset policy used by the HTML renderer."""

    echarts_script_url: str | None = (
        "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"
    )


_ENGLISH_STOPWORDS = frozenset(
    {
        "about", "after", "also", "and", "are", "been", "being", "between",
        "can", "could", "data", "does", "each", "for", "from", "had", "has",
        "have", "into", "its", "more", "not", "only", "other", "should",
        "such", "than", "that", "the", "their", "then", "there", "these",
        "they", "this", "those", "through", "under", "using", "was", "were",
        "which", "will", "with", "would", "your",
    }
)
_VIETNAMESE_STOPWORDS = frozenset(
    {
        "bị", "bởi", "các", "có", "của", "đã", "đang", "được", "giữa",
        "khi", "không", "là", "một", "những", "này", "theo", "trong",
        "từ", "và", "với",
    }
)


@dataclass(frozen=True, slots=True)
class LocalePolicy:
    """Locale-specific labels and tokenization inputs."""

    locale: str
    html_lang: str
    report_label: str
    data_notes_label: str
    source_singular: str
    source_plural: str
    stopwords: frozenset[str]

    @classmethod
    def for_locale(cls, value: Any) -> "LocalePolicy":
        locale = str(value or "en").strip() or "en"
        language = locale.split("-", 1)[0].split("_", 1)[0].lower()
        if language == "vi":
            return cls(
                locale=locale,
                html_lang="vi",
                report_label="Báo cáo Data Intelligence",
                data_notes_label="Ghi chú dữ liệu",
                source_singular="nguồn",
                source_plural="nguồn",
                stopwords=_VIETNAMESE_STOPWORDS,
            )
        return cls(
            locale=locale,
            html_lang=language or "en",
            report_label="Data Intelligence Report",
            data_notes_label="Data notes",
            source_singular="source",
            source_plural="sources",
            stopwords=_ENGLISH_STOPWORDS if language == "en" else frozenset(),
        )


@dataclass(frozen=True, slots=True)
class SourceHandlerPolicy:
    """Stable capability contract for one materializable source type."""

    capability_id: str
    source_kind: str
    operation_kinds: frozenset[str]
    extensions: tuple[str, ...] = ()
    tool_aliases: tuple[str, ...] = ()
    argument_name: str = "path"

    def matches_source(self, source: str) -> bool:
        return bool(self.extensions) and Path(source).suffix.lower() in self.extensions

    def accepts_operation(self, operation_kind: str) -> bool:
        normalized = operation_kind.lower()
        return normalized == self.capability_id or normalized in self.operation_kinds


@dataclass(frozen=True, slots=True)
class SourceMaterializationRegistry:
    """Resolve source handlers by stable capability rather than tool display name."""

    handlers: tuple[SourceHandlerPolicy, ...]

    def resolve_source(
        self, sources: list[str], operation_kind: str
    ) -> tuple[SourceHandlerPolicy, str] | None:
        for source in sources:
            for handler in self.handlers:
                if handler.matches_source(source) and handler.accepts_operation(
                    operation_kind
                ):
                    return handler, source
        return None

    def resolve_operation(self, operation_kind: str) -> SourceHandlerPolicy | None:
        return next(
            (
                handler
                for handler in self.handlers
                if not handler.extensions
                and handler.accepts_operation(operation_kind)
            ),
            None,
        )

    @staticmethod
    def resolve_tool(
        method_hub: list[dict[str, Any]], handler: SourceHandlerPolicy
    ) -> dict[str, Any] | None:
        for tool in method_hub:
            capabilities = {
                str(item)
                for item in tool.get("capability_names", [])
                if str(item)
            }
            if handler.capability_id in capabilities:
                return tool
        aliases = set(handler.tool_aliases)
        return next(
            (
                tool
                for tool in method_hub
                if str(tool.get("tool_name", "")) in aliases
            ),
            None,
        )


DEFAULT_SOURCE_MATERIALIZATION_REGISTRY = SourceMaterializationRegistry(
    handlers=(
        SourceHandlerPolicy(
            capability_id="source.spreadsheet.materialize",
            source_kind="spreadsheet",
            extensions=(".xls", ".xlsx", ".xlsm", ".xltx", ".xltm"),
            tool_aliases=("materialize_spreadsheet",),
            operation_kinds=frozenset(
                {
                    "inspect", "inspect_data", "inspect_schema", "load",
                    "load_excel", "load_spreadsheet", "materialize_excel",
                    "materialize_source", "materialize_spreadsheet", "profile",
                    "read", "read_excel", "read_spreadsheet",
                }
            ),
        ),
        SourceHandlerPolicy(
            capability_id="source.pdf.extract_text",
            source_kind="document",
            extensions=(".pdf",),
            tool_aliases=("extract_pdf_text",),
            operation_kinds=frozenset(
                {
                    "extract_document_text", "extract_pdf_text", "extract_text",
                    "inspect_document", "load_document", "load_pdf",
                    "parse_document", "parse_pdf_to_text", "read_document",
                    "read_pdf", "read_source_content", "segment_by_page",
                    "split_pages",
                }
            ),
        ),
        SourceHandlerPolicy(
            capability_id="source.csv.scan",
            source_kind="table",
            extensions=(".csv",),
            tool_aliases=("scan_csv",),
            operation_kinds=frozenset(
                {
                    "inspect", "inspect_csv", "inspect_data", "inspect_schema",
                    "load", "load_csv", "materialize_csv",
                    "materialize_source", "profile", "profile_csv", "read",
                    "read_csv", "read_source_content",
                }
            ),
        ),
        SourceHandlerPolicy(
            capability_id="source.document.retrieve",
            source_kind="document",
            tool_aliases=("retrieve_document", "extract_document_text"),
            operation_kinds=frozenset(
                {"retrieve_document_content", "materialize_document"}
            ),
            argument_name="document",
        ),
        SourceHandlerPolicy(
            capability_id="source.vector.retrieve",
            source_kind="vector_collection",
            tool_aliases=("retrieve_vector_collection", "search_vector_collection"),
            operation_kinds=frozenset(
                {"retrieve_vector_content", "materialize_vector_collection"}
            ),
            argument_name="collection",
        ),
    )
)


def legacy_content_role(block: dict[str, Any]) -> str:
    """Map a pre-content-role template block during the compatibility window."""

    block_type = str(block.get("type", ""))
    if block_type == "chart":
        return "chart"
    if block_type == "kpi_group":
        return "metrics"
    if block_type == "table":
        return "table"
    identity = " ".join(
        [str(block.get("block_id", "")), str(block.get("title", ""))]
    ).lower()
    if any(token in identity for token in ("limitation", "caveat", "next")):
        return "limitation"
    if any(token in identity for token in ("supporting-evidence", "evidence")):
        return "supporting_evidence"
    if any(
        token in identity
        for token in ("interpretation", "implication", "why-it-matters")
    ):
        return "implication"
    if any(token in identity for token in ("finding", "takeaway")):
        return "key_findings"
    if "summary" in identity or "narrative" in identity:
        return "executive_summary"
    return "recommendation" if block_type == "recommendations" else "narrative"
