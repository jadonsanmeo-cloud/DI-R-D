"""General-purpose data analysis through one request-scoped Deep Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents._models import get_model_identifier, get_model_provider
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage, SystemMessage

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.tools import create_execute_python_tool

AgentFactory = Callable[..., object]

_HIDDEN_DEEP_AGENT_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "write_file",
    }
)


class GeneralPurposeEngine:
    """Analyze staged data with one Deep Agent and one execution tool."""

    name = "general_purpose"

    def __init__(
        self,
        llm: object | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        agent_factory: AgentFactory = create_deep_agent,
        allow_method_generation: bool = True,
        max_execution_attempts: int = 3,
    ) -> None:
        del allow_method_generation
        self.llm = llm or self._build_openrouter_llm(
            model=model,
            api_key=api_key,
            config_path=config_path,
            config_manager=config_manager,
        )
        self.agent_factory = agent_factory
        self.max_execution_attempts = max(1, max_execution_attempts)

    def _build_openrouter_llm(
        self,
        *,
        model: str | None,
        api_key: str | None,
        config_path: str | Path | None,
        config_manager: ConfigManager | None,
    ) -> object:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        resolved_key = (
            api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when no api_key is passed."
            )
        resolved_model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_model:
            raise ValueError("LLM_MODEL_NAME is required when no model is passed.")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=resolved_key,
            base_url=settings.base_url,
            model=resolved_model,
        )

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent in {
            "reason",
            "general",
            "unknown",
        }

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del user_context
        if runtime.sandbox is None:
            raise RuntimeError(
                "GeneralPurposeEngine requires a request-scoped sandbox."
            )

        runtime.run_context.record_step(
            "deep_agent_started",
            inputs={
                "objective": spec.objective,
                "source_paths": runtime.sandbox.source_paths,
            },
        )
        execute_python = create_execute_python_tool(runtime)
        self._register_minimal_profile()
        agent = self.agent_factory(
            model=self.llm,
            tools=[execute_python],
            system_prompt=self._system_prompt(spec, corpus_package, runtime),
            backend=runtime.sandbox.backend,
            subagents=[],
            middleware=[
                _RequireSuccessfulExecutionMiddleware(
                    runtime,
                    max_attempts=self.max_execution_attempts,
                )
            ],
            name="sandbox-general-analysis",
        )
        result = None
        try:
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": spec.objective,
                        }
                    ]
                }
            )
        except RuntimeError:
            if (
                _last_successful_execution_result(runtime)
                is not _NO_SUCCESSFUL_EXECUTION
                or _execution_attempt_count(runtime) < self.max_execution_attempts
            ):
                raise
            fallback_observation = execute_python.invoke(
                {
                    "code": _trusted_profile_code(
                        list(runtime.sandbox.source_paths.values())
                    )
                }
            )
            if not fallback_observation.get("success"):
                raise RuntimeError(
                    "The trusted data-profile fallback failed: "
                    + str(fallback_observation.get("stderr") or "unknown error")
                )
        successful_result = _last_successful_execution_result(runtime)
        if successful_result is _NO_SUCCESSFUL_EXECUTION:
            attempt_count = _execution_attempt_count(runtime)
            raise RuntimeError(
                "Deep Agent completed without a successful execute_python call "
                f"after {attempt_count} attempt(s)."
            )
        answer = (
            _last_message_text(result).strip()
            if result is not None
            else self._synthesize_fallback_answer(spec, successful_result)
        )
        if not answer:
            answer = json.dumps(
                successful_result,
                ensure_ascii=False,
                default=str,
            )
        runtime.run_context.record_step(
            "deep_agent_completed",
            outputs={"answer": answer},
        )
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=answer,
            metadata={
                "sources": list(corpus_package.sources),
                "sandbox_sources": dict(runtime.sandbox.source_paths),
            },
        )

    def _register_minimal_profile(self) -> None:
        identifier = get_model_identifier(self.llm)
        provider = get_model_provider(self.llm)
        if not identifier:
            raise RuntimeError(
                "Deep Agent tool restriction requires a model with a resolvable "
                "model identifier."
            )
        if ":" in identifier:
            profile_key = identifier
        elif provider:
            profile_key = f"{provider}:{identifier}"
        else:
            raise RuntimeError(
                "Deep Agent tool restriction requires a model with a resolvable "
                "provider."
            )
        register_harness_profile(
            profile_key,
            HarnessProfile(
                excluded_tools=_HIDDEN_DEEP_AGENT_TOOLS,
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            ),
        )

    def _system_prompt(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str:
        if runtime.sandbox is None:
            raise RuntimeError("The request sandbox is unavailable.")
        source_payload = [
            {
                "original": source,
                "sandbox_path": runtime.sandbox.source_paths.get(source),
            }
            for source in corpus_package.sources
        ]
        return (
            "You are the only analysis agent for this request. Use the staged data "
            "to answer the objective.\n\n"
            "You have exactly one action: execute_python. Pass a complete Python "
            "analysis program in its code argument. The runtime persists every "
            "attempt as an artifact before execution. The program must read only "
            "the staged sandbox paths listed below, perform the analysis, and "
            "assign its final JSON-serializable value to a top-level variable "
            "named result. Always call execute_python. The code runs in a fresh "
            "Python namespace: helper names such as read_file are not defined. "
            "Use Python imports plus open, pathlib, pandas, openpyxl, xlrd, pyarrow, "
            "or pypdf to read the staged paths directly. The Python program is "
            "only for data inspection and analysis: return compact structured "
            "data such as schema, counts, metrics, samples, and findings in "
            "result. Do not generate HTML, Markdown, CSS, JavaScript, or report "
            "prose inside the Python program; format the final response only "
            "after successful execution. Write normal multi-line Python rather "
            "than compressing the program into semicolon-separated statements. "
            "If execution fails, use stderr to rewrite the complete program and "
            "call execute_python again, for at most "
            f"{self.max_execution_attempts} attempts. Base the final answer only "
            "on successful execution output; never invent data.\n\n"
            f"Objective: {spec.objective}\n"
            f"Constraints: {json.dumps(spec.constraints, default=str)}\n"
            "Staged sources:\n"
            f"{json.dumps(source_payload, indent=2, default=str)}"
        )

    def _synthesize_fallback_answer(
        self,
        spec: ExecutionSpec,
        successful_result: Any,
    ) -> str:
        result_json = json.dumps(
            successful_result,
            ensure_ascii=False,
            default=str,
        )
        try:
            response = self.llm.invoke(
                [
                    SystemMessage(
                        content=(
                            "Use only the supplied successful data-analysis result "
                            "to answer the objective. Do not invent facts. Follow the "
                            "requested output format. For HTML, return one complete "
                            "valid HTML document without Markdown fences."
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"Objective: {spec.objective}\n"
                            f"Analysis result: {result_json}"
                        )
                    ),
                ]
            )
        except Exception:
            return result_json
        return _message_text(response).strip() or result_json


class _RequireSuccessfulExecutionMiddleware(AgentMiddleware):
    """Require tool retries until execution succeeds or reaches its limit."""

    name = "RequireSuccessfulExecutionMiddleware"

    def __init__(
        self,
        runtime: EngineRuntimeContext,
        *,
        max_attempts: int,
    ) -> None:
        self.runtime = runtime
        self.max_attempts = max(1, max_attempts)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        if (
            _last_successful_execution_result(self.runtime)
            is not _NO_SUCCESSFUL_EXECUTION
        ):
            return handler(request)
        attempt_count = _execution_attempt_count(self.runtime)
        if attempt_count >= self.max_attempts:
            raise RuntimeError(
                "execute_python failed "
                f"{attempt_count} time(s); the execution retry limit is "
                f"{self.max_attempts}."
            )
        request = request.override(tool_choice="execute_python")
        return handler(request)


_NO_SUCCESSFUL_EXECUTION = object()


def _execution_attempt_count(runtime: EngineRuntimeContext) -> int:
    run_artifact = runtime.run_artifact
    if run_artifact is None:
        return 0
    try:
        manifest = json.loads(run_artifact.manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 0
    attempts = manifest.get("attempts", [])
    return len(attempts) if isinstance(attempts, list) else 0


def _last_successful_execution_result(runtime: EngineRuntimeContext) -> Any:
    run_artifact = runtime.run_artifact
    if run_artifact is None:
        return _NO_SUCCESSFUL_EXECUTION
    try:
        manifest = json.loads(run_artifact.manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _NO_SUCCESSFUL_EXECUTION
    for attempt in reversed(manifest.get("attempts", [])):
        if not attempt.get("success"):
            continue
        execution_ref = str(attempt.get("execution_artifact_ref") or "")
        filename = execution_ref.rsplit("/", 1)[-1]
        if not filename:
            continue
        execution_path = run_artifact.root / "executions" / filename
        try:
            observation = json.loads(execution_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        return observation.get("result")
    return _NO_SUCCESSFUL_EXECUTION


def _trusted_profile_code(source_paths: list[str]) -> str:
    """Build deterministic sandbox code for common local data formats."""

    paths_json = json.dumps(source_paths, ensure_ascii=True)
    return f"""
from pathlib import Path
import pandas as pd

source_paths = {paths_json}
profiles = []

def frame_profile(path, frame):
    clean_sample = frame.head(5).where(pd.notna(frame), None)
    numeric = frame.select_dtypes(include="number")
    return {{
        "path": path,
        "kind": "table",
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "columns": [str(column) for column in frame.columns],
        "dtypes": {{str(column): str(dtype) for column, dtype in frame.dtypes.items()}},
        "missing_values": {{
            str(column): int(count)
            for column, count in frame.isna().sum().items()
        }},
        "unique_counts": {{
            str(column): int(frame[column].nunique(dropna=True))
            for column in frame.columns
        }},
        "numeric_summary": numeric.describe().to_dict() if not numeric.empty else {{}},
        "sample_rows": clean_sample.to_dict(orient="records"),
    }}

for source_path in source_paths:
    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        profiles.append(frame_profile(source_path, pd.read_csv(path)))
    elif suffix == ".tsv":
        profiles.append(frame_profile(source_path, pd.read_csv(path, sep="\\t")))
    elif suffix in {{".xlsx", ".xls"}}:
        profiles.append(frame_profile(source_path, pd.read_excel(path)))
    elif suffix == ".parquet":
        profiles.append(frame_profile(source_path, pd.read_parquet(path)))
    elif suffix in {{".json", ".jsonl"}}:
        profiles.append(
            frame_profile(
                source_path,
                pd.read_json(path, lines=suffix == ".jsonl"),
            )
        )
    elif suffix == ".pdf":
        from pypdf import PdfReader
        pages = [page.extract_text() or "" for page in PdfReader(path).pages]
        profiles.append({{
            "path": source_path,
            "kind": "document",
            "page_count": len(pages),
            "character_count": sum(len(page) for page in pages),
            "preview": "\\n".join(pages)[:4000],
        }})
    elif suffix in {{".txt", ".md", ".log"}}:
        text = path.read_text(encoding="utf-8", errors="replace")
        profiles.append({{
            "path": source_path,
            "kind": "document",
            "line_count": len(text.splitlines()),
            "character_count": len(text),
            "preview": text[:4000],
        }})
    else:
        profiles.append({{
            "path": source_path,
            "kind": "binary",
            "size_bytes": int(path.stat().st_size),
        }})

result = {{"sources": profiles}}
""".strip()


def _last_message_text(result: object) -> str:
    if not isinstance(result, dict):
        return str(result)
    messages = result.get("messages") or []
    if not messages:
        raise RuntimeError("Deep Agent returned no messages.")
    return _message_text(messages[-1])


def _message_text(message: object) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                text_parts.append(block)
        if text_parts:
            return "\n".join(text_parts)
    if content is None:
        return ""
    return str(content)
