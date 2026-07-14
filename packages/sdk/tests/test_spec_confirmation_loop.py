import unittest

from data_intelligence_sdk.core.pipeline import DataIntelligencePipeline
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    EvidenceBundle,
    ExecutionSpec,
    FinalResponse,
    UserQuery,
)
from data_intelligence_sdk.spec import (
    ConsoleSpecConfirmationProvider,
    DefaultSpecConfirmation,
    SpecConfirmationDecision,
    SpecConfirmationRequest,
    StaticSpecConfirmationProvider,
)


class FakeAnalyzer:
    def analyze(self, query, corpus_package, session_context=None, user_context=None):
        return "reason"


class RevisableSpecBuilder:
    def __init__(self):
        self.feedback = []

    def build(self, query, intent, corpus_package, session_context=None, user_context=None):
        return ExecutionSpec(intent=intent, objective="draft objective")

    def revise(
        self,
        *,
        previous_spec,
        user_feedback,
        query,
        intent,
        corpus_package,
        session_context=None,
        user_context=None,
    ):
        self.feedback.append(user_feedback)
        return ExecutionSpec(intent=intent, objective=f"revised: {user_feedback}")


class BuildOnlySpecBuilder:
    def build(self, query, intent, corpus_package, session_context=None, user_context=None):
        return ExecutionSpec(intent=intent, objective="draft objective")


class FakeRegistry:
    def __init__(self, engine):
        self.engine = engine

    def select(self, spec):
        self.selected_spec = spec
        return self.engine


class FakeEngine:
    name = "fake"

    def run(self, spec, corpus_package, runtime, user_context=None) -> EngineOutput:
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=spec.objective,
        )


class FakeEvidenceCollector:
    def collect(self, spec, output):
        return EvidenceBundle(sources=spec.data_requirements)


class FakeSynthesizer:
    def synthesize(self, spec, output, evidence):
        return FinalResponse(
            answer=str(output.result),
            evidence=evidence,
            metadata={"confirmed": spec.confirmed},
        )


class SpecConfirmationLoopTests(unittest.TestCase):
    def test_pipeline_revises_spec_until_confirmation_accepts_it(self) -> None:
        builder = RevisableSpecBuilder()
        provider = StaticSpecConfirmationProvider(
            [
                SpecConfirmationDecision(
                    action="revise",
                    feedback="Only include completed orders.",
                ),
                SpecConfirmationDecision(action="ok"),
            ]
        )
        registry = FakeRegistry(FakeEngine())
        pipeline = DataIntelligencePipeline(
            intent_analyzer=FakeAnalyzer(),
            spec_builder=builder,
            spec_confirmation=DefaultSpecConfirmation(provider),
            engine_registry=registry,
            evidence_collector=FakeEvidenceCollector(),
            synthesizer=FakeSynthesizer(),
        )

        response = pipeline.run(
            UserQuery("What is revenue?"),
            DataCorpusPackage(sources=["sales.csv"]),
        )

        self.assertEqual(response.answer, "revised: Only include completed orders.")
        self.assertEqual(builder.feedback, ["Only include completed orders."])
        self.assertTrue(registry.selected_spec.confirmed)
        self.assertTrue(response.metadata["confirmed"])

    def test_pipeline_raises_when_revision_is_requested_but_builder_cannot_revise(
        self,
    ) -> None:
        provider = StaticSpecConfirmationProvider(
            [SpecConfirmationDecision(action="revise", feedback="Narrow scope")]
        )
        pipeline = DataIntelligencePipeline(
            intent_analyzer=FakeAnalyzer(),
            spec_builder=BuildOnlySpecBuilder(),
            spec_confirmation=DefaultSpecConfirmation(provider),
            engine_registry=FakeRegistry(FakeEngine()),
            evidence_collector=FakeEvidenceCollector(),
            synthesizer=FakeSynthesizer(),
        )

        with self.assertRaisesRegex(TypeError, "revise"):
            pipeline.run(UserQuery("x"), DataCorpusPackage())


class ConsoleSpecConfirmationProviderTests(unittest.TestCase):
    def test_console_provider_returns_ok_decision(self) -> None:
        outputs = []
        provider = ConsoleSpecConfirmationProvider(
            input_func=lambda prompt: "ok",
            output_func=outputs.append,
        )

        decision = provider.request_confirmation(
            SpecConfirmationRequest(
                spec=ExecutionSpec(intent="reason", objective="draft"),
                message="Confirm this spec",
            )
        )

        self.assertEqual(decision, SpecConfirmationDecision(action="ok"))
        self.assertTrue(any("Confirm this spec" in output for output in outputs))

    def test_console_provider_returns_revision_feedback(self) -> None:
        answers = iter(["revise", "Only completed orders."])
        provider = ConsoleSpecConfirmationProvider(
            input_func=lambda prompt: next(answers),
            output_func=lambda text: None,
        )

        decision = provider.request_confirmation(
            SpecConfirmationRequest(
                spec=ExecutionSpec(intent="reason", objective="draft"),
                message="Confirm this spec",
            )
        )

        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.feedback, "Only completed orders.")


if __name__ == "__main__":
    unittest.main()
