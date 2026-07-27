"""Generate a direct Markdown spec without running an engine."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    create_example_pipeline,
)
from data_intelligence_sdk.core.types import (
    SessionContext,
    UserContext,
    UserQuery,
)
from data_intelligence_sdk.runtime.logger import RuntimeLogger


PipelineFactory = Callable[..., object]


class _PreparationOnlyEngine:
    name = "spec-preparation-only"

    def can_handle(self, spec: object) -> bool:
        return False

    def run(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("The prepare-spec CLI cannot execute an engine.")


class _CliLogger(RuntimeLogger):
    def __init__(self, stream: TextIO, *, verbose: bool) -> None:
        self.stream = stream
        self.verbose = verbose

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        safe_payload = _redact(payload or {})
        if not self.verbose:
            safe_payload = {
                key: value
                for key, value in safe_payload.items()
                if key.endswith("count") or key in {"intent", "catalog_intent_id"}
            }
        print(
            json.dumps(
                {"event": event, "payload": safe_payload},
                ensure_ascii=True,
                default=str,
                sort_keys=True,
            ),
            file=self.stream,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Markdown spec without executing Report Engine."
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", default=".data/debug-spec/execution-spec.md")
    parser.add_argument("--user-id")
    parser.add_argument("--session-id")
    parser.add_argument("--config")
    parser.add_argument("--intent-service-url")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    pipeline_factory: PipelineFactory = create_example_pipeline,
    stream: TextIO | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output_stream = stream or sys.stderr
    workspace = (cwd or Path.cwd()).resolve()
    environment = environ or os.environ
    logger = _CliLogger(output_stream, verbose=args.verbose)
    phase = "spec_preparation"
    try:
        logger.log("spec_preparation.started")
        pipeline = pipeline_factory(
            logger=logger,
            config_path=_resolve_optional_path(args.config, workspace),
            use_llm_spec_builder=True,
            intent_service_base_url=args.intent_service_url,
            engine=_PreparationOnlyEngine(),
        )
        prepared = pipeline.prepare_markdown(  # type: ignore[attr-defined]
            UserQuery(
                text=args.query,
                user_id=args.user_id,
                session_id=args.session_id,
            ),
            SessionContext(session_id=args.session_id),
            UserContext(user_id=args.user_id),
        )
        logger.log(
            "spec_preparation.completed",
            {
                "intent": prepared.intent_analysis.intent,
                "catalog_intent_id": prepared.intent_analysis.catalog_intent_id,
                "preprocessing_step_count": len(
                    prepared.intent_analysis.preprocessing_steps
                ),
                "character_count": len(prepared.spec_markdown),
            },
        )

        phase = "markdown_write"
        output_path = _resolve_output_path(args.output, workspace)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(prepared.spec_markdown, encoding="utf-8")
        logger.log(
            "markdown_write.completed",
            {
                "output_path": str(output_path),
                "character_count": len(prepared.spec_markdown),
            },
        )
        return 0
    except Exception as exc:
        logger.log(
            f"{phase}.failed",
            {"error_type": type(exc).__name__, "environment_key_count": len(environment)},
        )
        return 1


def _resolve_input_path(path_value: str, workspace: Path) -> Path:
    path = Path(path_value)
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


def _resolve_optional_path(path_value: str | None, workspace: Path) -> str | None:
    return str(_resolve_input_path(path_value, workspace)) if path_value else None


def _resolve_output_path(path_value: str, workspace: Path) -> Path:
    path = Path(path_value)
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if any(token in normalized_key for token in ("api_key", "password", "secret", "token")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
