"""Runtime configuration loading and caching."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_ENV_PATTERN = re.compile(r"^\$\{env:([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")
DEFAULT_CONFIG_PATH = Path("configs/development/proxy-openrouter.toml")


@dataclass(frozen=True, slots=True)
class OpenRouterSettings:
    """Resolved settings needed to construct an OpenRouter chat model."""

    model: str | None = None
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"


def _resolve_env_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _ENV_PATTERN.match(value)
    if match is None:
        return value
    env_name, default = match.groups()
    return os.environ.get(env_name, default or "")


class ConfigManager:
    """Loads project config once and resolves provider settings on demand."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
        if not path.is_absolute():
            path = Path.cwd() / path
        self.config_path = path
        self._payload: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """Return cached raw TOML payload for this config path."""

        if self._payload is None:
            if not self.config_path.exists():
                self._payload = {}
            else:
                with self.config_path.open("rb") as config_file:
                    self._payload = tomllib.load(config_file)
        return self._payload

    def openrouter_settings(self) -> OpenRouterSettings:
        """Resolve OpenRouter model settings from cached config and env."""

        payload = self.load()
        llms = payload.get("models", {}).get("llms", [])
        selected = next(
            (
                llm
                for llm in llms
                if str(llm.get("provider", "")).lower()
                in {"openrouter", "proxy/openrouter"}
            ),
            llms[0] if llms else {},
        )
        resolved = {key: _resolve_env_value(value) for key, value in selected.items()}
        return OpenRouterSettings(
            model=resolved.get("name") or os.environ.get("OPENROUTER_MODEL"),
            api_key=resolved.get("api_key") or os.environ.get("OPENROUTER_API_KEY"),
            base_url=resolved.get("api_base")
            or resolved.get("api_url")
            or "https://openrouter.ai/api/v1",
        )


@lru_cache(maxsize=None)
def get_config_manager(config_path: str | None = None) -> ConfigManager:
    """Return a cached config manager for a path."""

    return ConfigManager(config_path)
