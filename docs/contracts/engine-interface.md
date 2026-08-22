# Engine Interface

Status: Draft interface

This document defines the engine interface. It is the small surface area an
engine implementation should understand: input, output, runtime access, and the
callable `run()` shape.

## Interface Summary

An engine receives request state through `EngineInput`, uses request-scoped
services through `EngineRuntimeContext`, and returns `EngineOutput`.

```text
EngineInput -> Engine.run(...) -> EngineOutput
```

## Engine Input

`EngineInput` lives in `data_intelligence_sdk.core.types` and is exported from
`data_intelligence_sdk.engines` for engine authors.

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class EngineInput:
    query: UserQuery
    spec: ExecutionSpec
    runtime: EngineRuntimeContext
    user_context: UserContext | None = None
```

Field meanings:

| Field          | Meaning                                                                                                         |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| `query`        | Original user request.                                                                                          |
| `spec`         | Confirmed execution spec selected for the engine.                                                               |
| `runtime`      | Request-scoped services such as run context, sandbox, MCP client/tools, artifacts, logs, and resource controls. |
| `user_context` | Optional long-lived user preferences/history.                                                                   |

Rules:

- Do not copy secrets out of `runtime` into metadata, prompts, logs, or output.
- Treat `EngineInput` as request-scoped. Do not store it on a singleton engine
  after `run()` returns.
- Prefer adding new runtime capabilities to `EngineRuntimeContext` rather than
  adding more arguments to `run()`.

## Engine Output

Engines return the existing `EngineOutput` from `data_intelligence_sdk.core.types`.

```python
@dataclass(slots=True)
class EngineOutput:
    engine_name: str
    answer: str | None = None
    result: Any = None
    evidence: EvidenceBundle | None = None
    trace: EngineTrace = field(default_factory=EngineTrace)
    metadata: dict[str, Any] = field(default_factory=dict)
```

Use it like this:

- `engine_name`: stable public engine identifier, for example `general`.
- `answer`: user-facing answer when the engine can provide text directly.
- `result`: structured or rendered result when text is not enough.
- `evidence`: optional sources, observations, method calls, and artifacts.
- `trace`: structured steps and method calls collected during the run.
- `metadata`: small non-secret engine-specific details.

## Engine Protocol

The engine object exposes a stable name, a routing description, and a `run()`
method.

```python
class Engine(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def run(
        self,
        input: EngineInput,
    ) -> EngineOutput: ...
```

The preferred direct engine call uses `EngineInput`:

```python
engine.run(
    EngineInput(
        query=query,
        spec=spec,
        runtime=runtime,
        user_context=user_context,
    )
)
```

The current pipeline may still call some existing engines with an expanded
positional form while they are being adapted. The base interface for new engine
implementations is `EngineInput`.

## Building A New Engine

Start with the simple shape:

```python
from data_intelligence_sdk.engines import EngineInput, EngineOutput

class MyEngine:
    name = "my_engine"
    description = "Short routing description."

    def run(self, input: EngineInput) -> EngineOutput:
        runtime = input.runtime
        runtime.run_context.record_step(
            "my_engine_started",
            inputs={"objective": input.spec.objective},
        )
        return EngineOutput(
            engine_name=self.name,
            answer="...",
            trace=runtime.run_context.trace,
        )
```

Keep engine code boring:

- Read the user request from `input.query` and execution details from
  `input.spec`.
- Use `input.runtime` for sandbox, MCP, artifacts, logs, and tracing.
- Return `EngineOutput`; do not return engine-specific objects from `run()`.
- Add only the runtime fields the engine actually needs.
