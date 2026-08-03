"""Safe subscriber payloads derived from durable engine events."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_PREVIEW_BUDGET = 12_000
_MAX_DEPTH = 4
_MAX_ITEMS = 12
_MAX_STRING = 1_600
_MAX_CODE_STRING = 65_536
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "private_key",
    "secret",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in {"authorization", "cookie", "token"}
        or normalized.endswith(("_authorization", "_cookie", "_token"))
        or any(part in normalized for part in _SENSITIVE_KEY_PARTS)
    )


def bounded_event_preview(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> Any:
    """Return a bounded, redacted value safe for realtime and history UIs."""

    remaining = budget if budget is not None else [_PREVIEW_BUDGET]
    if remaining[0] <= 0:
        return "[preview truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        remaining[0] -= len(str(value))
        return value
    if isinstance(value, str):
        preview = value[:_MAX_STRING]
        remaining[0] -= len(preview)
        return preview + ("..." if len(value) > len(preview) else "")
    if depth >= _MAX_DEPTH:
        return "[nested value omitted]"
    if isinstance(value, Mapping):
        preview: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, item in items[:_MAX_ITEMS]:
            key = str(raw_key)
            remaining[0] -= len(key)
            preview[key] = (
                "[redacted]"
                if _is_sensitive_key(key)
                else bounded_event_preview(
                    item,
                    depth=depth + 1,
                    budget=remaining,
                )
            )
            if remaining[0] <= 0:
                break
        if len(items) > len(preview):
            preview["_truncated"] = f"{len(items) - len(preview)} more fields"
        return preview
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        preview = [
            bounded_event_preview(item, depth=depth + 1, budget=remaining)
            for item in items[:_MAX_ITEMS]
            if remaining[0] > 0
        ]
        if len(items) > len(preview):
            preview.append(f"[{len(items) - len(preview)} more items]")
        return preview
    return bounded_event_preview(str(value), depth=depth, budget=remaining)


def _generated_code_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    outputs = payload.get("outputs")
    inputs = payload.get("inputs")
    raw_source: Any = None
    if isinstance(outputs, Mapping):
        raw_source = outputs.get("source_code")
    if not raw_source and isinstance(inputs, Mapping):
        raw_source = inputs.get("source_code")
    if raw_source is None:
        return None
    if not isinstance(raw_source, str) or not raw_source.strip():
        return None
    attempt = inputs.get("attempt") if isinstance(inputs, Mapping) else None
    tool_name = str(
        (outputs.get("tool_name") if isinstance(outputs, Mapping) else None)
        or payload.get("method_name")
        or "generated_code"
    )
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", tool_name).strip("-.")
    suffix = f"-attempt-{attempt}" if attempt is not None else ""
    content = raw_source[:_MAX_CODE_STRING]
    return {
        "name": f"{safe_name or 'generated-code'}{suffix}.py",
        "language": str(outputs.get("language") or "python"),
        "content": content,
        "truncated": len(content) < len(raw_source),
        "artifact_ref": None,
    }


def runtime_event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    """Map one persisted event into the canonical pipeline runtime payload."""

    raw_payload = event.get("payload")
    persisted_payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    event_type = str(event.get("event_type") or "runtime.event")
    name = (
        persisted_payload.get("name")
        or persisted_payload.get("method_name")
        or persisted_payload.get("step_name")
        or persisted_payload.get("engine_name")
        or event_type
    )
    description = persisted_payload.get("description")
    raw_artifact_refs = persisted_payload.get("artifact_refs", [])
    artifact_refs = (
        [str(item) for item in raw_artifact_refs]
        if isinstance(raw_artifact_refs, list)
        else []
    )
    code = _generated_code_payload(persisted_payload)
    detail_payload = {
        key: value
        for key, value in persisted_payload.items()
        if key
        not in {
            "artifact_refs",
            "description",
            "log_refs",
            "method_name",
            "name",
            "status",
            "step_name",
        }
    }
    if isinstance(detail_payload.get("outputs"), Mapping):
        detail_payload["outputs"] = {
            key: value
            for key, value in detail_payload["outputs"].items()
            if key not in {"artifact_refs", "source_code"}
        }
    if isinstance(detail_payload.get("inputs"), Mapping):
        detail_payload["inputs"] = {
            key: value
            for key, value in detail_payload["inputs"].items()
            if key != "source_code"
        }
    subscriber_payload = {
        "event_id": event.get("event_id"),
        "run_id": event.get("run_id"),
        "sequence": event.get("sequence"),
        "phase": str(event.get("phase") or "engine"),
        "event_type": event_type,
        "status": str(event.get("status") or "completed"),
        "name": str(name),
        "description": str(description) if description is not None else None,
        "artifact_refs": artifact_refs,
        "details": bounded_event_preview(detail_payload),
    }
    if code is not None:
        subscriber_payload["code"] = code
    return subscriber_payload
