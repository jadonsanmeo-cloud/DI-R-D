from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.core.types import (
    IntentAnalysis,
    PreparedMarkdownExecution,
    PreprocessingStep,
    UserQuery,
)
from scripts.prepare_spec import main


class FakePipeline:
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare_markdown(self, query: UserQuery, *args: object) -> PreparedMarkdownExecution:
        self.prepare_calls += 1
        step = PreprocessingStep(
            name="understand_request",
            order=1,
            step_type="understand",
            required=True,
        )
        analysis = IntentAnalysis(
            intent="reason",
            catalog_intent_id="data_query",
            preprocessing_steps=[step],
        )
        return PreparedMarkdownExecution(
            query=query,
            intent_analysis=analysis,
            spec_markdown=(
                "# Interactive Execution Spec\n\n"
                "## User Request\n\nSummarize the example.\n\n"
                "## Intent\n\nCreate a report.\n\n"
                "## Preparation Guidance\n\nFollow intent preprocessing.\n\n"
                "## Execution Instructions\n\nRetrieve relevant ingested documents.\n\n"
                "## Expected Output\n\nA cited Markdown report.\n"
            ),
        )


class PrepareSpecCliTests(unittest.TestCase):
    def test_cli_stops_after_prepare_and_writes_markdown_with_ordered_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "debug" / "execution-spec.md"
            stream = io.StringIO()
            fake_pipeline = FakePipeline()
            captured_factory_kwargs: dict[str, object] = {}

            def factory(**kwargs: object) -> FakePipeline:
                captured_factory_kwargs.update(kwargs)
                return fake_pipeline

            exit_code = main(
                [
                    "--query",
                    "Summarize the example",
                    "--output",
                    str(output),
                    "--intent-service-url",
                    "http://localhost:8005",
                    "--verbose",
                ],
                pipeline_factory=factory,
                stream=stream,
                cwd=root,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_pipeline.prepare_calls, 1)
            self.assertTrue(output.is_file())
            self.assertIn(
                "# Interactive Execution Spec",
                output.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                captured_factory_kwargs["intent_service_base_url"],
                "http://localhost:8005",
            )
            logs = stream.getvalue()
            phases = [
                "spec_preparation.started",
                "spec_preparation.completed",
                "markdown_write.completed",
            ]
            self.assertEqual(phases, [phase for phase in phases if phase in logs])
            self.assertNotIn("engine_selection", logs)
            self.assertNotIn("engine_execution", logs)

    def test_cli_returns_nonzero_without_printing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stream = io.StringIO()
            secret = "sk-test-secret-value"

            exit_code = main(
                [
                    "--query",
                    "Inspect data",
                ],
                pipeline_factory=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("provider unavailable")
                ),
                stream=stream,
                cwd=Path(temp_dir),
                environ={"OPENROUTER_API_KEY": secret},
            )

            self.assertNotEqual(exit_code, 0)
            self.assertIn("spec_preparation.failed", stream.getvalue())
            self.assertNotIn(secret, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
