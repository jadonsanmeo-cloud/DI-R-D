import unittest

from data_intelligence_sdk.core.types import CapabilityRequirement
from data_intelligence_sdk.runtime.method_hub import MethodHub, RegisteredMethod


def read_orders(customer_id: str) -> list[dict[str, str]]:
    return [{"customer_id": customer_id, "order_id": "order-1"}]


class MethodHubTests(unittest.TestCase):
    def test_register_and_get_preserves_simple_api(self) -> None:
        hub = MethodHub()
        hub.register("read_orders", read_orders)

        self.assertIs(hub.get("read_orders"), read_orders)

    def test_list_methods_returns_registered_definitions(self) -> None:
        hub = MethodHub()
        hub.register(
            "read_orders",
            read_orders,
            capability_names=["query_customer_orders"],
            trust_level="builtin",
            metadata={"owner": "analytics"},
        )

        methods = hub.list_methods()

        self.assertEqual(len(methods), 1)
        self.assertIsInstance(methods[0], RegisteredMethod)
        self.assertEqual(methods[0].name, "read_orders")
        self.assertEqual(methods[0].capability_names, ["query_customer_orders"])
        self.assertEqual(methods[0].trust_level, "builtin")
        self.assertEqual(methods[0].metadata, {"owner": "analytics"})

    def test_resolve_returns_method_for_capability_requirement(self) -> None:
        hub = MethodHub()
        hub.register(
            "read_orders", read_orders, capability_names=["query_customer_orders"]
        )
        requirement = CapabilityRequirement(name="query_customer_orders")

        resolved = hub.resolve(requirement)

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "read_orders")
        self.assertIs(resolved.method, read_orders)

    def test_resolve_returns_none_when_capability_is_missing(self) -> None:
        hub = MethodHub()
        requirement = CapabilityRequirement(name="query_customer_orders")

        self.assertIsNone(hub.resolve(requirement))


if __name__ == "__main__":
    unittest.main()
