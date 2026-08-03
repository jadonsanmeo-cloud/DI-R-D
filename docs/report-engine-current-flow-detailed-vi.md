# Report Engine - Luồng hiện tại chi tiết

> Cập nhật: 2026-08-03
> Phạm vi: luồng sinh report từ dữ liệu đã ingest trong Data Intelligence SDK.  
> Trạng thái: mô tả implementation đang có trong workspace, bao gồm các thay đổi local chưa được commit.
> Cơ sở kiểm tra: source ở branch `minhanh-report-refactor`, commit `a1313ac`,
> các thay đổi local hiện tại và hai artifact gần nhất lúc audit.

## 1. Mục tiêu của luồng

Report Engine nhận một yêu cầu ngôn ngữ tự nhiên, xác định tài liệu đã ingest có
liên quan, lấy đủ dữ liệu của tài liệu được chọn, lập kế hoạch phân tích, tạo một
template instance phù hợp với mục tiêu của run, thực thi các bước dữ liệu và sinh
report có evidence.

Luồng được thiết kế để không mặc định rằng mọi dữ liệu đều là bảng, không mặc
định một domain cụ thể và không bắt buộc mọi nhiệm vụ phải dùng Code Agent.

Các đầu vào chính:

- `UserQuery`: yêu cầu của người dùng.
- `ExecutionSpec`: objective, scope, capability requirements, constraints và
  presentation contract đã được xác nhận.
- `DataCorpusPackage`: scope dữ liệu ban đầu mà request cung cấp cho engine.
- `EngineRuntimeContext`: Method Hub, sandbox, artifact store, run context và
  danh sách tool đang được expose.

Các đầu ra chính:

- structured report payload;
- Markdown;
- HTML, CSS và JavaScript;
- chart configuration;
- data/code/execution artifacts;
- `events.jsonl` ghi trace của toàn bộ run.

## 2. Sơ đồ E2E

```text
Web request
    |
    v
AXIOM Intent Service + Spec Builder
    |
    v
ExecutionSpec đã xác nhận
    |
    v
Report Engine
    |
    +--> 1. Ingested Corpus Resolver
    |       +--> semantic chunk search để tìm document identity
    |       +--> overview để kiểm tra manifest/content profile
    |       `--> hydrate document bằng corpus_get_file_ingested_data
    |
    +--> 2. Template Architecture Pass
    |
    +--> 3. Plan Agent
    |
    +--> 4. Template Agent tạo run-local template instance
    |
    +--> 5. Plan-Template Contract Negotiation
    |
    +--> 6. DAG Scheduler
    |       |
    |       `--> với từng PlanStep:
    |             resolve inputs
    |                 |
    |                 v
    |             Router Agent
    |                 |
    |                 +--> Method Hub / materialized corpus
    |                 |
    |                 +--> Semantic Analysis Agent
    |                 |
    |                 `--> Code Agent
    |                         -> Sandbox validation
    |                         -> Validator Agent
    |                         -> Sandbox execution
    |                 |
    |                 v
    |             DataScience Processor
    |
    +--> 7. Chart Input Assembler + Chart Agent
    |
    +--> 8. Structured Report Agent + structural focused repair
    |
    `--> 9. Renderer + artifacts
```

Lưu ý đặc biệt: graph trong source hiện tại **không có** node readiness gate,
numeric-claim verifier hoặc report-content gate ở giữa data execution và
renderer. Các node này xuất hiện trong một số artifact cũ, nhưng không tồn tại
trong implementation đang được audit. Không được dùng artifact cũ để kết luận
rằng branch hiện tại vẫn có các gate đó.

Graph cấp report nằm trong
`packages/sdk/src/data_intelligence_sdk/engines/reporting/engine.py::_build_graph`.
Graph thực thi một PlanStep nằm trong cùng file tại `_build_data_step_graph`.

### 2.1. Bản đồ module

| Module | Trách nhiệm chính | Không nên làm |
| --- | --- | --- |
| `base.py` | Kiểu lỗi contract và lớp nền gọi prompt/LLM | Không quyết định routing hoặc chất lượng dữ liệu |
| `corpus.py` | Discover document, chọn identity, materialize `all/page`, kiểm tra completeness, dựng evidence package | Không dùng top-k preview làm full evidence |
| `planning.py` | `PlanAgent`, `TemplatePool`, `TemplateAgent`; normalize plan/template và tạo proposal | Không thực thi tool hoặc tự tính KPI |
| `contracts.py` | Phân loại execution class, bind Method Hub arguments, validate contract Plan-Template | Không sửa ngầm dữ liệu để làm contract “có vẻ đúng” |
| `utils.py` | Chuẩn hóa PlanStep I/O, step-output registry, input resolver, schema/profile helper, scope payload | Không chứa business rule theo domain/file |
| `execution.py` | Router, Semantic Agent, Code Agent, Validator Agent, DataScience Agent và ToolExecutor | Không coi code chạy được là bằng chứng code đúng nghiệp vụ |
| `processing.py` | Materialize step output, dựng profile/sample, grounding số cục bộ, metric/evidence/chart dataset | Không thay thế claim verifier cấp report |
| `composition.py` | Chart Agent và Structured Report Agent | Không tự nâng claim chưa xác minh thành sự thật |
| `rendering.py` | Render structured report thành Markdown/HTML/CSS/JS | Không quyết định tính đúng của metric hoặc claim |
| `policies.py` | Limit, locale, chart, sampling, presentation, template/source policy | Không chứa hardcode cho một file hay domain |
| `prompts.py` | Prompt contract của các agent | Không phải nguồn chân lý duy nhất cho schema/runtime contract |
| `engine.py` | LangGraph orchestration, scheduler, route branch, retry, compose và render | Không nên che lỗi phase con bằng fallback không có provenance |

### 2.2. Hai graph thực thi

Graph cấp report:

```text
resolve_ingested_data
  -> template_draft
  -> plan
  -> template
  -> negotiate --revise--> plan
               --failed--> negotiation_failed -> render
               --execute-> schedule_data
  -> prepare_charts
  -> run_chart (0..n)
  -> compose_report
  -> render
```

Graph cấp data step:

```text
resolve_inputs
  -> route
     -> existing tool / reused corpus materialization
     -> semantic analysis
     -> generated code: generate -> sandbox/contract validate -> validator
     -> unsupported
  -> DataScience Processor nếu execution thành công
  -> kết quả step
```

Hai graph dùng chung `_StepOutputRegistry`. Registry có `RLock`, nên việc đăng
ký/resolve output giữa các step chạy song song đã có bảo vệ ở mức cấu trúc dữ
liệu. Điều này không thay thế transaction hoặc correctness validation cho nội
dung output.

### 2.3. Contract chi tiết của từng module

#### `base.py`

- Cung cấp `_PromptAgent` để chuẩn hóa việc gọi model và parse message content.
- Định nghĩa `ReportFlowContractError` kèm phase/error code cho các boundary có
  contract rõ.
- Không lưu state của report; state thuộc LangGraph và runtime context.

#### `corpus.py`

- Entry point chính: `ReportCorpusResolver.resolve`.
- Input: spec, corpus package, runtime/Method Hub và selector nếu có.
- Output: `ReportCorpusResolution`, selected documents, materialization map và
  evidence package đã enrich lại vào corpus context.
- Guardrail quan trọng: selected document phải indexed/processing completed và
  materialization phải complete; page pagination phải tiến lên.
- Failure domain: discovery unavailable/ambiguous, identity missing, document
  not ready, payload invalid hoặc incomplete.

#### `planning.py`

- `PlanAgent`: tạo/normalize DAG, bind dependency input và có heuristic fallback.
- `TemplatePool`: load manifest/blueprint, selection policy và preview policy.
- `TemplateAgent`: chọn blueprint, tạo run-local sections/blocks/requirements,
  repair design và ghi provenance.
- Module này mô tả “cần làm gì” và “cần trình bày gì”, không gọi Method Hub.

#### `contracts.py`

- Chuẩn hóa `execution_class` và kiểm tra operation có hợp input/output không.
- `ToolArgumentBinder` bind literal/resolved step output vào tool schema, phát
  hiện required argument, duplicate binding, path/value mismatch.
- `ReportContractValidator` đối chiếu Plan outputs với template requirements,
  dependency, shape, semantic role và schema compatibility.

#### `utils.py`

- Normalize PlanStep `inputs/outputs`, symbolic reference và schema/profile.
- `_StepOutputRegistry` lưu value, artifact ref, host path, sandbox path, schema,
  profile và semantic roles.
- `_StepInputResolver` resolve upstream refs và merge argument theo schema.
- Dựng scoped corpus/method-hub payload cho agent mà không truyền runtime object
  trực tiếp vào prompt.

#### `execution.py`

- `RouterAgent`: chọn Method Hub, corpus materialization, semantic, code hoặc
  unsupported; kết quả còn được engine enforce theo execution class.
- `SemanticAnalysisAgent`: xử lý evidence phi cấu trúc theo batch và output
  schema; có một lần repair khi contract fail.
- `CodeAgent`: sinh strict structured code spec; không tự execute.
- `ValidatorAgent`: đánh giá semantic correctness sau deterministic preflight;
  invalid/missing verdict được coi là fail.
- `DataScienceAgent`: diễn giải output cho report-facing content; có deterministic
  fallback.
- `ToolExecutor`: gọi tool trusted hoặc publish sandbox result đã validated.

#### `processing.py`

- `DataScienceProcessor.process` đăng ký output, profile/sample, gọi DataScience
  Agent, grounding số, tạo metrics/chart datasets và lineage.
- `ChartInputAssembler.prepare` nối template chart requirement với dataset của
  step, đồng thời tạo fallback result khi không có dataset phù hợp.
- Đây là adapter từ execution payload sang composition payload.

#### `composition.py`

- `ChartAgent` chọn chart type/encoding và dựng ECharts option hoặc fallback.
- `ReportAgent.run_structured` tạo structured report theo template instance,
  structural repair và deterministic fallback.
- `reconcile_template_instance` đồng bộ block đã thực sự render về template.

#### `rendering.py`

- `ReportRenderer.render` tạo Markdown, HTML, CSS và JavaScript.
- Có escaping, formatting metric/table, theme và ECharts bootstrap.
- Chỉ tiêu thụ structured payload; không có quyền nâng trust level của data.

#### `policies.py`

- `ChartPolicy`, `AnalysisSamplingPolicy`, `ReportPresentationPolicy` giới hạn
  kích thước/presentation.
- `TemplateSelectionPolicy` đọc fallback/threshold từ manifest.
- `SourceMaterializationRegistry` ánh xạ stable source capability tới tool được
  expose.
- `LocalePolicy` cung cấp label/token policy cho `en` và `vi`.

#### `prompts.py`

- Chứa instruction và response contract cho Plan/Template/Router/Semantic/Code/
  Validator/DataScience/Chart/Report agent.
- Prompt giúp model tuân thủ contract nhưng enforcement cuối phải nằm trong
  Python schema/validator, không được tin prompt như security boundary.

#### `engine.py`

- `ReportEngine.run` tạo graph state, policy/runtime object và trả `EngineOutput`.
- `_build_graph` điều phối report-level nodes; `_build_data_step_graph` điều phối
  executor của từng step.
- Quản lý semaphore, negotiation loop, scheduler, retry Code Agent, chart fan-out,
  report composition, renderer và event recording.
- Đây là nơi phải đặt các cross-step trust gate vì từng agent riêng lẻ không có
  đủ state để đánh giá toàn report.

## 3. Phase 0 - AXIOM nhận request và tạo ExecutionSpec

Web không trực tiếp gọi từng report agent. Request đi qua các service của AXIOM
để phân tích intent và chuẩn bị ExecutionSpec.

ExecutionSpec cần trả lời tối thiểu các câu hỏi:

- Người dùng muốn đạt mục tiêu gì?
- Scope là organization corpus hay một file/document cụ thể?
- Engine nào cần chạy?
- Output cần những loại nội dung nào?
- Có ràng buộc về nguồn, evidence, audience hoặc format không?

Markdown spec hiện có thể chứa `Presentation Contract`, ví dụ:

```json
{
  "report_content_roles": [
    "executive_summary",
    "narrative",
    "chart",
    "recommendation",
    "limitations"
  ]
}
```

Danh sách trên mô tả nhu cầu trình bày, không phải bố cục section cố định và
không quyết định executor nào sẽ được gọi.

Nếu Spec Builder lỗi hoặc thiếu contract bắt buộc, run có thể dừng trước Report
Engine với thông báo như `The execution spec could not be prepared`.

## 4. Phase 1 - Ingested Corpus Resolver

### 4.1. Tại sao phải có resolver trước Plan Agent?

Plan Agent cần biết nguồn dữ liệu thực tế đang tồn tại. Nếu chỉ truyền một
`DataCorpusPackage` rỗng từ web, Plan Agent không thể tự biết document ID hoặc
file nào cần phân tích.

Resolver vì vậy là boundary trước Plan Agent:

```text
query + organization scope
    -> discover document identity
    -> hydrate document metadata/content
    -> dựng lại DataCorpusPackage có dữ liệu
    -> Plan Agent
```

### 4.2. Trường hợp request đã chỉ rõ document

Nếu ExecutionSpec đã có `document_id`, `file_name` hoặc `object_key`, resolver
dùng selector đó và không cần suy đoán identity từ nội dung chunk.

### 4.3. Trường hợp request chỉ nêu chủ đề

Nếu request nói kiểu `make a report about X` mà chưa có document selector:

1. Resolver gọi một corpus discovery tool, ưu tiên tool tương thích đang được
   Method Hub expose, ví dụ `corpus_retrieve_context`.
2. Search trả về các chunk phù hợp.
3. Resolver gom các hit theo `document_id`.
4. Chunk chỉ dùng để xác định file liên quan; chunk không được coi là toàn bộ dữ
   liệu của report.
5. Resolver chọn document theo score, margin và candidate policy.
6. Resolver gọi `corpus_get_file_ingested_data` bằng `document_id` đã xác định.

Đây là khác biệt quan trọng giữa search cho hỏi đáp và data preparation cho
report:

```text
Hỏi đáp: query -> top-k chunks -> trả lời

Report: query -> top-k chunks -> document identity
                              -> hydrate toàn bộ document liên quan
                              -> phân tích report
```

### 4.4. `overview`, `all`, `page` và `auto`

- `overview`: lấy manifest, content summary, profile và preview nhỏ để biết
  document có gì.
- `all`: lấy toàn bộ chunk/content của document trong giới hạn policy.
- `page`: lấy dữ liệu theo từng trang/chunk window khi file lớn.
- `materialization_mode=auto`: xem overview rồi quyết định dùng `all` hoặc phân
  trang dựa trên kích thước thực tế.

Mục tiêu là hỗ trợ datalake lớn mà không biến một overview sample thành toàn bộ
evidence của report.

### 4.5. Kết quả phase corpus

Resolver tạo:

- document manifest;
- canonical source reference, ví dụ `corpus://org/document-id`;
- content profile và previews;
- materialized document artifacts;
- `DataCorpusPackage` mới cho Plan Agent;
- metadata ghi discovery tool, score, selector và materialization policy.

Các lỗi cần dừng rõ ràng ở boundary này:

- không có discovery tool;
- không tìm thấy document;
- nhiều candidate mơ hồ nhưng policy yêu cầu chọn chính xác;
- document chưa indexed/ready;
- `corpus_get_file_ingested_data` không được expose;
- payload trả về không đúng schema hoặc bị thiếu chunk.

## 5. Phase 2 - Template Architecture Pass

Template Architecture Pass diễn ra trước Plan Agent để phác thảo loại report
cần tạo và nhu cầu dữ liệu ở mức presentation.

Nó không nên khóa report vào một bố cục canonical. Nhiệm vụ chính:

- xem objective, source profile và presentation roles;
- chọn candidate blueprint phù hợp;
- đề xuất architecture theo run;
- xác định những loại nội dung cần có;
- tạo feedback ban đầu cho Plan Agent.

Template trong pool là blueprint, không phải report instance hoàn chỉnh. Blueprint
có thể cung cấp block primitive, adaptation policy và design intent, nhưng tiêu
đề, số section, thứ tự, nội dung và data bindings được điều chỉnh cho run.

## 6. Phase 3 - Plan Agent

Plan Agent nhận:

- ExecutionSpec;
- DataCorpusPackage đã được resolver làm đầy;
- template architecture feedback;
- plan cũ nếu đây là repair/revision.

Plan Agent sinh một DAG gồm các `PlanStep`. Mỗi step cần khai báo:

```json
{
  "step_id": "derive-period-performance",
  "description": "Derive comparable period metrics from normalized records",
  "depends_on": ["extract-evidence"],
  "inputs": [
    {
      "name": "evidence",
      "ref": "step-output://extract-evidence/evidence_records"
    }
  ],
  "operation": {
    "capability": "derive_period_metrics",
    "execution_class": "deterministic_transform",
    "execution_mode": "auto"
  },
  "outputs": [
    {
      "name": "period_metrics",
      "shape": "table",
      "schema": {"type": "array"}
    }
  ]
}
```

Plan validator kiểm tra:

- step ID và dependency;
- DAG cycle;
- input/output refs;
- output shape và JSON schema;
- duplicate/conflicting bindings;
- operation contract;
- source lineage;
- required template data requests.

Nếu plan có contract errors, Plan Agent được repair có feedback một lần. Chỉ
nhận plan repair nếu số lỗi giảm. Nếu vẫn lỗi, workflow dừng thay vì chạy một
plan không hợp lệ.

Tuy nhiên, ngay trong `PlanAgent.run`, nếu model không trả payload hợp lệ hoặc
không có step, code hiện tạo `_fallback_plan`. Fallback này là heuristic tổng
quát chứ không chứa logic riêng cho Fashion Star, nhưng nó vẫn có thể che việc
Plan Agent/model thực sự thất bại. Trace hiện chưa làm nổi bật đầy đủ run đang
dùng plan do LLM sinh hay plan fallback.

Một contract Plan tốt không chỉ có `type: array`. Với step quan trọng, schema
nên khai báo fields, required fields, `additionalProperties`, cardinality hoặc
invariant nghiệp vụ có thể kiểm tra. Nếu schema quá rộng, validator chỉ chứng
minh output đúng kiểu chung chứ chưa chứng minh đúng dữ liệu mà report cần.

## 7. Phase 4 - Template Agent và template instance

Template Agent nhận plan đã validate và tạo template instance dành riêng cho
run hiện tại.

Ví dụ một block instance:

```json
{
  "block_id": "operating-context-analysis",
  "type": "narrative",
  "content_role": "narrative",
  "title": "Drivers and Operating Context",
  "purpose": "Explain evidenced drivers, constraints and trade-offs",
  "required": true,
  "data_requirements": ["goal-evidence"]
}
```

Trong đó:

- `block_id` là identity dùng để bind nội dung;
- `type` là primitive renderer hỗ trợ;
- `content_role` mô tả ý nghĩa nội dung;
- `purpose` là câu hỏi phân tích của block;
- `required` quyết định block có được phép biến mất hay không;
- `data_requirements` nối block với output của Plan.

Template instance không nên mặc định:

- domain tài chính;
- tên cột `revenue`, `profit`, `Month`;
- số lượng section cố định;
- chart type cố định;
- metric chính cố định;
- phép tính margin/growth cố định.

Guardrail hiện yêu cầu một report phân tích phải có ít nhất một required
analytical-development block ngoài phần mở đầu, nhưng không bắt buộc tên hoặc vị
trí cụ thể của block đó.

Template Agent có ba đường lui hiện tại:

- giữ `previous_instance` khi đang negotiation/repair;
- chọn fallback template được manifest khai báo nếu proposal không đạt ngưỡng;
- dùng adaptive neutral design khi design proposal không dùng được.

Các fallback này giúp workflow bền hơn và không hardcode domain. Đổi lại, nếu
provenance/fallback reason không được hiển thị rõ ở output vận hành, lỗi model
selection có thể bị hiểu nhầm là một lần chọn template bình thường.

## 8. Phase 5 - Plan-Template Contract Negotiation

Negotiation kiểm tra hai phía có nối được với nhau không:

```text
Template cần requirement A
        ↕
Plan có output nào thỏa semantic role, shape và schema của A?
```

Kết quả có thể là:

- `accepted`: tất cả required requirement được resolve;
- `partial`: một phần contract được chấp nhận và graph hiện vẫn cho phép execute;
- `retry/revise`: còn thiếu nhưng có thể sửa trong giới hạn iteration;
- `failed`: required contract không thể đáp ứng.

Negotiation không phải một vòng lặp vô hạn giữa hai LLM. Graph có một node
negotiation với giới hạn iteration. Khi accepted, execution DAG được prune để
chỉ giữ các step có consumer trong template hoặc cần thiết cho dependency.

Graph có giới hạn iteration và phát hiện trạng thái revision bị lặp bằng hash.
Rủi ro còn lại là `partial` vẫn được route như `accepted`; nếu thiếu requirement
quan trọng nhưng không được đánh dấu required chính xác, run có thể bước vào DAG
với coverage chưa đủ.

## 9. Phase 6 - DAG Scheduler và data-step graph

Scheduler tìm các step có dependency đã hoàn thành và chạy chúng với giới hạn
concurrency. Mỗi step đi qua graph riêng:

```text
resolve_inputs
    |
    +-- failed --> finalize_execution_failure
    |
    v
route
    |
    +-- existing tool --> execute_existing ------+
    |                                             |
    +-- semantic ------> execute_semantic --------+--> analyze
    |                                             |
    +-- generated code -> generate_code           |
    |                     -> validate_code         |
    |                     -> execute_generated ----+
    |
    `-- unsupported --> finalize_execution_failure
```

`max_data_concurrency` mặc định là 4 và `max_chart_concurrency` mặc định là 6.
Nếu dependency required thất bại, downstream step được tạo kết quả `skipped`.
Nếu DAG unresolved/cyclic lọt tới scheduler, các step liên quan cũng bị skip và
ghi warning. Vấn đề nghiêm trọng hiện tại là khi mọi step đã có trạng thái
`completed/failed/skipped`, scheduler vẫn chuyển tới chart và compose; source
hiện không có readiness gate để chặn required failure trước Report Agent.

### 9.1. Input Resolver

Input Resolver biến symbolic ref thành dữ liệu cụ thể:

- `step-output://step-id/output-name`;
- corpus source refs;
- materialized artifact refs;
- literal parameters hợp lệ.

Nó phải giữ lineage và không được lấy toàn bộ object output nhét vào một tham số
chỉ cần `document_id`.

### 9.2. Router Agent

Router chọn executor. Router không trực tiếp xử lý dữ liệu và không tự chạy code.

Contract hiện có bốn execution class:

| Execution class | Ý nghĩa | Executor phù hợp |
| --- | --- | --- |
| `source_operation` | Lấy/hydrate dữ liệu từ hệ thống ngoài | Method Hub |
| `semantic_inference` | Hiểu ngôn ngữ, trích evidence, tổng hợp ý nghĩa | Semantic Analysis Agent |
| `deterministic_transform` | Phép tính/biến đổi có quy tắc xác định | Code Agent |
| `auto` | Router chọn theo capability và tool metadata | Một trong ba nhánh |

Router có thể dùng native LLM tool binding để chọn Method Hub tool. Tool Argument
Binder sau đó resolve argument và validate theo `parameters_schema`.

Router không chỉ dựa vào tên operation tự do: execution class, source handler,
Method Hub metadata và schema đều tham gia binding. Dù vậy, capability registry
chưa hoàn toàn self-describing; một số alias/mapping vẫn nằm trong Python policy,
nên thêm capability mới đôi khi vẫn cần đổi code.

### 9.3. Nhánh Method Hub

Dùng cho capability đã có tool tin cậy, ví dụ:

- corpus search;
- materialize ingested document;
- lấy dữ liệu từ provider ngoài;
- thao tác nguồn mà local code không được phép giả lập.

Nếu source operation thiếu tool bắt buộc, route phải là `unsupported`. Không nên
fallback sang Code Agent để giả vờ đọc một nguồn mà sandbox không truy cập được.

Implementation hiện tại chưa tuân thủ hoàn toàn nguyên tắc trên cho mọi Method
Hub error. `fallback_to_generation_on_tool_error=True` là giá trị mặc định. Khi
tool đã bind nhưng execute thất bại và step có resolved input, `_existing_execution_choice`
có thể chuyển sang Code Agent. Điều này trộn lỗi provider/tool với lỗi thiếu
deterministic capability và là một fallback cần được tắt mặc định hoặc điều
khiển bằng contract rõ ràng.

### 9.4. Nhánh Semantic Analysis Agent

Dùng khi kết quả cần hiểu ý nghĩa của evidence:

- trích xuất claim từ văn bản;
- xác định driver, risk, limitation;
- tổng hợp insight;
- nối evidence với source lineage;
- chuẩn hóa nội dung phi cấu trúc thành record có schema.

Agent nhận toàn bộ resolved evidence set của step, không chỉ một chunk preview.
Output vẫn phải tuân thủ schema do PlanStep khai báo.

### 9.5. Nhánh Code Agent

Dùng cho phép tính xác định trên đầu vào đã biết schema:

- aggregate/group-by;
- growth rate;
- ranking;
- join;
- pivot;
- date normalization;
- descriptive statistics;
- chart-ready transformation.

Luồng Code Agent:

```text
step contract + resolved inputs + schema catalog
    -> LLM phải sinh đúng một JSON object
    -> kiểm tra 5 field bắt buộc và JSON Schema
    -> bind đúng upstream input vào parameter
    -> AST, function signature, import và path validation
    -> chạy trong AXIOM sandbox
    -> validate output bằng JSON Schema của PlanStep
    -> Validator Agent chỉ chạy sau deterministic preflight pass
    -> nếu toàn bộ pass: đăng ký interface ở trust level generated_validated
    -> nếu code-contract/runtime/output/semantic lỗi: repair có giới hạn
    -> nếu binding hoặc sandbox infrastructure lỗi: dừng nhánh generation
```

Năm field bắt buộc của code response hiện tại:

```json
{
  "tool_name": "...",
  "parameters_schema": {"type": "object"},
  "output_schema": {},
  "source_code": "def ...",
  "execution_arguments": {}
}
```

Code Agent không còn nhận raw fenced Python, nested alias hoặc tự chế default
schema. Tên tool không được tự sửa cho khớp function. Input path chỉ được bind
khi upstream output đã có `sandbox_path`; URI `corpus://...` không được coi là
file local. Một upstream input cũng không được đồng thời bind vào hai parameter
khác nhau. Những thay đổi này làm trust boundary chặt hơn và loại bỏ nhiều
“chữa cháy” im lặng.

`ToolExecutor.execute_generated` không chạy code lần hai: nó dùng kết quả sandbox
đã được validate, sau đó mới đăng ký interface. Đây là hành vi đúng vì tránh
validation một execution nhưng publish kết quả từ execution khác.

Phân loại retry hiện có nhưng chưa hoàn chỉnh. Classifier dựa trên substring của
error message. `input_binding_error` và `sandbox_infrastructure_error` dừng ngay;
code/runtime/output/semantic error có thể regenerate tối đa 4 attempts và stop
sớm khi cùng fingerprint lặp lại. Với sandbox infrastructure error, thiết kế tốt
hơn là retry chính sandbox execution theo policy riêng, không regenerate code và
cũng không fail ngay ở lần đầu.

Code Agent không phải lựa chọn phù hợp để tự đọc hiểu PDF thô. Nếu một task như
`identify risks and operating context` bị route sang code, đó thường là lỗi
classification của Plan/Router.

### 9.6. DataScience Processor

Sau khi một executor trả dữ liệu thành công, DataScience Processor chuyển kết
quả thành report-facing payload:

- `analysis_summary`;
- metrics;
- evidence items;
- chart data;
- `report_content.block_content` bind theo run-local `block_id`;
- limitations/warnings;
- source lineage.

Processor không thay thế Router. Nó diễn giải kết quả đã materialize cho các
consumer block trong template instance.

Processor hiện làm thêm các việc quan trọng:

- ghi step output vào artifact store và sandbox staging;
- tạo schema/profile/sample có giới hạn;
- tạo `metric_id` theo `step_id + metric name` và gắn `evidence_refs`;
- kiểm tra số xuất hiện trong phần phân tích có nằm trong raw result hay không;
- thay nội dung không grounded bằng deterministic fallback;
- tính lại một số field phần trăm khi input khai báo rõ current/comparison.

Đây chỉ là numeric grounding cục bộ theo “giá trị số có xuất hiện trong raw
data”. Nó chưa chứng minh phép tính đúng, chưa phân biệt các số bằng nhau nhưng
khác ngữ nghĩa, và chưa đảm bảo mọi numeric claim trong report cuối tham chiếu
đúng `metric_id/evidence_id`.

## 10. Phase 7 - Chart pipeline

Chart Input Assembler nhận datasets từ các data step và template block cần chart.

Một chart dataset cần có:

- records thực;
- dimension;
- measures;
- unit/format metadata nếu có;
- provenance;
- analytical intent hoặc insight target.

Chart Agent chọn cách visualize phù hợp với shape dữ liệu. Chart là optional khi
không có dataset hợp lệ; engine không nên dựng chart từ retrieval metadata như
`relevance_score`, rank hoặc vector distance chỉ để lấp chỗ trống.

Chart tốt phải trả lời một câu hỏi phân tích. Ví dụ chart trend phải cho thấy sự
thay đổi của metric theo thời gian và phần insight phải diễn giải điều đáng chú
ý, không chỉ nhắc lại tên trục.

Implementation hiện kiểm tra tối thiểu `analytical_purpose`, `evidence_claim`
và ít nhất hai data point. Khi model trả chart spec không hợp lệ, Chart Agent có
deterministic fallback. Fallback này giúp presentation không sập, nhưng quality
check hiện chưa xác minh sâu rằng chart type phù hợp với phân phối dữ liệu, claim
có được tái tạo từ đúng series hay không, hoặc chart có bổ sung evidence thay vì
lặp lại bảng. Fallback status cũng cần được đưa ra observability rõ hơn.

## 11. Phase 8 - Structured Report Agent

Report Agent nhận:

- ExecutionSpec;
- template instance;
- kết quả các data step;
- chart payloads;
- source/evidence lineage;
- presentation policy.

Agent chỉ được tạo nội dung cho các block của run-local template instance. Với
narrative block, nội dung tốt nên theo cấu trúc:

```text
claim
  -> evidence
  -> interpretation
  -> consequence/trade-off
```

Executive Answer không nên là transcript dạng `metric: value`. Nó phải tổng hợp:

- kết luận chính;
- evidence quan trọng;
- điều gì giải thích kết quả;
- rủi ro/giới hạn;
- hành động hoặc quyết định cần cân nhắc.

Sau lần compose đầu, structural check bên trong Report Agent tìm:

- required block bị thiếu;
- required narrative quá mỏng;
- narrative chỉ chứa danh sách metric;
- recommendation không có nội dung;
- payload sai shape.

Nếu phát hiện vấn đề, Report Agent được focused repair đúng các `block_id` lỗi.
Repair chỉ được nhận nếu giảm lỗi hoặc tạo nội dung tốt hơn.

Report Agent dựng một deterministic fallback report trước khi gọi LLM. Nếu model
trả payload không hợp lệ, fallback có thể trở thành output. Cơ chế này có ích cho
khả năng render nhưng có thể che lỗi compose nếu run metadata chỉ nói
`generation_mode=langchain` dựa trên việc engine có LLM, thay vì ghi rõ agent nào
đã dùng fallback.

Phải phân biệt hai lớp validation:

- **Đang có:** kiểm tra shape/required block/độ mỏng/trùng lặp ở Report Agent và
  focused repair.
- **Đang thiếu trong source hiện tại:** gate required data-step readiness,
  verifier buộc numeric claim tham chiếu metric/evidence đã xác minh, và content
  gate kiểm tra claim-evidence/report completeness trước renderer.

Do thiếu lớp thứ hai, một report có payload cấu trúc hợp lệ vẫn có thể chứa claim
sai nghiệp vụ hoặc được render sau khi required step failed/skipped.

## 12. Phase 9 - Renderer và artifacts

Renderer chuyển structured report thành:

- `rendered/report.md`;
- `rendered/report.html`;
- `rendered/report.css`;
- `rendered/report.js`.

Artifact directory còn có thể chứa:

```text
artifacts/<run-id>/
    events.jsonl
    manifest.json
    data/
    code/
    executions/
    rendered/
```

- `events.jsonl`: trace agent/tool/engine theo sequence.
- `data/`: materialized data và step outputs.
- `code/`: code do Code Agent sinh, nếu nhánh code được chạy.
- `executions/`: sandbox execution result.
- `rendered/`: report cuối.

Không có folder `code/` trong một run không có nghĩa workflow thiếu Code Agent.
Nó có thể đơn giản là không có PlanStep nào cần deterministic generated code.

Renderer hỗ trợ block primitives và ECharts, đồng thời có logic giảm lặp giữa
summary/executive block. Renderer không và không nên xác minh dữ liệu. HTML mặc
định tải ECharts từ CDN; nếu môi trường trình duyệt không ra internet, phần chart
có thể báo runtime không load dù report text vẫn tồn tại. Đây là dependency vận
hành/presentation, không phải lỗi tính toán.

## 13. Tại sao Code Agent từng lỗi nhiều?

Các lỗi cũ đến từ nhiều lớp khác nhau:

### 13.1. Semantic task bị phân loại thành generated code

Task đọc risk, limitation hoặc driver từ PDF từng bị giao cho Code Agent. Model
phải viết regex để làm một việc vốn cần hiểu ngôn ngữ, nên dễ thiếu và overfit.

### 13.2. Model không tuân thủ code response contract

Một số response:

- không phải JSON;
- không có Python source;
- source rỗng;
- tên function không khớp `tool_name`;
- parameters schema không khớp function signature.

### 13.3. Input quá thô

Code Agent từng nhận object PDF rất lớn gồm block, HTML, bbox, tables, chunks và
metadata. Generated parser trở nên phụ thuộc parser schema và layout của một file.

### 13.4. Tool binding sai

Có run từng truyền toàn bộ document content vào `document_id`. Tool fail rồi flow
fallback sang code, khiến lỗi binding trông giống lỗi Code Agent.

### 13.5. Output contract và semantic expectation không khớp

Code có thể chạy thành công nhưng trả thiếu metric hoặc sai shape. Sandbox chỉ
chứng minh code chạy; Validator vẫn phải reject vì kết quả không đáp ứng PlanStep.

### 13.6. Retry khuếch đại sequence

Mỗi generated-code step có nhiều generation attempts. Nhiều step cùng lỗi có thể
tạo hàng chục event `code_agent`, `sandbox_validate` và `validator_agent`.

## 14. Trạng thái sau thay đổi gần nhất

Audit dùng hai artifact mới nhất có liên quan:

### 14.1. Run `02082026-1812-966c0216-3d55-4778-9bac-885c165b2c9c`

Run này hoàn tất 43 event và đi qua Code Agent. Generated spec từng bind cùng một
upstream output vào cả object parameter và path parameter. Các thay đổi local
hiện tại đã chặn duplicate binding kiểu này và bắt buộc schema/code response
chặt hơn.

Artifact của run này có các event:

```text
report_readiness_gate
numeric_claim_verifier
report_content_gate
```

Nhưng tên node trên không xuất hiện trong source branch hiện tại. Đây là bằng
chứng artifact được tạo từ một trạng thái code khác trước đó; không phải bằng
chứng branch hiện tại vẫn được bảo vệ bởi các gate.

### 14.2. Run `02082026-1820-02bbbaae-815c-49e3-95fb-187c5b8a0044`

Yêu cầu có phép tính average. Trace cho thấy:

```text
corpus resolve/materialize
  -> plan/template/negotiation
  -> materialized source
  -> deterministic table extraction: 12 rows
  -> Router bind Method Hub mean
  -> Method Hub trả 227500.0
  -> ToolExecutor completed
  -> run.failed: ReportFlowContractError: runtime phase failed
```

Run này **không đi qua Code Agent**. Lỗi xảy ra sau `tool_executor` sequence 29,
trước khi có event `datascience_agent` cho step cuối. Trace chỉ còn generic
`runtime phase failed`, nên không đủ dữ liệu để kết luận exception cụ thể nằm ở
DataScience Agent, provider call hay processor. Đây là lỗ hổng observability ở
node boundary: một phase con ném exception nhưng run-level handler làm mất type,
message và context gốc.

### 14.3. Trạng thái test của thay đổi local

Bộ test report được chạy sau refactor Code Agent đạt:

```text
213 passed, 4 subtests passed
```

Điều này xác nhận các unit/contract test hiện tại không regression. Nó chưa thay
thế E2E qua AXIOM services, provider thật, Method Hub thật và sandbox thật. Chưa
có artifact E2E mới sau toàn bộ thay đổi local để chứng minh luồng web hoàn tất.

## 15. Những phần còn mang tính fixed policy

Không tìm thấy hardcode tên `Fashion Star`, document ID, tên file hoặc giá trị
KPI của file thử nghiệm trong source report engine đang audit. Tuy nhiên vẫn có
contract, alias và fallback policy cố định:

- tập route và execution class hợp lệ;
- một số exact capability alias cho semantic inference;
- tập block primitive mà renderer hỗ trợ;
- min/max content length;
- generation/negotiation/concurrency limits;
- sampling và chart resource limits;
- blacklist retrieval metadata không được làm chart measure;
- fallback blueprint trung lập.

Đây không phải hardcode domain như `revenue` hay `profit`, nhưng vẫn là điểm có
thể cấu hình hóa nếu muốn platform mở rộng mà không sửa Python.

Các giá trị mặc định đáng chú ý:

| Policy | Mặc định | Ý nghĩa/rủi ro |
| --- | ---: | --- |
| Code generation attempts | 4 | Có thể tăng latency nếu lỗi không thuộc code |
| Negotiation iterations | 3 | Bounded loop; tốt, nhưng `partial` vẫn execute |
| Data concurrency | 4 | Chạy nhiều step song song |
| Chart concurrency | 6 | Chạy nhiều chart song song |
| Tool-error to Code fallback | `true` | Rủi ro che lỗi Method Hub/provider |
| Analysis sample records | 12 | Prompt không nhận toàn bộ rows; processor vẫn giữ raw data |
| Chart dataset rows | 40 | Có thể truncate visualization data |
| Chart categories | 12 | Policy presentation, không phải data validation |
| Max KPI items | 8 | Density limit, không quyết định KPI nào đúng |

Các fallback còn chạy được trong flow:

- Plan Agent fallback plan;
- Template Agent manifest/adaptive fallback;
- Method Hub error chuyển sang generated code;
- Chart Agent deterministic fallback;
- Report Agent structured fallback;
- DataScience Agent deterministic fallback/grounding replacement.

Không phải fallback nào cũng xấu. Vấn đề là fallback cần có eligibility contract,
provenance và status riêng. Hiện `generation_mode` chỉ phản ánh engine có LLM hay
không, nên chưa cho biết agent nào thực sự fallback.

## 16. Hướng phát triển tiếp theo

### 16.1. Typed Plan IR đầy đủ hơn

PlanStep nên khai báo rõ:

- input shape;
- output shape;
- execution class;
- operation intent;
- deterministic/semantic boundary;
- evidence requirements;
- allowed fallback.

Router khi đó gần giống compiler dispatch hơn là đoán executor từ prose.

### 16.2. Capability registry động

Chuyển exact capability mapping sang registry/config do capability tự mô tả:

```json
{
  "capability": "semantic_extraction",
  "execution_class": "semantic_inference",
  "accepted_input_shapes": ["document", "text", "evidence_records"],
  "output_shapes": ["array", "object"]
}
```

### 16.3. Canonical evidence layer

Tách rõ:

```text
raw ingested document
    -> semantic extraction
    -> canonical evidence records
    -> deterministic computation
    -> report composition
```

Code Agent chỉ làm việc trên records có schema thay vì parser-specific PDF object.

### 16.4. Error-class-aware retry

Đã có một phần:

- binding error dừng Code Agent;
- code contract/runtime/output/semantic error có thể regenerate;
- lỗi giống hệt lặp lại được stop early bằng fingerprint;
- sandbox infrastructure error không regenerate code.

Phần còn thiếu:

- classifier vẫn dựa trên substring error message;
- binding error chưa có đường quay lại Binder/Plan trong cùng run;
- semantic mismatch chưa chuyển executor/repair Plan;
- sandbox infrastructure error hiện fail ngay, chưa retry riêng sandbox execution;
- retry budget chưa tách theo error class/provider.

### 16.5. Report quality contract theo objective

Thay vì chỉ kiểm tra độ dài, quality evaluator nên kiểm tra:

- coverage của objective;
- evidence cho từng claim;
- sự nhất quán số liệu;
- độ khác biệt giữa các section;
- analytical depth;
- recommendation/actionability;
- chart-insight alignment;
- uncertainty và limitation.

### 16.6. Policy cấu hình theo report profile

Đưa content length, retry limits, sampling, chart exclusions và optionality vào
policy/config. Profile có thể thay đổi theo document size, audience và report
intent mà không hardcode domain hoặc bố cục.

### 16.7. Evaluation suite đa domain

Cần benchmark trên nhiều dạng input:

- PDF narrative;
- PDF nhiều bảng;
- CSV/tabular;
- nhiều document cùng chủ đề;
- organization corpus mơ hồ;
- file rất lớn cần paged materialization;
- report không cần chart;
- report cần phép tính phức tạp;
- domain vận hành, y tế, pháp lý, kỹ thuật và nghiên cứu.

Các tiêu chí nên đo gồm route accuracy, evidence coverage, retry count, latency,
schema validity, hallucination rate và report usefulness.

## 17. Kiểm tra cuối: các vấn đề còn lại

### 17.1. Ma trận ưu tiên

| Mức | Vấn đề | Hậu quả | Boundary cần sửa |
| --- | --- | --- | --- |
| P0 | Source hiện không có readiness gate trước compose | Required step `failed/skipped` vẫn có thể đi tới report | `engine.py`, sau scheduler và trước chart/compose |
| P0 | Không có verifier numeric claim/evidence cấp report | Report cấu trúc hợp lệ vẫn có thể dùng số không có metric/evidence ID | Giữa Report Agent và renderer |
| P0 | Exception ở phase con bị co thành `runtime phase failed` | Không biết node/provider/error gốc; khó sửa run mới nhất | Run wrapper + node-level error event |
| P1 | Tool execute error mặc định fallback sang Code Agent | Provider/binding/source lỗi bị hiểu sai thành thiếu code | `_existing_execution_choice` |
| P1 | Nhiều fallback không có provenance thống nhất | Run “completed/langchain” có thể thực chất dùng fallback | State/output quality metadata |
| P1 | `partial` negotiation được execute | Template coverage thiếu có thể lọt vào DAG | Negotiation acceptance contract |
| P1 | Metric grounding chỉ kiểm tra số có xuất hiện | Số đúng giá trị nhưng sai ngữ nghĩa hoặc phép tính vẫn có thể pass | Deterministic metric/evidence layer |
| P1 | Status `completed` chưa tách các mức tin cậy | Người dùng hiểu workflow hoàn tất là report đã xác minh | Final response/run state |
| P1 | Plan schema có thể quá rộng | Output rác vẫn đúng `array/object` về kỹ thuật | Plan contract validator |
| P2 | Sandbox infrastructure không có retry riêng | Lỗi tạm thời làm fail run dù code không sai | Retry controller |
| P2 | Error classifier dựa trên text marker | Message mới có thể bị phân loại sai | Typed exception/error code |
| P2 | Chart quality check còn nông | Chart hợp lệ kỹ thuật nhưng không phải evidence tốt | Chart verifier |
| P2 | CDN ECharts là external runtime dependency | Chart có thể không render ở mạng hạn chế | Asset policy/renderer |
| P2 | Artifact và source có thể lệch version | Audit/reproduce run sai | Run manifest commit + dirty diff hash |

### 17.2. P0 - thiếu report-level trust gates

Đây là vấn đề lớn nhất. Luồng hiện tại có validation ở từng executor, nhưng sau
khi scheduler hoàn tất, graph chuyển thẳng sang chart rồi compose. Cần ba gate
tách biệt và fail closed:

```text
data steps complete
  -> readiness gate
       required step completed?
       required output tồn tại?
       schema/cardinality/invariant pass?
  -> chart/report composition
  -> claim verifier
       mọi numeric claim có metric_id/evidence_id?
       phép tính tái tạo được?
       scope/units/period có khớp?
  -> content gate
       required block đủ evidence?
       limitation/partial status được phản ánh?
  -> renderer
```

Không nên khôi phục verifier dạng regex chỉ chặn mọi token số. Token `13` có thể
là section number, count, year fragment hoặc metric. Verifier phải đọc structured
claim objects, không suy luận identity chỉ từ text cuối.

### 17.3. P0 - observability mất lỗi gốc

Run 18:20 chứng minh Method Hub đã trả kết quả thành công nhưng run chết trước
DataScience event. Cần record failure ngay tại mỗi LangGraph node với:

- `node_name`, `step_id`, `attempt`;
- exception class và stable error code;
- message gốc đã redact secret;
- provider/tool/model nếu liên quan;
- input/output artifact refs;
- stack/log ref, không cần nhét toàn stack vào UI.

Run-level handler chỉ nên thêm context, không thay exception cụ thể bằng một câu
`runtime phase failed` duy nhất.

### 17.4. P1 - fallback policy chưa đúng trust boundary

Fallback cần được chia thành ba nhóm:

1. **Presentation fallback:** chart/report layout fallback; có thể cho phép nhưng
   phải đánh dấu degraded/partial.
2. **Planning fallback:** plan/template fallback; chỉ được chạy nếu contract
   validator chứng minh đủ objective coverage và ghi provenance.
3. **Data/execution fallback:** Method Hub sang Code Agent; chỉ được phép khi
   capability thật sự chưa có và input đã materialize. Không được dùng cho auth,
   network, provider, tool-runtime hoặc source-access error.

Giá trị mặc định `fallback_to_generation_on_tool_error=True` đang vi phạm nhóm 3.

### 17.5. P1 - data contract và metric contract chưa đủ mạnh

Schema đúng hướng nhưng chưa đồng đều. Mỗi required analytical output nên có:

- JSON Schema cụ thể đến field/type;
- min/max items hoặc expected cardinality khi có thể suy ra;
- semantic role, unit, period, entity scope;
- primary key/dedup rule;
- nullability;
- invariant như total = sum(rows), ratio = numerator/denominator;
- lineage tới source rows và transform/tool execution.

Metric layer nên tạo object bất biến:

```json
{
  "metric_id": "step.metric",
  "value": 227500,
  "unit": "USD",
  "period": "2025",
  "formula": "mean(net_benefit)",
  "input_evidence_ids": ["..."],
  "execution_ref": "artifact://...",
  "validation_status": "verified"
}
```

Report Agent chỉ được diễn giải các object này. Claim mới phải trỏ tới metric hoặc
evidence ID, không tự cộng/tính từ prose.

### 17.6. P1 - semantics của trạng thái run

Nên tách tối thiểu:

```text
execution_completed
data_validated
claims_verified
report_content_validated
report_completed
```

`report_completed=true` chỉ được đặt khi các trạng thái required trước đó đều
true. Nếu presentation fallback được dùng, thêm `degraded=true` cùng danh sách
fallbacks. Nếu data thiếu nhưng user cho phép partial, trạng thái phải là
`partial`, không phải `completed` không điều kiện.

### 17.7. P2 - chart và report quality

Chart verifier nên kiểm tra deterministic:

- dataset ref tồn tại và đúng lineage;
- encoding field tồn tại, type phù hợp;
- series/axis/unit nhất quán;
- time series được sort theo thời gian;
- không truncate làm thay đổi claim;
- claim tái tạo được từ plotted rows;
- type phù hợp cardinality và analytical purpose;
- chart không chỉ lặp một KPI hoặc retrieval metadata.

Report content evaluator nên đánh giá objective coverage, claim duplication,
evidence density, limitation và chart-insight alignment. Các đánh giá mềm có thể
dùng LLM, nhưng số liệu và lineage phải được kiểm tra bằng code.

### 17.8. Những điểm hiện đã đúng hướng

- Discovery chunk chỉ dùng tìm identity; selected document được materialize
  `all/page` và bắt buộc `returned_chunks == total_chunks`, `has_more == false`.
- Document ID/materialization được giữ trong evidence package thay vì giao LLM
  tự đoán lại.
- Plan-Template negotiation bounded và có cycle/stall protection.
- Step output registry có lock và giữ artifact/sandbox path/schema/profile.
- Router tách source, semantic và deterministic execution.
- Code Agent đã fail closed hơn: strict JSON contract, schema/AST/signature/path
  validation, no raw-code fallback, no silent schema fabrication.
- Generated tool chỉ được nâng trust sau sandbox + output contract + Validator
  pass; không execute lần hai.
- Numeric grounding cục bộ loại một phần hallucinated numbers trước composition.
- Không thấy hardcode Fashion Star hoặc KPI riêng của file trong report engine.
- Unit/contract test report hiện pass 213 test và 4 subtest.

### 17.9. Thứ tự sửa khuyến nghị

1. Khôi phục/thiết kế lại typed readiness, claim và content gates; thêm test fail
   closed cho required failed/skipped step và unverified claim.
2. Giữ exception gốc và ghi node-failure event để tái hiện run 18:20.
3. Đặt tool-error fallback mặc định `false`; chỉ generation fallback khi registry
   xác nhận capability absent và input local/materialized hợp lệ.
4. Chuẩn hóa run quality status và fallback provenance.
5. Nâng Plan output contract và deterministic metric registry.
6. Tách retry theo typed error; thêm sandbox retry riêng.
7. Nâng chart verifier và E2E evaluation đa domain.
8. Chạy lại E2E web với artifact mới có commit/diff fingerprint trước khi kết luận
   “luồng đã chạy được”.

## 18. Kết luận kiến trúc

Luồng hiện tại đã có boundary đúng hướng:

- search chunk chỉ dùng để tìm document;
- full/page materialization cung cấp evidence cho report;
- Plan quyết định cần làm gì;
- Template quyết định report cần trình bày gì;
- Router quyết định executor;
- Method Hub lấy dữ liệu;
- Semantic Analysis hiểu nội dung;
- Code Agent thực hiện phép biến đổi xác định;
- DataScience Processor chuẩn bị report-facing content;
- Report Agent tổng hợp;
- Renderer chỉ trình bày.

Luồng hiện tại **chưa thể được coi là clean/fail-closed hoàn toàn**. Phần
discovery/materialization, input binding và Code Agent trust boundary đã tốt hơn
rõ rệt, nhưng report-level correctness boundary đang thiếu trong source hiện tại.
Vì vậy điều kiện “chạy hết graph” chưa đồng nghĩa “report đã được xác minh”.

Ưu tiên không phải ép Code Agent xử lý nhiều hơn, mà là khép khoảng trống giữa
validated step outputs và report synthesis: typed evidence/metric contract,
readiness/claim/content gates, trạng thái tin cậy tách biệt và observability giữ
nguyên lỗi gốc.
