import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.methods.csv import (
    count_csv,
    filter_csv,
    register_csv_methods,
    scan_csv,
    sum_csv,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


class CsvMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "sales.csv"
        self.csv_path.write_text(
            "country,status,revenue\n"
            "US,complete,10.5\n"
            "US,pending,4.5\n"
            "CA,complete,7\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scan_csv_returns_columns_count_and_sample_rows(self) -> None:
        result = scan_csv(str(self.csv_path), limit=2)

        self.assertEqual(result["path"], str(self.csv_path))
        self.assertEqual(result["columns"], ["country", "status", "revenue"])
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(len(result["sample_rows"]), 2)

    def test_count_csv_counts_all_rows_or_filtered_rows(self) -> None:
        self.assertEqual(
            count_csv(str(self.csv_path)), {"path": str(self.csv_path), "count": 3}
        )
        self.assertEqual(
            count_csv(str(self.csv_path), "status", "eq", "complete")["count"], 2
        )

    def test_sum_csv_sums_all_rows_or_filtered_rows(self) -> None:
        self.assertEqual(sum_csv(str(self.csv_path), "revenue")["sum"], 22.0)
        self.assertEqual(
            sum_csv(str(self.csv_path), "revenue", "country", "eq", "US")["sum"], 15.0
        )

    def test_filter_csv_returns_matching_rows(self) -> None:
        result = filter_csv(str(self.csv_path), "country", "eq", "US")

        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["status"], "complete")

    def test_csv_methods_raise_for_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            filter_csv(str(self.csv_path), "missing", "eq", "x")
        with self.assertRaises(ValueError):
            filter_csv(str(self.csv_path), "country", "bad_op", "US")
        with self.assertRaises(ValueError):
            sum_csv(str(self.csv_path), "country")

    def test_register_csv_methods_registers_expected_methods(self) -> None:
        method_hub = MethodHub()

        register_csv_methods(method_hub)

        method_names = {method.name for method in method_hub.list_methods()}
        self.assertEqual(
            method_names, {"scan_csv", "filter_csv", "count_csv", "sum_csv"}
        )
        self.assertIn(
            "answer_csv_question",
            method_hub.get_definition("scan_csv").capability_names,
        )


if __name__ == "__main__":
    unittest.main()
