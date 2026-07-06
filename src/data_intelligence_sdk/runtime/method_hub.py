"""Method hub boundary for engine-accessible capabilities."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
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
