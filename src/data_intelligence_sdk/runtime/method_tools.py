"""Meta-tools for discovering and invoking Method Hub methods."""

from __future__ import annotations

import inspect
from dataclasses import asdict, is_dataclass
from typing import Any

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.method_hub import MethodHub, RegisteredMethod

__all__ = ["describe_method", "run_method", "search_methods"]


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _json_safe_value(asdict(value))
    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            if not isinstance(key, str):
                continue
            payload[key] = _json_safe_value(value[key])
        return payload
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _describe_signature(method: object) -> dict[str, Any]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {"signature": None, "parameters": []}

    parameters = []
    for parameter in signature.parameters.values():
        entry = {
            "name": parameter.name,
            "kind": parameter.kind.name.lower(),
        }
        if parameter.annotation is not inspect.Signature.empty:
            entry["annotation"] = str(parameter.annotation)
        if parameter.default is not inspect.Signature.empty:
            entry["default"] = _json_safe_value(parameter.default)
        parameters.append(entry)
    return {"signature": str(signature), "parameters": parameters}


def search_methods(
    hub: MethodHub,
    query: str,
    *,
    top_k: int = 5,
    executable_only: bool = True,
) -> list[dict[str, Any]]:
    """Search methods using the hub's lexical discovery logic."""

    return hub.search(query, top_k=top_k, executable_only=executable_only)


def describe_method(hub: MethodHub, name: str) -> dict[str, Any]:
    """Return a JSON-serializable method description without exposing the callable."""

    definition = hub.get_definition(name)
    signature = _describe_signature(definition.method)
    metadata = _json_safe_value(definition.metadata)
    if metadata is None:
        metadata = {}
    return {
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "capability_names": list(definition.capability_names),
        "tags": list(definition.tags),
        "trust_level": definition.trust_level,
        "status": definition.status,
        "priority": definition.priority,
        "source": definition.source,
        "metadata": metadata,
        **signature,
    }


def run_method(
    hub: MethodHub,
    name: str,
    arguments: dict[str, Any],
    *,
    runtime: EngineRuntimeContext | None = None,
) -> Any:
    """Execute a registered method through the hub's trust enforcement."""

    method = hub.get(name)
    try:
        result = method(**arguments)
    except Exception as exc:
        if runtime is not None:
            runtime.run_context.record_method_call(
                name,
                status="failed",
                inputs=_json_safe_value(arguments),
                outputs={"error": str(exc)},
            )
        raise

    if runtime is not None:
        runtime.run_context.record_method_call(
            name,
            status="completed",
            inputs=_json_safe_value(arguments),
            outputs={"result": _json_safe_value(result)},
        )
    return result
