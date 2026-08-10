# Remote Engine Development Plan

## Purpose

Build a remote engine service in a separate repo. The service will run in the
same internal Docker/network environment as the Data Intelligence API, receive
engine requests over HTTP, call Method Hub through MCP directly, use the
request-scoped sandbox provisioned by the API/runtime, and use the API Runtime
Gateway for trace, artifacts, and run coordination.

The remote engine should feel like a normal engine to its own code: it receives
an `EngineInput`-like object with `query`, `spec`, `runtime`, and
`user_context`. The difference is that `runtime` is a client/proxy, not the
local Python `EngineRuntimeContext` object from the API process.

## High-Level Flow

```text
Data Intelligence API
  -> Engine Registry selects remote engine
  -> RemoteEngineAdapter calls remote engine service

Remote Engine Service
  -> receives query, spec, user_context, runtime_handle
  -> builds EngineInput(query, spec, runtime_client, user_context)
  -> runs engine implementation
  -> calls Method Hub MCP directly when it needs Method Hub tools
  -> calls the provisioned sandbox directly when it needs code execution
  -> calls Runtime Gateway when it needs artifacts/trace/run events
  -> returns EngineOutput-compatible JSON

Data Intelligence API
  -> maps remote response back to EngineOutput / FinalResponse
```

## Network Model

Both services run in the same internal network, for example Docker Compose:

```text
api-service              http://api:8000
remote-engine-service    http://remote-engine:8080
sandbox-service          internal sandbox service with request-scoped sessions
method-hub               internal MCP service behind API/runtime
```

The API calls the remote engine through an internal URL:

```text
http://remote-engine:8080/api/v1/engine/run
```

The remote engine calls trace, artifact, and run-coordination capabilities
through the API Runtime Gateway:

```text
http://api:8000/api/v1/runtime/runs/{run_id}/...
```

The remote engine calls Method Hub directly through its internal MCP endpoint:

```text
http://method-hub:8000/mcp
```

The remote engine calls the already-provisioned sandbox session directly through
the internal sandbox endpoint supplied in the runtime handle:

```text
http://sandbox-service:8004/workspaces/{workspace_id}/sessions/{session_id}/...
```

## Request Contract For Remote Engine

The remote engine run endpoint receives:

- `query`: original user request.
- `spec`: confirmed execution spec selected for this engine.
- `user_context`: optional user preferences/history.
- `runtime_handle`: short-lived handle for calling Runtime Gateway.

The remote engine must not require direct access to `DataCorpusPackage`.

The remote engine must not expect the API process's Python
`EngineRuntimeContext` object. It should create a local `RuntimeClient` from the
`runtime_handle`.

## Runtime Handle

The API provides a runtime handle with:

- `run_id`: current request/run ID.
- `endpoint`: base Runtime Gateway URL for this run.
- `token`: short-lived scoped token.
- `capabilities`: enabled runtime capabilities, such as `sandbox`,
  `method_hub`, `artifacts`, and `trace`.
- `method_hub`: optional MCP endpoint, scoped MCP token, and tool catalog for
  direct Method Hub calls.
- `sandbox`: optional sandbox endpoint and workspace/session identifiers for the
  request sandbox that the API/runtime already created.

This handle is the remote engine's runtime access. It should be treated as a
request-scoped secret.

## Runtime Client In Remote Engine Repo

The remote engine repo should provide a small runtime client with methods like:

- `record_event(event_type, payload)`
- `record_artifact(...)` or `upload_artifact(...)` when artifact support is
  added

Internally, these methods call the API Runtime Gateway using the runtime handle.
This keeps artifact and trace behavior tied to the current API-owned request
runtime.

For Method Hub, the remote engine should provide a separate MCP client created
from the `method_hub` section of the runtime handle. That client calls the MCP
server directly and records tool-call trace through the Runtime Gateway.

For sandbox execution, the remote engine should provide a separate sandbox
client created from the `sandbox` section of the runtime handle. The API/runtime
creates the sandbox before calling the remote engine; the remote engine only uses
that existing session and records execution trace/artifact refs through the
Runtime Gateway.

## Engine Interface In Remote Repo

The remote engine implementation should use this conceptual interface:

```text
EngineInput
  query
  spec
  runtime
  user_context

EngineOutput
  engine_name
  answer
  result
  metadata
```

The engine code should read from `query` and `spec`, use `runtime` for tools,
and return an `EngineOutput`-compatible response.

## Responsibilities: This Repo

1. Add `RemoteEngineAdapter` and register it in the existing engine registry.
2. Send remote engine requests to the configured internal engine endpoint.
3. Provision the request sandbox before calling the remote engine when sandbox is
   enabled.
4. Create a short-lived runtime handle for each run.
5. Include sandbox endpoint and workspace/session identifiers in the runtime
   handle when sandbox is enabled.
6. Include Method Hub MCP endpoint, scoped MCP token, and available tool catalog
   in the runtime handle when Method Hub is enabled.
7. Expose Runtime Gateway endpoints for trace, artifacts, and run events.
8. Map remote engine responses back to local `EngineOutput`.
9. Keep `DataCorpusPackage` inside pipeline/spec-building only; do not send it
   as engine input.

## Responsibilities: Remote Engine Repo

1. Expose `POST /api/v1/engine/run`.
2. Accept `query`, `spec`, `user_context`, and `runtime_handle`.
3. Build a local `RuntimeClient` from `runtime_handle`.
4. Build an `EngineInput`-like object for the engine implementation.
5. Use the sandbox client to call the existing request sandbox directly when
   sandbox is enabled.
6. Use the MCP client to call Method Hub directly when Method Hub is enabled.
7. Use Runtime Gateway for artifacts, trace events, and run events.
8. Return `EngineOutput`-compatible JSON.
9. Keep runtime and MCP tokens secret and do not persist them beyond the request.

## Security Rules

- Runtime tokens are scoped to one `run_id`.
- Runtime tokens should expire quickly.
- Remote engines should only receive capabilities enabled for the current run.
- Do not send permanent MCP credentials to the remote engine; send only scoped
  request tokens when direct MCP access is enabled.
- Do not send sandbox provider credentials to the remote engine; send only
  scoped request/session identifiers for the already-created sandbox.
- Do not send local filesystem paths as runtime authority.

## First Milestone

Build the synchronous path first:

1. API calls remote engine and waits for a response.
2. Remote engine creates `RuntimeClient` from `runtime_handle`.
3. Remote engine can call Method Hub directly through MCP.
4. Remote engine can record MCP tool-call trace through Runtime Gateway.
5. Remote engine can call the existing request sandbox directly.
6. Remote engine can record sandbox execution trace/artifact refs through
   Runtime Gateway.
7. Remote engine returns an `EngineOutput`-compatible response.

Async jobs, polling, retries, and multi-worker lease storage can come later.

## Open Questions

- Should remote engine responses include full trace details, or should all trace
  events be written through Runtime Gateway?
- Should artifacts be uploaded through Runtime Gateway in milestone one, or only
  artifact refs after the engine has produced output?
- Should remote engines be enabled by config only, or also discovered through an
  engine catalog service later?
