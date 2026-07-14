from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.methods.csv import register_csv_methods
from data_intelligence_sdk.runtime.method_catalog import (
    build_catalog_payload,
    read_catalog,
    write_catalog,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


def blocked_tool() -> str:
    return "blocked"


class MethodCatalogTests(unittest.TestCase):
    def test_build_catalog_filters_executable_methods(self) -> None:
        hub = MethodHub()
        register_csv_methods(hub)
        hub.register(
            "blocked_tool",
            blocked_tool,
            capability_names=["blocked_tool"],
            trust_level="blocked",
            status="draft",
        )

        executable_payload = build_catalog_payload(hub)
        full_payload = build_catalog_payload(hub, executable_only=False)

        executable_names = {method["name"] for method in executable_payload["methods"]}
        full_names = {method["name"] for method in full_payload["methods"]}

        self.assertNotIn("blocked_tool", executable_names)
        self.assertIn("blocked_tool", full_names)
        self.assertEqual(executable_payload["format"], "child-method-hub-catalog-v1")
        self.assertTrue(json.dumps(executable_payload, ensure_ascii=False))

    def test_write_catalog_creates_parent_directories_and_round_trips(self) -> None:
        hub = MethodHub()
        register_csv_methods(hub)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "nested" / "catalog.json"
            written_path = write_catalog(hub, output_path)
            payload = read_catalog(written_path)
            self.assertTrue(written_path.exists())
            self.assertEqual(payload, build_catalog_payload(hub))


if __name__ == "__main__":
    unittest.main()
