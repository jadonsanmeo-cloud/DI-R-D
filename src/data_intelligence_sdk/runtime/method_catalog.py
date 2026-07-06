"""LLM-facing catalog generation for Method Hub registrations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.runtime.method_loader import load_manifest_directory

__all__ = ["build_catalog_payload", "read_catalog", "write_catalog"]


def build_catalog_payload(
    hub: MethodHub, *, executable_only: bool = True
) -> dict[str, Any]:
    """Return a deterministic JSON-serializable catalog payload."""

    return hub.build_llm_catalog(executable_only=executable_only)


def write_catalog(
    hub: MethodHub,
    output_path: str | Path,
    *,
    executable_only: bool = True,
) -> Path:
    """Write the catalog payload to disk as pretty-printed UTF-8 JSON."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_catalog_payload(hub, executable_only=executable_only)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_catalog(path: str | Path) -> dict[str, Any]:
    """Read a catalog payload back from disk."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Catalog file must contain a JSON object.")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Method Hub catalog.")
    parser.add_argument(
        "--manifest-dir",
        required=True,
        help="Directory containing YAML manifests to load into a temporary hub.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the JSON catalog to write.",
    )
    parser.add_argument(
        "--all-methods",
        action="store_true",
        help="Include non-executable methods in the catalog.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for building a catalog from manifests."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    hub = MethodHub()
    load_manifest_directory(hub, args.manifest_dir)
    write_catalog(hub, args.output, executable_only=not args.all_methods)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI convenience.
    raise SystemExit(main())
