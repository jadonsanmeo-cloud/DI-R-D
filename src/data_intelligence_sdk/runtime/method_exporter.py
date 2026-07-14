"""Export method bundles for parent Method Hubs."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from data_intelligence_sdk.core.errors import DataIntelligenceError
from data_intelligence_sdk.runtime.method_hub import MethodHub, RegisteredMethod

__all__ = [
    "MethodBundle",
    "MethodExportError",
    "export_method_bundle",
    "validate_bundle",
]

_BUNDLE_FORMAT = "method-bundle-v1"
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class MethodExportError(DataIntelligenceError):
    """Raised when a bundle cannot be exported or validated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(name: str) -> str:
    normalized = _SAFE_NAME_PATTERN.sub("_", name.strip())
    normalized = normalized.strip("._-")
    if not normalized:
        raise MethodExportError("Method name cannot be converted into a safe bundle name.")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            if not isinstance(key, str):
                continue
            safe = _json_safe_value(value[key])
            if safe is not None or value[key] is None:
                payload[key] = safe
        return payload
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for item in value:
            safe = _json_safe_value(item)
            if safe is not None or item is None:
                items.append(safe)
        return items
    return None


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items
    return []


@dataclass(slots=True)
class MethodBundle:
    """Metadata snapshot for an exported method bundle."""

    method_name: str
    version: str
    status: str
    trust_level: str
    priority: int
    description: str
    capability_names: list[str]
    tags: list[str]
    source: str | None
    created_at: str = field(default_factory=_utc_now)
    tests_passed: bool | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _BUNDLE_FORMAT,
            "method_name": self.method_name,
            "version": self.version,
            "status": self.status,
            "trust_level": self.trust_level,
            "priority": self.priority,
            "description": self.description,
            "capability_names": list(self.capability_names),
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "tests_passed": self.tests_passed,
            "files": deepcopy(self.files),
            "manifest": deepcopy(self.manifest),
        }


def _synthesize_manifest(definition: RegisteredMethod) -> dict[str, Any]:
    metadata = _json_safe_value(definition.metadata)
    if metadata is None:
        metadata = {}
    manifest = {
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "entrypoint": f"{definition.method.__module__}:{getattr(definition.method, '__name__', definition.name)}",
        "capability_names": list(definition.capability_names),
        "tags": list(definition.tags),
        "use_when": _text_list(definition.metadata.get("use_when")),
        "do_not_use_when": _text_list(definition.metadata.get("do_not_use_when")),
        "trust_level": definition.trust_level,
        "status": definition.status,
        "priority": definition.priority,
        "metadata": metadata,
        "source": definition.source,
    }
    return manifest


def _write_source_snapshot(definition: RegisteredMethod, output_dir: Path) -> Path:
    method = definition.method
    source_path = inspect.getsourcefile(method) or inspect.getfile(method)
    destination = output_dir / "source.py"
    if source_path and Path(source_path).exists():
        shutil.copy2(source_path, destination)
        return destination
    try:
        source_text = inspect.getsource(method)
    except (OSError, TypeError) as exc:
        raise MethodExportError(
            f"{definition.name}: unable to determine source for bundle export."
        ) from exc
    destination.write_text(
        "# Generated source snapshot for exported method\n" + source_text,
        encoding="utf-8",
    )
    return destination


def _write_manifest_snapshot(manifest: dict[str, Any], output_dir: Path) -> Path:
    path = output_dir / "method.yaml"
    path.write_text(
        yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _build_bundle_manifest(definition: RegisteredMethod) -> dict[str, Any]:
    manifest = _synthesize_manifest(definition)
    manifest["metadata"] = _json_safe_value(manifest["metadata"]) or {}
    return manifest


def export_method_bundle(
    hub: MethodHub,
    method_name: str,
    output_dir: str | Path,
    *,
    tests_passed: bool | None = None,
) -> Path:
    """Export a method bundle containing source, manifest, and checksum metadata."""

    definition = hub.get_definition(method_name)
    bundle_root = Path(output_dir).resolve() / _safe_name(definition.name)
    bundle_root.mkdir(parents=True, exist_ok=True)

    manifest = _build_bundle_manifest(definition)
    manifest_path = _write_manifest_snapshot(manifest, bundle_root)
    source_path = _write_source_snapshot(definition, bundle_root)

    files = []
    for file_path in (manifest_path, source_path):
        files.append(
            {
                "path": file_path.name,
                "sha256": _sha256(file_path),
                "bytes": file_path.stat().st_size,
            }
        )

    bundle = MethodBundle(
        method_name=definition.name,
        version=definition.version,
        status=definition.status,
        trust_level=definition.trust_level,
        priority=definition.priority,
        description=definition.description,
        capability_names=list(definition.capability_names),
        tags=list(definition.tags),
        source=definition.source,
        tests_passed=tests_passed,
        files=files,
        manifest=manifest,
    )
    bundle_path = bundle_root / "bundle.json"
    bundle_path.write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bundle_root


def validate_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    """Validate an exported bundle without mutating its contents."""

    root = Path(bundle_dir)
    bundle_path = root / "bundle.json"
    if not bundle_path.exists():
        raise MethodExportError(f"{bundle_path}: bundle.json does not exist.")
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MethodExportError(f"{bundle_path}: bundle.json must contain a JSON object.")

    checks: list[dict[str, Any]] = []
    valid = True
    for file_info in payload.get("files", []):
        if not isinstance(file_info, dict):
            valid = False
            checks.append({"path": None, "valid": False, "reason": "invalid file entry"})
            continue
        file_name = file_info.get("path")
        if not isinstance(file_name, str):
            valid = False
            checks.append({"path": None, "valid": False, "reason": "invalid file name"})
            continue
        file_path = root / file_name
        if not file_path.exists():
            valid = False
            checks.append({"path": file_name, "valid": False, "reason": "file missing"})
            continue
        expected = str(file_info.get("sha256", ""))
        actual = _sha256(file_path)
        file_valid = expected == actual
        valid = valid and file_valid
        checks.append(
            {
                "path": file_name,
                "valid": file_valid,
                "expected_sha256": expected,
                "actual_sha256": actual,
            }
        )

    return {
        "bundle_path": str(bundle_path),
        "valid": valid,
        "tests_passed": payload.get("tests_passed"),
        "checks": checks,
    }
