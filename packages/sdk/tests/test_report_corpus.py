from __future__ import annotations

import base64
import gzip
import json
import os
import unittest
from unittest.mock import patch

from data_intelligence_sdk.core.types import DataCorpusPackage, ExecutionSpec
from data_intelligence_sdk.engines.reporting.corpus import (
    CORPUS_GET_FILE_INGESTED_DATA,
    CORPUS_HYBRID_SEARCH,
    CORPUS_KEYWORD_SEARCH_CONTENTS,
    CORPUS_MATERIALIZE_OPERATION,
    CORPUS_MATERIALIZED_ROUTE,
    ReportCorpusPolicy,
    ReportCorpusResolutionError,
    ReportCorpusResolver,
    ingested_data_analysis_records,
    ingested_document_route,
    unwrap_ingested_data_result,
)
from data_intelligence_sdk.engines.reporting.execution import RouterAgent, ToolExecutor
from data_intelligence_sdk.engines.reporting.planning import (
    PlanAgent,
    TemplateAgent,
    TemplatePool,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition


def _tool_definition() -> MCPToolDefinition:
    return MCPToolDefinition(
        name=CORPUS_GET_FILE_INGESTED_DATA,
        description="Get ingested data for one corpus document.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file_name": {"type": ["string", "null"]},
                "document_id": {"type": ["string", "null"]},
                "organization_id": {"type": ["string", "null"]},
                "bucket": {"type": ["string", "null"]},
                "object_key": {"type": ["string", "null"]},
                "match_mode": {"type": "string"},
                "mode": {"type": "string"},
                "chunk_start": {"type": "integer"},
                "chunk_limit": {"type": ["integer", "null"]},
            },
        },
    )


def _discovery_tool_definition(name: str) -> MCPToolDefinition:
    return MCPToolDefinition(
        name=name,
        description="Search ingested corpus chunks and return document metadata.",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "organization_id": {"type": ["string", "null"]},
            },
            "required": ["query"],
        },
    )


def _document_payload(document_id: str, file_name: str = "report.pdf") -> dict:
    return {
        "method": CORPUS_GET_FILE_INGESTED_DATA,
        "result": {
            "error": None,
            "matches": [],
            "document": {
                "document_id": document_id,
                "organization_id": "test-org",
                "file_name": file_name,
                "bucket": "test-org",
                "object_key": file_name,
                "source_uri": f"s3://test-org/{file_name}",
                "current_status": "indexed",
                "size_bytes": 100,
            },
            "processing_run": {"run_id": "run-1", "status": "completed"},
            "content_summary": {
                "content_types": ["main_text", "tables"],
                "total_chunks": 1,
                "returned_chunks": 1,
                "has_more": False,
            },
            "contents": [
                {
                    "content_id": "content-1",
                    "type": "main_text",
                    "text": "A substantive report finding.",
                }
            ],
            "chunks": [
                {
                    "chunk_index": 0,
                    "embedding_id": "embedding-1",
                    "content_type": "text",
                    "text": "A substantive report finding.",
                    "metadata": {"position": {"chunk_index": 0}},
                }
            ],
        },
        "metadata": {"implementation": "corpus-service"},
    }


def _compressed_document_payload(
    document_id: str,
    file_name: str = "report.pdf",
) -> dict:
    payload = _document_payload(document_id, file_name)
    result = payload["result"]
    compressed_fields = {
        "contents": result["contents"],
        "chunks": result["chunks"],
    }
    encoded = gzip.compress(
        json.dumps(
            compressed_fields,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        mtime=0,
    )
    result["contents"] = []
    result["chunks"] = []
    result["compressed_payload"] = base64.b64encode(encoded).decode("ascii")
    result["compression"] = {
        "enabled": True,
        "algorithm": "gzip",
        "encoding": "base64",
        "json_fields": ["contents", "chunks"],
        "compressed_bytes": len(encoded),
    }
    return payload


class _FakeMCPClient:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        response = self.response
        if isinstance(response, dict) and name in response:
            response = response[name]
        if callable(response):
            return response(arguments)
        return response


def _runtime(
    client: _FakeMCPClient,
    *extra_tools: MCPToolDefinition,
) -> EngineRuntimeContext:
    return EngineRuntimeContext(
        mcp_client=client,
        mcp_tools=(_tool_definition(), *extra_tools),
    )


class ReportCorpusResolverTests(unittest.TestCase):
    def test_compressed_ingested_payload_is_decoded_before_validation(self):
        client = _FakeMCPClient(_compressed_document_payload("doc-compressed"))
        resolver = ReportCorpusResolver()
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report from report.pdf",
            constraints={
                "report_data_selection": {
                    "selector": {"type": "file_name", "value": "report.pdf"},
                    "match_mode": "exact",
                }
            },
        )

        resolved = resolver.resolve(
            spec,
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(client),
            query_text=spec.objective,
        )

        document = resolved.evidence_package["documents"][0]
        self.assertTrue(document["content_profile"]["has_text"])
        self.assertEqual(len(resolved.materializations["doc-compressed"]["chunks"]), 1)
        self.assertEqual(
            len(ingested_data_analysis_records(client.response)),
            2,
        )

    def test_invalid_compressed_payload_has_explicit_error(self):
        payload = _compressed_document_payload("doc-corrupt")
        payload["result"]["compressed_payload"] = "not-valid-base64!"

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            unwrap_ingested_data_result(payload)

        self.assertEqual(
            raised.exception.code,
            "corpus_ingested_data_compression_invalid",
        )

    def test_decompressed_payload_honors_configured_safety_limit(self):
        payload = _compressed_document_payload("doc-too-large")

        with patch.dict(
            os.environ,
            {"REPORT_INGESTED_DATA_MAX_DECOMPRESSED_PAYLOAD_BYTES": "16"},
        ):
            with self.assertRaises(ReportCorpusResolutionError) as raised:
                unwrap_ingested_data_result(payload)

        self.assertEqual(
            raised.exception.code,
            "corpus_ingested_data_compression_limit_exceeded",
        )

    def test_exact_file_selector_hydrates_before_planning(self):
        client = _FakeMCPClient(_document_payload("doc-001"))
        resolver = ReportCorpusResolver()
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report from report.pdf",
            constraints={
                "report_data_selection": {
                    "selector": {"type": "file_name", "value": "report.pdf"},
                    "match_mode": "exact",
                }
            },
        )

        resolved = resolver.resolve(
            spec,
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(client),
            query_text=spec.objective,
        )

        self.assertEqual(
            client.calls,
            [
                (
                    CORPUS_GET_FILE_INGESTED_DATA,
                    {
                        "file_name": "report.pdf",
                        "mode": "overview",
                        "organization_id": "test-org",
                        "match_mode": "exact",
                    },
                ),
                (
                    CORPUS_GET_FILE_INGESTED_DATA,
                    {
                        "document_id": "doc-001",
                        "mode": "all",
                        "organization_id": "test-org",
                    },
                ),
            ],
        )
        self.assertEqual(
            resolved.spec.constraints["selected_data_context"][
                "selected_documents"
            ],
            ["doc-001"],
        )
        self.assertEqual(
            resolved.corpus_package.sources,
            ["corpus://test-org/doc-001"],
        )
        document = resolved.evidence_package["documents"][0]
        self.assertTrue(document["content_profile"]["has_tables"])
        self.assertIn("main_text", document["previews"])
        self.assertIn("doc-001", resolved.materializations)

    def test_incomplete_full_materialization_fails_before_planning(self):
        def response(arguments):
            payload = _document_payload("doc-incomplete")
            payload["result"]["content_summary"].update(
                {
                    "total_chunks": 2,
                    "returned_chunks": 1,
                    "has_more": True,
                    "next_chunk_start": 1,
                }
            )
            return payload

        resolver = ReportCorpusResolver()
        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Create a report from report.pdf",
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(_FakeMCPClient(response)),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_materialization_incomplete",
        )

    def test_large_document_materializes_all_chunk_pages(self):
        def response(arguments):
            payload = _document_payload("doc-large")
            result = payload["result"]
            if arguments["mode"] == "overview":
                result["content_summary"].update(
                    {
                        "total_chunks": 3,
                        "returned_chunks": 1,
                        "has_more": True,
                        "next_chunk_start": 1,
                    }
                )
                return payload
            start = arguments["chunk_start"]
            indexes = [0, 1] if start == 0 else [2]
            result["contents"] = []
            result["chunks"] = [
                {
                    "chunk_index": index,
                    "embedding_id": f"embedding-{index}",
                    "content_type": "text",
                    "text": f"Chunk {index}",
                    "metadata": {"position": {"chunk_index": index}},
                }
                for index in indexes
            ]
            result["content_summary"].update(
                {
                    "total_chunks": 3,
                    "returned_chunks": len(indexes),
                    "has_more": start == 0,
                    "next_chunk_start": 2 if start == 0 else None,
                }
            )
            return payload

        client = _FakeMCPClient(response)
        resolver = ReportCorpusResolver(
            ReportCorpusPolicy(
                all_mode_max_chunks=1,
                materialization_page_size=2,
            )
        )
        resolved = resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective="Create a report from report.pdf",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(client),
        )

        self.assertEqual(
            [arguments["mode"] for _, arguments in client.calls],
            ["overview", "page", "page"],
        )
        self.assertEqual(
            len(resolved.materializations["doc-large"]["chunks"]),
            3,
        )
        self.assertFalse(
            resolved.materializations["doc-large"]["content_summary"]["has_more"]
        )

    def test_markdown_document_id_is_used_without_semantic_search(self):
        resolver = ReportCorpusResolver()
        selection = resolver.extract_selection(
            ExecutionSpec(
                intent="report",
                objective="Use document_id: doc-001 for the report.",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            query_text=None,
            policy=resolver.policy,
        )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.selector_name, "document_id")
        self.assertEqual(selection.selector_value, "doc-001")

    def test_bare_file_name_does_not_capture_surrounding_request_text(self):
        resolver = ReportCorpusResolver()
        selection = resolver.extract_selection(
            ExecutionSpec(
                intent="report",
                objective=(
                    "Create a report from "
                    "16ae3cff-1bba-495c-9ba1-f63ff80e7570.pdf."
                ),
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            query_text=None,
            policy=resolver.policy,
        )

        self.assertIsNotNone(selection)
        self.assertEqual(
            selection.selector_value,
            "16ae3cff-1bba-495c-9ba1-f63ff80e7570.pdf",
        )

    def test_file_name_with_spaces_is_extracted_after_from(self):
        resolver = ReportCorpusResolver()
        selection = resolver.extract_selection(
            ExecutionSpec(
                intent="report",
                objective="Create a report from 25.07 Template.pdf.",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            query_text=None,
            policy=resolver.policy,
        )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.selector_value, "25.07 Template.pdf")

    def test_missing_selector_fails_before_plan(self):
        resolver = ReportCorpusResolver()

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Create a report about pulmonary hypertension.",
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(_FakeMCPClient({})),
                query_text=None,
            )

        self.assertEqual(raised.exception.code, "report_file_selector_missing")

    def test_chunk_search_discovers_deduplicates_and_hydrates_document(self):
        search_payload = {
            "method": CORPUS_HYBRID_SEARCH,
            "result": {
                "results": [
                    {
                        "document": {
                            "document_id": "doc-001",
                            "organization_id": "test-org",
                            "file_name": "action-plan.pdf",
                        },
                        "text": "Pulmonary hypertension action plan.",
                        "score": 0.91,
                    },
                    {
                        "document": {
                            "document_id": "doc-001",
                            "organization_id": "test-org",
                            "file_name": "action-plan.pdf",
                        },
                        "text": "Clinical recommendations.",
                        "score": 0.82,
                    },
                    {
                        "document": {
                            "document_id": "doc-002",
                            "organization_id": "test-org",
                            "file_name": "other-report.pdf",
                        },
                        "text": "A less relevant report.",
                        "score": 0.60,
                    },
                ]
            },
        }
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: search_payload,
                CORPUS_GET_FILE_INGESTED_DATA: _document_payload(
                    "doc-001",
                    "action-plan.pdf",
                ),
            }
        )
        resolver = ReportCorpusResolver()
        markdown = """# Interactive Execution Spec

## User Request

Create a report about the pulmonary hypertension action plan.

## Intent

Create a report.
"""

        resolved = resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective=markdown,
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(
                client,
                _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
            ),
            query_text=markdown,
        )

        self.assertEqual(
            client.calls,
            [
                (
                    CORPUS_HYBRID_SEARCH,
                    {
                        "query": (
                            "Create a report about the pulmonary hypertension "
                            "action plan."
                        ),
                        "top_k": 10,
                        "organization_id": "test-org",
                    },
                ),
                (
                    CORPUS_GET_FILE_INGESTED_DATA,
                    {
                        "document_id": "doc-001",
                        "mode": "overview",
                        "organization_id": "test-org",
                    },
                ),
                (
                    CORPUS_GET_FILE_INGESTED_DATA,
                    {
                        "document_id": "doc-001",
                        "mode": "all",
                        "organization_id": "test-org",
                    },
                ),
            ],
        )
        selection = resolved.evidence_package["selection"]
        self.assertEqual(selection["selector_name"], "document_id")
        self.assertEqual(selection["selector_value"], "doc-001")
        self.assertEqual(selection["selection_source"], "chunk_search")
        self.assertEqual(selection["discovery_tool"], CORPUS_HYBRID_SEARCH)
        self.assertEqual(selection["discovery_score"], 0.91)
        report_selection = resolved.spec.constraints["report_data_selection"]
        self.assertEqual(report_selection["selection_source"], "chunk_search")
        self.assertEqual(
            report_selection["discovery"],
            {
                "tool_name": CORPUS_HYBRID_SEARCH,
                "query": (
                    "Create a report about the pulmonary hypertension "
                    "action plan."
                ),
                "score": 0.91,
            },
        )

    def test_exact_file_stem_match_is_not_rejected_by_score_calibration(self):
        def hydrate(arguments):
            document_id = arguments["document_id"]
            return _document_payload(
                document_id,
                "sample-monthly-operations-report-2026.pdf",
            )

        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {
                    "result": {
                        "results": [
                            {
                                "document": {
                                    "document_id": "doc-target",
                                    "organization_id": "test-org",
                                    "file_name": (
                                        "sample-monthly-operations-report-2026.pdf"
                                    ),
                                },
                                "score": 0.30,
                            },
                            {
                                "document": {
                                    "document_id": "doc-unrelated",
                                    "organization_id": "test-org",
                                    "file_name": "unrelated-report.pdf",
                                },
                                "score": 0.30,
                            },
                        ]
                    }
                },
                CORPUS_GET_FILE_INGESTED_DATA: hydrate,
            }
        )

        resolved = ReportCorpusResolver().resolve(
            ExecutionSpec(
                intent="report",
                objective=(
                    "Create an evidence-based report about "
                    "sample-monthly-operations-report-2026."
                ),
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(
                client,
                _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
            ),
        )

        selection = resolved.evidence_package["selection"]
        self.assertEqual(selection["selector_value"], "doc-target")
        self.assertEqual(
            [item["document_id"] for item in selection["discovered_documents"]],
            ["doc-target"],
        )
        self.assertEqual(
            selection["discovered_documents"][0]["identity_match"],
            {
                "field": "file_name",
                "value": "sample-monthly-operations-report-2026.pdf",
                "match": "stem",
            },
        )

    def test_low_score_without_identity_evidence_remains_rejected(self):
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {
                    "result": {
                        "results": [
                            {
                                "document": {
                                    "document_id": "doc-a",
                                    "file_name": "unrelated-a.pdf",
                                },
                                "score": 0.30,
                            },
                            {
                                "document": {
                                    "document_id": "doc-b",
                                    "file_name": "unrelated-b.pdf",
                                },
                                "score": 0.29,
                            },
                        ]
                    }
                },
            }
        )

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            ReportCorpusResolver().resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Create a report about a missing operations source.",
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(
                    client,
                    _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_discovery_low_confidence",
        )

    def test_explicit_selector_bypasses_available_discovery_tool(self):
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: AssertionError(
                    "Discovery must not run for an explicit selector."
                ),
                CORPUS_GET_FILE_INGESTED_DATA: _document_payload("doc-001"),
            }
        )
        resolver = ReportCorpusResolver()

        resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective="Create a report from document_id: doc-001.",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(
                client,
                _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
            ),
        )

        self.assertEqual(
            [name for name, _ in client.calls],
            [
                CORPUS_GET_FILE_INGESTED_DATA,
                CORPUS_GET_FILE_INGESTED_DATA,
            ],
        )

    def test_near_tied_chunk_search_hydrates_all_relevant_documents(self):
        def hydrate(arguments):
            document_id = arguments["document_id"]
            return _document_payload(document_id, f"{document_id}.pdf")

        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {
                    "result": {
                        "results": [
                            {
                                "document": {
                                    "document_id": "doc-001",
                                    "organization_id": "test-org",
                                    "file_name": "report-a.pdf",
                                },
                                "score": 0.80,
                            },
                            {
                                "document": {
                                    "document_id": "doc-002",
                                    "organization_id": "test-org",
                                    "file_name": "report-b.pdf",
                                },
                                "score": 0.77,
                            },
                        ]
                    }
                },
                CORPUS_GET_FILE_INGESTED_DATA: hydrate,
            }
        )
        resolver = ReportCorpusResolver()

        resolved = resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective="Compare pulmonary hypertension recommendations.",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(
                client,
                _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
            ),
        )

        self.assertEqual(
            [item["document_id"] for item in resolved.evidence_package["documents"]],
            ["doc-001", "doc-002"],
        )
        self.assertEqual(
            [name for name, _ in client.calls],
            [
                CORPUS_HYBRID_SEARCH,
                CORPUS_GET_FILE_INGESTED_DATA,
                CORPUS_GET_FILE_INGESTED_DATA,
                CORPUS_GET_FILE_INGESTED_DATA,
                CORPUS_GET_FILE_INGESTED_DATA,
            ],
        )
        self.assertEqual(
            resolved.evidence_package["selection"]["candidate_policy"],
            "all_matches",
        )

    def test_strict_discovery_policy_requires_selection_for_near_tie(self):
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {
                    "result": {
                        "results": [
                            {
                                "document": {
                                    "document_id": "doc-001",
                                    "organization_id": "test-org",
                                },
                                "score": 0.80,
                            },
                            {
                                "document": {
                                    "document_id": "doc-002",
                                    "organization_id": "test-org",
                                },
                                "score": 0.77,
                            },
                        ]
                    }
                },
            }
        )
        resolver = ReportCorpusResolver()

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Compare pulmonary hypertension recommendations.",
                    constraints={
                        "report_data_discovery": {
                            "candidate_policy": "require_selection",
                        }
                    },
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(
                    client,
                    _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_selection_required",
        )

    def test_all_relevant_discovery_honors_document_limit(self):
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {
                    "result": {
                        "results": [
                            {
                                "document": {"document_id": "doc-001"},
                                "score": 0.90,
                            },
                            {
                                "document": {"document_id": "doc-002"},
                                "score": 0.89,
                            },
                        ]
                    }
                },
            }
        )
        resolver = ReportCorpusResolver()

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Report the matching audit documents.",
                    constraints={
                        "report_data_discovery": {
                            "max_documents": 1,
                        }
                    },
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(
                    client,
                    _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_candidate_limit_exceeded",
        )

    def test_discovery_falls_back_when_preferred_tool_fails(self):
        def fail_hybrid(arguments):
            del arguments
            raise RuntimeError("embedding provider unavailable")

        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: fail_hybrid,
                CORPUS_KEYWORD_SEARCH_CONTENTS: {
                    "method": CORPUS_KEYWORD_SEARCH_CONTENTS,
                    "result": {
                        "results": [
                            {
                                "document": {
                                    "document_id": "doc-003",
                                    "organization_id": "test-org",
                                    "file_name": "keyword-match.pdf",
                                },
                                "score": 1.0,
                            }
                        ]
                    },
                },
                CORPUS_GET_FILE_INGESTED_DATA: _document_payload(
                    "doc-003",
                    "keyword-match.pdf",
                ),
            }
        )
        resolver = ReportCorpusResolver()

        resolved = resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective="Report the national audit action plan.",
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(
                client,
                _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
                _discovery_tool_definition(CORPUS_KEYWORD_SEARCH_CONTENTS),
            ),
        )

        self.assertEqual(
            [name for name, _ in client.calls],
            [
                CORPUS_HYBRID_SEARCH,
                CORPUS_KEYWORD_SEARCH_CONTENTS,
                CORPUS_GET_FILE_INGESTED_DATA,
                CORPUS_GET_FILE_INGESTED_DATA,
            ],
        )
        self.assertEqual(
            resolved.evidence_package["selection"]["discovery_tool"],
            CORPUS_KEYWORD_SEARCH_CONTENTS,
        )

    def test_discovery_no_matches_is_explicit(self):
        client = _FakeMCPClient(
            {
                CORPUS_HYBRID_SEARCH: {"result": {"results": []}},
            }
        )
        resolver = ReportCorpusResolver()

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Report an unknown clinical programme.",
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(
                    client,
                    _discovery_tool_definition(CORPUS_HYBRID_SEARCH),
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_discovery_no_matches",
        )

    def test_multiple_matches_remain_explicit_candidates(self):
        candidates = [
            {
                "document_id": "doc-001",
                "organization_id": "test-org",
                "file_name": "report-a.pdf",
            },
            {
                "document_id": "doc-002",
                "organization_id": "test-org",
                "file_name": "report-b.pdf",
            },
        ]
        client = _FakeMCPClient(
            {
                "method": CORPUS_GET_FILE_INGESTED_DATA,
                "result": {
                    "error": "multiple_documents_matched",
                    "message": "Multiple documents matched.",
                    "matches": candidates,
                    "document": None,
                    "contents": [],
                    "chunks": [],
                },
            }
        )
        resolver = ReportCorpusResolver()

        with self.assertRaises(ReportCorpusResolutionError) as raised:
            resolver.resolve(
                ExecutionSpec(
                    intent="report",
                    objective="Create a report.",
                    constraints={
                        "report_data_selection": {
                            "file_name": "report",
                            "match_mode": "contains",
                        }
                    },
                ),
                DataCorpusPackage(metadata={"organization_id": "test-org"}),
                _runtime(client),
            )

        self.assertEqual(
            raised.exception.code,
            "ingested_document_selection_required",
        )
        self.assertEqual(raised.exception.details["candidates"], candidates)
        self.assertEqual(len(client.calls), 1)

    def test_all_matches_are_hydrated_by_document_id(self):
        candidates = [
            {
                "document_id": "doc-001",
                "organization_id": "test-org",
                "file_name": "report-a.pdf",
            },
            {
                "document_id": "doc-002",
                "organization_id": "test-org",
                "file_name": "report-b.pdf",
            },
        ]

        def response(arguments):
            document_id = arguments.get("document_id")
            if document_id:
                return _document_payload(
                    document_id,
                    f"{document_id}.pdf",
                )
            return {
                "method": CORPUS_GET_FILE_INGESTED_DATA,
                "result": {
                    "error": "multiple_documents_matched",
                    "matches": candidates,
                    "document": None,
                    "contents": [],
                    "chunks": [],
                },
            }

        client = _FakeMCPClient(response)
        resolver = ReportCorpusResolver()
        resolved = resolver.resolve(
            ExecutionSpec(
                intent="report",
                objective="Create a combined report.",
                constraints={
                    "report_data_selection": {
                        "file_name": "report",
                        "match_mode": "contains",
                        "candidate_policy": "all_matches",
                    }
                },
            ),
            DataCorpusPackage(metadata={"organization_id": "test-org"}),
            _runtime(client),
        )

        self.assertEqual(
            [
                item["document_id"]
                for item in resolved.evidence_package["documents"]
            ],
            ["doc-001", "doc-002"],
        )
        self.assertEqual(
            [call[1].get("document_id") for call in client.calls[1:]],
            ["doc-001", "doc-002", "doc-001", "doc-002"],
        )


class ReportCorpusExecutionTests(unittest.TestCase):
    def test_plan_normalizes_declared_source_class_to_selected_document(self):
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report.",
            constraints={
                "selected_data_context": {
                    "selected_documents": ["doc-001"],
                    "selected_sources": ["corpus://test-org/doc-001"],
                }
            },
        )
        corpus = DataCorpusPackage(
            sources=["corpus://test-org/doc-001"],
            metadata={
                "ingested_documents": [
                    {
                        "document_id": "doc-001",
                        "organization_id": "test-org",
                        "source_ref": "corpus://test-org/doc-001",
                    }
                ]
            },
        )
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "source-step",
                        "operation": {
                            "kind": "source_operation",
                        },
                        "required_data": {},
                        "outputs": [
                            {
                                "name": "content",
                                "type": "object",
                                "shape": "record",
                            }
                        ],
                    }
                ]
            },
            spec,
            corpus,
            None,
            [],
        )

        self.assertEqual(len(plan["steps"]), 1)
        step = plan["steps"][0]
        self.assertEqual(step["operation"]["kind"], CORPUS_MATERIALIZE_OPERATION)
        self.assertEqual(step["required_data"]["documents"], ["doc-001"])
        self.assertEqual(step["outputs"][0]["type"], "array")
        self.assertEqual(step["outputs"][0]["shape"], "table")

    def test_plan_binds_llm_materialize_alias_to_selected_document(self):
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report.",
            constraints={
                "selected_data_context": {
                    "selected_documents": ["doc-001"],
                    "selected_sources": ["corpus://test-org/doc-001"],
                }
            },
        )
        corpus = DataCorpusPackage(
            sources=["corpus://test-org/doc-001"],
            metadata={
                "ingested_documents": [
                    {
                        "document_id": "doc-001",
                        "organization_id": "test-org",
                        "source_ref": "corpus://test-org/doc-001",
                    }
                ]
            },
        )
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "load-document",
                        "operation": {"kind": "materialize_document"},
                        "required_data": {"documents": []},
                        "outputs": [
                            {
                                "name": "document-content",
                                "type": "object",
                                "shape": "table",
                            }
                        ],
                    }
                ]
            },
            spec,
            corpus,
            None,
            [],
        )

        step = plan["steps"][0]
        self.assertEqual(step["operation"]["kind"], CORPUS_MATERIALIZE_OPERATION)
        self.assertEqual(step["required_data"]["documents"], ["doc-001"])
        self.assertEqual(step["operation"]["parameters"]["document_ids"], ["doc-001"])
        self.assertEqual(step["outputs"][0]["type"], "array")

    def test_plan_inserts_materialization_before_unbound_analysis(self):
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report.",
            constraints={
                "selected_data_context": {
                    "selected_documents": ["doc-001"],
                    "selected_sources": ["corpus://test-org/doc-001"],
                }
            },
        )
        corpus = DataCorpusPackage(
            sources=["corpus://test-org/doc-001"],
            metadata={
                "ingested_documents": [
                    {
                        "document_id": "doc-001",
                        "organization_id": "test-org",
                        "source_ref": "corpus://test-org/doc-001",
                    }
                ]
            },
        )
        plan = PlanAgent(None)._normalize_plan(
            {
                "steps": [
                    {
                        "step_id": "analyze-content",
                        "operation": {"kind": "extract_metrics"},
                        "required_data": {"documents": []},
                        "outputs": [{"name": "metrics", "shape": "record"}],
                    }
                ]
            },
            spec,
            corpus,
            None,
            [],
        )

        self.assertEqual(len(plan["steps"]), 2)
        source_step, analysis_step = plan["steps"]
        self.assertEqual(
            source_step["operation"]["kind"],
            CORPUS_MATERIALIZE_OPERATION,
        )
        self.assertEqual(analysis_step["depends_on"], [source_step["step_id"]])
        self.assertEqual(
            analysis_step["inputs"][0]["ref"],
            "step-output://materialize-selected-documents/ingested-document-content",
        )

    def test_router_reuses_complete_resolver_materialization(self):
        route = ingested_document_route(
            {
                "step_id": "materialize-doc",
                "required_data": {"documents": []},
                "operation": {
                    "kind": CORPUS_MATERIALIZE_OPERATION,
                    "parameters": {},
                },
            },
            [
                {
                    "tool_name": CORPUS_GET_FILE_INGESTED_DATA,
                    "parameters_schema": _tool_definition().input_schema,
                }
            ],
            selected_document_ids=["doc-001"],
            materialized_document_ids={"doc-001"},
        )

        self.assertEqual(route["route"], CORPUS_MATERIALIZED_ROUTE)
        self.assertEqual(route["arguments"], {"document_ids": ["doc-001"]})

    def test_router_binds_ingested_operation_to_exact_tool(self):
        route = RouterAgent(None).run(
            {
                "step_id": "materialize-doc",
                "description": "Materialize the ingested document.",
                "required_data": {"documents": ["doc-001"]},
                "operation": {
                    "kind": CORPUS_MATERIALIZE_OPERATION,
                    "parameters": {
                        "document_id": "doc-001",
                        "organization_id": "test-org",
                        "mode": "all",
                    },
                },
            },
            [
                {
                    "tool_name": CORPUS_GET_FILE_INGESTED_DATA,
                    "description": "Get ingested document data.",
                    "parameters_schema": _tool_definition().input_schema,
                    "capability_names": [],
                }
            ],
            ["corpus://test-org/doc-001"],
        )

        self.assertEqual(route["route"], "existing_tool")
        self.assertEqual(route["tool_name"], CORPUS_GET_FILE_INGESTED_DATA)
        self.assertEqual(
            route["arguments"],
            {
                "document_id": "doc-001",
                "organization_id": "test-org",
                "mode": "all",
            },
        )

    def test_tool_executor_flattens_contents_and_chunks(self):
        client = _FakeMCPClient(_document_payload("doc-001"))
        result = ToolExecutor().execute_existing(
            {
                "tool_name": CORPUS_GET_FILE_INGESTED_DATA,
                "arguments": {
                    "document_id": "doc-001",
                    "organization_id": "test-org",
                    "mode": "all",
                },
            },
            _runtime(client),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["raw_result"]), 2)
        self.assertEqual(
            {item["record_kind"] for item in result["raw_result"]},
            {"extracted_content", "indexed_chunk"},
        )

    def test_template_preview_uses_ingested_content_not_local_path(self):
        policy = TemplatePool().selection_policy()
        previews = TemplateAgent._content_preview(
            ["corpus://test-org/doc-001"],
            policy,
            [
                {
                    "document_id": "doc-001",
                    "source_ref": "corpus://test-org/doc-001",
                    "file_name": "report.pdf",
                    "content_types": ["main_text", "tables"],
                    "content_profile": {"has_tables": True},
                    "previews": {
                        "main_text": "A substantive report finding.",
                        "tables": "| Metric | Value |",
                    },
                    "artifact_ref": "artifact://run/data/doc-001.json",
                }
            ],
        )

        self.assertEqual(previews[0]["document_id"], "doc-001")
        self.assertIn("A substantive report finding.", previews[0]["content"])
        self.assertTrue(previews[0]["content_profile"]["has_tables"])


if __name__ == "__main__":
    unittest.main()
