import unittest

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    InterfaceDefinition,
)
from data_intelligence_sdk.runtime.interfaces import InMemoryInterfaceRegistry


class InterfaceRegistryTests(unittest.TestCase):
    def test_register_and_list_interfaces(self) -> None:
        registry = InMemoryInterfaceRegistry()
        interface = InterfaceDefinition(
            name="orders_reader",
            description="Reads order rows.",
            implementation_ref="builtin://orders_reader",
            source="builtin",
            trust_level="builtin",
            metadata={"capability_names": ["query_customer_orders"]},
        )

        registry.register(interface)

        self.assertEqual(registry.list_available(), [interface])

    def test_find_returns_matching_interface_for_capability(self) -> None:
        registry = InMemoryInterfaceRegistry()
        interface = InterfaceDefinition(
            name="orders_reader",
            implementation_ref="builtin://orders_reader",
            source="builtin",
            trust_level="builtin",
            metadata={"capability_names": ["query_customer_orders"]},
        )
        registry.register(interface)
        requirement = CapabilityRequirement(name="query_customer_orders")
        corpus = DataCorpusPackage(sources=["warehouse.orders"])

        self.assertEqual(registry.find(requirement, corpus), interface)

    def test_find_returns_none_when_no_interface_matches(self) -> None:
        registry = InMemoryInterfaceRegistry()
        requirement = CapabilityRequirement(name="query_customer_orders")
        corpus = DataCorpusPackage(sources=["warehouse.orders"])

        self.assertIsNone(registry.find(requirement, corpus))


if __name__ == "__main__":
    unittest.main()
