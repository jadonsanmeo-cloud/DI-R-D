"""General-purpose fallback engine backed by MethodHub tools."""

from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
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
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool, create_schema_from_function

from data_intelligence_sdk.core.types import (
    DataCorpusPackage,
    EngineOutput,
    ExecutionSpec,
    UserContext,
)
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext


class GeneralPurposeEngine:
    """Fallback engine that answers with available MethodHub capabilities."""

    name = "general_purpose"

    def __init__(self, llm: object, *, allow_method_generation: bool = True) -> None:
        self.llm = llm
        self.allow_method_generation = allow_method_generation

    @classmethod
    def from_openrouter(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        allow_method_generation: bool = True,
    ) -> "GeneralPurposeEngine":
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when no api_key is passed."
            )
        model = model or os.environ.get("OPENROUTER_MODEL")
        if not model:
            raise ValueError("OPENROUTER_MODEL is required when no model is passed.")
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=api_key, base_url="https://openrouter.ai/api/v1", model=model
        )
        return cls(llm=llm, allow_method_generation=allow_method_generation)

    def can_handle(self, spec: ExecutionSpec) -> bool:
        return spec.engine_hint == self.name or spec.intent in {"custom", "unknown"}

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
                outputs={"result": output},
            )
            return output
        except Exception as exc:
            runtime.run_context.record_method_call(
                method_name, status="failed", inputs=inputs, outputs={"error": str(exc)}
            )
            raise

    def _build_agent_prompt(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str:
        method_lines = []
        for registered in runtime.method_hub.list_methods():
            description = registered.metadata.get("description", "")
            capabilities = ", ".join(registered.capability_names)
            method_lines.append(
                f"- {registered.name}: {description} Capabilities: {capabilities}"
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
        self, runtime: EngineRuntimeContext, corpus_package: DataCorpusPackage
    ) -> dict[str, object]:
        tools = {}
        for registered in runtime.method_hub.list_methods():

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
        self, runtime: EngineRuntimeContext, corpus_package: DataCorpusPackage
    ) -> list[StructuredTool]:
        tools = []
        for registered in runtime.method_hub.list_methods():

            def make_tool(method_name: str):
                method = runtime.method_hub.get(method_name)

                def tool(**kwargs: Any) -> Any:
                    kwargs = self._normalize_tool_inputs(
                        method_name, kwargs, corpus_package
                    )
                    return self._call_method(runtime, method_name, kwargs)

                tool.__name__ = method_name
                tool.__doc__ = getattr(method, "__doc__", None) or registered.metadata.get(
                    "description", method_name
                )
                schema_model = create_schema_from_function(
                    f"{method_name}_schema", method  # type: ignore[arg-type]
                )
                if hasattr(schema_model, "model_json_schema"):
                    args_schema = schema_model.model_json_schema()
                else:  # pragma: no cover - compatibility with Pydantic v1.
                    args_schema = schema_model.schema()
                return tool, args_schema

            tool, args_schema = make_tool(registered.name)

            tools.append(
                StructuredTool.from_function(
                    func=tool,
                    name=registered.name,
                    description=registered.metadata.get("description", registered.name),
                    args_schema=args_schema,
                    infer_schema=False,
                )
            )
        return tools

    def _run_agent(
        self,
        spec: ExecutionSpec,
        corpus_package: DataCorpusPackage,
        runtime: EngineRuntimeContext,
    ) -> str | None:
        prompt = self._build_agent_prompt(spec, corpus_package, runtime)
        tools = self._build_agent_tools(runtime, corpus_package)

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
        tools = self._build_langchain_tools(runtime, corpus_package)

        if create_react_agent is not None and AgentExecutor is not None:
            prompt = PromptTemplate.from_template(
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
