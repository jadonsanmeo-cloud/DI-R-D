import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


def load_create_report_module():
    module_path = Path(__file__).resolve().parents[1] / "examples" / "create_report.py"
    spec = importlib.util.spec_from_file_location("create_report_example", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CreateReportExampleTests(unittest.TestCase):
    def test_main_loads_package_and_prints_markdown_report(self) -> None:
        module = load_create_report_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir)
            schema_path = package_dir / "schema.json"
            schema_path.write_text(
                json.dumps(
                    {"tables": {"orders": {"columns": ["order_id", "revenue"]}}}
                ),
                encoding="utf-8",
            )
            catalog_path = package_dir / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "summary": "A small sales corpus.",
                        "datasets": [
                            {
                                "name": "orders",
                                "kind": "db_table",
                                "description": "Order revenue records.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            package_path = package_dir / "data_corpus_package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "vectordb": "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
                        "db": "postgresql://demo:demo@localhost:5432/data_corpus",
                        "schema": "schema.json",
                        "catalog": "catalog.json",
                    }
                ),
                encoding="utf-8",
            )
            argv = [
                "create_report.py",
                "--package",
                str(package_path),
                "--query",
                "Create a report about this data corpus",
            ]

            with (
                patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                module.main()

        rendered = stdout.getvalue()
        self.assertIn("# Data Intelligence Report", rendered)
        self.assertIn("A small sales corpus.", rendered)
        self.assertIn("## Datasets", rendered)
        self.assertIn("orders", rendered)


if __name__ == "__main__":
    unittest.main()
