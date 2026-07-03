from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_intelligence_sdk.datahub import NAPH_DATAHUB


class DataHubCatalogTests(unittest.TestCase):
    def test_current_datahub_is_a_static_catalog_of_parsed_files(self) -> None:
        self.assertEqual(NAPH_DATAHUB.metadata["concept"], "static_file_catalog")
        self.assertEqual(len(NAPH_DATAHUB.files), 9)
        self.assertEqual(len(NAPH_DATAHUB.list_files("csv")), 2)
        self.assertEqual(len(NAPH_DATAHUB.list_files("pdf")), 7)

    def test_csv_entries_have_links_schema_and_descriptions(self) -> None:
        open_data = NAPH_DATAHUB.get_file("csv:naph_10ar_open_data")

        self.assertEqual(open_data.link, "data_parsed/NAPH 10AR - Open Data.csv")
        self.assertIsNone(open_data.parsed_link)
        self.assertIn("NAPH open data table", open_data.description)
        self.assertEqual(open_data.metadata["row_count"], 91749)
        self.assertEqual(len(open_data.schema), 15)
        self.assertEqual(open_data.schema[0].name, "Section")

        specification = NAPH_DATAHUB.get_file("csv:naph_10ar_open_data_specification")
        self.assertEqual(
            specification.link, "data_parsed/NAPH 10AR - Open Data - Specification.csv"
        )
        self.assertIn("data dictionary", specification.description)
        self.assertEqual(
            [column.name for column in specification.schema],
            [
                "Field_Name",
                "Field_Type",
                "Field_Description",
                "Notes",
                "Field_Values",
            ],
        )

    def test_pdf_entries_have_links_to_parsed_markdown_and_descriptions(self) -> None:
        main_report = NAPH_DATAHUB.get_file("pdf:naph_10ar_main_report")

        self.assertEqual(
            main_report.link, "data_parsed/documents/naph_10ar_main_report.md"
        )
        self.assertEqual(
            main_report.parsed_link, "data_parsed/documents/naph_10ar_main_report.md"
        )
        self.assertEqual(
            main_report.metadata["source_file"], "NAPH 10AR - Main Report.pdf"
        )
        self.assertEqual(main_report.metadata["page_count"], 102)
        self.assertIn("annual report", main_report.description)

    def test_can_convert_to_pipeline_datahub_context(self) -> None:
        context = NAPH_DATAHUB.to_context()

        self.assertEqual(context.sources[0], "csv:naph_10ar_open_data_specification")
        self.assertIn("csv:naph_10ar_open_data", context.schemas)
        self.assertEqual(
            context.metadata["files"][0]["link"],
            "data_parsed/NAPH 10AR - Open Data - Specification.csv",
        )


if __name__ == "__main__":
    unittest.main()
