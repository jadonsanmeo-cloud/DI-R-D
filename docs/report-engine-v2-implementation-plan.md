# Report Engine v2 implementation plan

## 1. Objective

Evolve the current sequential `ReportEngine` into a template-driven workflow
with explicit contracts, bounded Plan/Template negotiation, dependency-aware
execution, controlled concurrency, traceable data outputs, parallel chart
generation, structured report output, and separate rendering.

The target workflow is:

```text
User goal + DataCorpusPackage
        |
        v
PlanAgent <------> TemplateAgent <------> TemplatePool
        |                  |
        |                  +---- TemplateInstance
        +---- FinalPlan ----------+
                                  |
                                  v
                             DAG Scheduler
                                  |
                    +-------------+-------------+
                    |                           |
              existing method             generated method
                    |               Code -> Sandbox -> Validator
                    +----------- ToolExecutor --------+
                                                   |
                                                   v
                                          DataStepProcessor
                                  +----------------+----------------+
                                  |                                 |
                          deterministic runtime                AnalysisAgent
                    artifact/schema/profile/sample/metrics     bounded summary
                                  |                                 |
                                  +------------ DataStepResult -----+
                                                   |
                         +-------------------------+------------------+
                         |                                            |
                         v                                            v
                  ChartInputAssembler                            ReportAgent
                         |                                            ^
                 parallel ChartRequests                              |
                         |                                            |
                    ChartAgent --------------------------------------+
                         |
                    ChartResults
                         |
                         +-----------------------> ReportAgent
                                                     |
                                              StructuredReport JSON
                                                     |
                                                  Renderer
                                               HTML / Markdown
```

The workflow uses JSON objects for agent and runtime contracts. JSONL is
reserved for event streams and trace records; it is not the report contract.

## 2. Decisions fixed by this plan

1. `NegotiationRunner`, not either agent, owns Plan/Template loop state.
2. PlanAgent and TemplateAgent are stateless from the workflow perspective.
3. Every PlanAgent revision response contains a complete `ReportPlan` snapshot.
4. NegotiationRunner sends PlanAgent the current plan and a batch of
   `MissingDataRequest` objects, not the entire template layout.
5. PlanAgent returns one `RequestResolution` for every missing-data request.
6. One template requirement may bind to one or more named plan outputs.
7. Canonical template files are immutable. A run modifies only a cloned
   `TemplateInstance`.
8. DAG Scheduler decides when a step may run; RouterAgent decides how one ready
   step should run.
9. Existing and generated methods execute through one `ToolExecutor` contract.
10. Data Runtime and the old DataScience stage are exposed as one workflow
    component named `DataStepProcessor`.
11. Deterministic code owns persistence, schema, profiling, sampling, metrics,
    and data shaping. `AnalysisAgent` only interprets bounded verified context.
12. `DataStepResult` publishes generic `data_outputs`, not chart-owned datasets.
13. `ChartInputAssembler` may resolve multiple step outputs for one chart.
14. Business joins, ratios, aggregations, and derived metrics require a PlanStep;
    neither ChartInputAssembler nor ChartAgent may calculate them.
15. ChartAgent creates safe ECharts presentation JSON only.
16. ReportAgent emits `StructuredReport` JSON. Renderer owns HTML and Markdown.
17. Every loop, retry path, and concurrency pool has a configured limit.

## 3. Scope boundaries

### Included

- A versioned built-in TemplatePool with optional application overrides.
- Template selection, cloning, adaptation, binding, and bounded negotiation.
- Versioned ReportPlan DAGs with named inputs and outputs.
- In-process dependency scheduling with bounded concurrency.
- Existing-tool and generated-tool execution through ToolExecutor.
- Inline and artifact-backed data outputs.
- Deterministic schema, profile, sample, metric, and lineage generation.
- A bounded AnalysisAgent summary for each completed data step.
- Multi-output and multi-step chart bindings.
- Independent ECharts ChartAgent tasks.
- StructuredReport, HTML renderer, and Markdown renderer.
- Required/optional failure policies and auditable partial reports.

### Deferred

- Distributed scheduling across processes or machines.
- A visual template editor.
- Persisting adapted TemplateInstances into the canonical pool.
- Live LLM and database calls in unit tests.
- Arbitrary JavaScript callbacks inside ECharts options.
- Automatic workflow replanning after runtime data-quality failures. Runtime
  failures use explicit fallback policies in v2; they do not re-enter template
  negotiation.

## 4. Current baseline and migration constraints

The current `ReportEngine` already has Plan, Router, Code, Sandbox, Validator,
MethodHub, DataScience, and Report stages. It currently sorts plan steps and
executes them sequentially. Tool results are passed inline to DataScienceAgent,
and ReportAgent emits Markdown directly.

The migration must preserve:

- `EngineOutput` and `EngineTrace` as the public engine boundary.
- `EngineRuntimeContext` as the runtime-service boundary.
- Existing MethodHub registration, evidence, and trust levels.
- Offline deterministic behavior when no LLM is configured.
- Existing public engine construction during the migration window.
- Existing tests until assertions are intentionally migrated to v2 contracts.

Known gaps to close:

- `depends_on` controls ordering but does not bind real upstream outputs.
- Existing-tool failures collapse to `[]` instead of a structured failure.
- Generated tools do not share a complete execution request with existing tools.
- Large results can enter prompts and trace metadata directly.
- DataScienceAgent duplicates deterministic profiling work.
- There is no run-local TemplateInstance or Plan/Template negotiation runtime.
- There is no chart assembly contract, StructuredReport, or Renderer boundary.

## 5. Common contract conventions

All persisted objects and all objects exchanged with an LLM use an explicit
`schema_version`. Stable IDs are required for plans, steps, requirements,
outputs, artifacts, charts, sections, blocks, and report runs.

The common status vocabulary is:

```text
pending | ready | running | completed | partial | failed | skipped
```

Where useful, contracts include this envelope:

```json
{
  "schema_version": "1.0",
  "run_id": "run-123",
  "status": "completed",
  "warnings": [],
  "errors": [],
  "lineage": {}
}
```

Rules:

- `warnings` describe recoverable degradation.
- `errors` contain structured codes and messages, not raw exceptions only.
- Full connection strings, secrets, and sensitive sample values are redacted.
- Large row sets never appear in trace metadata or LLM prompts.
- An artifact reference is not sufficient by itself; its schema, row count, and
  bounded preview remain available to downstream deterministic code.

## 6. Core contracts

Implement the contracts under `data_intelligence_sdk/reporting/contracts.py` as
dataclasses or typed models with explicit `to_dict`, `from_dict`, and validation.

### 6.1 ReportPlan and PlanStep

PlanAgent returns a complete, versioned plan snapshot:

```json
{
  "schema_version": "1.0",
  "plan_id": "plan-abc",
  "revision": 2,
  "objective": "Create a performance report",
  "steps": [
    {
      "step_id": "step-1",
      "task": "Calculate c grouped by a",
      "required": true,
      "depends_on": [],
      "inputs": [
        {
          "kind": "corpus_dataset",
          "dataset_id": "table-abc",
          "columns": ["a", "c"]
        }
      ],
      "operation": {
        "capability": "group_by_sum",
        "parameters": {
          "group_by": ["a"],
          "measure": "c"
        }
      },
      "outputs": [
        {
          "output_name": "c-by-a",
          "shape": "time_series",
          "semantic_roles": {
            "time": "a",
            "measure": "c"
          },
          "expected_schema": {
            "a": "date",
            "c": "number"
          }
        }
      ]
    }
  ]
}
```

An upstream input uses a named output reference:

```json
{
  "kind": "step_output",
  "step_id": "step-1",
  "output_name": "c-by-a",
  "required": true
}
```

Plan validation requires:

- Unique step IDs and output names within each step.
- Every dependency and step-output reference exists.
- Every step-output input also appears in `depends_on`.
- No cycles.
- Required source columns exist in the corpus catalog when statically known.
- Every step has at least one named output.

### 6.2 Template contracts

`TemplateDefinition` is the immutable payload loaded from the TemplatePool. It
contains selection metadata, data requirements, sections, blocks, chart slots,
layout, constraints, and fallback rules.

`TemplateInstance` is the run-local clone:

```json
{
  "schema_version": "1.0",
  "template_instance_id": "run-123.executive-overview",
  "template_id": "executive-overview",
  "template_version": "1.0.0",
  "revision": 2,
  "sections": [],
  "bindings": [],
  "fallback_states": [],
  "status": "partial"
}
```

A binding may resolve one requirement to multiple outputs:

```json
{
  "requirement_id": "c-d-comparison",
  "output_refs": [
    {"step_id": "step-1", "output_name": "c-by-a"},
    {"step_id": "step-2", "output_name": "d-by-a"}
  ],
  "status": "bound"
}
```

`MissingDataRequest` contains computation-relevant information only:

```json
{
  "request_id": "missing-primary-trend",
  "requirement_id": "primary-trend",
  "required": false,
  "expected_shape": "time_series",
  "semantic_roles": {
    "time": "time",
    "measure": "primary_measure"
  },
  "constraints": {
    "max_rows": 36
  },
  "reason": "No compatible plan output exists"
}
```

It does not include section layout, colors, titles, or unrelated blocks.

### 6.3 Plan revision response

For the initial call, `request_resolutions` is empty. For every later call,
PlanAgent returns one resolution for every request in the input batch:

```json
{
  "schema_version": "1.0",
  "iteration": 2,
  "plan": {
    "plan_id": "plan-abc",
    "revision": 2,
    "steps": []
  },
  "request_resolutions": [
    {
      "request_id": "missing-primary-trend",
      "requirement_id": "primary-trend",
      "decision": "added",
      "output_refs": [
        {"step_id": "step-2", "output_name": "c-by-a"}
      ],
      "reason": null
    },
    {
      "request_id": "missing-secondary-measure",
      "requirement_id": "secondary-measure",
      "decision": "rejected",
      "output_refs": [],
      "reason": "No compatible source column exists"
    }
  ]
}
```

Allowed decisions are `already_satisfied`, `added`, `revised`, and `rejected`.
The revised plan is the source of truth; `output_refs` are hints that must still
be validated against the returned plan.

### 6.4 NegotiationResult

```json
{
  "schema_version": "1.0",
  "iteration": 2,
  "plan": {},
  "template_instance": {},
  "missing_data_requests": [],
  "status": "accepted",
  "stop_reason": "all_required_bindings_resolved",
  "revision_hash": "sha256:...",
  "warnings": []
}
```

Negotiation statuses are `needs_plan_revision`, `accepted`, `partial`, and
`failed`.

### 6.5 Route and tool execution contracts

RouterAgent returns a decision only:

```json
{
  "schema_version": "1.0",
  "step_id": "step-1",
  "route": "existing_tool",
  "tool_name": "group_by_sum",
  "arguments": {
    "dataset": "table-abc",
    "group_by": "a",
    "column": "c"
  },
  "reason": "The registered method satisfies the required capability"
}
```

Allowed routes are `existing_tool`, `generated_tool`, and `unavailable`.

ToolExecutor accepts both existing and validated generated methods through the
same request:

```json
{
  "schema_version": "1.0",
  "execution_id": "exec-123",
  "step_id": "step-1",
  "tool_name": "group_by_sum",
  "arguments": {},
  "input_refs": [],
  "expected_outputs": [
    {"output_name": "c-by-a", "shape": "time_series"}
  ],
  "policy": {
    "timeout_seconds": 30,
    "max_attempts": 1
  }
}
```

It returns raw named values before DataStepProcessor persistence:

```json
{
  "schema_version": "1.0",
  "execution_id": "exec-123",
  "step_id": "step-1",
  "status": "completed",
  "tool_name": "group_by_sum",
  "outputs": {
    "c-by-a": [
      {"a": "2026-01-01", "c": 10},
      {"a": "2026-01-02", "c": 20}
    ]
  },
  "duration_ms": 18,
  "warnings": [],
  "errors": []
}
```

### 6.6 DataStepResult and DataOutput

`DataStepProcessor` converts ToolExecutionResult into one validated result:

```json
{
  "schema_version": "1.0",
  "step_id": "step-1",
  "status": "completed",
  "analysis_summary": "Column c increased from 10 to 20.",
  "data_outputs": [
    {
      "output_name": "c-by-a",
      "shape": "time_series",
      "data": {
        "mode": "inline",
        "value": [
          {"a": "2026-01-01", "c": 10},
          {"a": "2026-01-02", "c": 20}
        ]
      },
      "schema": {
        "a": "date",
        "c": "number"
      },
      "profile": {
        "row_count": 2,
        "null_counts": {"a": 0, "c": 0},
        "numeric": {
          "c": {"min": 10, "max": 20, "mean": 15}
        }
      },
      "semantic_roles": {
        "time": "a",
        "measure": "c"
      }
    }
  ],
  "aggregated_metrics": {
    "total_c": 30,
    "average_c": 15
  },
  "warnings": [],
  "lineage": {
    "plan_step_id": "step-1",
    "execution_id": "exec-123",
    "tool_name": "group_by_sum",
    "source_refs": ["corpus://table-abc"]
  }
}
```

Large output uses the same `data` field with artifact mode:

```json
{
  "mode": "artifact",
  "artifact_ref": "artifact://run-123/step-1/c-by-a",
  "format": "parquet",
  "row_count": 100000,
  "preview": [
    {"a": "2026-01-01", "c": 10}
  ]
}
```

Exactly one of `value` or `artifact_ref` is present. DataOutput is a generic
step output and is not owned by a chart.

### 6.7 Chart assembly contracts

ChartInputAssembler returns one request per ready chart slot plus structured
failures for slots that cannot run:

```json
{
  "schema_version": "1.0",
  "requests": [
    {
      "chart_id": "executive.trend.c-d-comparison",
      "chart_slot_id": "c-d-comparison",
      "intent": "Compare c and d over a",
      "suggested_type": "line",
      "allowed_types": ["line", "bar"],
      "datasets": [
        {
          "dataset_id": "step-1.c-by-a",
          "source_output_ref": {
            "step_id": "step-1",
            "output_name": "c-by-a"
          },
          "artifact_ref": null,
          "rows": [
            {"a": "2026-01-01", "c": 10},
            {"a": "2026-01-02", "c": 20}
          ],
          "encoding": {
            "x_field": "a",
            "y_field": "c",
            "series_name": "c"
          }
        },
        {
          "dataset_id": "step-2.d-by-a",
          "source_output_ref": {
            "step_id": "step-2",
            "output_name": "d-by-a"
          },
          "artifact_ref": null,
          "rows": [
            {"a": "2026-01-01", "d": 15},
            {"a": "2026-01-02", "d": 15}
          ],
          "encoding": {
            "x_field": "a",
            "y_field": "d",
            "series_name": "d"
          }
        }
      ],
      "constraints": {
        "max_points": 36,
        "sort": "chronological"
      },
      "presentation": {
        "title": "c and d over a",
        "show_legend": true
      },
      "fallback": {"action": "table"},
      "lineage": {
        "source_output_refs": ["step-1.c-by-a", "step-2.d-by-a"]
      }
    }
  ],
  "failures": []
}
```

ChartResult contains safe ECharts JSON:

```json
{
  "schema_version": "1.0",
  "chart_id": "executive.trend.c-d-comparison",
  "status": "completed",
  "chart_type": "line",
  "echarts_option": {},
  "source_output_refs": ["step-1.c-by-a", "step-2.d-by-a"],
  "warnings": []
}
```

### 6.8 StructuredReport and RenderedReport

ReportAgent receives TemplateInstance, DataStepResults, ChartResults, user goal,
and evidence references as separate fields. It returns blocks in template order:

```json
{
  "schema_version": "1.0",
  "report_id": "report-123",
  "status": "completed",
  "title": "Performance overview",
  "sections": [
    {
      "section_id": "summary",
      "blocks": [
        {
          "block_id": "headline",
          "type": "narrative",
          "content": "...",
          "source_refs": ["step-1.c-by-a"]
        },
        {
          "block_id": "trend",
          "type": "chart",
          "chart_id": "executive.trend.c-d-comparison"
        }
      ]
    }
  ],
  "warnings": []
}
```

Renderer returns:

```json
{
  "schema_version": "1.0",
  "report_id": "report-123",
  "format": "html",
  "status": "completed",
  "content": "<html>...</html>",
  "artifact_ref": null,
  "warnings": []
}
```

Large rendered content may use `artifact_ref` instead of inline `content`.

## 7. End-to-end processing logic

### 7.1 Plan/Template negotiation

NegotiationRunner owns this state:

```text
current_plan
template_instance
iteration
revision history
previous revision hash
```

Algorithm:

1. Call PlanAgent with user goal, corpus catalog, source descriptors, capability
   descriptors, and no missing requests.
2. Validate the complete ReportPlan revision.
3. Give TemplateAgent the user goal, complete plan, corpus catalog, and template
   descriptors.
4. Select and clone one TemplateDefinition into a TemplateInstance.
5. Bind every requirement used by a section, block, or chart slot to compatible
   named plan outputs.
6. Emit all unresolved requirements in one `missing_data_requests` array.
7. If no required request remains unresolved, accept or return partial with
   explicit optional fallbacks.
8. Otherwise call PlanAgent with the complete current plan, the whole missing
   request batch, corpus catalog, and capability descriptors.
9. Validate that the response contains a complete higher plan revision and one
   resolution for every request.
10. Give TemplateAgent the revised plan, current TemplateInstance, and request
    resolutions. Rebind from validated plan outputs; do not trust resolution
    output refs without validation.
11. Increase TemplateInstance revision and record the decision.
12. Stop on accepted, partial, fatal requirement failure, unchanged revision
    hash, or configured iteration limit.

Negotiation invariants:

- Plan and TemplateInstance revisions increase monotonically.
- Canonical TemplateDefinition content never changes.
- Every binding resolves to an existing named output.
- A rejected optional request activates its declared fallback.
- A rejected required request fails negotiation unless its requirement defines
  an accepted required fallback.
- The same plan/template hash cannot start another iteration.

### 7.2 DAG scheduling

Scheduler validates the FinalPlan before execution and maintains:

```text
pending -> ready -> running -> completed
                           -> failed
pending ------------------> skipped
```

Algorithm:

1. Build dependency and reverse-dependency indexes.
2. Mark steps with no unresolved hard dependencies as ready.
3. Dispatch ready steps while the data-task semaphore has capacity.
4. Resolve corpus and upstream output references before routing.
5. Execute one step through Router, optional code generation, ToolExecutor, and
   DataStepProcessor.
6. Register every DataOutput in a run-local output index using
   `{step_id}.{output_name}`.
7. Mark downstream steps ready as soon as all required dependencies complete.
8. Skip consumers of failed required inputs; preserve optional-input warnings.
9. Notify chart scheduling whenever a new bound output becomes available.
10. On cancellation or timeout, move every started task to a terminal state.

Scheduler is an in-process dispatcher and resource coordinator, not a
distributed load balancer. Router still owns tool selection.

### 7.3 Routing and generated methods

For each ready step:

1. Router receives one PlanStep, resolved input descriptors, and MethodHub
   descriptors. It does not execute a method.
2. For `existing_tool`, build ToolExecutionRequest and call ToolExecutor.
3. For `generated_tool`, CodeAgent generates a complete interface and source.
4. Sandbox runs structural and sample validation.
5. ValidatorAgent returns pass/fail and actionable feedback.
6. Correctable failures return to CodeAgent until the configured maximum.
7. Register a validated generated method with its trust evidence.
8. Execute it through the same ToolExecutor path as an existing method.
9. Convert every exception into a structured ToolExecutionResult.

An existing-method failure may fall back to code generation only once and only
when the configured policy and PlanStep permit generated code.

### 7.4 DataStepProcessor

DataStepProcessor is one public workflow stage with internal deterministic
helpers and an optional AnalysisAgent:

```text
DataStepProcessor
|-- DataOutputStore
|-- SchemaInferrer
|-- DataProfiler
|-- DataSampler
|-- MetricCalculator
|-- OutputValidator
`-- AnalysisAgent
```

Algorithm:

1. Match ToolExecutionResult named values to PlanStep output definitions.
2. Validate expected output names and basic shapes.
3. Persist large outputs; retain small outputs inline according to policy.
4. Generate deterministic schema, profile, sample, and lineage.
5. Compute only metrics explicitly requested by the PlanStep operation or
   template-bound data requirement. Do not invent business metrics.
6. Build a bounded AnalysisAgent input from step description, verified metrics,
   schema, profile, and redacted sample.
7. Ask AnalysisAgent for interpretation, caveats, and concise summary only.
8. Validate the agent response and combine it with deterministic data into one
   DataStepResult.
9. If no LLM is configured, create a deterministic summary from verified
   metrics and profile.

AnalysisAgent must not:

- Call tools or execute code.
- Recalculate metrics from sample rows.
- Create chart datasets or ECharts options.
- Modify data artifacts.

### 7.5 ChartInputAssembler

ChartInputAssembler is deterministic and has no LLM. It receives:

```text
TemplateInstance + TemplateBindings + DataStepResults + ArtifactStore
```

Suggested structure:

```python
class ChartInputAssembler:
    def assemble_ready(
        self,
        template_instance,
        bindings,
        output_index,
        completed_chart_ids,
    ) -> ChartAssemblyResult:
        ...

    def _collect_chart_slots(self, template_instance): ...
    def _resolve_bindings(self, chart_slot, bindings): ...
    def _resolve_output(self, output_ref, output_index): ...
    def _load_bounded_rows(self, data_output): ...
    def _validate_shape_and_roles(self, chart_slot, outputs): ...
    def _build_chart_request(self, chart_slot, outputs): ...
```

Algorithm for each chart slot:

1. Resolve `data_requirement_refs` to TemplateBindings.
2. Resolve every binding output ref through the run output index.
3. Wait if a required producer step is still non-terminal.
4. Return `insufficient_data` without an LLM call when a required terminal
   output is unavailable or incompatible.
5. Read inline values or bounded artifact rows.
6. Validate schema, shape, semantic roles, units, cardinality, and point limit.
7. Map semantic roles to concrete fields and series names.
8. Build one ChartRequest containing only data needed by that chart slot.
9. Preserve all source output and artifact references in lineage.

Allowed deterministic presentation operations:

- Field projection and safe label renaming.
- Declared chronological or categorical sorting.
- Policy-approved deterministic downsampling with a warning.
- Validation and bounded data loading.

Disallowed operations:

- Business aggregation, joins, ratios, growth calculations, and new metrics.
- Guessing missing semantic roles.
- Combining incompatible populations.

If a chart needs `e = c - d`, PlanAgent must add a downstream PlanStep that
depends on the outputs containing c and d. ChartInputAssembler then consumes
that derived step output.

### 7.6 Chart execution

1. Scheduler or ChartTaskRunner receives valid ChartRequests.
2. Run independent requests concurrently under the chart semaphore.
3. Invoke one stateless ChartAgent task per logical chart ID.
4. Validate returned ECharts option as pure JSON.
5. Reject functions, scripts, unsupported URLs, and invalid field encodings.
6. Retry only correctable structural failures.
7. Apply the chart slot fallback after retry exhaustion.

Chart ID format:

```text
{template_id}.{section_id}.{chart_slot_id}[.{instance_key}]
```

### 7.7 Report and render

ReportAgent receives TemplateInstance, DataStepResults, ChartResults, user goal,
and evidence references. It must:

1. Preserve template section and block order.
2. Insert content only into declared blocks.
3. Use deterministic metrics as the source of numeric facts.
4. Reference charts by deterministic chart ID.
5. Preserve source refs for facts, tables, recommendations, and charts.
6. Mark missing optional blocks and fallbacks explicitly.
7. Return JSON matching the StructuredReport schema.

Renderer receives the same StructuredReport for HTML or Markdown. It escapes
all text, embeds safe ECharts JSON for HTML, and applies deterministic table or
message fallbacks for Markdown charts.

## 8. Concurrency and resource policy

Initial configurable defaults:

| Resource | Default maximum |
| --- | ---: |
| Plan/Template negotiation calls | 1 |
| Router calls | 4 |
| Data step executions | 4 |
| Code generation calls | 1 |
| Sandbox executions | 1 |
| AnalysisAgent calls | 4 |
| ChartAgent calls | 2 |
| Plan/Template iterations | 3 |
| Generated-code attempts | 2 |
| Chart structural retries | 1 |

Additional limits:

- Per-source database concurrency.
- Tool timeout and maximum result bytes.
- Maximum inline rows and bytes.
- Profile and sample row/byte limits.
- Maximum chart points per slot.
- Global LLM request concurrency and token budget when available.

Independent existing-tool and generated-tool branches may overlap, but code
generation and sandbox work use their own smaller semaphores.

## 9. Failure transitions

| Failure | Transition |
| --- | --- |
| Invalid initial plan | Retry PlanAgent, then fail negotiation |
| Missing optional template data | Activate fallback and return partial |
| Missing required template data | Revise plan or fail negotiation |
| Same negotiation revision hash | Stop with partial/failed result |
| Existing method unavailable | Generated route if policy permits |
| Existing method execution failure | One generated fallback if permitted |
| Generated code validation failure | Retry CodeAgent to configured limit |
| Required DAG dependency failure | Skip dependent step |
| Optional DAG input failure | Continue with warning when contract permits |
| DataOutput shape mismatch | Fail step; do not pass invalid output downstream |
| AnalysisAgent failure | Use deterministic summary and warning |
| Chart input insufficient | Skip LLM and apply chart fallback |
| Invalid ECharts JSON | Structural retry, then fallback |
| Optional chart failure | Partial report |
| Required report block unresolved | Partial or failed according to template |
| Renderer failure | Preserve StructuredReport and return render error |

## 10. Target modules

```text
src/data_intelligence_sdk/reporting/
|-- contracts.py
|-- configuration.py
|-- planning.py
|-- template_pool.py
|-- template_agent.py
|-- negotiation.py
|-- scheduler.py
|-- task_state.py
|-- routing.py
|-- data_step_processor.py
|-- analysis_agent.py
|-- chart_input.py
|-- chart_agent.py
|-- chart_validation.py
|-- report_agent.py
|-- report_validation.py
`-- renderers/
    |-- base.py
    |-- html.py
    `-- markdown.py

src/data_intelligence_sdk/runtime/
|-- tool_executor.py
|-- data_artifacts.py
|-- data_profiler.py
`-- data_sampler.py
```

`DataStepProcessor` is the public stage. Runtime helpers remain separate small
classes/functions for deterministic testing and reuse; they are not additional
workflow agents.

## 11. Phase-by-phase implementation

### Phase 0 - Freeze contracts and configuration

Tasks:

1. Implement all contract models and JSON serialization.
2. Implement contract validators and stable error codes.
3. Add ReportWorkflowConfig with every loop, retry, timeout, artifact, sample,
   and concurrency limit.
4. Freeze required/optional failure policy and generated fallback policy.

Acceptance:

- Every example payload in this document parses and validates.
- No agent consumes an unvalidated dictionary.
- No loop or retry uses a hard-coded limit outside configuration defaults.

### Phase 1 - TemplatePool foundation

Tasks:

1. Load packaged resources with `importlib.resources`.
2. Validate manifest, schema version, requirement references, block references,
   and chart-slot references.
3. Expose lightweight descriptors without loading every template body.
4. Support application templates with explicit precedence.
5. Return immutable TemplateDefinitions.

Acceptance:

- Built-in templates load from an installed wheel.
- Canonical definitions cannot be mutated by a run.

### Phase 2 - Plan/Template negotiation

Tasks:

1. Upgrade PlanAgent output to ReportPlan and PlanRevisionResponse.
2. Implement named PlanStep inputs and outputs.
3. Implement TemplateAgent selection and cloning.
4. Implement one-to-many TemplateBindings.
5. Implement batched MissingDataRequests and RequestResolutions.
6. Implement NegotiationRunner, revision history, hash progress detection, and
   configured iteration limit.
7. Record every revision and decision in EngineRunContext.

Acceptance:

- Execution receives exactly one validated FinalPlan and TemplateInstance.
- Mixed feasible and infeasible missing requests resolve in one iteration.
- Agents do not depend on hidden in-memory plan state.

### Phase 3 - DAG Scheduler

Tasks:

1. Validate dependencies, output refs, and cycles.
2. Implement node states and ready queue.
3. Resolve upstream DataOutput refs before step dispatch.
4. Implement bounded asyncio execution and cancellation.
5. Implement required/optional dependency failure propagation.
6. Emit output-ready events for chart scheduling.

Acceptance:

- Independent steps overlap under a controlled test.
- Dependent steps never start before required outputs exist.

### Phase 4 - Router and ToolExecutor

Tasks:

1. Make RouterAgent return validated RouteDecision only.
2. Introduce ToolExecutionRequest and ToolExecutionResult.
3. Route existing methods through ToolExecutor.
4. Route validated generated methods through ToolExecutor.
5. Centralize argument validation, trust checks, timeout, retry, tracing,
   exception normalization, and named output validation.
6. Implement the configured existing-to-generated fallback.

Acceptance:

- Existing and generated methods return identical result shapes.
- No RouterAgent or AnalysisAgent directly invokes MethodHub callables.

### Phase 5 - DataStepProcessor

Tasks:

1. Implement inline/artifact DataOutput storage.
2. Implement deterministic schema, profile, sample, and redaction.
3. Implement metric calculation from declared operations only.
4. Implement AnalysisAgent with bounded verified context.
5. Add deterministic no-LLM summary.
6. Return validated DataStepResult and register outputs in the run index.

Acceptance:

- Metrics are computed once and reused by report and charts.
- Large data remains outside prompts and trace metadata.
- AnalysisAgent cannot alter deterministic values.

### Phase 6 - ChartInputAssembler

Tasks:

1. Resolve chart slots through requirements and bindings.
2. Support one or many source output refs.
3. Load bounded inline/artifact data.
4. Validate shapes, roles, units, cardinality, and point limits.
5. Build one minimal ChartRequest per ready slot.
6. Return structured failures without an LLM call for insufficient data.
7. Preserve source output and artifact lineage.

Acceptance:

- One chart can consume outputs from multiple independent steps.
- A derived metric cannot be created inside ChartInputAssembler.

### Phase 7 - ChartAgent and validation

Tasks:

1. Invoke one stateless task per ChartRequest.
2. Generate pure JSON ECharts options.
3. Validate chart types, axes, series, encodings, legends, units, and URLs.
4. Reject executable functions and arbitrary scripts.
5. Implement bounded structural retry and template fallback.
6. Run independent requests concurrently.

Acceptance:

- ChartResults are safe, deterministic in identity, and traceable to all source
  outputs.

### Phase 8 - StructuredReport and renderers

Tasks:

1. Implement StructuredReport schema and validator.
2. Update ReportAgent to consume TemplateInstance, DataStepResults, and
   ChartResults separately.
3. Preserve block order, source refs, warnings, and fallback status.
4. Implement deterministic offline report builder.
5. Implement safe HTML/ECharts renderer.
6. Implement Markdown renderer with table/message chart fallback.

Acceptance:

- The same StructuredReport renders to HTML and Markdown.
- Renderer does not change metrics or narrative meaning.

### Phase 9 - Observability, migration, and hardening

Tasks:

1. Record agent calls, revisions, task transitions, executions, outputs, charts,
   fallbacks, and renders in EngineRunContext.
2. Add correlation fields for run, plan revision, template instance, step,
   execution, artifact, and chart IDs.
3. Add `workflow_version="v1"|"v2"` to ReportEngine during migration.
4. Compare v1 and v2 on deterministic fixtures.
5. Update examples to demonstrate template and render-format selection.
6. Build and inspect a wheel for all template resources.

Acceptance:

- Existing public construction remains valid.
- The full offline test suite needs no external services.
- v2 produces StructuredReport and at least one rendered format.

## 12. Test strategy

### Unit tests

- Contract serialization, validation, and error codes.
- Template loading, immutability, and cross-references.
- Plan DAG validation and output reference resolution.
- Negotiation batching, mixed resolutions, progress hash, and iteration limit.
- Scheduler state transitions, concurrency, cancellation, and failure propagation.
- Router decisions and ToolExecutor normalization.
- Inline/artifact storage, profiling, sampling, metrics, and redaction.
- AnalysisAgent input bounding and deterministic fallback.
- Chart binding, multi-output assembly, and insufficient-data handling.
- ECharts validation and function/script rejection.
- StructuredReport validation and renderer escaping.

### Contract tests

- Every agent response parses into its declared contract.
- Every TemplateBinding resolves to one or more real Plan outputs.
- Every PlanStep input resolves to corpus data or an upstream DataOutput.
- Every ToolExecutionResult output matches the PlanStep output definition.
- Every ChartRequest source resolves to a DataOutput.
- Every report chart block resolves to ChartResult or declared fallback.
- Every numeric report fact references a deterministic metric or DataOutput.

### Integration scenarios

1. Executive overview with headline metrics and two charts.
2. Missing optional time dimension activates a message fallback.
3. Two missing requests: one feasible and one rejected in the same revision.
4. Missing fatal requirement fails negotiation cleanly.
5. Independent existing-tool and generated-tool branches overlap safely.
6. Existing tool fails and uses generated fallback when policy permits.
7. Generated code repeatedly fails validation and stops at its limit.
8. A downstream step consumes two upstream DataOutputs.
9. One chart consumes outputs from two independent steps.
10. A derived chart metric is produced by an explicit downstream PlanStep.
11. Optional chart failure produces a partial report.
12. Empty corpus produces a deterministic no-data report.
13. Large output uses an artifact while prompts contain bounded context only.
14. The same StructuredReport renders to HTML and Markdown.

### Packaging tests

- Build a wheel.
- Inspect it for manifest, schema, and all built-in templates.
- Install it into an isolated environment.
- Load every template through `importlib.resources`.

## 13. Recommended implementation order

1. Contracts and ReportWorkflowConfig.
2. TemplatePool loader and immutable definitions.
3. ReportPlan named outputs and DAG validation.
4. TemplateAgent and NegotiationRunner.
5. Scheduler and run-local output index.
6. Router contract and unified ToolExecutor.
7. DataOutput storage and DataStepProcessor.
8. ChartInputAssembler with multi-output support.
9. ChartAgent and ECharts validator.
10. StructuredReport and report validation.
11. HTML and Markdown renderers.
12. Tracing, examples, failure tests, packaging tests, and migration flag.

Do not parallelize components that are still negotiating a shared contract.
After contracts are frozen, TemplatePool, ToolExecutor, deterministic data
helpers, chart validation, and renderers can be implemented independently.

## 14. Rollout checkpoints

### Milestone A - Template-ready

- Contracts and configuration are stable.
- Built-in templates load from package resources.
- Plan/Template negotiation returns FinalPlan and TemplateInstance.

### Milestone B - Data-aware execution

- Named inputs and outputs carry real lineage.
- Scheduler runs independent steps concurrently.
- Existing and generated tools share ToolExecutor.
- DataStepProcessor emits validated DataStepResults.

### Milestone C - Parallel charts

- ChartInputAssembler resolves one or multiple outputs per slot.
- Independent ChartAgent tasks run concurrently.
- Invalid or missing chart inputs use explicit fallbacks.

### Milestone D - Structured report

- ReportAgent emits validated StructuredReport JSON.
- HTML and Markdown renderers produce deterministic output.

### Milestone E - Production hardening

- Limits, timeouts, redaction, retries, partial failures, and lineage are tested.
- v2 passes integration and packaging tests and becomes the default.

## 15. Definition of done

Report Engine v2 is complete when:

- PlanAgent and TemplateAgent converge within a bounded, auditable loop.
- Every plan dependency carries a real named DataOutput reference.
- Scheduler starts work only when required dependencies are available.
- Existing and generated methods share one execution contract.
- DataStepProcessor keeps deterministic values separate from LLM interpretation.
- Large datasets stay outside LLM context and trace metadata.
- A chart can consume one or multiple step outputs.
- Derived chart metrics are explicit PlanSteps, never hidden chart calculations.
- Every required chart resolves to safe ECharts JSON or an explicit fallback.
- ReportAgent produces validated StructuredReport JSON.
- HTML and Markdown render from the same report object.
- Partial and failed states are visible and auditable.
- Every fact and chart traces back to plan, execution, output, and source.
- Unit, contract, integration, failure-path, and packaging tests pass offline.

## 16. Immediate next implementation slice

The next mergeable slice should establish contracts without changing current
v1 execution:

1. Add `reporting/contracts.py` with ReportPlan, TemplateInstance,
   TemplateBinding, MissingDataRequest, PlanRevisionResponse, and validators.
2. Add ReportWorkflowConfig with negotiation and generation limits.
3. Implement the `importlib.resources` TemplatePool loader.
4. Add a read-only TemplateAgent selector that clones TemplateInstance.
5. Implement pure binding validation for one and multiple output refs.
6. Add contract and TemplatePool tests, including wheel-content coverage.
7. Keep existing ReportEngine behavior behind `workflow_version="v1"` until the
   negotiation contracts are stable.
