# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is a Python workspace containing a reusable SDK and a FastAPI application. The current goal, per [README.md](README.md), is to capture architecture boundaries from [ai-sdk-platform-architecture.svg](ai-sdk-platform-architecture.svg), not to provide production-ready SDK implementations. Many classes are intentionally contracts, placeholders, or `NotImplementedError` stubs.

## Common commands

```bash
# Create/use the uv-managed virtual environment and install both workspace packages
uv venv --python 3.11
uv pip install -e packages/sdk -e packages/api

# Quick import smoke test
uv run python - <<'PY'
import data_intelligence_sdk as dis
print(dis.SUPPORTED_INTENTS)
PY

# Run the stdlib unittest suite
uv run python -m unittest discover -s tests -v

# Build a wheel/sdist if the `build` package is available
uv run python -m build
```

No lint tool or formatter is configured in [pyproject.toml](pyproject.toml) yet. Tests use the stdlib `unittest` runner. If pytest is added later, typical invocations would be:

```bash
uv run pytest
uv run pytest path/to/test_file.py::test_name
```

## Architecture overview

The intended flow is documented in [README.md](README.md):

```text
User Query + DataCorpusPackage
  -> Intent Analyzer
  -> Spec Builder
  -> Spec Confirmation
  -> Engine Registry
  -> Selected Engine
  -> EngineRuntimeContext
       -> MethodHub
       -> InterfaceRegistry
       -> InterfaceBuilder
       -> SandboxExecutor
       -> Artifacts / Logs / Resources
  -> Engine Execution
       -> reuse trusted interface/method
       -> or create generated interface
       -> validate/run generated interface in sandbox
       -> emit EngineOutput + EngineTrace
  -> Evidence Collector
  -> Synthesizer
  -> FinalResponse(answer + evidence)
```

The workspace uses `src/` layouts with the SDK at [packages/sdk/src/data_intelligence_sdk/](packages/sdk/src/data_intelligence_sdk/) and the API at [packages/api/src/data_intelligence_api/](packages/api/src/data_intelligence_api/). Core data passed between layers is defined as small `@dataclass(slots=True)` contracts in [packages/sdk/src/data_intelligence_sdk/core/types.py](packages/sdk/src/data_intelligence_sdk/core/types.py).

Key architectural boundaries:

- [packages/sdk/src/data_intelligence_sdk/core/pipeline.py](packages/sdk/src/data_intelligence_sdk/core/pipeline.py) contains `DataIntelligencePipeline`, the planned composition point for the full flow. Its `run()` method is intentionally unimplemented until contracts settle.
- [packages/sdk/src/data_intelligence_sdk/intent/analyzer.py](packages/sdk/src/data_intelligence_sdk/intent/analyzer.py), [packages/sdk/src/data_intelligence_sdk/spec/builder.py](packages/sdk/src/data_intelligence_sdk/spec/builder.py), [packages/sdk/src/data_intelligence_sdk/spec/confirmation.py](packages/sdk/src/data_intelligence_sdk/spec/confirmation.py), [packages/sdk/src/data_intelligence_sdk/evidence/collector.py](packages/sdk/src/data_intelligence_sdk/evidence/collector.py), and [packages/sdk/src/data_intelligence_sdk/synthesis/synthesizer.py](packages/sdk/src/data_intelligence_sdk/synthesis/synthesizer.py) define `Protocol` interfaces for each stage.
- [packages/sdk/src/data_intelligence_sdk/core/types.py](packages/sdk/src/data_intelligence_sdk/core/types.py) defines the shared `@dataclass(slots=True)` contracts. `DataCorpusPackage` is the public data context concept; `DataHubContext` is a compatibility alias during the transition.
- `Intent` remains a controlled literal string from `SUPPORTED_INTENTS`; richer task detail belongs in `ExecutionSpec`, including `capability_requirements`.
- [packages/sdk/src/data_intelligence_sdk/registry/engine_registry.py](packages/sdk/src/data_intelligence_sdk/registry/engine_registry.py) provides the `EngineRegistry` protocol and `InMemoryEngineRegistry`. Selection checks `ExecutionSpec.engine_hint` first, then iterates registered engines and calls `can_handle()`.
- [packages/sdk/src/data_intelligence_sdk/engines/base.py](packages/sdk/src/data_intelligence_sdk/engines/base.py) defines the engine protocol. Placeholder engine implementations exist for SQL, Python, RAG, workflow, and custom intents; they only implement `name` and `can_handle()` and deliberately do not execute yet.
- [packages/sdk/src/data_intelligence_sdk/runtime/engine_runtime.py](packages/sdk/src/data_intelligence_sdk/runtime/engine_runtime.py) defines `EngineRuntimeContext`, the selected engine's access point for runtime services.
- [packages/sdk/src/data_intelligence_sdk/runtime/run_context.py](packages/sdk/src/data_intelligence_sdk/runtime/run_context.py) defines `EngineRunContext`, which engines should use to record `EngineStep`, `MethodCall`, artifact refs, and log refs. Engine outputs should be built around `EngineOutput` and `EngineTrace`, not custom trace shapes.
- [packages/sdk/src/data_intelligence_sdk/runtime/method_hub.py](packages/sdk/src/data_intelligence_sdk/runtime/method_hub.py) defines Method Hub contracts for callable runtime capabilities.
- [packages/sdk/src/data_intelligence_sdk/runtime/interfaces.py](packages/sdk/src/data_intelligence_sdk/runtime/interfaces.py) defines interface discovery and builder contracts for reusing or proposing interfaces.
- [packages/sdk/src/data_intelligence_sdk/sandbox/executor.py](packages/sdk/src/data_intelligence_sdk/sandbox/executor.py) defines the sandbox execution boundary for generated or untrusted interfaces.
- Placeholder engine implementations for SQL, Python, RAG, workflow, and custom intents should receive `DataCorpusPackage` and `EngineRuntimeContext`; they deliberately do not execute yet.
- [packages/sdk/src/data_intelligence_sdk/runtime/](packages/sdk/src/data_intelligence_sdk/runtime/) and [packages/sdk/src/data_intelligence_sdk/sandbox/](packages/sdk/src/data_intelligence_sdk/sandbox/) remain boundary placeholders for method access, controlled execution, logging, caching, resources, workspaces, artifacts, and logs.
- [packages/sdk/src/data_intelligence_sdk/__init__.py](packages/sdk/src/data_intelligence_sdk/__init__.py) re-exports the public core contracts. Update it when adding public types intended for SDK consumers.
- [packages/sdk/src/data_intelligence_sdk/engines/general.py](packages/sdk/src/data_intelligence_sdk/engines/general.py) defines `GeneralPurposeEngine`, the fallback/general engine.
- OpenRouter through LangChain is the first supported LLM provider for the general engine. Live calls should only happen when a pipeline run invokes an OpenRouter-backed engine.
- CSV MethodHub methods live under [packages/sdk/src/data_intelligence_sdk/methods/csv.py](packages/sdk/src/data_intelligence_sdk/methods/csv.py) and provide `scan_csv`, `filter_csv`, `count_csv`, and `sum_csv`.
- The SDK package should expose protocol boundaries and reusable engines/methods. Concrete workflow wiring belongs in consuming apps or examples, such as [examples/basic_workflow.py](examples/basic_workflow.py).
- Do not add live OpenRouter calls to tests; use fake engines, fake LLMs, or constructor/config validation tests.

Design notes from the README to preserve:

- `DataCorpusPackage` describes the available data universe for a task. It may contain source refs, schemas, semantic metadata, and policy metadata; it does not necessarily contain raw data.
- `DataHubContext` remains available as a compatibility alias for `DataCorpusPackage` during the transition.
- `Intent` is a controlled literal string from `SUPPORTED_INTENTS`; richer task detail belongs in `ExecutionSpec`, including `capability_requirements`.
- `SessionContext` is short-lived conversation/task state; `UserContext` is longer-lived user preference/history state.
- `EvidenceBundle` should be assembled from engine trace, method calls, interface definitions used or created, sandbox results, observations, artifact refs, and log refs for audit and synthesis.
