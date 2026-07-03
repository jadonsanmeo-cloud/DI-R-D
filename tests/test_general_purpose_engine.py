import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_intelligence_sdk.core.types import (
    CapabilityRequirement,
    DataCorpusPackage,
    ExecutionSpec,
    InterfaceDefinition,
)
import data_intelligence_sdk.engines.general as general_module
from data_intelligence_sdk.engines.general import GeneralPurposeEngine
from data_intelligence_sdk.methods.csv import register_csv_methods
from data_intelligence_sdk.methods.vector import register_vector_methods
from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.interfaces import InMemoryInterfaceRegistry
from data_intelligence_sdk.runtime.method_hub import MethodHub
from data_intelligence_sdk.sandbox.executor import SandboxRunResult


class FakeBuilder:
    def propose(self, requirement, corpus_package):
        return InterfaceDefinition(
            name="generated_method", metadata={"capability_names": [requirement.name]}
        )


class FakeSandbox:
    def validate(self, interface, validation_inputs, resource_policy=None):
        return SandboxRunResult(status="completed", result={"validated": True})

    def run(self, interface, inputs, resource_policy=None):
        return SandboxRunResult(status="completed")


class FakeToolCallingAgent:
    def __init__(self, tool_calls, final_answer="agent answer"):
        self.tool_calls = tool_calls
        self.final_answer = final_answer
        self.prompts = []
        self.tools = []
        self.tool_results = []

    def run(self, *, prompt, tools):
        self.prompts.append(prompt)
        self.tools.append(tools)
        return {"tool_calls": self.tool_calls, "final_answer": self.final_answer}


class FakeSummarizingAgent(FakeToolCallingAgent):
    def run(self, *, prompt, tools):
        self.prompts.append(prompt)
        self.tools.append(tools)
        result = tools["scan_csv"]({"path": self.tool_calls[0]["args"]["path"]})
        self.tool_results.append(result)
        return {
            "tool_calls": [],
            "final_answer": f"columns: {', '.join(result['columns'])}",
        }


class FakeInvokeResponse:
    def __init__(self, content):
        self.content = content


class FakeInvokeModel:
    def __init__(self, content):
        self.content = content
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return FakeInvokeResponse(self.content)

class FakeChatOpenAI:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeChatOpenAI.instances.append(self)


class FakeAgentExecutor:
    instances = []

    def __init__(self, *, agent, tools):
        self.agent = agent
        self.tools = tools
        self.invocations = []
        FakeAgentExecutor.instances.append(self)

    def invoke(self, inputs):
        self.invocations.append(inputs)
        scan_tool = next(tool for tool in self.tools if tool.name == "scan_csv")
        scan_result = scan_tool.invoke({"path": inputs["source_path"]})
        return {"output": f"ReAct saw {scan_result['row_count']} rows"}

class FakeLangChainV1Agent:
    instances = []

    def __init__(self, *, model, tools, system_prompt):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.invocations = []
        FakeLangChainV1Agent.instances.append(self)

    def invoke(self, inputs):
        self.invocations.append(inputs)
        scan_tool = next(tool for tool in self.tools if tool.name == "scan_csv")
        scan_result = scan_tool.invoke({"path": inputs["messages"][0]["content"].split("Data source path: ")[1]})
        return {"messages": [FakeInvokeResponse(f"LangChain v1 saw {scan_result['row_count']} rows")]}


class GeneralPurposeEngineTests(unittest.TestCase):
    def test_constructor_requires_openrouter_api_key_without_injected_llm(self) -> None:
        old_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                GeneralPurposeEngine(model="some/model")
        finally:
            if old_key is not None:
                os.environ["OPENROUTER_API_KEY"] = old_key

    def test_constructor_requires_openrouter_model_without_injected_llm(self) -> None:
        old_model = os.environ.pop("OPENROUTER_MODEL", None)
        try:
            with self.assertRaisesRegex(ValueError, "LLM_MODEL_NAME"):
                GeneralPurposeEngine(api_key="key")
        finally:
            if old_model is not None:
                os.environ["OPENROUTER_MODEL"] = old_model

    def test_constructor_loads_openrouter_defaults_from_config_file(self) -> None:
        FakeChatOpenAI.instances = []
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "openrouter.toml"
            config_path.write_text(
                "[models]\n"
                "[[models.llms]]\n"
                "name = \"anthropic/claude-3.5-sonnet\"\n"
                "provider = \"openrouter\"\n"
                "api_base = \"https://openrouter.ai/api/v1\"\n"
                "api_key = \"${env:OPENROUTER_API_KEY}\"\n",
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "config-key"}, clear=True),
                patch.dict("sys.modules", {"langchain_openai": type("Module", (), {"ChatOpenAI": FakeChatOpenAI})}),
            ):
                engine = GeneralPurposeEngine(config_path=str(config_path))

        self.assertIs(engine.llm, FakeChatOpenAI.instances[0])
        self.assertEqual(
            FakeChatOpenAI.instances[0].kwargs,
            {
                "api_key": "config-key",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "anthropic/claude-3.5-sonnet",
            },
        )

    def test_constructor_loads_repo_proxy_openrouter_config_by_default(self) -> None:
        FakeChatOpenAI.instances = []

        with (
            patch.dict(
                os.environ,
                {
                    "LLM_MODEL_NAME": "openrouter/default-model",
                    "OPENROUTER_API_KEY": "default-config-key",
                },
                clear=True,
            ),
            patch.dict("sys.modules", {"langchain_openai": type("Module", (), {"ChatOpenAI": FakeChatOpenAI})}),
        ):
            GeneralPurposeEngine()

        self.assertEqual(
            FakeChatOpenAI.instances[0].kwargs,
            {
                "api_key": "default-config-key",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openrouter/default-model",
            },
        )

    def test_constructor_arguments_override_openrouter_config_file(self) -> None:
        FakeChatOpenAI.instances = []
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "openrouter.toml"
            config_path.write_text(
                "[models]\n"
                "[[models.llms]]\n"
                "name = \"config/model\"\n"
                "provider = \"openrouter\"\n"
                "api_key = \"config-key\"\n",
                encoding="utf-8",
            )

            with patch.dict(
                "sys.modules",
                {"langchain_openai": type("Module", (), {"ChatOpenAI": FakeChatOpenAI})},
            ):
                GeneralPurposeEngine(
                    model="argument/model",
                    api_key="argument-key",
                    config_path=str(config_path),
                )

        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["model"], "argument/model")
        self.assertEqual(FakeChatOpenAI.instances[0].kwargs["api_key"], "argument-key")

    def test_name_and_can_handle_general_specs(self) -> None:
        engine = GeneralPurposeEngine(llm=object())

        self.assertEqual(engine.name, "general_purpose")
        self.assertTrue(
            engine.can_handle(ExecutionSpec(intent="reason", objective="x"))
        )
        self.assertTrue(
            engine.can_handle(ExecutionSpec(intent="unknown", objective="x"))
        )

    def test_engine_lets_agent_call_method_hub_tools_and_records_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\nCA,complete,7\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)
            agent = FakeToolCallingAgent(
                tool_calls=[{"name": "count_csv", "args": {"path": str(csv_path)}}],
                final_answer="There are 2 rows.",
            )

            output = GeneralPurposeEngine(llm=agent).run(
                ExecutionSpec(intent="reason", objective="How many rows are there?"),
                DataCorpusPackage(sources=[str(csv_path)]),
                runtime,
            )

            self.assertEqual(output.result, "There are 2 rows.")
            self.assertIn("How many rows are there?", agent.prompts[0])
            self.assertIn(str(csv_path), agent.prompts[0])
            self.assertIn("count_csv", agent.tools[0])
            self.assertEqual(output.trace.method_calls[0].method_name, "count_csv")
            self.assertEqual(output.trace.method_calls[0].status, "completed")
            self.assertEqual(output.trace.method_calls[0].outputs["result"]["count"], 2)

    def test_engine_exposes_scan_csv_tool_to_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)
            agent = FakeToolCallingAgent(
                tool_calls=[{"name": "scan_csv", "args": {"path": str(csv_path)}}],
                final_answer="Columns are country, status, revenue.",
            )

            output = GeneralPurposeEngine(llm=agent).run(
                ExecutionSpec(
                    intent="reason", objective="What columns are in this file?"
                ),
                DataCorpusPackage(sources=[str(csv_path)]),
                runtime,
            )

            self.assertEqual(output.result, "Columns are country, status, revenue.")
            self.assertEqual(output.trace.method_calls[0].method_name, "scan_csv")

    def test_agent_prompt_guides_vectordb_sources_to_vector_search(self) -> None:
        method_hub = MethodHub()
        register_csv_methods(method_hub)
        register_vector_methods(method_hub)
        runtime = EngineRuntimeContext(method_hub=method_hub)

        prompt = GeneralPurposeEngine(llm=object())._build_agent_prompt(
            ExecutionSpec(intent="unknown", objective="What is the data about?"),
            DataCorpusPackage(
                sources=[
                    "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb",
                    "postgresql://demo:demo@localhost:5432/data_corpus",
                ]
            ),
            runtime,
        )

        self.assertIn("Use search_vector_chunks", prompt)
        self.assertIn("schema=vectordb", prompt)
        self.assertIn("Do not pass database URIs to CSV tools", prompt)

    def test_vector_search_tool_uses_package_vectordb_when_agent_passes_bad_reference(self) -> None:
        method_hub = MethodHub()

        def fake_search_vector_chunks(vectordb: str, query: str, limit: int = 5):
            return {"vectordb": vectordb, "query": query, "limit": limit}

        method_hub.register(
            "search_vector_chunks",
            fake_search_vector_chunks,
            capability_names=["search_vectordb"],
        )
        runtime = EngineRuntimeContext(method_hub=method_hub)
        agent = FakeToolCallingAgent(
            tool_calls=[
                {
                    "name": "search_vector_chunks",
                    "args": {"vectordb": "vectordb", "query": "What is the data about?"},
                }
            ],
            final_answer="Used vector search.",
        )
        vectordb_uri = "postgresql://demo:demo@localhost:5432/data_corpus?schema=vectordb"

        output = GeneralPurposeEngine(llm=agent).run(
            ExecutionSpec(intent="unknown", objective="What is the data about?"),
            DataCorpusPackage(
                sources=[
                    vectordb_uri,
                    "postgresql://demo:demo@localhost:5432/data_corpus",
                ]
            ),
            runtime,
        )

        self.assertEqual(output.result, "Used vector search.")
        self.assertEqual(output.trace.method_calls[0].inputs["vectordb"], vectordb_uri)

    def test_invoke_model_uses_langchain_react_agent_executor(self) -> None:
        FakeAgentExecutor.instances = []
        created_agents = []

        def fake_create_react_agent(llm, tools, prompt):
            created_agents.append({"llm": llm, "tools": tools, "prompt": prompt})
            return "react-agent"

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)
            model = FakeInvokeModel("unused")

            with (
                patch.object(general_module, "create_react_agent", fake_create_react_agent),
                patch.object(general_module, "AgentExecutor", FakeAgentExecutor),
            ):
                output = GeneralPurposeEngine(llm=model).run(
                    ExecutionSpec(
                        intent="reason", objective="What columns are in this file?"
                    ),
                    DataCorpusPackage(sources=[str(csv_path)]),
                    runtime,
                )

        self.assertEqual(output.result, "ReAct saw 1 rows")
        self.assertEqual(created_agents[0]["llm"], model)
        self.assertIn("scan_csv", [tool.name for tool in created_agents[0]["tools"]])
        self.assertEqual(FakeAgentExecutor.instances[0].agent, "react-agent")
        self.assertEqual(
            FakeAgentExecutor.instances[0].invocations[0],
            {"input": "What columns are in this file?", "source_path": str(csv_path)},
        )
        self.assertEqual(output.trace.method_calls[0].method_name, "scan_csv")
        self.assertEqual(
            output.trace.method_calls[0].inputs, {"path": str(csv_path)}
        )

    def test_langchain_react_path_does_not_use_hardcoded_tool_result_formatting(self) -> None:
        FakeAgentExecutor.instances = []

        def fake_create_react_agent(llm, tools, prompt):
            return "react-agent"

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\nCA,complete,7\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)

            with (
                patch.object(general_module, "create_react_agent", fake_create_react_agent),
                patch.object(general_module, "AgentExecutor", FakeAgentExecutor),
            ):
                output = GeneralPurposeEngine(llm=FakeInvokeModel("unused")).run(
                    ExecutionSpec(
                        intent="reason", objective="What columns are in this file?"
                    ),
                    DataCorpusPackage(sources=[str(csv_path)]),
                    runtime,
                )

        self.assertEqual(output.result, "ReAct saw 2 rows")
        self.assertNotEqual(
            output.result, "CSV columns: country, status, revenue; rows: 2"
        )

    def test_invoke_model_uses_langchain_v1_agent_when_react_executor_is_unavailable(self) -> None:
        FakeLangChainV1Agent.instances = []

        def fake_create_agent(*, model, tools, system_prompt):
            return FakeLangChainV1Agent(
                model=model, tools=tools, system_prompt=system_prompt
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\nCA,complete,7\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)
            model = FakeInvokeModel("unused")

            with (
                patch.object(general_module, "create_react_agent", None),
                patch.object(general_module, "AgentExecutor", None),
                patch.object(general_module, "create_agent", fake_create_agent),
            ):
                output = GeneralPurposeEngine(llm=model).run(
                    ExecutionSpec(
                        intent="reason", objective="What is the data about?"
                    ),
                    DataCorpusPackage(sources=[str(csv_path)]),
                    runtime,
                )

        self.assertEqual(output.result, "LangChain v1 saw 2 rows")
        self.assertEqual(FakeLangChainV1Agent.instances[0].model, model)
        self.assertIn("scan_csv", [tool.name for tool in FakeLangChainV1Agent.instances[0].tools])
        self.assertIn("What is the data about?", FakeLangChainV1Agent.instances[0].invocations[0]["messages"][0]["content"])
        self.assertEqual(output.trace.method_calls[0].method_name, "scan_csv")

    def test_csv_question_does_not_use_hardcoded_fallback_when_langchain_agent_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sales.csv"
            csv_path.write_text(
                "country,status,revenue\nUS,complete,10\nCA,complete,7\n",
                encoding="utf-8",
            )
            method_hub = MethodHub()
            register_csv_methods(method_hub)
            runtime = EngineRuntimeContext(method_hub=method_hub)

            with (
                patch.object(general_module, "create_react_agent", None),
                patch.object(general_module, "AgentExecutor", None),
                patch.object(general_module, "create_agent", None),
            ):
                output = GeneralPurposeEngine(llm=FakeInvokeModel("unused")).run(
                    ExecutionSpec(
                        intent="reason", objective="What is the data about?"
                    ),
                    DataCorpusPackage(sources=[str(csv_path)]),
                    runtime,
                )

        self.assertIn(
            "LangChain ReAct agent support is not available", str(output.result)
        )
        self.assertEqual(output.trace.method_calls, [])

    def test_missing_generation_services_returns_clear_answer(self) -> None:
        output = GeneralPurposeEngine(llm=object()).run(
            ExecutionSpec(
                intent="reason",
                objective="needs unavailable capability",
                capability_requirements=[CapabilityRequirement("missing")],
            ),
            DataCorpusPackage(sources=["sales.csv"]),
            EngineRuntimeContext(),
        )

        self.assertIn("method generation is not configured", str(output.result))
        self.assertEqual(output.trace.steps[-1].name, "method_generation_unavailable")

    def test_generated_method_lifecycle_validates_and_registers_interface(self) -> None:
        registry = InMemoryInterfaceRegistry()
        runtime = EngineRuntimeContext(
            interface_builder=FakeBuilder(),
            sandbox_executor=FakeSandbox(),
            interface_registry=registry,
        )

        output = GeneralPurposeEngine(llm=object()).run(
            ExecutionSpec(
                intent="reason",
                objective="needs generated capability",
                capability_requirements=[CapabilityRequirement("generated_capability")],
            ),
            DataCorpusPackage(sources=["sales.csv"]),
            runtime,
        )

        interface = output.metadata["interface_defs"][0]
        self.assertEqual(interface.trust_level, "generated_validated")
        self.assertEqual(registry.list_available(), [interface])
        self.assertEqual(output.metadata["sandbox_results"][0].status, "completed")


if __name__ == "__main__":
    unittest.main()
