"""General-purpose fallback engine backed by MethodHub tools."""

from __future__ import annotations

import os
import inspect
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:  # pragma: no cover - exercised with monkeypatched tests when unavailable.
    from langchain.agents import AgentExecutor, create_react_agent
except ImportError:  # pragma: no cover
    AgentExecutor = None  # type: ignore[assignment]
    create_react_agent = None  # type: ignore[assignment]
try:  # pragma: no cover - exercised with monkeypatched tests when unavailable.
    from langchain.agents import create_agent
except ImportError:  # pragma: no cover
    create_agent = None  # type: ignore[assignment]

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.config import ConfigManager, get_config_manager
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class _LocalStructuredTool:
    """Tiny tool wrapper used when LangChain tool classes are unavailable."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        func: Any,
        args_schema: dict[str, Any],
    ) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self._func = func

    def invoke(self, args: Any) -> Any:
        if hasattr(args, "model_dump"):
            kwargs = args.model_dump()
        elif hasattr(args, "dict"):
            kwargs = args.dict()
        elif isinstance(args, dict):
            kwargs = args
        else:
            kwargs = dict(args)
        return self._func(**kwargs)

    __call__ = invoke


def _build_signature_schema(method: Any) -> dict[str, Any]:
    """Build a minimal JSON-schema-like payload from a Python callable signature."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}, "required": []}

    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        schema: dict[str, Any] = {"title": parameter.name}
        annotation = parameter.annotation
        if annotation in (int, "int"):
            schema["type"] = "integer"
        elif annotation in (float, "float"):
            schema["type"] = "number"
        elif annotation in (bool, "bool"):
            schema["type"] = "boolean"
        elif annotation in (list, "list"):
            schema["type"] = "array"
        elif annotation in (dict, "dict"):
            schema["type"] = "object"
        else:
            schema["type"] = "string"
        if parameter.default is inspect._empty:
            required.append(parameter.name)
        else:
            schema["default"] = parameter.default
        properties[parameter.name] = schema

    return {"type": "object", "properties": properties, "required": required}


class GeneralPurposeEngine:
    """Fallback engine that answers with available MethodHub capabilities."""

    name = "general_purpose"

    def __init__(
        self,
        llm: object | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        allow_method_generation: bool = True,
    ) -> None:
        self.llm = llm or self._build_openrouter_llm(
            model=model,
            api_key=api_key,
            config_path=config_path,
            config_manager=config_manager,
        )
        self.allow_method_generation = allow_method_generation

    def _build_openrouter_llm(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
    ) -> object:
        manager = config_manager or get_config_manager(
            str(config_path) if config_path is not None else None
        )
        settings = manager.openrouter_settings()
        api_key = api_key or settings.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when no api_key is passed."
            )
        model = model or settings.model or os.environ.get("OPENROUTER_MODEL")
        if not model:
            raise ValueError("LLM_MODEL_NAME is required when no model is passed.")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(api_key=api_key, base_url=settings.base_url, model=model)

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent in {"reason", "unknown"}

    def run(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
        user_context: UserContext | None = None,
    ) -> EngineOutput:
        del user_context
        runtime.run_context.record_step(
            "general_purpose_start", inputs={"objective": spec.objective}
        )
        if not corpus_package.sources:
            return runtime.run_context.build_output(
                engine_name=self.name,
                result="No data source was provided. Add a CSV path to DataCorpusPackage.sources.",
                metadata={"sources": []},
            )

        agent_result = self._run_agent(spec, corpus_package, runtime)
        if agent_result:
            return runtime.run_context.build_output(
                engine_name=self.name,
                result=agent_result,
                metadata={"sources": corpus_package.sources},
            )

        if self.allow_method_generation:
            return self._handle_method_generation(spec, corpus_package, runtime)

        runtime.run_context.record_step("method_generation_disabled", status="skipped")
        return runtime.run_context.build_output(
            engine_name=self.name,
            result="The required capability is not available, and method generation is not configured.",
            metadata={"sources": corpus_package.sources},
        )

    def _call_method(
        self, runtime: EngineRuntimeContext, method_name: str, inputs: dict[str, Any]
    ) -> Any:
        try:
            method = runtime.method_hub.get(method_name)
            output = method(**inputs)
            runtime.run_context.record_method_call(
                method_name,
                status="completed",
                inputs=inputs,
                outputs={"result": _to_dict(output)},
            )
            return output
        except Exception as exc:
            runtime.run_context.record_method_call(
                method_name, status="failed", inputs=inputs, outputs={"error": str(exc)}
            )
            raise

    def _selected_methods(
        self,
        spec: ExecutionSpec,
        runtime: EngineRuntimeContext,
        corpus_package: DataCorpusPackage,
    ) -> list[object]:
        del corpus_package
        if spec.capability_requirements:
            selected = runtime.method_hub.select_for_requirements(
                spec.capability_requirements
            )
            if selected:
                selected_names = {method.name for method in selected}
                requested_capabilities = {
                    requirement.name.strip()
                    for requirement in spec.capability_requirements
                    if requirement.name.strip()
                }
                for registered in runtime.method_hub.list_methods(executable_only=True):
                    if registered.name in selected_names:
                        continue
                    if not requested_capabilities.intersection(
                        registered.capability_names
                    ):
                        continue
                    selected.append(registered)
                    selected_names.add(registered.name)
                return selected
        return runtime.method_hub.list_methods(executable_only=True)

    def _build_agent_prompt(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str:
        selected_methods = self._selected_methods(spec, runtime, corpus_package)
        method_lines = []
        for registered in selected_methods:
            description = (
                getattr(registered, "description", "")
                or registered.metadata.get("description", "")
            )
            capabilities = ", ".join(registered.capability_names)
            trust_level = registered.trust_level
            method_lines.append(
                f"- {registered.name} [{registered.status}, {trust_level}]: "
                f"{description} Capabilities: {capabilities}"
            )
        sources = (
            "\n".join(f"- {source}" for source in corpus_package.sources) or "- none"
        )
        methods = "\n".join(method_lines) or "- none"
        return (
            "Answer the user query using MethodHub tools for factual data access.\n"
            "Do not guess facts about data files; call tools instead.\n\n"
            "Source usage rules:\n"
            "- Use CSV tools only for local file paths ending in .csv.\n"
            "- Use search_vector_chunks for postgresql:// sources with schema=vectordb.\n"
            "- Do not pass database URIs to CSV tools.\n\n"
            f"User objective: {spec.objective}\n\n"
            f"Data sources:\n{sources}\n\n"
            f"Available tools:\n{methods}\n"
        )

    def _build_react_prompt(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> object:
        prompt_text = (
            self._build_agent_prompt(spec, corpus_package, runtime)
            + "\nUse this ReAct format:\n"
            + "Question: {input}\n"
            + "Thought: reason about the next step\n"
            + "Action: one of [{tool_names}]\n"
            + "Action Input: a JSON object with tool arguments\n"
            + "Observation: tool result\n"
            + "... repeat Thought/Action/Action Input/Observation as needed\n"
            + "Thought: I now know the final answer\n"
            + "Final Answer: the answer\n\n"
            + "Tools:\n{tools}\n\n"
            + "Question: {input}\n"
            + "Data source path: {source_path}\n"
            + "{agent_scratchpad}"
        )
        try:
            from langchain_core.prompts import PromptTemplate
        except ImportError:
            return prompt_text
        return PromptTemplate.from_template(prompt_text)

    def _find_vectordb_source(self, corpus_package: DataCorpusPackage) -> str | None:
        for source in corpus_package.sources:
            source_text = str(source)
            parsed = urlparse(source_text)
            if parsed.scheme in {"postgres", "postgresql"}:
                query = parse_qs(parsed.query)
                if query.get("schema", [""])[0] == "vectordb":
                    return source_text
            if source_text.rstrip("/").endswith("/vectordb"):
                return source_text
        package_metadata = corpus_package.metadata.get("package")
        if isinstance(package_metadata, dict):
            vectordb = package_metadata.get("vectordb")
            if isinstance(vectordb, str):
                return vectordb
        return None

    def _normalize_tool_inputs(
        self,
        method_name: str,
        inputs: dict[str, Any],
        corpus_package: DataCorpusPackage,
    ) -> dict[str, Any]:
        if method_name != "search_vector_chunks":
            return inputs
        vectordb_source = self._find_vectordb_source(corpus_package)
        if vectordb_source is None:
            return inputs
        normalized = dict(inputs)
        parsed = urlparse(str(normalized.get("vectordb", "")))
        if parsed.scheme not in {"postgres", "postgresql"}:
            normalized["vectordb"] = vectordb_source
        return normalized

    def _build_agent_tools(
        self,
        spec: ExecutionSpec,
        runtime: EngineRuntimeContext,
        corpus_package: DataCorpusPackage,
    ) -> dict[str, object]:
        tools = {}
        for registered in self._selected_methods(spec, runtime, corpus_package):

            def make_tool(method_name: str):
                def tool(args: dict[str, Any]) -> Any:
                    args = self._normalize_tool_inputs(
                        method_name, args, corpus_package
                    )
                    return self._call_method(runtime, method_name, args)

                return tool

            tools[registered.name] = make_tool(registered.name)
        return tools

    def _build_langchain_tools(
        self,
        spec: ExecutionSpec,
        runtime: EngineRuntimeContext,
        corpus_package: DataCorpusPackage,
    ) -> list[object]:
        tools = []
        try:
            from langchain_core.tools import StructuredTool  # type: ignore
        except ImportError:
            StructuredTool = None  # type: ignore[assignment]
        for registered in self._selected_methods(spec, runtime, corpus_package):

            def make_tool(method_name: str):
                method = runtime.method_hub.get(method_name)

                def tool(**kwargs: Any) -> Any:
                    kwargs = self._normalize_tool_inputs(
                        method_name, kwargs, corpus_package
                    )
                    return self._call_method(runtime, method_name, kwargs)

                tool.__name__ = method_name
                tool.__doc__ = getattr(
                    method, "__doc__", None
                ) or registered.metadata.get("description", method_name)
                args_schema = _build_signature_schema(method)
                if StructuredTool is not None:
                    return StructuredTool.from_function(
                        func=tool,
                        name=registered.name,
                        description=registered.metadata.get(
                            "description", registered.name
                        ),
                        infer_schema=True,
                    )
                return _LocalStructuredTool(
                    name=registered.name,
                    description=registered.metadata.get(
                        "description", registered.name
                    ),
                    func=tool,
                    args_schema=args_schema,
                )

            tools.append(make_tool(registered.name))
        return tools

    def _run_agent(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str | None:
        prompt = self._build_agent_prompt(spec, corpus_package, runtime)
        tools = self._build_agent_tools(spec, runtime, corpus_package)

        if hasattr(self.llm, "run"):
            response = self.llm.run(prompt=prompt, tools=tools)
            for call in response.get("tool_calls", []):
                tool_name = call["name"]
                args = call.get("args", {})
                tools[tool_name](args)
            return str(response.get("final_answer", ""))

        agent_result = self._run_langchain_agent(spec, corpus_package, runtime)
        if agent_result:
            return agent_result

        return None

    def _run_langchain_agent(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str | None:
        if not hasattr(self.llm, "invoke"):
            return None
        tools = self._build_langchain_tools(spec, runtime, corpus_package)

        if create_react_agent is not None and AgentExecutor is not None:
            prompt = self._build_react_prompt(spec, corpus_package, runtime)
            agent = create_react_agent(self.llm, tools, prompt)
            executor = AgentExecutor(agent=agent, tools=tools)
            result = executor.invoke(
                {"input": spec.objective, "source_path": corpus_package.sources[0]}
            )
            return self._extract_langchain_agent_output(result)

        if create_agent is not None:
            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=self._build_agent_prompt(spec, corpus_package, runtime),
            )
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": f"{spec.objective}\nData source path: {corpus_package.sources[0]}",
                        }
                    ]
                }
            )
            return self._extract_langchain_agent_output(result)

        return (
            "LangChain ReAct agent support is not available. Install a LangChain "
            "version that exposes create_react_agent and AgentExecutor or create_agent."
        )

    def _extract_langchain_agent_output(self, result: Any) -> str:
        if isinstance(result, dict):
            if "output" in result:
                return str(result.get("output", ""))
            messages = result.get("messages")
            if messages:
                last_message = messages[-1]
                content = getattr(last_message, "content", None)
                if content is None and isinstance(last_message, dict):
                    content = last_message.get("content")
                if content is not None:
                    return str(content)
                return str(last_message)
        return str(result)

    def _handle_method_generation(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> EngineOutput:
        if (
            runtime.interface_builder is None
            or runtime.sandbox_executor is None
            or runtime.interface_registry is None
        ):
            runtime.run_context.record_step(
                "method_generation_unavailable",
                status="skipped",
                description="Required interface builder, sandbox executor, or interface registry is missing.",
            )
            return runtime.run_context.build_output(
                engine_name=self.name,
                result="The required capability is not available, and method generation is not configured.",
                metadata={"sources": corpus_package.sources},
            )

        requirement = (
            spec.capability_requirements[0] if spec.capability_requirements else None
        )
        if requirement is None:
            runtime.run_context.record_step(
                "method_generation_unavailable", status="skipped"
            )
            return runtime.run_context.build_output(
                engine_name=self.name,
                result="The required capability is not available, and method generation is not configured.",
                metadata={"sources": corpus_package.sources},
            )

        interface = runtime.interface_builder.propose(requirement, corpus_package)
        if interface.trust_level != "generated_validated":
            interface.trust_level = "generated_unvalidated"
        sandbox_result = runtime.sandbox_executor.validate(interface, {}, None)
        if sandbox_result.status == "completed":
            interface.trust_level = "generated_validated"
            runtime.interface_registry.register(interface)
            runtime.run_context.record_step(
                "method_generation_validated", outputs={"interface": interface.name}
            )
            return runtime.run_context.build_output(
                engine_name=self.name,
                result=f"Generated and validated interface: {interface.name}",
                metadata={
                    "sources": corpus_package.sources,
                    "interface_defs": [interface],
                    "sandbox_results": [sandbox_result],
                },
            )
        runtime.run_context.record_step(
            "method_generation_failed",
            status="failed",
            outputs={"interface": interface.name},
        )
        return runtime.run_context.build_output(
            engine_name=self.name,
            result=f"Generated interface validation failed: {interface.name}",
            metadata={
                "sources": corpus_package.sources,
                "interface_defs": [interface],
                "sandbox_results": [sandbox_result],
            },
        )


def _to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return {"result": value}
