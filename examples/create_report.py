"""Create a basic report from a DataCorpusPackage.

Examples:
    uv run python examples/create_report.py
    uv run python examples/create_report.py --package examples/data_corpus_package/data_corpus_package.json
    uv run python examples/create_report.py --source sales.csv --query "Create a report"
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

EXAMPLES_DIR = Path(__file__).resolve().parent
load_dotenv(EXAMPLES_DIR.parent / ".env", override=False)
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from data_intelligence_sdk import DataCorpusPackage, UserQuery
from data_intelligence_sdk.runtime import FileRuntimeLogger
from basic_workflow import create_report_pipeline

DEFAULT_PACKAGE = EXAMPLES_DIR / "data_corpus_package" / "data_corpus_package.json"


def _resolve_package_ref(package_path: Path, ref: str) -> str:
    if urlparse(ref).scheme:
        return ref
    path = Path(ref)
    if not path.is_absolute():
        path = package_path.parent / path
    return str(path)


def _load_package_json(path: str) -> DataCorpusPackage:
    package_path = Path(path)
    package_payload = json.loads(package_path.read_text(encoding="utf-8"))

    vectordb_path = _resolve_package_ref(package_path, package_payload["vectordb"])
    db_path = _resolve_package_ref(package_path, package_payload["db"])
    schema_path = _resolve_package_ref(package_path, package_payload["schema"])
    catalog_path = _resolve_package_ref(package_path, package_payload["catalog"])

    schemas = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))

    return DataCorpusPackage(
        sources=[vectordb_path, db_path],
        schemas=schemas,
        metadata={
            "catalog": catalog,
            "package": {
                "vectordb": vectordb_path,
                "db": db_path,
                "schema": schema_path,
                "catalog": catalog_path,
            },
        },
    )


def _render_report_markdown(report: dict[str, object]) -> str:
    title = str(report.get("title", "Report"))
    summary = str(report.get("summary", ""))
    lines = [f"# {title}", "", summary]
    sections = report.get("sections", [])
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading", "Section"))
            content = str(section.get("content", ""))
            lines.extend(["", f"## {heading}", "", content])
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Create a basic report from a DataCorpusPackage."
    )
    parser.add_argument(
        "--package",
        dest="package_path",
        default=str(DEFAULT_PACKAGE) if DEFAULT_PACKAGE.exists() else None,
        help="Path to a package manifest with vectordb, db, schema, and catalog refs.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Source reference to include when no package is provided. Can be repeated.",
    )
    parser.add_argument(
        "--query",
        default="Create a report about this data corpus.",
        help="Report request to send through the pipeline.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Write structured pipeline trace events. Enabled by default.",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable structured pipeline trace logging.",
    )
    parser.add_argument(
        "--trace-log-path",
        default="logs/pipeline.log",
        help="Path used for structured pipeline trace events.",
    )
    args = parser.parse_args()

    if args.sources:
        corpus_package = DataCorpusPackage(sources=list(args.sources or []))
    elif args.package_path:
        corpus_package = _load_package_json(args.package_path)
    else:
        corpus_package = DataCorpusPackage()

    pipeline = create_report_pipeline(
        logger=None if args.no_trace else FileRuntimeLogger(args.trace_log_path)
    )
    response = pipeline.run(UserQuery(args.query), corpus_package)
    report = response.answer
    if isinstance(report, str):
        if report.lstrip().lower().startswith(("<!doctype html", "<html")):
            print(report)
            return
        try:
            report_payload = ast.literal_eval(report)
        except (SyntaxError, ValueError):
            print(report)
            return
    else:
        report_payload = report
    if not isinstance(report_payload, dict):
        print(str(report_payload))
        return
    print(_render_report_markdown(report_payload), end="")


if __name__ == "__main__":
    main()
