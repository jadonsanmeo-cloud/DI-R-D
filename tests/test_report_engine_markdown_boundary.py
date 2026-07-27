from __future__ import annotations

import unittest
from unittest.mock import Mock

from data_intelligence_sdk.engines.report import ReportEngine
from data_intelligence_sdk.core.types import EngineOutput


class ReportEngineMarkdownBoundaryTests(unittest.TestCase):
    def test_run_markdown_receives_exact_markdown_and_organization(self) -> None:
        engine = ReportEngine.__new__(ReportEngine)
        engine.run = Mock(return_value=EngineOutput(engine_name="report", result="report"))
        runtime = object()
        user_context = object()
        markdown = "# Interactive Execution Spec\n\n## Execution Instructions\nRetrieve data.\n"

        result = engine.run_markdown(
            spec_markdown=markdown,
            organization_id="test-org",
            runtime=runtime,
            user_context=user_context,
        )

        self.assertEqual(result.answer, "report")
        engine.run.assert_called_once()
        spec, corpus, received_runtime, received_user = engine.run.call_args.args
        self.assertEqual(spec.objective, markdown.strip())
        self.assertEqual(corpus.metadata["organization_id"], "test-org")
        self.assertIs(received_runtime, runtime)
        self.assertIs(received_user, user_context)


if __name__ == "__main__":
    unittest.main()
