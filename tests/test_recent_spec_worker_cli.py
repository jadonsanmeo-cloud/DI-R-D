from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from scripts.recent_spec_worker import main


class EmptySource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def load_recent(self, *, organization_id: str, limit: int) -> list[object]:
        self.calls.append((organization_id, limit))
        return []


class RecentSpecWorkerCliTests(unittest.TestCase):
    def test_once_uses_environment_defaults_and_runs_one_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = EmptySource()
            factory_urls: list[str] = []

            def source_factory(database_url: str) -> EmptySource:
                factory_urls.append(database_url)
                return source

            exit_code = main(
                ["--once"],
                environ={
                    "CORPUS_DATABASE_URL": "postgresql://secret@localhost/corpus",
                    "CORPUS_ORGANIZATION_ID": "test-org",
                    "RECENT_SPEC_OUTPUT_DIR": temp_dir,
                },
                stream=io.StringIO(),
                source_factory=source_factory,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(factory_urls, ["postgresql://secret@localhost/corpus"])
            self.assertEqual(source.calls, [("test-org", 3)])
            self.assertEqual(list(Path(temp_dir).glob("*.md")), [])

    def test_missing_required_configuration_is_safe(self) -> None:
        stream = io.StringIO()

        exit_code = main(
            ["--once"],
            environ={"CORPUS_DATABASE_URL": "postgresql://secret@localhost/corpus"},
            stream=stream,
            source_factory=lambda database_url: EmptySource(),
        )

        self.assertNotEqual(exit_code, 0)
        self.assertIn("configuration.failed", stream.getvalue())
        self.assertNotIn("postgresql://secret", stream.getvalue())

    def test_explicit_non_positive_limit_is_rejected(self) -> None:
        stream = io.StringIO()

        exit_code = main(
            ["--once", "--limit", "0"],
            environ={
                "CORPUS_DATABASE_URL": "postgresql://localhost/corpus",
                "CORPUS_ORGANIZATION_ID": "test-org",
            },
            stream=stream,
            source_factory=lambda database_url: EmptySource(),
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("configuration.failed", stream.getvalue())

    def test_loop_waits_default_900_seconds_and_exits_on_interrupt(self) -> None:
        source = EmptySource()
        sleeps: list[float] = []

        def interrupting_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = main(
                [],
                environ={
                    "CORPUS_DATABASE_URL": "postgresql://localhost/corpus",
                    "CORPUS_ORGANIZATION_ID": "test-org",
                    "RECENT_SPEC_OUTPUT_DIR": temp_dir,
                },
                stream=io.StringIO(),
                source_factory=lambda database_url: source,
                sleep=interrupting_sleep,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(source.calls, [("test-org", 3)])
        self.assertEqual(sleeps, [900.0])


if __name__ == "__main__":
    unittest.main()
