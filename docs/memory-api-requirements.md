# Memory API Requirements for Data Intelligence SDK

## 1. Mục tiêu

Tài liệu này mô tả các API mà Data Intelligence SDK cần từ một Memory Platform độc lập.

Thiết kế tham khảo cách TencentDB Agent Memory phân tầng memory thành:

| Layer | Nội dung | Cách sử dụng |
| --- | --- | --- |
| L0 | Hội thoại gốc | Khôi phục lịch sử, kiểm chứng nguồn |
| L1 | Fact, preference, constraint, event | Recall chính xác theo query |
| L2 | Scenario hoặc working context | Khôi phục ngữ cảnh dự án/tác vụ |
| L3 | User/Agent profile dài hạn | Bootstrap hành vi ổn định |

Tuy nhiên, API đề xuất không sao chép nguyên contract của TencentDB. Contract được điều chỉnh theo flow hiện tại của AXIOM:

```text
User query
  -> Data Intelligence SDK load memory đúng một lần
  -> Query Orchestrator
       -> direct/general response
       -> delegated intent/spec/report flow
```

Trong giai đoạn này, tài liệu chỉ tập trung vào read/recall. Capture turn, extraction và update canonical memory được coi là trách nhiệm của hệ thống khác và chưa nằm trong phạm vi tích hợp của SDK.

## 2. Điều kiện hiện tại của AXIOM

### 2.1 Identity tạm thời

Trong môi trường development:

```text
tenant_id = test-org
user_id   = local-dev-user
```

Contract vẫn phải hỗ trợ identity đầy đủ để không phải thay API khi chuyển production:

```text
tenant_id     required
user_id       required
workspace_id  optional
agent_id      optional
session_id    optional
trace_id      optional
```

### 2.2 Memory được load một lần

SDK không nên gọi memory service riêng tại từng bước orchestrator, intent và spec.

Flow mong muốn:

```text
Request bắt đầu
  -> gọi Context Assembly API một lần
  -> nhận structured Memory Context
  -> tạo immutable MemoryContext trong SDK
  -> orchestrator lấy view phù hợp
  -> spec builder lấy view phù hợp
```

### 2.3 SDK quyết định cách đưa memory vào prompt

Memory Platform chịu trách nhiệm:

- Scope isolation và authorization.
- Retrieval, ranking và deduplication.
- Layer/type selection.
- Token/character budgeting.
- Trả provenance và version.

Data Intelligence SDK chịu trách nhiệm:

- Chọn memory nào cho orchestrator.
- Chọn memory nào cho delegated spec flow.
- Render memory vào AXIOM prompt envelope.
- Không để memory override system instruction hoặc current user request.

Memory Platform không nên trả một system prompt hoàn chỉnh phụ thuộc vào implementation nội bộ của SDK.

## 3. Danh sách API đề xuất

### API bắt buộc cho phiên bản đầu

| API | Mức độ | Mục đích |
| --- | --- | --- |
| `POST /v1/memory/context:assemble` | Bắt buộc | Load toàn bộ memory context cần thiết đúng một lần |
| `POST /v1/memory/search` | Bắt buộc | Search memory có kiểm soát, dùng cho debug và on-demand retrieval |
| `POST /v1/memory/items:batchGet` | Bắt buộc | Lấy nội dung/version hiện tại của các memory cụ thể |
| `GET /v1/memory/capabilities` | Bắt buộc | Capability negotiation và schema compatibility |
| `GET /health/live` | Bắt buộc | Kiểm tra process còn sống |
| `GET /health/ready` | Bắt buộc | Kiểm tra database/retriever sẵn sàng phục vụ |

### API nên có ở giai đoạn tiếp theo

| API | Mức độ | Mục đích |
| --- | --- | --- |
| `POST /v1/memory/conversations:context` | Nên có | Lấy recent turns và compacted conversation summary |
| `POST /v1/memory/items:list` | Nên có | Trang quản trị/debug liệt kê memory theo scope |
| `POST /v1/memory/usage:record` | Nên có | Ghi nhận memory nào thực sự được sử dụng |

### Chưa yêu cầu trong giai đoạn này

```text
POST /experience-turns
POST /memory/candidates
POST /memory/consolidate
PATCH /memory/items/{id}
DELETE /memory/items/{id}
GET /memory/jobs/{id}
```

Đây là write-side API thuộc capture/update lifecycle, tạm thời không do Data Intelligence SDK gọi.

## 4. API 1: Context Assembly

### Endpoint

```http
POST /v1/memory/context:assemble
```

### Mục đích

Đây là API runtime quan trọng nhất. SDK gọi đúng một lần ở đầu request để lấy một snapshot memory nhất quán.

API cần trả đủ dữ liệu để SDK có thể tạo hai view:

```text
Orchestrator view
  profile
  preference
  constraint

Delegated/spec view
  preference
  constraint
  semantic
  episodic
  outcome
  procedure
  scenario
```

### Request mẫu

```json
{
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user",
    "workspace_id": null,
    "agent_id": null,
    "session_id": "conversation-123"
  },
  "query": "Compare the latest NAPH reports and summarize the main findings",
  "requested_layers": ["L1", "L2", "L3"],
  "requested_types": [
    "profile",
    "preference",
    "constraint",
    "semantic",
    "episodic",
    "outcome",
    "procedure",
    "scenario"
  ],
  "retrieval": {
    "strategy": "hybrid",
    "limit": 30,
    "min_score": 0.0,
    "deduplicate": true
  },
  "budget": {
    "max_items": 30,
    "max_characters": 12000,
    "max_tokens": 3000
  },
  "options": {
    "include_content": true,
    "include_provenance": true,
    "include_expired": false
  },
  "trace_id": "resp_abc123"
}
```

### Response mẫu

```json
{
  "context_id": "memctx_01JXYZ",
  "snapshot_version": "2026-08-09T10:15:30.123Z",
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user",
    "workspace_id": null,
    "agent_id": null,
    "session_id": "conversation-123"
  },
  "items": [
    {
      "memory_id": "mem_pref_001",
      "layer": "L1",
      "memory_type": "preference",
      "content": "The user prefers responses in Vietnamese with an executive summary first.",
      "score": 0.94,
      "confidence": 0.91,
      "importance": 0.82,
      "version": 3,
      "status": "active",
      "valid_from": "2026-08-01T08:00:00Z",
      "valid_to": null,
      "updated_at": "2026-08-08T12:00:00Z",
      "provenance": [
        {
          "source_type": "conversation_turn",
          "source_id": "turn_123"
        }
      ]
    }
  ],
  "budget": {
    "returned_items": 1,
    "estimated_tokens": 24,
    "truncated": false
  },
  "retrieval": {
    "strategy": "hybrid",
    "duration_ms": 42
  }
}
```

### Yêu cầu hành vi

1. Scope phải được enforce ở database/retriever, không filter sau khi search.
2. Chỉ trả memory có `status=active`, trừ khi request yêu cầu khác.
3. Kết quả phải deterministic trong cùng `snapshot_version` khi input không đổi.
4. Deduplication phải xử lý memory trùng nội dung hoặc memory đã bị supersede.
5. Budget phải được áp dụng trước khi response được trả về.
6. API phải trả structured data, không nhúng trực tiếp system instruction.
7. Nếu vector search lỗi nhưng BM25 còn hoạt động, API có thể degraded fallback và phải báo strategy thực tế.

### Vì sao cần API này

Nếu chỉ cung cấp một API vector search thô, SDK sẽ phải tự giải quyết:

- Layer priority.
- Type filtering.
- Deduplication.
- Version/supersession.
- Token budgeting.
- Scope isolation.

Những trách nhiệm này thuộc Memory Platform và phải nhất quán giữa mọi consumer.

## 5. API 2: Memory Search

### Endpoint

```http
POST /v1/memory/search
```

### Mục đích

API này phục vụ:

- Debug tại sao một memory được hoặc không được recall.
- On-demand retrieval trong tương lai.
- Memory tools nếu engine/agent được phép chủ động tìm memory.
- Tìm sâu L0/L1 mà không inject toàn bộ vào prompt.

### Request mẫu

```json
{
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user",
    "agent_id": null,
    "session_id": null
  },
  "query": "preferred report language and output format",
  "filters": {
    "layers": ["L1", "L2", "L3"],
    "memory_types": ["profile", "preference", "constraint"],
    "statuses": ["active"],
    "updated_after": null
  },
  "strategy": "hybrid",
  "limit": 10,
  "budget": {
    "max_characters": 6000,
    "max_tokens": 1500
  },
  "explain": true,
  "trace_id": "debug_001"
}
```

### Response mẫu

```json
{
  "items": [
    {
      "memory_id": "mem_pref_001",
      "layer": "L1",
      "memory_type": "preference",
      "content": "The user prefers Vietnamese responses.",
      "score": 0.92,
      "score_components": {
        "lexical": 0.74,
        "semantic": 0.95,
        "recency": 0.61,
        "importance": 0.82
      },
      "version": 3,
      "status": "active"
    }
  ],
  "query": {
    "strategy_requested": "hybrid",
    "strategy_used": "hybrid",
    "duration_ms": 35
  },
  "budget": {
    "returned_items": 1,
    "estimated_tokens": 12,
    "truncated": false
  }
}
```

### Yêu cầu hành vi

- `explain=true` chỉ nên dùng cho debug vì có thể tăng latency và payload.
- Search phải hỗ trợ lexical/BM25 ngay cả khi embedding provider không sẵn sàng.
- Hybrid search nên dùng một cơ chế fusion rõ ràng, ví dụ Reciprocal Rank Fusion.
- API phải trả strategy thực tế đã sử dụng.
- Không trả memory ngoài scope kể cả khi semantic score cao.

## 6. API 3: Batch Get Memory Items

### Endpoint

```http
POST /v1/memory/items:batchGet
```

### Mục đích

Context Assembly có thể trả một snapshot. Trước khi dùng lại memory ID đã cache hoặc được tham chiếu từ artifact, SDK cần kiểm tra:

- Memory còn active hay không.
- Version có thay đổi không.
- Memory có bị supersede hoặc revoke không.

### Request mẫu

```json
{
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user"
  },
  "memory_ids": ["mem_pref_001", "mem_constraint_002"],
  "include_content": true,
  "include_provenance": true
}
```

### Response mẫu

```json
{
  "items": [
    {
      "memory_id": "mem_pref_001",
      "version": 3,
      "status": "active",
      "content": "The user prefers Vietnamese responses.",
      "superseded_by": null
    },
    {
      "memory_id": "mem_constraint_002",
      "version": 4,
      "status": "superseded",
      "content": null,
      "superseded_by": "mem_constraint_009"
    }
  ],
  "not_found": []
}
```

### Yêu cầu hành vi

- Authorization phải được kiểm tra cho từng ID.
- Không được tiết lộ rằng một ID tồn tại ở tenant khác.
- `not_found` nên dùng chung cho cả không tồn tại và không có quyền.
- API phải hỗ trợ tối thiểu 100 IDs/request để tránh N+1 calls.

## 7. API 4: Conversation Context

### Endpoint

```http
POST /v1/memory/conversations:context
```

### Trạng thái

Nên có ở giai đoạn tiếp theo. Flow AXIOM hiện chưa forward đầy đủ conversation history xuống SDK.

### Mục đích

Conversation history không nên chỉ là append vô hạn mọi turn cũ vào prompt. API cần trả:

- Một số recent turns còn nguyên văn.
- Structured hoặc textual compacted summary.
- Các unresolved references cần thiết để hiểu query hiện tại.
- Conversation version để phát hiện race condition.

### Request mẫu

```json
{
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user",
    "session_id": "conversation-123"
  },
  "query": "Compare it with the previous report",
  "recent_turn_limit": 8,
  "budget": {
    "max_tokens": 2000
  },
  "include_summary": true,
  "resolve_references": true
}
```

### Response mẫu

```json
{
  "conversation_id": "conversation-123",
  "conversation_version": 18,
  "summary": "The user is comparing the 2024 and 2025 NAPH reports. The previous turn focused on admission trends.",
  "recent_turns": [
    {
      "turn_id": "turn_017",
      "role": "user",
      "content": "Summarize the 2025 NAPH report."
    },
    {
      "turn_id": "turn_018",
      "role": "assistant",
      "content": "The report shows..."
    }
  ],
  "resolved_query": "Compare the 2025 NAPH report with the previously discussed 2024 NAPH report.",
  "budget": {
    "estimated_tokens": 350,
    "truncated": false
  }
}
```

### Yêu cầu hành vi

- Recent turns phải được sắp xếp theo đúng sequence, không chỉ timestamp.
- Summary phải có version và source turn boundary.
- Không compact mất các unresolved decisions hoặc entity references đang còn active.
- API không được tự ghi/update conversation khi chỉ thực hiện read.

## 8. API 5: Capabilities

### Endpoint

```http
GET /v1/memory/capabilities
```

### Mục đích

SDK cần biết Memory Platform hỗ trợ gì trước khi bật feature:

- Các layer có sẵn.
- Các memory type được hỗ trợ.
- Retrieval strategies.
- Maximum limits.
- API/schema version.
- Conversation summary support.
- Provenance support.

### Response mẫu

```json
{
  "service": "axiom-memory-platform",
  "api_version": "v1",
  "schema_version": "2026-08-01",
  "layers": ["L0", "L1", "L2", "L3"],
  "memory_types": [
    "profile",
    "preference",
    "constraint",
    "semantic",
    "episodic",
    "outcome",
    "procedure",
    "scenario"
  ],
  "retrieval_strategies": ["lexical", "semantic", "hybrid"],
  "features": {
    "context_assembly": true,
    "conversation_summary": true,
    "provenance": true,
    "search_explanation": true,
    "snapshot_consistency": true
  },
  "limits": {
    "max_search_items": 100,
    "max_batch_get_ids": 200,
    "max_context_tokens": 8000
  }
}
```

### Yêu cầu hành vi

- Endpoint không phụ thuộc database write hoặc LLM provider.
- Response nên cache được trong thời gian ngắn.
- Breaking schema change phải thay `api_version` hoặc `schema_version`.

## 9. API 6: Health

### Liveness

```http
GET /health/live
```

```json
{
  "status": "alive"
}
```

Liveness chỉ kiểm tra process/event loop, không gọi database hoặc model provider.

### Readiness

```http
GET /health/ready
```

```json
{
  "status": "ready",
  "components": {
    "database": "ready",
    "lexical_index": "ready",
    "vector_index": "degraded",
    "embedding_provider": "unavailable"
  },
  "supported_retrieval": ["lexical"]
}
```

Readiness phải phản ánh degraded mode. Vector provider lỗi không nhất thiết làm toàn bộ service unavailable nếu lexical recall vẫn hoạt động.

## 10. API 7: List Memory Items cho quản trị/debug

### Endpoint

```http
POST /v1/memory/items:list
```

### Mục đích

API này không nằm trên hot path của SDK. Nó phục vụ UI quản trị, kiểm thử thủ công và điều tra lỗi recall.

### Request mẫu

```json
{
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user"
  },
  "filters": {
    "layers": ["L1", "L2", "L3"],
    "memory_types": [],
    "statuses": ["active", "superseded", "deleted"]
  },
  "page_size": 50,
  "page_token": null,
  "order_by": "updated_at desc"
}
```

### Response mẫu

```json
{
  "items": [],
  "next_page_token": null
}
```

### Yêu cầu hành vi

- Phải dùng cursor/page token, không dùng offset cho dataset lớn.
- API quản trị có thể cần role/permission mạnh hơn runtime recall.
- Deleted item không được trả mặc định.

## 11. API 8: Usage Record

### Endpoint

```http
POST /v1/memory/usage:record
```

### Trạng thái

Nên có sau khi read integration ổn định. API này không update nội dung memory.

### Mục đích

Memory Platform cần biết memory nào thực sự hữu ích để:

- Theo dõi usage count.
- Điều chỉnh ranking.
- Phát hiện memory không bao giờ được sử dụng.
- Audit memory nào đã ảnh hưởng tới response.

### Request mẫu

```json
{
  "context_id": "memctx_01JXYZ",
  "scope": {
    "tenant_id": "test-org",
    "user_id": "local-dev-user"
  },
  "response_id": "resp_abc123",
  "used_memory_ids": ["mem_pref_001"],
  "usage": {
    "consumer": "data-intelligence-sdk",
    "stage": "query_orchestrator",
    "outcome": "direct_response"
  },
  "idempotency_key": "resp_abc123:memory-usage"
}
```

### Yêu cầu hành vi

- Phải idempotent.
- Không được thay đổi content/version của memory.
- Lỗi usage tracking không được làm fail response chính.

## 12. Error contract chung

Tất cả API nên dùng một error envelope thống nhất:

```json
{
  "error": {
    "code": "memory_scope_invalid",
    "message": "The supplied memory scope is invalid.",
    "retryable": false,
    "trace_id": "resp_abc123",
    "details": {}
  }
}
```

### Error codes tối thiểu

| HTTP | Code | Retry | Ý nghĩa |
| --- | --- | --- | --- |
| 400 | `memory_request_invalid` | Không | Payload không hợp lệ |
| 400 | `memory_budget_invalid` | Không | Budget âm hoặc vượt giới hạn |
| 401 | `memory_unauthenticated` | Không | Thiếu/sai service credential |
| 403 | `memory_scope_forbidden` | Không | Caller không có quyền với scope |
| 404 | `memory_item_not_found` | Không | Item không tồn tại hoặc không được phép thấy |
| 409 | `memory_snapshot_conflict` | Có thể | Snapshot/version không còn hợp lệ |
| 429 | `memory_rate_limited` | Có | Quá giới hạn request |
| 503 | `memory_retriever_unavailable` | Có | Không còn retrieval strategy khả dụng |
| 504 | `memory_timeout` | Có | Vượt timeout |

SDK cần fail-open cho recall:

```text
Memory API lỗi
  -> log structured event
  -> dùng empty MemoryContext
  -> vẫn xử lý query
```

Ngoại lệ: authorization hoặc scope mismatch phải được log ở mức security/error, không được âm thầm thử tenant/user khác.

## 13. Security và isolation requirements

### Scope enforcement

Memory Platform phải enforce tối thiểu:

```text
tenant_id
user_id
workspace_id
agent_id
session_id
```

Không nhất thiết mọi memory đều có tất cả trường, nhưng quy tắc visibility phải được định nghĩa rõ.

Ví dụ:

```text
tenant memory     -> dùng chung trong tenant nếu policy cho phép
workspace memory  -> chỉ workspace tương ứng
user memory       -> chỉ user tương ứng
agent memory      -> chỉ agent tương ứng
session memory    -> chỉ session tương ứng
```

### Service authentication

SDK gọi bằng service credential:

```http
Authorization: Bearer <service-token>
```

User identity phải nằm trong signed/trusted headers hoặc body đã được upstream xác thực. Không được tin trực tiếp identity tùy ý từ frontend.

### Prompt-injection safety

Memory content phải được coi là untrusted context:

```text
- Không được chứa system role thực thi trực tiếp.
- Không được override current user request.
- SDK phải đặt memory trong vùng prompt có label rõ ràng.
- Provenance/debug metadata không nên đưa vào prompt mặc định.
```

## 14. Performance requirements

### Context Assembly SLO đề xuất

| Chỉ số | Mục tiêu development | Mục tiêu production |
| --- | ---: | ---: |
| p50 latency | < 100 ms | < 75 ms |
| p95 latency | < 500 ms | < 250 ms |
| hard timeout | 2 s | 1 s |
| availability | 99% | 99.9% |

### Các guarantee cần có

- Context API phải có hard item/token budget.
- Search phải hỗ trợ timeout và cancellation.
- API phải trả latency/retrieval metadata.
- Không gọi LLM trên synchronous recall path.
- L2/L3 phải được precomputed, không tạo lại khi SDK gọi recall.

## 15. Observability requirements

Memory Platform cần log/tracing tối thiểu:

```text
memory.context.started
memory.context.completed
memory.context.degraded
memory.context.failed
memory.search.started
memory.search.completed
memory.search.failed
memory.scope.denied
```

Mỗi event cần có:

```text
trace_id
context_id
tenant_id
user_id hoặc user hash
requested layers/types
returned item count
duration_ms
strategy requested/used
truncated
degraded reason
error code
```

Không log toàn bộ memory content ở production.

## 16. Mapping vào Data Intelligence SDK

### Runtime flow đề xuất

```text
POST /api/v1/responses
  -> build request identity
  -> POST /v1/memory/context:assemble
  -> parse response thành immutable MemoryContext
  -> QueryOrchestrator dùng orchestrator view
  -> nếu direct: render profile/preference/constraint
  -> nếu delegate: truyền cùng MemoryContext xuống Markdown Spec Builder
  -> Spec Builder render preference/constraint/semantic/episodic/outcome/procedure/scenario
  -> response.completed
```

### Không nên làm

```text
orchestrator gọi memory API
intent gọi memory API lần nữa
spec builder gọi memory API lần nữa
engine gọi memory API lần nữa mà không có budget/authorization thống nhất
```

Điều này tạo snapshot không nhất quán, tăng latency và làm khó trace.

### Partition logic trong SDK

SDK có thể giữ mapping:

```text
orchestrator:
  profile
  preference
  constraint

spec/report:
  preference
  constraint
  semantic
  episodic
  outcome
  procedure
  scenario
```

Mapping này thuộc SDK vì nó phụ thuộc vai trò của từng prompt layer. Memory Platform chỉ trả structured memory đúng scope và ranking.

## 17. Minimum Viable Contract để yêu cầu team khác

Nếu cần thu gọn để triển khai vòng đầu, yêu cầu team Memory cung cấp bốn endpoint sau:

```text
POST /v1/memory/context:assemble
POST /v1/memory/search
GET  /v1/memory/capabilities
GET  /health/ready
```

Trong đó `context:assemble` bắt buộc hỗ trợ:

```text
scope isolation
L1/L2/L3 hoặc type tương đương
hybrid retrieval
structured result
version/status
provenance
item/token budget
timeout/degraded metadata
```

Sau đó bổ sung:

```text
POST /v1/memory/items:batchGet
POST /v1/memory/conversations:context
POST /v1/memory/items:list
POST /v1/memory/usage:record
```

## 18. Acceptance tests cho API provider

### Isolation

```text
Given memory thuộc user A
When user B search cùng nội dung
Then memory A không xuất hiện
```

### Load once

```text
Given một SDK request
When orchestrator delegate xuống spec
Then Memory Platform chỉ nhận một context assembly request
```

### Supersession

```text
Given memory v1 đã bị memory v2 supersede
When context được assemble
Then chỉ v2 active được trả về
```

### Budget

```text
Given có hàng trăm memory liên quan
When max_tokens=1500
Then response không vượt budget và truncated=true nếu cần
```

### Degraded retrieval

```text
Given vector provider unavailable
When lexical index còn healthy
Then API trả lexical results và strategy_used=lexical
```

### Fail-open tại SDK

```text
Given Memory Platform timeout
When user gửi query
Then SDK log memory.load.failed và vẫn xử lý query với empty MemoryContext
```

## 19. Quyết định kiến trúc đề xuất

1. SDK sử dụng một Context Assembly API làm read entry point chính.
2. Memory được trả dưới dạng structured items, không phải prompt hoàn chỉnh.
3. SDK load đúng một lần và tự tạo các prompt view.
4. L2/L3 được precomputed và có thể inject trực tiếp với budget nhỏ.
5. L0/L1 chi tiết được ưu tiên retrieval theo query; về sau có thể toolize thay vì inject toàn bộ.
6. Conversation history và durable memory là hai khái niệm khác nhau, không gộp vào một list message vô hạn.
7. Capture/update lifecycle tạm thời không nằm trong API dependency của Data Intelligence SDK.
8. Scope, ACL, version, provenance và budgeting là trách nhiệm bắt buộc của Memory Platform.
