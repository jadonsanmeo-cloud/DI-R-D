# Method Hub Runtime Toggle Design

**Status:** Approved for implementation planning

## Goal

Add a per-request frontend toggle that controls whether a Data Intelligence run
may use AXIOM Method Hub. Method Hub is the platform MCP server.

- When disabled, engines receive no MCP capability and solve the request with
  generated code in the request sandbox.
- When enabled, engines may call MCP tools directly for simple operations and
  may generate sandbox code that calls multiple MCP tools and transforms their
  results.
- If Method Hub is enabled but unavailable, the request fails. It must not
  silently fall back to code-only execution.

## Non-Goals

- Letting the browser provide an MCP endpoint or credential.
- Allowing unrestricted sandbox network access.
- Replacing the existing AXIOM sandbox with host-side code execution.
- Persisting a global user preference in this first version.
- Automatically switching an in-progress response after its runtime mode has
  been captured.

## Request Semantics

The toggle is scoped to one Responses request. Its default comes from the
server's `[method_hub].enabled` configuration.

```json
{
  "input": "Scan the files and normalize the result",
  "data_corpus_package": {
    "sources": [],
    "schemas": {},
    "metadata": {}
  },
  "runtime_options": {
    "method_hub_enabled": true
  }
}
```

`runtime_options.method_hub_enabled` is optional. When omitted, the API uses
the configured default. The resolved boolean is persisted with the response so
preparation, revision, confirmation, execution, history, and audit use the same
mode.

Changing the frontend toggle after a request has started affects only the next
request.

## Capability Discovery API

The API exposes a small non-secret capability endpoint:

```http
GET /api/v1/runtime-capabilities
```

```json
{
  "method_hub": {
    "default_enabled": false,
    "available": true
  }
}
```

`available` is based on an MCP initialize and `tools/list` probe with a short
server-side cache. The response never includes the endpoint, token, headers, or
tool inventory.

When Method Hub is enabled for a new request, the API performs a fresh readiness
check before starting spec preparation. Failure returns HTTP `503` with a safe
error. Execution also establishes a fresh client and fails the run if the MCP
transport becomes unavailable.

## Frontend

The Responses page adds a compact `Method Hub` switch beside the model selector
in both the empty-state composer and active-chat composer.

States:

- `Off`: code and sandbox only.
- `On`: direct MCP tools plus sandbox MCP orchestration.
- `Unavailable`: disabled switch with a tooltip explaining that Method Hub is
  not reachable.

The page loads the capability endpoint once, initializes the switch from
`default_enabled`, and sends the current value in `runtime_options` for each new
request. A pending or completed message shows the captured mode rather than the
current composer value.

Response history stores and restores the captured mode.

## API and Workflow Wiring

The API adds typed `RuntimeOptionsRequest` and resolved `WorkflowRuntimeOptions`
models. Request preparation resolves the optional override against server
configuration before constructing the pipeline.

The pipeline factory receives the resolved options explicitly. It must not read
request-specific toggle state from process-global environment variables.

For a disabled request:

- `MCPMethodClient` is not created;
- `EngineRuntimeContext.mcp_client` is `None`;
- no remote tools are registered or described to the engine;
- no Method Hub readiness probe is performed.

For an enabled request:

- the factory creates and verifies `MCPMethodClient`;
- the MCP catalog is discovered once for the pipeline execution;
- runtime tools and sandbox MCP access are provisioned from that catalog;
- transport initialization or availability failure is terminal.

## Engine Behavior

### Direct calls

MCP tool definitions are converted into agent-native structured tools. A direct
tool invocation calls `MCPMethodClient.call_tool()` on the host and records an
`EngineMethodCall` in `EngineRunContext`.

The engine system prompt instructs the model to use a direct MCP tool when one
tool can satisfy the operation without custom transformation.

### Composite calls

`execute_python` remains available in both modes. With Method Hub enabled, its
prompt also includes the MCP catalog and the sandbox helper contract:

```python
from axiom_method_hub import call_tool

scan = call_tool("scan_files", {"path": "/workspace/input"})
result = {
    "files": [
        {"name": item["name"], "size": item["size"]}
        for item in scan["files"]
    ]
}
```

The engine should generate composite code when it needs to call multiple tools,
join their outputs, filter records, calculate derived values, or format a tool
result into the requested shape.

Both GeneralPurposeEngine and ReportEngine receive the same resolved runtime
mode. Shared MCP binding code supplies the catalog, direct tools, and audit
records so engine implementations do not independently implement transport.

## Sandbox MCP Broker

The gVisor sandbox remains offline with `runsc --network=none`. Method Hub access
is provided by a request-scoped broker rather than general network egress.

When the runtime mode is disabled, the sandbox is provisioned exactly as it is
today.

When enabled:

1. The Data Intelligence API asks sandbox-service for the predefined
   `method_hub` capability profile. It does not send an arbitrary URL.
2. sandbox-service verifies its platform-owned Method Hub configuration.
3. The worker creates a per-sandbox Unix socket broker and bind-mounts only that
   socket into the sandbox at `/run/axiom/method-hub.sock`.
4. The preinstalled `axiom_method_hub` helper sends MCP `tools/list` and
   `tools/call` requests over the socket.
5. The broker forwards requests to the configured Method Hub MCP endpoint using
   platform credentials.
6. The broker records authoritative call metadata and returns structured MCP
   results to the generated Python process.

The sandbox never receives the Method Hub URL, bearer token, authorization
headers, or a general-purpose network interface. Arbitrary generated code can
access only the broker operations allowed by the capability profile.

The sandbox client and service contracts add a fixed capability profile field;
they do not accept arbitrary destinations:

```json
{
  "workspace_id": "...",
  "runtime": "python",
  "capability_profiles": ["method_hub"]
}
```

Unknown profiles are rejected. Production workers fail readiness when a
configured profile cannot be enforced.

## Audit and Redaction

Direct and brokered calls share these records:

- tool name;
- redacted arguments;
- status and duration;
- producer step and sandbox command identifiers;
- truncated result preview or artifact reference;
- safe error category.

The sandbox command result includes broker-generated method-call records. The
SDK imports them into `EngineRunContext`; generated stdout is not trusted as the
source of audit data.

The following values must never appear in generated code, events, stdout,
artifacts, history, logs, or final responses:

- Method Hub endpoint credentials;
- authorization headers;
- broker signing keys;
- raw platform service configuration.

## Failure Behavior

- Capability endpoint probe failure reports `available=false`.
- Enabling an unavailable Method Hub rejects a new request with HTTP `503`.
- MCP transport/session failure during execution fails the run.
- An individual tool application error is recorded as a structured failed call;
  the engine may correct arguments or choose another MCP tool according to its
  normal execution policy.
- Broker startup, socket, policy, or forwarding failure fails the sandbox
  command and therefore the run.
- Disabled mode never attempts MCP initialization or broker provisioning.

## Compatibility

Existing clients that omit `runtime_options` retain server-configured behavior.
Existing sandbox requests that omit `capability_profiles` remain offline and
unchanged. Response history rows created before this feature deserialize with
the configured default marked as unknown rather than rewriting historical
behavior.

## Verification

Data Intelligence tests cover:

- request override and server-default resolution;
- persistence across confirmation and history;
- disabled mode creating no MCP client;
- enabled mode readiness failure returning `503`;
- direct MCP tool binding and audit records;
- prompt/catalog behavior for composite sandbox code;
- General and Report engine runtime propagation;
- frontend default, disabled, enabled, unavailable, and submitted-value states.

AXIOM tests cover:

- capability profile validation;
- no broker or socket in disabled mode;
- broker socket mounting in enabled mode;
- only `tools/list` and `tools/call` forwarding;
- credentials remaining outside sandbox-visible state;
- authoritative call records in command results;
- production worker readiness failure when broker policy cannot be enforced;
- gVisor retaining `--network=none` in both modes.
