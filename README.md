## Repository Layout

```text
packages/
  sdk/       # reusable Python library
  api/       # FastAPI application built on the SDK
web/         # separate Next.js frontend
examples/    # runnable examples and sample corpus packages
data/samples/ # small checked-in fixtures
configs/     # development and example configuration
docs/
docker/
```

The SDK and API are separate Python workspace packages. The API owns application
composition; the SDK does not import the API, web client, examples, or repository
datasets at runtime.

The API layering and planned worker/RabbitMQ boundary are documented in
[`docs/api-architecture.md`](docs/api-architecture.md).

## Setup

```
uv venv --python 3.11
uv pip install -e packages/sdk -e packages/api

docker compose -f examples/data_corpus_package/docker-compose.yml up -d
```

# Data Intelligence

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

The CLI builds the spec, selects the engine, provisions one sandbox, stages the
local source files, runs the Deep Agent, and prints the answer. Add `--verbose`
to enable AXIOM debug logs and print response metadata:

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
The general engine requires `SANDBOX_ENABLED=true` and a workspace ID.

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

### Optional LangSmith tracing

LangChain/LangGraph runs and the custom OpenAI-compatible spec-builder client
can be traced to LangSmith. Tracing is disabled unless explicitly enabled:

```text
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=data-intelligence-sdk
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

These variables can be placed in `docker/.env` for local development. The SDK
does not send traces when `LANGCHAIN_TRACING_V2` is unset or false.

### Explicit Method Hub membership

The default Method Hub membership is defined in
`configs/development/method-hub.toml`. It contains concrete executable method
names only; high-level capability labels such as `answer_question` are resolved
by the engine and do not belong in this list. Override the profile location or
the complete list with:

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
`http://SERVER_IP:8000`. API, upload, chat, artifact, and PostgreSQL data are
stored in named Docker volumes.

Validate and start the stack:

```bash
docker compose -f docker/docker-compose.yaml config --quiet
docker compose -f docker/docker-compose.yaml up --build -d
docker compose -f docker/docker-compose.yaml ps
curl http://127.0.0.1:8000/health
```

Inspect API logs:

```bash
docker compose -f docker/docker-compose.yaml logs -f api
```

Stop the stack while retaining database and API volumes:

```bash
docker compose -f docker/docker-compose.yaml down
```
