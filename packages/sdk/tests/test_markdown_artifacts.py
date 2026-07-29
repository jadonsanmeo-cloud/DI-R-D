import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    EngineOutput,
    FinalResponse,
    IntentAnalysis,
    UserQuery,
)
from data_intelligence_sdk.engines.report import DataScienceAgent, ReportEngine
from data_intelligence_sdk.engines.reporting.utils import (
    _extract_message_content,
    _parse_json_payload,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.artifacts import FilesystemArtifactStore


class _IntentAnalyzer:
    def analyze(self, query, session_context, user_context):
        del query, session_context, user_context
        return IntentAnalysis(intent="report")


class _MarkdownSpecBuilder:
    def build(self, query, intent_analysis):
        del intent_analysis
        return f"# Report\n\n{query.text}"


class _MarkdownReportEngine:
    def run_markdown(self, **kwargs):
        self.runtime = kwargs["runtime"]
        self.user_query = kwargs["user_query"]
        return FinalResponse(answer="generated report", metadata={"engine_name": "report"})


class _BlockResponse:
    def __init__(self, content):
        self.content = content


class _BlockLLM:
    def invoke(self, prompt):
        del prompt
        return _BlockResponse(
            [
                {"type": "reasoning", "content": ""},
                {
                    "type": "text",
                    "text": (
                        "Here is the result:\n"
                        '{"status":"completed","report_content":'
                        '{"executive_summary":"Evidence-backed summary."}}'
                    ),
                },
            ]
        )


class MarkdownArtifactTests(unittest.TestCase):
    def test_markdown_workflow_creates_reopens_and_finalizes_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            report_engine = _MarkdownReportEngine()
            pipeline = DataIntelligencePipeline(
                intent_analyzer=_IntentAnalyzer(),
                spec_builder=object(),
                spec_confirmation=object(),
                engine_registry=object(),
                artifact_store=FilesystemArtifactStore(directory),
                markdown_spec_builder=_MarkdownSpecBuilder(),
                markdown_report_engine=report_engine,
            )

            prepared = pipeline.prepare_markdown(UserQuery(text="Create a report"))
            run_id = prepared.run_artifact_id
            self.assertIsNotNone(run_id)
            self.assertTrue((Path(directory) / str(run_id) / "manifest.json").exists())

            # The Responses API serializes the prepared execution before confirmation.
            prepared.run_artifact = None
            result = pipeline.execute_confirmed_markdown(
                prepared,
                prepared.spec_markdown,
            )

            manifest = json.loads(
                (Path(directory) / str(run_id) / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(result.metadata["artifact_ref"], f"artifact://{run_id}")
            self.assertEqual(report_engine.runtime.run_artifact.run_id, run_id)
            self.assertEqual(report_engine.user_query.text, "Create a report")

    def test_report_engine_keeps_user_goal_separate_from_confirmed_markdown(self):
        engine = ReportEngine()
        engine.run = Mock(
            return_value=EngineOutput(
                engine_name="report",
                answer="generated report",
            )
        )
        confirmed_markdown = (
            "# Interactive Execution Spec\n\n"
            "## User Request\nCreate a report.\n\n"
            "## Execution Instructions\nUse the ingested corpus."
        )

        engine.run_markdown(
            spec_markdown=confirmed_markdown,
            organization_id="organization",
            runtime=EngineRuntimeContext(),
            user_query=UserQuery(text="Create a report about audit findings"),
        )

        engine_input = engine.run.call_args.args[0]
        self.assertEqual(
            engine_input.query.text,
            "Create a report about audit findings",
        )
        self.assertEqual(
            engine_input.spec.objective,
            "Create a report about audit findings",
        )
        self.assertEqual(
            engine_input.spec.constraints["confirmed_spec_markdown"],
            confirmed_markdown,
        )

    def test_content_blocks_and_surrounding_prose_are_parsed(self):
        response = _BlockLLM().invoke(None)
        content = _extract_message_content(response)
        payload = _parse_json_payload(content)

        self.assertEqual(
            payload["report_content"]["executive_summary"],
            "Evidence-backed summary.",
        )

    def test_datascience_summary_uses_executive_summary_when_field_is_omitted(self):
        result = DataScienceAgent(_BlockLLM()).run(
            step={"step_id": "analyze", "description": "Analyze evidence"},
            materialized_result={"profile": {"row_count": 1}},
            upstream_step_results=[],
            template_requirements=[],
            raw_data=[{"text": "Evidence"}],
            user_goal="Explain the evidence",
        )

        self.assertEqual(result["analysis_summary"], "Evidence-backed summary.")


if __name__ == "__main__":
    unittest.main()
