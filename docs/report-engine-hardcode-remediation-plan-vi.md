# Kế hoạch xử lý hardcode trong Report Engine

## 1. Phạm vi

Tài liệu này cập nhật audit theo cấu trúc file mới của Report Engine và kế hoạch
xử lý. Các hạng mục P0/P1 đã được triển khai trên nhánh `refactor/report`; phần
mô tả bên dưới được giữ lại làm rationale và tiêu chí regression.

Nguồn tham khảo chính là `report-engine-hardcode-audit.md` do người dùng cung
cấp. Các vị trí bên dưới dùng số dòng sau khi `report.py` được chia nhỏ.

## 2. Các cụm hardcode ưu tiên cao

### P0. Scope document/vector chưa được materialize nhất quán

Vị trí:

- `reporting/planning.py:502-540`: fallback chỉ tạo bước cho vector collection
  khi scope không explicit.
- `reporting/planning.py:623-625`: `has_available_data` tính source, table và
  vector collection nhưng không tính document.
- `reporting/utils.py:823-845`: document và vector collection được đưa vào scope,
  nhưng contract này chưa được chuyển thành retrieval/materialization step tương
  ứng trong mọi nhánh.

Giá trị đang bị đóng cứng là cách suy diễn “nguồn dữ liệu hợp lệ” và loại step
được tạo theo từng loại scope. Hậu quả là explicit document/vector scope có thể
rơi xuống `corpus-overview` hoặc tạo template request không có input nội dung.

Đích thiết kế:

- Một `SourceMaterializationPolicy`/registry nhận source kind và trả về stable
  capability ID, input contract, output shape và semantic roles.
- Document, vector collection, table và local source cùng tham gia một contract
  xác định `has_materializable_data`.
- Fallback planner chỉ dùng policy/registry, không tự suy diễn bằng chuỗi `if`.

Kiểm thử bắt buộc:

- Explicit selected document tạo retrieval step có `goal_evidence`.
- Explicit selected vector collection tạo retrieval step có `goal_evidence`.
- Scope rỗng mới được phép rơi xuống metadata-only report.
- Mixed scope không bỏ sót bất kỳ source bắt buộc nào.

### P0. Sandbox capability snapshot được khai báo trong engine

Vị trí:

- `reporting/engine.py:1068-1089`: Python `3.11`, danh sách package,
  `network_access=False`, `source_access=read_only` và materialization shapes
  được khai báo trực tiếp.
- `reporting/prompts.py:117-151`: CodeAgent tin vào capability payload này.

Đích thiết kế:

- Sandbox Service cung cấp contract versioned `SandboxCapabilities`.
- Runtime lấy một snapshot duy nhất cho toàn bộ run.
- Prompt generation, deterministic validation và execution cùng dùng đúng
  snapshot đó.
- Capability snapshot và version được ghi vào trace để tái hiện lỗi.

Kiểm thử bắt buộc:

- Package thêm/bớt ở Sandbox Service tự phản ánh trong prompt và validator.
- Mismatch giữa snapshot và executor bị fail sớm với lỗi contract rõ ràng.
- Không còn package/version/network policy literal trong Report Engine.

### P0. Router phụ thuộc exact tool name và extension

Vị trí:

- `reporting/execution.py:161-230`: route spreadsheet/PDF theo extension và exact
  name `materialize_spreadsheet`, `extract_pdf_text`.
- `reporting/execution.py:305-369`: logic tương tự bị lặp trong fallback và thêm
  exact name `scan_csv`.

Đích thiết kế:

- Method Hub công bố stable capability ID, input/output schema, MIME/source
  contract và operation semantics.
- `SourceHandlerRegistry` ánh xạ source contract sang capability, không ánh xạ
  trực tiếp sang tên tool.
- `_normalize_route()` và `_fallback_route()` dùng chung một resolver.
- Operation compatibility được kiểm tra trước source preference; sự hiện diện
  của PDF không được phép biến một `aggregate` step thành text extraction.

Kiểm thử bắt buộc:

- Đổi display/tool name không làm thay đổi route khi capability ID giữ nguyên.
- Synonym và MIME alias được resolve bởi registry.
- Aggregate trên mixed CSV/PDF không bị route sang PDF extraction.
- Không còn extension list trùng lặp giữa hai đường route.

### P0. Output format không có typed contract

Vị trí:

- `reporting/engine.py:256-260`: giữ nguyên chuỗi `output_format` từ spec.
- `reporting/engine.py:867-884`: chỉ phân nhánh riêng cho
  `structured_report` và `html`; mọi giá trị khác rơi xuống Markdown.
- `reporting/rendering.py:49-84`: renderer thực tế chỉ tạo Markdown, CSS,
  JavaScript và HTML.

Đích thiết kế:

- Tạo `ReportFormat` enum và `RendererRegistry`.
- Validate format trước khi build/chạy graph.
- Mỗi format phải khai báo media type, renderer capability và artifact contract.
- Format không hỗ trợ phải bị reject rõ ràng; metadata không được ghi format khác
  với nội dung thực tế.

Kiểm thử bắt buộc:

- `pdf` bị reject nếu chưa đăng ký renderer.
- `html`, `markdown`, `structured_report` trả đúng content type và metadata.
- Registry có thể thêm renderer mới mà không sửa `ReportEngine`.

### P0. Template binding đang quyết định execution plan

Vị trí:

- `reporting/engine.py:440-478`: `_plan_for_template()` khởi tạo tập step từ
  binding rồi chỉ bổ sung dependency.

Đích thiết kế:

- Execution plan là hợp của template-bound steps, dependencies, `required=True`
  steps và output mang semantic role `goal_evidence`.
- Lý do giữ hoặc loại từng step được ghi vào trace.
- Template không được phép vô tình loại bỏ evidence bắt buộc cho objective.

Kiểm thử bắt buộc:

- Required step không có template binding vẫn được thực thi.
- `goal_evidence` step không có binding vẫn được thực thi.
- Optional, unbound và không critical vẫn được prune.

## 3. Các cụm hardcode ưu tiên trung bình

### P1. Template selection thiên về extension

Vị trí:

- `reporting/planning.py:819-876`: `.pdf` ưu tiên `document-analysis`, `.csv` và
  spreadsheet ưu tiên `data-profile`; intent-based candidates chỉ tham gia sau
  mapping này.

Kế hoạch:

- Chuyển MIME types, semantic tags, supported plan shapes và selection hints vào
  template manifest.
- Xây scoring contract có trọng số cho objective, plan shape, semantic roles,
  MIME và extension; extension chỉ là tín hiệu phụ.
- Trace score của từng candidate.

### P1. Chart policy dùng nhiều limit không nhất quán

Vị trí:

- `reporting/processing.py:51-54`: default `max_inline_chart_rows=100`.
- `reporting/processing.py:388-430`: nhiều nhánh cắt dataset ở `40`.
- `reporting/composition.py:102-104`: fallback truy cập `allowed[0]`, có thể lỗi
  khi `allowed_types=[]`.

Kế hoạch:

- Tạo typed `ChartPolicy` cho input row limit, inline row limit, series/category
  limit và truncation semantics.
- Mọi nhánh tạo chart dataset dùng cùng policy và luôn tính `truncated` trước khi
  cắt.
- Empty `allowed_types` trả fallback có cấu trúc, không index trực tiếp.

### P1. Report composition suy diễn content role từ từ khóa tiếng Anh

Vị trí:

- `reporting/composition.py:378-380`: suy diễn evidence/limitation/caveat.
- `reporting/composition.py:566-600`: suy diễn limitation, supporting evidence,
  finding và takeaway từ block ID/title.

Kế hoạch:

- Thêm `content_role` bắt buộc hoặc versioned fallback vào template schema.
- Template validation reject role không hợp lệ trước khi chạy report.
- Giữ heuristic cũ trong một migration adapter có deprecation telemetry, sau đó
  loại bỏ khi toàn bộ template đã nâng version.

### P1. HTML renderer và term-frequency gắn với CDN/English

Vị trí:

- `reporting/rendering.py:302-321`: footer tiếng Anh, `lang="en"` và ECharts
  `5.5.1` từ jsDelivr.
- `reporting/processing.py:497`: English stopword list được khai báo trực tiếp.

Kế hoạch:

- Inject `AssetResolver`; ưu tiên bundled/versioned asset và hỗ trợ explicit CDN
  policy.
- Thêm locale vào report metadata và renderer context.
- Dùng locale-aware tokenizer/stopword provider.
- Offline/CSP mode phải tạo report có trạng thái chart fallback rõ ràng.

## 4. Thứ tự triển khai đề xuất

1. **Contract baseline:** thêm characterization tests cho toàn bộ lỗi đã audit,
   nhưng chưa đổi behavior.
2. **Typed policies:** định nghĩa `SandboxCapabilities`, `ReportFormat`,
   `ChartPolicy`, source/capability contracts và `content_role`.
3. **Runtime authority:** kết nối Sandbox Service và Method Hub làm nguồn dữ liệu
   authoritative; ghi snapshot/version vào trace.
4. **Planner/router migration:** chuyển materialization và routing sang registry;
   xử lý explicit document/vector scope.
5. **Execution-plan safety:** hợp nhất bound, dependency, required và
   goal-evidence steps.
6. **Template migration:** chuyển selection hints/content roles vào manifest,
   thêm version migration và validation.
7. **Renderer/locale migration:** asset resolver, locale-aware text processing và
   offline/CSP behavior.
8. **Cleanup:** xóa heuristic, exact tool-name checks, duplicated extension lists
   và legacy defaults sau thời gian compatibility.

Mỗi phase nên là một PR độc lập, có contract tests và không trộn với refactor cấu
trúc file. Thứ tự P0 ưu tiên correctness; P1 ưu tiên khả năng mở rộng và vận hành.

## 5. Trạng thái triển khai

- `reporting/policies.py` chứa typed format registry, chart policy, locale/asset
  policy và source materialization registry.
- `runtime/sandbox.py` cung cấp versioned `SandboxEnvironment`; API sandbox
  provider lấy capability payload từ Sandbox Service khi service công bố.
- Planner tạo retrieval step cho explicit document/vector scope.
- Router resolve stable capability ID và chỉ dùng exact tool name như compatibility
  alias tập trung trong registry.
- Execution-plan pruning giữ template bindings, dependencies, required steps và
  `goal_evidence`.
- Template selection dùng weighted objective/plan/source scoring; extension nằm
  trong manifest và có trọng số thấp.
- Built-in templates có `content_role`; template schema yêu cầu role và legacy
  adapter chỉ còn ở pool boundary.
- Chart dataset dùng một policy thống nhất, bảo toàn trạng thái truncation và xử
  lý an toàn `allowed_types=[]`.
- Renderer nhận locale và asset policy; có thể tắt CDN rõ ràng cho offline/CSP.
- Regression coverage nằm trong
  `packages/sdk/tests/test_report_hardcode_policies.py`.

## 6. Tiêu chí hoàn tất toàn bộ kế hoạch

- Report Engine không chứa literal package inventory của sandbox.
- Router không phụ thuộc display/tool name và không lặp extension list.
- Unsupported output format không thể tạo metadata sai.
- Required/goal-evidence step không bị template prune.
- Explicit document/vector scope luôn có đường materialization.
- Chart limit/truncation thống nhất và không có empty-list `IndexError`.
- Template composition không phụ thuộc từ khóa tiếng Anh.
- Renderer hoạt động theo explicit asset và locale policy.
