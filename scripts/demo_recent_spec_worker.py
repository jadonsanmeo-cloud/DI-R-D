"""Demonstrate scheduled-spec sliding windows without external services."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.scheduled_specs import (
    FilesystemScheduledSpecStore,
    RecentDocument,
    RecentDocumentSpecWorker,
    ScheduledReportSpecPrompt,
)


class DemoDocumentSource:
    """Mutable in-memory corpus used only to demonstrate ingest cycles."""

    def __init__(self, documents: list[RecentDocument]) -> None:
        self.documents = list(documents)

    def ingest(self, *documents: RecentDocument) -> None:
        self.documents.extend(documents)

    def load_recent(
        self,
        *,
        organization_id: str,
        limit: int,
    ) -> list[RecentDocument]:
        matching = [
            document
            for document in self.documents
            if document.organization_id == organization_id
        ]
        return sorted(
            matching,
            key=lambda item: (item.ingested_at, item.document_id),
            reverse=True,
        )[:limit]


class DemoLogger(RuntimeLogger):
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        values = payload or {}
        if event == "scheduled_spec.document.created":
            self.write(
                f"  CREATED {values['document_id']} -> "
                f"{Path(str(values['output_path'])).name}"
            )
        elif event == "scheduled_spec.document.skipped":
            self.write(f"  SKIPPED {values['document_id']}: {values['reason']}")
        elif event == "scheduled_spec.document.failed":
            self.write(f"  FAILED {values['document_id']}: {values['error_type']}")

    def write(self, message: str = "") -> None:
        print(message, file=self.stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Demonstrate how the scheduled worker handles three-document "
            "sliding windows."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=".data/demo-scheduled-specs",
        help="Empty directory where demonstration Markdown specs are written.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Pause between simulated ingest cycles (default: 2 seconds).",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stream: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    output_stream = stream or sys.stdout
    logger = DemoLogger(output_stream)
    output_dir = Path(args.output_dir)

    if args.interval_seconds <= 0:
        logger.write("ERROR: --interval-seconds must be positive.")
        return 2
    if output_dir.exists() and any(output_dir.iterdir()):
        logger.write(
            f"ERROR: Output directory must be empty: {output_dir}. "
            "Use a new directory or remove the previous demo output."
        )
        return 2

    base_time = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
    source = DemoDocumentSource(
        [_document(letter, base_time, offset) for offset, letter in enumerate("ABCDE")]
    )
    worker = RecentDocumentSpecWorker(
        source=source,
        prompt=ScheduledReportSpecPrompt(),
        store=FilesystemScheduledSpecStore(output_dir),
        organization_id="test-org",
        limit=3,
        logger=logger,
    )

    logger.write("Scheduled Spec Worker Sliding-Window Demo")
    logger.write("Organization: test-org | Window size: 3")
    logger.write(f"Output: {output_dir.resolve()}")
    logger.write(
        "This demo creates Markdown specs only. It does not run Report Engine "
        "or generate reports."
    )

    _run_cycle(
        number=1,
        description="Initial corpus contains five documents: A, B, C, D, E",
        source=source,
        worker=worker,
        output_dir=output_dir,
        logger=logger,
    )

    _wait(args.interval_seconds, logger, sleep)
    source.ingest(_document("F", base_time, 5))
    _run_cycle(
        number=2,
        description="Ingested one new document: F",
        source=source,
        worker=worker,
        output_dir=output_dir,
        logger=logger,
    )

    _wait(args.interval_seconds, logger, sleep)
    source.ingest(
        _document("G", base_time, 6),
        _document("H", base_time, 7),
    )
    _run_cycle(
        number=3,
        description="Ingested two new documents: G, H",
        source=source,
        worker=worker,
        output_dir=output_dir,
        logger=logger,
    )

    logger.write()
    logger.write("DEMO COMPLETED")
    logger.write("Worker execution stops after spec creation; no reports were run.")
    logger.write(
        "Final specs: "
        + ", ".join(path.name for path in sorted(output_dir.glob("*.md")))
    )
    return 0


def _run_cycle(
    *,
    number: int,
    description: str,
    source: DemoDocumentSource,
    worker: RecentDocumentSpecWorker,
    output_dir: Path,
    logger: DemoLogger,
) -> None:
    window = source.load_recent(organization_id="test-org", limit=3)
    chronological_window = sorted(
        window,
        key=lambda document: (document.ingested_at, document.document_id),
    )
    logger.write()
    logger.write(f"=== CYCLE {number} ===")
    logger.write(description)
    logger.write(
        "Newest window: "
        + ", ".join(document.document_id for document in chronological_window)
    )
    logger.write("Worker decisions:")
    result = worker.run_cycle()
    logger.write(
        "SUMMARY "
        f"loaded={result.loaded} created={result.created} "
        f"skipped={result.skipped} failed={result.failed}"
    )
    logger.write(
        "SPECS " + ", ".join(path.name for path in sorted(output_dir.glob("*.md")))
    )


def _wait(
    interval_seconds: float,
    logger: DemoLogger,
    sleep: Callable[[float], None],
) -> None:
    logger.write()
    logger.write(f"Waiting {interval_seconds:g} seconds for the next worker cycle...")
    sleep(interval_seconds)


def _document(
    document_id: str,
    base_time: datetime,
    minute_offset: int,
) -> RecentDocument:
    return RecentDocument(
        document_id=document_id,
        organization_id="test-org",
        file_name=f"report-{document_id}.pdf",
        source_uri=f"s3://axiom-uploads/test-org/report-{document_id}.pdf",
        ingested_at=base_time + timedelta(minutes=minute_offset),
    )


if __name__ == "__main__":
    raise SystemExit(main())
