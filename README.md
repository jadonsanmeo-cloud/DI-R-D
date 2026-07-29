# Data Intelligence SDK

Repository gồm SDK điều phối multi-agent, FastAPI backend và Next.js frontend để
phân tích dữ liệu, tạo report có cấu trúc, KPI, chart và xuất HTML.

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
  docker-compose.yaml  # API + PostgreSQL stack
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

Các cấu hình local quan trọng đã có sẵn trong `docker/.env.example`:

```env
SANDBOX_TOKEN=<sandbox-service-client-token>
REPORT_FORCE_CODE_AGENT=false
```

Ý nghĩa:

- `SANDBOX_TOKEN`: credential để API gọi AXIOM Sandbox Service qua gateway.
- `REPORT_FORCE_CODE_AGENT=false`: Router ưu tiên method deterministic có sẵn trong
  Method Hub, bao gồm reader `.xls`/`.xlsx`. Chỉ đổi thành `true` khi cần kiểm thử
  riêng nhánh sinh code.
- `DATA_CORPUS_ROOT=.`: file upload được lưu dưới `.uploads/` trong repository.

Đặt `[sandbox].enabled = true` trong `configs/proxy-openrouter.toml` để bật
sandbox. AXIOM Sandbox Service quản lý container, network và resource limits; SDK
chỉ giữ endpoint, workspace ID và client token cần cho luồng QA.

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

Với package mẫu:

```powershell
uv run python examples/create_report.py --package examples/data_corpus_package/data_corpus_package.json --query "Create a report about this data corpus"
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

## PostgreSQL Tùy Chọn

Nếu không đặt `DATABASE_URL`, API dùng in-memory run repository. Cách này đủ để
upload file và test report local, nhưng trạng thái run mất khi restart API.

PostgreSQL phù hợp khi cần giữ trạng thái response lâu hơn. Có thể khởi động riêng
database từ Compose:

```powershell
docker compose -f docker/docker-compose.yaml up -d db
```

Sau đó thêm vào `docker/.env`:

```env
DATABASE_URL=postgresql://data_intelligence:data_intelligence@localhost:5432/data_intelligence
```

## Lưu Ý Về Docker Compose

`docker/docker-compose.yaml` chỉ cung cấp API container và PostgreSQL. Sandbox
runtime thuộc AXIOM Sandbox Service; hãy cấu hình endpoint để API container truy
cập được service đó, ví dụ qua shared Docker network hoặc gateway nội bộ.

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

Kiểm tra `[sandbox].endpoint`, `workspace_id`, `SANDBOX_TOKEN` và network route từ
API tới AXIOM Sandbox Service. Resource limits phải được cấu hình ở Sandbox
Service, không đặt trong SDK/API.

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
- `sandbox`: one isolated AXIOM execution environment per request for generated code. It is not a staged file workspace for engine input data.
- `artifacts`: one persistent filesystem bundle per pipeline invocation, containing every generated-code attempt and execution observation.
- `context`: user and session context placeholders.

## Base Design Notes

- `DataCorpusPackage` describes the available data universe for pipeline/spec-building and compatibility flows. Engines should not depend on direct access to it.
- `DataHubContext` remains available as a compatibility alias for `DataCorpusPackage` during the transition.
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
from data_intelligence_sdk import DataCorpusPackage, UserQuery
from examples.basic_workflow import create_example_pipeline

pipeline = create_example_pipeline()
response = pipeline.run(
    UserQuery("What is the total revenue in this file?"),
    DataCorpusPackage(sources=["sales.csv"]),
)
print(response.answer)
print(response.metadata["artifact_ref"])
```

The high-level request starts from `UserQuery`. The compatibility pipeline may
still accept `DataCorpusPackage` while it prepares a spec, but selected engines
do not receive that package directly; they receive `EngineInput`.

Run the example pipeline from the command line:

```bash
uv run python examples/run_pipeline.py --source sales.csv --query "What is the total revenue?"
uv run python examples/run_pipeline.py --package examples/data_corpus_package/data_corpus_package.json --query "Summarize this package"
```

The `--package` file is compatibility input for the demo pipeline/spec builder.
It describes a data corpus package by reference:

```json
{
  "vectordb": "vectordb",
  "db": "warehouse.db",
  "schema": "schema.json",
  "catalog": "catalog.json"
}
```

Relative paths resolve from the package file directory. The example runner maps `vectordb` and `db` into `DataCorpusPackage.sources`, loads `schema.json` into `DataCorpusPackage.schemas`, and loads `catalog.json` into `DataCorpusPackage.metadata["catalog"]` before engine selection. Engines do not access these fields directly.

The checked-in example at `examples/data_corpus_package/` models `db` as a Postgres-style warehouse reference and `vectordb` as persisted document chunk storage with `chunk_id`, `document_id`, `content`, `embedding`, and `metadata` fields. It includes five raw CSV files under `raw/csv/` and five raw text files under `raw/txt/`.

Mock embeddings are deterministic local vectors. The metadata records `OPENROUTER_EMBEDDING_MODEL`, defaulting to `openai/text-embedding-3-small`, so a real ingestion implementation can swap in OpenRouter embeddings later without changing the package shape.

Start the local mock Postgres + pgvector package with:

```bash
docker compose -f examples/data_corpus_package/docker-compose.yml up -d
```

The seed SQL creates relational `customers`, `orders`, `products`, `support_tickets`, `web_events`, and `documents` tables plus `vectordb.document_chunks` rows with mock text content and embeddings.

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
  --package examples/data_corpus_package/data_corpus_package.json \
  --config configs/proxy-openrouter.toml \
  --env-file docker/.env \
  --query "Summarize this data corpus package"
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
token = "${env:SANDBOX_TOKEN}"
```

The CLI example discovers the sibling client source directly for local
development. Production applications should install `axiom-sandbox-client`.
The general engine requires `[sandbox].enabled = true` and a workspace ID.
AXIOM Sandbox Service owns sandbox lifecycle, isolation, and resource limits;
the SDK/API only supplies the endpoint and client credentials.

The API defaults `REPORT_FORCE_CODE_AGENT` to `false` so deterministic Method Hub
tools are preferred when their contracts match. Set it to `true` only to exercise
the generated-code route even when a matching tool exists.

### Per-request Method Hub mode

The frontend reads `GET /api/v1/runtime-capabilities` and sends an explicit
Method Hub choice with each Responses request:

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
`configs/proxy-openrouter.toml` supplies the default. The API persists the
resolved boolean before spec confirmation so revise and confirm use the same
mode. Enabled requests discover the MCP catalog immediately; an unavailable
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
same Markdown preparation path as the Responses API and stops before confirmation
and engine execution. It does not require local source files or a corpus package.

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
dashboard. Interactive Responses queries use a separate Markdown path.

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

## FastAPI Responses Backend

The repository includes a FastAPI consuming application that runs the example
Data Intelligence workflow and streams lifecycle and final-output events.

Configure the workflow and API:

```text
OPENROUTER_API_KEY=...
DATA_CORPUS_ROOT=/absolute/path/to/Data-Intelligence-SDK
DATABASE_URL=postgresql://data_intelligence:data_intelligence@127.0.0.1:5432/data_intelligence
MODEL_CONFIG_PATH=/absolute/path/to/Data-Intelligence-SDK/configs/proxy-openrouter.toml
```

CORS, timeouts, upload limits, chat model, provider endpoints, Sandbox, and
Method Hub settings are configured in `configs/proxy-openrouter.toml`.

Start the data_intelligence_api:

```bash
uv run uvicorn data_intelligence_api.main:app --reload --host 127.0.0.1 --port 8000
```

Check health:

```bash
curl http://127.0.0.1:8000/health
```

Submit a general or data-dependent response request:

```bash
curl --no-buffer \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "What is the total revenue?",
    "session_id": "demo"
  }' \
  http://127.0.0.1:8000/api/v1/responses
```

An LLM orchestrator handles the request before the data workflow. It has access
only to general model knowledge and conversation context; it cannot access
Method Hub, MCP, Corpus Service, private documents, or a sandbox.

For general questions it streams `response.output_text.delta` followed by
`response.completed`. This path creates no pending confirmation record.

When the model needs user- or organization-specific data, it calls the native
`delegate_to_data_flow` tool. The original query and context then enter the
existing intent and Markdown preparation flow, which ends with
`response.requires_confirmation`. That event includes a `response_id`,
`confirmation_token`, revision, intent metadata, and `spec_markdown`. No engine
runs before confirmation.

The orchestrator reuses `OPENAI_COMPATIBLE_*` settings. When they are absent,
`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, and `LLM_MODEL_NAME` are accepted as
fallbacks.

Revise the pending spec (repeat as needed):

```bash
curl -N http://127.0.0.1:8000/api/v1/responses/RESP_ID/decision \
  -H 'Content-Type: application/json' \
  -H 'X-Confirmation-Token: TOKEN' \
  -d '{"action":"revise","revision":1,"spec_markdown":"# Interactive Execution Spec\n\n## User Request\n\nWhat is the total revenue?\n\n## Intent\n\nReport.\n\n## Preparation Guidance\n\nFollow intent metadata.\n\n## Execution Instructions\n\nRetrieve every relevant ingested document.\n\n## Expected Output\n\nA cited Markdown report."}'
```

Confirm and stream the answer/report:

```bash
curl -N http://127.0.0.1:8000/api/v1/responses/RESP_ID/decision \
  -H 'Content-Type: application/json' \
  -H 'X-Confirmation-Token: TOKEN' \
  -d '{"action":"confirm","revision":2}'
```

Recover a paused response with `GET /api/v1/responses/RESP_ID` and the same
`X-Confirmation-Token` header.

### Backend Docker Service

The Compose configuration at `docker/docker-compose.yaml` runs the FastAPI API
and a private PostgreSQL 17 service. The API defaults to
`http://127.0.0.1:8036`; database and persistent application data remain in
named Docker volumes.

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
Sandbox, Method Hub, CORS, timeouts, or upload limits. Compose bind-mounts this
file at runtime, so configuration changes require only
`docker compose -f docker/docker-compose.yaml restart api`, not an image rebuild.
PostgreSQL stays private inside Docker, while the DI API is available at
`http://SERVER_IP:8036`. API, upload, artifact, and PostgreSQL data are
stored in named Docker volumes.

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

Run a streaming response from the ingested organization corpus:

```bash
curl -N http://127.0.0.1:8036/api/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "Analyze this dataset",
    "session_id": "docker-test"
  }'
```

Stop the stack while retaining database and API volumes:

```bash
docker compose -f docker/docker-compose.yaml down
```
