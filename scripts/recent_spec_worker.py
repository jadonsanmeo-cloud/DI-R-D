"""Create scheduled dashboard-report Markdown specs from recent corpus documents."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from data_intelligence_api.infrastructure.corpus import PostgresRecentDocumentSource
from data_intelligence_sdk.runtime.logger import RuntimeLogger
from data_intelligence_sdk.scheduled_specs import (
    FilesystemScheduledSpecStore,
    RecentDocumentSpecWorker,
    ScheduledReportSpecPrompt,
)


class _WorkerLogger(RuntimeLogger):
    def __init__(self, stream: TextIO, *, verbose: bool) -> None:
        self.stream = stream
        self.verbose = verbose

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        safe_payload = payload or {}
        if not self.verbose:
            safe_payload = {
                key: value
                for key, value in safe_payload.items()
                if key.endswith("count") or key in {"document_id", "reason"}
            }
        print(
            json.dumps(
                {"event": event, "payload": safe_payload},
                ensure_ascii=True,
                default=str,
                sort_keys=True,
            ),
            file=self.stream,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create Markdown specs for the newest indexed corpus documents."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument("--organization-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--interval-seconds", type=float)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
    source_factory: Callable[[str], object] = PostgresRecentDocumentSource,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    environment = environ or os.environ
    output_stream = stream or sys.stderr
    logger = _WorkerLogger(output_stream, verbose=args.verbose)
    try:
        database_url = args.database_url or environment.get("CORPUS_DATABASE_URL", "")
        organization_id = args.organization_id or environment.get(
            "CORPUS_ORGANIZATION_ID", ""
        )
        output_dir = args.output_dir or environment.get(
            "RECENT_SPEC_OUTPUT_DIR", ".data/scheduled-report-specs"
        )
        limit = (
            args.limit
            if args.limit is not None
            else _environment_int(environment, "RECENT_SPEC_WORKER_LIMIT", 3)
        )
        interval_seconds = (
            args.interval_seconds
            if args.interval_seconds is not None
            else _environment_float(
                environment, "RECENT_SPEC_WORKER_INTERVAL_SECONDS", 900.0
            )
        )
        if not database_url.strip():
            raise ValueError("CORPUS_DATABASE_URL is required.")
        if not organization_id.strip():
            raise ValueError("CORPUS_ORGANIZATION_ID is required.")
        if limit <= 0 or interval_seconds <= 0:
            raise ValueError("Worker limit and interval must be positive.")
    except (TypeError, ValueError) as exc:
        logger.log(
            "scheduled_spec.configuration.failed", {"error_type": type(exc).__name__}
        )
        return 2

    worker = RecentDocumentSpecWorker(
        source=source_factory(database_url),  # type: ignore[arg-type]
        prompt=ScheduledReportSpecPrompt(),
        store=FilesystemScheduledSpecStore(Path(output_dir)),
        organization_id=organization_id,
        limit=limit,
        logger=logger,
    )

    if args.once:
        return _run_once(worker, logger)

    try:
        while True:
            _run_once(worker, logger)
            sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.log("scheduled_spec.worker.stopped")
        return 0


def _run_once(worker: RecentDocumentSpecWorker, logger: RuntimeLogger) -> int:
    try:
        result = worker.run_cycle()
    except Exception as exc:
        logger.log(
            "scheduled_spec.cycle.failed",
            {"error_type": type(exc).__name__},
        )
        return 1
    return 1 if result.failed else 0


def _environment_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    return int(environment.get(name, str(default)))


def _environment_float(
    environment: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    return float(environment.get(name, str(default)))


if __name__ == "__main__":
    raise SystemExit(main())
