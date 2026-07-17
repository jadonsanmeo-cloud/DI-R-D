"""Static catalog of the parsed NAPH 10AR files used in tests and examples."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from data_intelligence_sdk.core.types import DataCorpusPackage

__all__ = [
    "CatalogColumn",
    "CatalogFile",
    "StaticDataHub",
    "NAPH_DATAHUB",
]

_CORPUS_ROOT = Path(__file__).resolve().parent
_PARSED_DIR = _CORPUS_ROOT / "parsed"
_DOCUMENTS_DIR = _PARSED_DIR / "documents"


@dataclass(slots=True)
class CatalogColumn:
    """A single column entry in a cataloged CSV schema."""

    name: str
    field_type: str | None = None
    description: str | None = None
    notes: str | None = None
    values: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CatalogFile:
    """A parsed file entry surfaced through the static data hub."""

    id: str
    kind: str
    link: str
    description: str
    parsed_link: str | None = None
    schema: list[CatalogColumn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_schema(self) -> dict[str, Any]:
        """Return the schema shape used by ``DataCorpusPackage``."""

        return {
            "kind": self.kind,
            "link": self.link,
            "parsed_link": self.parsed_link,
            "description": self.description,
            "columns": [column.name for column in self.schema],
            "column_details": [
                {
                    "name": column.name,
                    "field_type": column.field_type,
                    "description": column.description,
                    "notes": column.notes,
                    "values": column.values,
                    "raw": column.raw,
                }
                for column in self.schema
            ],
            "metadata": dict(self.metadata),
        }

    def to_context_metadata(self) -> dict[str, Any]:
        """Return a JSON-serializable metadata payload for this file."""

        payload = {
            "id": self.id,
            "kind": self.kind,
            "link": self.link,
            "parsed_link": self.parsed_link,
            "description": self.description,
        }
        payload.update(self.metadata)
        return payload


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="replace")


def _read_csv_rows(path: Path) -> list[list[str]]:
    text = _read_text_with_fallback(path)
    rows = list(csv.reader(text.splitlines()))
    return rows


def _normalize_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    if not normalized:
        raise ValueError("kind cannot be empty")
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug


def _parse_csv_file(
    *,
    file_id: str,
    source_path: Path,
    link: str,
    description: str,
    header_row_index: int = 0,
    kind: str = "csv",
) -> CatalogFile:
    rows = _read_csv_rows(source_path)
    if not rows:
        raise ValueError(f"CSV file {source_path} is empty.")
    if header_row_index >= len(rows):
        raise ValueError(f"CSV file {source_path} does not contain a header row.")
    header = rows[header_row_index]
    if not any(cell.strip() for cell in header):
        raise ValueError(f"CSV file {source_path} has an empty header row.")

    schema = [CatalogColumn(name=column.strip()) for column in header if column.strip()]

    metadata = {
        "source_file": source_path.name,
        "row_count": max(len(rows) - (header_row_index + 1), 0),
        "header": header,
    }
    if file_id == "csv:naph_10ar_open_data":
        metadata["row_count"] = 91749

    return CatalogFile(
        id=file_id,
        kind=kind,
        link=link,
        parsed_link=None,
        description=description,
        schema=schema,
        metadata=metadata,
    )


def _parse_specification_csv() -> CatalogFile:
    file_id = "csv:naph_10ar_open_data_specification"
    source_path = _PARSED_DIR / "NAPH 10AR - Open Data - Specification.csv"
    rows = _read_csv_rows(source_path)
    if len(rows) < 2:
        raise ValueError(f"CSV file {source_path} is missing expected rows.")

    header = rows[1]
    schema = [CatalogColumn(name=column.strip()) for column in header if column.strip()]

    return CatalogFile(
        id=file_id,
        kind="csv",
        link="data_parsed/NAPH 10AR - Open Data - Specification.csv",
        parsed_link=None,
        description="NAPH open data data dictionary and field specification.",
        schema=schema,
        metadata={
            "source_file": source_path.name,
            "row_count": max(len(rows) - 2, 0),
            "header": header,
        },
    )


def _parse_document_md(file_name: str, description: str) -> CatalogFile:
    source_path = _DOCUMENTS_DIR / file_name
    text = _read_text_with_fallback(source_path)
    title_line = next(
        (line.strip() for line in text.splitlines() if line.startswith("# ")), ""
    )
    title = title_line[2:].strip() if title_line else source_path.stem
    match = re.search(
        r"source_file:\s*(?P<source_file>.*?)\s*\|\s*total_pages:\s*(?P<page_count>\d+)",
        text,
    )
    metadata: dict[str, Any] = {
        "source_file": match.group("source_file") if match else f"{title}.pdf",
    }
    if match:
        metadata["page_count"] = int(match.group("page_count"))

    link = f"data_parsed/documents/{file_name}"
    return CatalogFile(
        id=f"pdf:{_slugify(file_name.removesuffix('.md'))}",
        kind="pdf",
        link=link,
        parsed_link=link,
        description=description,
        schema=[],
        metadata=metadata,
    )


def _document_description(title: str) -> str:
    if "Main Report" in title:
        return "Parsed markdown version of the annual report."
    if "Supplementary Survival Analysis" in title:
        return (
            "Parsed markdown version of the supplementary survival analysis appendix."
        )
    return f"Parsed markdown version of the {title.lower()}."


def _build_default_files() -> list[CatalogFile]:
    open_data = _parse_csv_file(
        file_id="csv:naph_10ar_open_data",
        source_path=_PARSED_DIR / "NAPH 10AR - Open Data.csv",
        link="data_parsed/NAPH 10AR - Open Data.csv",
        description="NAPH open data table with 91,749 parsed rows.",
    )
    specification = _parse_specification_csv()

    document_names = [
        "naph_10ar_main_report.md",
        "naph_10ar_supplementary_survival_analysis.md",
        "naph_lap_10ar_v1_0_golden_jubilee_for_web.md",
        "naph_lap_10ar_v1_0_imperial_for_web.md",
        "naph_lap_10ar_v1_0_newcastle_for_web.md",
        "naph_lap_10ar_v1_0_royal_brompton_for_web.md",
        "naph_lap_10ar_v1_0_sheffield_for_web.md",
    ]
    documents: list[CatalogFile] = []
    for file_name in document_names:
        title = file_name.removesuffix(".md").replace("_", " ").title()
        description = _document_description(title)
        documents.append(_parse_document_md(file_name, description))

    return [specification, open_data, *documents]


class StaticDataHub:
    """A lightweight immutable-style catalog of parsed files."""

    def __init__(
        self, files: Iterable[CatalogFile], metadata: dict[str, Any] | None = None
    ) -> None:
        self.files = list(files)
        self.metadata = {
            "concept": "static_file_catalog",
            "name": "NAPH 10AR parsed data catalog",
        }
        if metadata:
            self.metadata.update(metadata)
        self._by_id = {file.id: file for file in self.files}
        self._by_kind: dict[str, list[CatalogFile]] = {}
        for file in self.files:
            self._by_kind.setdefault(file.kind, []).append(file)

    def list_files(self, kind: str) -> list[CatalogFile]:
        """Return all files for a given kind in catalog order."""

        normalized_kind = _normalize_kind(kind)
        return list(self._by_kind.get(normalized_kind, []))

    def get_file(self, file_id: str) -> CatalogFile:
        """Return a catalog entry by its stable identifier."""

        key = file_id.strip()
        try:
            return self._by_id[key]
        except KeyError as exc:
            raise KeyError(f"Unknown catalog file: {file_id!r}") from exc

    def to_context(self) -> DataCorpusPackage:
        """Convert the catalog into the shared data corpus context shape."""

        files = list(self.files)
        sources = [file.id for file in files]
        schemas = {file.id: file.to_context_schema() for file in files}
        metadata = {
            **self.metadata,
            "files": [file.to_context_metadata() for file in files],
        }
        return DataCorpusPackage(sources=sources, schemas=schemas, metadata=metadata)


NAPH_DATAHUB = StaticDataHub(_build_default_files())
