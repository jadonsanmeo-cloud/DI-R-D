from __future__ import annotations

import re


_LANGUAGE_PREFIX = re.compile(
    r"^\s*(?P<language>en|zh|vi)(?:[-_][a-z]{2})?"
    r"(?:\s*(?:[:|,-])\s*|\s+)(?P<message>.+?)\s*$",
    re.IGNORECASE,
)


def extract_language_prefix(message: str) -> tuple[str | None, str]:
    """Extract an explicit `en`, `zh`, or `vi` prefix from a user query."""
    value = (message or "").strip()
    match = _LANGUAGE_PREFIX.match(value)
    if not match:
        return None, value
    return match.group("language").lower(), match.group("message").strip()
