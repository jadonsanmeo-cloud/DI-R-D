# Local Ingest to Markdown Spec Runbook

This runbook describes the current local development flow:

```text
PDF
-> AXIOM Document Service
-> MinIO
-> Kafka s3-events
-> Spark streaming
-> AXIOM_DE-RD
-> Corpus PostgreSQL
-> Markdown spec
```

There are two spec-generation flows:

```text
Interactive: query -> Markdown spec
Scheduled: three newest documents -> one Markdown spec per missing seed
```

The scheduled worker currently creates spec files only. It does not invoke
Report Engine or generate reports.

## Local Ports

| Service | Address |
| --- | --- |
| AXIOM Document Service | `http://localhost:38001` |
| AXIOM Corpus Service | `http://localhost:38002` |
| Corpus PostgreSQL | `localhost:30433` |
| MinIO API | `http://localhost:30443` |
| MinIO Console | `http://localhost:31443` |
| Kafka UI | `http://localhost:8080` |
| Spark Master UI | `http://localhost:38080` |
| Spark Worker UI | `http://localhost:38081` |
| AXIOM_DE-RD | `http://localhost:18000` |
| AXIOM Intent Service | `http://localhost:8005` |
| Data Intelligence API | `http://localhost:8036` |

Port `8003` is not published by the current AXIOM Compose configuration.

## 1. Start AXIOM Infrastructure

Open a Windows CMD terminal:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\AXIOM"
docker compose -f deployments\k8s\docker-compose.yml up -d
docker compose -f deployments\k8s\docker-compose.yml ps
```

The following services should be running:

```text
k8s-document-service-1
k8s-corpus-service-1
k8s-kafka-1
k8s-minio-1
k8s-postgres-1
k8s-spark-master-1
k8s-spark-worker-1
```

Check the databases exposed by the HTTP services:

```cmd
curl http://localhost:38001/api/v1/health/db
curl http://localhost:38002/api/v1/health/db
```

## 2. Start AXIOM_DE-RD

Open a second CMD terminal:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\AXIOM_DE-RD"
uv sync
uv run uvicorn src.api.app:app --host 0.0.0.0 --port 18000
```

Keep this terminal open. A successful startup includes:

```text
Application startup complete
Uvicorn running on http://0.0.0.0:18000
```

The application may not define `/health`. Use OpenAPI to verify connectivity:

```cmd
curl http://localhost:18000/openapi.json
```

## 3. Start Spark Streaming

Open Git Bash. Do not run this command from the `docker-desktop:~#` shell.

```bash
cd "/e/UET/Lab/Data Intelligence/AXIOM"
docker ps --format '{{.Names}}' | grep spark-master
```

The result should include `k8s-spark-master-1`.

Run the streaming job:

```bash
MSYS_NO_PATHCONV=1 \
MSYS2_ARG_CONV_EXCL="*" \
AXIOM_SPARK_LOG_LEVEL=INFO \
AXIOM_SPARK_STARTING_OFFSETS=earliest \
sh services/indexing-streaming/scripts/run_process_s3_events.sh
```

Keep this terminal open. Successful startup includes:

```text
Submitted application: axiom-process-s3-events
Connected to Spark cluster
Executor ... is now RUNNING
Starting Spark job
topic=s3-events
starting_offsets=earliest
```

The native Hadoop warning is non-fatal:

```text
Unable to load native-hadoop library
```

Check the `indexing_service_url` printed by Spark. It must point to the current
Windows host IP on port `18000`. Use `ipconfig` in CMD to verify the IPv4
address if Spark cannot contact AXIOM_DE-RD.

## 4. Upload Five Small NAPH PDFs

Upload through AXIOM Document Service on port `38001`. Do not use the temporary
SDK upload endpoint because it only stores local files and does not publish the
S3 event needed by the indexing pipeline.

Open another CMD terminal and run each command:

```cmd
curl --location "http://localhost:38001/api/v1/organizations/test-org/files" --form "files=@E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\examples\naph_corpus\raw\NAPH LAP 10AR v1.0 Royal Brompton for web.pdf"
```

```cmd
curl --location "http://localhost:38001/api/v1/organizations/test-org/files" --form "files=@E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\examples\naph_corpus\raw\NAPH LAP 10AR v1.0 Newcastle for web.pdf"
```

```cmd
curl --location "http://localhost:38001/api/v1/organizations/test-org/files" --form "files=@E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\examples\naph_corpus\raw\NAPH LAP 10AR v1.0 Imperial for web.pdf"
```

```cmd
curl --location "http://localhost:38001/api/v1/organizations/test-org/files" --form "files=@E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\examples\naph_corpus\raw\NAPH LAP 10AR v1.0 Golden Jubilee for web.pdf"
```

```cmd
curl --location "http://localhost:38001/api/v1/organizations/test-org/files" --form "files=@E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\examples\naph_corpus\raw\NAPH LAP 10AR v1.0 Sheffield for web.pdf"
```

A successful upload returns HTTP `201`.

List the files stored for the organization:

```cmd
curl http://localhost:38001/api/v1/organizations/test-org/files
```

## 5. Monitor Indexing

After uploading, inspect the Spark terminal for events such as:

```text
s3-events
create
batch
document
indexing
```

AXIOM_DE-RD should receive a request to `POST /v1/dataeng`.

Inspect service logs when needed:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\AXIOM"
docker compose -f deployments\k8s\docker-compose.yml logs --tail 100 document-service
docker compose -f deployments\k8s\docker-compose.yml logs --tail 100 corpus-service
```

The Kafka and Spark interfaces are available at:

```text
http://localhost:8080
http://localhost:38080
```

## 6. Verify Corpus PostgreSQL

Connect to the shared AXIOM PostgreSQL container:

```cmd
docker exec -it k8s-postgres-1 psql -U app_dev -d corpus
```

Inspect the schema and counts:

```sql
\dt
SELECT COUNT(*) FROM documents;
SELECT COUNT(*) FROM corpus_documents;
SELECT COUNT(*) FROM document_contents;
SELECT COUNT(*) FROM document_embeddings;
```

Inspect recent data:

```sql
SELECT * FROM documents ORDER BY created_at DESC LIMIT 10;
SELECT * FROM document_processing_runs LIMIT 10;
```

If a referenced column does not exist, inspect the actual schema:

```sql
\d documents
\d document_processing_runs
\d document_embeddings
```

Interpretation:

```text
documents > 0            document metadata exists
document_contents > 0    extraction/chunking produced content
document_embeddings > 0  embeddings were generated
successful processing    indexing completed
```

Exit `psql` with `\q`.

## 7. Create an Interactive Markdown Spec

Intent Service currently requires `GEMINI_API_KEY`. To run without Intent
Service, omit `--intent-service-url`. The fallback analyzer maps requests that
contain words such as `report`, `summarize`, `summary`, or `dashboard` to the
`report` intent. This is a heuristic, not an unconditional hardcode.

Open CMD:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK"
set OPENROUTER_API_KEY=YOUR_NEW_OPENROUTER_KEY
set LLM_MODEL_NAME=qwen/qwen3-30b-a3b
set DEFAULT_ORGANIZATION_ID=test-org
```

Generate the spec:

```cmd
uv run python scripts\prepare_spec.py ^
  --query "Create a report that summarizes the latest NAPH reports and compares their main findings" ^
  --output .data\debug-spec\execution-spec.md ^
  --config configs\proxy-openrouter.toml ^
  --verbose
```

The valid config path is `configs\proxy-openrouter.toml`, not
`configs\development\proxy-openrouter.toml`.

A successful run ends with:

```text
spec_preparation.completed
markdown_write.completed
```

Inspect the result:

```cmd
type .data\debug-spec\execution-spec.md
```

The Markdown must contain:

```text
# Interactive Execution Spec
## User Request
## Intent
## Preparation Guidance
## Execution Instructions
## Expected Output
```

### How Ingested Data Reaches the Interactive Flow

The interactive Markdown builder does not currently receive PDF contents,
chunks, embeddings, or document IDs. It generates a spec from the query and
intent analysis only.

The Markdown instructs Report Engine to retrieve relevant documents from the
configured organization corpus. `organization_id=test-org` is passed when the
confirmed Markdown reaches `ReportEngine.run_markdown()`.

The current Report Engine boundary still uses a compatibility bridge into the
legacy graph. Retrieval directly from Corpus Service is not yet fully wired.

## 8. Run the Scheduled Spec Worker

The worker reads the three newest indexed documents from Corpus PostgreSQL and
creates one Markdown spec for every seed without an existing spec file.

Run one cycle:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK"
set CORPUS_DATABASE_URL=postgresql+psycopg://app_dev:app_dev_password@localhost:30433/corpus
set CORPUS_ORGANIZATION_ID=test-org
uv run python scripts\recent_spec_worker.py --once --verbose
```

Generated files are stored under:

```text
.data\scheduled-report-specs
```

Run continuously with the default 15-minute interval:

```cmd
uv run python scripts\recent_spec_worker.py --verbose
```

The sliding-window behavior is:

```text
Initial newest documents: a, b, c
-> create a.md, b.md, c.md

After ingesting d: b, c, d
-> b.md and c.md already exist
-> create d.md only
```

The worker currently does not:

```text
invoke ReportEngine
retrieve related document contents or chunks
generate a report
persist a report
publish a report to the dashboard
```

## 9. Start the Data Intelligence API When Needed

The SDK Compose file is inside the `docker` directory:

```cmd
cd /d "E:\UET\Lab\Data Intelligence\Data-Intelligence-SDK\docker"
docker compose up -d
docker compose ps
curl http://localhost:8036/health
```

The expected health response is:

```json
{"status":"ok"}
```

When temporarily bypassing Intent Service, leave `INTENT_SERVICE_BASE_URL`
unset or empty in the API environment.

Create a Responses API request:

```cmd
curl --no-buffer "http://localhost:8036/api/v1/responses" ^
  -H "Content-Type: application/json" ^
  -d "{\"input\":\"Create a report summarizing the latest NAPH reports\",\"session_id\":\"naph-demo\"}"
```

The confirmation event contains `response_id`, `confirmation_token`,
`revision`, and `spec_markdown`.

## Final Checklist

```text
[ ] AXIOM containers are healthy
[ ] AXIOM_DE-RD is listening on port 18000
[ ] Spark streaming is running
[ ] Uploads use Document Service port 38001 and return HTTP 201
[ ] Spark receives s3-events
[ ] Corpus PostgreSQL contains documents, contents, and embeddings
[ ] OPENROUTER_API_KEY is set to a valid, non-exposed key
[ ] LLM_MODEL_NAME is qwen/qwen3-30b-a3b
[ ] The config path is configs\proxy-openrouter.toml
[ ] --intent-service-url is omitted for the temporary fallback flow
[ ] prepare_spec.py creates execution-spec.md
[ ] recent_spec_worker.py creates scheduled Markdown specs
```

Rotate any API key that has previously been pasted into logs, chat, or source
files before continued use.
