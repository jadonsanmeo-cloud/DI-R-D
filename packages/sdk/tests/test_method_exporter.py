from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.methods.csv import register_csv_methods
from data_intelligence_sdk.runtime.method_exporter import (
    export_method_bundle,
    validate_bundle,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


class MethodExporterTests(unittest.TestCase):
    def test_export_method_bundle_writes_manifest_source_and_checksums(self) -> None:
        hub = MethodHub()
        register_csv_methods(hub)

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_root = export_method_bundle(hub, "scan_csv", Path(temp_dir) / "exports")
            bundle_path = bundle_root / "bundle.json"
            manifest_path = bundle_root / "method.yaml"
            source_path = bundle_root / "source.py"
            payload = json.loads(bundle_path.read_text(encoding="utf-8"))
            validation = validate_bundle(bundle_root)

            self.assertTrue(bundle_root.exists())
            self.assertTrue(bundle_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(source_path.exists())
            self.assertEqual(payload["format"], "method-bundle-v1")
            self.assertEqual(payload["method_name"], "scan_csv")
            self.assertIsNone(payload["tests_passed"])
            self.assertTrue(validation["valid"])
            self.assertEqual(payload["manifest"]["entrypoint"], "data_intelligence_sdk.methods.csv:scan_csv")
            self.assertEqual({file_info["path"] for file_info in payload["files"]}, {"method.yaml", "source.py"})


if __name__ == "__main__":
    unittest.main()
