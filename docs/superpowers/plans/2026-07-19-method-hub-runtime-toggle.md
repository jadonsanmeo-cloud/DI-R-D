# Method Hub Runtime Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-request frontend Method Hub toggle that defaults from server configuration, fails immediately when enabled but unavailable, exposes MCP tools directly to engines, and lets generated sandbox code compose MCP calls through a request-scoped Unix socket broker.

**Architecture:** The API resolves and persists one explicit runtime option, discovers the MCP catalog before preparing or executing a request, and provisions the sandbox with a fixed `method_hub` capability profile. Direct engine tool calls use the host-side MCP client; generated Python stays inside gVisor with `--network=none` and reaches Method Hub only through `/run/axiom/method-hub.sock`, whose broker owns credentials and authoritative call auditing.

**Tech Stack:** Python 3.11/3.12, FastAPI, Pydantic, SQLAlchemy/Alembic, MCP streamable HTTP, Unix domain sockets, gVisor `runsc`, LangChain tools, Next.js 13, React 18, TypeScript, Ant Design.

---

## File Map

### AXIOM sandbox service and client

- Create `../AXIOM/services/sandbox-service/migrations/versions/0003_method_hub_capability.py`: persist sandbox capability profiles and command-level Method Hub audit records.
- Create `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/models.py`: broker request, response, and audit models.
- Create `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/client.py`: platform-owned MCP transport adapter.
- Create `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/broker.py`: per-sandbox Unix socket broker and lifecycle manager.
- Create `../AXIOM/services/sandbox-service/runtime/python/axiom_method_hub.py`: helper imported by generated sandbox Python.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/models/schemas.py`: fixed capability profile request/response and command audit fields.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/models/entities.py`: JSON persistence fields.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/services/sandbox_service.py`: copy validated profiles into sandbox rows.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/config.py`: broker-owned endpoint, token, socket root, timeout, and result limits.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/runtime/base.py`: pass capability mount data into runtime creation.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/runtime/runsc.py`: mount only the broker directory and inject the command identifier while retaining `--network=none`.
- Modify `../AXIOM/services/sandbox-service/src/sandbox_service/worker.py`: start, restore, stop, and drain brokers around sandbox/command lifecycle.
- Modify `../AXIOM/services/sandbox-service/Dockerfile.gvisor-worker`: install the Python helper into the immutable sandbox rootfs.
- Modify `../AXIOM/services/sandbox-service/docker-compose.yml`: configure the worker's internal Method Hub endpoint and broker root.
- Modify `../AXIOM/services/sandbox-service/pyproject.toml`: add the MCP client dependency.
- Modify `../AXIOM/packages/axiom-sandbox-client/src/axiom_sandbox_client/client.py`: send fixed capability profiles from sync and async clients.
- Modify `../AXIOM/packages/axiom-sandbox-client/src/axiom_sandbox_client/models.py`: expose profiles and authoritative method-call records.

### Data Intelligence SDK and API

- Create `packages/sdk/src/data_intelligence_sdk/tools/mcp.py`: convert discovered MCP definitions into request-scoped LangChain tools and record calls.
- Modify `packages/sdk/src/data_intelligence_sdk/runtime/engine_runtime.py`: carry the immutable discovered MCP catalog.
- Modify `packages/sdk/src/data_intelligence_sdk/runtime/deep_agent_sandbox.py`: surface sandbox broker audit records in execution observations.
- Modify `packages/sdk/src/data_intelligence_sdk/tools/execution.py`: merge broker audit records into `EngineRunContext`.
- Modify `packages/sdk/src/data_intelligence_sdk/core/pipeline.py`: propagate the discovered catalog into engine runtime.
- Modify `packages/sdk/src/data_intelligence_sdk/engines/general.py`: expose direct MCP tools and teach composite code to use `axiom_method_hub`.
- Modify `packages/sdk/src/data_intelligence_sdk/engines/report.py`: include remote MCP definitions in routing, invoke them directly, and run generated composite tools in the request sandbox.
- Create `packages/api/src/data_intelligence_api/application/runtime_capabilities.py`: resolve server defaults and probe Method Hub without exposing endpoint or credentials.
- Create `packages/api/src/data_intelligence_api/http/routers/runtime_capabilities.py`: `GET /api/v1/runtime-capabilities`.
- Modify `packages/api/src/data_intelligence_api/http/schemas/responses.py`: request runtime options and history output.
- Modify `packages/api/src/data_intelligence_api/domain/workflow.py`: immutable resolved workflow runtime options.
- Modify `packages/api/src/data_intelligence_api/application/workflow.py`: resolve, fail fast, and pass runtime options through every factory call.
- Modify `packages/api/src/data_intelligence_api/infrastructure/workflow/pipeline_factory.py`: request the sandbox capability profile and pass the MCP catalog into the SDK pipeline.
- Modify `packages/api/src/data_intelligence_api/infrastructure/config/settings.py`: cache the private Method Hub endpoint and public default state from TOML.
- Modify `packages/api/src/data_intelligence_api/http/routers/responses.py`: persist the resolved option and reuse it for revise/confirm/history.
- Modify `packages/api/src/data_intelligence_api/app/factory.py`: register the capability router.

### Frontend

- Create `web/new-components/responses-chat/MethodHubToggle.tsx`: shared compact toggle used by both composer layouts.
- Create `web/utils/runtime-capabilities.ts`: capability fetch and initial-state helpers.
- Modify `web/types/responses.ts`: runtime option and capability response types.
- Modify `web/utils/responses-request.ts`: include the resolved per-request option.
- Modify `web/hooks/use-responses-chat.ts`: accept and forward the toggle in the reusable responses hook.
- Modify `web/pages/index.tsx`: fetch capabilities, render the toggle twice from one state source, submit it, and restore it from history.
- Modify `web/locales/en/chat.ts`: English toggle labels and failure text.
- Modify `web/locales/zh/chat.ts`: Chinese toggle labels and failure text.
- Modify `README.md`: document request semantics and the local service topology.

## Task 1: Persist Fixed Sandbox Capability Profiles and Method Call Audits

**Files:**

- Create: `../AXIOM/services/sandbox-service/migrations/versions/0003_method_hub_capability.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/models/schemas.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/models/entities.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/services/sandbox_service.py`

- [ ] **Step 1: Add the API schema fields with a closed capability enum**

In `models/schemas.py`, define only the supported profile and use it on both create and response models:

```python
CapabilityProfile = Literal["method_hub"]


class MethodHubCallRecord(BaseModel):
    command_id: UUID
    tool_name: str
    status: Literal["completed", "failed"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class CreateSandboxRequest(BaseModel):
    workspace_id: UUID
    runtime: Literal["python"] = "python"
    capability_profiles: list[CapabilityProfile] = Field(
        default_factory=list,
        max_length=1,
    )
    idle_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    cpu_limit: float = Field(default=1, ge=0.1, le=4)
    memory_mb: int = Field(default=512, ge=128, le=4096)
    pids_limit: int = Field(default=128, ge=16, le=512)
    workspace_mb: int = Field(default=256, ge=16, le=2048)


class SandboxResponse(BaseModel):
    api_version: str = "v1"
    trace_id: str
    id: UUID
    workspace_id: UUID
    runtime: str
    capability_profiles: list[CapabilityProfile] = Field(default_factory=list)
    status: str
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class CommandResponse(BaseModel):
    api_version: str = "v1"
    trace_id: str
    id: UUID
    sandbox_id: UUID
    workspace_id: UUID
    runtime: str
    dependencies: list[str] = Field(default_factory=list)
    method_calls: list[MethodHubCallRecord] = Field(default_factory=list)
    status: str
    stdout: str
    stderr: str
    exit_code: int | None
    success: bool | None
    truncated: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
```

Pydantic's `Literal` validation must reject arbitrary profile names with HTTP 422; do not accept endpoint URLs or free-form profile configuration from callers.

- [ ] **Step 2: Add JSON columns to the SQLAlchemy entities**

In `models/entities.py`, add:

```python
class SandboxModel(Base):
    __tablename__ = "sandboxes"
    capability_profiles: Mapped[list[str]] = mapped_column(JSON, default=list)


class CommandModel(Base):
    __tablename__ = "sandbox_commands"
    method_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
```

- [ ] **Step 3: Add the Alembic migration**

Create `migrations/versions/0003_method_hub_capability.py`:

```python
"""add method hub sandbox capability"""

from alembic import op
import sqlalchemy as sa

revision = "0003_method_hub_capability"
down_revision = "0002_command_dependencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sandboxes",
        sa.Column(
            "capability_profiles",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "sandbox_commands",
        sa.Column(
            "method_calls",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("sandbox_commands", "method_calls")
    op.drop_column("sandboxes", "capability_profiles")
```

- [ ] **Step 4: Persist the validated profile list during sandbox creation**

Add this argument in the `SandboxModel` constructor inside `create_sandbox()`:

```python
capability_profiles=list(dict.fromkeys(payload.capability_profiles)),
```

- [ ] **Step 5: Commit the sandbox contract**

```bash
cd ../AXIOM
git add services/sandbox-service/migrations/versions/0003_method_hub_capability.py \
  services/sandbox-service/src/sandbox_service/models/schemas.py \
  services/sandbox-service/src/sandbox_service/models/entities.py \
  services/sandbox-service/src/sandbox_service/services/sandbox_service.py
git commit -m "feat(sandbox): persist method hub capability"
```

## Task 2: Implement the Request-Scoped Method Hub Unix Socket Broker

**Files:**

- Create: `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/__init__.py`
- Create: `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/models.py`
- Create: `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/client.py`
- Create: `../AXIOM/services/sandbox-service/src/sandbox_service/method_hub/broker.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/config.py`
- Modify: `../AXIOM/services/sandbox-service/pyproject.toml`

- [ ] **Step 1: Add private broker configuration**

Add these settings to `SandboxSettings`:

```python
method_hub_mcp_url: str | None = None
method_hub_token: str | None = None
method_hub_broker_root: str = "/var/lib/axiom-sandbox/method-hub-brokers"
method_hub_timeout_seconds: float = Field(default=30, gt=0, le=120)
method_hub_max_result_bytes: int = Field(default=1_048_576, ge=1024)
```

Extend `fail_closed_in_production()`:

```python
if self.environment.lower() == "production":
    if not self.method_hub_broker_root.startswith("/"):
        raise ValueError(
            "production requires an absolute method_hub_broker_root path"
        )
```

The endpoint and token remain worker settings and must never be copied into sandbox rows, command payloads, logs, or API responses.

- [ ] **Step 2: Add the MCP dependency**

Add this runtime dependency in `services/sandbox-service/pyproject.toml`:

```toml
"mcp>=1.28.1",
```

- [ ] **Step 3: Define the broker protocol models**

Create `method_hub/models.py`:

```python
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BrokerRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    command_id: UUID
    operation: Literal["tools/list", "tools/call"]
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class BrokerResponse(BaseModel):
    request_id: str
    ok: bool
    result: Any = None
    error: str | None = None


class BrokerAuditRecord(BaseModel):
    command_id: UUID
    tool_name: str
    status: Literal["completed", "failed"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime
```

- [ ] **Step 4: Implement the private MCP transport adapter**

Create `method_hub/client.py` with the same sync-over-async boundary used by the Data Intelligence SDK, but keep the implementation local to the sandbox service:

```python
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MethodHubBrokerClient:
    def __init__(
        self,
        endpoint: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout_seconds = timeout_seconds

    @asynccontextmanager
    async def _session(self):
        headers = (
            {"Authorization": f"Bearer {self.token}"} if self.token else None
        )
        async with streamable_http_client(
            self.endpoint,
            headers=headers,
            timeout=self.timeout_seconds,
        ) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    def list_tools(self) -> list[dict[str, Any]]:
        return asyncio.run(self._list_tools())

    async def _list_tools(self) -> list[dict[str, Any]]:
        async with self._session() as session:
            response = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in response.tools
            ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return asyncio.run(self._call_tool(name, arguments))

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._session() as session:
            response = await session.call_tool(name, arguments)
            if response.isError:
                text = "\n".join(
                    str(getattr(item, "text", item)) for item in response.content
                )
                raise RuntimeError(text or f"Method Hub tool failed: {name}")
            if response.structuredContent is not None:
                return response.structuredContent
            return [getattr(item, "text", item) for item in response.content]
```

- [ ] **Step 5: Implement broker lifecycle and authoritative audit buffering**

Create `method_hub/broker.py`. The manager owns one `ThreadingUnixStreamServer` per sandbox, accepts only command UUIDs explicitly activated by the worker, sets socket ownership for sandbox UID/GID 65532, bounds serialized results, and drains records only after the worker finishes that command:

```python
from __future__ import annotations

import json
import os
import shutil
import socketserver
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sandbox_service.method_hub.client import MethodHubBrokerClient
from sandbox_service.method_hub.models import (
    BrokerAuditRecord,
    BrokerRequest,
    BrokerResponse,
)

SOCKET_NAME = "method-hub.sock"


class _BrokerState:
    def __init__(self, client: MethodHubBrokerClient, max_result_bytes: int) -> None:
        self.client = client
        self.max_result_bytes = max_result_bytes
        self.audit: dict[UUID, list[BrokerAuditRecord]] = {}
        self.active_commands: set[UUID] = set()
        self.lock = threading.Lock()

    def handle(self, request: BrokerRequest) -> BrokerResponse:
        with self.lock:
            active = request.command_id in self.active_commands
        if not active:
            return BrokerResponse(
                request_id=request.request_id,
                ok=False,
                error="Method Hub command context is not active",
            )
        if request.operation == "tools/list":
            return BrokerResponse(
                request_id=request.request_id,
                ok=True,
                result=self.client.list_tools(),
            )
        if not request.tool_name:
            return BrokerResponse(
                request_id=request.request_id,
                ok=False,
                error="tools/call requires tool_name",
            )
        started_at = datetime.now(UTC)
        try:
            result = self.client.call_tool(request.tool_name, request.arguments)
            encoded = json.dumps(result, default=str).encode()
            if len(encoded) > self.max_result_bytes:
                raise RuntimeError("Method Hub result exceeds broker result limit")
            record = BrokerAuditRecord(
                command_id=request.command_id,
                tool_name=request.tool_name,
                status="completed",
                arguments=request.arguments,
                result=result,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            response = BrokerResponse(
                request_id=request.request_id,
                ok=True,
                result=result,
            )
        except Exception as exc:
            record = BrokerAuditRecord(
                command_id=request.command_id,
                tool_name=request.tool_name,
                status="failed",
                arguments=request.arguments,
                error=str(exc),
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            response = BrokerResponse(
                request_id=request.request_id,
                ok=False,
                error=str(exc),
            )
        with self.lock:
            self.audit.setdefault(request.command_id, []).append(record)
        return response


class MethodHubBrokerManager:
    def __init__(
        self,
        root: Path,
        client: MethodHubBrokerClient,
        *,
        max_result_bytes: int,
    ) -> None:
        self.root = root
        self.client = client
        self.max_result_bytes = max_result_bytes
        self._brokers: dict[UUID, tuple[socketserver.ThreadingUnixStreamServer, _BrokerState]] = {}

    def start(self, sandbox_id: UUID) -> Path:
        self.stop(sandbox_id)
        socket_dir = self.root / str(sandbox_id)
        socket_dir.mkdir(parents=True, mode=0o750)
        os.chown(socket_dir, 65532, 65532)
        state = _BrokerState(self.client, self.max_result_bytes)

        class Handler(socketserver.StreamRequestHandler):
            def handle(handler_self) -> None:
                payload = handler_self.rfile.readline(1_048_577)
                request = BrokerRequest.model_validate_json(payload)
                response = state.handle(request)
                handler_self.wfile.write(response.model_dump_json().encode() + b"\n")

        server = socketserver.ThreadingUnixStreamServer(
            str(socket_dir / SOCKET_NAME),
            Handler,
        )
        socket_path = socket_dir / SOCKET_NAME
        os.chown(socket_path, 65532, 65532)
        socket_path.chmod(0o660)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._brokers[sandbox_id] = (server, state)
        return socket_dir

    def activate(self, sandbox_id: UUID, command_id: UUID) -> None:
        broker = self._brokers.get(sandbox_id)
        if broker is None:
            raise RuntimeError("Method Hub broker is unavailable")
        with broker[1].lock:
            broker[1].active_commands.add(command_id)

    def deactivate(self, sandbox_id: UUID, command_id: UUID) -> None:
        broker = self._brokers.get(sandbox_id)
        if broker is None:
            return
        with broker[1].lock:
            broker[1].active_commands.discard(command_id)

    def drain(self, sandbox_id: UUID, command_id: UUID) -> list[dict[str, Any]]:
        broker = self._brokers.get(sandbox_id)
        if broker is None:
            return []
        state = broker[1]
        with state.lock:
            records = state.audit.pop(command_id, [])
        return [record.model_dump(mode="json") for record in records]

    def stop(self, sandbox_id: UUID) -> None:
        broker = self._brokers.pop(sandbox_id, None)
        if broker is not None:
            broker[0].shutdown()
            broker[0].server_close()
        shutil.rmtree(self.root / str(sandbox_id), ignore_errors=True)
```

Create `method_hub/__init__.py` exporting `MethodHubBrokerClient` and `MethodHubBrokerManager`.

- [ ] **Step 6: Commit the broker boundary**

```bash
cd ../AXIOM
git add services/sandbox-service/pyproject.toml \
  services/sandbox-service/src/sandbox_service/config.py \
  services/sandbox-service/src/sandbox_service/method_hub
git commit -m "feat(sandbox): add method hub socket broker"
```

## Task 3: Mount the Broker into gVisor and Tie It to Worker Lifecycle

**Files:**

- Create: `../AXIOM/services/sandbox-service/runtime/python/axiom_method_hub.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/runtime/base.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/runtime/runsc.py`
- Modify: `../AXIOM/services/sandbox-service/src/sandbox_service/worker.py`
- Modify: `../AXIOM/services/sandbox-service/Dockerfile.gvisor-worker`
- Modify: `../AXIOM/services/sandbox-service/docker-compose.yml`

- [ ] **Step 1: Extend the immutable runtime specification**

Replace `SandboxSpec` in `runtime/base.py` with:

```python
@dataclass(frozen=True)
class SandboxSpec:
    sandbox_id: str
    cpu_millis: int
    memory_mb: int
    pids_limit: int
    workspace_mb: int
    capability_profiles: tuple[str, ...] = ()
    method_hub_socket_dir: str | None = None
```

- [ ] **Step 2: Add the generated-code helper**

Create `runtime/python/axiom_method_hub.py`:

```python
from __future__ import annotations

import json
import os
import socket
from typing import Any
from uuid import uuid4

SOCKET_PATH = "/run/axiom/method-hub.sock"


def _request(payload: dict[str, Any]) -> Any:
    command_id = os.environ.get("AXIOM_COMMAND_ID", "").strip()
    if not command_id:
        raise RuntimeError("AXIOM_COMMAND_ID is unavailable")
    request = {
        "request_id": uuid4().hex,
        "command_id": command_id,
        **payload,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(SOCKET_PATH)
        client.sendall(json.dumps(request).encode() + b"\n")
        response_file = client.makefile("rb")
        response = json.loads(response_file.readline(1_048_577))
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "Method Hub call failed")
    return response.get("result")


def list_tools() -> list[dict[str, Any]]:
    return _request({"operation": "tools/list"})


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if not name.strip():
        raise ValueError("Method Hub tool name must not be empty")
    return _request(
        {
            "operation": "tools/call",
            "tool_name": name,
            "arguments": arguments,
        }
    )
```

- [ ] **Step 3: Mount only the broker directory while retaining network isolation**

Keep `_base()` unchanged with:

```python
"--network=none",
```

In `_write_bundle_config()`, validate the profile and append the directory mount only when enabled:

```python
if "method_hub" in spec.capability_profiles:
    if not spec.method_hub_socket_dir:
        raise RuntimeError("method_hub capability requires a broker socket")
    socket_dir = Path(spec.method_hub_socket_dir).resolve()
    if not (socket_dir / "method-hub.sock").is_socket():
        raise RuntimeError("method_hub broker socket is not ready")
    mounts.append(
        {
            "destination": "/run/axiom",
            "type": "bind",
            "source": str(socket_dir),
            "options": ["rbind", "rw", "nosuid", "nodev", "noexec"],
        }
    )
```

Do not add a network route, DNS configuration, host networking, or endpoint environment variable to the OCI bundle.

- [ ] **Step 4: Inject the authoritative command ID into each `runsc exec`**

Derive the authoritative UUID before building the `runsc exec` command. The worker's dependency-recovery attempt uses an `-retry` execution suffix, but both attempts belong to the same persisted command:

```python
audit_command_id = command_id.removesuffix("-retry")
```

Add this option before `provider_ref` in `RunscAdapter.execute()`:

```python
f"--env=AXIOM_COMMAND_ID={audit_command_id}",
```

The resulting command prefix is:

```python
[
    *self._base(),
    "exec",
    "--cwd=/workspace",
    "--user=65532:65532",
    f"--env=AXIOM_COMMAND_ID={audit_command_id}",
    "--internal-pid-file",
    str(pid_path),
    provider_ref,
]
```

- [ ] **Step 5: Construct and own the broker manager in the worker**

Add a factory near `build_adapter()`:

```python
def build_method_hub_broker_manager(
    settings: SandboxSettings,
) -> MethodHubBrokerManager | None:
    if not settings.method_hub_mcp_url:
        return None
    client = MethodHubBrokerClient(
        settings.method_hub_mcp_url,
        token=settings.method_hub_token,
        timeout_seconds=settings.method_hub_timeout_seconds,
    )
    return MethodHubBrokerManager(
        Path(settings.method_hub_broker_root),
        client,
        max_result_bytes=settings.method_hub_max_result_bytes,
    )
```

Extend `SandboxWorker.__init__()` with `method_hub_brokers: MethodHubBrokerManager | None` and save it as `self.method_hub_brokers`.

- [ ] **Step 6: Start, restore, drain, and stop brokers in lifecycle order**

Before `adapter.create()` in the `create` operation:

```python
profiles = tuple(sandbox.capability_profiles or [])
socket_dir: Path | None = None
if "method_hub" in profiles:
    if self.method_hub_brokers is None:
        raise RuntimeError("method_hub capability is not configured on this worker")
    socket_dir = self.method_hub_brokers.start(sandbox.id)

try:
    provider_ref = self.adapter.create(
        SandboxSpec(
            sandbox_id=str(sandbox.id),
            cpu_millis=sandbox.cpu_millis,
            memory_mb=sandbox.memory_mb,
            pids_limit=sandbox.pids_limit,
            workspace_mb=sandbox.workspace_mb,
            capability_profiles=profiles,
            method_hub_socket_dir=str(socket_dir) if socket_dir else None,
        )
    )
except Exception:
    if self.method_hub_brokers is not None:
        self.method_hub_brokers.stop(sandbox.id)
    raise
```

Immediately before the first `adapter.execute()` for an enabled sandbox, activate the persisted command UUID:

```python
method_hub_active = "method_hub" in (sandbox.capability_profiles or [])
if method_hub_active:
    if self.method_hub_brokers is None:
        raise RuntimeError("Method Hub broker is unavailable")
    self.method_hub_brokers.activate(sandbox.id, command_id)
```

Both the first execution and dependency-recovery execution remain inside the same active command scope. Initialize `method_calls: list[dict[str, Any]] = []`, wrap the existing block beginning with `dependency_path, dependency_error = prepare_dependency_path(...)` and ending after `flush_buffered_logs()` in `try`, then append this exact `finally` block:

```python
finally:
    if method_hub_active and self.method_hub_brokers is not None:
        method_calls = self.method_hub_brokers.drain(sandbox.id, command_id)
        self.method_hub_brokers.deactivate(sandbox.id, command_id)
```

The drain/deactivate statements execute from `finally`, including timeout, cancellation, dependency failure, and adapter exceptions.

Inside the final command transaction:

```python
current.method_calls = method_calls
```

For `stop`, `delete`, create failure, and lost-runtime reconciliation, call:

```python
if self.method_hub_brokers is not None:
    self.method_hub_brokers.stop(sandbox.id)
```

During `reconcile()`, mark every running `method_hub` sandbox failed if the worker process has restarted and no live broker exists. A recreated host directory is not guaranteed to replace the directory inode already bind-mounted into gVisor, so require sandbox reprovisioning instead of attempting an unsafe broker restore.

- [ ] **Step 7: Install the helper into the sandbox rootfs**

In `Dockerfile.gvisor-worker`, copy the helper before switching the rootfs stage to UID 65532:

```dockerfile
FROM python:3.12-slim AS sandbox-rootfs

RUN groupadd --gid 65532 sandbox \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace \
    && chown 65532:65532 /workspace

COPY services/sandbox-service/runtime/python/axiom_method_hub.py \
    /usr/local/lib/python3.12/site-packages/axiom_method_hub.py

RUN chmod 0644 /usr/local/lib/python3.12/site-packages/axiom_method_hub.py

USER 65532:65532
WORKDIR /workspace
```

- [ ] **Step 8: Configure local Compose without exposing secrets to the sandbox**

Add these worker environment values in `services/sandbox-service/docker-compose.yml`:

```yaml
METHOD_HUB_MCP_URL: ${METHOD_HUB_MCP_URL:-http://methods-hub:8000/mcp}
METHOD_HUB_TOKEN: ${METHOD_HUB_TOKEN:-}
METHOD_HUB_BROKER_ROOT: /var/lib/axiom-sandbox/method-hub-brokers
```

The worker and Methods-Hub service must share the Compose network. The gVisor child remains networkless and receives only the socket bind mount.

- [ ] **Step 9: Commit runtime integration**

```bash
cd ../AXIOM
git add services/sandbox-service/runtime/python/axiom_method_hub.py \
  services/sandbox-service/src/sandbox_service/runtime/base.py \
  services/sandbox-service/src/sandbox_service/runtime/runsc.py \
  services/sandbox-service/src/sandbox_service/worker.py \
  services/sandbox-service/Dockerfile.gvisor-worker \
  services/sandbox-service/docker-compose.yml
git commit -m "feat(sandbox): mount method hub broker in gvisor"
```

## Task 4: Extend the AXIOM Sandbox Client Contract

**Files:**

- Modify: `../AXIOM/packages/axiom-sandbox-client/src/axiom_sandbox_client/client.py`
- Modify: `../AXIOM/packages/axiom-sandbox-client/src/axiom_sandbox_client/models.py`

- [ ] **Step 1: Add client-side capability and audit models**

In `models.py`, add:

```python
from typing import Any, Literal

CapabilityProfile = Literal["method_hub"]


class MethodHubCallRecord(BaseModel):
    command_id: UUID
    tool_name: str
    status: Literal["completed", "failed"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime
```

Add these fields to the existing response models:

```python
class SandboxRecord(BaseModel):
    capability_profiles: list[CapabilityProfile] = Field(default_factory=list)


class CommandResult(BaseModel):
    method_calls: list[MethodHubCallRecord] = Field(default_factory=list)
```

- [ ] **Step 2: Send capability profiles from the sync client**

Change `SandboxClient.create_sandbox()` to:

```python
def create_sandbox(
    self,
    workspace_id: UUID,
    *,
    idle_ttl_seconds: int = 600,
    capability_profiles: list[CapabilityProfile] | None = None,
) -> Sandbox:
    record = self._request(
        "POST",
        "/api/v1/sandboxes",
        json={
            "workspace_id": str(workspace_id),
            "runtime": "python",
            "idle_ttl_seconds": idle_ttl_seconds,
            "capability_profiles": capability_profiles or [],
        },
        model=SandboxRecord,
    )
    return Sandbox(self, record)
```

Add the same keyword to `run_once()` and forward it:

```python
sandbox = self.create_sandbox(
    workspace_id,
    idle_ttl_seconds=idle_ttl_seconds,
    capability_profiles=capability_profiles,
)
```

- [ ] **Step 3: Mirror the keyword in the async client**

Locate `AsyncSandboxClient.create_sandbox()` and `run_once()` in the same file and use the identical JSON field and `CapabilityProfile` type. Keep sync and async request payloads byte-for-byte equivalent after JSON serialization.

- [ ] **Step 4: Commit the public client contract**

```bash
cd ../AXIOM
git add packages/axiom-sandbox-client/src/axiom_sandbox_client/client.py \
  packages/axiom-sandbox-client/src/axiom_sandbox_client/models.py
git commit -m "feat(client): support sandbox capability profiles"
```

## Task 5: Bind Direct MCP Tools and Import Sandbox Audit into the SDK

**Files:**

- Create: `packages/sdk/src/data_intelligence_sdk/tools/mcp.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/tools/__init__.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/runtime/engine_runtime.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/runtime/deep_agent_sandbox.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/tools/execution.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/core/pipeline.py`

- [ ] **Step 1: Carry the immutable discovered catalog through pipeline runtime**

In `engine_runtime.py`, import `MCPToolDefinition` and add:

```python
mcp_tools: tuple[MCPToolDefinition, ...] = ()
```

In `DataIntelligencePipeline.__init__()`, add and store:

```python
mcp_tools: tuple[MCPToolDefinition, ...] = (),
```

When constructing `EngineRuntimeContext`, pass:

```python
mcp_tools=self.mcp_tools,
```

This catalog is discovered before pipeline construction and remains stable for the request. Engines must not call `list_tools()` independently and observe a different catalog mid-run.

- [ ] **Step 2: Create direct LangChain tools from MCP definitions**

Create `tools/mcp.py`:

```python
from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition


def _call_remote_tool(
    runtime: EngineRuntimeContext,
    definition: MCPToolDefinition,
    arguments: dict[str, Any],
) -> Any:
    if runtime.mcp_client is None:
        raise RuntimeError("Method Hub is enabled but its MCP client is unavailable.")
    try:
        result = runtime.mcp_client.call_tool(definition.name, arguments)
    except Exception as exc:
        runtime.run_context.record_method_call(
            definition.name,
            status="failed",
            inputs=arguments,
            outputs={"error": str(exc), "provider": "mcp"},
        )
        raise
    runtime.run_context.record_method_call(
        definition.name,
        status="completed",
        inputs=arguments,
        outputs={"result": result, "provider": "mcp"},
    )
    return result


def create_mcp_tools(runtime: EngineRuntimeContext) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for definition in runtime.mcp_tools:
        def invoke(
            _definition: MCPToolDefinition = definition,
            **arguments: Any,
        ) -> Any:
            return _call_remote_tool(runtime, _definition, arguments)

        tools.append(
            StructuredTool.from_function(
                func=invoke,
                name=definition.name,
                description=definition.description or f"Method Hub tool {definition.name}",
                args_schema=definition.input_schema,
                infer_schema=False,
            )
        )
    return tools


def mcp_catalog_prompt(runtime: EngineRuntimeContext) -> list[dict[str, Any]]:
    return [
        {
            "name": definition.name,
            "description": definition.description,
            "input_schema": definition.input_schema,
            "capability_names": list(definition.capability_names),
        }
        for definition in runtime.mcp_tools
    ]
```

Export `create_mcp_tools` and `mcp_catalog_prompt` from `tools/__init__.py`.

- [ ] **Step 3: Surface broker audits from sandbox command results**

In `DeepAgentSandboxSession.execute_python()`, add:

```python
"method_calls": [
    (
        call.model_dump(mode="json")
        if hasattr(call, "model_dump")
        else dict(call)
    )
    for call in getattr(command, "method_calls", [])
],
```

The sandbox service record is authoritative. Generated stdout must never be parsed to infer whether a Method Hub call occurred.

- [ ] **Step 4: Merge broker audits into the engine run context**

Add this helper to `tools/execution.py`:

```python
def _record_sandbox_method_calls(
    runtime: EngineRuntimeContext,
    observation: dict[str, Any],
) -> None:
    for call in observation.get("method_calls", []):
        runtime.run_context.record_method_call(
            str(call["tool_name"]),
            status=str(call["status"]),
            inputs=dict(call.get("arguments", {})),
            outputs={
                "result": call.get("result"),
                "error": call.get("error"),
                "provider": "sandbox_mcp_broker",
                "command_id": call.get("command_id"),
                "started_at": call.get("started_at"),
                "finished_at": call.get("finished_at"),
            },
            log_refs=(
                [f"sandbox-command://{observation['command_id']}"]
                if observation.get("command_id")
                else []
            ),
        )
```

Call it immediately after `runtime.sandbox.execute_python()` returns and before recording the outer `execute_python` call:

```python
_record_sandbox_method_calls(runtime, observation)
```

- [ ] **Step 5: Commit the SDK MCP binding**

```bash
git add packages/sdk/src/data_intelligence_sdk/tools/mcp.py \
  packages/sdk/src/data_intelligence_sdk/tools/__init__.py \
  packages/sdk/src/data_intelligence_sdk/runtime/engine_runtime.py \
  packages/sdk/src/data_intelligence_sdk/runtime/deep_agent_sandbox.py \
  packages/sdk/src/data_intelligence_sdk/tools/execution.py \
  packages/sdk/src/data_intelligence_sdk/core/pipeline.py
git commit -m "feat(sdk): bind method hub mcp tools"
```

## Task 6: Add API Runtime Option Resolution and Capability Discovery

**Files:**

- Create: `packages/api/src/data_intelligence_api/application/runtime_capabilities.py`
- Create: `packages/api/src/data_intelligence_api/http/routers/runtime_capabilities.py`
- Modify: `packages/api/src/data_intelligence_api/http/schemas/responses.py`
- Modify: `packages/api/src/data_intelligence_api/domain/workflow.py`
- Modify: `packages/api/src/data_intelligence_api/infrastructure/config/settings.py`
- Modify: `packages/api/src/data_intelligence_api/app/factory.py`

- [ ] **Step 1: Define request and public capability schemas**

Add to `http/schemas/responses.py`:

```python
class RuntimeOptionsRequest(BaseModel):
    method_hub_enabled: bool | None = None


class MethodHubCapabilityResponse(BaseModel):
    default_enabled: bool
    available: bool


class RuntimeCapabilitiesResponse(BaseModel):
    method_hub: MethodHubCapabilityResponse
```

Add the option to request and history models:

```python
class CreateResponseRequest(BaseModel):
    input: str | None = None
    data_corpus_package: DataCorpusPackageRequest
    user_id: str | None = None
    session_id: str | None = None
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )


class ResponseHistoryDetail(BaseModel):
    runtime_options: RuntimeOptionsRequest = Field(
        default_factory=RuntimeOptionsRequest
    )
```

- [ ] **Step 2: Define the resolved domain option**

In `domain/workflow.py`, add:

```python
@dataclass(frozen=True, slots=True)
class WorkflowRuntimeOptions:
    method_hub_enabled: bool
```

Add it to `WorkflowInvocation`:

```python
runtime_options: WorkflowRuntimeOptions
```

- [ ] **Step 3: Load private endpoint and public default from TOML once**

Add fields to `ApiSettings`:

```python
method_hub_default_enabled: bool = False
method_hub_endpoint: str = "http://localhost:8000/mcp"
```

In `from_env()`, retain the existing `ConfigManager` instance:

```python
config_manager = ConfigManager(model_config_path)
payload = config_manager.load()
method_hub = config_manager.method_hub_settings()
```

Then set:

```python
method_hub_default_enabled=method_hub.enabled,
method_hub_endpoint=method_hub.endpoint,
```

The endpoint is application-private. It must not appear in any response model.

- [ ] **Step 4: Add capability resolution and fail-fast discovery**

Create `application/runtime_capabilities.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from data_intelligence_sdk.runtime.mcp_client import (
    MCPMethodClient,
    MCPToolDefinition,
)

from data_intelligence_api.domain.workflow import WorkflowRuntimeOptions
from data_intelligence_api.http.schemas.responses import RuntimeOptionsRequest


class MethodHubUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedMethodHub:
    client: MCPMethodClient | None
    tools: tuple[MCPToolDefinition, ...]


def resolve_runtime_options(
    requested: RuntimeOptionsRequest,
    *,
    default_enabled: bool,
) -> WorkflowRuntimeOptions:
    enabled = (
        default_enabled
        if requested.method_hub_enabled is None
        else requested.method_hub_enabled
    )
    return WorkflowRuntimeOptions(method_hub_enabled=enabled)


def resolve_method_hub(
    options: WorkflowRuntimeOptions,
    *,
    endpoint: str,
) -> ResolvedMethodHub:
    if not options.method_hub_enabled:
        return ResolvedMethodHub(client=None, tools=())
    client = MCPMethodClient(endpoint)
    try:
        tools = tuple(client.list_tools())
    except Exception as exc:
        raise MethodHubUnavailableError(
            "Method Hub is enabled for this request but is unavailable."
        ) from exc
    return ResolvedMethodHub(client=client, tools=tools)


def method_hub_available(endpoint: str) -> bool:
    try:
        MCPMethodClient(endpoint).connect()
    except Exception:
        return False
    return True
```

- [ ] **Step 5: Add the capability endpoint**

Create `http/routers/runtime_capabilities.py`:

```python
from fastapi import APIRouter

from data_intelligence_api.application.runtime_capabilities import (
    method_hub_available,
)
from data_intelligence_api.http.schemas.responses import (
    MethodHubCapabilityResponse,
    RuntimeCapabilitiesResponse,
)
from data_intelligence_api.infrastructure.config.settings import ApiSettings


def create_runtime_capabilities_router(settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/v1/runtime-capabilities",
        response_model=RuntimeCapabilitiesResponse,
    )
    def get_runtime_capabilities() -> RuntimeCapabilitiesResponse:
        return RuntimeCapabilitiesResponse(
            method_hub=MethodHubCapabilityResponse(
                default_enabled=settings.method_hub_default_enabled,
                available=method_hub_available(settings.method_hub_endpoint),
            )
        )

    return router
```

Register it in `app/factory.py`:

```python
app.include_router(create_runtime_capabilities_router(resolved_settings))
```

- [ ] **Step 6: Commit API capability discovery**

```bash
git add packages/api/src/data_intelligence_api/application/runtime_capabilities.py \
  packages/api/src/data_intelligence_api/http/routers/runtime_capabilities.py \
  packages/api/src/data_intelligence_api/http/schemas/responses.py \
  packages/api/src/data_intelligence_api/domain/workflow.py \
  packages/api/src/data_intelligence_api/infrastructure/config/settings.py \
  packages/api/src/data_intelligence_api/app/factory.py
git commit -m "feat(api): expose method hub runtime capability"
```

## Task 7: Propagate the Resolved Option Through Workflow and Sandbox Provisioning

**Files:**

- Modify: `packages/api/src/data_intelligence_api/application/workflow.py`
- Modify: `packages/api/src/data_intelligence_api/infrastructure/workflow/pipeline_factory.py`
- Modify: `packages/api/src/data_intelligence_api/http/routers/responses.py`

- [ ] **Step 1: Resolve the request option when building the invocation**

Change `build_workflow_invocation()` to accept the configured default:

```python
def build_workflow_invocation(
    request: CreateResponseRequest,
    data_corpus_root: Path,
    *,
    method_hub_default_enabled: bool,
) -> WorkflowInvocation:
    query_text = (request.input or "").strip() or DEFAULT_QUERY
    return WorkflowInvocation(
        query=UserQuery(
            text=query_text,
            user_id=request.user_id,
            session_id=request.session_id,
        ),
        corpus_package=DataCorpusPackage(
            sources=resolve_sources(
                request.data_corpus_package.sources,
                data_corpus_root,
            ),
            schemas=request.data_corpus_package.schemas,
            metadata=request.data_corpus_package.metadata,
        ),
        session_context=SessionContext(session_id=request.session_id),
        user_context=UserContext(user_id=request.user_id),
        runtime_options=resolve_runtime_options(
            request.runtime_options,
            default_enabled=method_hub_default_enabled,
        ),
    )
```

- [ ] **Step 2: Make the pipeline factory require explicit runtime options**

Replace `default_pipeline_factory()` with:

```python
def default_pipeline_factory(
    *,
    logger: RuntimeLogger,
    runtime_options: WorkflowRuntimeOptions,
) -> DataIntelligencePipeline:
    config_manager = ConfigManager(os.getenv("MODEL_CONFIG_PATH") or None)
    method_hub_settings = config_manager.method_hub_settings()
    resolved_method_hub = resolve_method_hub(
        runtime_options,
        endpoint=method_hub_settings.endpoint,
    )
    return create_example_pipeline(
        logger=logger,
        config_manager=config_manager,
        use_llm_spec_builder=True,
        intent_service_base_url=os.getenv("INTENT_SERVICE_BASE_URL") or None,
        mcp_client=resolved_method_hub.client,
        mcp_tools=resolved_method_hub.tools,
        method_hub_enabled=runtime_options.method_hub_enabled,
    )
```

Update `execute_workflow()`, `prepare_workflow()`, `revise_workflow()`, and `execute_prepared_workflow()` so every `pipeline_factory()` call includes the same named argument:

```python
pipeline = pipeline_factory(
    logger=logger,
    runtime_options=runtime_options,
)
```

Add a required `runtime_options: WorkflowRuntimeOptions` parameter to revise and execute-prepared functions; `prepare_workflow()` reads `invocation.runtime_options`.

- [ ] **Step 3: Provision the fixed sandbox profile from the resolved mode**

Extend `_AxiomSandboxProvider.__init__()`:

```python
capability_profiles: tuple[str, ...] = (),
```

Store it and change `open()` to:

```python
sandbox = self.client.create_sandbox(
    self.workspace_id,
    capability_profiles=list(self.capability_profiles),
)
```

Extend `_configure_axiom_sandbox_provider()` with `method_hub_enabled: bool` and construct:

```python
return _AxiomSandboxProvider(
    sandbox_client,
    workspace_id=UUID(settings.workspace_id),
    cleanup=not keep_sandbox,
    capability_profiles=("method_hub",) if method_hub_enabled else (),
)
```

Add parameters to `create_example_pipeline()`:

```python
mcp_tools: tuple[MCPToolDefinition, ...] = (),
method_hub_enabled: bool = False,
```

Pass `method_hub_enabled` into `_configure_axiom_sandbox_provider()` and `mcp_tools` into `DataIntelligencePipeline`.

- [ ] **Step 4: Persist the resolved boolean before confirmation**

In `create_response()`, call:

```python
invocation = build_workflow_invocation(
    payload,
    settings.data_corpus_root,
    method_hub_default_enabled=settings.method_hub_default_enabled,
)
```

When creating the pending run, replace the raw payload with an explicit resolved value:

```python
request_payload={
    **payload.model_dump(mode="json"),
    "runtime_options": {
        "method_hub_enabled": invocation.runtime_options.method_hub_enabled,
    },
},
```

This prevents a TOML default change between initial preparation and later confirmation from changing an existing request.

- [ ] **Step 5: Reuse the persisted option for revise and confirm**

After loading the claimed run in `decide_response()`, reconstruct:

```python
runtime_options = WorkflowRuntimeOptions(
    method_hub_enabled=bool(
        run.request_payload.get("runtime_options", {}).get(
            "method_hub_enabled",
            settings.method_hub_default_enabled,
        )
    )
)
```

Pass `runtime_options` to `revise_workflow()` and `execute_prepared_workflow()`. Do not inspect current frontend state or current TOML defaults during a decision request.

- [ ] **Step 6: Return the persisted option in history**

In `_history_detail()`, add:

```python
runtime_options=RuntimeOptionsRequest.model_validate(
    run.request_payload.get("runtime_options", {})
),
```

- [ ] **Step 7: Map fail-fast Method Hub errors to a stable response code**

Import `MethodHubUnavailableError` in `responses.py` and replace `_run_worker()` with:

```python
def _run_worker(
    operation: Callable[[], object],
    messages: queue.Queue[object],
    *,
    error_code: str,
    safe_error: str,
) -> None:
    try:
        messages.put(WorkflowResultMessage(result=operation()))
    except MethodHubUnavailableError:
        logger.exception("Method Hub is unavailable")
        messages.put(
            WorkflowFailedMessage(
                code="method_hub_unavailable",
                message=(
                    "Method Hub is enabled for this request but is unavailable."
                ),
            )
        )
    except Exception:
        logger.exception("Data Intelligence workflow phase failed")
        messages.put(WorkflowFailedMessage(code=error_code, message=safe_error))
```

All other pipeline failures retain their existing safe error code/message. Do not silently rebuild the pipeline with `mcp_client=None`.

- [ ] **Step 8: Commit end-to-end request propagation**

```bash
git add packages/api/src/data_intelligence_api/application/workflow.py \
  packages/api/src/data_intelligence_api/infrastructure/workflow/pipeline_factory.py \
  packages/api/src/data_intelligence_api/http/routers/responses.py
git commit -m "feat(api): propagate method hub request option"
```

## Task 8: Update General and Report Engines for Direct and Composite MCP Use

**Files:**

- Modify: `packages/sdk/src/data_intelligence_sdk/engines/general.py`
- Modify: `packages/sdk/src/data_intelligence_sdk/engines/report.py`

- [ ] **Step 1: Expose direct MCP tools to the General Purpose agent**

Import the shared bindings:

```python
from data_intelligence_sdk.tools import (
    create_execute_python_tool,
    create_mcp_tools,
    mcp_catalog_prompt,
)
```

Build the request tool list in `run()`:

```python
execute_python = create_execute_python_tool(runtime)
mcp_tools = create_mcp_tools(runtime)
self._register_minimal_profile()
agent = self.agent_factory(
    model=self.llm,
    tools=[*mcp_tools, execute_python],
    system_prompt=self._system_prompt(spec, corpus_package, runtime),
    backend=runtime.sandbox.backend,
    subagents=[],
    name="axiom-general-analysis",
)
```

- [ ] **Step 2: Replace the single-action prompt with mode-aware routing rules**

Use this core prompt block in `_system_prompt()`:

```python
catalog = mcp_catalog_prompt(runtime)
method_hub_instructions = (
    "Method Hub is enabled. For one tool operation, call the matching Method "
    "Hub tool directly. For multiple tool calls or any transformation that "
    "combines/modifies tool results, call execute_python and import "
    "`call_tool` from `axiom_method_hub`; assign the final JSON-serializable "
    "value to `result`. Never attempt HTTP access from generated code.\n\n"
    f"Method Hub catalog:\n{json.dumps(catalog, indent=2, default=str)}\n\n"
    if catalog
    else (
        "Method Hub is disabled. Use execute_python for the complete analysis "
        "and assign the final JSON-serializable value to `result`.\n\n"
    )
)
return (
    "You are the only analysis agent for this request. Use the staged data "
    "and the available tools to answer the objective.\n\n"
    f"{method_hub_instructions}"
    "Base the final answer only on successful tool or sandbox output; never "
    "invent data. If an execution fails, inspect the structured error and "
    "correct the next attempt.\n\n"
    f"Objective: {spec.objective}\n"
    f"Constraints: {json.dumps(spec.constraints, default=str)}\n"
    "Staged sources:\n"
    f"{json.dumps(source_payload, indent=2, default=str)}"
)
```

- [ ] **Step 3: Accept either a direct MCP call or sandbox execution as grounding**

Replace `_latest_successful_execution()` with:

```python
def _latest_successful_grounding(
    runtime: EngineRuntimeContext,
) -> dict[str, Any] | None:
    for call in reversed(runtime.run_context.trace.method_calls):
        if call.status != "completed":
            continue
        if call.method_name == "execute_python":
            if call.outputs.get("success") is True:
                return call.outputs
            continue
        return {
            "success": True,
            "result": call.outputs.get("result", call.outputs),
            "stdout": "",
            "stderr": "",
            "method_name": call.method_name,
        }
    return None
```

Rename local `execution` variables to `grounding` and update retry/failure text to require “a successful Method Hub or execute_python result.” This preserves code-only behavior when the catalog is empty and permits simple direct tool calls when enabled.

- [ ] **Step 4: Add remote tools to Report Engine inventory**

Extend `_method_hub_payload()`:

```python
local_methods = [
    {
        "tool_name": registered.name,
        "description": registered.metadata.get("description", ""),
        "parameters_schema": registered.metadata.get(
            "parameters_schema", _method_parameters_schema(registered.method)
        ),
        "output_schema": registered.metadata.get("output_schema", {}),
        "capability_names": registered.capability_names,
        "trust_level": registered.trust_level,
        "provider": "local",
    }
    for registered in runtime.method_hub.list_methods()
]
remote_methods = [
    {
        "tool_name": definition.name,
        "description": definition.description,
        "parameters_schema": definition.input_schema,
        "output_schema": definition.metadata.get("output_schema", {}),
        "capability_names": list(definition.capability_names),
        "trust_level": "platform_remote",
        "provider": "mcp",
    }
    for definition in runtime.mcp_tools
]
return [*local_methods, *remote_methods]
```

- [ ] **Step 5: Invoke Report Engine remote routes directly through MCP**

Update `ToolExecutor.execute_existing()` before local lookup:

```python
remote_names = {definition.name for definition in runtime.mcp_tools}
try:
    if tool_name in remote_names:
        if runtime.mcp_client is None:
            raise RuntimeError("Method Hub MCP client is unavailable.")
        result = runtime.mcp_client.call_tool(tool_name, arguments)
        provider = "mcp"
    else:
        method = runtime.method_hub.get(tool_name)
        result = method(**arguments)
        provider = "local"
    status = "completed_no_data" if not _normalize_rows(result) else "completed"
    runtime.run_context.record_method_call(
        tool_name,
        status="completed",
        inputs=arguments,
        outputs={
            "result": result,
            "result_summary": self._result_summary(result),
            "provider": provider,
        },
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "tool_name": tool_name,
        "arguments": arguments,
        "raw_result": result,
        "error": None,
    }
except Exception as exc:
    provider = "mcp" if tool_name in remote_names else "local"
    runtime.run_context.record_method_call(
        tool_name,
        status="failed",
        inputs=arguments,
        outputs={"error": str(exc), "provider": provider},
    )
    return {
        "schema_version": "1.0",
        "status": "failed",
        "tool_name": tool_name,
        "arguments": arguments,
        "raw_result": None,
        "error": str(exc),
    }
```

A remote call error remains an engine-visible failure; it must not disable Method Hub for the rest of the run.

- [ ] **Step 6: Run generated Report Engine composition in the request sandbox**

Update `CODE_AGENT_PROMPT` with these concrete rules:

```text
6. For Method Hub composition, import `call_tool` from `axiom_method_hub`.
7. Never open HTTP sockets or embed an MCP endpoint/token.
8. Define the requested function and keep its return value JSON-serializable.
```

In `ToolExecutor.execute_generated()`, prefer the request sandbox and run artifact:

```python
if runtime.sandbox is None or runtime.run_artifact is None:
    return {
        "schema_version": "1.0",
        "status": "failed",
        "tool_name": interface.name,
        "raw_result": sample_data,
        "error": "Request sandbox is unavailable.",
    }

arguments = code_spec.get("execution_arguments", {})
if not isinstance(arguments, dict):
    arguments = {}
source_code = str(code_spec.get("source_code") or "")
program = (
    "import json\n"
    f"{source_code}\n"
    f"result = {interface.name}(**json.loads({json.dumps(json.dumps(arguments))}))\n"
)
observation = runtime.sandbox.execute_python(
    program,
    runtime.run_artifact,
)
for call in observation.get("method_calls", []):
    runtime.run_context.record_method_call(
        str(call["tool_name"]),
        status=str(call["status"]),
        inputs=dict(call.get("arguments", {})),
        outputs={
            "result": call.get("result"),
            "error": call.get("error"),
            "provider": "sandbox_mcp_broker",
            "command_id": call.get("command_id"),
        },
    )
if not observation.get("success"):
    raise RuntimeError(observation.get("stderr") or "Generated tool failed.")
result = observation.get("result")
```

Continue with the existing `completed`/`completed_no_data` result construction. Remove the old path that registers and runs generated code in-process through `runtime.sandbox_executor`; generated code must use the request-scoped sandbox.

- [ ] **Step 7: Commit engine behavior**

```bash
git add packages/sdk/src/data_intelligence_sdk/engines/general.py \
  packages/sdk/src/data_intelligence_sdk/engines/report.py
git commit -m "feat(engines): route work through method hub"
```

## Task 9: Add the Frontend Method Hub Toggle and Per-Request Payload

**Files:**

- Create: `web/new-components/responses-chat/MethodHubToggle.tsx`
- Create: `web/utils/runtime-capabilities.ts`
- Modify: `web/types/responses.ts`
- Modify: `web/utils/responses-request.ts`
- Modify: `web/hooks/use-responses-chat.ts`
- Modify: `web/pages/index.tsx`
- Modify: `web/locales/en/chat.ts`
- Modify: `web/locales/zh/chat.ts`

- [ ] **Step 1: Define frontend request and capability types**

Add to `web/types/responses.ts`:

```typescript
export interface RuntimeOptions {
  method_hub_enabled: boolean;
}

export interface RuntimeCapabilities {
  method_hub: {
    default_enabled: boolean;
    available: boolean;
  };
}
```

Extend request and history types:

```typescript
export interface CreateResponseRequest {
  input?: string;
  data_corpus_package: CorpusRequest;
  session_id: string;
  runtime_options: RuntimeOptions;
}

export interface ResponseHistoryDetail {
  runtime_options: RuntimeOptions;
}
```

- [ ] **Step 2: Add the capability fetch helper**

Create `web/utils/runtime-capabilities.ts`:

```typescript
import type { RuntimeCapabilities } from '@/types/responses';

export async function fetchRuntimeCapabilities(signal?: AbortSignal): Promise<RuntimeCapabilities> {
  const response = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/runtime-capabilities`, { signal });
  if (!response.ok) {
    throw new Error(`Runtime capability request failed with status ${response.status}`);
  }
  return response.json() as Promise<RuntimeCapabilities>;
}

export function initialMethodHubEnabled(capabilities: RuntimeCapabilities): boolean {
  return capabilities.method_hub.default_enabled;
}
```

- [ ] **Step 3: Include the explicit boolean in reusable request preparation**

Change `prepareResponseSubmission()`:

```typescript
export function prepareResponseSubmission(
  input: string,
  sources: string[],
  sessionId: string,
  methodHubEnabled: boolean,
): PreparedResponseSubmission | null {
  const normalizedSources = sources.map(source => source.trim()).filter(Boolean);
  const normalizedInput = input.trim();
  if (!normalizedInput && normalizedSources.length === 0) return null;

  return {
    visibleInput: normalizedInput || DEFAULT_RESPONSE_QUERY,
    request: {
      ...(normalizedInput ? { input: normalizedInput } : {}),
      data_corpus_package: { sources: normalizedSources, schemas: {}, metadata: {} },
      session_id: sessionId,
      runtime_options: { method_hub_enabled: methodHubEnabled },
    },
  };
}
```

Change the reusable hook signature to `submit(input, sources, methodHubEnabled)` and forward the boolean to `prepareResponseSubmission()`.

- [ ] **Step 4: Create one shared compact toggle component**

Create `MethodHubToggle.tsx`:

```tsx
import ApiOutlined from '@ant-design/icons/ApiOutlined';
import { Switch, Tooltip } from 'antd';

type MethodHubToggleProps = {
  enabled: boolean;
  available: boolean;
  loading: boolean;
  label: string;
  unavailableLabel: string;
  onChange: (enabled: boolean) => void;
};

export default function MethodHubToggle({
  enabled,
  available,
  loading,
  label,
  unavailableLabel,
  onChange,
}: MethodHubToggleProps) {
  const cannotEnable = !available && !enabled;
  return (
    <Tooltip title={available ? label : unavailableLabel}>
      <label className='flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-xs text-gray-600 shadow-sm dark:border-white/10 dark:bg-[#25262b] dark:text-gray-300'>
        <ApiOutlined className={enabled ? 'text-blue-600' : 'text-gray-400'} />
        <span>{label}</span>
        <Switch
          size='small'
          checked={enabled}
          loading={loading}
          disabled={loading || cannotEnable}
          onChange={onChange}
        />
      </label>
    </Tooltip>
  );
}
```

If the server default is enabled while availability is false, the switch remains on and can be turned off. If it is off and unavailable, it cannot be turned on.

- [ ] **Step 5: Fetch capability state once in the main page**

Add state near existing composer state:

```typescript
const [methodHubEnabled, setMethodHubEnabled] = useState(false);
const [methodHubAvailable, setMethodHubAvailable] = useState(false);
const [runtimeCapabilitiesLoading, setRuntimeCapabilitiesLoading] = useState(true);
```

Add one mount effect:

```typescript
useEffect(() => {
  const controller = new AbortController();
  setRuntimeCapabilitiesLoading(true);
  fetchRuntimeCapabilities(controller.signal)
    .then(capabilities => {
      setMethodHubAvailable(capabilities.method_hub.available);
      setMethodHubEnabled(initialMethodHubEnabled(capabilities));
    })
    .catch(error => {
      if (controller.signal.aborted) return;
      setMethodHubAvailable(false);
      setMethodHubEnabled(false);
      message.error(error instanceof Error ? error.message : t('method_hub_unavailable'));
    })
    .finally(() => {
      if (!controller.signal.aborted) setRuntimeCapabilitiesLoading(false);
    });
  return () => controller.abort();
}, [t]);
```

- [ ] **Step 6: Render the same state in both composer toolbars**

Insert this component beside the Add button in both the active-chat and hero composer toolbars:

```tsx
<MethodHubToggle
  enabled={methodHubEnabled}
  available={methodHubAvailable}
  loading={runtimeCapabilitiesLoading}
  label={t('method_hub_toggle')}
  unavailableLabel={t('method_hub_unavailable')}
  onChange={setMethodHubEnabled}
/>
```

Do not create separate state for the two visual instances.

- [ ] **Step 7: Submit and restore the per-request value**

Add to the main `POST /api/v1/responses` body:

```typescript
runtime_options: {
  method_hub_enabled: methodHubEnabled,
},
```

When history detail loads, restore:

```typescript
setMethodHubEnabled(detail.runtime_options.method_hub_enabled);
```

This restoration affects the composer for a follow-up/new run; it does not mutate the completed historical run.

- [ ] **Step 8: Add localized labels**

Add to `web/locales/en/chat.ts`:

```typescript
method_hub_toggle: 'Method Hub',
method_hub_unavailable: 'Method Hub is unavailable. Turn it off to run code-only.',
```

Add to `web/locales/zh/chat.ts`:

```typescript
method_hub_toggle: 'Method Hub',
method_hub_unavailable: 'Method Hub 当前不可用，请关闭后使用纯代码模式。',
```

- [ ] **Step 9: Commit the frontend toggle**

```bash
git add web/new-components/responses-chat/MethodHubToggle.tsx \
  web/utils/runtime-capabilities.ts \
  web/types/responses.ts \
  web/utils/responses-request.ts \
  web/hooks/use-responses-chat.ts \
  web/pages/index.tsx \
  web/locales/en/chat.ts \
  web/locales/zh/chat.ts
git commit -m "feat(web): add method hub runtime toggle"
```

## Task 10: Document Configuration, Security, and Local Operation

**Files:**

- Modify: `configs/proxy-openrouter.toml`
- Modify: `README.md`
- Modify: `../AXIOM/services/sandbox-service/.env.example`
- Modify: `../AXIOM/services/sandbox-service/deploy/README.md`

- [ ] **Step 1: Keep the user-facing default in TOML**

Document and retain this application configuration in `configs/proxy-openrouter.toml`:

```toml
[method_hub]
enabled = false
endpoint = "http://methods-hub:8000/mcp"
```

`enabled` is the default for omitted request options. `endpoint` is private server configuration and is never serialized to the browser.

- [ ] **Step 2: Document sandbox worker broker configuration**

Add to `../AXIOM/services/sandbox-service/.env.example`:

```dotenv
METHOD_HUB_MCP_URL=http://methods-hub:8000/mcp
METHOD_HUB_TOKEN=
METHOD_HUB_BROKER_ROOT=/var/lib/axiom-sandbox/method-hub-brokers
```

In the sandbox deployment README, state these invariants explicitly:

```text
- The sandbox API accepts only the fixed capability profile `method_hub`.
- The gVisor child always runs with `runsc --network=none`.
- The worker owns the Method Hub URL and token.
- Generated code sees only `/run/axiom/method-hub.sock` and `AXIOM_COMMAND_ID`.
- The worker persists broker audit records on the sandbox command result.
- Missing broker configuration fails sandbox provisioning for enabled requests.
```

- [ ] **Step 3: Document request and capability APIs in the SDK README**

Add these examples to `README.md`:

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

```json
{
  "method_hub": {
    "default_enabled": false,
    "available": true
  }
}
```

Explain that omitted `runtime_options.method_hub_enabled` uses the TOML default, an explicit boolean is persisted for the run, and enabled-but-unavailable requests fail with `method_hub_unavailable` rather than falling back silently.

- [ ] **Step 4: Commit documentation and configuration**

```bash
git add configs/proxy-openrouter.toml README.md
git commit -m "docs: explain method hub runtime mode"

cd ../AXIOM
git add services/sandbox-service/.env.example \
  services/sandbox-service/deploy/README.md
git commit -m "docs(sandbox): explain method hub broker"
```

## Completion Checklist

- [ ] The browser receives only `default_enabled` and `available`; it never receives an MCP endpoint, token, header, or tool credential.
- [ ] Each new response persists an explicit `method_hub_enabled` boolean before the confirmation phase.
- [ ] Revise and confirm reuse the persisted boolean even if server configuration changes.
- [ ] Disabled requests create no MCP client, perform no MCP health/list call, expose no MCP agent tools, and request no sandbox capability profile.
- [ ] Enabled requests discover tools before workflow work begins and return `method_hub_unavailable` on discovery failure.
- [ ] Direct single-tool work uses host-side LangChain MCP tools and records structured method calls.
- [ ] Composite work uses generated Python plus `axiom_method_hub.call_tool()` inside the request sandbox.
- [ ] Sandbox creation accepts only `method_hub`; arbitrary endpoints and arbitrary profile names are rejected.
- [ ] `runsc --network=none` remains present for every sandbox.
- [ ] The worker owns one socket broker per enabled sandbox and removes it on stop, delete, provisioning failure, lost-runtime reconciliation, and expiry.
- [ ] Command audit records come from the broker and are imported into the final engine trace; stdout is not authoritative.
- [ ] Both composer layouts render one shared toggle state and history restores the persisted value.
- [ ] No automated test work is included, per user request.
