import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from data_intelligence_sdk.methods.local_data import extract_pdf_text


class LocalPdfMethodTests(unittest.TestCase):
    def test_extract_pdf_text_returns_page_level_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with path.open("wb") as output:
                writer.write(output)

            result = extract_pdf_text(str(path))

        self.assertEqual(result["source"], str(path))
        self.assertEqual(result["page_count"], 1)
        self.assertEqual(result["extracted_page_count"], 1)
        self.assertEqual(
            result["rows"][0],
            {
                "page_number": 1,
                "text": "",
                "character_count": 0,
                "truncated": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
