"""Method hub boundary for engine-accessible capabilities."""

from __future__ import annotations

import inspect as _inspect
import importlib
import re
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from data_intelligence_sdk.core.errors import DataIntelligenceError
from data_intelligence_sdk.core.types import CapabilityRequirement, MethodStatus, TrustLevel

__all__ = [
    "DuplicateMethodError",
    "InvalidMethodError",
    "MethodHub",
    "MethodHubError",
    "MethodNotFoundError",
    "MethodTrustError",
    "RegisteredMethod",
]

_EXECUTABLE_TRUST_LEVELS = {
    "builtin",
    "user_approved",
    "generated_validated",
}
_TRUST_RANK = {
    "builtin": 4,
    "user_approved": 3,
    "generated_validated": 2,
    "generated_unvalidated": 1,
    "blocked": 0,
}
_VALID_STATUSES = {"draft", "experimental", "stable", "deprecated"}
_SEARCH_WEIGHTS = {
    "name": 4.0,
    "capability": 3.0,
    "tags": 2.0,
    "description": 1.5,
    "use_when": 1.0,
    "do_not_use_when": 0.75,
    "category": 0.75,
}
_UNSUPPORTED = object()
_REGISTRATION_FIELDS = {
    "name",
    "method",
    "capability_names",
    "trust_level",
    "metadata",
    "version",
    "description",
    "tags",
    "status",
    "priority",
    "source",
}


class MethodHubError(DataIntelligenceError):
    """Base error for Method Hub operations."""


class InvalidMethodError(MethodHubError, ValueError):
    """Raised when a method registration payload is malformed."""


class DuplicateMethodError(MethodHubError):
    """Raised when a method name is already registered and replace=False."""


class MethodNotFoundError(MethodHubError, LookupError):
    """Raised when a method lookup fails."""


class MethodTrustError(MethodHubError, PermissionError):
    """Raised when a caller tries to execute a non-executable method."""


def _normalize_name(name: object) -> str:
    if not isinstance(name, str):
        raise InvalidMethodError("Method name must be a string.")
    normalized = name.strip()
    if not normalized:
        raise InvalidMethodError("Method name cannot be empty.")
    return normalized


def _normalize_text(value: object | None, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    if field_name and normalized == "" and value != "":
        return ""
    return normalized


def _normalize_string_list(
    values: Iterable[object] | None, field_name: str
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raise InvalidMethodError(f"{field_name} must be a list of strings.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise InvalidMethodError(f"{field_name} must be a list of strings.")
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _normalize_trust_level(trust_level: object) -> TrustLevel:
    normalized = _normalize_text(trust_level, "trust_level").lower()
    if normalized not in _TRUST_RANK:
        raise InvalidMethodError(f"Invalid trust level: {trust_level!r}")
    return normalized  # type: ignore[return-value]


def _normalize_status(status: object) -> MethodStatus:
    normalized = _normalize_text(status, "status").lower()
    if normalized not in _VALID_STATUSES:
        raise InvalidMethodError(f"Invalid method status: {status!r}")
    return normalized  # type: ignore[return-value]


def _normalize_priority(priority: object) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise InvalidMethodError("priority must be an integer.")
    return priority


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            if not isinstance(key, str):
                continue
            safe = _json_safe_value(value[key])
            if safe is not _UNSUPPORTED:
                payload[key] = safe
        return payload
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for item in value:
            safe = _json_safe_value(item)
            if safe is not _UNSUPPORTED:
                items.append(safe)
        return items
    if isinstance(value, set):
        items = []
        for item in sorted(value, key=lambda item: repr(item)):
            safe = _json_safe_value(item)
            if safe is not _UNSUPPORTED:
                items.append(safe)
        return items
    return _UNSUPPORTED


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _text_from_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key in sorted(value.keys(), key=str):
            parts.append(str(key))
            parts.append(_text_from_value(value[key]))
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text_from_value(item) for item in value if item is not None)
    return str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _describe_signature(method: object) -> dict[str, Any]:
    try:
        signature = _inspect.signature(method)
    except (TypeError, ValueError):
        return {"signature": None, "parameters": []}

    parameters = []
    for parameter in signature.parameters.values():
        entry = {
            "name": parameter.name,
            "kind": parameter.kind.name.lower(),
        }
        if parameter.annotation is not _inspect.Signature.empty:
            entry["annotation"] = str(parameter.annotation)
        if parameter.default is not _inspect.Signature.empty:
            default = _json_safe_value(parameter.default)
            entry["default"] = default if default is not _UNSUPPORTED else repr(parameter.default)
        parameters.append(entry)
    return {"signature": str(signature), "parameters": parameters}


def _validate_callable_arguments(method: object, arguments: Mapping[str, Any]) -> dict[str, Any]:
    try:
        signature = _inspect.signature(method)
    except (TypeError, ValueError):
        return {
            "schema_version": "1.0",
            "valid": True,
            "message": "Signature is unavailable; arguments were not statically validated.",
            "missing": [],
            "unexpected": [],
        }
    try:
        signature.bind(**dict(arguments))
    except TypeError as exc:
        return {
            "schema_version": "1.0",
            "valid": False,
            "message": str(exc),
            "missing": _missing_required_parameters(signature, arguments),
            "unexpected": _unexpected_parameters(signature, arguments),
        }
    return {
        "schema_version": "1.0",
        "valid": True,
        "message": "Arguments match the callable signature.",
        "missing": [],
        "unexpected": [],
    }


def _missing_required_parameters(
    signature: _inspect.Signature, arguments: Mapping[str, Any]
) -> list[str]:
    missing = []
    for name, parameter in signature.parameters.items():
        if parameter.default is not _inspect.Signature.empty:
            continue
        if parameter.kind in {
            _inspect.Parameter.VAR_POSITIONAL,
            _inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if name not in arguments:
            missing.append(name)
    return missing


def _unexpected_parameters(
    signature: _inspect.Signature, arguments: Mapping[str, Any]
) -> list[str]:
    if any(
        parameter.kind == _inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return []
    allowed = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind
        in {
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
            _inspect.Parameter.KEYWORD_ONLY,
        }
    }
    return sorted(str(name) for name in arguments if name not in allowed)


def _field_search_score(
    query_tokens: set[str],
    *,
    field_name: str,
    text: str,
    weight: float,
) -> tuple[float, str | None]:
    if not text:
        return 0.0, None
    text_tokens = set(_tokenize(text))
    matches = sorted(query_tokens.intersection(text_tokens))
    if not matches:
        return 0.0, None
    coverage = len(matches) / max(len(query_tokens), 1)
    score = weight * coverage
    return score, f"{field_name}: {', '.join(matches[:4])}"


@dataclass(slots=True)
class RegisteredMethod:
    """Callable method plus capability and trust metadata."""

    name: str
    method: object
    capability_names: list[str] = field(default_factory=list)
    trust_level: TrustLevel = "builtin"
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0.0"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: MethodStatus = "stable"
    priority: int = 0
    source: str | None = None

    def is_executable(self) -> bool:
        """Return whether the method is allowed to execute."""

        return self.trust_level in _EXECUTABLE_TRUST_LEVELS and self.status != "deprecated"

    def to_catalog_entry(self) -> dict[str, Any]:
        """Return a JSON-serializable catalog entry for discovery and export."""

        metadata = _json_safe_value(self.metadata)
        if metadata is _UNSUPPORTED:
            metadata = {}
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capability_names": list(self.capability_names),
            "trust_level": self.trust_level,
            "status": self.status,
            "priority": self.priority,
            "tags": list(self.tags),
            "source": self.source,
            "metadata": metadata,
            "is_executable": self.is_executable(),
        }


class MethodHub:
    """Registry for methods that engines may call during execution."""

    def __init__(self) -> None:
        self._methods: dict[str, RegisteredMethod] = {}
        self._history: list[dict[str, Any]] = []
        self._proposals: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        method: object,
        *,
        capability_names: list[str] | None = None,
        trust_level: TrustLevel = "builtin",
        metadata: dict[str, Any] | None = None,
        version: str = "1.0.0",
        description: str | None = None,
        tags: list[str] | None = None,
        status: MethodStatus = "stable",
        priority: int = 0,
        source: str | None = None,
        replace: bool = False,
    ) -> None:
        """Register a callable method and its discovery metadata."""

        normalized_name = _normalize_name(name)
        if not callable(method):
            raise InvalidMethodError(
                f"Registered method {normalized_name!r} must be callable."
            )
        if normalized_name in self._methods and not replace:
            raise DuplicateMethodError(
                f"Method {normalized_name!r} is already registered."
            )

        normalized_capabilities = _normalize_string_list(
            capability_names, "capability_names"
        )
        normalized_tags = _normalize_string_list(tags, "tags")
        normalized_trust_level = _normalize_trust_level(trust_level)
        normalized_status = _normalize_status(status)
        normalized_priority = _normalize_priority(priority)
        metadata_copy = deepcopy(metadata or {})
        if not isinstance(metadata_copy, dict):
            raise InvalidMethodError("metadata must be a mapping.")

        normalized_description = _normalize_text(description, "description")
        if not normalized_description and "description" in metadata_copy:
            normalized_description = _normalize_text(
                metadata_copy.get("description"), "description"
            )
        normalized_version = _normalize_text(version, "version") or "1.0.0"
        normalized_source = _normalize_text(source, "source") or None

        self._methods[normalized_name] = RegisteredMethod(
            name=normalized_name,
            method=method,
            capability_names=normalized_capabilities,
            trust_level=normalized_trust_level,
            metadata=metadata_copy,
            version=normalized_version,
            description=normalized_description,
            tags=normalized_tags,
            status=normalized_status,
            priority=normalized_priority,
            source=normalized_source,
        )
        self._record_history(
            "register",
            normalized_name,
            status="completed",
            details={"replace": replace, "version": normalized_version},
        )

    def list(
        self,
        filters: Mapping[str, Any] | None = None,
        *,
        executable_only: bool = False,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return JSON-serializable method catalog entries.

        This is the interface-level companion to ``list_methods()``, which
        intentionally returns ``RegisteredMethod`` objects for internal callers.
        """

        methods = self.list_methods(executable_only=executable_only, statuses=statuses)
        filters = dict(filters or {})
        capability = filters.get("capability")
        tag = filters.get("tag")
        trust_level = filters.get("trust_level")
        source = filters.get("source")
        if capability is not None:
            methods = [
                method for method in methods if str(capability) in method.capability_names
            ]
        if tag is not None:
            methods = [method for method in methods if str(tag) in method.tags]
        if trust_level is not None:
            normalized_trust = _normalize_trust_level(trust_level)
            methods = [
                method for method in methods if method.trust_level == normalized_trust
            ]
        if source is not None:
            methods = [method for method in methods if method.source == str(source)]
        return [method.to_catalog_entry() for method in methods]

    def inspect(self, name: str, *, version: str | None = None) -> dict[str, Any]:
        """Return a detailed JSON-serializable method description."""

        definition = self._lookup_version(name, version)
        return {
            **definition.to_catalog_entry(),
            **_describe_signature(definition.method),
        }

    def validate(self, definition: Mapping[str, Any] | RegisteredMethod) -> dict[str, Any]:
        """Validate a method definition payload without registering it."""

        payload = self._coerce_registration_payload(definition)
        return {
            "schema_version": "1.0",
            "valid": True,
            "name": payload["name"],
            "version": payload["version"],
            "is_executable": (
                payload["trust_level"] in _EXECUTABLE_TRUST_LEVELS
                and payload["status"] != "deprecated"
            ),
            "warnings": self._definition_warnings(payload),
        }

    def insert(
        self,
        definition: Mapping[str, Any] | RegisteredMethod,
        *,
        replace: bool = False,
    ) -> RegisteredMethod:
        """Insert a method from a structured definition payload."""

        payload = self._coerce_registration_payload(definition)
        self.register(**payload, replace=replace)
        inserted = self.get_definition(payload["name"])
        self._record_history(
            "insert",
            inserted.name,
            status="completed",
            details={"replace": replace, "version": inserted.version},
        )
        return inserted

    def update(self, name: str, patch: Mapping[str, Any]) -> RegisteredMethod:
        """Update method metadata, lifecycle fields, or callable implementation."""

        definition = self._lookup(name)
        if not isinstance(patch, Mapping):
            raise InvalidMethodError("patch must be a mapping.")
        unknown = set(patch) - (_REGISTRATION_FIELDS - {"name"})
        if unknown:
            raise InvalidMethodError(f"Unsupported method patch fields: {sorted(unknown)!r}")
        if "method" in patch and not callable(patch["method"]):
            raise InvalidMethodError("method must be callable.")
        if "capability_names" in patch:
            definition.capability_names = _normalize_string_list(
                patch.get("capability_names"), "capability_names"
            )
        if "trust_level" in patch:
            definition.trust_level = _normalize_trust_level(patch["trust_level"])
        if "metadata" in patch:
            metadata = deepcopy(patch.get("metadata") or {})
            if not isinstance(metadata, dict):
                raise InvalidMethodError("metadata must be a mapping.")
            definition.metadata = metadata
        if "version" in patch:
            definition.version = _normalize_text(patch.get("version"), "version") or "1.0.0"
        if "description" in patch:
            definition.description = _normalize_text(patch.get("description"), "description")
        if "tags" in patch:
            definition.tags = _normalize_string_list(patch.get("tags"), "tags")
        if "status" in patch:
            definition.status = _normalize_status(patch["status"])
        if "priority" in patch:
            definition.priority = _normalize_priority(patch["priority"])
        if "source" in patch:
            definition.source = _normalize_text(patch.get("source"), "source") or None
        if "method" in patch:
            definition.method = patch["method"]
        self._record_history(
            "update",
            definition.name,
            status="completed",
            details={"fields": sorted(patch.keys())},
        )
        return definition

    def deprecate(self, name: str, *, reason: str | None = None) -> RegisteredMethod:
        """Mark a method deprecated while preserving its definition for history."""

        metadata = deepcopy(self._lookup(name).metadata)
        if reason:
            metadata["deprecation_reason"] = reason
        return self.update(name, {"status": "deprecated", "metadata": metadata})

    def remove(self, name: str) -> dict[str, Any]:
        """Remove a method definition from this in-memory hub."""

        definition = self._lookup(name)
        removed = definition.to_catalog_entry()
        del self._methods[definition.name]
        self._record_history(
            "remove",
            definition.name,
            status="completed",
            details={"version": definition.version},
        )
        return removed

    def export(self, name: str, *, version: str | None = None) -> dict[str, Any]:
        """Export a registered method as a JSON/YAML-friendly manifest."""

        definition = self._lookup_version(name, version)
        entrypoint = self._entrypoint_for(definition)
        metadata = _json_safe_value(definition.metadata)
        if metadata is _UNSUPPORTED:
            metadata = {}
        manifest = {
            "schema_version": "1.0",
            "name": definition.name,
            "version": definition.version,
            "entrypoint": entrypoint,
            "description": definition.description,
            "capability_names": list(definition.capability_names),
            "trust_level": definition.trust_level,
            "status": definition.status,
            "priority": definition.priority,
            "tags": list(definition.tags),
            "source": definition.source,
            "metadata": metadata,
            "callable_exportable": entrypoint is not None,
        }
        self._record_history(
            "export",
            definition.name,
            status="completed",
            outputs={"manifest": manifest},
            details={"version": definition.version},
        )
        return manifest

    def import_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        method: object | None = None,
        replace: bool = False,
    ) -> RegisteredMethod:
        """Import a method from an exported manifest or manifest-like mapping."""

        if not isinstance(manifest, Mapping):
            raise InvalidMethodError("manifest must be a mapping.")
        entrypoint = manifest.get("entrypoint")
        resolved_method = method
        if resolved_method is None:
            if not entrypoint:
                raise InvalidMethodError(
                    "import_manifest requires a method argument when entrypoint is missing."
                )
            resolved_method = self._import_entrypoint(str(entrypoint))
        definition = {
            "name": manifest.get("name"),
            "method": resolved_method,
            "capability_names": manifest.get("capability_names", []),
            "trust_level": manifest.get("trust_level", "builtin"),
            "metadata": manifest.get("metadata", {}),
            "version": manifest.get("version", "1.0.0"),
            "description": manifest.get("description", ""),
            "tags": manifest.get("tags", []),
            "status": manifest.get("status", "stable"),
            "priority": manifest.get("priority", 0),
            "source": manifest.get("source") or entrypoint,
        }
        inserted = self.insert(definition, replace=replace)
        self._record_history(
            "import",
            inserted.name,
            status="completed",
            details={"replace": replace, "entrypoint": entrypoint},
        )
        return inserted

    def dry_run(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Validate a method call without executing the callable."""

        definition = self._lookup_version(name, version)
        normalized_arguments = dict(arguments or {})
        argument_validation = _validate_callable_arguments(
            definition.method, normalized_arguments
        )
        result = {
            **argument_validation,
            "method_name": definition.name,
            "version": definition.version,
            "trust_level": definition.trust_level,
            "status": definition.status,
            "is_executable": definition.is_executable(),
            "arguments": _json_safe_value(normalized_arguments),
        }
        self._record_history(
            "dry_run",
            definition.name,
            status="completed" if result["valid"] else "failed",
            inputs=normalized_arguments,
            outputs=result,
        )
        return result

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        version: str | None = None,
        runtime: Any | None = None,
    ) -> Any:
        """Execute a method through trust checks and record an audit entry."""

        definition = self._lookup_version(name, version)
        method = self.get(definition.name)
        normalized_arguments = dict(arguments or {})
        started = time.perf_counter()
        try:
            result = method(**normalized_arguments)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            outputs = {"error": str(exc), "duration_ms": duration_ms}
            self._record_history(
                "call",
                definition.name,
                status="failed",
                inputs=normalized_arguments,
                outputs=outputs,
                details={"version": definition.version},
            )
            if runtime is not None:
                runtime.run_context.record_method_call(
                    definition.name,
                    status="failed",
                    inputs=_json_safe_value(normalized_arguments),
                    outputs=outputs,
                )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        safe_result = _json_safe_value(result)
        outputs = {
            "result": safe_result if safe_result is not _UNSUPPORTED else repr(result),
            "duration_ms": duration_ms,
        }
        self._record_history(
            "call",
            definition.name,
            status="completed",
            inputs=normalized_arguments,
            outputs=outputs,
            details={"version": definition.version},
        )
        if runtime is not None:
            runtime.run_context.record_method_call(
                definition.name,
                status="completed",
                inputs=_json_safe_value(normalized_arguments),
                outputs=outputs,
            )
        return result

    def propose(
        self,
        definition: Mapping[str, Any] | RegisteredMethod,
        *,
        proposal_id: str | None = None,
        title: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        """Create an in-memory pending method proposal."""

        payload = self._coerce_registration_payload(definition)
        payload["trust_level"] = "generated_unvalidated"
        proposal_id = proposal_id or uuid.uuid4().hex
        if proposal_id in self._proposals:
            raise DuplicateMethodError(f"Proposal {proposal_id!r} already exists.")
        record = {
            "schema_version": "1.0",
            "proposal_id": proposal_id,
            "status": "pending",
            "title": title,
            "summary": summary,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "definition": payload,
        }
        self._proposals[proposal_id] = record
        self._record_history(
            "propose",
            payload["name"],
            status="completed",
            details={"proposal_id": proposal_id},
        )
        return self._public_proposal(record)

    def approve(
        self,
        proposal_id: str,
        *,
        trust_level: TrustLevel = "generated_validated",
        replace: bool = False,
    ) -> RegisteredMethod:
        """Approve and insert a pending in-memory proposal."""

        proposal = self._proposal(proposal_id)
        if proposal["status"] != "pending":
            raise InvalidMethodError(f"Proposal {proposal_id!r} is not pending.")
        definition = deepcopy(proposal["definition"])
        definition["trust_level"] = _normalize_trust_level(trust_level)
        inserted = self.insert(definition, replace=replace)
        proposal["status"] = "accepted"
        proposal["updated_at"] = _utc_now()
        self._record_history(
            "approve",
            inserted.name,
            status="completed",
            details={"proposal_id": proposal_id, "trust_level": inserted.trust_level},
        )
        return inserted

    def reject(self, proposal_id: str, *, reason: str | None = None) -> dict[str, Any]:
        """Reject a pending in-memory proposal."""

        proposal = self._proposal(proposal_id)
        if proposal["status"] != "pending":
            raise InvalidMethodError(f"Proposal {proposal_id!r} is not pending.")
        proposal["status"] = "rejected"
        proposal["reason"] = reason
        proposal["updated_at"] = _utc_now()
        self._record_history(
            "reject",
            proposal["definition"]["name"],
            status="completed",
            details={"proposal_id": proposal_id, "reason": reason},
        )
        return self._public_proposal(proposal)

    def proposals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """List in-memory method proposals."""

        records = list(self._proposals.values())
        if status is not None:
            records = [record for record in records if record["status"] == status]
        return [self._public_proposal(record) for record in sorted(
            records, key=lambda record: str(record["proposal_id"])
        )]

    def select(
        self,
        step_request: Mapping[str, Any] | str,
        *,
        top_k: int = 5,
        executable_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Rank methods for a task/step request using the search index."""

        query = step_request if isinstance(step_request, str) else _text_from_value(step_request)
        return self.search(query, top_k=top_k, executable_only=executable_only)

    def history(
        self,
        name: str | None = None,
        *,
        action: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return audit events recorded by this in-memory hub."""

        events = self._history
        if name is not None:
            normalized_name = _normalize_name(name)
            events = [event for event in events if event.get("method_name") == normalized_name]
        if action is not None:
            events = [event for event in events if event.get("action") == action]
        if limit is not None:
            events = events[-max(0, int(limit)) :]
        return deepcopy(events)

    def _lookup(self, name: str) -> RegisteredMethod:
        normalized_name = _normalize_name(name)
        try:
            return self._methods[normalized_name]
        except KeyError as exc:
            raise MethodNotFoundError(
                f"Method {normalized_name!r} is not registered."
            ) from exc

    def get(self, name: str) -> object:
        """Return the callable for a registered and executable method."""

        definition = self._lookup(name)
        if not definition.is_executable():
            raise MethodTrustError(
                f"Method {definition.name!r} is not executable because it is "
                f"trusted as {definition.trust_level!r} with status {definition.status!r}."
            )
        return definition.method

    def get_definition(self, name: str) -> RegisteredMethod:
        """Return the registered definition, even if it is not executable."""

        return self._lookup(name)

    def list_methods(
        self,
        executable_only: bool = False,
        statuses: set[str] | None = None,
    ) -> list[RegisteredMethod]:
        """Return registered methods in deterministic order."""

        methods = list(self._methods.values())
        if executable_only:
            methods = [method for method in methods if method.is_executable()]
        if statuses is not None:
            normalized_statuses = {
                _normalize_status(status) for status in statuses if str(status).strip()
            }
            methods = [method for method in methods if method.status in normalized_statuses]
        return sorted(methods, key=self._sort_key)

    def resolve(self, requirement: CapabilityRequirement) -> RegisteredMethod | None:
        """Return the best executable method matching a capability requirement."""

        requirement_name = _normalize_text(requirement.name, "requirement.name")
        if not requirement_name:
            return None
        candidates = [
            method
            for method in self._methods.values()
            if requirement_name in method.capability_names and method.is_executable()
        ]
        if not candidates:
            return None
        return sorted(candidates, key=self._sort_key)[0]

    def resolve_all(
        self, requirements: Iterable[CapabilityRequirement]
    ) -> dict[str, RegisteredMethod | None]:
        """Resolve multiple requirements to their best matching methods."""

        resolved: dict[str, RegisteredMethod | None] = {}
        for requirement in requirements:
            requirement_name = _normalize_text(requirement.name, "requirement.name")
            if not requirement_name:
                continue
            resolved[requirement_name] = self.resolve(requirement)
        return resolved

    def select_for_requirements(
        self, requirements: Iterable[CapabilityRequirement]
    ) -> list[RegisteredMethod]:
        """Resolve requirements and return unique methods in deterministic order."""

        selected: list[RegisteredMethod] = []
        seen_names: set[str] = set()
        for requirement in requirements:
            method = self.resolve(requirement)
            if method is None or method.name in seen_names:
                continue
            seen_names.add(method.name)
            selected.append(method)
        return selected

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        executable_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Lexically search methods using discovery metadata."""

        query = _normalize_text(query, "query")
        if not query:
            return []
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return []

        candidates = self.list_methods(executable_only=executable_only)
        scored: list[dict[str, Any]] = []
        for method in candidates:
            score = 0.0
            reasons: list[str] = []

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="name",
                text=method.name,
                weight=_SEARCH_WEIGHTS["name"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="capability_names",
                text=" ".join(method.capability_names),
                weight=_SEARCH_WEIGHTS["capability"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="tags",
                text=" ".join(method.tags),
                weight=_SEARCH_WEIGHTS["tags"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="description",
                text=method.description,
                weight=_SEARCH_WEIGHTS["description"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="use_when",
                text=_text_from_value(method.metadata.get("use_when")),
                weight=_SEARCH_WEIGHTS["use_when"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="do_not_use_when",
                text=_text_from_value(method.metadata.get("do_not_use_when")),
                weight=_SEARCH_WEIGHTS["do_not_use_when"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            field_score, reason = _field_search_score(
                query_tokens,
                field_name="category",
                text=_text_from_value(method.metadata.get("category")),
                weight=_SEARCH_WEIGHTS["category"],
            )
            score += field_score
            if reason:
                reasons.append(reason)

            if not reasons:
                reasons.append("fallback: no lexical overlap")

            scored.append(
                {
                    "name": method.name,
                    "version": method.version,
                    "score": round(score, 4),
                    "description": method.description,
                    "capability_names": list(method.capability_names),
                    "status": method.status,
                    "trust_level": method.trust_level,
                    "priority": method.priority,
                    "tags": list(method.tags),
                    "reason": "; ".join(reasons),
                }
            )

        scored.sort(
            key=lambda item: (
                -float(item["score"]),
                -self._trust_rank(str(item["trust_level"])),
                -int(item["priority"]),
                str(item["name"]).casefold(),
            )
        )
        return scored[: max(0, int(top_k))]

    def build_llm_catalog(self, executable_only: bool = True) -> dict[str, Any]:
        """Build a compact JSON-serializable catalog for LLM discovery."""

        return {
            "format": "child-method-hub-catalog-v1",
            "methods": [method.to_catalog_entry() for method in self.list_methods(
                executable_only=executable_only
            )],
        }

    def _lookup_version(self, name: str, version: str | None = None) -> RegisteredMethod:
        definition = self._lookup(name)
        if version is not None and definition.version != version:
            raise MethodNotFoundError(
                f"Method {definition.name!r} version {version!r} is not registered."
            )
        return definition

    def _coerce_registration_payload(
        self, definition: Mapping[str, Any] | RegisteredMethod
    ) -> dict[str, Any]:
        if isinstance(definition, RegisteredMethod):
            payload = {
                "name": definition.name,
                "method": definition.method,
                "capability_names": list(definition.capability_names),
                "trust_level": definition.trust_level,
                "metadata": deepcopy(definition.metadata),
                "version": definition.version,
                "description": definition.description,
                "tags": list(definition.tags),
                "status": definition.status,
                "priority": definition.priority,
                "source": definition.source,
            }
        elif isinstance(definition, Mapping):
            payload = dict(definition)
        else:
            raise InvalidMethodError("method definition must be a mapping or RegisteredMethod.")

        unknown = set(payload) - _REGISTRATION_FIELDS
        if unknown:
            raise InvalidMethodError(f"Unsupported method definition fields: {sorted(unknown)!r}")
        if "name" not in payload:
            raise InvalidMethodError("method definition requires a name.")
        if "method" not in payload:
            raise InvalidMethodError("method definition requires a callable method.")
        normalized_name = _normalize_name(payload["name"])
        method = payload["method"]
        if not callable(method):
            raise InvalidMethodError(
                f"Registered method {normalized_name!r} must be callable."
            )
        metadata = deepcopy(payload.get("metadata") or {})
        if not isinstance(metadata, dict):
            raise InvalidMethodError("metadata must be a mapping.")
        return {
            "name": normalized_name,
            "method": method,
            "capability_names": _normalize_string_list(
                payload.get("capability_names"), "capability_names"
            ),
            "trust_level": _normalize_trust_level(payload.get("trust_level", "builtin")),
            "metadata": metadata,
            "version": _normalize_text(payload.get("version", "1.0.0"), "version") or "1.0.0",
            "description": _normalize_text(payload.get("description"), "description")
            or _normalize_text(metadata.get("description"), "description"),
            "tags": _normalize_string_list(payload.get("tags"), "tags"),
            "status": _normalize_status(payload.get("status", "stable")),
            "priority": _normalize_priority(payload.get("priority", 0)),
            "source": _normalize_text(payload.get("source"), "source") or None,
        }

    def _definition_warnings(self, payload: Mapping[str, Any]) -> list[str]:
        warnings = []
        if not payload.get("capability_names"):
            warnings.append("Method has no capability_names, so requirement resolution may not find it.")
        if not payload.get("description"):
            warnings.append("Method has no description, so search and routing quality may be lower.")
        metadata = payload.get("metadata", {})
        if isinstance(metadata, Mapping) and metadata.get("side_effects") is None:
            warnings.append("Method metadata does not declare side_effects.")
        if payload.get("trust_level") == "generated_unvalidated":
            warnings.append("Generated unvalidated methods are visible but not executable.")
        return warnings

    def _record_history(
        self,
        action: str,
        method_name: str,
        *,
        status: str,
        inputs: Mapping[str, Any] | None = None,
        outputs: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        safe_inputs = _json_safe_value(inputs or {})
        safe_outputs = _json_safe_value(outputs or {})
        safe_details = _json_safe_value(details or {})
        self._history.append(
            {
                "schema_version": "1.0",
                "timestamp": _utc_now(),
                "action": action,
                "method_name": method_name,
                "status": status,
                "inputs": safe_inputs if safe_inputs is not _UNSUPPORTED else {},
                "outputs": safe_outputs if safe_outputs is not _UNSUPPORTED else {},
                "details": safe_details if safe_details is not _UNSUPPORTED else {},
            }
        )

    def _proposal(self, proposal_id: str) -> dict[str, Any]:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise MethodNotFoundError(f"Proposal {proposal_id!r} is not registered.") from exc

    def _public_proposal(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        definition = dict(proposal["definition"])
        definition.pop("method", None)
        return {
            "schema_version": proposal["schema_version"],
            "proposal_id": proposal["proposal_id"],
            "status": proposal["status"],
            "title": proposal.get("title", ""),
            "summary": proposal.get("summary", ""),
            "reason": proposal.get("reason"),
            "created_at": proposal["created_at"],
            "updated_at": proposal["updated_at"],
            "definition": _json_safe_value(definition),
        }

    def _entrypoint_for(self, definition: RegisteredMethod) -> str | None:
        metadata_entrypoint = definition.metadata.get("entrypoint")
        if metadata_entrypoint:
            return str(metadata_entrypoint)
        module = getattr(definition.method, "__module__", None)
        qualname = getattr(definition.method, "__qualname__", None)
        if not module or not qualname or "<locals>" in str(qualname):
            return None
        return f"{module}:{qualname}"

    def _import_entrypoint(self, entrypoint: str) -> object:
        if ":" not in entrypoint:
            raise InvalidMethodError("entrypoint must use 'module:attribute' format.")
        module_name, attribute_path = entrypoint.split(":", 1)
        try:
            value: object = importlib.import_module(module_name)
            for attribute in attribute_path.split("."):
                value = getattr(value, attribute)
        except (ImportError, AttributeError) as exc:
            raise InvalidMethodError(f"entrypoint {entrypoint!r} could not be imported.") from exc
        if not callable(value):
            raise InvalidMethodError(f"entrypoint {entrypoint!r} is not callable.")
        return value

    @staticmethod
    def _trust_rank(trust_level: str) -> int:
        return _TRUST_RANK.get(trust_level, 0)

    @staticmethod
    def _sort_key(method: RegisteredMethod) -> tuple[int, int, str]:
        return (
            -method.priority,
            -_TRUST_RANK.get(method.trust_level, 0),
            method.name.casefold(),
        )
