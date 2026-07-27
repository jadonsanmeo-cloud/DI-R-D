import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from data_intelligence_sdk.runtime.config import (
    IntentServiceSettings,
    MethodHubSettings,
)

from data_intelligence_api.application.workflow import (
    DEFAULT_QUERY,
    SourceValidationError,
    build_workflow_invocation,
    default_pipeline_factory,
)
from data_intelligence_api.http.schemas.responses import (
    CreateResponseRequest,
    DataCorpusPackageRequest,
)
from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    ExampleIntentAnalyzer,
    create_example_pipeline,
)


class BackendWorkflowTests(unittest.TestCase):
    def test_default_pipeline_factory_uses_given_model_config(self) -> None:
        sentinel = object()
        manager = Mock()
        manager.method_hub_settings.return_value = MethodHubSettings(enabled=False)
        manager.intent_service_settings.return_value = IntentServiceSettings(
            endpoint="http://localhost:8005",
            enabled=True,
        )
        with (
            patch.dict(
                "os.environ",
                {"MODEL_CONFIG_PATH": "/app/configs/proxy-openrouter.toml"},
                clear=True,
            ),
            patch(
                "data_intelligence_api.application.workflow.ConfigManager",
                return_value=manager,
            ) as config_manager,
            patch(
                "data_intelligence_api.application.workflow.create_example_pipeline",
                return_value=sentinel,
            ) as create_pipeline,
        ):
            result = default_pipeline_factory(logger="logger")

        self.assertIs(result, sentinel)
        config_manager.assert_called_once_with("/app/configs/proxy-openrouter.toml")
        create_pipeline.assert_called_once_with(
            logger="logger",
            config_manager=manager,
            use_llm_spec_builder=True,
            intent_service_base_url="http://localhost:8005",
            mcp_client=None,
        )

    def test_default_pipeline_factory_passes_intent_service_base_url(self) -> None:
        sentinel = object()
        manager = Mock()
        manager.method_hub_settings.return_value = MethodHubSettings(enabled=False)
        manager.intent_service_settings.return_value = IntentServiceSettings(
            endpoint="http://localhost:8005",
            enabled=True,
        )
        with (
            patch.dict(
                "os.environ",
                {"INTENT_SERVICE_BASE_URL": "http://localhost:8005"},
                clear=True,
            ),
            patch(
                "data_intelligence_api.application.workflow.ConfigManager",
                return_value=manager,
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
            config_manager=manager,
            use_llm_spec_builder=True,
            intent_service_base_url="http://localhost:8005",
            mcp_client=None,
        )

    def test_example_pipeline_uses_intent_service_analyzer_when_configured(
        self,
    ) -> None:
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
                artifact_store=Mock(),
                sandbox_provider=Mock(),
            )

        analyzer_class.assert_called_once()
        analyzer_kwargs = analyzer_class.call_args.kwargs
        self.assertEqual(analyzer_kwargs["base_url"], "http://localhost:8005")
        self.assertIsInstance(
            analyzer_kwargs["fallback_analyzer"],
            ExampleIntentAnalyzer,
        )
        self.assertIs(pipeline.intent_analyzer, analyzer_class.return_value)

    def test_example_intent_analyzer_does_not_infer_intent_from_datahub(self) -> None:
        intent = ExampleIntentAnalyzer().analyze(
            UserQuery(text="Hello"),
            DataCorpusPackage(sources=["orders.csv"]),
        )

        self.assertEqual(intent, "general")

    def test_request_maps_to_sdk_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sales.csv"
            source.write_text("revenue\n42\n", encoding="utf-8")
            request = CreateResponseRequest(
                input="What is total revenue?",
                data_corpus_package=DataCorpusPackageRequest(
                    sources=["sales.csv"],
                    schemas={"sales": {"revenue": "number"}},
                    metadata={"catalog": "demo"},
                ),
                user_id="user-1",
                session_id="session-1",
            )

            invocation = build_workflow_invocation(request, root)

        self.assertEqual(invocation.query.text, "What is total revenue?")
        self.assertEqual(invocation.query.user_id, "user-1")
        self.assertEqual(invocation.query.session_id, "session-1")
        self.assertEqual(invocation.corpus_package.sources, [str(source.resolve())])
        self.assertEqual(
            invocation.corpus_package.schemas,
            {"sales": {"revenue": "number"}},
        )
        self.assertEqual(invocation.corpus_package.metadata, {"catalog": "demo"})
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

    def test_query_without_data_sources_is_allowed(self) -> None:
        request = CreateResponseRequest(
            input="hello",
            data_corpus_package=DataCorpusPackageRequest(sources=[]),
        )

        invocation = build_workflow_invocation(request, Path.cwd())

        self.assertEqual(invocation.query.text, "hello")
        self.assertEqual(invocation.corpus_package.sources, [])

    def test_missing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = CreateResponseRequest(
                data_corpus_package=DataCorpusPackageRequest(sources=["missing.csv"])
            )
            with self.assertRaisesRegex(SourceValidationError, "does not exist"):
                build_workflow_invocation(request, Path(temp_dir))

    def test_parent_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            with tempfile.TemporaryDirectory() as outside_dir:
                outside = Path(outside_dir) / "secret.csv"
                outside.write_text("secret\nvalue\n", encoding="utf-8")
                request = CreateResponseRequest(
                    data_corpus_package=DataCorpusPackageRequest(sources=[str(outside)])
                )
                with self.assertRaisesRegex(SourceValidationError, "outside"):
                    build_workflow_invocation(request, Path(root_dir))

    def test_remote_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = CreateResponseRequest(
                data_corpus_package=DataCorpusPackageRequest(
                    sources=["https://example.com/data.csv"]
                )
            )
            with self.assertRaisesRegex(SourceValidationError, "Remote"):
                build_workflow_invocation(request, Path(temp_dir))

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
                    build_workflow_invocation(request, root)


if __name__ == "__main__":
    unittest.main()
