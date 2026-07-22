# Engine Input, Event, and Output Interface

Status: Draft for user review

## 1. Purpose

This document defines the contract between the Data Intelligence pipeline and an
engine. The contract has three goals:

1. Give an engine all request data and runtime capabilities needed to execute a
   confirmed specification.
2. Emit structured intermediate events continuously so the API can stream
   progress to the UI.
3. Return one detailed final output that can be persisted, audited, synthesized,
   and replayed without reconstructing state from engine-specific objects.

The contract covers:

- engine request data;
- Method Hub MCP access;
- request-scoped AXIOM sandbox access;
- realtime event emission;
- execution plans and steps;
- generated code and sandbox executions;
- artifacts, warnings, errors, and resource usage;
- evidence collected from data, methods, sandbox results, and artifacts;
- final engine output;
- ordering, ownership, persistence, and UI mapping rules.

## 2. Non-Goals

This contract does not define:

- how an engine reasons internally;
- which LLM or agent framework an engine uses;
- the HTTP or SSE API schema exposed to external clients;
- AXIOM Sandbox service internals;
- Method Hub server internals;
- artifact binary storage implementation;
- frontend component implementation.

The API may map these SDK types into versioned transport schemas, but it should
preserve their semantics.

## 3. Design Principles

### 3.1 One explicit engine input

An engine receives one `EngineInput` object instead of a growing positional
argument list. Request data and runtime capabilities remain grouped and named.

### 3.2 Realtime events plus a final aggregate

Intermediate events are emitted while an engine runs. The final `EngineOutput`
contains the complete aggregate state. Events support live UI updates; the
output supports persistence and downstream processing.

### 3.3 Shared structured models

Plans, steps, artifacts, code records, method calls, and sandbox executions use
the same models in intermediate events and in the final output. The UI should
not need separate schemas for live and completed runs.

### 3.4 Lifecycle ownership is explicit

The pipeline provisions shared clients, configuration, artifact sessions,
event sinks, and resource controls. The engine uses the injected
`SandboxClient` to create and delete its request sandbox. The engine must not
close or replace the shared client.

### 3.5 Durable ordering

The event sink, not the engine, assigns event identifiers, timestamps, and
monotonic sequence numbers. This keeps events ordered when an engine runs steps
concurrently.

### 3.6 Partial work remains observable

If execution fails, completed plans, steps, code attempts, method calls,
sandbox executions, and artifacts remain available in the terminal output and
event history.

## 4. Top-Level Engine Contract

```python
from typing import Protocol


class Engine(Protocol):
    @property
    def name(self) -> str:
        """Return the stable engine identifier."""

    @property
    def description(self) -> str:
        """Describe the engine capabilities for agent-based routing."""

    def can_handle(self, spec: ExecutionSpec) -> bool:
        """Return whether this engine can execute the specification."""

    def run(self, input: EngineInput) -> EngineOutput:
        """Execute one request, emit events, and return the aggregate output."""
```

`run()` remains synchronous in this contract. An engine may use internal async
or concurrent execution. Realtime delivery is provided by `EngineEventSink`,
not by making `run()` a generator.

### 4.1 Engine selection

The engine registry exposes an immutable catalog containing each registered
engine's `name` and `description`. After spec confirmation, an engine selector
agent receives the confirmed spec and this catalog and returns one exact
registered engine name.

`ExecutionSpec.engine_hint` is not included in the selector prompt and does not
override the selector decision. If the selector is unavailable, returns an
invalid payload, or chooses an unknown name, the registry selects the
registered `general_purpose` engine. A registry without that configured
fallback is invalid.

Selector-less registries may retain deterministic `can_handle()` routing for
offline examples and explicitly constructed pipelines.

## 5. Engine Input

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EngineInput:
    run_id: str
    request_id: str | None

    query: UserQuery
    spec: ExecutionSpec
    data: DataCorpusPackage

    session_context: SessionContext | None
    user_context: UserContext | None

    runtime: EngineRuntimeServices
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 5.1 Field semantics

| Field             | Required | Meaning                                                                                          |
| ----------------- | -------: | ------------------------------------------------------------------------------------------------ |
| `run_id`          |      Yes | Stable identifier for one engine execution. It is shared by events, artifacts, logs, and output. |
| `request_id`      |       No | API or upstream request identifier used for correlation.                                         |
| `query`           |      Yes | Original user query, including user/session identifiers and request metadata.                    |
| `spec`            |      Yes | Confirmed execution specification selected for execution.                                        |
| `data`            |      Yes | Data sources, schemas, metadata, and staged corpus context available to the engine.              |
| `session_context` |       No | Short-lived conversation or task state.                                                          |
| `user_context`    |       No | Long-lived user preferences and history.                                                         |
| `runtime`         |      Yes | Request-scoped runtime services and controls.                                                    |
| `metadata`        |       No | Additional non-secret correlation or engine routing metadata.                                    |

### 5.2 Input invariants

- `run_id` must be non-empty and unique for an active run.
- `spec.confirmed` must be `True` before `Engine.run()` is called.
- `data.sources` must already be validated and scoped to the request.
- Runtime credentials must remain inside runtime service objects. They must not
  be copied into `metadata`.
- `EngineInput` is request-scoped and must not be retained by a singleton engine
  after `run()` returns.

## 6. Runtime Services

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class EngineRuntimeServices:
    mcp_client: MCPMethodClient | None
    mcp_tools: tuple[MCPToolDefinition, ...]

    sandbox_client: SandboxClient
    sandbox_workspace_id: UUID

    events: EngineEventSink
    artifacts: RunArtifactSession | None
    resource_manager: ResourceManager | None
    logger: RuntimeLogger | None

    sandbox_idle_ttl_seconds: int = 600
    sandbox_ready_timeout_seconds: float = 30
    deadline: datetime | None = None
    cancellation: CancellationToken | None = None
```

### 6.1 Method Hub

Method Hub is exposed to engines only through the AXIOM MCP server. `mcp_tools`
is the request-scoped catalog discovered before execution, and `mcp_client`
invokes those tools. The SDK does not register or execute local Method Hub
implementations. Calls must be represented as structured `EngineMethodCall`
records and events.

When `mcp_client` is `None`, the engine must execute without Method Hub
capabilities. If Method Hub was explicitly requested, the pipeline must fail
before engine execution when discovery or connectivity is unavailable.

Method Hub credentials, transport headers, and access tokens are owned by the
client. They must never be included in events, logs, outputs, or engine metadata.

### 6.2 AXIOM Sandbox Service

The engine receives a configured `SandboxClient` and the AXIOM workspace ID.
It follows the same service lifecycle as `services/sandbox-service/test_sandbox.py`:

```text
create sandbox
  -> wait until running
  -> create command
  -> consume command SSE events
  -> receive terminal command result
  -> delete sandbox in finally
```

The equivalent SDK flow is:

```python
sandbox = runtime.sandbox_client.create_sandbox(
    runtime.sandbox_workspace_id,
    idle_ttl_seconds=runtime.sandbox_idle_ttl_seconds,
)
try:
    sandbox.wait_until_ready(
        timeout=runtime.sandbox_ready_timeout_seconds,
    )

    command = sandbox.run(
        code,
        dependencies=dependencies,
        timeout_seconds=timeout_seconds,
        wait=False,
    )

    for log_event in command.stream():
        runtime.events.emit(map_sandbox_log_event(log_event))

    result = command.result(timeout=timeout_seconds + 30)
finally:
    sandbox.delete()
```

The created `Sandbox` object supports the actual client operations:

```python
sandbox.run(...)
sandbox.get_command(...)
sandbox.list_files(...)
sandbox.read(...)
sandbox.write(...)
sandbox.delete_file(...)
sandbox.stop()
sandbox.delete()
```

Ownership rules:

- The pipeline constructs and configures the shared `SandboxClient`.
- The engine creates one request sandbox with `sandbox_workspace_id`.
- The engine waits for `SandboxStatus.RUNNING` before creating commands.
- The engine owns the created `Sandbox` lifecycle and always calls
  `sandbox.delete()` in `finally`.
- The engine must not close or replace the shared `SandboxClient`.
- The engine may create multiple commands in the same sandbox when steps share
  files or intermediate state.
- `CommandHandle.stream()` is the source of realtime command log events.
- `CommandHandle.result()` or the terminal SSE event provides final command
  status, stdout, stderr, exit code, and truncation state.
- Every command and generated-code attempt produces structured sandbox and
  artifact records.
- Sandbox tokens, HTTP headers, and control-plane credentials must not appear
  in engine events or output.

### 6.3 Cancellation

```python
class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool:
        """Return whether cancellation was requested."""

    def raise_if_cancelled(self) -> None:
        """Raise the standard engine cancellation exception when cancelled."""
```

The engine checks cancellation and deadline state:

- before starting a step;
- before a Method Hub call;
- before a sandbox command;
- between retry attempts;
- before publishing a large artifact;
- before returning the final output.

## 7. Realtime Event Contract

### 7.1 Event types

```python
EngineExecutionStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "cancelled",
]

EngineEventType = Literal[
    "engine.started",
    "engine.completed",
    "engine.failed",
    "engine.cancelled",
    "plan.created",
    "plan.updated",
    "step.started",
    "step.progress",
    "step.completed",
    "step.failed",
    "step.skipped",
    "method.started",
    "method.completed",
    "method.failed",
    "code.generated",
    "code.validated",
    "code.execution_started",
    "code.execution_completed",
    "code.execution_failed",
    "sandbox.provisioning_started",
    "sandbox.ready",
    "sandbox.provisioning_failed",
    "sandbox.command_started",
    "sandbox.command_output",
    "sandbox.command_completed",
    "sandbox.command_failed",
    "sandbox.deleted",
    "artifact.created",
    "artifact.updated",
    "log.created",
    "warning.created",
]
```

### 7.2 Event draft emitted by an engine

```python
@dataclass(slots=True)
class EngineEventDraft:
    event_type: EngineEventType
    phase: str
    status: EngineExecutionStatus

    step_id: str | None = None
    parent_step_id: str | None = None
    message: str | None = None
    progress: EngineProgress | None = None

    payload: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

The engine does not assign event identity or ordering fields.

### 7.3 Durable event envelope

```python
@dataclass(frozen=True, slots=True)
class EngineEvent:
    schema_version: str
    event_id: str
    run_id: str
    sequence: int
    timestamp: datetime

    engine_name: str
    event_type: EngineEventType
    phase: str
    status: EngineExecutionStatus

    step_id: str | None
    parent_step_id: str | None
    message: str | None
    progress: EngineProgress | None

    payload: dict[str, Any]
    artifact_refs: list[str]
    metadata: dict[str, Any]
```

### 7.4 Event sink

```python
class EngineEventSink(Protocol):
    def emit(self, event: EngineEventDraft) -> EngineEvent:
        """Persist, sequence, publish, and return the durable event."""
```

The sink performs these operations atomically from the engine's perspective:

1. Validate and redact the draft.
2. Assign `schema_version`, `event_id`, `run_id`, `sequence`, and `timestamp`.
3. Persist the event to durable run history.
4. Publish the event to active subscribers such as an API SSE queue.
5. Return the durable event to the engine.

A disconnected UI subscriber must not fail the engine. Failure to persist a
durable event must fail the run because audit and replay guarantees are lost.

### 7.5 Progress

```python
@dataclass(frozen=True, slots=True)
class EngineProgress:
    current: float
    total: float | None = None
    unit: str | None = None
    percent: float | None = None
    message: str | None = None
```

Rules:

- `current` must be non-negative.
- `total`, when present, must be positive and greater than or equal to
  `current`.
- `percent`, when present, must be between `0` and `100`.
- The UI must not infer completion from progress. Completion requires a terminal
  step or engine event.

## 8. Plan Contract

```python
PlanStatus = Literal["draft", "running", "completed", "failed", "cancelled"]


@dataclass(slots=True)
class EnginePlan:
    plan_id: str
    revision: int
    objective: str
    status: PlanStatus
    steps: list[EnginePlanStep]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnginePlanStep:
    step_id: str
    sequence: int
    name: str
    description: str | None = None
    kind: str = "task"
    dependencies: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    status: EngineExecutionStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
```

Plan rules:

- A plan is optional. Simple engines may execute without one.
- `plan.created` contains the complete first revision.
- `plan.updated` contains a complete new revision, not an ambiguous partial
  patch.
- `revision` starts at `1` and increases monotonically.
- Plan step identifiers remain stable across revisions when the logical step is
  unchanged.
- Runtime execution steps should reference the matching plan `step_id` when one
  exists.

## 9. Execution Step Contract

```python
@dataclass(slots=True)
class EngineStep:
    step_id: str
    parent_step_id: str | None
    sequence: int

    name: str
    kind: str
    description: str | None
    status: EngineExecutionStatus
    progress: EngineProgress | None

    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: float | None

    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    artifact_refs: list[str] = field(default_factory=list)
    code_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)

    error: EngineError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Step rules:

- `step.started` establishes the step before any progress or child events.
- `step.progress` may be emitted zero or more times.
- Exactly one of `step.completed`, `step.failed`, or `step.skipped` terminates a
  started step.
- No events may update a step after its terminal event.
- A child step references `parent_step_id` and may execute concurrently with
  other children.
- Inputs, outputs, and metadata are redacted before persistence.
- Large data values should be represented by artifact references rather than
  embedded directly.

## 10. Artifact Contract

```python
ArtifactKind = Literal[
    "file",
    "table",
    "chart",
    "image",
    "html",
    "markdown",
    "json",
    "code",
    "report",
    "log",
    "other",
]


@dataclass(slots=True)
class EngineArtifact:
    artifact_ref: str
    name: str
    kind: ArtifactKind

    uri: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None

    producer_step_id: str | None = None
    downloadable: bool = True
    preview: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Artifact rules:

- `artifact_ref` is the durable identity used by steps, code, events, and UI.
- `uri` may be local, object-storage, or platform-specific, but must not expose
  credentials.
- `preview` is optional and size-limited. Large content remains in artifact
  storage.
- An artifact becomes visible to the UI after `artifact.created` is durable.
- `artifact.updated` may add metadata or replace a preview, but must not change
  `artifact_ref` identity.

## 11. Generated Code Contract

```python
CodeStatus = Literal[
    "generated",
    "validated",
    "executing",
    "executed",
    "failed",
    "rejected",
]


@dataclass(slots=True)
class EngineCodeArtifact:
    code_id: str
    attempt: int
    language: str
    status: CodeStatus

    purpose: str | None = None
    producer_step_id: str | None = None

    source: str | None = None
    source_artifact_ref: str | None = None

    validation: CodeValidationResult | None = None
    execution: SandboxExecutionRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodeValidationResult:
    status: Literal["passed", "failed", "skipped"]
    validator: str | None = None
    feedback: str | None = None
    violations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

Code rules:

- Generated code is optional.
- Every attempt receives a stable `code_id` and positive `attempt` number.
- Large source is stored as an artifact and referenced through
  `source_artifact_ref`.
- Inline `source` is allowed only for a size-limited UI preview or when no
  artifact session exists.
- Validation and execution are distinct states and distinct events.
- Failed code attempts remain in the final output for audit and debugging.
- Secrets must never be inserted into generated source or source previews.

## 12. Method Hub Call Contract

```python
@dataclass(slots=True)
class EngineMethodCall:
    call_id: str
    method_name: str
    provider: Literal["mcp"]
    status: EngineExecutionStatus

    step_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None

    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)

    error: EngineError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Method call rules:

- A `method.started` event is emitted before invoking the local or remote
  method.
- Exactly one `method.completed` or `method.failed` event terminates the call.
- Remote transport details and authorization data are redacted.
- Retry attempts use distinct `call_id` values and may reference an original
  call through metadata.

## 13. Sandbox Execution Contract

```python
SandboxCommandStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelling",
    "cancelled",
]


@dataclass(slots=True)
class SandboxExecutionRecord:
    execution_id: str
    sandbox_id: str | None
    command_id: str | None
    step_id: str | None

    runtime: str
    dependencies: list[str]
    timeout_seconds: int

    command_status: SandboxCommandStatus
    status: EngineExecutionStatus
    success: bool | None

    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None

    code_preview: str | None = None
    result: Any = None
    stdout_preview: str | None = None
    stderr_preview: str | None = None
    exit_code: int | None = None
    truncated: bool = False
    last_sandbox_event_sequence: int = 0

    code_ref: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)
    resource_usage: dict[str, Any] = field(default_factory=dict)

    error: EngineError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Sandbox rules:

- The record mirrors the AXIOM `CommandResult` identity and terminal fields.
- `CommandHandle.stream(last_event_id=...)` yields ordered AXIOM `LogEvent`
  values containing `sequence`, `event`, and `data`.
- Each AXIOM log event is mapped to `sandbox.command_output` or `log.created`
  and linked to the engine step and command ID.
- Full code, stdout, and stderr should be stored as artifacts when they
  exceed preview limits.
- `code_preview`, `stdout_preview`, and `stderr_preview` are redacted and
  truncated.
- A sandbox execution is linked to the generating step and code artifact when
  applicable.
- `timed_out` maps to engine status `failed` and error category `timeout`.
- `cancelled` maps to engine status `cancelled`.
- `succeeded` maps to engine status `completed`.
- The engine records the last consumed sandbox event sequence so command stream
  consumption can resume with `last_event_id` when needed.
- Engine code must not expose raw sandbox credentials or control-plane payloads.

## 14. Errors and Warnings

```python
EngineErrorCategory = Literal[
    "validation",
    "method_hub",
    "sandbox",
    "resource",
    "timeout",
    "cancelled",
    "persistence",
    "internal",
]


@dataclass(slots=True)
class EngineError:
    code: str
    message: str
    category: EngineErrorCategory
    retryable: bool

    step_id: str | None = None
    cause_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EngineWarning:
    code: str
    message: str
    step_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
```

Rules:

- User-facing messages must be safe and must not contain stack traces or
  credentials.
- Internal exception details may be persisted only after redaction and only in
  restricted logs or artifacts.
- `retryable` describes whether repeating the failed operation may succeed. It
  does not authorize automatic retry by itself.
- Warnings do not make a run fail.
- A terminal engine failure includes one top-level `EngineError` and may also
  preserve step-level errors.

## 15. Resource Usage

```python
@dataclass(slots=True)
class EngineResourceUsage:
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None

    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_requests: int = 0

    method_calls: int = 0
    sandbox_commands: int = 0
    code_attempts: int = 0

    cpu_seconds: float | None = None
    peak_memory_bytes: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None

    estimated_cost: float | None = None
    cost_currency: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Unknown metrics remain `None`; they must not be reported as zero unless they
were actually measured.

## 16. Final Engine Output

### 16.1 Evidence contract

`EngineOutput` uses the existing `EvidenceBundle` contract so engine evidence
and final-response evidence share one representation:

```python
@dataclass(slots=True)
class EvidenceBundle:
    sources: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    steps: list[EngineStep] = field(default_factory=list)
    method_calls: list[EngineMethodCall] = field(default_factory=list)
    interface_defs: list[InterfaceDefinition] = field(default_factory=list)
    sandbox_results: list[SandboxExecutionRecord] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    log_refs: list[str] = field(default_factory=list)
```

Evidence semantics:

- `sources` identifies the approved corpus sources used to produce the result.
- `observations` contains structured facts, measurements, validations, or
  intermediate findings that support the result.
- `steps` identifies the execution steps that produced relevant evidence.
- `method_calls` records Method Hub results used by the engine.
- `interface_defs` records generated or selected interfaces used to obtain
  evidence.
- `sandbox_results` records sandbox commands whose outputs support the result.
- `artifact_refs` links tables, charts, reports, code, files, and other durable
  evidence artifacts.
- `log_refs` links restricted execution logs needed for audit or debugging.

Evidence rules:

- Evidence must be traceable to a source, step, method call, sandbox execution,
  or artifact.
- The engine must not include an observation that was not produced or verified
  during the run.
- Large evidence values must be stored as artifacts and referenced by ID.
- Evidence inherits the same redaction and credential-handling rules as events
  and output.
- Failed and partial runs preserve all evidence collected before termination.
- Evidence may be `None` only when the result does not make data-derived or
  tool-derived claims.

```python
EngineRunStatus = Literal["completed", "failed", "cancelled", "partial"]


@dataclass(slots=True)
class EngineOutput:
    schema_version: str
    run_id: str
    engine_name: str
    status: EngineRunStatus

    result: Any = None
    summary: str | None = None
    evidence: EvidenceBundle | None = None

    plan: EnginePlan | None = None
    steps: list[EngineStep] = field(default_factory=list)
    artifacts: list[EngineArtifact] = field(default_factory=list)
    code: list[EngineCodeArtifact] = field(default_factory=list)

    method_calls: list[EngineMethodCall] = field(default_factory=list)
    sandbox_executions: list[SandboxExecutionRecord] = field(default_factory=list)

    warnings: list[EngineWarning] = field(default_factory=list)
    error: EngineError | None = None

    usage: EngineResourceUsage = field(default_factory=EngineResourceUsage)
    trace: EngineTrace = field(default_factory=EngineTrace)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 16.2 Output status semantics

| Status      | Meaning                                                                                            |
| ----------- | -------------------------------------------------------------------------------------------------- |
| `completed` | The engine completed its objective and returned a usable result.                                   |
| `partial`   | The engine produced a usable but incomplete result and recorded warnings or failed optional steps. |
| `failed`    | The objective was not completed. Partial trace and artifacts may still exist.                      |
| `cancelled` | Execution stopped because cancellation was requested.                                              |

### 16.3 Output invariants

- `run_id` matches `EngineInput.run_id`.
- `engine_name` matches the selected engine's stable name.
- `completed` output has no top-level error.
- `failed` output has a top-level error.
- `cancelled` output uses an error or terminal reason with category
  `cancelled`.
- A completed or partial data-derived result includes evidence supporting its
  claims.
- Evidence references only steps, calls, executions, artifacts, logs, and
  sources belonging to the same run.
- Referenced steps, code, calls, executions, and artifacts have unique
  identifiers within the run.
- Final output reflects every durable event emitted by the engine.
- Final output may contain additional aggregate fields that were not streamed,
  but it must not contradict durable events.

## 17. Required Event Ordering

The normal lifecycle is:

```text
engine.started
  sandbox.provisioning_started
  sandbox.ready | sandbox.provisioning_failed
  plan.created?                       optional
  plan.updated*                       optional
  step.started
    step.progress*
    method.started
      method.completed | method.failed
    code.generated?
    code.validated?
    code.execution_started?
      sandbox.command_started
      sandbox.command_output*
      sandbox.command_completed | sandbox.command_failed
    code.execution_completed | code.execution_failed
    artifact.created*
  step.completed | step.failed | step.skipped
  sandbox.deleted
engine.completed | engine.failed | engine.cancelled
```

Additional ordering rules:

- Each run has exactly one `engine.started` event.
- Each run has exactly one terminal engine event.
- A step must exist before events reference its `step_id`.
- A method call, code attempt, or sandbox execution must have a corresponding
  start event before its terminal event.
- `sandbox.ready` must occur before the first `sandbox.command_started` event.
- `sandbox.deleted` is emitted after cleanup succeeds. Cleanup failure is
  represented by `warning.created` and must not hide the original engine error.
- Global `sequence` is strictly increasing for a run.
- Concurrent steps may interleave events. Consumers use `step_id` and
  `parent_step_id` to reconstruct each branch.
- Event ordering is authoritative for UI replay.

## 18. Pipeline Lifecycle and Ownership

```text
Pipeline
  1. Validate and confirm ExecutionSpec
  2. Select Engine
  3. Create run ID, artifact session, logger, and durable event sink
  4. Configure SandboxClient and sandbox workspace ID
  5. Resolve the optional Method Hub boundary
  6. Build EngineInput
  7. Call Engine.run(input)
       - Engine emits intermediate events continuously
       - Event sink persists and forwards events to UI subscribers
       - Engine creates and waits for an AXIOM sandbox
       - Engine creates commands and streams command SSE events
       - Engine deletes the created sandbox in finally
       - Engine returns EngineOutput
  8. Validate output invariants
  9. Persist/finalize output and artifact manifest
 10. Collect evidence and synthesize FinalResponse
 11. Close shared runtime clients when their application lifecycle ends
```

The pipeline owns:

- SandboxClient configuration and application-level client lifecycle;
- sandbox workspace configuration;
- Method Hub configuration;
- event sequencing and durable persistence;
- artifact session lifecycle;
- output validation;
- final response synthesis.

The engine owns:

- execution strategy;
- request sandbox creation, readiness waiting, and deletion;
- command creation, SSE consumption, terminal result collection, and command
  cancellation;
- plan creation and revision;
- step creation and progress;
- Method Hub and sandbox operation selection;
- generated code decisions;
- aggregate engine result construction.

## 19. UI Mapping

| Event                          | Primary UI behavior                                                                     |
| ------------------------------ | --------------------------------------------------------------------------------------- |
| `engine.started`               | Open the execution panel and show the engine as running.                                |
| `plan.created`                 | Create the task plan/checklist.                                                         |
| `plan.updated`                 | Replace the visible plan with the new revision while preserving stable step identities. |
| `step.started`                 | Add or activate a step in the execution panel.                                          |
| `step.progress`                | Update progress, message, counters, or current operation.                               |
| `method.started`               | Show a Method Hub/tool call card as running.                                            |
| `method.completed`             | Show structured method output.                                                          |
| `method.failed`                | Show method error and retryability.                                                     |
| `code.generated`               | Show generated code or its artifact link.                                               |
| `code.validated`               | Show validation status and feedback.                                                    |
| `code.execution_started`       | Open the code execution/terminal state.                                                 |
| `sandbox.provisioning_started` | Show that an isolated AXIOM sandbox is being created.                                   |
| `sandbox.ready`                | Show that the sandbox is ready for commands.                                            |
| `sandbox.provisioning_failed`  | Show sandbox startup failure before command execution.                                  |
| `sandbox.command_started`      | Show the sandbox operation as running.                                                  |
| `sandbox.command_output`       | Append streamed SSE output to the terminal or step detail.                              |
| `sandbox.command_completed`    | Append command output or execution summary.                                             |
| `sandbox.command_failed`       | Show command status, stderr preview, exit code, and retryability.                       |
| `sandbox.deleted`              | Mark sandbox cleanup complete without removing execution history.                       |
| `artifact.created`             | Add a file, table, chart, image, HTML, Markdown, or report preview.                     |
| `warning.created`              | Show a non-terminal warning.                                                            |
| `step.completed`               | Mark the step completed.                                                                |
| `step.failed`                  | Mark the step failed while keeping its partial artifacts.                               |
| `engine.completed`             | Show the final result and completed state.                                              |
| `engine.failed`                | Show the terminal error and preserve partial execution history.                         |
| `engine.cancelled`             | Show that the user or system cancelled execution.                                       |

The UI should use `sequence` as its replay cursor. On reconnect, the API can
resume after the last acknowledged sequence.

## 20. Failure and Recovery Behavior

### 20.1 Method Hub failure

1. Emit `method.failed` with a redacted `EngineError`.
2. Record the failed call in the step and final output.
3. Retry only if engine policy permits and the error is retryable.
4. Continue with a partial result or fail the containing step.

### 20.2 Sandbox failure

1. Provisioning failure emits `sandbox.provisioning_failed` and prevents
   command execution.
2. Command failure emits `sandbox.command_failed` or
   `code.execution_failed`.
3. Persist available stdout, stderr, command, and code artifacts after
   redaction.
4. Record AXIOM command status, timeout, exit code, truncation state, last log
   sequence, and resource usage when available.
5. Retry, revise code, continue partially, or fail according to engine policy.
6. Always attempt `sandbox.delete()` in `finally`. Cleanup failure emits a
   warning and does not replace the original failure.

### 20.3 Engine exception

If an engine raises instead of returning a failed output, the pipeline must:

1. Convert the exception into a redacted `EngineError`.
2. Emit `engine.failed` if a terminal engine event has not already been emitted.
3. Preserve all durable intermediate events and artifacts.
4. Build or persist a failed aggregate output when possible.
5. Finalize the run as failed.

### 20.4 UI disconnect

UI or SSE disconnection does not cancel the engine. Events continue to be
persisted. Explicit cancellation requires the cancellation control path.

### 20.5 Durable event persistence failure

The run fails immediately because ordered replay and audit guarantees cannot be
maintained. Subscriber publication failure alone is non-fatal.

## 21. Security and Redaction

The following values must never appear in event payloads, outputs, previews,
logs, plans, or generated code:

- API keys;
- Authorization headers;
- sandbox tokens;
- database passwords;
- connection-string credentials;
- private signing material;
- raw provider configuration containing secrets.

The event sink and artifact persistence layer apply defense-in-depth redaction.
Engines must still avoid including secrets in drafts because subscriber
delivery may occur immediately after persistence.

Large or sensitive source data should be referenced by approved artifact or
corpus identifiers instead of copied into events.

## 22. Serialization and Versioning

- `EngineEvent.schema_version` and `EngineOutput.schema_version` start at
  `"1.0"`.
- Datetime values use UTC ISO 8601 strings at transport boundaries.
- Enum-like literals serialize as lowercase strings.
- Unknown metadata fields must be ignored by compatible consumers.
- Adding optional fields is backward compatible.
- Removing fields, renaming fields, changing field meaning, or making optional
  fields required needs a new major schema version.
- `Any` values must be JSON-compatible before transport or persistence. Binary
  data uses artifact references.

## 23. Compatibility with the Current SDK

The current interface is:

```python
engine.run(spec, corpus_package, runtime, user_context) -> EngineOutput
```

The target interface is:

```python
engine.run(input: EngineInput) -> EngineOutput
```

The current `EngineRuntimeContext` maps into `EngineRuntimeServices` as follows:

| Current field            | Target field                                                                 |
| ------------------------ | ---------------------------------------------------------------------------- |
| `run_context`            | `events` plus aggregate trace construction                                   |
| `mcp_client`             | `mcp_client`                                                                 |
| `mcp_tools`              | `mcp_tools`                                                                  |
| `sandbox`                | Replaced by `sandbox_client` plus `sandbox_workspace_id`                     |
| `run_artifact`           | `artifacts`                                                                  |
| `resource_manager`       | `resource_manager`                                                           |
| `artifact_store`         | Pipeline-owned artifact provisioning; not required directly by an engine run |
| `logger` outside runtime | `logger`                                                                     |

Existing `EngineStep`, `MethodCall`, `EngineTrace`, and `EngineOutput` types can
be expanded or replaced with the detailed models in this contract. Migration
should update the pipeline, `GeneralPurposeEngine`, and `ReportEngine` together
so that one execution path does not mix incompatible signatures.

## 24. Example Engine Execution

```python
def run(self, input: EngineInput) -> EngineOutput:
    started = utc_now()
    steps: list[EngineStep] = []
    artifacts: list[EngineArtifact] = []
    code_records: list[EngineCodeArtifact] = []
    sandbox_records: list[SandboxExecutionRecord] = []
    evidence = EvidenceBundle(sources=list(input.data.sources))

    input.runtime.events.emit(
        EngineEventDraft(
            event_type="engine.started",
            phase="engine",
            status="running",
            message="Starting analysis",
        )
    )

    plan = build_plan(input.spec, input.data)
    input.runtime.events.emit(
        EngineEventDraft(
            event_type="plan.created",
            phase="planning",
            status="completed",
            payload={"plan": plan},
        )
    )

    for plan_step in plan.steps:
        if input.runtime.cancellation is not None:
            input.runtime.cancellation.raise_if_cancelled()
        input.runtime.events.emit(
            EngineEventDraft(
                event_type="step.started",
                phase="engine",
                status="running",
                step_id=plan_step.step_id,
                message=plan_step.name,
            )
        )

        # The engine may call Method Hub or use sandbox_client to create and
        # stream AXIOM commands here, emitting matching events and records.

        input.runtime.events.emit(
            EngineEventDraft(
                event_type="step.completed",
                phase="engine",
                status="completed",
                step_id=plan_step.step_id,
            )
        )

    output = EngineOutput(
        schema_version="1.0",
        run_id=input.run_id,
        engine_name=self.name,
        status="completed",
        result={"answer": "..."},
        summary="Analysis completed",
        evidence=evidence,
        plan=plan,
        steps=steps,
        artifacts=artifacts,
        code=code_records,
        sandbox_executions=sandbox_records,
        usage=EngineResourceUsage(
            started_at=started,
            completed_at=utc_now(),
        ),
    )

    input.runtime.events.emit(
        EngineEventDraft(
            event_type="engine.completed",
            phase="engine",
            status="completed",
            payload={"summary": output.summary},
            artifact_refs=[item.artifact_ref for item in output.artifacts],
        )
    )
    return output
```

The final implementation should centralize repetitive event and aggregate
updates in a run context or recorder so engines do not manually maintain two
independent representations.

## 25. Acceptance Criteria for a Future Implementation

A future implementation conforms to this contract when:

1. `Engine.run()` accepts one `EngineInput` and returns one `EngineOutput`.
2. Input includes confirmed spec, query, data, user/session context, optional
   Method Hub, configured `SandboxClient`, sandbox workspace ID, event sink,
   artifacts, resource controls, cancellation, and deadline.
3. Intermediate events are emitted during execution and receive durable,
   monotonic sequence numbers.
4. The API can forward events to the UI without parsing engine-specific result
   dictionaries.
5. Plan, step, code, method, sandbox, and artifact models are shared between
   events and final output.
6. Every run and every started step has exactly one terminal event.
7. UI disconnect does not stop execution or event persistence.
8. Failures preserve partial events and artifacts.
9. Secrets are redacted from all observable output.
10. Existing general and report engines migrate to the same interface without
    mixed signatures.
11. Every engine-created sandbox follows create, wait, command stream, terminal
    result, and delete semantics compatible with AXIOM Sandbox Service.
12. Completed and partial data-derived outputs include evidence traceable to
    run sources, steps, Method Hub calls, sandbox executions, or artifacts.
