from __future__ import annotations

import inspect
import json
import os
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    InterfaceDefinition,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.sandbox.executor import SandboxRunResult

PLAN_AGENT_PROMPT = """
You are the Data Planning Agent. Your task is to decompose the user's report goal into a directed acyclic graph (DAG) of data-processing steps.

# INPUT
1. `user_goal` (String): The user's report request.
2. `corpus_package` (JSON): The database/vector database schema and data catalog.

# INSTRUCTIONS
1. Compare `user_goal` with `corpus_package` to identify the necessary tables and columns.
2. Break the goal into small extraction and calculation steps.
3. Identify dependencies between steps. If Step B needs the result of Step A, add Step A to Step B's `depends_on` list.
4. Define `fallback_step` behavior when a primary step has no data.

# OUTPUT
Return only a JSON array with this structure:
[
  {
    "step_id": "step_1",
    "description": "Detailed data retrieval goal for this step.",
    "required_data": {"tables": ["table_A"], "columns": ["col_1", "col_2"]},
    "depends_on": [],
    "fallback_step": "Fallback action when data is missing"
  },
  {
    "step_id": "step_2",
    "description": "...",
    "required_data": {"tables": ["table_B"], "columns": ["col_3"]},
    "depends_on": ["step_1"],
    "fallback_step": null
  }
]
""".strip()

ROUTER_AGENT_PROMPT = """
You are the Routing Agent. Your task is to compare a step request with the available tool inventory and decide whether to use an existing tool or create a new one.

# INPUT
1. `step_request` (JSON): The current step's `description` and `required_data`.
2. `method_hub` (JSON): Existing tools with `tool_name`, `description`, and `parameters_schema`.

# INSTRUCTIONS
1. Read the `description` in `step_request`.
2. Inspect `method_hub` and find a tool whose `description` exactly satisfies the step request.
3. If a suitable tool exists, extract arguments from `step_request` so they match the tool's `parameters_schema`.
4. If no suitable tool exists, set the creation flag.

# OUTPUT
Return only JSON in this format:
{
  "use_existing_tool": true,
  "tool_name": "existing_tool_name_or_null",
  "arguments": {"parameter_name": "value"}
}
""".strip()

CODE_AGENT_PROMPT = """
You are the Data Programming Agent. Your task is to generate Python source code that extracts and processes data.

# INPUT
1. `step_request` (JSON): The step `description` and `required_data`.
2. `schema_catalog` (JSON): Real table structure and data dictionary.
3. `error_logs` (String/Null): Sandbox error logs from the previous failed run.
4. `validation_feedback` (String/Null): Logic feedback from the Validator Agent from the previous failed run.

# INSTRUCTIONS
1. Read `task_description` and match it exactly against `schema_catalog`. Do not invent table or column names that do not exist.
2. Write Python source code using Pandas or SQLAlchemy as a single function.
3. The function must handle empty data. Return an empty list `[]` instead of raising an exception.
4. Include full type hints and a complete docstring.
5. If `error_logs` or `validation_feedback` is provided, analyze the cause and fix the source code in this output.

# OUTPUT
Return only JSON in this format:
{
  "tool_name": "snake_case_function_name",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "param_1": {"type": "string", "description": "..."}
    },
    "required": ["param_1"]
  },
  "source_code": "complete Python source code"
}
""".strip()

VALIDATOR_AGENT_PROMPT = """
You are the Validation Agent. Your task is to check the technical and logical correctness of generated source code.

# INPUT
1. `step_description` (String): The original business requirement.
2. `source_code` (String): Source code written by the Code Agent.
3. `sandbox_logs` (String): Command execution status or error logs.
4. `sample_data` (JSON): Sample output data from the sandbox run.

# INSTRUCTIONS
1. Evaluate `sandbox_logs`. If there is any runtime or syntax error, immediately mark the result as Fail.
2. Evaluate `sample_data` and compare data types with the required output.
3. Compare `sample_data` with `step_description`. Ensure the business logic is correct. For example, if the request asks for a count, the result should be a number, not an array.
4. If there is an issue, write detailed feedback so the Code Agent can fix it.

# OUTPUT
Return only JSON in this format:
{
  "status": "Pass",
  "feedback": null,
  "validated_code": "original source code"
}
""".strip()

DATASCIENCE_AGENT_PROMPT = """
You are the Data Science Agent. Your task is to interpret numeric results and summarize trends from real data.

# INPUT
1. `step_id` (String): Step identifier.
2. `step_description` (String): Business analysis request.
3. `raw_data` (JSON): Real data returned by a database tool. It may be an empty array `[]`.

# INSTRUCTIONS
1. Check `raw_data`. If it is empty, produce this analysis message: "No data matched the conditions".
2. If data exists, compute basic statistics such as maximum, minimum, average, or identify anomalies.
3. Summarize the analysis in a concise paragraph of about 3-5 sentences that directly answers `step_description`.
4. Reduce `raw_data` into aggregated data. Do not retain unnecessary raw columns, to avoid memory overflow.

# OUTPUT
Return only JSON in this format:
{
  "step_id": "...",
  "analysis_summary": "Short paragraph summarizing key findings from the data.",
  "aggregated_data": {"key_statistic": "value"}
}
""".strip()

REPORT_AGENT_PROMPT = """
You are the Report Agent. Your task is to synthesize separate analysis blocks into a complete written document.

# INPUT
1. `user_goal` (String): The user's overall goal.
2. `all_steps_data` (JSON Array): Completed step data with `step_id`, `analysis_summary`, and `aggregated_data`.

# INSTRUCTIONS
1. Structure the report in a logical order: Introduction, Key Metrics, Analysis Details, Conclusion.
2. Use `analysis_summary` from the steps to build the report narrative.
3. Present `aggregated_data` as Markdown tables when useful.
4. Remove duplicate information and keep the text coherent.
5. Use strict Markdown.

# OUTPUT
Return the final Markdown report directly. Do not wrap it in JSON.
""".strip()

GENERATED_TOOL_CAPABILITY = "generated_report_data_tool"


def _json_dumps(value: Any) -> str:
    return json.dumps(_to_jsonable(value), ensure_ascii=False, indent=2, default=str)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _extract_message_content(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    if isinstance(response, dict):
        if "content" in response:
            return str(response["content"])
        if "output" in response:
            return str(response["output"])
        messages = response.get("messages")
        if messages:
            return _extract_message_content(messages[-1])
    return str(response)


def _parse_json_payload(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    return json.loads(stripped)


def _source_summary(sources: list[str]) -> str:
    if not sources:
        return "No sources were provided."
    return "\n".join(f"- {source}" for source in sources)


def _dataset_summary(catalog: dict[str, Any]) -> str:
    datasets = catalog.get("datasets", [])
    if not datasets:
        return "No catalog datasets were provided."
    lines = []
    for dataset in datasets:
        name = dataset.get("name", "unnamed")
        kind = dataset.get("kind", "dataset")
        description = dataset.get("description", "No description provided.")
        lines.append(f"- {name} ({kind}): {description}")
    return "\n".join(lines)


def _schema_summary(schemas: dict[str, Any]) -> str:
    lines = []
    for table_name, table in schemas.get("tables", {}).items():
        columns = ", ".join(table.get("columns", [])) or "no columns listed"
        lines.append(f"- table {table_name}: {columns}")
    for collection_name, collection in schemas.get("vector_collections", {}).items():
        columns = ", ".join(collection.get("columns", [])) or "no columns listed"
        lines.append(f"- vector collection {collection_name}: {columns}")
    return "\n".join(lines) if lines else "No schema metadata was provided."


def _table_columns(table: dict[str, Any]) -> list[str]:
    columns = table.get("columns", [])
    if isinstance(columns, dict):
        return list(columns.keys())
    if isinstance(columns, list):
        return [str(column) for column in columns]
    return []


def _first_source(sources: list[str], suffix: str | None = None) -> str | None:
    for source in sources:
        if suffix is None or str(source).lower().endswith(suffix):
            return str(source)
    return str(sources[0]) if sources else None


def _method_parameters_schema(method: object) -> dict[str, Any]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}, "required": []}

    properties = {}
    required = []
    for name, parameter in signature.parameters.items():
        if name in {"self", "args", "kwargs"}:
            continue
        properties[name] = {
            "type": "string",
            "description": f"Argument `{name}` for the tool.",
        }
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _method_hub_payload(runtime: EngineRuntimeContext) -> list[dict[str, Any]]:
    payload = []
    for registered in runtime.method_hub.list_methods():
        payload.append(
            {
                "tool_name": registered.name,
                "description": registered.metadata.get("description", ""),
                "parameters_schema": registered.metadata.get(
                    "parameters_schema",
                    _method_parameters_schema(registered.method),
                ),
                "capability_names": registered.capability_names,
                "trust_level": registered.trust_level,
            }
        )
    return payload


def _corpus_payload(corpus_package: DataCorpusPackage) -> dict[str, Any]:
    return {
        "sources": corpus_package.sources,
        "schemas": corpus_package.schemas,
        "metadata": corpus_package.metadata,
    }


class _PromptAgent:
    def __init__(self, name: str, system_prompt: str, llm: object | None) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def _invoke_text(self, **inputs: Any) -> str | None:
        if self.llm is None or not hasattr(self.llm, "invoke"):
            return None
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=self.system_prompt),
                    ("user", "\n\n".join(f"{key}:\n{{{key}}}" for key in inputs)),
                ]
            )
            prompt_value = prompt.invoke(
                {
                    key: _json_dumps(value)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in inputs.items()
                }
            )
            response = self.llm.invoke(prompt_value)
            return _extract_message_content(response)
        except Exception:
            return None

    def _invoke_json(self, **inputs: Any) -> Any | None:
        text = self._invoke_text(**inputs)
        if text is None:
            return None
        try:
            return _parse_json_payload(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


class PlanAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("plan_agent", PLAN_AGENT_PROMPT, llm)

    def run(
        self, user_goal: str, corpus_package: DataCorpusPackage
    ) -> list[dict[str, Any]]:
        payload = self._invoke_json(
            user_goal=user_goal, corpus_package=_corpus_payload(corpus_package)
        )
        if isinstance(payload, list):
            return [step for step in payload if isinstance(step, dict)]
        return self._fallback_plan(user_goal, corpus_package)

    def _fallback_plan(
        self, user_goal: str, corpus_package: DataCorpusPackage
    ) -> list[dict[str, Any]]:
        tables = corpus_package.schemas.get("tables", {})
        if isinstance(tables, dict) and tables:
            steps = []
            for index, (table_name, table) in enumerate(tables.items(), start=1):
                steps.append(
                    {
                        "step_id": f"step_{index}",
                        "description": (
                            f"Analyze `{table_name}` data relevant to the report goal: "
                            f"{user_goal}"
                        ),
                        "required_data": {
                            "tables": [table_name],
                            "columns": _table_columns(table),
                        },
                        "depends_on": [],
                        "fallback_step": "Use schema and catalog metadata if no rows are available.",
                    }
                )
            return steps

        return [
            {
                "step_id": "step_1",
                "description": f"Create a corpus overview for the report goal: {user_goal}",
                "required_data": {"tables": [], "columns": []},
                "depends_on": [],
                "fallback_step": "Use available source, catalog, and schema metadata.",
            }
        ]


class RouterAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("router_agent", ROUTER_AGENT_PROMPT, llm)

    def run(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        payload = self._invoke_json(step_request=step_request, method_hub=method_hub)
        if isinstance(payload, dict) and "use_existing_tool" in payload:
            payload.setdefault("arguments", {})
            return payload
        return self._fallback_route(step_request, method_hub, sources)

    def _fallback_route(
        self,
        step_request: dict[str, Any],
        method_hub: list[dict[str, Any]],
        sources: list[str],
    ) -> dict[str, Any]:
        if not method_hub:
            return {"use_existing_tool": False, "tool_name": None, "arguments": {}}

        csv_source = _first_source(sources, ".csv")
        if csv_source:
            scan_tool = next(
                (tool for tool in method_hub if tool["tool_name"] == "scan_csv"),
                None,
            )
            if scan_tool is not None:
                return {
                    "use_existing_tool": True,
                    "tool_name": "scan_csv",
                    "arguments": {"path": csv_source},
                }

        description = str(step_request.get("description", "")).lower()
        required_tables = {
            str(table).lower()
            for table in step_request.get("required_data", {}).get("tables", [])
        }
        for tool in method_hub:
            haystack = " ".join(
                [
                    str(tool.get("tool_name", "")),
                    str(tool.get("description", "")),
                    " ".join(map(str, tool.get("capability_names", []))),
                ]
            ).lower()
            if any(token in haystack for token in required_tables) or any(
                word in haystack for word in description.split()
            ):
                return {
                    "use_existing_tool": True,
                    "tool_name": tool["tool_name"],
                    "arguments": {},
                }

        return {"use_existing_tool": False, "tool_name": None, "arguments": {}}


class CodeAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("code_agent", CODE_AGENT_PROMPT, llm)

    def run(
        self,
        step_request: dict[str, Any],
        schema_catalog: dict[str, Any],
        error_logs: str | None = None,
        validation_feedback: str | None = None,
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_request=step_request,
            schema_catalog=schema_catalog,
            error_logs=error_logs,
            validation_feedback=validation_feedback,
        )
        if isinstance(payload, dict):
            payload.setdefault("tool_name", "generated_report_tool")
            payload.setdefault(
                "parameters_schema", {"type": "object", "properties": {}, "required": []}
            )
            payload.setdefault("source_code", "")
            return payload
        return {
            "tool_name": "generated_report_tool",
            "parameters_schema": {"type": "object", "properties": {}, "required": []},
            "source_code": (
                "from typing import Any\n\n"
                "def generated_report_tool(**kwargs: Any) -> list[Any]:\n"
                '    """Return no rows when code generation is unavailable."""\n'
                "    return []\n"
            ),
        }


class ValidatorAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("validator_agent", VALIDATOR_AGENT_PROMPT, llm)

    def run(
        self,
        step_description: str,
        source_code: str,
        sandbox_logs: str,
        sample_data: Any,
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_description=step_description,
            source_code=source_code,
            sandbox_logs=sandbox_logs,
            sample_data=sample_data,
        )
        if isinstance(payload, dict) and "status" in payload:
            return payload
        lowered_logs = sandbox_logs.lower()
        if "failed" in lowered_logs or "error" in lowered_logs:
            return {
                "status": "Fail",
                "feedback": sandbox_logs,
                "validated_code": None,
            }
        return {
            "status": "Pass",
            "feedback": None,
            "validated_code": source_code,
        }


class DataScienceAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("datascience_agent", DATASCIENCE_AGENT_PROMPT, llm)

    def run(
        self, step_id: str, step_description: str, raw_data: Any
    ) -> dict[str, Any]:
        payload = self._invoke_json(
            step_id=step_id,
            step_description=step_description,
            raw_data=raw_data,
        )
        if isinstance(payload, dict):
            payload.setdefault("step_id", step_id)
            payload.setdefault("analysis_summary", "No analysis summary was produced.")
            payload.setdefault("aggregated_data", {})
            return payload
        return self._fallback_analysis(step_id, step_description, raw_data)

    def _fallback_analysis(
        self, step_id: str, step_description: str, raw_data: Any
    ) -> dict[str, Any]:
        normalized = self._normalize_raw_data(raw_data)
        if not normalized:
            return {
                "step_id": step_id,
                "analysis_summary": "No data matched the conditions.",
                "aggregated_data": {"record_count": 0},
            }

        numeric_values: dict[str, list[float]] = {}
        for row in normalized:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                try:
                    numeric_values.setdefault(str(key), []).append(float(value))
                except (TypeError, ValueError):
                    continue

        aggregated: dict[str, Any] = {"record_count": len(normalized)}
        for key, values in numeric_values.items():
            if values:
                aggregated[f"{key}_min"] = min(values)
                aggregated[f"{key}_max"] = max(values)
                aggregated[f"{key}_average"] = sum(values) / len(values)

        return {
            "step_id": step_id,
            "analysis_summary": (
                f"The step `{step_id}` processed {len(normalized)} records for: "
                f"{step_description}"
            ),
            "aggregated_data": aggregated,
        }

    def _normalize_raw_data(self, raw_data: Any) -> list[Any]:
        if raw_data is None:
            return []
        if isinstance(raw_data, list):
            return raw_data
        if isinstance(raw_data, dict):
            for key in ("rows", "sample_rows", "data", "result"):
                value = raw_data.get(key)
                if isinstance(value, list):
                    return value
            return [raw_data]
        return [raw_data]


class ReportAgent(_PromptAgent):
    def __init__(self, llm: object | None) -> None:
        super().__init__("report_agent", REPORT_AGENT_PROMPT, llm)

    def run(
        self,
        user_goal: str,
        all_steps_data: list[dict[str, Any]],
        corpus_package: DataCorpusPackage,
    ) -> str:
        text = self._invoke_text(user_goal=user_goal, all_steps_data=all_steps_data)
        if text:
            return text
        return self._fallback_report(user_goal, all_steps_data, corpus_package)

    def _fallback_report(
        self,
        user_goal: str,
        all_steps_data: list[dict[str, Any]],
        corpus_package: DataCorpusPackage,
    ) -> str:
        catalog = corpus_package.metadata.get("catalog", {})
        if not isinstance(catalog, dict):
            catalog = {}
        lines = [
            "# Data Intelligence Report",
            "",
            "## Introduction",
            "",
            f"This report summarizes the available analysis for: {user_goal}.",
            "",
            "## Key Metrics",
            "",
        ]
        summary = catalog.get("summary")
        if summary:
            lines[6:6] = [str(summary), ""]
        if all_steps_data:
            for step in all_steps_data:
                aggregated = step.get("aggregated_data", {})
                if isinstance(aggregated, dict) and aggregated:
                    lines.extend(self._render_markdown_table(aggregated))
                    lines.append("")
        else:
            lines.extend(["No completed analysis steps were available.", ""])

        lines.extend(["## Analysis Details", ""])
        for step in all_steps_data:
            lines.extend(
                [
                    f"### {step.get('step_id', 'step')}",
                    "",
                    str(step.get("analysis_summary", "No analysis summary was produced.")),
                    "",
                ]
            )

        lines.extend(
            [
                "## Conclusion",
                "",
                "The workflow completed the available analysis steps and synthesized them into this report.",
                "",
                "## Sources",
                "",
                _source_summary(corpus_package.sources),
                "",
                "## Datasets",
                "",
                _dataset_summary(catalog),
                "",
                "## Schema",
                "",
                _schema_summary(corpus_package.schemas),
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _render_markdown_table(self, data: dict[str, Any]) -> list[str]:
        lines = ["| Metric | Value |", "| --- | --- |"]
        for key, value in data.items():
            rendered = _json_dumps(value) if isinstance(value, (dict, list)) else value
            lines.append(f"| {key} | {str(rendered).replace(chr(10), '<br>')} |")
        return lines


class ReportEngine:
    """Run the full report-generation agent workflow."""

    name = "report"

    def __init__(
        self,
        llm: object | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        max_generation_attempts: int = 2,
    ) -> None:
        self.llm = llm
        if self.llm is None:
            self.llm = self._try_build_openrouter_llm(
                model=model,
                api_key=api_key,
                config_path=config_path,
                config_manager=config_manager,
            )
        self.max_generation_attempts = max_generation_attempts
        self.plan_agent = PlanAgent(self.llm)
        self.router_agent = RouterAgent(self.llm)
        self.code_agent = CodeAgent(self.llm)
        self.validator_agent = ValidatorAgent(self.llm)
        self.datascience_agent = DataScienceAgent(self.llm)
        self.report_agent = ReportAgent(self.llm)

    def _try_build_openrouter_llm(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
    ) -> object | None:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        resolved_api_key = (
            api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        )
        resolved_model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not resolved_api_key or not resolved_model:
            return None

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=resolved_api_key,
            base_url=settings.base_url,
            model=resolved_model,
        )

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent == "report"

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del user_context
        runtime.run_context.record_step(
            "report_workflow_start",
            inputs={"objective": spec.objective, "sources": corpus_package.sources},
        )

        plan = self._run_plan_agent(spec, corpus_package, runtime)
        all_steps_data = []
        for step in self._sort_steps(plan):
            raw_data = self._route_and_collect_data(step, corpus_package, runtime)
            analysis = self._run_datascience_agent(step, raw_data, runtime)
            all_steps_data.append(analysis)

        report = self._run_report_agent(
            spec.objective, all_steps_data, corpus_package, runtime
        )
        generation_mode = "langchain" if self.llm is not None else "fallback"
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=report,
            metadata={
                "sources": corpus_package.sources,
                "report_format": "markdown",
                "generation_mode": generation_mode,
                "plan": plan,
                "all_steps_data": all_steps_data,
                "workflow": [
                    "Plan Agent",
                    "Router Agent",
                    "Code Agent",
                    "Sandbox",
                    "Validator Agent",
                    "Method Hub",
                    "DataScience Agent",
                    "Report Agent",
                ],
            },
        )

    def _run_plan_agent(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> list[dict[str, Any]]:
        plan = self.plan_agent.run(spec.objective, corpus_package)
        runtime.run_context.record_step(
            "plan_agent", inputs={"user_goal": spec.objective}, outputs={"plan": plan}
        )
        return plan

    def _route_and_collect_data(
        self,
        step: dict[str, Any],
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> Any:
        method_hub = _method_hub_payload(runtime)
        route = self.router_agent.run(step, method_hub, corpus_package.sources)
        runtime.run_context.record_step(
            "router_agent",
            inputs={"step_request": step, "method_hub": method_hub},
            outputs={"route": route},
        )
        if route.get("use_existing_tool") and route.get("tool_name"):
            return self._call_existing_tool(route, runtime)
        return self._generate_validate_register_and_run(step, corpus_package, runtime)

    def _call_existing_tool(
        self, route: dict[str, Any], runtime: EngineRuntimeContext
    ) -> Any:
        tool_name = str(route["tool_name"])
        arguments = route.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            method = runtime.method_hub.get(tool_name)
            result = method(**arguments)
            runtime.run_context.record_method_call(
                tool_name,
                status="completed",
                inputs=arguments,
                outputs={"result": result},
            )
            return result
        except Exception as exc:
            runtime.run_context.record_method_call(
                tool_name,
                status="failed",
                inputs=arguments,
                outputs={"error": str(exc)},
            )
            return []

    def _generate_validate_register_and_run(
        self,
        step: dict[str, Any],
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> Any:
        error_logs = None
        validation_feedback = None
        for attempt in range(1, self.max_generation_attempts + 1):
            code_spec = self.code_agent.run(
                step,
                _corpus_payload(corpus_package),
                error_logs=error_logs,
                validation_feedback=validation_feedback,
            )
            runtime.run_context.record_step(
                "code_agent",
                inputs={
                    "step_request": step,
                    "attempt": attempt,
                    "error_logs": error_logs,
                    "validation_feedback": validation_feedback,
                },
                outputs={"tool_name": code_spec.get("tool_name")},
            )
            interface = self._build_generated_interface(step, code_spec)
            sandbox_result = self._validate_in_sandbox(interface, runtime)
            runtime.run_context.record_step(
                "sandbox_validate",
                status=sandbox_result.status,
                inputs={"interface": interface.name},
                outputs={
                    "result": sandbox_result.result,
                    "error": sandbox_result.error,
                },
                artifact_refs=sandbox_result.artifact_refs,
                log_refs=sandbox_result.log_refs,
            )
            sandbox_logs = self._sandbox_logs(sandbox_result)
            validation = self.validator_agent.run(
                str(step.get("description", "")),
                str(code_spec.get("source_code", "")),
                sandbox_logs,
                sandbox_result.result,
            )
            validation_status = (
                "completed"
                if str(validation.get("status", "")).lower() == "pass"
                else "failed"
            )
            runtime.run_context.record_step(
                "validator_agent",
                status=validation_status,
                inputs={"step_description": step.get("description", "")},
                outputs={"validation": validation},
            )
            if validation_status == "completed":
                return self._register_generated_tool_and_run(
                    interface, step, runtime, sandbox_result.result
                )
            error_logs = sandbox_logs
            validation_feedback = str(validation.get("feedback", ""))

        return []

    def _build_generated_interface(
        self, step: dict[str, Any], code_spec: dict[str, Any]
    ) -> InterfaceDefinition:
        tool_name = str(code_spec.get("tool_name") or "generated_report_tool")
        return InterfaceDefinition(
            name=tool_name,
            description=str(step.get("description", "")),
            input_schema=code_spec.get("parameters_schema", {}),
            output_schema={"type": "array"},
            implementation_ref=str(code_spec.get("source_code", "")),
            source="generated",
            trust_level="generated_unvalidated",
            metadata={
                "capability_names": [GENERATED_TOOL_CAPABILITY],
                "source_code": code_spec.get("source_code", ""),
                "step_request": step,
            },
        )

    def _validate_in_sandbox(
        self, interface: InterfaceDefinition, runtime: EngineRuntimeContext
    ) -> SandboxRunResult:
        if runtime.sandbox_executor is None:
            return SandboxRunResult(
                status="failed",
                error="Sandbox executor is not configured.",
            )
        try:
            return runtime.sandbox_executor.validate(interface, {}, None)
        except Exception as exc:
            return SandboxRunResult(status="failed", error=str(exc))

    def _sandbox_logs(self, sandbox_result: SandboxRunResult) -> str:
        if sandbox_result.status == "completed":
            return "Success"
        return f"Error: {sandbox_result.error or 'Sandbox validation failed.'}"

    def _register_generated_tool_and_run(
        self,
        interface: InterfaceDefinition,
        step: dict[str, Any],
        runtime: EngineRuntimeContext,
        sample_data: Any,
    ) -> Any:
        interface.trust_level = "generated_validated"
        if runtime.interface_registry is not None:
            runtime.interface_registry.register(interface)

        if runtime.sandbox_executor is None:
            return sample_data if sample_data is not None else []

        def generated_tool(**kwargs: Any) -> Any:
            result = runtime.sandbox_executor.run(interface, kwargs, None)
            if result.status != "completed":
                raise RuntimeError(result.error or "Generated tool execution failed.")
            return result.result

        runtime.method_hub.register(
            interface.name,
            generated_tool,
            capability_names=[GENERATED_TOOL_CAPABILITY],
            trust_level="generated_validated",
            metadata={
                "description": interface.description or "",
                "parameters_schema": interface.input_schema,
                "source_code": interface.metadata.get("source_code", ""),
            },
        )
        runtime.run_context.record_step(
            "method_hub_register",
            inputs={"tool_name": interface.name},
            outputs={"trust_level": interface.trust_level},
        )
        try:
            result = generated_tool()
            runtime.run_context.record_method_call(
                interface.name,
                status="completed",
                inputs={},
                outputs={"result": result},
            )
            return result
        except Exception as exc:
            runtime.run_context.record_method_call(
                interface.name,
                status="failed",
                inputs={},
                outputs={"error": str(exc)},
            )
            return sample_data if sample_data is not None else []

    def _run_datascience_agent(
        self, step: dict[str, Any], raw_data: Any, runtime: EngineRuntimeContext
    ) -> dict[str, Any]:
        analysis = self.datascience_agent.run(
            str(step.get("step_id", "step")),
            str(step.get("description", "")),
            raw_data,
        )
        runtime.run_context.record_step(
            "datascience_agent",
            inputs={"step_id": step.get("step_id"), "raw_data": raw_data},
            outputs={"analysis": analysis},
        )
        return analysis

    def _run_report_agent(
        self,
        user_goal: str,
        all_steps_data: list[dict[str, Any]],
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str:
        report = self.report_agent.run(user_goal, all_steps_data, corpus_package)
        runtime.run_context.record_step(
            "report_agent",
            inputs={"user_goal": user_goal, "all_steps_data": all_steps_data},
            outputs={"report_format": "markdown"},
        )
        return report

    def _sort_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        remaining = {str(step.get("step_id", index)): step for index, step in enumerate(steps)}
        ordered = []
        while remaining:
            progressed = False
            for step_id, step in list(remaining.items()):
                depends_on = {str(item) for item in step.get("depends_on", [])}
                if depends_on.issubset(
                    {str(item.get("step_id", "")) for item in ordered}
                ):
                    ordered.append(step)
                    del remaining[step_id]
                    progressed = True
            if not progressed:
                ordered.extend(remaining.values())
                break
        return ordered
