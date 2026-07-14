from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.runtime.method_loader import (
    MethodManifestError,
    import_entrypoint,
    load_manifest,
    load_manifest_directory,
    validate_manifest,
)


class MethodLoaderTests(unittest.TestCase):
    def test_validate_manifest_and_import_entrypoint(self) -> None:
        manifest = validate_manifest(
            {
                "name": "scan_csv",
                "entrypoint": "data_intelligence_sdk.methods.csv:scan_csv",
                "capability_names": ["scan_csv", "inspect_data"],
                "trust_level": "builtin",
                "status": "stable",
                "priority": 10,
                "tags": ["csv"],
                "metadata": {"category": "csv"},
            }
        )

        callable_object = import_entrypoint(manifest["entrypoint"])

        self.assertEqual(manifest["name"], "scan_csv")
        self.assertEqual(callable_object.__name__, "scan_csv")

    def test_load_manifest_directory_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b_scan.yaml").write_text(
                "\n".join(
                    [
                        "name: scan_csv",
                        "version: '1.0.0'",
                        "entrypoint: data_intelligence_sdk.methods.csv:scan_csv",
                        "description: Inspect CSV.",
                        "capability_names:",
                        "  - scan_csv",
                        "trust_level: builtin",
                        "status: stable",
                        "priority: 100",
                        "metadata:",
                        "  category: csv",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "a_filter.yaml").write_text(
                "\n".join(
                    [
                        "name: filter_csv",
                        "version: '1.0.0'",
                        "entrypoint: data_intelligence_sdk.methods.csv:filter_csv",
                        "description: Filter CSV.",
                        "capability_names:",
                        "  - filter_csv",
                        "trust_level: builtin",
                        "status: stable",
                        "priority: 90",
                        "metadata:",
                        "  category: csv",
                    ]
                ),
                encoding="utf-8",
            )
            hub = MethodHub()

            loaded = load_manifest_directory(hub, root)

        self.assertEqual([method.name for method in loaded], ["filter_csv", "scan_csv"])
        self.assertEqual(
            [method.name for method in hub.list_methods()],
            ["scan_csv", "filter_csv"],
        )

    def test_load_manifest_directory_loads_repo_builtin_manifests(self) -> None:
        hub = MethodHub()
        manifest_dir = (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "sdk"
            / "src"
            / "data_intelligence_sdk"
            / "methods"
            / "manifests"
        )

        loaded = load_manifest_directory(hub, manifest_dir)

        self.assertEqual(len(loaded), 5)
        self.assertEqual(
            {method.name for method in loaded},
            {
                "scan_csv",
                "filter_csv",
                "count_csv",
                "sum_csv",
                "search_vector_chunks",
            },
        )
        self.assertEqual(
            hub.get_definition("scan_csv").metadata["entrypoint"],
            "data_intelligence_sdk.methods.csv:scan_csv",
        )

    def test_load_manifest_reports_validation_errors_with_path_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "invalid.yaml"
            manifest_path.write_text(
                "\n".join(
                    [
                        "name: scan_csv",
                        "entrypoint: data_intelligence_sdk.methods.csv:scan_csv",
                        "capability_names:",
                        "  - scan_csv",
                        "trust_level: builtin",
                        "status: invalid",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(MethodManifestError) as exc:
                load_manifest(MethodHub(), manifest_path)

        self.assertIn("invalid.yaml", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
