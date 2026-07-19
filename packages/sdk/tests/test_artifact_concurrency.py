import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.core.types import DataCorpusPackage, UserQuery
from data_intelligence_sdk.sandbox.artifacts import RunArtifactSession


class ArtifactConcurrencyTests(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_permission_error(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "manifest.json"
            real_replace = __import__("os").replace
            calls = 0

            def flaky_replace(source, target):
                nonlocal calls
                calls += 1
                if calls < 3:
                    raise PermissionError(5, "Access is denied")
                return real_replace(source, target)

            with patch(
                "data_intelligence_sdk.sandbox.artifacts.os.replace",
                side_effect=flaky_replace,
            ):
                RunArtifactSession(
                    run_id="00000000-0000-0000-0000-000000000004",
                    root=Path(directory),
                )._write_text_atomic(destination, "{}\n")

            self.assertEqual(calls, 3)
            self.assertEqual(destination.read_text(encoding="utf-8"), "{}\n")

    def test_rendered_report_assets_are_persisted_and_manifested(self):
        with tempfile.TemporaryDirectory() as directory:
            session = RunArtifactSession.create(
                run_id="00000000-0000-0000-0000-000000000003",
                root=Path(directory) / "run",
                query=UserQuery(text="test"),
                corpus_package=DataCorpusPackage(),
            )

            css = session.record_rendered_report(
                "css",
                "text/css",
                "body { color: black; }",
            )
            javascript = session.record_rendered_report(
                "javascript",
                "application/javascript",
                "console.log('report');",
            )
            manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(
                css.path.read_text(encoding="utf-8"),
                "body { color: black; }",
            )
            self.assertEqual(
                javascript.path.read_text(encoding="utf-8"),
                "console.log('report');",
            )
            self.assertEqual(
                {item["format"] for item in manifest["rendered_reports"]},
                {"css", "javascript"},
            )

    def test_parallel_events_and_code_attempts_keep_manifest_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            session = RunArtifactSession.create(
                run_id="00000000-0000-0000-0000-000000000002",
                root=Path(directory) / "run",
                query=UserQuery(text="test"),
                corpus_package=DataCorpusPackage(),
            )

            def record_event(index):
                session.record_event(
                    phase="parallel",
                    event_type="parallel.event",
                    payload={"index": index},
                )

            def record_attempt(index):
                session.record_code_attempt(f"result = {index}\n")

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(record_event, range(40)))
                list(executor.map(record_attempt, range(12)))

            events = [
                json.loads(line)
                for line in session.events_path.read_text(encoding="utf-8").splitlines()
            ]
            manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(
                [event["sequence"] for event in events],
                list(range(1, len(events) + 1)),
            )
            self.assertEqual(manifest["event_count"], len(events))
            self.assertEqual(
                [item["attempt"] for item in manifest["attempts"]],
                list(range(1, 13)),
            )
            self.assertEqual(len(list((session.root / "code").glob("*.py"))), 12)


if __name__ == "__main__":
    unittest.main()
