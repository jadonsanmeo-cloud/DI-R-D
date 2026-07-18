"""Select local documents, confirm an execution spec, and run the pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import termios
import tty
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Sequence, TextIO

from dotenv import load_dotenv

EXAMPLES_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLES_DIR.parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from basic_workflow import create_example_pipeline  # noqa: E402
from data_intelligence_sdk.core.types import (  # noqa: E402
    DataCorpusPackage,
    ExecutionSpec,
    FinalResponse,
    PreparedExecution,
    UserQuery,
)
from data_intelligence_sdk.runtime import (  # noqa: E402
    ConfigManager,
    FileRuntimeLogger,
    OpenRouterSettings,
)

DEFAULT_DOCUMENTS_DIR = EXAMPLES_DIR / "naph_corpus" / "parsed" / "documents"
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "proxy-openrouter.toml"
DEFAULT_ENV_FILE = PROJECT_ROOT / "docker" / ".env"


class UserCancelled(Exception):
    """Raised when the interactive user cancels the workflow."""


class _ResolvedConfigManager:
    """Override model settings while preserving other runtime configuration."""

    def __init__(
        self,
        manager: ConfigManager,
        settings: OpenRouterSettings,
    ) -> None:
        self._manager = manager
        self._settings = settings

    def openrouter_settings(self) -> OpenRouterSettings:
        return self._settings

    def __getattr__(self, name: str) -> object:
        return getattr(self._manager, name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select documents, confirm an execution spec, and run the pipeline."
        )
    )
    parser.add_argument(
        "--documents-dir",
        default=str(DEFAULT_DOCUMENTS_DIR),
        help="Directory containing selectable data source files.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Model and runtime configuration TOML path.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Environment file used by configuration placeholders.",
    )
    parser.add_argument("--model", help="Model name override.")
    parser.add_argument("--api-key", help="Provider API key override.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL override.")
    parser.add_argument(
        "--trace-log-path",
        default="logs/pipeline.log",
        help="Structured operational log path.",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Disable structured operational file logging.",
    )
    return parser


def _create_pipeline(args: argparse.Namespace) -> object:
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    manager = ConfigManager(args.config)
    configured = manager.openrouter_settings()
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

    logger = None if args.no_trace else FileRuntimeLogger(args.trace_log_path)
    return create_example_pipeline(
        config_manager=_ResolvedConfigManager(manager, settings),
        model=settings.model,
        api_key=settings.api_key,
        use_llm_spec_builder=True,
        logger=logger,
    )


def _document_files(directory: str | Path) -> list[Path]:
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Document directory does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Document path is not a directory: {root}")
    files = sorted(
        (path for path in root.iterdir() if path.is_file()),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise ValueError(f"No document files found in: {root}")
    return files


def _read_terminal_key(stream: TextIO) -> str:
    character = stream.read(1)
    if character == "":
        raise EOFError
    if character == "\x03":
        raise KeyboardInterrupt
    if character == "\x04":
        raise EOFError
    if character == "\x1b":
        suffix = stream.read(2)
        if suffix == "[A":
            return "up"
        if suffix == "[B":
            return "down"
        return "ignore"
    if character == " ":
        return "toggle"
    if character in {"\r", "\n"}:
        return "confirm"
    if character.lower() == "q":
        return "quit"
    return "ignore"


def _render_selector(
    files: list[Path],
    cursor: int,
    selected: set[int],
    output: TextIO,
) -> None:
    print("\x1b[2J\x1b[H", end="", file=output)
    print("Select data sources", file=output)
    print("Space toggle | Up/Down move | Enter confirm | q quit", file=output)
    print("", file=output)
    for index, path in enumerate(files):
        marker = "x" if index in selected else " "
        pointer = ">" if index == cursor else " "
        print(f"{pointer} [{marker}] {path.name}", file=output)
    if not selected:
        print("\nSelect at least one file before pressing Enter.", file=output)
    output.flush()


def _select_with_keys(
    files: list[Path],
    read_key: Callable[[], str],
    output: TextIO,
) -> list[Path]:
    cursor = 0
    selected: set[int] = set()
    while True:
        _render_selector(files, cursor, selected, output)
        key = read_key()
        if key == "up":
            cursor = (cursor - 1) % len(files)
        elif key == "down":
            cursor = (cursor + 1) % len(files)
        elif key == "toggle":
            if cursor in selected:
                selected.remove(cursor)
            else:
                selected.add(cursor)
        elif key == "confirm" and selected:
            return [files[index] for index in sorted(selected)]
        elif key == "quit":
            raise UserCancelled


def select_documents(
    files: list[Path],
    *,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    key_reader: Callable[[], str] | None = None,
) -> list[Path]:
    """Interactively select one or more document paths."""

    if key_reader is not None:
        return _select_with_keys(files, key_reader, output)
    if not input_stream.isatty() or not output.isatty():
        raise RuntimeError("Interactive file selection requires a TTY terminal.")

    descriptor = input_stream.fileno()
    previous = termios.tcgetattr(descriptor)
    print("\x1b[?25l", end="", file=output, flush=True)
    try:
        tty.setcbreak(descriptor)
        return _select_with_keys(
            files,
            lambda: _read_terminal_key(input_stream),
            output,
        )
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        print("\x1b[?25h", file=output, flush=True)


def _section(title: str, output: TextIO) -> None:
    print(f"\n=== {title} ===", file=output)


def _prompt_line(prompt: str, input_stream: TextIO, output: TextIO) -> str:
    print(prompt, end="", file=output, flush=True)
    value = input_stream.readline()
    if value == "":
        raise UserCancelled
    return value.strip()


def _prompt_non_empty(
    prompt: str,
    input_stream: TextIO,
    output: TextIO,
) -> str:
    while True:
        value = _prompt_line(prompt, input_stream, output)
        if value:
            return value
        print("Value cannot be empty.", file=output)


def _print_spec(title: str, spec: ExecutionSpec, output: TextIO) -> None:
    _section(title, output)
    print(
        json.dumps(asdict(spec), indent=2, ensure_ascii=False, default=str),
        file=output,
    )


def _confirm_or_revise(
    pipeline: object,
    prepared: PreparedExecution,
    *,
    input_stream: TextIO,
    output: TextIO,
) -> ExecutionSpec:
    current = prepared.spec
    _print_spec("Draft Execution Spec", current, output)
    while True:
        choice = _prompt_line(
            "Choose [confirm/revise/quit]: ",
            input_stream,
            output,
        ).lower()
        if choice in {"confirm", "c"}:
            current.confirmed = True
            return current
        if choice in {"revise", "r"}:
            feedback = _prompt_non_empty(
                "Feedback: ",
                input_stream,
                output,
            )
            current = pipeline.revise_spec(prepared, current, feedback)
            _print_spec("Revised Execution Spec", current, output)
            continue
        if choice in {"quit", "q"}:
            raise UserCancelled
        print("Please choose confirm, revise, or quit.", file=output)


def _artifact_path(pipeline: object, artifact_ref: str) -> Path | None:
    if not artifact_ref.startswith("artifact://"):
        return None
    run_id = artifact_ref.removeprefix("artifact://").split("/", 1)[0]
    artifact_store = getattr(pipeline, "artifact_store", None)
    root = getattr(artifact_store, "root", None)
    return (Path(root) / run_id).resolve() if root is not None else None


def _print_response(
    pipeline: object,
    response: FinalResponse,
    output: TextIO,
) -> None:
    _section("Engine", output)
    print(response.metadata.get("engine_name", "unknown"), file=output)
    _section("Final Response", output)
    print(response.answer, file=output)
    artifact_ref = response.metadata.get("artifact_ref")
    if artifact_ref:
        _section("Artifact", output)
        print(f"Reference: {artifact_ref}", file=output)
        path = _artifact_path(pipeline, str(artifact_ref))
        if path is not None:
            print(f"Filesystem: {path}", file=output)


def _finalize_cancelled(prepared: PreparedExecution | None) -> None:
    if prepared is not None and prepared.run_artifact is not None:
        prepared.run_artifact.finalize(
            status="failed",
            failure_phase="user_confirmation",
            error="UserCancelled: interactive run cancelled",
        )


def run(
    args: argparse.Namespace,
    *,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    key_reader: Callable[[], str] | None = None,
    pipeline_factory: Callable[[argparse.Namespace], object] = _create_pipeline,
) -> int:
    """Run the interactive select, spec, confirm, and execute workflow."""

    prepared: PreparedExecution | None = None
    try:
        files = _document_files(args.documents_dir)
        selected = select_documents(
            files,
            input_stream=input_stream,
            output=output,
            key_reader=key_reader,
        )
        _section("Data Corpus Package", output)
        for path in selected:
            print(f"- {path}", file=output)

        _section("User Query", output)
        query_text = _prompt_non_empty("> ", input_stream, output)
        pipeline = pipeline_factory(args)
        corpus = DataCorpusPackage(sources=[str(path.resolve()) for path in selected])
        prepared = pipeline.prepare_spec(UserQuery(query_text), corpus)
        confirmed = _confirm_or_revise(
            pipeline,
            prepared,
            input_stream=input_stream,
            output=output,
        )
        _section("Executing Runtime", output)
        response = pipeline.execute_confirmed_spec(prepared, confirmed)
        _print_response(pipeline, response, output)
        return 0
    except (UserCancelled, KeyboardInterrupt, EOFError):
        _finalize_cancelled(prepared)
        print("\nCancelled.", file=output)
        return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args, input_stream=input_stream, output=output)
    except Exception as exc:
        message = str(exc)
        if args.api_key:
            message = message.replace(args.api_key, "[REDACTED]")
        print(f"error: {message}", file=error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
