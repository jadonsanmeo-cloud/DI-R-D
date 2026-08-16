from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from scripts.demo_recent_spec_worker import main


class DemoRecentSpecWorkerTests(unittest.TestCase):
    def test_demo_explains_sliding_windows_and_creates_only_missing_specs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "specs"
            stream = io.StringIO()
            sleeps: list[float] = []

            exit_code = main(
                [
                    "--output-dir",
                    str(output_dir),
                    "--interval-seconds",
                    "2",
                ],
                stream=stream,
                sleep=sleeps.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(sleeps, [2.0, 2.0])
            self.assertEqual(
                sorted(path.name for path in output_dir.glob("*.md")),
                ["C.md", "D.md", "E.md", "F.md", "G.md", "H.md"],
            )
            logs = stream.getvalue()
            self.assertIn("CYCLE 1", logs)
            self.assertIn("Newest window: C, D, E", logs)
            self.assertIn("created=3 skipped=0 failed=0", logs)
            self.assertIn("CYCLE 2", logs)
            self.assertIn("Newest window: D, E, F", logs)
            self.assertIn("SKIPPED D: spec_exists", logs)
            self.assertIn("SKIPPED E: spec_exists", logs)
            self.assertIn("created=1 skipped=2 failed=0", logs)
            self.assertIn("CYCLE 3", logs)
            self.assertIn("Newest window: F, G, H", logs)
            self.assertIn("SKIPPED F: spec_exists", logs)
            self.assertIn("created=2 skipped=1 failed=0", logs)
            self.assertIn("Worker execution stops after spec creation", logs)

    def test_demo_rejects_a_non_empty_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "specs"
            output_dir.mkdir()
            (output_dir / "old.md").write_text("old", encoding="utf-8")
            stream = io.StringIO()

            exit_code = main(
                ["--output-dir", str(output_dir)],
                stream=stream,
                sleep=lambda _: None,
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("Output directory must be empty", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
