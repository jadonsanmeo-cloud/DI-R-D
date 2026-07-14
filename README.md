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
  -> Spec Confirmation
  -> Engine Registry
  -> Selected Engine
  -> EngineRuntimeContext
       -> MethodHub
       -> InterfaceRegistry
       -> InterfaceBuilder
       -> SandboxExecutor
       -> Artifacts / Logs / Resources
  -> Engine Execution
       -> reuse trusted interface/method
       -> or create generated interface
       -> validate/run generated interface in sandbox
       -> emit EngineOutput + EngineTrace
  -> Evidence Collector
  -> Synthesizer
  -> FinalResponse(answer + evidence)
```

Supporting layers:

- `runtime`: method hub, interface registry, interface builder boundary, engine runtime context, executor, logger, resource manager, cache.
- `sandbox`: controlled execution, data/workspace/artifacts/logs boundaries.
- `context`: user and session context placeholders.
- `docs/method_hub.md`: Method Hub contract, manifest schema, catalog generation, and proposal/export workflow.

## Base Design Notes

- `DataCorpusPackage` describes the available data universe for a task. It may contain source refs, schemas, semantic metadata, and policy metadata; it does not necessarily contain raw data.
- `DataHubContext` remains available as a compatibility alias for `DataCorpusPackage` during the transition.
- `Intent` is a controlled string selected from `SUPPORTED_INTENTS`: `reason` for questions about data, `report` for report generation, and `unknown` for outliers. The spec carries the richer objective, constraints, and capability requirements.
- `ExecutionSpec.capability_requirements` describes what the selected engine/runtime must resolve.
- Engines receive an `EngineRuntimeContext`, which owns an `EngineRunContext` for trace recording and exposes runtime services such as `MethodHub`, `InterfaceRegistry`, `InterfaceBuilder`, and `SandboxExecutor`.
- Engines should request capabilities from runtime services. They should not each reimplement interface discovery, generated interface lifecycle, sandbox policy, trust promotion, artifact/log policy, or evidence construction.
- Generated interfaces start as `generated_unvalidated` and should be validated through sandbox execution before reuse.
- `EngineOutput` contains raw engine output plus `EngineTrace`.
- `EvidenceBundle` uses engine trace, method calls, interface definitions, sandbox results, observations, artifact refs, and log refs for audit and final response generation.
- `SessionContext` is separate from `UserContext`: session context is short-lived conversation/task state, while user context is longer-lived preference and history.

## Base Query-to-Answer Workflow

The SDK exposes interfaces/contracts and reusable runtime capabilities. Concrete analyzer/spec/evidence/synthesis implementations live in consuming applications or examples, not in the SDK package. `GeneralPurposeEngine` is the fallback/general engine, OpenRouter is the first supported LLM provider through LangChain, and concrete CSV capabilities are exposed through MethodHub methods.

```python
from data_intelligence_sdk import DataCorpusPackage, UserQuery
from examples.basic_workflow import create_example_pipeline

pipeline = create_example_pipeline()
response = pipeline.run(
    UserQuery("What is the total revenue in this file?"),
    DataCorpusPackage(sources=["sales.csv"]),
)
print(response.answer)
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

### Interactive workflow CLI

Use the interactive demo to walk through the current query-to-response flow:

```bash
uv run python examples/demo_workflow_cli.py \
  --package examples/data_corpus_package/data_corpus_package.json \
  --config configs/development/proxy-openrouter.toml \
  --env-file docker/.env \
  --query "Summarize this data corpus package"
```

The CLI uses the LLM-backed spec builder, displays the inferred intent and
draft `ExecutionSpec`, then pauses for an explicit decision:

```text
Decision [c]onfirm / [r]evise / [q]uit: r
Revision feedback: Focus on monthly revenue and cite the source datasets

Decision [c]onfirm / [r]evise / [q]uit: c
```

The engine does not run before confirmation. Use `q` to stop without executing
the spec, or add `--verbose` to print the full spec, evidence, engine steps,
MethodHub calls, artifact/log references, and response metadata:

```bash
uv run python examples/demo_workflow_cli.py \
  --query "Explain the main findings" \
  --verbose
```

Provider settings normally come from `configs/development/proxy-openrouter.toml` and the
environment. The CLI loads `docker/.env` by default so the checked-in TOML's
`${env:...}` placeholders work in local runs. Use `--env-file PATH` to load a
different env file; values already exported in the shell take precedence.
Provider settings can also be overridden with `--config`, `--model`,
`--api-key`, and `--base-url`. Structured pipeline events are written to
`logs/pipeline.log` by default; pass `--no-trace` to disable that file.

Known PostgreSQL tables and pgvector document chunks use trusted, read-only
MethodHub methods, so the demo does not require a generated-code sandbox for
those sources. Start the checked-in corpus database before confirming a run:

```bash
docker compose -f examples/data_corpus_package/docker-compose.yml up -d
```

Generated tools for capabilities outside the trusted method set still require
a separately configured `SandboxExecutor`.

`--config` accepts either a repository-relative path or an absolute path:

```bash
uv run python examples/demo_workflow_cli.py \
  --config /absolute/path/to/proxy-openrouter.toml \
  --env-file /absolute/path/to/.env \
  --query "Summarize this data corpus package"
```

CSV MethodHub methods live in `data_intelligence_sdk.methods` and include `scan_csv`, `filter_csv`, `count_csv`, and `sum_csv`. Example pipeline factories live in `examples.basic_workflow` as app-owned wiring outside the SDK package.

Tests use fake engines or fake LLMs and do not call OpenRouter.

## FastAPI Responses Backend

The repository includes a FastAPI consuming application that runs the example
Data Intelligence workflow and streams lifecycle and final-output events.

Configure the workflow and API:

```text
OPENROUTER_API_KEY=...
LLM_MODEL_NAME=...
DATA_CORPUS_ROOT=/absolute/path/to/Data-Intelligence-SDK
DATABASE_URL=postgresql://data_intelligence:data_intelligence@127.0.0.1:5432/data_intelligence
MODEL_CONFIG_PATH=/absolute/path/to/Data-Intelligence-SDK/configs/development/proxy-openrouter.toml
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

The workflow infers whether to answer, reason, or create a report from the query.
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
credentials and `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` are read
from `docker/.env` at container startup. The API loads its model definition from
`/app/configs/development/proxy-openrouter.toml` through `MODEL_CONFIG_PATH`; values such as
`LLM_MODEL_NAME` and `OPENROUTER_API_KEY` are resolved from `docker/.env`.

Build and start the API:

```bash
docker compose -f docker/docker-compose.yaml up --build -d
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
