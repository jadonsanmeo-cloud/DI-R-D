import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    IntentAnalysis,
    PreparedExecution,
    PreprocessingStep,
    UserQuery,
    PreparedMarkdownExecution,
)
from data_intelligence_sdk.engines.report import ReportEngine

from data_intelligence_api.http.schemas.responses import CreateResponseRequest, DataCorpusPackageRequest
from data_intelligence_api.application.workflow import (
    DEFAULT_QUERY,
    SourceValidationError,
    build_workflow_invocation,
    default_pipeline_factory,
    prepared_from_payload,
    prepared_to_payload,
    spec_from_payload,
    spec_to_payload,
    resolve_sources,
    markdown_spec_from_payload,
    markdown_spec_to_payload,
)
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    ExampleIntentAnalyzer,
    create_example_pipeline,
)


class BackendWorkflowTests(unittest.TestCase):
    def test_markdown_spec_payload_round_trips_and_rejects_legacy_json(self) -> None:
        markdown = (
            "# Interactive Execution Spec\n\n"
            "## User Request\n\nQuery.\n\n"
            "## Intent\n\nReport.\n\n"
            "## Preparation Guidance\n\nRetrieve.\n\n"
            "## Execution Instructions\n\nRetrieve data.\n\n"
            "## Expected Output\n\nMarkdown report.\n"
        )

        self.assertEqual(
            markdown_spec_from_payload(markdown_spec_to_payload(markdown)),
            markdown,
        )
        with self.assertRaises(ValueError):
            markdown_spec_from_payload({"objective": "legacy"})

    def test_legacy_spec_payload_defaults_to_empty_preprocessing(self) -> None:
        spec = spec_from_payload(
            {"intent": "general", "objective": "Inspect the corpus"}
        )

        self.assertEqual(spec.preprocessing_steps, [])

    def test_prepared_payload_round_trips_intent_analysis(self) -> None:
        step = PreprocessingStep(
            name="understand_request",
            order=1,
            step_type="understand",
            required=True,
        )
        spec = ExecutionSpec(
            intent="reason",
            objective="Inspect the corpus",
            preprocessing_steps=[step],
        )
        prepared = PreparedExecution(
            query=UserQuery(text="Inspect orders"),
            intent="reason",
            corpus_package=DataCorpusPackage(sources=["orders.csv"]),
            spec=spec,
            intent_analysis=IntentAnalysis(
                intent="reason",
                catalog_intent_id="data_query",
                preprocessing_steps=[step],
            ),
        )

        restored_spec = spec_from_payload(spec_to_payload(spec))
        restored = prepared_from_payload(
            prepared_to_payload(prepared),
            restored_spec,
        )

        self.assertEqual(restored_spec.preprocessing_steps, [step])
        self.assertEqual(restored.intent_analysis.catalog_intent_id, "data_query")
        self.assertEqual(restored.intent_analysis.preprocessing_steps, [step])

    def test_default_pipeline_factory_uses_given_model_config(self) -> None:
        sentinel = object()
        with (
            patch.dict(
                "os.environ",
                {"MODEL_CONFIG_PATH": "/app/configs/development/proxy-openrouter.toml"},
                clear=True,
            ),
            patch(
                "data_intelligence_api.application.workflow.create_example_pipeline",
                return_value=sentinel,
            ) as create_pipeline,
        ):
            result = default_pipeline_factory(logger="logger")

        self.assertIs(result, sentinel)
        create_pipeline.assert_called_once_with(
            logger="logger",
            config_manager=ANY,
            use_llm_spec_builder=True,
            intent_service_base_url=None,
            default_organization_id="test-org",
            mcp_client=None,
        )

    def test_default_pipeline_factory_passes_intent_service_base_url(self) -> None:
        sentinel = object()
        with (
            patch.dict(
                "os.environ",
                {"INTENT_SERVICE_BASE_URL": "http://localhost:8005"},
                clear=True,
            ),
            patch(
                "data_intelligence_api.application.workflow.create_example_pipeline",
                return_value=sentinel,
            ) as create_pipeline,
        ):
            result = default_pipeline_factory(logger="logger")

        self.assertIs(result, sentinel)
        create_pipeline.assert_called_once_with(
            logger="logger",
            config_manager=ANY,
            use_llm_spec_builder=True,
            intent_service_base_url="http://localhost:8005",
            default_organization_id="test-org",
            mcp_client=None,
        )

    def test_example_pipeline_uses_intent_service_analyzer_when_configured(self) -> None:
        class FakeEngine:
            name = "fake"

            def can_handle(self, spec):
                return True

        with patch(
            "data_intelligence_api.infrastructure.workflow.pipeline_factory.AxiomIntentServiceAnalyzer"
        ) as analyzer_class:
            pipeline = create_example_pipeline(
                engine=FakeEngine(),
                intent_service_base_url="http://localhost:8005",
            )

        analyzer_class.assert_called_once_with(base_url="http://localhost:8005")
        self.assertIs(pipeline.intent_analyzer, analyzer_class.return_value)

    def test_llm_pipeline_uses_report_engine_for_markdown_execution(self) -> None:
        class FakeEngine:
            name = "fake"

            def can_handle(self, spec):
                return True

        pipeline = create_example_pipeline(
            engine=FakeEngine(),
            spec_llm_client=object(),
            use_llm_spec_builder=True,
        )

        self.assertIsInstance(pipeline.markdown_report_engine, ReportEngine)
        self.assertIsNotNone(pipeline.markdown_spec_builder)

    def test_confirmed_markdown_bypasses_engine_registry(self) -> None:
        class FakeEngine:
            name = "fake"

            def can_handle(self, spec):
                return True

        class MarkdownReportEngine:
            def __init__(self) -> None:
                self.received = None

            def run_markdown(self, **kwargs):
                self.received = kwargs
                return "report-result"

        report_engine = MarkdownReportEngine()
        pipeline = create_example_pipeline(
            engine=FakeEngine(),
            markdown_report_engine=report_engine,
            default_organization_id="test-org",
        )
        pipeline.engine_registry.select = lambda spec: self.fail(
            "Markdown confirmation must not select an engine from the registry."
        )
        prepared = PreparedMarkdownExecution(
            query=UserQuery(text="Create a report"),
            intent_analysis=IntentAnalysis(intent="report"),
            spec_markdown="# Interactive Execution Spec",
        )

        result = pipeline.execute_confirmed_markdown(
            prepared,
            "# Interactive Execution Spec\n\nConfirmed instructions.",
        )

        self.assertEqual(result.answer, "report-result")
        self.assertEqual(report_engine.received["organization_id"], "test-org")
        self.assertEqual(
            report_engine.received["spec_markdown"],
            "# Interactive Execution Spec\n\nConfirmed instructions.",
        )

    def test_example_intent_analyzer_does_not_infer_intent_from_datahub(self) -> None:
        intent = ExampleIntentAnalyzer().analyze(
            UserQuery(text="Hello"),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(intent, "general")

    def test_request_maps_to_sdk_contracts(self) -> None:
        request = CreateResponseRequest(
            input="What is total revenue?",
            user_id="user-1",
            session_id="session-1",
        )

        invocation = build_workflow_invocation(request, Path("unused"))

        self.assertEqual(invocation.query.text, "What is total revenue?")
        self.assertEqual(invocation.query.user_id, "user-1")
        self.assertEqual(invocation.query.session_id, "session-1")
        self.assertEqual(invocation.corpus_package.sources, [])
        self.assertEqual(invocation.user_context.user_id, "user-1")
        self.assertEqual(invocation.session_context.session_id, "session-1")

    def test_blank_input_uses_default_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sales.csv"
            source.write_text("revenue\n42\n", encoding="utf-8")
            request = CreateResponseRequest(
                input="   ",
                data_corpus_package=DataCorpusPackageRequest(sources=["sales.csv"]),
            )

            invocation = build_workflow_invocation(request, root)

        self.assertEqual(invocation.query.text, DEFAULT_QUERY)

    def test_missing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = CreateResponseRequest(
                data_corpus_package=DataCorpusPackageRequest(sources=["missing.csv"])
            )
            with self.assertRaisesRegex(SourceValidationError, "does not exist"):
                resolve_sources(request.data_corpus_package.sources, Path(temp_dir))

    def test_parent_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            with tempfile.TemporaryDirectory() as outside_dir:
                outside = Path(outside_dir) / "secret.csv"
                outside.write_text("secret\nvalue\n", encoding="utf-8")
                request = CreateResponseRequest(
                    data_corpus_package=DataCorpusPackageRequest(sources=[str(outside)])
                )
                with self.assertRaisesRegex(SourceValidationError, "outside"):
                    resolve_sources(request.data_corpus_package.sources, Path(root_dir))

    def test_remote_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = CreateResponseRequest(
                data_corpus_package=DataCorpusPackageRequest(
                    sources=["https://example.com/data.csv"]
                )
            )
            with self.assertRaisesRegex(SourceValidationError, "Remote"):
                resolve_sources(request.data_corpus_package.sources, Path(temp_dir))

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            with tempfile.TemporaryDirectory() as outside_dir:
                root = Path(root_dir)
                outside = Path(outside_dir) / "secret.csv"
                outside.write_text("secret\nvalue\n", encoding="utf-8")
                link = root / "linked.csv"
                try:
                    link.symlink_to(outside)
                except OSError as error:
                    self.skipTest(f"Symlinks are unavailable: {error}")
                request = CreateResponseRequest(
                    data_corpus_package=DataCorpusPackageRequest(sources=["linked.csv"])
                )
                with self.assertRaisesRegex(SourceValidationError, "outside"):
                    resolve_sources(request.data_corpus_package.sources, root)


if __name__ == "__main__":
    unittest.main()
