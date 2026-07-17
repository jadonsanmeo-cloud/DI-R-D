"""Demo the minimal data + query -> runtime -> answer flow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence, TextIO

from dotenv import load_dotenv

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from basic_workflow import create_example_pipeline  # noqa: E402
from data_intelligence_sdk import UserQuery  # noqa: E402
from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    FinalResponse,
)  # noqa: E402
from data_intelligence_sdk.runtime import (  # noqa: E402
    ArtifactSettings,
    ConfigManager,
    FileRuntimeLogger,
    MethodHubSettings,
    OpenRouterSettings,
    SandboxSettings,
)
from run_pipeline import _load_package_json  # noqa: E402

load_dotenv()

DEFAULT_PACKAGE = EXAMPLES_DIR / "data_corpus_package" / "data_corpus_package.json"
DEFAULT_QUERY = "Summarize this data corpus package."


class _ResolvedConfigManager:
    """Expose resolved model and sandbox settings to the runtime factory."""

    def __init__(self, settings: OpenRouterSettings) -> None:
        self.settings = settings

    def openrouter_settings(self) -> OpenRouterSettings:
        return self.settings

    def method_hub_settings(self) -> MethodHubSettings:
        return MethodHubSettings(enabled=False)

    def sandbox_settings(self) -> SandboxSettings:
        return SandboxSettings(
            endpoint=os.environ.get("SANDBOX_URL", "http://localhost:8004"),
            enabled=os.environ.get("SANDBOX_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            workspace_id=os.environ.get("SANDBOX_WORKSPACE_ID"),
            token=os.environ.get("SANDBOX_TOKEN"),
        )

    def artifact_settings(self) -> ArtifactSettings:
        return ArtifactSettings(root=os.environ.get("ARTIFACT_ROOT", "artifacts"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run data + query -> spec -> engine -> sandbox -> answer + artifact."
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
        default="configs/development/proxy-openrouter.toml",
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
        help="Enable runtime debug logs and print response metadata.",
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
    if args.verbose:
        os.environ.setdefault("AXIOM_DEBUG", "true")
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    configured = ConfigManager(args.config).openrouter_settings()
    settings = OpenRouterSettings(
        model=args.model or configured.model,
        api_key=args.api_key or configured.api_key,
        base_url=args.base_url or configured.base_url,
    )
    if not settings.model:
        raise ValueError("LLM model is required. Set LLM_MODEL_NAME or pass --model.")
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


def _section(title: str, output: TextIO) -> None:
    print(f"\n=== {title} ===", file=output)


def _print_corpus(corpus: DataCorpusPackage, output: TextIO) -> None:
    _section("Data Corpus Package", output)
    print(f"Sources: {len(corpus.sources)}", file=output)
    for source in corpus.sources:
        print(f"- {source}", file=output)


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
    artifact_ref = response.metadata.get("artifact_ref")
    if artifact_ref:
        _section("Artifact", output)
        print(artifact_ref, file=output)
    if verbose:
        _section("Metadata", output)
        print(
            json.dumps(response.metadata, indent=2, ensure_ascii=False, default=str),
            file=output,
        )


def run(
    args: argparse.Namespace,
    *,
    output: TextIO = sys.stdout,
) -> int:
    corpus = _load_package_json(args.package)
    logger = None if args.no_trace else FileRuntimeLogger(args.trace_log_path)
    pipeline = _create_pipeline(args, logger)

    _print_corpus(corpus, output)
    _section("User Query", output)
    print(args.query, file=output)
    _section("Executing Runtime", output)
    response = pipeline.run(UserQuery(args.query), corpus)
    _print_response(response, output, verbose=args.verbose)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args, output=output)
    except Exception as exc:
        message = str(exc)
        if args.api_key:
            message = message.replace(args.api_key, "[REDACTED]")
        print(f"error: {message}", file=error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
