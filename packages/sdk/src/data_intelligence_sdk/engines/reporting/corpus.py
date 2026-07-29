"""Ingested-corpus boundary owned by the Report Engine.

The report workflow uses corpus search tools only to discover document
identities from natural-language requests.  Once a document is selected,
``corpus_get_file_ingested_data`` remains the sole hydration boundary.  Search
chunks never become report data or identity values themselves, and ambiguous
document matches remain explicit candidates.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


CORPUS_GET_FILE_INGESTED_DATA = "corpus_get_file_ingested_data"
CORPUS_HYBRID_SEARCH = "corpus_hybrid_search"
CORPUS_KEYWORD_SEARCH_CONTENTS = "corpus_keyword_search_contents"
CORPUS_RETRIEVE_CONTEXT = "corpus_retrieve_context"
CORPUS_MATERIALIZE_OPERATION = "corpus_ingested_document_materialize"
_CORPUS_OPERATIONS = frozenset(
    {
        CORPUS_MATERIALIZE_OPERATION,
        "analyze_ingested_document",
        "analyze_document_chunks",
        "analyze_extracted_tables",
        "compare_ingested_documents",
        "materialize_ingested_document",
    }
)
_SELECTOR_NAMES = ("document_id", "object_key", "file_name")
_TOOL_PARAMETERS = frozenset(
    {
        "file_name",
        "document_id",
        "organization_id",
        "bucket",
        "object_key",
        "match_mode",
        "mode",
        "chunk_start",
        "chunk_limit",
    }
)
_FILE_PATTERN = re.compile(
    r"(?<![\w.-])"
    r"([A-Za-z0-9][A-Za-z0-9_.()-]{0,220}"
    r"\.(?:pdf|docx?|txt|md|csv|xlsx?|jsonl?|ya?ml|pptx?|parquet|html?))"
    r"(?![\w-])",
    flags=re.IGNORECASE,
)
_QUOTED_FILE_PATTERN = re.compile(
    r"[`\"']([^`\"'\r\n]{1,240}"
    r"\.(?:pdf|docx?|txt|md|csv|xlsx?|jsonl?|ya?ml|pptx?|parquet|html?))[`\"']",
    flags=re.IGNORECASE,
)
_CONTEXT_FILE_PATTERN = re.compile(
    r"\b(?:from|file(?:[\s_-]*name)?)\b\s*"
    r"(?:the\s+)?(?:file\s+)?(?:named\s+)?[:=]?\s*[`\"']?"
    r"([A-Za-z0-9][^`\"'\r\n,;]{0,220}?"
    r"\.(?:pdf|docx?|txt|md|csv|xlsx?|jsonl?|ya?ml|pptx?|parquet|html?))",
    flags=re.IGNORECASE,
)
_DOCUMENT_ID_PATTERN = re.compile(
    r"\bdocument[\s_-]*id\b\s*[:=]?\s*[`\"']?"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{2,})",
    flags=re.IGNORECASE,
)
_OBJECT_KEY_PATTERN = re.compile(
    r"\bobject[\s_-]*key\b\s*[:=]?\s*[`\"']?"
    r"([^`\"'\r\n,;]{1,512})",
    flags=re.IGNORECASE,
)


class ReportCorpusResolutionError(RuntimeError):
    """A stable report-local corpus resolution failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class ReportFileSelection:
    """Validated identity selector extracted from the confirmed report spec."""

    selector_name: str
    selector_value: str
    organization_id: str | None = None
    bucket: str | None = None
    match_mode: str = "exact"
    mode: str = "overview"
    chunk_start: int = 0
    chunk_limit: int | None = None
    candidate_policy: str = "require_selection"
    max_documents: int = 20
    selection_source: str = "explicit"
    discovery_tool: str | None = None
    discovery_query: str | None = None
    discovery_score: float | None = None
    discovered_documents: tuple[dict[str, Any], ...] = ()

    def arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            self.selector_name: self.selector_value,
            "mode": self.mode,
        }
        if self.organization_id:
            arguments["organization_id"] = self.organization_id
        if self.bucket:
            arguments["bucket"] = self.bucket
        if self.selector_name == "file_name":
            arguments["match_mode"] = self.match_mode
        if self.mode == "page":
            arguments["chunk_start"] = self.chunk_start
            if self.chunk_limit is not None:
                arguments["chunk_limit"] = self.chunk_limit
        return arguments


@dataclass(frozen=True, slots=True)
class ReportCorpusResolution:
    """Resolved state passed from the corpus boundary into planning."""

    spec: ExecutionSpec
    corpus_package: DataCorpusPackage
    evidence_package: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReportCorpusDiscoveryRequest:
    """Validated semantic-discovery policy derived from the report spec."""

    query: str
    organization_id: str | None
    top_k: int
    min_score: float
    min_margin: float
    candidate_policy: str
    max_documents: int
    mode: str


@dataclass(frozen=True, slots=True)
class ReportCorpusPolicy:
    """Limits and stable tool contract for report corpus materialization."""

    tool_name: str = CORPUS_GET_FILE_INGESTED_DATA
    discovery_tool_names: tuple[str, ...] = (
        CORPUS_HYBRID_SEARCH,
        CORPUS_KEYWORD_SEARCH_CONTENTS,
        CORPUS_RETRIEVE_CONTEXT,
    )
    default_mode: str = "overview"
    default_max_documents: int = 20
    default_discovery_top_k: int = 10
    default_discovery_min_score: float = 0.35
    default_discovery_min_margin: float = 0.08
    max_preview_characters_per_document: int = 6_000
    max_chunk_previews_per_document: int = 3


class ReportCorpusResolver:
    """Resolve identity-selected ingested documents before report planning."""

    def __init__(self, policy: ReportCorpusPolicy | None = None) -> None:
        self.policy = policy or ReportCorpusPolicy()

    def should_resolve(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> bool:
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        if isinstance(constraints.get("report_data_selection"), dict):
            return True
        if any(str(source).startswith("corpus://") for source in corpus_package.sources):
            return True
        return any(tool.name == self.policy.tool_name for tool in runtime.mcp_tools)

    def resolve(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        *,
        query_text: str | None = None,
    ) -> ReportCorpusResolution:
        tool = self._resolve_tool(runtime)
        selection = self.extract_selection(
            spec,
            corpus_package,
            query_text=query_text,
            policy=self.policy,
        )
        if selection is None:
            selection = self._discover_selection(
                spec,
                corpus_package,
                runtime,
                query_text=query_text,
            )

        if selection.discovered_documents:
            documents = self._hydrate_discovered_documents(
                selection,
                runtime,
                tool_name=tool.name,
            )
        else:
            initial_payload = self._call_tool(
                runtime,
                tool_name=tool.name,
                arguments=selection.arguments(),
            )
            documents = self._documents_from_payload(
                initial_payload,
                selection,
                runtime,
                tool_name=tool.name,
            )
        evidence = self._evidence_package(
            documents,
            selection,
            runtime,
        )
        resolved_spec, resolved_corpus = self._enrich_report_context(
            spec,
            corpus_package,
            evidence,
            selection,
        )
        return ReportCorpusResolution(
            spec=resolved_spec,
            corpus_package=resolved_corpus,
            evidence_package=evidence,
        )

    def _discover_selection(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        *,
        query_text: str | None,
    ) -> ReportFileSelection:
        request = self._discovery_request(
            spec,
            corpus_package,
            query_text=query_text,
        )
        tools = self._resolve_discovery_tools(runtime)
        if not tools:
            raise ReportCorpusResolutionError(
                "report_file_selector_missing",
                (
                    "The report spec did not provide file_name, document_id, or "
                    "object_key, and Method Hub exposes no compatible corpus "
                    "discovery tool."
                ),
                details={"discovery_query": request.query},
            )

        failures = []
        for tool in tools:
            arguments = self._discovery_arguments(tool, request)
            try:
                payload = self._call_discovery_tool(
                    runtime,
                    tool_name=tool.name,
                    arguments=arguments,
                )
            except ReportCorpusResolutionError as exc:
                failures.append(
                    {
                        "tool_name": tool.name,
                        "code": exc.code,
                        "error": str(exc),
                    }
                )
                continue
            candidates = _aggregate_discovery_candidates(payload)
            if not candidates:
                continue
            selected = self._select_discovery_candidates(candidates, request)
            primary = selected[0]
            return ReportFileSelection(
                selector_name="document_id",
                selector_value=str(primary["document_id"]),
                organization_id=(
                    _optional_string(primary.get("organization_id"))
                    or request.organization_id
                ),
                mode=request.mode,
                candidate_policy=(
                    "all_matches" if len(selected) > 1 else "require_selection"
                ),
                max_documents=request.max_documents,
                selection_source="chunk_search",
                discovery_tool=tool.name,
                discovery_query=request.query,
                discovery_score=_optional_float(primary.get("score")),
                discovered_documents=tuple(deepcopy(selected)),
            )

        if failures and len(failures) == len(tools):
            raise ReportCorpusResolutionError(
                "corpus_document_discovery_failed",
                "Every compatible corpus discovery tool failed.",
                details={
                    "query": request.query,
                    "failures": failures,
                },
            )
        raise ReportCorpusResolutionError(
            "ingested_document_discovery_no_matches",
            "Corpus chunk search found no ingested document for the report query.",
            details={
                "query": request.query,
                "organization_id": request.organization_id,
                "tools": [tool.name for tool in tools],
                "failures": failures,
            },
        )

    def _discovery_request(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        *,
        query_text: str | None,
    ) -> ReportCorpusDiscoveryRequest:
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        raw = constraints.get("report_data_discovery")
        raw = raw if isinstance(raw, dict) else {}
        query = _optional_string(raw.get("query"))
        if query is None:
            query = _report_request_text(query_text) or _report_request_text(
                spec.objective
            )
        if query is None:
            raise ReportCorpusResolutionError(
                "report_discovery_query_missing",
                "The report spec contains no usable corpus discovery query.",
            )
        organization_id = _optional_string(
            raw.get("organization_id")
            or corpus_package.metadata.get("organization_id")
        )
        top_k = min(
            100,
            _positive_int(
                raw.get("top_k"),
                self.policy.default_discovery_top_k,
            ),
        )
        min_score = _bounded_float(
            raw.get("min_score"),
            self.policy.default_discovery_min_score,
            minimum=0.0,
            maximum=1.0,
        )
        min_margin = _bounded_float(
            raw.get("min_margin"),
            self.policy.default_discovery_min_margin,
            minimum=0.0,
            maximum=1.0,
        )
        candidate_policy = str(
            raw.get("candidate_policy") or "all_relevant"
        ).lower()
        if candidate_policy not in {
            "all_relevant",
            "auto_select_confident",
            "require_selection",
        }:
            raise ReportCorpusResolutionError(
                "report_discovery_policy_invalid",
                (
                    "report_data_discovery.candidate_policy must be "
                    "all_relevant, auto_select_confident, or "
                    "require_selection."
                ),
            )
        max_documents = min(
            100,
            _positive_int(
                raw.get("max_documents"),
                self.policy.default_max_documents,
            ),
        )
        mode = str(raw.get("initial_mode") or self.policy.default_mode).lower()
        if mode not in {"all", "overview", "page"}:
            raise ReportCorpusResolutionError(
                "report_discovery_policy_invalid",
                "report_data_discovery.initial_mode must be all, overview, or page.",
            )
        return ReportCorpusDiscoveryRequest(
            query=query,
            organization_id=organization_id,
            top_k=top_k,
            min_score=min_score,
            min_margin=min_margin,
            candidate_policy=candidate_policy,
            max_documents=max_documents,
            mode=mode,
        )

    def _resolve_discovery_tools(
        self,
        runtime: EngineRuntimeContext,
    ) -> list[Any]:
        definitions = {tool.name: tool for tool in runtime.mcp_tools}
        compatible = []
        for name in self.policy.discovery_tool_names:
            tool = definitions.get(name)
            if tool is None:
                continue
            properties = tool.input_schema.get("properties", {})
            if isinstance(properties, dict) and "query" in properties:
                compatible.append(tool)
        return compatible

    @staticmethod
    def _discovery_arguments(
        tool: Any,
        request: ReportCorpusDiscoveryRequest,
    ) -> dict[str, Any]:
        properties = tool.input_schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        arguments: dict[str, Any] = {"query": request.query}
        if "top_k" in properties:
            arguments["top_k"] = request.top_k
        if request.organization_id and "organization_id" in properties:
            arguments["organization_id"] = request.organization_id
        return arguments

    def _call_discovery_tool(
        self,
        runtime: EngineRuntimeContext,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            raw_result = runtime.mcp_client.call_tool(tool_name, arguments)
            payload = _unwrap_discovery_result(raw_result)
        except Exception as exc:
            runtime.run_context.record_method_call(
                tool_name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc), "provider": "mcp"},
            )
            raise ReportCorpusResolutionError(
                "corpus_document_discovery_call_failed",
                str(exc),
            ) from exc
        candidates = _aggregate_discovery_candidates(payload)
        runtime.run_context.record_method_call(
            tool_name,
            inputs=arguments,
            outputs={
                "provider": "mcp",
                "result_count": len(_discovery_hits(payload)),
                "document_count": len(candidates),
                "document_ids": [
                    item["document_id"] for item in candidates
                ],
            },
        )
        return payload

    @staticmethod
    def _select_discovery_candidates(
        candidates: list[dict[str, Any]],
        request: ReportCorpusDiscoveryRequest,
    ) -> list[dict[str, Any]]:
        if len(candidates) == 1:
            candidate = candidates[0]
            score = _optional_float(candidate.get("score"))
            if score is not None and score < request.min_score:
                raise ReportCorpusResolutionError(
                    "ingested_document_discovery_low_confidence",
                    "The only discovered document is below the confidence threshold.",
                    details={
                        "query": request.query,
                        "min_score": request.min_score,
                        "candidates": candidates,
                    },
                )
            return [candidate]

        top = candidates[0]
        runner_up = candidates[1]
        top_score = _optional_float(top.get("score"))
        runner_up_score = _optional_float(runner_up.get("score"))
        if request.candidate_policy == "all_relevant":
            if top_score is None:
                raise ReportCorpusResolutionError(
                    "ingested_document_selection_required",
                    "Corpus search returned multiple unscored document candidates.",
                    details={
                        "query": request.query,
                        "candidates": candidates,
                    },
                )
            cutoff = max(request.min_score, top_score - request.min_margin)
            relevant = [
                item
                for item in candidates
                if (
                    _optional_float(item.get("score")) is not None
                    and float(item["score"]) >= cutoff
                )
            ]
            if not relevant:
                raise ReportCorpusResolutionError(
                    "ingested_document_discovery_low_confidence",
                    "No discovered document meets the relevance threshold.",
                    details={
                        "query": request.query,
                        "min_score": request.min_score,
                        "candidates": candidates,
                    },
                )
            if len(relevant) > request.max_documents:
                raise ReportCorpusResolutionError(
                    "ingested_document_candidate_limit_exceeded",
                    (
                        f"Chunk search matched {len(relevant)} relevant "
                        f"documents, exceeding the report limit of "
                        f"{request.max_documents}."
                    ),
                    details={"candidates": relevant},
                )
            return relevant
        confident = (
            request.candidate_policy == "auto_select_confident"
            and top_score is not None
            and runner_up_score is not None
            and top_score >= request.min_score
            and (top_score - runner_up_score) >= request.min_margin
        )
        if confident:
            return [top]
        raise ReportCorpusResolutionError(
            "ingested_document_selection_required",
            "Corpus chunk search matched multiple possible report documents.",
            details={
                "query": request.query,
                "candidate_policy": request.candidate_policy,
                "min_score": request.min_score,
                "min_margin": request.min_margin,
                "candidates": candidates,
            },
        )

    def _resolve_tool(self, runtime: EngineRuntimeContext) -> Any:
        if runtime.mcp_client is None:
            raise ReportCorpusResolutionError(
                "method_hub_unavailable",
                "Method Hub MCP client is required for ingested report data.",
            )
        tool = next(
            (
                definition
                for definition in runtime.mcp_tools
                if definition.name == self.policy.tool_name
            ),
            None,
        )
        if tool is None:
            raise ReportCorpusResolutionError(
                "corpus_ingested_data_tool_missing",
                f"Method Hub does not expose {self.policy.tool_name}.",
            )
        properties = tool.input_schema.get("properties", {})
        if not isinstance(properties, dict) or not _TOOL_PARAMETERS.issubset(properties):
            missing = sorted(_TOOL_PARAMETERS.difference(properties or {}))
            raise ReportCorpusResolutionError(
                "corpus_ingested_data_tool_incompatible",
                "The Method Hub tool schema is missing required report parameters.",
                details={"missing_parameters": missing},
            )
        return tool

    def _call_tool(
        self,
        runtime: EngineRuntimeContext,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            raw_result = runtime.mcp_client.call_tool(tool_name, arguments)
            payload = unwrap_ingested_data_result(raw_result)
        except Exception as exc:
            runtime.run_context.record_method_call(
                tool_name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc), "provider": "mcp"},
            )
            raise ReportCorpusResolutionError(
                "corpus_ingested_data_call_failed",
                str(exc),
            ) from exc
        runtime.run_context.record_method_call(
            tool_name,
            inputs=arguments,
            outputs={
                "provider": "mcp",
                "error": payload.get("error"),
                "document_id": (
                    payload.get("document", {}).get("document_id")
                    if isinstance(payload.get("document"), dict)
                    else None
                ),
                "candidate_count": len(_dict_items(payload.get("matches"))),
                "content_count": len(_dict_items(payload.get("contents"))),
                "chunk_count": len(_dict_items(payload.get("chunks"))),
            },
        )
        return payload

    def _hydrate_discovered_documents(
        self,
        selection: ReportFileSelection,
        runtime: EngineRuntimeContext,
        *,
        tool_name: str,
    ) -> list[dict[str, Any]]:
        hydrated = []
        for candidate in selection.discovered_documents:
            document_id = _optional_string(candidate.get("document_id"))
            if document_id is None:
                continue
            arguments: dict[str, Any] = {
                "document_id": document_id,
                "mode": selection.mode,
            }
            organization_id = (
                _optional_string(candidate.get("organization_id"))
                or selection.organization_id
            )
            if organization_id:
                arguments["organization_id"] = organization_id
            if selection.mode == "page":
                arguments["chunk_start"] = selection.chunk_start
                if selection.chunk_limit is not None:
                    arguments["chunk_limit"] = selection.chunk_limit
            hydrated.append(
                self._validate_document_payload(
                    self._call_tool(
                        runtime,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                )
            )
        if not hydrated:
            raise ReportCorpusResolutionError(
                "ingested_document_not_found",
                "Chunk search returned no valid document identities to hydrate.",
            )
        return hydrated

    def _documents_from_payload(
        self,
        payload: dict[str, Any],
        selection: ReportFileSelection,
        runtime: EngineRuntimeContext,
        *,
        tool_name: str,
    ) -> list[dict[str, Any]]:
        matches = _dict_items(payload.get("matches"))
        error = str(payload.get("error") or "")
        document = payload.get("document")
        if isinstance(document, dict) and document.get("document_id"):
            return [self._validate_document_payload(payload)]
        if error == "document_not_found" or (not matches and not document):
            raise ReportCorpusResolutionError(
                "ingested_document_not_found",
                "No ingested document matched the confirmed report selector.",
                details={"selector": asdict(selection)},
            )
        if len(matches) > 1 and selection.candidate_policy != "all_matches":
            raise ReportCorpusResolutionError(
                "ingested_document_selection_required",
                "The selector matched multiple ingested documents.",
                details={"candidates": matches},
            )
        if len(matches) > selection.max_documents:
            raise ReportCorpusResolutionError(
                "ingested_document_candidate_limit_exceeded",
                (
                    f"The selector matched {len(matches)} documents, exceeding "
                    f"the report limit of {selection.max_documents}."
                ),
                details={"candidates": matches},
            )
        if not matches:
            raise ReportCorpusResolutionError(
                "ingested_document_not_found",
                "The corpus response did not contain a document or candidates.",
            )

        hydrated = []
        for match in matches:
            document_id = str(match.get("document_id") or "").strip()
            if not document_id:
                continue
            arguments: dict[str, Any] = {
                "document_id": document_id,
                "mode": selection.mode,
            }
            organization_id = str(
                match.get("organization_id") or selection.organization_id or ""
            ).strip()
            if organization_id:
                arguments["organization_id"] = organization_id
            if selection.mode == "page":
                arguments["chunk_start"] = selection.chunk_start
                if selection.chunk_limit is not None:
                    arguments["chunk_limit"] = selection.chunk_limit
            hydrated.append(
                self._validate_document_payload(
                    self._call_tool(
                        runtime,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                )
            )
        if not hydrated:
            raise ReportCorpusResolutionError(
                "ingested_document_not_found",
                "No valid candidate document ids were returned by corpus-service.",
            )
        return hydrated

    @staticmethod
    def _validate_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
        error = str(payload.get("error") or "")
        if error:
            raise ReportCorpusResolutionError(
                f"corpus_{error}",
                str(payload.get("message") or error),
                details={"matches": _dict_items(payload.get("matches"))},
            )
        document = payload.get("document")
        if not isinstance(document, dict) or not document.get("document_id"):
            raise ReportCorpusResolutionError(
                "ingested_document_not_found",
                "Corpus-service did not return document metadata.",
            )
        status = str(document.get("current_status") or "")
        if status and status != "indexed":
            raise ReportCorpusResolutionError(
                "ingested_document_not_ready",
                f"Document {document.get('document_id')} is {status!r}, not indexed.",
            )
        processing_run = payload.get("processing_run")
        processing_status = (
            str(processing_run.get("status") or "")
            if isinstance(processing_run, dict)
            else ""
        )
        if processing_status and processing_status != "completed":
            raise ReportCorpusResolutionError(
                "ingested_document_processing_failed",
                (
                    f"Document {document.get('document_id')} processing status "
                    f"is {processing_status!r}."
                ),
            )
        if not ingested_data_has_content(payload):
            raise ReportCorpusResolutionError(
                "ingested_document_has_no_data",
                (
                    f"Document {document.get('document_id')} has no extracted "
                    "contents or indexed chunks."
                ),
            )
        return payload

    def _evidence_package(
        self,
        payloads: list[dict[str, Any]],
        selection: ReportFileSelection,
        runtime: EngineRuntimeContext,
    ) -> dict[str, Any]:
        documents = []
        for payload in payloads:
            document = deepcopy(payload["document"])
            document_id = str(document["document_id"])
            organization_id = str(
                document.get("organization_id") or selection.organization_id or ""
            )
            source_ref = (
                f"corpus://{organization_id}/{document_id}"
                if organization_id
                else f"corpus://{document_id}"
            )
            artifact_ref = f"memory://report/ingested/{document_id}"
            if runtime.run_artifact is not None:
                try:
                    artifact = runtime.run_artifact.record_data_output(
                        "resolve-ingested-data",
                        document_id,
                        payload,
                    )
                    artifact_ref = artifact.artifact_ref
                    runtime.run_context.add_artifact_ref(artifact_ref)
                except Exception:
                    pass
            previews, chunk_previews = self._previews(payload)
            summary = (
                deepcopy(payload.get("content_summary"))
                if isinstance(payload.get("content_summary"), dict)
                else {}
            )
            content_types = [
                str(item) for item in summary.get("content_types", []) if str(item)
            ]
            if not content_types:
                content_types = list(
                    dict.fromkeys(
                        str(item.get("type") or item.get("content_type"))
                        for item in (
                            _dict_items(payload.get("contents"))
                            + _dict_items(payload.get("chunks"))
                        )
                        if item.get("type") or item.get("content_type")
                    )
                )
            documents.append(
                {
                    **document,
                    "source_ref": source_ref,
                    "processing_run": deepcopy(payload.get("processing_run")),
                    "content_summary": summary,
                    "content_types": content_types,
                    "content_profile": {
                        "has_text": any(
                            item in content_types
                            for item in ("main_text", "text", "blocks")
                        ),
                        "has_tables": any(
                            item in content_types for item in ("table", "tables")
                        ),
                        "has_figures": any(
                            item in content_types for item in ("figure", "figures", "image")
                        ),
                        "has_formulas": any(
                            item in content_types for item in ("formula", "formulas")
                        ),
                        "total_chunks": int(summary.get("total_chunks") or 0),
                    },
                    "previews": previews,
                    "chunk_previews": chunk_previews,
                    "artifact_ref": artifact_ref,
                }
            )
        return {
            "schema_version": "1.0",
            "tool_name": self.policy.tool_name,
            "selection": asdict(selection),
            "organization_id": selection.organization_id,
            "documents": documents,
        }

    def _previews(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        remaining = self.policy.max_preview_characters_per_document
        previews: dict[str, str] = {}
        for item in _dict_items(payload.get("contents")):
            if remaining <= 0:
                break
            content_type = str(item.get("type") or "content")
            text = str(item.get("text") or "")
            sample = text[: min(remaining, 1_500)]
            if sample:
                previews[content_type] = sample
                remaining -= len(sample)
        chunk_previews = []
        for item in _dict_items(payload.get("chunks"))[
            : self.policy.max_chunk_previews_per_document
        ]:
            text = str(item.get("text") or "")
            chunk_previews.append(
                {
                    "chunk_index": item.get("chunk_index"),
                    "embedding_id": item.get("embedding_id"),
                    "content_type": item.get("content_type"),
                    "text": text[:1_000],
                }
            )
        return previews, chunk_previews

    @staticmethod
    def _enrich_report_context(
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        evidence: dict[str, Any],
        selection: ReportFileSelection,
    ) -> tuple[ExecutionSpec, DataCorpusPackage]:
        resolved_spec = deepcopy(spec)
        resolved_corpus = deepcopy(corpus_package)
        documents = _dict_items(evidence.get("documents"))
        document_ids = [str(item["document_id"]) for item in documents]
        source_refs = [str(item["source_ref"]) for item in documents]

        constraints = (
            deepcopy(resolved_spec.constraints)
            if isinstance(resolved_spec.constraints, dict)
            else {}
        )
        selected = constraints.get("selected_data_context")
        selected = deepcopy(selected) if isinstance(selected, dict) else {}
        selected["selected_documents"] = document_ids
        selected["selected_sources"] = source_refs
        constraints["selected_data_context"] = selected
        report_selection = constraints.get("report_data_selection")
        report_selection = (
            deepcopy(report_selection) if isinstance(report_selection, dict) else {}
        )
        report_selection.update(
            {
                "selector": {
                    "type": selection.selector_name,
                    "value": selection.selector_value,
                },
                "organization_id": selection.organization_id,
                "bucket": selection.bucket,
                "match_mode": selection.match_mode,
                "initial_mode": selection.mode,
                "candidate_policy": selection.candidate_policy,
                "selection_source": selection.selection_source,
                "discovery": (
                    {
                        "tool_name": selection.discovery_tool,
                        "query": selection.discovery_query,
                        "score": selection.discovery_score,
                    }
                    if selection.selection_source == "chunk_search"
                    else None
                ),
                "resolved_documents": [
                    {
                        "document_id": item["document_id"],
                        "organization_id": item.get("organization_id"),
                        "source_ref": item["source_ref"],
                        "artifact_ref": item["artifact_ref"],
                    }
                    for item in documents
                ],
            }
        )
        constraints["report_data_selection"] = report_selection
        resolved_spec.constraints = constraints

        resolved_corpus.sources = list(
            dict.fromkeys([*resolved_corpus.sources, *source_refs])
        )
        metadata = deepcopy(resolved_corpus.metadata)
        metadata["ingested_documents"] = documents
        metadata["ingested_evidence_package"] = evidence
        resolved_corpus.metadata = metadata
        return resolved_spec, resolved_corpus

    @classmethod
    def extract_selection(
        cls,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        *,
        query_text: str | None,
        policy: ReportCorpusPolicy,
    ) -> ReportFileSelection | None:
        constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
        raw = constraints.get("report_data_selection")
        raw = raw if isinstance(raw, dict) else {}
        selector_name, selector_value = cls._selector_from_mapping(raw)
        organization_id = _optional_string(
            raw.get("organization_id")
            or corpus_package.metadata.get("organization_id")
        )
        bucket = _optional_string(raw.get("bucket"))

        if selector_name is None:
            for source in corpus_package.sources:
                parsed = cls._selector_from_source(str(source))
                if parsed is not None:
                    selector_name, selector_value, source_organization = parsed
                    organization_id = organization_id or source_organization
                    break
        if selector_name is None:
            texts = [
                *(str(item) for item in spec.data_requirements),
                str(query_text or ""),
                str(spec.objective or ""),
            ]
            for text in texts:
                parsed = cls._selector_from_text(text)
                if parsed is not None:
                    selector_name, selector_value = parsed
                    break
        if selector_name is None or selector_value is None:
            return None

        match_mode = str(raw.get("match_mode") or "exact").lower()
        if match_mode not in {"exact", "contains"}:
            raise ReportCorpusResolutionError(
                "report_file_selector_invalid",
                "match_mode must be exact or contains.",
            )
        mode = str(
            raw.get("initial_mode") or raw.get("mode") or policy.default_mode
        ).lower()
        if mode not in {"all", "overview", "page"}:
            raise ReportCorpusResolutionError(
                "report_file_selector_invalid",
                "mode must be all, overview, or page.",
            )
        candidate_policy = str(
            raw.get("candidate_policy") or "require_selection"
        ).lower()
        if candidate_policy not in {"require_selection", "all_matches"}:
            raise ReportCorpusResolutionError(
                "report_file_selector_invalid",
                "candidate_policy must be require_selection or all_matches.",
            )
        max_documents = min(
            100,
            _positive_int(
                raw.get("max_documents"),
                policy.default_max_documents,
            ),
        )
        chunk_start = max(0, _int_value(raw.get("chunk_start"), 0))
        chunk_limit = (
            min(10_000, _positive_int(raw.get("chunk_limit"), 1))
            if raw.get("chunk_limit") is not None
            else None
        )
        return ReportFileSelection(
            selector_name=selector_name,
            selector_value=selector_value,
            organization_id=organization_id,
            bucket=bucket,
            match_mode=match_mode,
            mode=mode,
            chunk_start=chunk_start,
            chunk_limit=chunk_limit,
            candidate_policy=candidate_policy,
            max_documents=max_documents,
        )

    @staticmethod
    def _selector_from_mapping(
        raw: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        selector = raw.get("selector")
        if isinstance(selector, dict):
            name = str(selector.get("type") or selector.get("name") or "").strip()
            value = _optional_string(selector.get("value"))
            if name in _SELECTOR_NAMES and value:
                return name, value
        for name in _SELECTOR_NAMES:
            value = _optional_string(raw.get(name))
            if value:
                return name, value
        return None, None

    @staticmethod
    def _selector_from_source(
        source: str,
    ) -> tuple[str, str, str | None] | None:
        if not source.startswith("corpus://"):
            return None
        path = source.removeprefix("corpus://").strip("/")
        if not path:
            return None
        parts = path.split("/")
        if len(parts) >= 2:
            return "document_id", parts[-1], parts[-2]
        return "document_id", parts[0], None

    @staticmethod
    def _selector_from_text(text: str) -> tuple[str, str] | None:
        if not text.strip():
            return None
        if match := _DOCUMENT_ID_PATTERN.search(text):
            return "document_id", match.group(1).strip()
        if match := _OBJECT_KEY_PATTERN.search(text):
            return "object_key", match.group(1).strip().rstrip(".")
        if match := _QUOTED_FILE_PATTERN.search(text):
            return "file_name", match.group(1).strip()
        if match := _CONTEXT_FILE_PATTERN.search(text):
            return "file_name", match.group(1).strip()
        if match := _FILE_PATTERN.search(text):
            return "file_name", match.group(1).strip()
        return None


def unwrap_ingested_data_result(result: Any) -> dict[str, Any]:
    """Unwrap the Methods-Hub database-method envelope."""

    if not isinstance(result, dict):
        raise ReportCorpusResolutionError(
            "corpus_ingested_data_result_invalid",
            "Method Hub returned a non-object ingested-data result.",
        )
    nested = result.get("result")
    if isinstance(nested, dict) and (
        result.get("method") == CORPUS_GET_FILE_INGESTED_DATA
        or "document" in nested
        or "matches" in nested
    ):
        return nested
    return result


def ingested_data_has_content(result: Any) -> bool:
    """Return whether an ingested-data payload contains reportable evidence."""

    try:
        payload = unwrap_ingested_data_result(result)
    except ReportCorpusResolutionError:
        return False
    return bool(_dict_items(payload.get("contents")) or _dict_items(payload.get("chunks")))


def ingested_data_analysis_records(result: Any) -> list[dict[str, Any]]:
    """Flatten contents and chunks into analysis-ready evidence records."""

    payload = unwrap_ingested_data_result(result)
    document = payload.get("document")
    document = document if isinstance(document, dict) else {}
    identity = {
        "document_id": document.get("document_id"),
        "organization_id": document.get("organization_id"),
        "file_name": document.get("file_name"),
        "object_key": document.get("object_key"),
        "source_uri": document.get("source_uri"),
    }
    records = []
    for item in _dict_items(payload.get("contents")):
        records.append(
            {
                **identity,
                "record_kind": "extracted_content",
                "content_id": item.get("content_id"),
                "content_type": item.get("type"),
                "text": item.get("text"),
            }
        )
    for item in _dict_items(payload.get("chunks")):
        records.append(
            {
                **identity,
                "record_kind": "indexed_chunk",
                "chunk_index": item.get("chunk_index"),
                "embedding_id": item.get("embedding_id"),
                "content_type": item.get("content_type"),
                "text": item.get("text"),
                "metadata": deepcopy(item.get("metadata", {})),
            }
        )
    return records


def ingested_document_route(
    step_request: dict[str, Any],
    method_hub: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build an exact Method Hub route for an ingested-document plan step."""

    operation = step_request.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    if str(operation.get("kind") or "") not in _CORPUS_OPERATIONS:
        return None
    tool = next(
        (
            item
            for item in method_hub
            if item.get("tool_name") == CORPUS_GET_FILE_INGESTED_DATA
        ),
        None,
    )
    if tool is None:
        return {
            "route": "unsupported",
            "tool_name": None,
            "arguments": {},
            "reason": (
                "corpus_get_file_ingested_data is required for ingested "
                "document report steps."
            ),
        }
    schema = tool.get("parameters_schema")
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict) or not {
        "document_id",
        "organization_id",
        "mode",
    }.issubset(properties):
        return {
            "route": "unsupported",
            "tool_name": None,
            "arguments": {},
            "reason": "The ingested-data tool schema is incompatible.",
        }
    parameters = operation.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    required_data = step_request.get("required_data")
    required_data = required_data if isinstance(required_data, dict) else {}
    document_ids = [
        str(item) for item in required_data.get("documents", []) if str(item)
    ]
    document_id = _optional_string(parameters.get("document_id"))
    if document_id is None and document_ids:
        document_id = document_ids[0]
    if document_id is None:
        return {
            "route": "unsupported",
            "tool_name": None,
            "arguments": {},
            "reason": "The ingested-document step has no resolved document_id.",
        }
    arguments: dict[str, Any] = {
        "document_id": document_id,
        "mode": str(parameters.get("mode") or "all"),
    }
    for name in ("organization_id", "chunk_start", "chunk_limit"):
        if parameters.get(name) is not None:
            arguments[name] = parameters[name]
    return {
        "route": "existing_tool",
        "tool_name": CORPUS_GET_FILE_INGESTED_DATA,
        "arguments": arguments,
        "reason": "Resolved by the Report Engine ingested-document contract.",
    }


def is_ingested_data_tool(tool_name: Any) -> bool:
    return str(tool_name or "") == CORPUS_GET_FILE_INGESTED_DATA


def _unwrap_discovery_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ReportCorpusResolutionError(
            "corpus_document_discovery_result_invalid",
            "Corpus discovery returned a non-object result.",
        )
    current = result
    for _ in range(4):
        if any(
            isinstance(current.get(name), list)
            for name in ("results", "matches", "chunks", "contexts")
        ):
            return current
        nested = current.get("result")
        if isinstance(nested, dict):
            current = nested
            continue
        if isinstance(nested, list):
            return {"results": nested}
        break
    return current


def _discovery_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for name in ("results", "matches", "chunks", "contexts"):
        values = payload.get(name)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    if _candidate_document_id(payload):
        return [payload]
    return []


def _aggregate_discovery_candidates(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for hit in _discovery_hits(payload):
        document = _candidate_document(hit)
        document_id = _candidate_document_id(hit)
        if not document_id:
            continue
        score = _candidate_score(hit)
        current = grouped.setdefault(
            document_id,
            {
                "document_id": document_id,
                "organization_id": _optional_string(
                    document.get("organization_id")
                ),
                "file_name": _optional_string(document.get("file_name")),
                "object_key": _optional_string(document.get("object_key")),
                "source_uri": _optional_string(document.get("source_uri")),
                "score": None,
                "mean_score": None,
                "matched_chunk_count": 0,
                "_score_total": 0.0,
                "_score_count": 0,
            },
        )
        current["matched_chunk_count"] += 1
        for name in (
            "organization_id",
            "file_name",
            "object_key",
            "source_uri",
        ):
            current[name] = current.get(name) or _optional_string(document.get(name))
        if score is not None:
            current["score"] = (
                score
                if current["score"] is None
                else max(float(current["score"]), score)
            )
            current["_score_total"] += score
            current["_score_count"] += 1

    candidates = []
    for candidate in grouped.values():
        score_count = int(candidate.pop("_score_count"))
        score_total = float(candidate.pop("_score_total"))
        candidate["mean_score"] = (
            score_total / score_count if score_count else None
        )
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            item.get("score") is not None,
            float(item.get("score") or 0.0),
            float(item.get("mean_score") or 0.0),
            int(item.get("matched_chunk_count") or 0),
            str(item.get("document_id") or ""),
        ),
        reverse=True,
    )
    return candidates


def _candidate_document(hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document")
    if isinstance(document, dict):
        return document
    metadata = hit.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    nested_document = metadata.get("document")
    if isinstance(nested_document, dict):
        return nested_document
    chunk = hit.get("chunk")
    if isinstance(chunk, dict):
        chunk_metadata = chunk.get("metadata")
        if isinstance(chunk_metadata, dict):
            nested_document = chunk_metadata.get("document")
            if isinstance(nested_document, dict):
                return nested_document
            return chunk_metadata
    return {**metadata, **hit}


def _candidate_document_id(hit: dict[str, Any]) -> str | None:
    document = _candidate_document(hit)
    return _optional_string(
        document.get("document_id")
        or hit.get("document_id")
        or document.get("id")
    )


def _candidate_score(hit: dict[str, Any]) -> float | None:
    direct = (
        hit.get("score")
        if hit.get("score") is not None
        else hit.get("relevance_score")
    )
    score = _optional_float(direct)
    if score is not None:
        return score
    scores = hit.get("scores")
    if isinstance(scores, dict):
        values = [
            value
            for value in (_optional_float(item) for item in scores.values())
            if value is not None
        ]
        if values:
            return max(values)
    return None


def _report_request_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(
        r"(?ims)^##\s+User Request\s*$\s*(.+?)(?=^##\s+|\Z)",
        text,
    )
    if match:
        text = match.group(1).strip()
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text).strip()
    return text or None


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_string(value: Any) -> str | None:
    rendered = str(value or "").strip()
    return rendered or None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_float(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, default: int) -> int:
    return max(1, _int_value(value, default))
