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

    def test_interface_list_inspect_validate_insert_and_call(self) -> None:
        hub = MethodHub()
        definition = {
            "name": "alpha_lookup",
            "method": alpha_lookup,
            "capability_names": ["lookup"],
            "description": "Lookup alpha values.",
            "metadata": {"side_effects": False},
            "tags": ["lookup"],
            "priority": 5,
        }

        validation = hub.validate(definition)
        inserted = hub.insert(definition)
        listed = hub.list({"capability": "lookup"})
        inspected = hub.inspect("alpha_lookup")
        dry_run = hub.dry_run("alpha_lookup", {"value": "x"})
        result = hub.call("alpha_lookup", {"value": "x"})

        self.assertTrue(validation["valid"])
        self.assertEqual(inserted.name, "alpha_lookup")
        self.assertEqual(listed[0]["name"], "alpha_lookup")
        self.assertEqual(inspected["signature"], "(value: 'str') -> 'str'")
        self.assertTrue(dry_run["valid"])
        self.assertEqual(result, "alpha:x")
        self.assertEqual(hub.history("alpha_lookup", action="call")[-1]["status"], "completed")

    def test_update_deprecate_and_remove_lifecycle(self) -> None:
        hub = MethodHub()
        hub.register("alpha_lookup", alpha_lookup, capability_names=["lookup"])

        updated = hub.update(
            "alpha_lookup",
            {
                "method": beta_lookup,
                "description": "Updated lookup.",
                "priority": 20,
                "metadata": {"side_effects": False},
            },
        )
        deprecated = hub.deprecate("alpha_lookup", reason="Use beta.")
        removed = hub.remove("alpha_lookup")

        self.assertIs(updated.method, beta_lookup)
        self.assertEqual(updated.priority, 20)
        self.assertEqual(deprecated.status, "deprecated")
        self.assertEqual(deprecated.metadata["deprecation_reason"], "Use beta.")
        self.assertEqual(removed["name"], "alpha_lookup")
        self.assertEqual(hub.history(action="remove")[-1]["method_name"], "alpha_lookup")

    def test_dry_run_reports_invalid_arguments_without_calling(self) -> None:
        hub = MethodHub()
        hub.register("alpha_lookup", alpha_lookup)

        result = hub.dry_run("alpha_lookup", {})

        self.assertFalse(result["valid"])
        self.assertEqual(result["missing"], ["value"])
        self.assertEqual(hub.history("alpha_lookup", action="dry_run")[-1]["status"], "failed")

    def test_in_memory_proposal_flow(self) -> None:
        hub = MethodHub()
        proposal = hub.propose(
            {
                "name": "alpha_lookup",
                "method": alpha_lookup,
                "capability_names": ["lookup"],
                "metadata": {"side_effects": False},
            },
            proposal_id="proposal-alpha",
        )

        self.assertEqual(proposal["status"], "pending")
        self.assertNotIn("method", proposal["definition"])
        self.assertEqual(hub.proposals(status="pending")[0]["proposal_id"], "proposal-alpha")

        approved = hub.approve("proposal-alpha")

        self.assertEqual(approved.trust_level, "generated_validated")
        self.assertEqual(hub.call("alpha_lookup", {"value": "ok"}), "alpha:ok")
        self.assertEqual(hub.proposals(status="accepted")[0]["proposal_id"], "proposal-alpha")

    def test_reject_proposal_and_select_for_task(self) -> None:
        hub = MethodHub()
        register_csv_methods(hub)
        proposal = hub.propose(
            {
                "name": "gamma_lookup",
                "method": gamma_lookup,
                "capability_names": ["lookup"],
                "metadata": {"side_effects": False},
            },
            proposal_id="proposal-gamma",
        )

        rejected = hub.reject(proposal["proposal_id"], reason="Not needed.")
        selected = hub.select({"description": "preview csv columns"}, top_k=1)

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reason"], "Not needed.")
        self.assertEqual(selected[0]["name"], "scan_csv")

    def test_export_and_import_manifest_interfaces(self) -> None:
        source_hub = MethodHub()
        source_hub.register(
            "alpha_lookup",
            alpha_lookup,
            capability_names=["lookup"],
            metadata={"side_effects": False},
        )

        manifest = source_hub.export("alpha_lookup")
        target_hub = MethodHub()
        imported = target_hub.import_manifest(manifest)

        self.assertEqual(manifest["entrypoint"], "test_method_hub_extended:alpha_lookup")
        self.assertTrue(manifest["callable_exportable"])
        self.assertEqual(imported.name, "alpha_lookup")
        self.assertEqual(target_hub.call("alpha_lookup", {"value": "z"}), "alpha:z")


if __name__ == "__main__":
    unittest.main()
