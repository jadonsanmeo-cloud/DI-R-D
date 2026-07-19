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
  sdk/                 # Report Engine, agent, Method Hub, sandbox contracts
  api/                 # FastAPI application
web/                   # Next.js frontend
examples/              # CLI examples and sample corpus packages
data/samples/          # small checked-in fixtures
configs/               # model configuration
docs/                  # architecture and flow documents
docker/
  sandbox.Dockerfile   # isolated runtime for generated Python code
  Dockerfile           # API image
  docker-compose.yaml  # API + PostgreSQL stack (see note below)
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
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

Mở `.env` và điền ít nhất:

```env
OPENROUTER_API_KEY=<your-openrouter-api-key>
LLM_MODEL_NAME=<openrouter-llm-model-id>
EMBEDDING_MODEL_NAME=<openrouter-embedding-model-id>
MODEL_CONFIG_PATH=configs/proxy-openrouter.toml
```

Các cấu hình local quan trọng đã có sẵn trong `.env.example`:

```env
SANDBOX_ENABLED=true
SANDBOX_BACKEND=docker
SANDBOX_DOCKER_IMAGE=data-intelligence-sandbox:local
REPORT_FORCE_CODE_AGENT=false
```

Ý nghĩa:

- `SANDBOX_BACKEND=docker`: generated Python code chạy trong container cô lập.
- `REPORT_FORCE_CODE_AGENT=false`: Router ưu tiên method deterministic có sẵn trong
  Method Hub, bao gồm reader `.xls`/`.xlsx`. Chỉ đổi thành `true` khi cần kiểm thử
  riêng nhánh sinh code.
- `DATA_CORPUS_ROOT=.`: file upload được lưu dưới `.uploads/` trong repository.
- `PIPELINE_TIMEOUT_SECONDS=300`: timeout tối đa của một pipeline request.

Không commit `.env`. File `.env.example` chỉ chứa tên biến và được phép commit.

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

### 5. Build Docker sandbox

```powershell
docker build -f docker/sandbox.Dockerfile -t data-intelligence-sandbox:local .
docker image inspect data-intelligence-sandbox:local
```

Mỗi report request dùng nhánh Code Agent sẽ:

1. Tạo container từ image trên.
2. Mount workspace tạm và stage file input.
3. Tắt network trong sandbox.
4. Áp giới hạn CPU, RAM và process.
5. Chạy generated function.
6. Thu kết quả và xóa container.

Image này chỉ tồn tại trên máy đã build. Người khác pull source từ GitHub vẫn phải
chạy lại lệnh `docker build`.

### 6. Chạy backend API

Mở terminal thứ nhất tại thư mục gốc:

```powershell
uv run uvicorn data_intelligence_api.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

Kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Kết quả mong đợi:

```text
status
------
ok
```

OpenAPI UI có tại `http://127.0.0.1:8000/docs`.

### 7. Cài và chạy frontend

Mở terminal thứ hai:

```powershell
cd web
Copy-Item .env.example .env.local
npm ci
npm run dev
```

`web/.env.local` cần chứa:

```env
API_BASE_URL=http://127.0.0.1:8000
```

Mở `http://localhost:3000`, upload một file và gửi yêu cầu, ví dụ:

```text
Create a report about this data file.
```

Luồng report chạy bất đồng bộ. Frontend sẽ poll response, sau đó hiển thị HTML
report khi pipeline hoàn tất.

### 8. Chạy CLI không cần frontend

Với một file local:

```powershell
uv run python examples/create_report.py --source "C:\path\to\data.pdf" --query "Create a report about this data file"
```

Với package mẫu:

```powershell
uv run python examples/create_report.py --package examples/data_corpus_package/data_corpus_package.json --query "Create a report about this data corpus"
```

CLI và API đều đọc biến từ `.env`.

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
- Docker image `data-intelligence-sandbox:local` tồn tại.
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
6. `.env` để chắc API key, model ID và sandbox backend đúng.

`artifacts/`, `.uploads/`, logs, `.env`, `.venv`, `.next` và `node_modules` là dữ
liệu local, đã được ignore và không nên push lên GitHub.

## PostgreSQL Tùy Chọn

Nếu không đặt `DATABASE_URL`, API dùng in-memory run repository. Cách này đủ để
upload file và test report local, nhưng trạng thái run mất khi restart API.

PostgreSQL phù hợp khi cần giữ trạng thái response lâu hơn. Có thể khởi động riêng
database từ Compose:

```powershell
docker compose -f docker/docker-compose.yaml up -d db
```

Sau đó thêm vào `.env`:

```env
DATABASE_URL=postgresql://data_intelligence:data_intelligence@localhost:5432/data_intelligence
```

## Lưu Ý Về Docker Compose

`docker/docker-compose.yaml` cung cấp API container và PostgreSQL cho môi trường
container hóa. Tuy nhiên API container hiện chưa được mount Docker socket và chưa
có Docker CLI để tạo request-scoped Docker sandbox trên host.

Vì vậy, với `SANDBOX_BACKEND=docker`, hãy dùng quickstart ở trên: chạy API trên
host và chỉ chạy sandbox trong Docker. Không dùng toàn bộ Compose stack cho nhánh
Code Agent cho tới khi deployment có một sandbox service riêng hoặc cấu hình Docker
socket với policy bảo mật phù hợp.

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

### Docker daemon không kết nối được

Mở Docker Desktop, chờ Engine chạy rồi kiểm tra:

```powershell
docker version
docker ps
```

### Không tìm thấy sandbox image

```powershell
docker build -f docker/sandbox.Dockerfile -t data-intelligence-sandbox:local .
```

### LLM trả 401, 403 hoặc model not found

Kiểm tra `OPENROUTER_API_KEY`, `LLM_MODEL_NAME` và
`EMBEDDING_MODEL_NAME` trong `.env`. Model ID phải đúng với model mà tài khoản
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
User Query + DataCorpusPackage
  -> Intent Analyzer
  -> Spec Builder
  -> Engine Registry
  -> GeneralPurposeEngine
  -> one request-scoped AXIOM sandbox
  -> one Deep Agent
       -> execute_python(code)
            -> persist code artifact
            -> sandbox.run(runtime runner)
            -> persist execution artifact
       -> observe and correct when needed
  -> FinalResponse(answer, artifact_ref, evidence=None)
```

Supporting layers:

- `runtime`: request orchestration, engine runtime context, logging, and the Deep Agents sandbox backend.
- `sandbox`: one isolated AXIOM environment per request with staged input data and direct source execution.
- `artifacts`: one persistent filesystem bundle per pipeline invocation, containing every generated-code attempt and execution observation.
- `context`: user and session context placeholders.
- `docs/method_hub.md`: Method Hub contract, manifest schema, catalog generation, and proposal/export workflow.

## Base Design Notes

- `DataCorpusPackage` describes the available data universe for a task. It may contain source refs, schemas, semantic metadata, and policy metadata; it does not necessarily contain raw data.
- `DataHubContext` remains available as a compatibility alias for `DataCorpusPackage` during the transition.
- `Intent` is a controlled string selected from `SUPPORTED_INTENTS`: `reason` for data questions, `report` for report generation, `general` for general-purpose queries handled by `GeneralPurposeEngine`, and `unknown` for classifier failures or legacy payloads. The spec carries the richer objective, constraints, and capability requirements.
- `ExecutionSpec.capability_requirements` describes what the selected engine/runtime must resolve.
- Engines receive an `EngineRuntimeContext`, which owns an `EngineRunContext` and the request-scoped sandbox session.
- `GeneralPurposeEngine` contains one Deep Agent. Method Hub, MCP, generated interface registration, and evidence synthesis are outside this first runtime flow.
- The agent sees exactly one custom `execute_python(code)` tool. The runtime stores the source before submitting it to the sandbox.
- `EngineOutput` contains raw engine output plus `EngineTrace`.
- `EvidenceBundle` uses engine trace, method calls, interface definitions, sandbox results, observations, artifact refs, and log refs for audit and final response generation.
- `SessionContext` is separate from `UserContext`: session context is short-lived conversation/task state, while user context is longer-lived preference and history.

## Base Query-to-Answer Workflow

The SDK exposes the runtime contracts while the application-owned factory wires OpenRouter, filesystem artifacts, and AXIOM sandbox-service. `GeneralPurposeEngine` is the selected analysis engine and uses `deepagents==0.6.12` to generate, execute, observe, and correct Python analysis through one tool.

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

Run the example pipeline from the command line:

```bash
uv run python examples/run_pipeline.py --source sales.csv --query "What is the total revenue?"
uv run python examples/run_pipeline.py --package examples/data_corpus_package/data_corpus_package.json --query "Summarize this package"
```

The `--package` file describes a data corpus package by reference:

```json
{
  "vectordb": "vectordb",
  "db": "warehouse.db",
  "schema": "schema.json",
  "catalog": "catalog.json"
}
```

Relative paths resolve from the package file directory. The example runner maps `vectordb` and `db` into `DataCorpusPackage.sources`, loads `schema.json` into `DataCorpusPackage.schemas`, and loads `catalog.json` into `DataCorpusPackage.metadata["catalog"]`.

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
LLM_MODEL_NAME
```

### Runtime workflow CLI

Use the demo to run the complete non-interactive query-to-answer flow:

```bash
uv run python examples/demo_workflow_cli.py \
  --package examples/data_corpus_package/data_corpus_package.json \
  --config configs/proxy-openrouter.toml \
  --env-file .env \
  --query "Summarize this data corpus package"
```

The CLI builds the spec, selects the engine, provisions one sandbox, stages the
local source files, runs the Deep Agent, and prints the answer. Add `--verbose`
to enable AXIOM debug logs and print response metadata:

```bash
uv run python examples/demo_workflow_cli.py \
  --query "Explain the main findings" \
  --verbose
```

Provider settings normally come from `configs/proxy-openrouter.toml` and the
environment. The CLI loads `.env` by default so the checked-in TOML's
`${env:...}` placeholders work in local runs. Use `--env-file PATH` to load a
different env file; values already exported in the shell take precedence.
Provider settings can also be overridden with `--config`, `--model`,
`--api-key`, and `--base-url`. Structured pipeline events are written to
`logs/pipeline.log` by default; pass `--no-trace` to disable that file.

When the AXIOM sandbox is enabled, the application-owned pipeline factory
creates one sandbox for the entire request. Configure the private AXIOM backend
in `.env`:

```env
METHODS_HUB_ENABLED=false
SANDBOX_ENABLED=true
SANDBOX_URL=http://localhost:8004
SANDBOX_WORKSPACE_ID=00000000-0000-0000-0000-000000000001
```

The CLI example discovers the sibling client source directly for local
development. Production applications should install `axiom-sandbox-client`.
The general engine requires `SANDBOX_ENABLED=true`. The AXIOM backend also
requires a workspace ID.

For local development without the private AXIOM service, build the bundled
Docker sandbox image:

```bash
docker build -f docker/sandbox.Dockerfile -t data-intelligence-sandbox:local .
```

Then configure the API process:

```env
SANDBOX_ENABLED=true
SANDBOX_BACKEND=docker
SANDBOX_DOCKER_IMAGE=data-intelligence-sandbox:local
REPORT_FORCE_CODE_AGENT=false
```

The Docker backend creates one network-disabled container per request, stages
the uploaded sources under `/workspace/input`, applies CPU, memory, and process
limits, and removes the container after the workflow completes. Optional local
limits are `SANDBOX_DOCKER_MEMORY`, `SANDBOX_DOCKER_CPUS`,
`SANDBOX_DOCKER_PIDS_LIMIT`, and `SANDBOX_DOCKER_WORKSPACE_SIZE`.
The API defaults `REPORT_FORCE_CODE_AGENT` to `false` so deterministic Method Hub
tools are preferred when their contracts match. Set it to `true` only to exercise
the generated-code route even when a matching tool exists.

Each invocation creates a persistent artifact bundle under `artifacts/<run-id>/`
by default. Configure a different root with `ARTIFACT_ROOT`. The bundle retains
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

### Explicit Method Hub membership

The default Method Hub membership is defined by `DEFAULT_METHODS` in
`packages/sdk/src/data_intelligence_sdk/runtime/method_profile.py`. It contains
concrete executable method names only; high-level capability labels such as
`answer_question` are resolved by the engine and do not belong in this list.
Optionally provide a TOML profile or override the complete list with:

```text
METHOD_HUB_CONFIG_PATH=/absolute/path/to/method-hub.toml
METHOD_HUB_METHODS=inspect_data_folder,search_text_files,scan_csv
```

Vector search methods are disabled by default while local-file workflows are
being used. Re-enable all vector methods with:

```text
ENABLE_VECTOR_METHODS=true
```

## FastAPI Responses Backend

The repository includes a FastAPI consuming application that runs the example
Data Intelligence workflow and streams lifecycle and final-output events.

Configure the workflow and API:

```text
OPENROUTER_API_KEY=...
LLM_MODEL_NAME=...
DATA_CORPUS_ROOT=/absolute/path/to/Data-Intelligence-SDK
DATABASE_URL=postgresql://data_intelligence:data_intelligence@127.0.0.1:5432/data_intelligence
MODEL_CONFIG_PATH=/absolute/path/to/Data-Intelligence-SDK/configs/proxy-openrouter.toml
API_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
PIPELINE_TIMEOUT_SECONDS=300
SPEC_CONFIRMATION_TTL_SECONDS=86400
MAX_SPEC_REVISION_ROUNDS=5
```

Start the data_intelligence_api:

```bash
uv run uvicorn data_intelligence_api.main:app --reload --host 127.0.0.1 --port 8000
```

Check health:

```bash
curl http://127.0.0.1:8000/health
```

Run a streaming response against a local corpus source:

```bash
curl --no-buffer \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "What is the total revenue?",
    "data_corpus_package": {
      "sources": ["data/samples/data.csv"],
      "schemas": {},
      "metadata": {}
    }
  }' \
  http://127.0.0.1:8000/api/v1/responses
```

The workflow classifies queries as `reason`, `report`, or `general`; all three
are routed to an appropriate registered engine. `unknown` remains available for
legacy or invalid classifier output.
The initial stream ends with `response.requires_confirmation` and includes a
`response_id`, `confirmation_token`, revision, intent, and editable spec. No
engine runs before this event is confirmed.

Revise the pending spec (repeat as needed):

```bash
curl -N http://127.0.0.1:8000/api/v1/responses/RESP_ID/decision \
  -H 'Content-Type: application/json' \
  -H 'X-Confirmation-Token: TOKEN' \
  -d '{"action":"revise","revision":1,"feedback":"Use monthly totals"}'
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

The Compose configuration at `docker/docker-compose.yaml` runs the FastAPI
data_intelligence_api and a private PostgreSQL 17 service. It exposes only the API at
`http://127.0.0.1:8000`, mounts `./data` read-only, and persists both uploaded
corpus files and confirmation state in Docker volumes. Optional model-provider
credentials and `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` can be
loaded from the root `.env` by passing `--env-file .env`. The API loads its model
definition from `/app/configs/proxy-openrouter.toml` through
`MODEL_CONFIG_PATH`; values such as `LLM_MODEL_NAME` and
`OPENROUTER_API_KEY` are resolved from the environment.

Build and start the API:

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up --build -d
```

Check container status and logs:

```bash
docker compose -f docker/docker-compose.yaml ps
docker compose -f docker/docker-compose.yaml logs -f api
```

Check API health:

```bash
curl http://127.0.0.1:8000/health
```

Run a streaming response:

```bash
curl -N http://127.0.0.1:8000/api/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "Analyze this dataset",
    "data_corpus_package": {
      "sources": ["data/samples/data.csv"],
      "schemas": {},
      "metadata": {}
    },
    "session_id": "docker-test"
  }'
```

Stop the API container:

```bash
docker compose -f docker/docker-compose.yaml down
```
