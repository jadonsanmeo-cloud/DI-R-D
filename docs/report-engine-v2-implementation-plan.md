# Report Engine v2 implementation plan

## 1. Objective

Evolve the current sequential `ReportEngine` into a template-driven report
workflow with explicit data lineage, dependency-aware execution, parallel chart
generation, structured report output, and separate rendering.

The target workflow is:

```text
User goal + DataCorpusPackage
        |
        v
PlanAgent <------> TemplateAgent <------> TemplatePool
        |                  |
        |                  +---- TemplateInstance
        +---- FinalPlan
                 |
                 v
            DAG Scheduler
                 |
       +---------+----------+
       |                    |
existing method       generated method
       |           Code -> Sandbox -> Validator
       +--------- Tool Executor -----+
                         |
                         v
                    Data Runtime
              artifact + schema + profile
                         |
                         v
                 DataScience tasks
                  |             |
                  |             +---- chart datasets
                  |                       |
                  |               ChartInputAssembler
                  |                       |
                  |             parallel ChartAgent tasks
                  |                       |
                  +------- ReportAgent <--+--- TemplateInstance
                              |
                        structured report
                              |
                           Renderer
                       HTML / Markdown
```

## 2. Scope boundaries

### Included

- A versioned built-in Template Pool.
- Template selection, cloning, adaptation, and Plan/Template negotiation.
- Data dependencies with named input and output references.
- A scheduler that starts a task as soon as its dependencies are ready.
- One execution path for existing and generated tools.
- Artifact references, deterministic schema/profile/sample generation, and
  chart-ready datasets.
- Independent ECharts chart tasks that can run concurrently.
- A structured report contract and HTML/Markdown renderers.
- Traceability from source data through analysis, chart, report, and render
  artifacts.

### Deferred

- Major semantic improvements to ValidatorAgent. Its interface will be kept
  compatible, but deeper business-logic validation can be added later.
- Persisting a modified TemplateInstance back into the canonical pool.
- A visual template editor.
- Distributed scheduling. The first scheduler is in-process.
- Live LLM or database calls in unit tests.

## 3. Current baseline and migration constraints

The current `ReportEngine` already has Plan, Router, Code, Sandbox, Validator,
MethodHub, DataScience, and Report stages. The migration must preserve:

- `EngineOutput` and `EngineTrace` as the engine boundary.
- `EngineRuntimeContext` as the runtime service boundary.
- Existing MethodHub registration and trust levels.
- Offline/fallback behavior when no LLM is configured.
- Existing tests until their assertions are intentionally migrated.

Known gaps to close:

- `depends_on` currently controls ordering but does not bind prior outputs.
- Existing-tool failures currently become an empty list without a route
  fallback or structured failure.
- Tool results are passed inline instead of through data artifacts.
- DataScience output only contains a summary and aggregated metrics.
- There is no TemplateAgent, TemplatePool, chart stage, report JSON contract,
  or renderer stage.
- ReportAgent emits Markdown directly.

## 4. Architectural rules to establish first

1. Agents decide, select, and explain; deterministic runtime code executes,
   profiles, samples, stores, schedules, and validates structural contracts.
2. Canonical templates are immutable. Every run operates on a cloned
   `TemplateInstance`.
3. A dependency means data lineage. Every dependency must be backed by an
   input reference to a named upstream output.
4. Large data travels by artifact reference, not in LLM prompts or trace
   metadata.
5. Business aggregations are computed once and shared by narrative and chart
   generation.
6. ChartAgent creates presentation specifications; it must not recompute
   business metrics.
7. Required and optional dependencies have different failure policies.
8. Every loop and retry path has a configured maximum.
9. ReportAgent produces structured JSON. Renderer owns output format.
10. ECharts output is JSON-serializable and cannot contain executable
    JavaScript functions.

## 5. Contracts to introduce

Define these as small dataclasses or typed dictionaries under a dedicated
`reporting/contracts.py` module. Keep JSON serialization explicit.

| Contract | Purpose |
| --- | --- |
| `ReportPlan` | Versioned DAG produced by PlanAgent |
| `PlanStep` | One executable data/analysis step |
| `DataReference` | Reference to a named upstream output or corpus dataset |
| `StepOutputDefinition` | Name, schema expectation, and intended consumers |
| `TemplateDefinition` | Immutable template loaded from the pool |
| `TemplateInstance` | Run-local adapted template with resolved bindings |
| `TemplateBinding` | Connect a template requirement to a plan output |
| `MissingDataRequest` | Template request for data not exposed by the plan |
| `NegotiationResult` | Accepted, partial, or failed plan/template pair |
| `ExecutionResult` | Status and artifact returned by Tool Executor |
| `DataArtifact` | URI, format, schema, profile, sample, and lineage |
| `DataStepResult` | Analysis, aggregates, and chart datasets for one step |
| `ChartDataset` | Chart-shaped data bound to one or more chart IDs |
| `ChartRequest` | Resolved template chart slot and data context |
| `ChartResult` | ECharts option, dataset refs, status, and warnings |
| `StructuredReport` | Sections and blocks produced by ReportAgent |
| `RenderedReport` | Format, content/artifact ref, and render warnings |

All contracts need:

- A `schema_version` where persisted or exchanged with an LLM.
- Stable IDs.
- `status`, `warnings`, and lineage where appropriate.
- Validation before a downstream component consumes them.

## 6. Phase-by-phase implementation

### Phase 0 — Freeze decisions and failure policies

#### Tasks

1. Confirm JSON, not JSONL, as the structured report format. Use JSONL only
   for event streams or trace records.
2. Confirm built-in template precedence versus application-provided templates.
3. Choose limits:
   - Maximum Plan/Template negotiation iterations.
   - Maximum generated-code attempts.
   - DataScience concurrency.
   - Chart concurrency.
   - Artifact/sample size.
4. Define required versus optional failure behavior.
5. Define whether an existing-tool execution failure may fall back to generated
   code.
6. Define artifact retention and redaction policies.

#### Deliverable

A configuration contract containing all limits and policies. Avoid hard-coded
limits across agent implementations.

#### Acceptance criteria

- Every loop has a limit.
- Every node type has a documented failure transition.
- No unresolved decision changes a public contract in later phases.

### Phase 1 — Template Pool foundation

#### Target files

```text
src/data_intelligence_sdk/templates/
└── pool/
    ├── manifest.json
    ├── template.schema.json
    ├── executive-overview.json
    ├── time-series-analysis.json
    └── segment-comparison.json

src/data_intelligence_sdk/reporting/
├── contracts.py
└── template_pool.py
```

#### Tasks

1. Implement `TemplatePool` using `importlib.resources`; never assume a real
   filesystem path because package resources may live in a wheel.
2. Load `manifest.json` and expose lightweight descriptors without reading all
   templates.
3. Load a template by `template_id` and optional version.
4. Validate a loaded payload against the supported schema version.
5. Return immutable `TemplateDefinition` objects.
6. Support an optional application template directory layered over built-ins.
7. Reject duplicate IDs at the same precedence level.
8. Add selection metadata indexing for keywords, report intents, and data
   signals.

#### Tests

- Built-in manifest and all three templates parse.
- IDs and `(template_id, version)` pairs are unique.
- Manifest paths exist as package resources.
- Unsupported schema versions fail clearly.
- Invalid block references and chart requirement references fail.
- Application templates can override or extend according to the chosen policy.
- A built wheel contains every JSON resource.

#### Acceptance criteria

- A caller can list and load templates without knowing their storage path.
- Canonical definitions cannot be mutated by a report run.

### Phase 2 — Plan/Template negotiation

#### Target modules

```text
src/data_intelligence_sdk/reporting/planning.py
src/data_intelligence_sdk/reporting/template_agent.py
src/data_intelligence_sdk/reporting/negotiation.py
```

#### Tasks

1. Upgrade PlanAgent output from unvalidated dictionaries to `ReportPlan`.
2. Require every `PlanStep` to declare named outputs.
3. Implement TemplateAgent selection using:
   - User goal.
   - Plan output definitions.
   - Corpus schema/catalog.
   - Template selection metadata.
4. Clone the selected definition into a `TemplateInstance`.
5. Bind every section block and chart slot to plan output references.
6. Emit `MissingDataRequest` records for unresolved required or optional data.
7. Ask PlanAgent to add feasible steps and reject infeasible requests with a
   reason.
8. Rebind and adapt the TemplateInstance after each plan revision.
9. Stop when:
   - All required bindings resolve.
   - The result is partial with accepted fallbacks.
   - A fatal requirement is unavailable.
   - The configured iteration limit is reached.
10. Record every revision and decision in `EngineRunContext`.

#### Negotiation invariants

- Plan revision numbers increase monotonically.
- Template instance revision numbers increase monotonically.
- Canonical templates never change.
- A binding references a real plan output.
- A plan dependency references a real upstream step.
- Cycles are rejected before execution.

#### Tests

- Template selects successfully from a fully compatible plan.
- Missing optional data produces a fallback and `partial` status.
- Missing feasible data causes PlanAgent to add a step.
- Missing fatal data produces `failed` status.
- The same plan/template revision hash stops the loop.
- The maximum iteration limit prevents infinite negotiation.

#### Acceptance criteria

- Execution receives exactly one validated `FinalPlan` and one immutable
  `TemplateInstance`.

### Phase 3 — Data-aware DAG Scheduler

#### Target modules

```text
src/data_intelligence_sdk/reporting/scheduler.py
src/data_intelligence_sdk/reporting/task_state.py
```

#### Tasks

1. Validate the DAG before starting work:
   - Unique step IDs.
   - Existing dependencies.
   - No cycles.
   - Valid input/output references.
2. Model node states: `pending`, `ready`, `running`, `completed`, `failed`, and
   `skipped`.
3. Mark a node ready only when its hard dependencies complete.
4. Resolve input references into artifact references, not inline datasets.
5. Use bounded concurrency with separate semaphores for data and chart tasks.
6. Start chart tasks as soon as their required `ChartDataset` is available.
7. Propagate failure according to required/optional edges.
8. Support cancellation and timeouts without leaving a node marked running.
9. Record scheduling transitions in the run trace.
10. Return an execution summary with completed, failed, and skipped nodes.

#### First implementation choice

Use in-process `asyncio` scheduling. Keep the scheduler interface independent of
`asyncio` so a distributed implementation can replace it later.

#### Tests

- Independent nodes overlap in a controlled async test.
- Dependent nodes never start early.
- A chart starts after its dataset is ready without waiting for unrelated data
  tasks.
- A failed required dependency skips its consumer.
- A failed optional chart does not fail the report.
- Cancellation produces terminal states for all started nodes.

#### Acceptance criteria

- Execution order follows data lineage.
- Concurrency is observable and bounded.

### Phase 4 — Unified routing and Tool Executor

#### Target modules

```text
src/data_intelligence_sdk/reporting/routing.py
src/data_intelligence_sdk/runtime/tool_executor.py
```

#### Tasks

1. Make RouterAgent return a validated route decision only.
2. Introduce `ToolExecutionRequest` with tool name, arguments, expected output,
   resource policy, and step ID.
3. Route existing methods through Tool Executor.
4. Route validated generated methods through the same Tool Executor.
5. Centralize:
   - Input validation.
   - Timeouts.
   - Retry policy.
   - Trace recording.
   - Exception normalization.
   - Artifact creation.
6. Decide and implement existing-tool failure fallback to code generation.
7. Preserve MethodHub trust levels and InterfaceRegistry evidence.

#### Tests

- Existing and generated methods produce the same execution-result shape.
- Invalid route arguments fail before method invocation.
- Exceptions become structured failures with log references.
- Retry and fallback limits are respected.

#### Acceptance criteria

- No direct method call remains inside RouterAgent or DataScienceAgent.

### Phase 5 — Data Runtime

#### Target modules

```text
src/data_intelligence_sdk/runtime/data_artifacts.py
src/data_intelligence_sdk/runtime/data_profiler.py
src/data_intelligence_sdk/runtime/data_sampler.py
```

#### Tasks

1. Define a `DataArtifactStore` protocol separate from string-only artifact
   references if necessary.
2. Store full step results in a supported format:
   - JSON for small structured values.
   - Parquet or Arrow for tabular data when dependencies are approved.
3. Generate deterministic schema metadata.
4. Generate bounded profiles:
   - Row and column counts.
   - Null counts.
   - Numeric min/max/mean.
   - Categorical cardinality.
   - Time ranges.
5. Generate bounded, deterministic samples with redaction.
6. Keep large values out of trace metadata and prompts.
7. Attach source dataset, method call, plan step, and upstream artifact lineage.
8. Distinguish source data from a step result; prefer `step_result_ref` over the
   ambiguous name `raw_data_ref`.

#### Tests

- Profiles are correct for empty, numeric, categorical, and time data.
- Sampling respects row/byte limits.
- Sensitive columns are redacted by policy.
- Artifact URIs resolve during the run.
- Traces contain refs and summaries, not full large datasets.

#### Acceptance criteria

- Downstream agents receive only bounded context plus resolvable references.

### Phase 6 — DataScience task contract

#### Target modules

```text
src/data_intelligence_sdk/reporting/data_science.py
src/data_intelligence_sdk/reporting/chart_data.py
```

#### Tasks

1. Pass one validated `PlanStep`, resolved input artifacts, and template data
   requirements into each DataScience task.
2. Separate physical computation from interpretation:
   - Tool/SQL/Pandas performs calculations.
   - DataScience selects or requests transformations and interprets results.
3. Return `DataStepResult` containing:
   - Status and step ID.
   - Step-result artifact.
   - Analysis summary.
   - Aggregated metrics.
   - Zero or more `ChartDataset` objects.
   - Warnings and lineage.
4. Shape chart datasets according to template constraints:
   - Projection.
   - Filtering.
   - Aggregation.
   - Time-grain selection.
   - Top-K plus optional `Other`.
   - Binning.
   - Sampling/downsampling.
5. Store large chart datasets as artifacts and expose only profile/sample
   inline.
6. Ensure a metric is computed once and reused by narrative and charts.
7. Handle empty or insufficient data explicitly.

#### Tests

- A step consumes an upstream step artifact through its input ref.
- KPI and chart data remain numerically consistent.
- Top-K and `Other` preserve totals.
- Time-series downsampling preserves chronological order.
- Empty data returns a completed no-data result or a structured failure based
  on policy.

#### Acceptance criteria

- Every required chart slot can resolve to a compatible chart dataset or a
  documented fallback.

### Phase 7 — ChartInputAssembler and ECharts ChartAgent

#### Target modules and resources

```text
src/data_intelligence_sdk/reporting/chart_input.py
src/data_intelligence_sdk/reporting/chart_agent.py
src/data_intelligence_sdk/reporting/chart_validation.py
.agents/skills/echarts-chart-agent/SKILL.md  # if repo-local is confirmed
```

#### Tasks

1. Resolve `TemplateInstance.chart_slot_id` to the correct chart dataset.
2. Validate semantic roles, required fields, units, cardinality, and point
   limits before invoking ChartAgent.
3. Skip the LLM call and return `insufficient_data` when input cannot satisfy
   the slot.
4. Invoke one stateless ChartAgent task per chart slot.
5. Let ChartAgent choose among the allowed types but preserve the template
   chart ID and intent.
6. Generate ECharts option as pure JSON.
7. Reject functions, arbitrary scripts, unapproved URLs, or unsupported ECharts
   features.
8. Validate axis types, encodings, series fields, units, legend behavior, and
   dataset references.
9. Retry only correctable structural failures.
10. Apply slot fallback after retry exhaustion.
11. Run independent chart tasks concurrently under a chart semaphore.

#### Chart ID rule

```text
{template_id}.{section_id}.{chart_slot_id}[.{instance_key}]
```

Random IDs may be used only for the containing report run, not for logical chart
identity.

#### Tests

- Multiple chart slots execute concurrently.
- A chart waits only for its declared dataset.
- An invalid ECharts option is rejected.
- JavaScript functions are rejected.
- Optional chart failure becomes table/message fallback.
- Chart IDs are deterministic.

#### Acceptance criteria

- ChartResult is safe, JSON-serializable, and traceable to one or more data
  artifacts.

### Phase 8 — Structured ReportAgent

#### Target modules

```text
src/data_intelligence_sdk/reporting/report_agent.py
src/data_intelligence_sdk/reporting/report_validation.py
```

#### Tasks

1. Change ReportAgent output from Markdown text to `StructuredReport` JSON.
2. Pass TemplateInstance, DataStepResults, and ChartResults as separate inputs.
3. Preserve template section and block order.
4. Insert content only into declared blocks.
5. Reference charts by deterministic chart ID.
6. Preserve source/artifact lineage for facts and metrics.
7. Mark missing optional blocks and fallbacks explicitly.
8. Validate structure before rendering.
9. Keep an offline deterministic fallback report builder.

#### Tests

- Output follows the TemplateInstance layout.
- Duplicate facts and charts are not emitted.
- Missing optional chart uses its declared fallback.
- Required missing content produces partial/failed status according to policy.
- Every chart reference resolves.

#### Acceptance criteria

- Report content is independent of HTML or Markdown concerns.

### Phase 9 — Renderer boundary

#### Target modules

```text
src/data_intelligence_sdk/reporting/renderers/base.py
src/data_intelligence_sdk/reporting/renderers/html.py
src/data_intelligence_sdk/reporting/renderers/markdown.py
```

#### Tasks

1. Define a Renderer protocol accepting `StructuredReport` and render options.
2. Implement HTML rendering with safe ECharts initialization.
3. Implement Markdown rendering with configurable chart fallback:
   - Image artifact when a chart-image renderer is available.
   - Table.
   - Link/placeholder.
4. Escape all narrative and label content for the target format.
5. Resolve value-format tokens such as currency and percentage through a
   whitelist.
6. Emit render warnings separately from analysis warnings.
7. Store large HTML output as an artifact when configured.
8. Decide whether Renderer lives inside ReportEngine or is invoked by the
   outer Synthesizer. Prefer ReportEngine returning structured output plus
   optional render artifacts while the outer pipeline preserves evidence.

#### Tests

- Snapshot-like assertions for stable HTML and Markdown fixtures.
- Escaping prevents script injection.
- Chart option JSON is embedded safely.
- Markdown fallback is deterministic.
- Renderer does not change metric values or narrative meaning.

#### Acceptance criteria

- The same StructuredReport can produce at least HTML and Markdown.

### Phase 10 — Observability and failure handling

#### Tasks

1. Record every agent invocation, task transition, method call, artifact, chart,
   fallback, and render result in `EngineRunContext`.
2. Use correlation fields:
   - `run_id`.
   - `plan_revision`.
   - `template_instance_id`.
   - `step_id`.
   - `chart_id`.
   - `artifact_id`.
3. Redact connection strings and sensitive sample values.
4. Define summary metadata for `EngineOutput` without embedding large objects.
5. Expose partial-report warnings to the final response.
6. Add metrics for negotiation iterations, task duration, concurrency, cache
   hits, retries, fallbacks, and token usage when available.

#### Acceptance criteria

- A report fact or chart can be traced back to its plan step, method call, and
  source artifact.

### Phase 11 — Integration and migration

#### Tasks

1. Extract the current agent classes from the monolithic `report.py` into the
   reporting modules incrementally.
2. Keep a compatibility facade named `ReportEngine`.
3. Add a feature flag or constructor option for `workflow_version="v1"|"v2"`
   during migration.
4. Run v1 and v2 against the same deterministic fixtures and compare:
   - Completed steps.
   - Metrics.
   - Evidence.
   - User-visible content.
5. Update `examples/basic_workflow.py` to demonstrate template selection and
   render-format selection.
6. Add one full offline example using the repository data corpus package.
7. Make v2 the default only after compatibility and failure-path tests pass.
8. Remove v1 after a documented deprecation window.

#### Acceptance criteria

- Existing public engine construction remains valid.
- No live external services are needed for the test suite.
- The example produces a structured report and at least one rendered format.

## 7. Recommended implementation sequence

Follow this order because each milestone creates stable inputs for the next:

1. Template JSON/schema and package resources.
2. Report contracts and TemplatePool loader.
3. Plan output references and DAG validation.
4. TemplateAgent and bounded negotiation loop.
5. Unified Tool Executor.
6. Data artifact/profile/sample runtime.
7. DataScience `DataStepResult` and chart datasets.
8. In-process DAG Scheduler.
9. ChartInputAssembler.
10. ECharts ChartAgent skill, agent, and validator.
11. Structured ReportAgent.
12. HTML and Markdown renderers.
13. Full tracing, failure tests, examples, and migration flag.

The scheduler can be prototyped earlier, but it should not become the default
until data references and task-result contracts are stable.

## 8. Parallel work opportunities

After contracts are frozen, these workstreams can proceed independently:

```text
TemplatePool loader ----+
                        +--> Plan/Template negotiation
Plan contracts ---------+

Artifact Store ---------+
Profiler/Sampler -------+--> DataScience result contract
Tool Executor ----------+

Chart skill ------------+
Chart validator --------+--> ChartAgent integration

Structured report ------+
HTML renderer ----------+--> Report/Renderer integration
Markdown renderer ------+
```

Do not parallelize components that are still negotiating their shared JSON
contract; that creates avoidable rework.

## 9. Test strategy

### Unit tests

- Template loading and validation.
- DAG validation and scheduling.
- Route and tool execution normalization.
- Data profiling, sampling, aggregation, and chart shaping.
- Chart input matching and ECharts validation.
- Structured report validation.
- Renderer escaping and fallbacks.

### Contract tests

- Agent prompt output parses into the declared contract.
- Every template requirement reference exists.
- Every TemplateInstance binding resolves to a plan output.
- Every ChartRequest resolves to a compatible ChartDataset.
- Every report chart block resolves to a ChartResult or fallback.

### Integration scenarios

1. Full data with executive template and two charts.
2. Missing optional time dimension with a table/message fallback.
3. Template asks for feasible missing data and PlanAgent adds a task.
4. Template asks for impossible required data and negotiation fails cleanly.
5. Existing tool succeeds.
6. Existing tool fails and generated fallback is attempted when enabled.
7. Generated tool fails validation repeatedly and stops at its limit.
8. Independent DataScience and chart tasks overlap.
9. One optional chart fails while the report completes partially.
10. Empty corpus produces a deterministic no-data report.

### Packaging tests

- Build a wheel.
- Inspect the wheel for manifest, schema, and all built-in templates.
- Install the wheel in an isolated environment.
- Load every template through `importlib.resources`.

## 10. Rollout checkpoints

### Milestone A — Template-ready

- Built-in pool is packaged.
- Loader and contracts are stable.
- Template selection works without modifying ReportEngine execution.

### Milestone B — Data-aware execution

- Named outputs and data dependencies work.
- Tool results become artifacts with profiles and samples.
- DataScience emits DataStepResult.

### Milestone C — Parallel charts

- Chart datasets bind to template slots.
- Independent ECharts tasks run concurrently.
- Invalid or missing chart inputs fall back safely.

### Milestone D — Structured report

- ReportAgent emits validated JSON.
- HTML and Markdown renderers produce deterministic outputs.

### Milestone E — Production hardening

- Observability, redaction, timeouts, retries, and partial failures are covered.
- v2 passes integration and packaging tests and becomes the default.

## 11. Definition of done

The new report workflow is complete when:

- TemplateAgent selects and adapts a packaged template.
- PlanAgent and TemplateAgent converge within a bounded loop.
- Data dependencies pass real upstream artifacts to downstream steps.
- Independent work runs concurrently under configured limits.
- Existing and generated tools share one execution contract.
- Large datasets remain outside LLM context and trace metadata.
- Every required chart resolves to safe ECharts JSON or an explicit fallback.
- ReportAgent produces a validated StructuredReport.
- HTML and Markdown can be rendered from the same report object.
- Partial and failed states are user-visible and auditable.
- All artifacts, facts, and charts have lineage in EngineTrace/EvidenceBundle.
- Unit, integration, failure-path, and wheel packaging tests pass without live
  external calls.

## 12. Immediate next implementation slice

The first code slice after this plan should be deliberately small:

1. Add report template dataclasses/contracts.
2. Implement the `importlib.resources` TemplatePool loader.
3. Validate manifest references and cross-references in all three templates.
4. Add TemplatePool unit tests and wheel-content test.
5. Add a read-only TemplateAgent selector that returns a cloned
   TemplateInstance but does not yet modify the existing ReportEngine flow.

This slice provides a stable foundation and can be merged without introducing
concurrency or changing current report output behavior.
