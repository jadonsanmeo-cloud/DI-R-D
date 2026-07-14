import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_intelligence_sdk.methods.local_data import (
    aggregate_delimited_file,
    filter_delimited_rows,
    inspect_data_folder,
    profile_delimited_file,
    register_local_data_methods,
    search_text_files,
    summarize_delimited_columns,
    summarize_wide_numeric_table,
)
from data_intelligence_sdk.runtime.method_hub import MethodHub


class LocalDataMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        documents = self.root / "documents"
        documents.mkdir()
        (self.root / "records.csv").write_text(
            "\n".join(
                [
                    "category,status,value,score",
                    "A,complete,10,0.9",
                    "A,pending,5,0.5",
                    "B,complete,7,0.7",
                    "B,complete,,0.8",
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "wide.tsv").write_text(
            "Year\tNorth\tSouth\tTotal\n2024\t10\t20\t30\n2023\t5\t25\t30\n",
            encoding="utf-8",
        )
        (documents / "notes.md").write_text(
            "# Notes\n\nThe complete records describe category performance.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_inspect_data_folder_returns_generic_inventory(self) -> None:
        result = inspect_data_folder(str(self.root))

        self.assertEqual(result["file_count"], 3)
        kinds = {row["kind"] for row in result["rows"]}
        self.assertIn("table", kinds)
        self.assertIn("document", kinds)

    def test_profile_delimited_file_profiles_any_csv(self) -> None:
        result = profile_delimited_file("records.csv", str(self.root))

        self.assertEqual(result["row_count"], 4)
        self.assertIn("value", result["numeric_columns"])
        self.assertIn("category", result["categorical_columns"])

    def test_summarize_delimited_columns_returns_top_values_and_numeric_stats(self) -> None:
        result = summarize_delimited_columns(
            "records.csv",
            columns=["category", "value"],
            data_root=str(self.root),
        )

        self.assertEqual(result["summarized_columns"], ["category", "value"])
        self.assertTrue(
            any(row["record_type"] == "top_value" and row["value"] == "A" for row in result["rows"])
        )
        self.assertTrue(
            any(row["record_type"] == "numeric_summary" and row["column"] == "value" for row in result["rows"])
        )

    def test_aggregate_delimited_file_groups_and_counts(self) -> None:
        result = aggregate_delimited_file(
            "records.csv",
            group_by="category",
            data_root=str(self.root),
        )

        counts = {row["group_value"]: row["row_count"] for row in result["rows"]}
        self.assertEqual(counts["A"], 2)
        self.assertEqual(counts["B"], 2)

    def test_filter_delimited_rows_searches_table_text(self) -> None:
        result = filter_delimited_rows(
            "records.csv",
            contains="pending",
            data_root=str(self.root),
        )

        self.assertEqual(result["matched_count_returned"], 1)
        self.assertEqual(result["rows"][0]["status"], "pending")

    def test_search_text_files_searches_markdown(self) -> None:
        result = search_text_files("complete performance", str(self.root))

        self.assertEqual(result["matched_document_count"], 1)
        self.assertEqual(result["rows"][0]["document"], "notes.md")

    def test_summarize_wide_numeric_table_handles_generic_wide_tsv(self) -> None:
        result = summarize_wide_numeric_table("wide.tsv", data_root=str(self.root))

        self.assertEqual(result["id_column"], "Year")
        self.assertIn("North", result["measure_columns"])
        self.assertEqual(result["rows"][0]["top_measure"], "Total")

    def test_register_local_data_methods_uses_generic_method_names(self) -> None:
        hub = MethodHub()

        register_local_data_methods(hub)

        method_names = {method.name for method in hub.list_methods()}
        self.assertIn("profile_delimited_file", method_names)
        self.assertIn("summarize_delimited_columns", method_names)
        self.assertIn("search_text_files", method_names)
        self.assertIn(
            "data_folder_report",
            hub.get_definition("inspect_data_folder").capability_names,
        )


if __name__ == "__main__":
    unittest.main()
