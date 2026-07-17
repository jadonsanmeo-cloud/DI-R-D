"""YAML manifest loader for Method Hub registrations."""

from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from data_intelligence_sdk.core.errors import DataIntelligenceError
from data_intelligence_sdk.runtime.method_hub import MethodHub, RegisteredMethod

__all__ = [
    "MethodManifestError",
    "import_entrypoint",
    "load_manifest",
    "load_manifest_directory",
    "validate_manifest",
]

_VALID_TRUST_LEVELS = {
    "builtin",
    "user_approved",
    "generated_unvalidated",
    "generated_validated",
    "blocked",
}
_VALID_STATUSES = {"draft", "experimental", "stable", "deprecated"}


class MethodManifestError(DataIntelligenceError):
    """Raised when a manifest cannot be loaded or validated."""


def _normalize_string_list(
    values: Iterable[object] | None, field_name: str, source: str | None
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raise MethodManifestError(
            _format_error(field_name, "must be a list of strings", source)
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise MethodManifestError(
                _format_error(field_name, "must be a list of strings", source)
            )
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _format_error(field_name: str, message: str, source: str | None) -> str:
    prefix = f"{source}: " if source else ""
    return f"{prefix}{field_name} {message}."


def _validate_entrypoint(entrypoint: object, source: str | None) -> str:
    if not isinstance(entrypoint, str):
        raise MethodManifestError(
            _format_error(
                "entrypoint", "must be a string in package.module:attr form", source
            )
        )
    normalized = entrypoint.strip()
    if ":" not in normalized:
        raise MethodManifestError(
            _format_error(
                "entrypoint", "must use package.module:attribute format", source
            )
        )
    module_name, attribute_path = normalized.split(":", 1)
    if not module_name.strip() or not attribute_path.strip():
        raise MethodManifestError(
            _format_error(
                "entrypoint", "must use package.module:attribute format", source
            )
        )
    return normalized


def _validate_manifest_mapping(
    data: Mapping[str, Any], source: str | None = None
) -> dict[str, Any]:
    manifest = deepcopy(dict(data))

    for field_name in ("name", "entrypoint", "capability_names"):
        if field_name not in manifest:
            raise MethodManifestError(_format_error(field_name, "is required", source))

    name = manifest["name"]
    if not isinstance(name, str) or not name.strip():
        raise MethodManifestError(
            _format_error("name", "must be a non-empty string", source)
        )
    manifest["name"] = name.strip()

    manifest["entrypoint"] = _validate_entrypoint(manifest["entrypoint"], source)
    manifest["capability_names"] = _normalize_string_list(
        manifest["capability_names"], "capability_names", source
    )
    if not manifest["capability_names"]:
        raise MethodManifestError(
            _format_error(
                "capability_names", "must contain at least one capability", source
            )
        )

    trust_level = manifest.get("trust_level", "builtin")
    if not isinstance(trust_level, str) or not trust_level.strip():
        raise MethodManifestError(
            _format_error("trust_level", "must be a valid trust level string", source)
        )
    trust_level = trust_level.strip().lower()
    if trust_level not in _VALID_TRUST_LEVELS:
        raise MethodManifestError(
            _format_error(
                "trust_level", f"must be one of {sorted(_VALID_TRUST_LEVELS)}", source
            )
        )
    manifest["trust_level"] = trust_level

    status = manifest.get("status", "stable")
    if not isinstance(status, str) or not status.strip():
        raise MethodManifestError(
            _format_error("status", "must be a valid method status string", source)
        )
    status = status.strip().lower()
    if status not in _VALID_STATUSES:
        raise MethodManifestError(
            _format_error("status", f"must be one of {sorted(_VALID_STATUSES)}", source)
        )
    manifest["status"] = status

    priority = manifest.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise MethodManifestError(
            _format_error("priority", "must be an integer", source)
        )
    manifest["priority"] = priority

    version = manifest.get("version", "1.0.0")
    manifest["version"] = (
        "1.0.0" if version is None else (str(version).strip() or "1.0.0")
    )
    description = manifest.get("description", "")
    manifest["description"] = "" if description is None else str(description).strip()
    manifest["tags"] = _normalize_string_list(manifest.get("tags"), "tags", source)
    manifest["use_when"] = _normalize_string_list(
        manifest.get("use_when"), "use_when", source
    )
    manifest["do_not_use_when"] = _normalize_string_list(
        manifest.get("do_not_use_when"), "do_not_use_when", source
    )

    metadata = manifest.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise MethodManifestError(
            _format_error("metadata", "must be a mapping", source)
        )
    manifest["metadata"] = deepcopy(dict(metadata))
    return manifest


def validate_manifest(data: object, source: str | None = None) -> dict[str, Any]:
    """Validate and normalize a manifest mapping."""

    if not isinstance(data, Mapping):
        raise MethodManifestError(
            _format_error("manifest", "root must be a mapping", source)
        )
    return _validate_manifest_mapping(data, source)


def import_entrypoint(entrypoint: str) -> object:
    """Import a callable from a ``package.module:attribute`` entrypoint."""

    module_name, attribute_path = entrypoint.split(":", 1)
    module = importlib.import_module(module_name)
    attribute: object = module
    for component in attribute_path.split("."):
        attribute = getattr(attribute, component)
    return attribute


def _build_registered_metadata(
    manifest: dict[str, Any], manifest_path: Path
) -> dict[str, Any]:
    metadata = deepcopy(manifest["metadata"])
    metadata["entrypoint"] = manifest["entrypoint"]
    metadata["manifest_path"] = str(manifest_path.resolve())
    metadata["use_when"] = list(manifest["use_when"])
    metadata["do_not_use_when"] = list(manifest["do_not_use_when"])
    metadata["tags"] = list(manifest["tags"])
    metadata["category"] = metadata.get("category")
    return metadata


def load_manifest(
    hub: MethodHub,
    manifest_path: str | Path,
    *,
    replace: bool = False,
) -> RegisteredMethod:
    """Load a single manifest file into a Method Hub."""

    path = Path(manifest_path)
    if not path.exists():
        raise MethodManifestError(f"{path}: manifest file does not exist.")
    try:
        manifest_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - exercised by invalid fixtures.
        raise MethodManifestError(f"{path}: invalid YAML manifest.") from exc

    manifest = validate_manifest(manifest_data, source=str(path))
    try:
        method = import_entrypoint(manifest["entrypoint"])
    except (ImportError, AttributeError) as exc:
        raise MethodManifestError(
            f"{path}: entrypoint {manifest['entrypoint']!r} could not be imported."
        ) from exc
    if not callable(method):
        raise MethodManifestError(
            f"{path}: entrypoint {manifest['entrypoint']!r} is not callable."
        )

    hub.register(
        manifest["name"],
        method,
        capability_names=list(manifest["capability_names"]),
        trust_level=manifest["trust_level"],
        metadata=_build_registered_metadata(manifest, path),
        version=manifest["version"],
        description=manifest["description"],
        tags=list(manifest["tags"]),
        status=manifest["status"],
        priority=manifest["priority"],
        source=str(path.resolve()),
        replace=replace,
    )
    return hub.get_definition(manifest["name"])


def load_manifest_directory(
    hub: MethodHub,
    directory: str | Path,
    *,
    replace: bool = False,
) -> list[RegisteredMethod]:
    """Load every ``.yaml`` and ``.yml`` manifest in a directory deterministically."""

    root = Path(directory)
    if not root.exists():
        raise MethodManifestError(f"{root}: manifest directory does not exist.")
    manifest_paths = sorted(
        {
            *root.rglob("*.yaml"),
            *root.rglob("*.yml"),
        },
        key=lambda path: path.relative_to(root).as_posix(),
    )
    loaded: list[RegisteredMethod] = []
    for manifest_path in manifest_paths:
        loaded.append(load_manifest(hub, manifest_path, replace=replace))
    return loaded
