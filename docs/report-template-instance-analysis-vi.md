# Phân tích Template và TemplateInstance của Report Engine

## Phạm vi

Tài liệu này mô tả luồng đang chạy trong
`packages/sdk/src/data_intelligence_sdk/engines/reporting` và template pool của
Data Intelligence SDK. Các nhận xét không đề xuất thay đổi frontend, General
Engine hay các service ngoài luồng reporting.

## LLM làm gì và code deterministic làm gì

| Giai đoạn | LLM | Deterministic runtime |
| --- | --- | --- |
| Chọn template | Đọc goal, plan, content preview và các candidate; đề xuất domain cùng confidence | Áp dụng explicit template, giữ template của revision trước, kiểm tra confidence và chọn fallback từ manifest |
| Thiết kế instance | Đề xuất section, title, purpose, block archetype, layout và instruction theo run | Chỉ chấp nhận archetype có thật, giữ requiredness, merge guardrail, chuẩn hóa ID/layout và fallback về canonical definition khi blueprint không hợp lệ |
| Binding dữ liệu | Không tự bind output | So shape và toàn bộ semantic-role group với named Plan output; sinh missing-data request nếu thiếu |
| Nội dung report | Data Science Agent phân tích evidence; Structured Report Agent viết nội dung theo block | Giữ nguyên cấu trúc instance, metric/chart/evidence ID, lọc lặp và render HTML/Markdown |
| Không có LLM | Không có selection hoặc blueprint động | Chọn manifest fallback hoặc explicit template và dùng canonical sections |

Template vì vậy không phải hoàn toàn do LLM sinh và cũng không phải layout
hardcode thuần túy. LLM đề xuất thiết kế theo run, còn code là policy boundary
quyết định đề xuất nào hợp lệ.

## Nhược điểm trước khi cải thiện

1. Bảy domain template chỉ kế thừa `adaptive-raw-report` và thêm ba câu guidance.
   Kết quả là khác nhau chủ yếu ở tên/domain, còn canonical sections gần như
   giống nhau.
2. Prompt yêu cầu LLM dùng block archetype nhưng candidate payload không gửi
   danh sách archetype. LLM chỉ thấy allowed content roles và phải đoán.
3. Blueprint không có cách chọn chính xác một archetype khi có nhiều block cùng
   content role. Runtime chọn theo vị trí block, không theo ý nghĩa.
4. Instruction do LLM trả về thay thế toàn bộ canonical instruction, làm mất
   guardrail domain.
5. LLM có thể hạ required block thành optional. Section ID trùng cũng chưa được
   xử lý.
6. Blueprint của một template bị từ chối hoặc confidence thấp vẫn có khả năng
   được thử áp vào template explicit/fallback khác.
7. Output không ghi rõ selection và instance design đến từ LLM, explicit policy
   hay deterministic fallback nên khó audit.
8. `template.schema.json` tồn tại nhưng definition sau inheritance không được
   validate khi load; version trong manifest và file có thể lệch.
9. Prompt selection chưa có thang confidence, tiêu chí loại candidate gần nhất
   hay quy tắc chống lặp nhiệm vụ giữa các block.

## Cơ chế sau khi cải thiện

- Candidate gửi `section_archetypes`, gồm ID, type, content role, requiredness,
  data requirements và canonical instructions.
- Blueprint dùng `archetype_ref`; runtime chỉ nhận ID thuộc đúng selected
  template và đúng content role.
- Canonical instructions luôn được giữ trước, run-specific instructions được
  nối thêm và loại trùng.
- Requiredness là policy của canonical template; LLM không được hạ required
  evidence hoặc nâng chart/KPI optional thành required.
- Blueprint khác domain, bị từ chối hoặc confidence thấp không được áp vào
  explicit/fallback template.
- `selection.mode` và `template_instance.provenance` ghi rõ:
  `explicit`, `previous_instance`, `llm`, `deterministic_fallback`,
  `llm_blueprint` hoặc `canonical_template`.
- Definition được merge inheritance trước rồi validate bằng JSON Schema; ID và
  version phải khớp manifest.
- Template và Structured Report prompt mô tả rõ selection method, confidence,
  analytical progression, evidence discipline, chart gate và output contract.

## Bảy domain blueprint mới

Mỗi domain có năm section canonical, evidence trail và limitation riêng:

- Business, Economics and Finance: decision context, performance/driver,
  market/scenario, decision evidence và risk/limitations.
- Education and Learning: learner context, concept/skill progression,
  outcome-pedagogy-assessment alignment, educational evidence và transfer limits.
- Technology and Engineering: system boundary, architecture/dependencies,
  design trade-offs, assurance evidence và technical unknowns.
- Health and Wellbeing: population/evidence context, outcomes/determinants,
  intervention/access/equity, evidence considerations và safety limits.
- Society, Culture and Relationships: represented perspectives, norms/power,
  variation/change, voice/evidence và positionality/missing voices.
- Media, Arts and Entertainment: creative context, form/meaning,
  audience/reception, work-level evidence và counter-readings.
- Science, Policy and Environment: question/scope, methods/mechanisms,
  policy/system trade-offs, evidence/decision implications và uncertainty.

Các chart block vẫn optional. Mỗi chart yêu cầu claim định lượng, comparison
base, unit/population/coverage phù hợp; qualitative evidence ưu tiên insight
grid, evidence list hoặc process flow.

## Artifact run ID

Run mới được ghi vào:

```text
artifacts/<ddmmyyyy-hhmm>-<uuid>/
```

Ví dụ:

```text
artifacts/28072026-1405-00000000-0000-0000-0000-000000000123/
```

UUID suffix giữ tính collision-safe khi nhiều run bắt đầu trong cùng một phút.
Reader và `open_run` vẫn chấp nhận UUID-only cũ để không làm hỏng lịch sử.

## Giới hạn còn lại

- Chất lượng blueprint động vẫn phụ thuộc model và độ đại diện của bounded
  content preview.
- Một `goal-evidence` requirement chung giúp negotiation ổn định nhưng chưa thể
  biểu diễn mọi metric/relationship chuyên ngành thành contract riêng.
- Structured Report Agent có thể viết prose theo từng block, nhưng các visual
  item deterministic vẫn dựa trên report-content roles chung.
- Chưa có evaluation corpus chấm chất lượng report giữa bảy domain bằng model
  thật; unit test hiện tập trung vào contract, policy và fallback.
