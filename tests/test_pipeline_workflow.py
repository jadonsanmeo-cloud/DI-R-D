import os
import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
    PreparedExecution,
    UserQuery,
)
from examples.basic_workflow import create_example_pipeline
from data_intelligence_sdk.runtime.method_hub import MethodHub


class FakeAnalyzer:
    def analyze(self, query, corpus_package, session_context=None, user_context=None):
        return "reason"


class FakeSpecBuilder:
    def build(
        self, query, intent, corpus_package, session_context=None, user_context=None
    ):
        return ExecutionSpec(intent=intent, objective=query.text)


class FakeConfirmation:
    def confirm(self, spec, session_context=None, user_context=None):
        spec.confirmed = True
        return spec


class FakeRegistry:
    def __init__(self, engine):
        self.engine = engine
        self.select_count = 0

    def select(self, spec):
        self.select_count += 1
        return self.engine


class FakeEngine:
    name = "fake"

    def __init__(self):
        self.runtime = None

    def can_handle(self, spec):
        return True

    def run(self, spec, corpus_package, runtime, user_context=None) -> EngineOutput:
        self.runtime = runtime
        runtime.run_context.record_step("fake_step")
        return runtime.run_context.build_output(engine_name=self.name, result="answer")


class FakeEvidenceCollector:
    def collect(self, spec, output):
        return EvidenceBundle(sources=["sales.csv"], steps=output.trace.steps)


class FakeSynthesizer:
    def synthesize(self, spec, output, evidence):
        return FinalResponse(answer=str(output.result), evidence=evidence)

class FakeLogger:
    def __init__(self):
        self.events = []

    def log(self, event, payload=None):
        self.events.append((event, payload or {}))


class PipelineWorkflowTests(unittest.TestCase):
    def _pipeline(self, *, logger=None):
        engine = FakeEngine()
        registry = FakeRegistry(engine)
        pipeline = DataIntelligencePipeline(
            intent_analyzer=FakeAnalyzer(),
            spec_builder=FakeSpecBuilder(),
            spec_confirmation=FakeConfirmation(),
            engine_registry=registry,
            evidence_collector=FakeEvidenceCollector(),
            synthesizer=FakeSynthesizer(),
            logger=logger,
        )
        return pipeline, registry

    def test_prepare_spec_stops_before_engine_selection(self) -> None:
        logger = FakeLogger()
        pipeline, registry = self._pipeline(logger=logger)

        prepared = pipeline.prepare_spec(
            UserQuery("answer"), DataCorpusPackage(sources=["sales.csv"])
        )

        self.assertIsInstance(prepared, PreparedExecution)
        self.assertEqual(prepared.intent, "reason")
        self.assertFalse(prepared.spec.confirmed)
        self.assertEqual(registry.select_count, 0)
        self.assertEqual(
            [event for event, _ in logger.events],
            ["pipeline.start", "pipeline.intent_analyzed", "pipeline.spec_built"],
        )

    def test_execute_confirmed_spec_rejects_unconfirmed_spec(self) -> None:
        pipeline, _ = self._pipeline()
        prepared = pipeline.prepare_spec(UserQuery("answer"), DataCorpusPackage())

        with self.assertRaisesRegex(ValueError, "confirmed"):
            pipeline.execute_confirmed_spec(prepared, prepared.spec)

    def test_execute_confirmed_spec_runs_from_engine_selection(self) -> None:
        logger = FakeLogger()
        pipeline, registry = self._pipeline(logger=logger)
        prepared = pipeline.prepare_spec(UserQuery("answer"), DataCorpusPackage())
        logger.events.clear()
        prepared.spec.confirmed = True

        response = pipeline.execute_confirmed_spec(prepared, prepared.spec)

        self.assertEqual(response.answer, "answer")
        self.assertEqual(registry.select_count, 1)
        self.assertEqual(
            [event for event, _ in logger.events],
            [
                "pipeline.spec_confirmed",
                "pipeline.engine_selected",
                "pipeline.engine_completed",
                "pipeline.evidence_collected",
                "pipeline.completed",
            ],
        )

    def test_pipeline_orchestrates_components(self) -> None:
        engine = FakeEngine()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=FakeAnalyzer(),
            spec_builder=FakeSpecBuilder(),
            spec_confirmation=FakeConfirmation(),
            engine_registry=FakeRegistry(engine),
            evidence_collector=FakeEvidenceCollector(),
            synthesizer=FakeSynthesizer(),
        )

        response = pipeline.run(
            UserQuery("answer"), DataCorpusPackage(sources=["sales.csv"])
        )

        self.assertEqual(response.answer, "answer")
        self.assertEqual(response.evidence.sources, ["sales.csv"])
        self.assertEqual(response.evidence.steps[0].name, "fake_step")
        self.assertIsInstance(engine.runtime.method_hub, MethodHub)

    def test_pipeline_logs_lifecycle_events(self) -> None:
        logger = FakeLogger()
        engine = FakeEngine()
        pipeline = DataIntelligencePipeline(
            intent_analyzer=FakeAnalyzer(),
            spec_builder=FakeSpecBuilder(),
            spec_confirmation=FakeConfirmation(),
            engine_registry=FakeRegistry(engine),
            evidence_collector=FakeEvidenceCollector(),
            synthesizer=FakeSynthesizer(),
            logger=logger,
        )

        pipeline.run(UserQuery("answer"), DataCorpusPackage(sources=["sales.csv"]))

        event_names = [event for event, _ in logger.events]
        self.assertEqual(
            event_names,
            [
                "pipeline.start",
                "pipeline.intent_analyzed",
                "pipeline.spec_built",
                "pipeline.spec_confirmed",
                "pipeline.engine_selected",
                "pipeline.engine_completed",
                "pipeline.evidence_collected",
                "pipeline.completed",
            ],
        )
        self.assertEqual(logger.events[0][1]["query"], "answer")
        self.assertEqual(logger.events[1][1]["intent"], "reason")
        self.assertEqual(logger.events[4][1]["engine_name"], "fake")
        self.assertEqual(logger.events[5][1]["method_call_count"], 0)

    def test_create_example_pipeline_with_fake_engine_runs_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\n", encoding="utf-8"
            )
            pipeline = create_example_pipeline(engine=FakeEngine())

            response = pipeline.run(
                UserQuery("count rows"), DataCorpusPackage(sources=[str(csv_path)])
            )

            self.assertTrue(response.answer)
            self.assertIsNotNone(response.evidence)

    def test_create_example_pipeline_defaults_to_openrouter_and_validates_api_key(
        self,
    ) -> None:
        old_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                create_example_pipeline(model="some/model", api_key=None)
        finally:
            if old_key is not None:
                os.environ["OPENROUTER_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
