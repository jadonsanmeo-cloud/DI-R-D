"""Interactively demo query + DataCorpusPackage -> workflow -> response."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from dotenv import load_dotenv

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from basic_workflow import create_example_pipeline  # noqa: E402
from data_intelligence_sdk import UserQuery  # noqa: E402
from data_intelligence_sdk.core.types import (  # noqa: E402
    DataCorpusPackage,
    ExecutionSpec,
    FinalResponse,
    PreparedExecution,
)
from data_intelligence_sdk.runtime import (  # noqa: E402
    ConfigManager,
    FileRuntimeLogger,
    OpenRouterSettings,
)
from run_pipeline import _load_package_json  # noqa: E402

load_dotenv()

DEFAULT_PACKAGE = EXAMPLES_DIR / "data_corpus_package" / "data_corpus_package.json"
DEFAULT_QUERY = "Summarize this data corpus package."
InputFunction = Callable[[str], str]


class _ResolvedConfigManager:
    """Expose one resolved provider configuration to all pipeline LLM clients."""

    def __init__(self, settings: OpenRouterSettings) -> None:
        self.settings = settings

    def openrouter_settings(self) -> OpenRouterSettings:
        return self.settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Demo query + DataCorpusPackage -> spec confirmation -> workflow -> response."
        )
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="User query.")
    parser.add_argument(
        "--package",
        default=str(DEFAULT_PACKAGE),
        help="Path to a data_corpus_package.json manifest.",
    )
    parser.add_argument("--model", help="Model name override.")
    parser.add_argument("--api-key", help="Provider API key override.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL override.")
    parser.add_argument(
        "--config",
        default="configs/proxy-openrouter.toml",
        help="Model configuration TOML path.",
    )
    parser.add_argument(
        "--env-file",
        default="docker/.env",
        help="Environment file used by TOML env placeholders.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full spec, evidence, trace, and response metadata.",
    )
    parser.add_argument(
        "--trace-log-path",
        default="logs/pipeline.log",
        help="Structured pipeline log path.",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable structured pipeline file logging.",
    )
    return parser


def _create_pipeline(args: argparse.Namespace, logger: object | None) -> object:
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    configured = ConfigManager(args.config).openrouter_settings()
    settings = OpenRouterSettings(
        model=args.model or configured.model,
        api_key=args.api_key or configured.api_key,
        base_url=args.base_url or configured.base_url,
    )
    if not settings.model:
        raise ValueError(
            "LLM model is required. Set LLM_MODEL_NAME or pass --model."
        )
    if not settings.api_key:
        raise ValueError(
            "Provider API key is required. Set OPENROUTER_API_KEY or pass --api-key."
        )
    if not settings.base_url:
        raise ValueError("Provider base URL is required. Pass --base-url.")

    return create_example_pipeline(
        config_manager=_ResolvedConfigManager(settings),
        model=settings.model,
        api_key=settings.api_key,
        use_llm_spec_builder=True,
        logger=logger,
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _section(title: str, output: TextIO) -> None:
    print(f"\n=== {title} ===", file=output)


def _print_corpus(corpus: DataCorpusPackage, output: TextIO) -> None:
    _section("Data Corpus Package", output)
    print(f"Sources: {len(corpus.sources)}", file=output)
    for source in corpus.sources:
        print(f"- {source}", file=output)
    print(f"Schemas: {'yes' if corpus.schemas else 'no'}", file=output)
    print(f"Metadata: {'yes' if corpus.metadata else 'no'}", file=output)


def _print_spec(spec: ExecutionSpec, output: TextIO, *, verbose: bool) -> None:
    _section("Execution Spec", output)
    if verbose:
        print(json.dumps(_jsonable(spec), indent=2, ensure_ascii=False), file=output)
        return
    print(f"Intent: {spec.intent}", file=output)
    print(f"Objective: {spec.objective}", file=output)
    print("Data requirements:", file=output)
    for requirement in spec.data_requirements or ["(none)"]:
        print(f"- {requirement}", file=output)
    capabilities = [item.name for item in spec.capability_requirements]
    capability_text = ", ".join(capabilities) if capabilities else "(none)"
    print(f"Capabilities: {capability_text}", file=output)
    print(f"Engine hint: {spec.engine_hint or '(automatic)'}", file=output)
    if spec.constraints:
        print(
            f"Constraints: {json.dumps(spec.constraints, ensure_ascii=False, default=str)}",
            file=output,
        )


def _print_response(
    response: FinalResponse,
    output: TextIO,
    *,
    verbose: bool,
) -> None:
    _section("Engine", output)
    print(response.metadata.get("engine_name", "unknown"), file=output)
    _section("Final Response", output)
    print(response.answer, file=output)
    if verbose:
        _section("Evidence And Trace", output)
        print(
            json.dumps(
                {
                    "evidence": _jsonable(response.evidence),
                    "metadata": _jsonable(response.metadata),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            file=output,
        )


def _confirm_or_revise(
    pipeline: object,
    prepared: PreparedExecution,
    input_fn: InputFunction,
    output: TextIO,
    *,
    verbose: bool,
) -> ExecutionSpec | None:
    current_spec = prepared.spec
    revision_count = 0
    max_revisions = int(getattr(pipeline, "max_spec_revision_rounds", 3))

    while True:
        _print_spec(current_spec, output, verbose=verbose)
        try:
            action = input_fn(
                "Decision [c]onfirm / [r]evise / [q]uit: "
            ).strip().lower()
        except EOFError:
            print("\nNo confirmation received; workflow stopped.", file=output)
            return None

        if action in {"c", "confirm"}:
            current_spec.confirmed = True
            prepared.spec = current_spec
            return current_spec
        if action in {"q", "quit"}:
            print("Workflow stopped before engine execution.", file=output)
            return None
        if action not in {"r", "revise"}:
            print("Enter c, r, or q.", file=output)
            continue
        if revision_count >= max_revisions:
            raise RuntimeError(
                f"Maximum spec revision rounds exceeded ({max_revisions})."
            )

        try:
            feedback = input_fn("Revision feedback: ").strip()
        except EOFError:
            print("\nNo revision feedback received; workflow stopped.", file=output)
            return None
        if not feedback:
            print("Revision feedback cannot be empty.", file=output)
            continue

        current_spec = pipeline.revise_spec(prepared, current_spec, feedback)
        prepared.spec = current_spec
        revision_count += 1


def run(
    args: argparse.Namespace,
    *,
    input_fn: InputFunction = input,
    output: TextIO = sys.stdout,
) -> int:
    corpus = _load_package_json(args.package)
    logger = None if args.no_trace else FileRuntimeLogger(args.trace_log_path)
    pipeline = _create_pipeline(args, logger)

    _print_corpus(corpus, output)
    _section("User Query", output)
    print(args.query, file=output)
    _section("Preparing Workflow", output)
    prepared = pipeline.prepare_spec(UserQuery(args.query), corpus)
    print(f"Intent: {prepared.intent}", file=output)

    confirmed_spec = _confirm_or_revise(
        pipeline,
        prepared,
        input_fn,
        output,
        verbose=args.verbose,
    )
    if confirmed_spec is None:
        return 0

    _section("Executing Confirmed Spec", output)
    response = pipeline.execute_confirmed_spec(prepared, confirmed_spec)
    _print_response(response, output, verbose=args.verbose)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: InputFunction = input,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args, input_fn=input_fn, output=output)
    except Exception as exc:
        message = str(exc)
        if args.api_key:
            message = message.replace(args.api_key, "[REDACTED]")
        print(f"error: {message}", file=error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
