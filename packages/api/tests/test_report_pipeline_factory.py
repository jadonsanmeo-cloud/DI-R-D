import os
import unittest
from unittest.mock import patch

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    _ReportDefaultsSpecBuilder,
    create_example_pipeline,
)
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    ExecutionSpec,
    UserQuery,
)


class _SpecBuilder:
    def build(self, query, intent, corpus_package, session_context, user_context):
        del corpus_package, session_context, user_context
        return ExecutionSpec(intent=intent, objective=query.text)

    def revise(
        self,
        *,
        previous_spec,
        user_feedback,
        query,
        intent,
        corpus_package,
        session_context,
        user_context,
    ):
        del (
            previous_spec,
            user_feedback,
            corpus_package,
            session_context,
            user_context,
        )
        return ExecutionSpec(intent=intent, objective=query.text)


class ReportPipelineFactoryTests(unittest.TestCase):
    def test_report_defaults_select_report_engine_and_html(self):
        builder = _ReportDefaultsSpecBuilder(_SpecBuilder())

        spec = builder.build(
            UserQuery(text="Create a report"),
            "report",
            DataCorpusPackage(sources=["sales.csv"]),
        )

        self.assertEqual(spec.engine_hint, "report")
        self.assertEqual(spec.constraints["output_format"], "html")

    def test_explicit_report_format_is_preserved(self):
        spec = ExecutionSpec(
            intent="report",
            objective="Create a report",
            constraints={"output_format": "markdown"},
        )

        result = _ReportDefaultsSpecBuilder._apply(spec)

        self.assertEqual(result.constraints["output_format"], "markdown")

    def test_api_defaults_report_engine_to_method_hub_routing(self):
        with patch.dict(os.environ, {"SANDBOX_ENABLED": "false"}, clear=False):
            os.environ.pop("REPORT_FORCE_CODE_AGENT", None)
            pipeline = create_example_pipeline(
                llm=object(),
                artifact_store=object(),
            )

        engine = pipeline.engine_registry.select(
            ExecutionSpec(
                intent="report",
                objective="Create a report",
                engine_hint="report",
            )
        )

        self.assertFalse(engine.force_code_agent)

    def test_api_can_restore_normal_router_selection(self):
        pipeline = create_example_pipeline(
            llm=object(),
            artifact_store=object(),
            force_report_code_agent=False,
        )

        engine = pipeline.engine_registry.select(
            ExecutionSpec(
                intent="report",
                objective="Create a report",
                engine_hint="report",
            )
        )

        self.assertFalse(engine.force_code_agent)


if __name__ == "__main__":
    unittest.main()
