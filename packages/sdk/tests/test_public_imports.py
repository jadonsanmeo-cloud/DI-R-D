import unittest

import data_intelligence_sdk as dis
from data_intelligence_sdk.evidence import EvidenceCollector
from data_intelligence_sdk.intent import IntentAnalyzer
from data_intelligence_sdk.spec import SpecBuilder, SpecConfirmation
from data_intelligence_sdk.synthesis import Synthesizer
from data_intelligence_sdk.runtime import (
    EngineRuntimeContext,
    InMemoryInterfaceRegistry,
    MethodHub,
)
from data_intelligence_sdk.sandbox import SandboxRunResult


class PublicImportTests(unittest.TestCase):
    def test_root_package_exports_new_core_contracts(self) -> None:
        self.assertTrue(hasattr(dis, "DataCorpusPackage"))
        self.assertTrue(hasattr(dis, "DataHubContext"))
        self.assertTrue(hasattr(dis, "CapabilityRequirement"))
        self.assertTrue(hasattr(dis, "InterfaceDefinition"))
        self.assertTrue(hasattr(dis, "TrustLevel"))

    def test_runtime_and_sandbox_packages_export_new_contracts(self) -> None:
        runtime = EngineRuntimeContext(
            method_hub=MethodHub(),
            interface_registry=InMemoryInterfaceRegistry(),
        )
        sandbox_result = SandboxRunResult(status="completed")

        self.assertIsInstance(runtime.method_hub, MethodHub)
        self.assertEqual(sandbox_result.status, "completed")

    def test_boundary_packages_export_protocol_contracts(self) -> None:
        self.assertIsNotNone(IntentAnalyzer)
        self.assertIsNotNone(SpecBuilder)
        self.assertIsNotNone(SpecConfirmation)
        self.assertIsNotNone(EvidenceCollector)
        self.assertIsNotNone(Synthesizer)

    def test_root_package_does_not_export_example_or_default_factories(self) -> None:
        removed_factory = "create_" + "default_pipeline"
        removed_openrouter_factory = removed_factory + "_from_openrouter"

        self.assertFalse(hasattr(dis, removed_factory))
        self.assertFalse(hasattr(dis, removed_openrouter_factory))


if __name__ == "__main__":
    unittest.main()
