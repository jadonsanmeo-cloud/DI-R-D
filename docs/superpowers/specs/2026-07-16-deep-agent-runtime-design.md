# Deep Agent Runtime Design

## Goal

Provide one simple runtime flow:

```text
DataCorpusPackage + UserQuery
  -> ExecutionSpec
  -> engine selection
  -> one request-scoped AXIOM sandbox
  -> one Deep Agent in GeneralPurposeEngine
  -> generated Python execution
  -> FinalResponse
```

This first version intentionally excludes evidence collection, Method Hub, MCP tools, reusable interface generation, and multiple agents.

## Runtime Boundary

Add a high-level runtime composition point that owns the complete request lifecycle. It receives the user query and corpus package, builds and confirms an execution spec, selects an engine, provisions one sandbox, invokes the engine, returns the answer, and cleans up the sandbox.

The existing `EngineRuntimeContext` remains the engine-facing request context. It will expose the request-scoped sandbox capability required by the selected engine rather than constructing a new sandbox for every generated-code step.

The runtime must clean up the sandbox after success or failure unless an explicit development setting keeps it alive for debugging.

## General Purpose Engine

`GeneralPurposeEngine` will contain one Deep Agent created with `deepagents==0.6.12`. It will no longer route through Method Hub, MCP, LangChain ReAct compatibility paths, interface registries, or the existing custom generate-observe-refine loop.

The agent receives:

- The confirmed execution objective.
- Corpus metadata and sandbox paths for staged input files.
- Instructions to generate Python analysis code, execute it, inspect the result, retry when needed, and return a natural-language answer.

Subagents, task delegation, and unrelated tools are disabled. The intended model-visible action surface is:

- Deep Agents' filesystem `write_file` capability for creating or replacing the analysis script.
- A custom `execute_python_file(path)` tool for running that script in the request sandbox.

## AXIOM Sandbox Integration

The runtime provisions one sandbox through `sandbox-service` before engine execution. Input files from `DataCorpusPackage.sources` are staged under `/workspace/input/`. Generated files live under `/workspace/agent/`.

The Deep Agents filesystem backend is bound to the same sandbox, so `write_file` writes directly into the environment where execution occurs. There is no second local or in-memory filesystem to synchronize.

`execute_python_file(path)` accepts an absolute sandbox path restricted to `/workspace/agent/` and executes it with Python. It returns a structured observation containing:

- success status;
- exit code;
- stdout;
- stderr;
- parsed result when the script emits the AXIOM result marker;
- command and sandbox identifiers when available.

The tool does not accept arbitrary shell commands. The agent may rewrite the Python file and call the tool again within the same sandbox.

## Data Flow

1. The runtime receives `UserQuery` and `DataCorpusPackage`.
2. The configured spec builder creates an `ExecutionSpec`.
3. The runtime marks the spec confirmed for this non-interactive first version.
4. The engine registry selects `GeneralPurposeEngine`.
5. The runtime creates one AXIOM sandbox and waits until it is ready.
6. Corpus source files are staged into the sandbox and their sandbox paths are supplied to the engine.
7. `GeneralPurposeEngine` invokes one Deep Agent.
8. The agent writes analysis code with `write_file`.
9. The agent runs it with `execute_python_file` and observes the result.
10. The agent retries by rewriting and re-executing when appropriate.
11. The agent returns the final answer.
12. The engine returns `EngineOutput`; the runtime converts it directly to `FinalResponse` with `evidence=None`.
13. The runtime deletes the sandbox in a `finally` block unless debug retention is enabled.

## Error Handling

- Spec or engine-selection failures stop before sandbox provisioning when possible.
- Sandbox provisioning or staging failures return a runtime error with the lifecycle phase identified.
- Python failures are returned to the Deep Agent as tool observations so it can correct the script.
- Invalid or out-of-scope execution paths are rejected before a command is submitted.
- Transport failures and exhausted agent retries fail the engine run without inventing an answer.
- Sandbox cleanup is attempted on every terminal path and must not hide the original failure.

## Compatibility

- Pin behavior to the project's locked `deepagents==0.6.12` API.
- Preserve the public core dataclasses where practical.
- Remove Method Hub dependencies from `GeneralPurposeEngine`; other engines may retain their current contracts.
- Keep `FinalResponse.evidence` optional and set it to `None` in this simplified runtime.
- Update the CLI example to use the single runtime call without interactive spec confirmation or evidence output.

## Testing

Tests use fake LLM and fake sandbox objects; they make no live OpenRouter or sandbox-service calls.

Coverage includes:

- one sandbox is created and cleaned up per runtime request;
- input sources are staged once and mapped to sandbox paths;
- `GeneralPurposeEngine` creates one Deep Agent with no Method Hub integration;
- generated code is written and executed in the same sandbox;
- `execute_python_file` rejects paths outside `/workspace/agent/`;
- execution failures are returned as observations and allow a retry;
- the final agent message becomes `FinalResponse.answer` with `evidence=None`;
- cleanup occurs after success, engine failure, and execution failure;
- the CLI follows the simplified non-interactive flow.

## Non-Goals

- Evidence bundles or audit synthesis.
- Method Hub or MCP tool selection.
- Interface registries, generated interface validation, or method persistence.
- Multiple engines acting in one request.
- Multiple Deep Agents or subagents.
- Arbitrary shell execution or dependency installation.
- Long-lived sandboxes shared across separate user requests.
