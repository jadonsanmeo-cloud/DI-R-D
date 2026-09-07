# Data Intelligence SDK

Repository gồm SDK điều phối multi-agent, FastAPI backend và Next.js frontend để
phân tích dữ liệu, tạo report có cấu trúc, KPI, chart và xuất HTML.

## Stateless GenReport Boundary

For AXIOM report execution, Data Intelligence is a stateless streaming adapter.
It receives AXIOM-owned history and execution capabilities, sends exactly one
internal request to `POST /api/v1/reports:stream`, and maps GenReport SSE
events to `runtime.*` events. It does not create GenReport conversations, upload
files to GenReport, or persist report usage/artifacts. AXIOM owns those records.

Configure the internal GenReport endpoint:

```env
GEN_REPORT_API_URL=http://host.docker.internal:8011
```

GenReport has no application-level service token and must remain behind a trusted
internal network boundary.

- Tài liệu luồng Report Engine hiện tại:
  [`docs/report-engine-v2-current-flow-vi.md`](docs/report-engine-v2-current-flow-vi.md)
- Kế hoạch thiết kế V2:
  [`docs/report-engine-v2-implementation-plan.md`](docs/report-engine-v2-implementation-plan.md)
- Kiến trúc API:
  [`docs/api-architecture.md`](docs/api-architecture.md)

## Repository Layout

```text
packages/
  sdk/                 # Report Engine, agents, MCP client, sandbox contracts
  api/                 # FastAPI application
web/                   # Next.js frontend
examples/              # CLI examples and sample corpus packages
data/samples/          # small checked-in fixtures
configs/               # model configuration
docs/                  # architecture and flow documents
docker/
  Dockerfile           # API image
  docker-compose.yaml  # stateless API adapter
```

SDK và API là hai Python workspace package riêng. API chịu trách nhiệm ghép model,
artifact store và sandbox provider; SDK không import API hoặc frontend.

## Quickstart End-to-End

Đây là cách setup local được khuyến nghị:

```text
Browser :3000
    |
    v
Next.js frontend
    |
    v
FastAPI :8000 (chạy trên host)
    |
    +--> OpenRouter
    |
    +--> Docker container sandbox theo từng request
    |
    +--> artifacts/<run-id>/
```

API nên chạy trên host để có thể gọi Docker Desktop/Engine và tạo sandbox
container. PostgreSQL không bắt buộc cho luồng upload file và tạo report cơ bản.

### 1. Yêu cầu hệ thống

Cài các công cụ sau:

- Git.
- Python 3.11.
- [uv](https://docs.astral.sh/uv/).
- Docker Desktop trên Windows/macOS hoặc Docker Engine trên Linux.
- Node.js 18 trở lên và npm 8 trở lên.
- Một OpenRouter API key và model ID mà tài khoản được phép sử dụng.

Kiểm tra:

```powershell
git --version
python --version
uv --version
docker version
node --version
npm --version
```

Trên Windows, cần mở Docker Desktop và chờ Docker Engine ở trạng thái Running.

### 2. Clone repository

```powershell
git clone <private-repository-url>
cd Data-Intelligence-SDK
```

Vì repository là private, tài khoản GitHub của người cài phải được cấp quyền.
Có thể clone bằng HTTPS với credential manager hoặc bằng SSH.

### 3. Tạo cấu hình môi trường

PowerShell:

```powershell
Copy-Item docker/.env.example docker/.env
```

Bash:

```bash
cp docker/.env.example docker/.env
```

Mở `docker/.env` và điền ít nhất:

```env
OPENROUTER_API_KEY=<your-openrouter-api-key>
MODEL_CONFIG_PATH=configs/proxy-openrouter.toml
DATA_CORPUS_ROOT=.
```

Model IDs, provider endpoints, CORS, timeout và upload limits được cấu hình trong
`configs/proxy-openrouter.toml`, không lặp lại trong `.env`.

- `DATA_CORPUS_ROOT=.`: file upload được lưu dưới `.uploads/` trong repository.

Đặt `[sandbox].enabled = true` trong `configs/proxy-openrouter.toml` để bật
sandbox. AXIOM Sandbox Service quản lý container, network và resource limits; SDK
chỉ giữ endpoint và workspace ID cần cho luồng QA.

Không commit `docker/.env`. File `docker/.env.example` chỉ chứa tên biến và được
phép commit.

### 4. Cài Python workspace

Tại thư mục gốc repository:

```powershell
uv venv --python 3.11
uv pip install -e packages/sdk -e packages/api
```

Lệnh trên cài SDK và API ở editable mode vào `.venv`. Không cần activate virtual
environment khi dùng tiền tố `uv run`.

Kiểm tra import:

```powershell
uv run python -c "import data_intelligence_sdk, data_intelligence_api; print('Python packages: OK')"
```

### 5. Kiểm tra AXIOM Sandbox Service

Khởi động Sandbox Service từ AXIOM platform và bảo đảm endpoint trong
`configs/proxy-openrouter.toml` truy cập được từ API. Mỗi QA request sẽ yêu cầu
service tạo một request-scoped sandbox, chạy generated code và dọn runtime.

Image này chỉ tồn tại trên máy đã build. Người khác pull source từ GitHub vẫn phải
chạy lại lệnh `docker build`.

### 6. Chạy backend API

Mở terminal thứ nhất tại thư mục gốc:

```powershell
$env:MODEL_CONFIG_PATH="configs/proxy-openrouter.host.toml"
$env:DATA_CORPUS_ROOT=(Get-Location).Path
.\.venv\Scripts\python.exe -m uvicorn data_intelligence_api.main:app --reload --host 127.0.0.1 --port 8036 --env-file docker/.env
```

Kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8036/health
```

Kết quả mong đợi:

```text
status
------
ok
```

OpenAPI UI có tại `http://127.0.0.1:8036/docs`.

### 7. Cài và chạy frontend

Mở terminal thứ hai:

```powershell
cd web
Copy-Item .env.example .env.local
npm.cmd ci
npm.cmd run dev
```

`web/.env.local` cần chứa:

```env
API_BASE_URL=http://127.0.0.1:8036
```

Mở `http://localhost:3000`, upload một file và gửi yêu cầu, ví dụ:

```text
Create a report about this data file.
```

Luồng report chạy bất đồng bộ. Frontend sẽ poll response, sau đó hiển thị HTML
report khi pipeline hoàn tất.

### Chạy bằng F5 trong VS Code

Mở đúng thư mục `Data-Intelligence-SDK` bằng VS Code, chọn cấu hình
`Data Intelligence: API + Web` rồi nhấn F5.

Cấu hình trong `.vscode/`:

- dùng Python 3.11 tại `.venv`;
- dùng `configs/proxy-openrouter.host.toml` để gọi AXIOM services qua
  `localhost:8000`, `localhost:8004` và `localhost:8005`;
- chạy hoặc tái sử dụng Next.js tại `http://127.0.0.1:3000`;
- chạy hoặc tái sử dụng API tại `http://127.0.0.1:8036`;
- ghi artifact vào `artifacts/<ddmmyyyy-hhmm>-<uuid>/`.

Nếu API Docker đang healthy trên port `8036`, F5 sẽ tái sử dụng API đó. Muốn
debug breakpoint Python trên host, dừng API container trước rồi nhấn F5; launcher
sẽ tự khởi động Uvicorn dưới debugger.

### 8. Chạy CLI không cần frontend

Với một file local:

```powershell
uv run python examples/create_report.py --source "C:\path\to\data.pdf" --query "Create a report about this data file"
```

CLI và API đọc secret cùng các cờ process-local từ `docker/.env`; cấu hình model vẫn lấy
từ `configs/proxy-openrouter.toml`.

## Kiểm Tra Sau Setup

Chạy unit test chính:

```powershell
uv run python -m unittest discover -s packages/sdk/tests
uv run python -m unittest discover -s packages/api/tests
```

Kiểm tra frontend:

```powershell
cd web
npm run build
```

Một lần test end-to-end tối thiểu nên xác nhận:

- API `/health` trả `ok`.
- AXIOM Sandbox Service trả trạng thái ready.
- Upload trả về source reference.
- Response chuyển từ trạng thái đang chạy sang hoàn tất.
- Report có narrative, evidence, KPI phù hợp và chart chỉ xuất hiện khi đủ dữ liệu.
- File HTML mở được và chart tải được từ ECharts CDN.

## Artifact Và Debug

Mỗi run tạo bundle:

```text
artifacts/<run-id>/
  manifest.json
  events.jsonl
  code/
  executions/
  data/
  rendered/
    report.md
    report.css
    report.js
    report.html
```

Khi UI báo `The data intelligence workflow could not complete`, kiểm tra theo thứ
tự:

1. Terminal API để lấy exception và response ID.
2. `artifacts/<run-id>/events.jsonl` để xem node nào lỗi.
3. `code/` để xem code do Code Agent sinh.
4. `executions/` để xem stdout, stderr, validation và sandbox result.
5. Docker Desktop để chắc Docker Engine đang chạy.
6. `docker/.env` để chắc API key và sandbox backend đúng; kiểm tra model ID trong
   `configs/proxy-openrouter.toml`.

`artifacts/`, `.uploads/`, logs, `docker/.env`, `.venv`, `.next` và `node_modules` là dữ
liệu local, đã được ignore và không nên push lên GitHub.

## Lưu Ý Về Docker Compose

`docker/docker-compose.yaml` chỉ cung cấp stateless API container. Response state
và confirmation thuộc AXIOM. Sandbox runtime thuộc AXIOM Sandbox Service; hãy cấu
hình endpoint để API container truy cập được service đó qua shared Docker network
hoặc gateway nội bộ.

## Troubleshooting

### `fatal: not a git repository`

Terminal đang đứng sai thư mục. Chạy:

```powershell
cd G:\DI_RandD\Data-Int-SDK\Data-Intelligence-SDK
git status
```

### API không import được package

Chạy lại từ repository root:

```powershell
uv pip install -e packages/sdk -e packages/api
```

### Không kết nối được Sandbox Service

Kiểm tra `[sandbox].endpoint`, `workspace_id` và network route từ API tới AXIOM
Sandbox Service. Resource limits phải được cấu hình ở Sandbox Service, không đặt
trong SDK/API.

### LLM trả 401, 403 hoặc model not found

Kiểm tra `OPENROUTER_API_KEY` trong `docker/.env` và model ID trong
`configs/proxy-openrouter.toml`. Model ID phải đúng với model mà tài khoản
OpenRouter được phép gọi.

### HTML có chart container nhưng chart trống

Renderer dùng ECharts CDN. Trình duyệt phải truy cập được CDN. Đồng thời kiểm tra
`report.js` và `chart_requests` trong artifact để phân biệt lỗi tải thư viện với
trường hợp ChartInputAssembler đánh dấu `insufficient_data`.

## Architecture Reference

Base Python package for a data intelligence orchestration system.

The current goal is to capture the architecture boundaries from
`ai-sdk-platform-architecture.svg`, not to provide production-ready SDK
implementations.

## Architecture Flow

```text
User Query
  -> Intent Analyzer
  -> Spec Builder
  -> Engine Registry (LLM-based selection)
  -> EngineInput(query, spec, runtime, user_context)
  -> selected Engine
       -> execute_python(code)
            -> persist code artifact
            -> sandbox.run(runtime runner)
            -> persist execution artifact
       -> observe and correct when needed
  -> FinalResponse(answer, artifact_ref, evidence=None)
```

Supporting layers:

- `runtime`: request orchestration, `EngineRuntimeContext`, logging, MCP access, artifacts, and sandbox execution.
- `sandbox`: one isolated AXIOM execution environment per request for generated code and staged uploaded files.
- `artifacts`: one persistent filesystem bundle per pipeline invocation, containing every generated-code attempt and execution observation.
- `context`: user and session context placeholders.

## Base Design Notes

- `UploadedFile` describes each user-uploaded file by filename. The API stages matching uploaded files into the request sandbox under that filename.
- `Intent` is a controlled string selected from `SUPPORTED_INTENTS`: `reason` for data questions, `report` for report generation, `general` for general-purpose queries handled by `GeneralPurposeEngine`, and `unknown` for classifier failures or legacy payloads. The spec carries the richer objective, constraints, and capability requirements.
- `ExecutionSpec.capability_requirements` describes what the selected engine/runtime must resolve.
- Engines receive `EngineInput(query, spec, runtime, user_context)`. `runtime` owns the request-scoped `EngineRunContext`, sandbox session, MCP/tools, logs, and artifacts.
- `GeneralPurposeEngine` contains one Deep Agent with request-scoped runtime tools and optional Method Hub MCP capabilities.
- With Method Hub disabled, the agent uses `execute_python(code)`. With it enabled, simple operations use direct MCP tools while composed operations use `execute_python(code)` and the sandbox broker helper.
- `EngineOutput` contains raw engine output plus `EngineTrace`.
- `EvidenceBundle` uses engine trace, method calls, interface definitions, sandbox results, observations, artifact refs, and log refs for audit and final response generation.
- `SessionContext` is separate from `UserContext`: session context is short-lived conversation/task state, while user context is longer-lived preference and history.

## Base Query-to-Answer Workflow

The SDK exposes the runtime contracts while the application-owned factory wires OpenRouter, filesystem artifacts, the AXIOM Method Hub MCP server, and AXIOM sandbox-service. `GeneralPurposeEngine` receives only `EngineInput`, then uses `deepagents==0.6.12` to select direct MCP tools or generate, execute, observe, and correct sandboxed Python analysis. The SDK contains no local Method Hub registry or concrete Method Hub implementations.

```python
from data_intelligence_sdk import UserQuery
from examples.basic_workflow import create_example_pipeline

pipeline = create_example_pipeline()
response = pipeline.run(
    UserQuery("What is the total revenue in this file?"),
)
print(response.answer)
print(response.metadata["artifact_ref"])
```

The high-level request starts from `UserQuery`. Uploaded filenames are carried
in `UserQuery.metadata["uploaded_files"]`; selected engines receive
`EngineInput`.

Run the example pipeline from the command line:

```bash
uv run python examples/run_pipeline.py --source sales.csv --query "What is the total revenue?"
```

Configure OpenRouter with:

```text
OPENROUTER_API_KEY
```

Model names and provider endpoints are configured in
`configs/proxy-openrouter.toml`.

### Runtime workflow CLI

Use the demo to run the complete non-interactive query-to-answer flow:

```bash
uv run python examples/demo_workflow_cli.py \
  --config configs/proxy-openrouter.toml \
  --env-file docker/.env \
  --query "Summarize these uploaded files"
```

The CLI builds the spec, selects the engine, provisions one request-scoped
sandbox for generated-code execution, runs the Deep Agent, and prints the
answer. Add `--verbose` to enable AXIOM debug logs and print response metadata:

```bash
uv run python examples/demo_workflow_cli.py \
  --query "Explain the main findings" \
  --verbose
```

Provider settings normally come from `configs/proxy-openrouter.toml`. The CLI
loads `docker/.env` by default so secret `${env:...}` placeholders work in
local runs. Use `--env-file PATH` to load a different env file; values already
exported in the shell take precedence.
Provider settings can also be overridden with `--config`, `--model`,
`--api-key`, and `--base-url`. Structured pipeline events are written to
`logs/pipeline.log` by default; pass `--no-trace` to disable that file.

When the AXIOM sandbox is enabled, the application-owned pipeline factory
creates one sandbox for the entire request. Configure it in
`configs/proxy-openrouter.toml`:

```toml
[sandbox]
enabled = true
endpoint = "http://host.docker.internal:8004"
workspace_id = "00000000-0000-0000-0000-000000000001"
```

The CLI example discovers the sibling client source directly for local
development. Production applications should install `axiom-sandbox-client`.
The general engine requires `[sandbox].enabled = true` and a workspace ID.
AXIOM Sandbox Service owns sandbox lifecycle, isolation, and resource limits;
the SDK/API only supplies the endpoint and workspace ID.

### Per-request Method Hub mode

The frontend reads `GET /api/v1/runtime-capabilities` and sends an explicit
Method Hub choice with each Responses request:

```json
{
  "input": "Scan the files and normalize the result",
  "uploaded_files": [{"filename": "sales.csv"}],
  "runtime_options": {
    "method_hub_enabled": true
  }
}
```

The capability response exposes only public state:

```json
{
  "method_hub": {
    "default_enabled": false,
    "available": true
  }
}
```

If `runtime_options.method_hub_enabled` is omitted, `[method_hub].enabled` in
`configs/proxy-openrouter.toml` supplies the default. AXIOM persists the prepared
request before confirmation, so revise and execute receive the same resolved
input. Enabled requests discover the MCP catalog immediately; an unavailable
server returns `method_hub_unavailable` instead of silently falling back.

Simple operations are exposed to engines as direct MCP tools. Composite work
runs generated Python in the request sandbox and calls Method Hub through the
worker-owned `/run/axiom/method-hub.sock` broker. The gVisor child keeps network
access disabled and never receives the MCP endpoint or credentials.

Each invocation creates a persistent artifact bundle under `artifacts/<run-id>/`
by default. Configure a different root with `[artifacts].root` in the TOML file.
The bundle retains
`code/attempt-NNN.py`, `executions/attempt-NNN.json`, and `manifest.json` after
the sandbox has been deleted.

`--config` accepts either a repository-relative path or an absolute path:

```bash
uv run python examples/demo_workflow_cli.py \
  --config /absolute/path/to/proxy-openrouter.toml \
  --env-file /absolute/path/to/.env \
  --query "Summarize this data corpus package"
```

Example pipeline factories live in `examples.basic_workflow` as app-owned wiring outside the SDK package.

Tests use fake engines or fake LLMs and do not call OpenRouter.

### Prepare an execution spec only

Use the preparation CLI when debugging intent resolution and spec generation.
It validates the corpus sources, calls AXIOM Intent Service, builds the structured
`ExecutionSpec`, and writes a deterministic Markdown view. It stops before
confirmation, engine selection, sandbox provisioning, and execution.

Prerequisites:

- export `OPENROUTER_API_KEY` for the spec-builder model;
- run AXIOM Intent Service on `http://localhost:8005` (the AXIOM development
  compose maps `8005:8005`);
- run the command from the `Data-Intelligence-SDK` repository root.

PowerShell example using the checked-in NAPH corpus:

```powershell
uv run python scripts/prepare_spec.py `
  --query "Summarize the available NAPH reports" `
  --output .data/debug-spec/execution-spec.md `
  --config configs/development/proxy-openrouter.toml `
  --intent-service-url http://localhost:8005 `
  --verbose
```

The output is written to `.data/debug-spec/execution-spec.md`. The CLI calls the
same Markdown preparation path as `POST /v1/specs:prepare` and stops before AXIOM
confirmation and engine execution. It does not require local source files or a
corpus package.

Useful lifecycle events include:

- `pipeline.intent_analysis.started` / `pipeline.intent_analyzed`;
- `spec.context_built`;
- `spec.llm_attempt.started` / `spec.llm_attempt.completed`;
- `spec.validation_retry` with only the safe exception type;
- `spec_preparation.completed` and `markdown_write.completed`.

The CLI returns a non-zero exit code and logs `<phase>.failed` on invalid source,
intent-service, model, validation, or output-write failures. Logs redact fields
whose names contain `api_key`, `password`, `secret`, or `token`.

### Scheduled dashboard-report spec worker

The scheduled spec worker is separate from the interactive query flow. Every
cycle it reads the three newest indexed Corpus documents for one organization,
using Corpus PostgreSQL `documents.created_at` as a temporary `ingested_at`
value. It creates one direct Markdown prompt per seed document and skips an
existing `<document-id>.md` file.

This worker only creates specs. It does not call Intent Service, confirm a spec,
run an engine, retrieve related documents, generate a report, or update the
dashboard. Interactive AXIOM requests use the stateless runtime operation path.

Run one cycle from Windows CMD:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK"
set CORPUS_DATABASE_URL=postgresql+psycopg://app_dev:app_dev_password@localhost:30433/corpus
set CORPUS_ORGANIZATION_ID=test-org
uv run python scripts\recent_spec_worker.py --once --verbose
```

Run continuously with the default 15-minute interval:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK"
set CORPUS_DATABASE_URL=postgresql+psycopg://app_dev:app_dev_password@localhost:30433/corpus
set CORPUS_ORGANIZATION_ID=test-org
uv run python scripts\recent_spec_worker.py --verbose
```

Default configuration:

```env
RECENT_SPEC_WORKER_INTERVAL_SECONDS=900
RECENT_SPEC_WORKER_LIMIT=3
RECENT_SPEC_OUTPUT_DIR=.data/scheduled-report-specs
```

Generated files are stored under `.data\scheduled-report-specs`. Use
`--output-dir`, `--limit`, or `--interval-seconds` to override the defaults, and
use `--once` when debugging or invoking the worker from Windows Task Scheduler.

### Optional LangSmith tracing

LangChain/LangGraph runs and the custom OpenAI-compatible spec-builder client
can be traced to LangSmith. Tracing is disabled unless explicitly enabled:

```text
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=data-intelligence-sdk
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

These variables can be placed in `.env` for local development. The SDK
does not send traces when `LANGCHAIN_TRACING_V2` is unset or false.

## Stateless Runtime API

The FastAPI application executes self-contained operations supplied by AXIOM.
It does not own response IDs, confirmation tokens, revisions, history, or a run
database.

Configure the runtime:

```text
OPENROUTER_API_KEY=...
DATA_CORPUS_ROOT=/absolute/path/to/Data-Intelligence-SDK
MODEL_CONFIG_PATH=/absolute/path/to/Data-Intelligence-SDK/configs/proxy-openrouter.toml
RUNTIME_SERVICE_TOKEN=shared-service-token
RUNTIME_CONSUMER_SERVICE=intelligence-service
```

Start the API and check process liveness:

```bash
uv run uvicorn data_intelligence_api.main:app --reload --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

AXIOM calls four authenticated operations:

- `POST /v1/specs:prepare` builds a Markdown spec and serializable prepared input.
- `POST /v1/specs:revise` validates a replacement Markdown spec.
- `POST /v1/execution:instant` selects (when the engine is `auto`) and runs an
  engine without producing a user-visible specification.
- `POST /v1/execution:thinking` selects and runs an engine after the caller has
  confirmed or revised the prepared Markdown specification.

Each payload carries `schema_version`, `operation_id`, `attempt`, `response_id`,
and a complete `runtime_input`. The public engines are `general`, `reason`, and
`report`; `auto` is resolved once by the SDK's LLM-backed Engine Selector. The
execution streams one `runtime.engine.selected` event before engine output.
When the selected engine is `report`, both Instant and Thinking forward its live
progress, output, usage, and completion SSE events. The runtime never accepts or
returns a confirmation token. The legacy
`/api/v1/responses` paths are intentionally absent and return `404`.

### Backend Docker Service

The Compose configuration at `docker/docker-compose.yaml` runs only the
stateless FastAPI API. It defaults to `http://127.0.0.1:8036`; corpus data and
runtime artifacts remain mounted, while response lifecycle state stays in AXIOM.

The Docker build expects the AXIOM platform and Data Intelligence SDK to be
sibling checkouts:

```text
AXIOM/
  AXIOM/
    packages/axiom-sandbox-client/
  Data-Intelligence-SDK/
    docker/
```

Copy the environment template and fill the secrets:

```bash
cp docker/.env.example docker/.env
```

Edit `configs/proxy-openrouter.toml` to change models, provider endpoints,
Sandbox, Method Hub, or CORS. Compose bind-mounts this file at runtime, so
configuration changes require only
`docker compose -f docker/docker-compose.yaml restart api`, not an image rebuild.
The DI API is available at `http://SERVER_IP:8036`.

Validate and start the stack:

```bash
docker compose -f docker/docker-compose.yaml config --quiet
docker compose -f docker/docker-compose.yaml up --build -d
docker compose -f docker/docker-compose.yaml ps
curl http://127.0.0.1:8036/health
```

Inspect API logs:

```bash
docker compose -f docker/docker-compose.yaml logs -f api
```

Prepare a stateless execution spec:

```bash
curl http://127.0.0.1:8036/v1/specs:prepare \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_RUNTIME_SERVICE_TOKEN' \
  -H 'X-Consumer-Service: intelligence-service' \
  -d '{
    "schema_version": "1",
    "operation_id": "op_prepare_demo",
    "attempt": 1,
    "response_id": "resp_demo",
    "runtime_input": {
      "input": "Analyze this dataset",
      "session_id": "docker-test",
      "runtime_options": {"engine": "report"}
    }
  }'
```

Stop the stack while retaining corpus data:

```bash
docker compose -f docker/docker-compose.yaml down
```

### Organization MethodHub tool bindings

Agent runtime construction uses `MCPMethodClient.list_agent_tools()`, which filters
MethodHub's full catalog against organization registrations. Raw `list_tools()` and
`call_tool()` remain unchanged. Configure `TOOL_SUBSCRIPTIONS_API_URL` to the gateway
GET endpoint `/authz-service/api/v1/authz/me/tool-subscriptions` (default origin
`http://host.docker.internal:8007`). The user bearer and organization must match.
No registrations means no MethodHub tools bound to the agent. Subscription service
errors stop construction instead of falling back to all tools. Changes apply to
new agents; the current agent retains its snapshot.
