from __future__ import annotations

import json
import unittest

from data_intelligence_sdk.core.types import CapabilityRequirement
from data_intelligence_sdk.methods.csv import register_csv_methods
from data_intelligence_sdk.runtime.method_hub import (
    DuplicateMethodError,
    InvalidMethodError,
    MethodHub,
    MethodTrustError,
)


def alpha_lookup(value: str) -> str:
    return f"alpha:{value}"


def beta_lookup(value: str) -> str:
    return f"beta:{value}"


def gamma_lookup(value: str) -> str:
    return f"gamma:{value}"


class MethodHubExtendedTests(unittest.TestCase):
    def test_register_validates_inputs_and_supports_replace(self) -> None:
        hub = MethodHub()
        metadata = {"owner": "analytics", "nested": {"region": "us"}}

        with self.assertRaises(InvalidMethodError):
            hub.register("", alpha_lookup)
        with self.assertRaises(InvalidMethodError):
            hub.register("alpha", object())

        hub.register(
            "alpha",
            alpha_lookup,
            capability_names=["lookup", "", "lookup"],
            tags=["core", "", "core"],
            metadata=metadata,
        )

        metadata["owner"] = "platform"
        metadata["nested"]["region"] = "eu"
        definition = hub.get_definition("alpha")
        self.assertEqual(definition.capability_names, ["lookup"])
        self.assertEqual(definition.tags, ["core"])
        self.assertEqual(definition.metadata["owner"], "analytics")
        self.assertEqual(definition.metadata["nested"]["region"], "us")

        with self.assertRaises(DuplicateMethodError):
            hub.register("alpha", beta_lookup)

        hub.register("alpha", beta_lookup, replace=True)
        self.assertIs(hub.get("alpha"), beta_lookup)

    def test_get_blocks_untrusted_methods_but_keeps_definitions_visible(self) -> None:
        hub = MethodHub()
        hub.register(
            "blocked_lookup",
            alpha_lookup,
            capability_names=["blocked_capability"],
            trust_level="blocked",
        )
        definition = hub.get_definition("blocked_lookup")

        self.assertEqual(definition.trust_level, "blocked")
        with self.assertRaises(MethodTrustError):
            hub.get("blocked_lookup")

    def test_deterministic_ordering_and_selection(self) -> None:
        hub = MethodHub()
        hub.register(
            "alpha_lookup",
            alpha_lookup,
            capability_names=["lookup"],
            priority=5,
            trust_level="builtin",
        )
        hub.register(
            "beta_lookup",
            beta_lookup,
            capability_names=["lookup"],
            priority=10,
            trust_level="user_approved",
        )
        hub.register(
            "gamma_lookup",
            gamma_lookup,
            capability_names=["lookup"],
            priority=10,
            trust_level="builtin",
        )

        method_names = [method.name for method in hub.list_methods()]
        self.assertEqual(method_names, ["gamma_lookup", "beta_lookup", "alpha_lookup"])
        self.assertEqual(hub.resolve(CapabilityRequirement(name="lookup")).name, "gamma_lookup")
        self.assertEqual(
            [method.name for method in hub.select_for_requirements(
                [CapabilityRequirement(name="lookup"), CapabilityRequirement(name="lookup")]
            )],
            ["gamma_lookup"],
        )
        self.assertEqual(
            hub.resolve_all([CapabilityRequirement(name="lookup")])["lookup"].name,
            "gamma_lookup",
        )

    def test_search_and_catalog_are_json_serializable(self) -> None:
        hub = MethodHub()
        register_csv_methods(hub)
        hub.register(
            "blocked_lookup",
            alpha_lookup,
            capability_names=["lookup"],
            trust_level="blocked",
            status="draft",
        )

        results = hub.search("preview csv columns")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "scan_csv")

        catalog = hub.build_llm_catalog()
        catalog_json = json.dumps(catalog, ensure_ascii=False)

        self.assertIn("child-method-hub-catalog-v1", catalog_json)
        self.assertNotIn("blocked_lookup", catalog_json)
        self.assertIn("scan_csv", catalog_json)


if __name__ == "__main__":
    unittest.main()
