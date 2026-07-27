from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_intelligence_sdk.scheduled_specs.store import FilesystemScheduledSpecStore


class FilesystemScheduledSpecStoreTests(unittest.TestCase):
    def test_creates_spec_once_without_leaving_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            store = FilesystemScheduledSpecStore(output_dir)

            self.assertFalse(store.exists("doc-123"))
            destination = store.create("doc-123", "# First\n")

            self.assertTrue(store.exists("doc-123"))
            self.assertEqual(destination, output_dir / "doc-123.md")
            self.assertEqual(destination.read_text(encoding="utf-8"), "# First\n")
            self.assertEqual(list(output_dir.glob("*.tmp")), [])

            with self.assertRaises(FileExistsError):
                store.create("doc-123", "# Replacement\n")

            self.assertEqual(destination.read_text(encoding="utf-8"), "# First\n")

    def test_rejects_unsafe_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FilesystemScheduledSpecStore(Path(temp_dir))

            for document_id in ("", "   ", "../secret", "folder/doc", "folder\\doc"):
                with self.subTest(document_id=document_id):
                    with self.assertRaises(ValueError):
                        store.exists(document_id)


if __name__ == "__main__":
    unittest.main()
