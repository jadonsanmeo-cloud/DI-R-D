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
DEFAULT_CONFIG_PATH = Path("configs/proxy-openrouter.toml")


@dataclass(frozen=True, slots=True)
class OpenRouterSettings:
    """Resolved settings needed to construct an OpenRouter chat model."""

    model: str | None = None
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"


@dataclass(frozen=True, slots=True)
class MethodHubSettings:
    """Connection settings for the remote Methods-Hub MCP server."""

    endpoint: str = "http://localhost:8000/mcp"
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class IntentServiceSettings:
    """Connection settings for the AXIOM Intent Service."""

    endpoint: str = "http://localhost:8005"
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    """Connection settings for an external sandbox execution service."""

    endpoint: str = "http://localhost:8004"
    enabled: bool = False
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSettings:
    """Filesystem root for per-run runtime artifacts."""

    root: str = "artifacts"


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

    def method_hub_settings(self) -> MethodHubSettings:
        """Resolve the Methods-Hub MCP endpoint from config or environment."""

        payload = self.load().get("method_hub", {})
        endpoint = _resolve_env_value(payload.get("endpoint"))
        raw_enabled = payload.get("enabled")
        if raw_enabled is None:
            raw_enabled = os.environ.get("METHODS_HUB_ENABLED", "false")
        enabled = str(_resolve_env_value(raw_enabled)).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return MethodHubSettings(
            endpoint=str(
                endpoint
                or os.environ.get("METHODS_HUB_MCP_URL")
                or "http://localhost:8000/mcp"
            ),
            enabled=enabled,
        )

    def sandbox_settings(self) -> SandboxSettings:
        """Resolve the sandbox endpoint and workspace from config or environment."""

        payload = self.load().get("sandbox", {})
        endpoint = _resolve_env_value(payload.get("endpoint"))
        raw_enabled = payload.get("enabled")
        if raw_enabled is None:
            raw_enabled = os.environ.get("SANDBOX_ENABLED", "false")
        enabled = str(_resolve_env_value(raw_enabled)).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        workspace_id = _resolve_env_value(payload.get("workspace_id"))
        return SandboxSettings(
            endpoint=str(
                endpoint or os.environ.get("SANDBOX_URL") or "http://localhost:8004"
            ),
            enabled=enabled,
            workspace_id=(
                str(workspace_id or os.environ.get("SANDBOX_WORKSPACE_ID"))
                if workspace_id or os.environ.get("SANDBOX_WORKSPACE_ID")
                else None
            ),
        )

    def intent_service_settings(self) -> IntentServiceSettings:
        """Resolve the required AXIOM Intent Service endpoint."""

        payload = self.load().get("intent_service", {})
        endpoint = _resolve_env_value(payload.get("endpoint"))
        raw_enabled = payload.get("enabled", False)
        enabled = str(_resolve_env_value(raw_enabled)).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return IntentServiceSettings(
            endpoint=str(endpoint or "http://localhost:8005"),
            enabled=enabled,
        )

    def artifact_settings(self) -> ArtifactSettings:
        """Resolve the local runtime artifact root."""

        payload = self.load().get("artifacts", {})
        root = _resolve_env_value(payload.get("root"))
        return ArtifactSettings(
            root=str(root or os.environ.get("ARTIFACT_ROOT") or "artifacts")
        )


@lru_cache(maxsize=None)
def get_config_manager(config_path: str | None = None) -> ConfigManager:
    """Return a cached config manager for a path."""

    return ConfigManager(config_path)
