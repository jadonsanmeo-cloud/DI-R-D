import inspect
import unittest

import data_intelligence_sdk as dis
from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    DataHubContext,
    ExecutionSpec,
    InterfaceDefinition,
)

from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class CoreTypesTests(unittest.TestCase):
    def test_data_corpus_package_keeps_existing_context_shape(self) -> None:
        corpus = DataCorpusPackage(
            sources=["warehouse.orders"],
            schemas={"warehouse.orders": {"columns": ["id", "total"]}},
            metadata={"owner": "analytics"},
        )

        self.assertEqual(corpus.sources, ["warehouse.orders"])
        self.assertEqual(corpus.schemas["warehouse.orders"]["columns"], ["id", "total"])
        self.assertEqual(corpus.metadata, {"owner": "analytics"})

    def test_datahub_context_is_compatibility_alias(self) -> None:
        self.assertIs(DataHubContext, DataCorpusPackage)
        self.assertIs(dis.DataCorpusPackage, DataCorpusPackage)
        self.assertIs(dis.DataHubContext, DataCorpusPackage)

    def test_execution_spec_accepts_capability_requirements(self) -> None:
        capability = CapabilityRequirement(
            name="query_customer_orders",
            description="Read customer order rows for analysis.",
            input_schema={"customer_id": "str"},
            output_schema={"orders": "list"},
            constraints={"freshness": "daily"},
            metadata={"source": "warehouse.orders"},
        )
        spec = ExecutionSpec(
            intent="reason",
            objective="Summarize customer orders.",
            capability_requirements=[capability],
        )

        self.assertEqual(spec.capability_requirements, [capability])
        self.assertEqual(spec.capability_requirements[0].name, "query_customer_orders")

    def test_interface_definition_defaults_to_generated_unvalidated(self) -> None:
        interface = InterfaceDefinition(
            name="orders_reader",
            description="Reads customer orders.",
            input_schema={"customer_id": "str"},
            output_schema={"orders": "list"},
            implementation_ref="generated://orders_reader.py",
        )

        self.assertEqual(interface.source, "generated")
        self.assertEqual(interface.trust_level, "generated_unvalidated")


class EngineSignatureTests(unittest.TestCase):
    def test_engine_run_signature_accepts_corpus_and_runtime_context(self) -> None:
        engine = GeneralPurposeEngine(llm=object())
        spec = ExecutionSpec(intent="reason", objective="Run general analysis.")
        corpus = DataCorpusPackage(sources=["warehouse.orders"])
        runtime = EngineRuntimeContext()

        output = engine.run(spec, corpus, runtime)

        self.assertEqual(output.engine_name, "general_purpose")

    def test_engine_run_signature_names_runtime_contracts(self) -> None:
        signature = inspect.signature(GeneralPurposeEngine.run)

        self.assertEqual(
            list(signature.parameters),
            ["self", "spec", "corpus_package", "runtime", "user_context"],
        )


if __name__ == "__main__":
    unittest.main()
