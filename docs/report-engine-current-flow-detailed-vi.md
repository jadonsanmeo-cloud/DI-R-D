# Report Engine - Luồng hiện tại chi tiết

> Cập nhật: 2026-08-02  
> Phạm vi: luồng sinh report từ dữ liệu đã ingest trong Data Intelligence SDK.  
> Trạng thái: mô tả implementation đang có trong workspace, bao gồm các thay đổi local chưa được commit.

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
    +--> 8. Structured Report Agent + focused repair
    |
    `--> 9. Renderer + artifacts
```

Graph cấp report nằm trong
`packages/sdk/src/data_intelligence_sdk/engines/reporting/engine.py::_build_graph`.
Graph thực thi một PlanStep nằm trong cùng file tại `_build_data_step_graph`.

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

## 8. Phase 5 - Plan-Template Contract Negotiation

Negotiation kiểm tra hai phía có nối được với nhau không:

```text
Template cần requirement A
        ↕
Plan có output nào thỏa semantic role, shape và schema của A?
```

Kết quả có thể là:

- `accepted`: tất cả required requirement được resolve;
- `retry/revise`: còn thiếu nhưng có thể sửa trong giới hạn iteration;
- `failed`: required contract không thể đáp ứng.

Negotiation không phải một vòng lặp vô hạn giữa hai LLM. Graph có một node
negotiation với giới hạn iteration. Khi accepted, execution DAG được prune để
chỉ giữ các step có consumer trong template hoặc cần thiết cho dependency.

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

### 9.3. Nhánh Method Hub

Dùng cho capability đã có tool tin cậy, ví dụ:

- corpus search;
- materialize ingested document;
- lấy dữ liệu từ provider ngoài;
- thao tác nguồn mà local code không được phép giả lập.

Nếu source operation thiếu tool bắt buộc, route phải là `unsupported`. Không nên
fallback sang Code Agent để giả vờ đọc một nguồn mà sandbox không truy cập được.

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
    -> LLM sinh code spec
    -> parse JSON/code
    -> AST và interface validation
    -> chạy thử trong AXIOM sandbox
    -> validate output schema và sample result
    -> Validator Agent đánh giá
    -> nếu pass: execute generated tool
    -> nếu fail: repair với feedback trong giới hạn attempt
```

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

Sau lần compose đầu, report quality check tìm:

- required block bị thiếu;
- required narrative quá mỏng;
- narrative chỉ chứa danh sách metric;
- recommendation không có nội dung;
- payload sai shape.

Nếu phát hiện vấn đề, Report Agent được focused repair đúng các `block_id` lỗi.
Repair chỉ được nhận nếu giảm lỗi hoặc tạo nội dung tốt hơn.

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

Run tham chiếu:
`artifacts/02082026-1330-cb2c18c0-52ad-4183-94aa-e1777beb1c55`.

Luồng thực tế của run này:

```text
corpus_retrieve_context
    -> corpus_get_file_ingested_data
    -> Template Architecture
    -> Plan Agent
    -> Template Agent
    -> negotiation accepted
    -> materialize-report-source: Method Hub/materialized corpus
    -> extract-goal-evidence: Semantic Analysis Agent
    -> DataScience Processor
    -> Chart Agent
    -> Report Agent
    -> Renderer
```

Run hoàn tất trong 32 sequence, không có Code Agent/Sandbox retry. Đây là route
hợp lý vì công việc chính là hiểu và tổng hợp nội dung một PDF.

Sau run này đã bổ sung thêm guardrail để:

- không coi retrieval metadata là chart measure;
- phát hiện narrative dạng transcript `metric: ...`.

Các guardrail cuối đã qua test suite nhưng chưa có một E2E artifact mới hơn để
đánh giá chất lượng render thực tế của cả hai thay đổi.

## 15. Những phần còn mang tính fixed policy

Không có hardcode filename hoặc số liệu của file thử nghiệm trong flow. Tuy
nhiên vẫn có những contract/policy cố định:

- tập route và execution class hợp lệ;
- một số exact capability alias cho semantic inference;
- tập block primitive mà renderer hỗ trợ;
- min/max content length;
- generation/negotiation attempt limits;
- sampling và chart resource limits;
- blacklist retrieval metadata không được làm chart measure;
- fallback blueprint trung lập.

Đây không phải hardcode domain như `revenue` hay `profit`, nhưng vẫn là điểm có
thể cấu hình hóa nếu muốn platform mở rộng mà không sửa Python.

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

- parse error: regenerate response;
- syntax error: repair code;
- binding error: quay về Binder/Plan;
- semantic mismatch: chuyển semantic executor hoặc repair Plan;
- sandbox infrastructure error: retry execution, không regenerate code;
- lỗi giống hệt lặp lại: stop early.

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

## 17. Kết luận kiến trúc

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

Điểm cần tiếp tục phát triển không phải là ép Code Agent xử lý mọi nhiệm vụ, mà
là làm contract giữa các phase rõ hơn, dữ liệu trung gian có schema tốt hơn và
retry/quality validation phản ứng đúng với từng loại lỗi.
