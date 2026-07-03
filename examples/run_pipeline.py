"""Run the default Data Intelligence pipeline against a DataCorpusPackage.

Examples:
    uv run python examples/run_pipeline.py
    uv run python examples/run_pipeline.py --source sales.csv --query "What is the total revenue?"
    uv run python examples/run_pipeline.py --source sales.csv --source customers.csv --query "What sources are available?"
    uv run python examples/run_pipeline.py --package examples/data_corpus_package/data_corpus_package.json --query "Summarize this package"

Package JSON shape:
    {"vectordb": "vectordb", "db": "warehouse.db", "schema": "schema.json", "catalog": "catalog.json"}

Requires OPENROUTER_API_KEY and LLM_MODEL_NAME in the environment or .env.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from data_intelligence_sdk import DataCorpusPackage, UserQuery
from basic_workflow import create_example_pipeline

load_dotenv()


SAMPLE_CSV = """country,status,revenue
US,complete,10.5
US,pending,4.5
CA,complete,7
"""


def _create_sample_csv() -> tuple[tempfile.TemporaryDirectory[str], str]:
    temp_dir = tempfile.TemporaryDirectory()
    csv_path = Path(temp_dir.name) / "sales.csv"
    csv_path.write_text(SAMPLE_CSV, encoding="utf-8")
    return temp_dir, str(csv_path)


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Data Intelligence SDK pipeline."
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Source reference to include in the DataCorpusPackage. Can be repeated.",
    )
    parser.add_argument(
        "--package",
        dest="package_path",
        help="Path to a package manifest with vectordb, db, schema, and catalog refs.",
    )
    parser.add_argument(
        "--query",
        default="What is the total revenue?",
        help="Question to ask about the data corpus.",
    )
    args = parser.parse_args()

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.package_path:
        corpus_package = _load_package_json(args.package_path)
    else:
        sources = list(args.sources or [])
        if not sources:
            temp_dir, sample_csv_path = _create_sample_csv()
            sources.append(sample_csv_path)
        corpus_package = DataCorpusPackage(sources=sources)

    try:
        pipeline = create_example_pipeline()

        response = pipeline.run(
            UserQuery(args.query),
            corpus_package,
        )

        print(response.answer)
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    main()
