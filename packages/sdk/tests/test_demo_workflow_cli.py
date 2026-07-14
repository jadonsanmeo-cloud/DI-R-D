import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    EngineStep,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
    MethodCall,
    PreparedExecution,
    UserQuery,
)


def load_module():
    module_path = (
        Path(__file__).resolve().parents[3] / "examples" / "demo_workflow_cli.py"
    )
    spec = importlib.util.spec_from_file_location("demo_workflow_cli", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakePipeline:
    max_spec_revision_rounds = 3

    def __init__(self) -> None:
        self.execute_calls = []
        self.revise_calls = []

    def prepare_spec(self, query, corpus):
        self.query = query
        self.corpus = corpus
        spec = ExecutionSpec(
            intent="reason",
            objective=query.text,
            data_requirements=list(corpus.sources),
            capability_requirements=[CapabilityRequirement(name="answer_question")],
        )
        return PreparedExecution(
            query=query,
            intent="reason",
            corpus_package=corpus,
            spec=spec,
        )

    def revise_spec(self, prepared, previous_spec, feedback):
        self.revise_calls.append((prepared, previous_spec, feedback))
        return ExecutionSpec(
            intent=previous_spec.intent,
            objective=f"{previous_spec.objective} ({feedback})",
            data_requirements=list(previous_spec.data_requirements),
            capability_requirements=list(previous_spec.capability_requirements),
        )

    def execute_confirmed_spec(self, prepared, confirmed_spec):
        self.execute_calls.append((prepared, confirmed_spec))
        return FinalResponse(
            answer="Demo answer",
            evidence=EvidenceBundle(
                sources=list(prepared.corpus_package.sources),
                steps=[EngineStep(name="answer", outputs={"status": "ok"})],
                method_calls=[MethodCall(method_name="search_vector_chunks")],
            ),
            metadata={"engine_name": "general_purpose"},
        )


class DemoWorkflowCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.package = DataCorpusPackage(
            sources=["postgresql://demo/db"],
            schemas={"tables": {"orders": {}}},
            metadata={"catalog": {"summary": "Demo corpus"}},
        )

    def args(self, *extra):
        return self.module._parser().parse_args(["--package", "package.json", *extra])

    def test_confirm_runs_current_spec_once(self) -> None:
        pipeline = FakePipeline()
        output = io.StringIO()
        with (
            patch.object(self.module, "_load_package_json", return_value=self.package),
            patch.object(self.module, "_create_pipeline", return_value=pipeline),
        ):
            result = self.module.run(
                self.args("--query", "Summarize orders"),
                input_fn=lambda prompt: "c",
                output=output,
            )

        self.assertEqual(result, 0)
        self.assertEqual(pipeline.query, UserQuery("Summarize orders"))
        self.assertEqual(len(pipeline.execute_calls), 1)
        self.assertTrue(pipeline.execute_calls[0][1].confirmed)
        self.assertIn("=== Execution Spec ===", output.getvalue())
        self.assertIn("=== Final Response ===\nDemo answer", output.getvalue())

    def test_revise_then_confirm_executes_revised_spec(self) -> None:
        pipeline = FakePipeline()
        answers = iter(["r", "Use monthly totals", "c"])
        output = io.StringIO()
        with (
            patch.object(self.module, "_load_package_json", return_value=self.package),
            patch.object(self.module, "_create_pipeline", return_value=pipeline),
        ):
            result = self.module.run(
                self.args(), input_fn=lambda prompt: next(answers), output=output
            )

        self.assertEqual(result, 0)
        self.assertEqual(pipeline.revise_calls[0][2], "Use monthly totals")
        executed_spec = pipeline.execute_calls[0][1]
        self.assertIn("Use monthly totals", executed_spec.objective)
        self.assertTrue(executed_spec.confirmed)

    def test_quit_does_not_execute_engine(self) -> None:
        pipeline = FakePipeline()
        with (
            patch.object(self.module, "_load_package_json", return_value=self.package),
            patch.object(self.module, "_create_pipeline", return_value=pipeline),
        ):
            result = self.module.run(
                self.args(), input_fn=lambda prompt: "q", output=io.StringIO()
            )

        self.assertEqual(result, 0)
        self.assertEqual(pipeline.execute_calls, [])

    def test_invalid_action_and_empty_feedback_reprompt(self) -> None:
        pipeline = FakePipeline()
        answers = iter(["x", "r", "", "r", "clearer scope", "c"])
        output = io.StringIO()
        with (
            patch.object(self.module, "_load_package_json", return_value=self.package),
            patch.object(self.module, "_create_pipeline", return_value=pipeline),
        ):
            self.module.run(
                self.args(), input_fn=lambda prompt: next(answers), output=output
            )

        self.assertIn("Enter c, r, or q.", output.getvalue())
        self.assertIn("Revision feedback cannot be empty.", output.getvalue())
        self.assertEqual(len(pipeline.revise_calls), 1)

    def test_verbose_prints_full_evidence_and_trace(self) -> None:
        pipeline = FakePipeline()
        output = io.StringIO()
        with (
            patch.object(self.module, "_load_package_json", return_value=self.package),
            patch.object(self.module, "_create_pipeline", return_value=pipeline),
        ):
            self.module.run(
                self.args("--verbose"),
                input_fn=lambda prompt: "c",
                output=output,
            )

        rendered = output.getvalue()
        self.assertIn('"method_name": "search_vector_chunks"', rendered)
        self.assertIn('"engine_name": "general_purpose"', rendered)

    def test_main_returns_error_and_redacts_cli_api_key(self) -> None:
        error = io.StringIO()
        secret = "sk-demo-secret"
        with patch.object(
            self.module,
            "run",
            side_effect=ValueError(f"provider rejected {secret}"),
        ):
            result = self.module.main(
                ["--api-key", secret], output=io.StringIO(), error=error
            )

        self.assertEqual(result, 1)
        self.assertNotIn(secret, error.getvalue())
        self.assertIn("[REDACTED]", error.getvalue())

    def test_create_pipeline_enables_llm_spec_builder_and_overrides_base_url(self) -> None:
        args = self.args(
            "--model",
            "demo-model",
            "--api-key",
            "demo-key",
            "--base-url",
            "http://localhost:20128/v1",
        )
        configured = self.module.OpenRouterSettings(
            model="config-model",
            api_key="config-key",
            base_url="https://openrouter.ai/api/v1",
        )
        with (
            patch.object(
                self.module.ConfigManager,
                "openrouter_settings",
                return_value=configured,
            ),
            patch.object(
                self.module, "create_example_pipeline", return_value="pipeline"
            ) as factory,
        ):
            pipeline = self.module._create_pipeline(args, logger=None)

        self.assertEqual(pipeline, "pipeline")
        kwargs = factory.call_args.kwargs
        self.assertTrue(kwargs["use_llm_spec_builder"])
        self.assertEqual(kwargs["model"], "demo-model")
        self.assertEqual(kwargs["api_key"], "demo-key")
        self.assertEqual(
            kwargs["config_manager"].openrouter_settings().base_url,
            "http://localhost:20128/v1",
        )

    def test_create_pipeline_loads_the_supplied_config_path(self) -> None:
        args = self.args(
            "--config",
            "/tmp/custom-proxy-openrouter.toml",
            "--env-file",
            "/tmp/custom.env",
        )
        configured = self.module.OpenRouterSettings(
            model="config-model",
            api_key="config-key",
            base_url="https://openrouter.ai/api/v1",
        )
        with (
            patch.object(self.module, "load_dotenv") as load_env,
            patch.object(
                self.module.ConfigManager,
                "__init__",
                return_value=None,
            ) as config_init,
            patch.object(
                self.module.ConfigManager,
                "openrouter_settings",
                return_value=configured,
            ),
            patch.object(
                self.module, "create_example_pipeline", return_value="pipeline"
            ),
        ):
            pipeline = self.module._create_pipeline(args, logger=None)

        self.assertEqual(pipeline, "pipeline")
        load_env.assert_called_once_with("/tmp/custom.env", override=False)
        config_init.assert_called_once_with("/tmp/custom-proxy-openrouter.toml")

    def test_package_loader_preserves_existing_manifest_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "schema.json").write_text(
                json.dumps({"tables": {"orders": {}}}), encoding="utf-8"
            )
            (root / "catalog.json").write_text(
                json.dumps({"datasets": [{"name": "orders"}]}), encoding="utf-8"
            )
            manifest = root / "package.json"
            manifest.write_text(
                json.dumps(
                    {
                        "vectordb": "postgresql://demo/db?schema=vectordb",
                        "db": "postgresql://demo/db",
                        "schema": "schema.json",
                        "catalog": "catalog.json",
                    }
                ),
                encoding="utf-8",
            )
            corpus = self.module._load_package_json(str(manifest))

        self.assertEqual(corpus.sources[0], "postgresql://demo/db?schema=vectordb")
        self.assertIn("orders", corpus.schemas["tables"])

    def test_direct_help_execution(self) -> None:
        script = (
            Path(__file__).resolve().parents[3] / "examples" / "demo_workflow_cli.py"
        )
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("spec confirmation", result.stdout)


if __name__ == "__main__":
    unittest.main()
