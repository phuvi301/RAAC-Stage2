# Đặc tả L4 — Adaptive Team Formation

## 1. Định nghĩa và phạm vi

L4 kế thừa toàn bộ quyền của L3 (routing, topology, số vòng) và thêm quyền **hình thành đội nhóm**: thêm, bớt, thay role hoặc agent instance, và giao subtask. Mọi quyền khác (trần tài nguyên `B`, sandbox, quyền tool, evaluator) vẫn cố định.

| Thuộc | Nội dung |
| --- | --- |
| Trong L4 | Thêm/bớt/thay role từ danh mục mở rộng; tạo thêm instance của một role; chia subtask và chạy song song; chọn topology/vòng cho đội đã hình thành |
| Ngoài L4 | Tự sinh system prompt tùy ý; cấp quyền host/network; sửa protected test; đọc gold patch/test đánh giá; chạy song song giữa các task `T_i` ở Macro; escalation sang level khác (thuộc Giai đoạn 4 của Proposal) |

Trong Oracle Benchmark (Giai đoạn 2), run L4 chạy độc lập, không controller chọn level, không escalation sang level khác. Việc hình thành đội **bên trong** L4 vẫn hoạt động và được tính cho L4.

## 2. Thành phần

| Thành phần | Bản chất | Vai trò |
| --- | --- | --- |
| Orchestrator runtime | Code (LangGraph, asyncio) | Chạy graph, fan-out/barrier/fan-in, đếm vòng, kiểm budget, checkpoint |
| **Team Lead** | Role LLM, không tool, không đọc/sửa repo | Đề xuất `TeamDecision` (mục 5); không tạo patch, không quyết định PASS |
| **Formation Policy** | Code, xác định | Kiểm duyệt mọi quyết định đội: role hợp lệ, trần, đường dẫn, budget. Từ chối hoặc cắt bớt đề xuất vi phạm |
| Team Board | Vùng trong `CollaborationState` | Ledger phân việc và signals (mục 7) |
| Merge/Conflict Detector | Code | Gộp patch của các writer, phát hiện xung đột (mục 8) |

Nguyên tắc: **LLM đề xuất, code kiểm duyệt.** PASS vẫn do code tính (test đạt và Reviewer duyệt, như L3). Nếu Team Lead hỏng schema hoặc bị Policy từ chối thì dùng đội mặc định (mục 4.4) và ghi `source: fallback`.

Điểm cần nhóm quyết định (ghi ở mục 12): Team Lead là role mới, nên tương tự trường hợp Hub (ghi tại level_definitions.md mục 11) — đề xuất xếp Team Lead là role L4, khai trong `registry/roles/extended/` (không phải core) để L1–L3 không bị ảnh hưởng.

## 3. Danh mục role mở rộng (template trong `registry/roles/extended/`)

Role mở rộng có cùng schema role như core (`id`, `role`, `group: extended`, `prompt`, `tools`, `max_llm_calls`) và phải có output schema riêng. Quyền tool tối thiểu cần thiết.

| Role | Mục đích | Tool | Output | Trục đích |
| --- | --- | --- | --- | --- |
| `team_lead` | Đề xuất đội, giao subtask | Không | `TeamDecision` | C, A |
| `security_reviewer` | Rà soát bảo mật patch theo `security_architecture` | Chỉ đọc (`read_file`, `search_repo`) | `SecurityReport`: findings có severity, file/line, AC/NFR liên quan | S |
| `test_specialist` | Viết/bổ sung test cho phần khó kiểm (mock, tích hợp, hồi quy) | Đọc + ghi **chỉ file test mới**, `run_tests` | `TestReport` (cùng schema Tester) | T (Testability thấp) |
| `integrator` | Xử lý xung đột sau merge theo Global Contract | Đọc/sửa trong workspace tích hợp, `run_tests` | Patch hợp nhất + `IntegrationNote` | C, A |
| `subtask_coder` | Instance của Coder với phạm vi `owned_paths` | Như Coder, bị chặn ghi ngoài `owned_paths` | Patch tích lũy theo baseline | C |

`subtask_coder` không phải role mới về trách nhiệm mà là instance của Coder có phạm vi hẹp; theo định nghĩa L3/L4, tạo thêm instance là team formation. Role khác (`architect_reviewer`, `performance_reviewer`) để mở rộng sau, không bắt buộc cho thực nghiệm.

Ràng buộc chung:
- Không role nào được đọc gold patch, test_patch đánh giá hoặc sửa protected test (hợp đồng `implementation-only-v1`).
- Role mới không tự cấp quyền host/network; chạy trong sandbox/Docker và trần chung.
- Thêm role vào danh mục là thay đổi registry, phải tăng `registry/VERSION` và được ghi vào bản ghi kết quả.

## 4. Quyết định đội

### 4.1 Điểm quyết định

1. **Khởi tạo (t0):** sau khi có Requirement Profile `[C, T, S, A]` và WorkSpec, trước khi Developer chạy lần đầu.
2. **Giữa chừng:** tại cuối mỗi vòng (sau Tester/Reviewer), khi có tín hiệu runtime (mục 4.3). Mỗi điểm quyết định tính một lần gọi Team Lead.

### 4.2 Điều kiện kích hoạt khi khởi tạo (đề xuất, ngưỡng cần hiệu chỉnh bằng oracle data)

Điểm số 1–5 theo rubric của `raac.ra`; **Testability thấp = khó kiểm thử**.

| Điều kiện | Thao tác đề xuất |
| --- | --- |
| `S ≥ 4` hoặc task chạm `security_architecture` | Thêm `security_reviewer` |
| `T ≤ 2` | Thêm `test_specialist` |
| `C ≥ 4` hoặc `A ≥ 4` và tách được ≥ 2 subtask có `owned_paths` rời nhau | Chia subtask, thêm `subtask_coder` (tối đa 3) và `integrator` |
| Điều kiện khác | Đội mặc định (mục 4.4) |

Các ngưỡng là giả thuyết khởi điểm; Proposal chưa ấn định ngưỡng tối ưu. Team Lead được xét thêm nội dung WorkSpec; Policy chỉ kiểm tính hợp lệ, không bắt buộc Team Lead tuân theo bảng này.

### 4.3 Tín hiệu runtime cho quyết định giữa chừng

| Tín hiệu | Nguồn | Thao tác có thể đề xuất |
| --- | --- | --- |
| Reviewer/Tester nêu vấn đề bảo mật chưa có trong profile | `ReviewReport`, `TestReport` | Thêm `security_reviewer` |
| Test fail lặp lại cùng nguyên nhân sau ≥ 2 vòng | Lịch sử vòng | Thêm `test_specialist` hoặc thay Coder bằng `subtask_coder` hẹp hơn |
| `conflict_detected` sau merge | Merge Detector | Thêm `integrator` |
| Một subtask bị chặn (`dependency_blocked`) | Board | Bớt hoặc thay subtask_coder |
| Hết budget còn lại dưới ngưỡng | `usage` | Bớt role không thiết yếu |

Một test fail đơn lẻ không tự là lý do thay đổi đội.

### 4.4 Đội mặc định

Khi Team Lead không đề xuất, lỗi schema hoặc Policy từ chối: đội tương đương L3 `chain_review_loop` với danh mục core (Analyst, Planner, Coder, Tester, Reviewer). Ghi rõ là fallback; không coi là quyết định thích ứng thành công.

## 5. Ba thao tác: thêm, bớt, thay

| Thao tác | Ý nghĩa | Điều kiện hợp lệ | Hiệu lực | Không được |
| --- | --- | --- | --- | --- |
| `add` | Thêm role hoặc instance mới vào đội | Role có trong registry (core/extended); chưa vượt trần; có lý do và `ac_ids`/`owned_paths` nếu là writer | Từ wave kế tiếp; không chèn vào wave đang chạy | Thêm role không có trong registry |
| `remove` | Bỏ role hoặc instance khỏi đội | Không bỏ role bắt buộc (Coder hoặc subtask_coder, Tester, Reviewer); instance đang ghi dở thì phải hoàn tất hoặc hủy có dọn dẹp | Từ wave kế tiếp; instance đang chạy bị hủy có cấu trúc | Bỏ Reviewer ở vòng cuối |
| `replace` | Thay role A bằng role B có trách nhiệm khác hoặc hẹp hơn | B hợp lệ như `add`; A được bàn giao artifact và phạm vi | Từ wave kế tiếp; instance A đóng, B nhận handoff | Đổi tên role mà không đổi trách nhiệm (không tính là replace) |

Gọi lại cùng một Coder không phải `add`. Kích hoạt lại một role core đã có là routing (quyền L2), không tính là thay đổi đội.

Role bắt buộc của đội cuối: ít nhất một writer (Coder hoặc subtask_coder), một Tester và một Reviewer duyệt patch cuối trước khi kết thúc.

## 6. Giới hạn (đề xuất khởi điểm; nằm trong `configs/levels/l4_team_formation.yaml`)

| Tham số | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `max_agents_total` | 8 | Tổng agent instance đã tạo trong run |
| `max_parallel_writers` | 3 | Writer chạy cùng lúc |
| `max_parallel_agents` | 4 | Tổng agent chạy cùng lúc |
| `max_team_changes` | 3 | Số lần quyết định đội làm thay đổi (add/remove/replace) |
| `max_subtasks` | 3 | Subtask tối đa mỗi task |
| `max_rounds` | 3 | Kế thừa L1/L3: số lần writer tạo hoặc cập nhật patch |
| `max_team_lead_calls` | 3 | Số lần gọi Team Lead |
| Token/timeout/số call | Dùng chung run | Không reset khi đổi đội; mọi nhánh cộng dồn vào cùng ngân sách |

Vượt trần thì Policy từ chối phần vượt, ghi `team_decision_rejected` kèm lý do. Hết budget thì dừng run theo `stop_reason` chung và hủy mọi nhánh anh em.

## 7. State và Team Board

`CollaborationState` vẫn là nguồn sự thật duy nhất (checkpoint, resume). Team Board là **vùng `board` trong state**, không phải store độc lập; chỉ ghi qua orchestrator (reducer). Kết quả cuối của board được gom lại vào state sau barrier. Xem phân biệt state/board trong [idea.md](idea.md) mục 1.3.

| Vùng | Quyền | Nội dung |
| --- | --- | --- |
| Immutable contract | Chỉ đọc | WorkSpec, `ac_catalog`, Global Contract (`CON/API/DM`, `global_runtime_contract`), `baseline_ref`, Requirement Profile |
| `team` | Orchestrator ghi | Danh sách instance hiện tại và lịch sử thay đổi đội |
| `ledger` | Orchestrator ghi, agent đề xuất | Mỗi dòng: `{subtask_id, agent_instance_id, role, wave_id, owned_paths, status, patch_ref, patch_hash, test_ref}` |
| `signals` | Append-only, giới hạn kích thước | Ghi chú có schema: `interface_published`, `assumption`, `dependency_blocked`, `security_finding`, `conflict_flagged` |

Mỗi agent song song đọc **snapshot chỉ đọc** của state tại thời điểm fan-out và chỉ trả kết quả của mình; không agent nào ghi trực tiếp vào state. State vòng đời Layer 1 của từng agent tách biệt, liên kết bằng `run_id`, `agent_instance_id`, `work_id = <task_id>:sub:<subtask_id>`.

## 8. Song song, cô lập workspace và merge

### 8.1 Hai loại song song

| Loại | Pha | Cần workspace riêng |
| --- | --- | --- |
| Chuyên gia chỉ đọc (Tester ∥ Reviewer ∥ `security_reviewer`) trên cùng `patch_hash` | **4A** | Không, dùng chung snapshot |
| Nhiều writer (`subtask_coder`) trên subtask rời nhau, kèm `integrator` | **4B** | **Có** |

`test_specialist` ghi file test mới nên được xếp như writer cho mục đích cô lập, dù chỉ ghi vào đường dẫn test được giao.

### 8.2 Quy trình pha 4B

1. **Gán phạm vi:** Team Lead đề xuất `subtask_id`, `ac_ids`, `owned_paths`; Policy kiểm tra các `owned_paths` trong cùng wave không chồng lấn và nằm trong repo.
2. **Cô lập:** mỗi writer có workspace riêng từ cùng `baseline_ref`: `git worktree` nếu là git repo; copy thư mục tạm nếu không; volume riêng trên Docker. Chặn ghi ngoài `owned_paths` ở Layer 3 (mở rộng edit guard).
3. **Fan-out:** chạy writer song song, mỗi writer có context, memory, checkpoint, `work_id` riêng.
4. **Barrier:** chờ mọi writer trả patch đúng schema, hoặc lỗi/timeout. Lỗi một nhánh được ghi lại; nhánh thành công không bị bỏ.
5. **Merge:** gộp các patch trên workspace tích hợp sạch theo thứ tự xác định (theo `subtask_id`), ví dụ `git apply --3way`. Xung đột văn bản thì phát `conflict_detected`.
6. **Kiểm tích hợp:** dù merge sạch vẫn chạy test tích hợp và hồi quy theo `global_runtime_contract.test_command`. Lỗi import/chữ ký/hợp đồng được ghi là xung đột ngữ nghĩa.
7. **Giải xung đột:** `integrator` xử lý theo WorkSpec và Global Contract; không được đổi public API/DB schema trái `CON/API/DM`. Nếu bất khả thi thì báo xung đột, không âm thầm sửa hợp đồng.
8. Sau merge, mọi test/review cũ **mất hiệu lực**; Tester và Reviewer chạy lại trên `patch_hash` mới.
9. **Dọn dẹp** worktree/volume cả khi lỗi hoặc hủy.

Nhiều patch độc lập chưa được coi là kết quả đã tích hợp: chỉ patch sau bước 6 (và qua Reviewer) mới là kết quả của L4.

### 8.3 Điều kiện chia subtask hợp lệ

Subtask hợp lệ khi: có `ac_ids` rõ; `owned_paths` rời nhau trong wave; dependency giữa subtask tạo DAG không chu trình (subtask phụ thuộc chạy ở wave sau); tổng AC của các subtask phủ AC của task. Không đạt thì Policy từ chối và dùng đội mặc định hoặc chạy subtask tuần tự.

## 9. Schema quyết định đội

`TeamDecision` do Team Lead trả. Framework bổ sung metadata (`source`, `decision_id`, thời điểm) sau khi Policy duyệt; model không tự khai các field đó.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TeamDecision",
  "type": "object",
  "additionalProperties": false,
  "required": ["decision_point", "reason", "operations"],
  "properties": {
    "decision_point": { "enum": ["initial", "mid_run"] },
    "reason": { "type": "string", "minLength": 1 },
    "evidence": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Tham chiếu tín hiệu: profile axis, signal id, report id"
    },
    "operations": {
      "type": "array",
      "maxItems": 4,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["op", "role"],
        "properties": {
          "op": { "enum": ["add", "remove", "replace"] },
          "role": { "type": "string", "description": "Role trong registry core/extended" },
          "target_instance_id": { "type": ["string", "null"], "description": "Bắt buộc với remove/replace" },
          "subtask_id": { "type": ["string", "null"] },
          "ac_ids": { "type": "array", "items": { "type": "string" } },
          "owned_paths": { "type": "array", "items": { "type": "string" } },
          "depends_on": { "type": "array", "items": { "type": "string" } },
          "wave": { "type": "integer", "minimum": 1 },
          "reason": { "type": "string" }
        }
      }
    },
    "topology": { "enum": ["chain", "chain_review_loop", "star", null] },
    "max_rounds": { "type": ["integer", "null"], "minimum": 1 }
  }
}
```

Quy tắc kiểm bằng code (Policy), không dựa vào schema JSON:
- `role` có trong registry; `target_instance_id` tồn tại với `remove/replace`.
- `owned_paths` tương đối POSIX, không chồng lấn trong cùng wave, không trỏ vào protected test hay vùng ngoài repo.
- `ac_ids` thuộc `ac_catalog`; `depends_on` không tạo chu trình.
- Không vượt trần mục 6; đội sau thao tác vẫn thỏa role bắt buộc (mục 5).
- Từ chối từng thao tác vi phạm, không thực thi một phần mà làm đội mất nhất quán; ghi `team_decision_rejected`.

Bản ghi quyết định đã duyệt:

```json
{
  "decision_id": "run-001:td-2",
  "source": "llm",
  "decision_point": "mid_run",
  "team_before": ["coder#1", "tester#1", "reviewer#1"],
  "team_after": ["coder#1", "tester#1", "reviewer#1", "security_reviewer#1"],
  "operations": [{ "op": "add", "role": "security_reviewer" }],
  "accepted": true,
  "rejected_reasons": [],
  "reason": "Reviewer nêu truy vấn DB không lọc quyền ở route công khai",
  "cost": { "calls": 1, "tokens": 0 }
}
```

`source` nhận `llm`, `fallback` hoặc `forced`.

## 10. Quan sát và kiểm chứng

### 10.1 Sự kiện log (mở rộng JSONL hiện có)

`team_decision`, `team_decision_rejected`, `team_changed` (trước/sau), `agent_spawned`, `agent_removed`, `wave_started`, `wave_barrier_reached`, `merge_started`, `conflict_detected` (loại: văn bản/ngữ nghĩa), `conflict_resolved`, `integration_test_result`, `board_signal`, `worktree_cleaned`. Mỗi event có `run_id`, `task_id`, `level=L4`, `agent_instance_id`, `parent_work_id`, `wave_id`. Mỗi approval gắn `agent_instance_id` và chỉ chặn đúng nhánh.

### 10.2 Metric (theo Proposal 7.2)

Chi phí formation (số call Team Lead, token), số agent, số lần đổi đội, số wave, `conflict_rate`, số message/signals, token/thời gian từng nhánh và tổng, số vòng đã chạy, nguyên nhân dừng. Tách riêng tài nguyên formation khỏi tài nguyên thực thi để đánh giá lợi ích biên của L4.

### 10.3 Test bắt buộc (nghiệm thu L4)

| Nhóm | Case |
| --- | --- |
| Policy | Từ chối role ngoài registry; vượt trần agent/thay đổi; `owned_paths` chồng lấn; bỏ role bắt buộc; thao tác sai thì không áp dụng một phần |
| Fallback | Team Lead trả JSON sai thì đội mặc định, `source: fallback` |
| Pha 4A | Chuyên gia chỉ đọc chạy song song trên cùng `patch_hash`; một nhánh lỗi không làm mất kết quả nhánh khác; kết quả gom đúng thứ tự |
| Pha 4B | Hai writer rời nhau merge sạch; xung đột văn bản bị bắt; xung đột ngữ nghĩa (đổi chữ ký hàm) bị bắt ở test tích hợp; Integrator xử lý; ghi ngoài `owned_paths` bị chặn |
| Vòng đời | Hủy nhánh khi hết budget; timeout; dọn worktree khi lỗi; resume giữa wave không chạy lại nhánh đã xong; resume không trộn cấu hình khác |
| Tài nguyên | Token cộng dồn đúng từ mọi nhánh vào cùng ngân sách; không reset khi đổi đội |
| Cách ly | Không đọc gold/test patch; memory và workspace không rò giữa subagent và giữa level |
| Level | Trace chỉ dùng quyền L4 đã công bố; không escalation sang level khác trong benchmark oracle |

Theo `level_definitions.md` mục 10, L4 chỉ được nghiệm thu khi có case kiểm chứng và trace cho từng quyền thích ứng đã công bố; có thư mục `l4_team_formation` hay nhiều role chưa đủ.

## 11. Kịch bản ví dụ

### Kịch bản A — Phát hiện vấn đề bảo mật giữa chừng (4A, thao tác `add`)

Task: "thêm API xem danh sách đơn hàng". Profile ban đầu `[C3, T4, S2, A2]`, nên đội khởi tạo là mặc định (chain_review_loop).

1. Vòng 1: Coder tạo patch, Tester pass.
2. Reviewer ghi comment blocker: truy vấn lấy đơn theo id không lọc theo `user_id`, có nguy cơ truy cập đơn của người khác.
3. Cuối vòng, Orchestrator gọi Team Lead với tín hiệu `security_finding`. Team Lead đề xuất `add security_reviewer`.
4. Policy duyệt: role hợp lệ, còn trần (đội 3 → 4). `team_changed` được ghi.
5. Vòng 2: Coder sửa; Tester và `security_reviewer` chạy song song trên cùng `patch_hash` (fan-out chỉ đọc); Reviewer chạy sau khi gom.
6. Reviewer approve, test đạt, không còn blocker thì PASS.

Bằng chứng cần có: `team_decision` với evidence trỏ tới comment Reviewer, `team_changed` trước/sau, hai nhánh song song với thời gian chồng nhau, chi phí formation.

### Kịch bản B — Hai subtask độc lập có xung đột ngữ nghĩa (4B, thao tác `add` rồi `add integrator`)

Task lớn: "thêm giỏ hàng và thanh toán". Profile `[C5, T3, S3, A4]`.

1. Khởi tạo: Team Lead chia hai subtask: `S1` (`cart/`, AC1–AC3) và `S2` (`payment/`, AC4–AC5), `owned_paths` rời nhau, cùng wave 1. Policy duyệt.
2. Mỗi `subtask_coder` làm trong worktree riêng từ cùng baseline, chạy song song.
3. Barrier: cả hai nộp patch. Merge không có xung đột văn bản.
4. Test tích hợp thất bại: `payment/` gọi `cart.total()` với chữ ký cũ, trong khi `S1` đã đổi chữ ký. Ghi `conflict_detected` loại ngữ nghĩa.
5. Team Lead đề xuất `add integrator`; Policy duyệt (số thay đổi 2/3). Integrator sửa chỗ gọi theo Global Contract `API-n`, không đổi API công khai.
6. Tester và Reviewer chạy lại trên patch hợp nhất, PASS thì kết thúc; worktree được dọn.

Bằng chứng cần có: ledger hai subtask, `merge_started`, `conflict_detected` (ngữ nghĩa), `integration_test_result`, `team_changed` thêm integrator, patch hash sau merge, chi phí từng nhánh.

## 12. Rủi ro và điểm cần nhóm duyệt

| # | Điểm cần quyết định | Đề xuất |
| --- | --- | --- |
| 1 | Team Lead là role mới: xếp L4 hay core? | Role mở rộng thuộc L4, không đụng L1–L3 (nhất quán với Hub) |
| 2 | Có đưa L4 vào phạm vi ĐACN hay chỉ L1–L3 và để interface L4? | Nếu thiếu thời gian, làm pha 4A trước, 4B để ĐATN |
| 3 | Pha 4B có bắt buộc không? | Không bắt buộc cho thực nghiệm; 4A đã tạo khác biệt cho trục S và T |
| 4 | Ngưỡng điều kiện kích hoạt và các trần ở mục 4.2, 6 | Giá trị khởi điểm; hiệu chỉnh bằng pilot/oracle, ghi lại thay đổi |
| 5 | Cách tính tài nguyên: formation tính vào L4 | Có, tách riêng để phân tích lợi ích biên |
| 6 | Chi phí merge/xung đột lớn khiến L4 thua L3 | Chấp nhận như một kết quả; không ép L4 tốt hơn |
| 7 | Windows: worktree, đường dẫn dài, file bị khóa khi xóa | Có chiến lược dọn khi lỗi; thử trên Windows sớm |
| 8 | Tạo role theo schema lúc chạy (không chỉ chọn từ registry) | Ngoài phạm vi bản này; chỉ chọn từ danh mục có sẵn |

## 13. Thứ tự hiện thực đề xuất (sau khi nhóm duyệt)

1. Role mở rộng + prompt + output schema + `configs/levels/l4_team_formation.yaml` + test loader/registry.
2. Formation Policy và `TeamDecision` (mục 5, 6, 9), có test không cần LLM.
3. Team Lead + đội mặc định + log quyết định, chạy tuần tự (chưa song song).
4. Pha 4A: fan-out/barrier/fan-in, board tối thiểu, `security_reviewer`, `test_specialist`.
5. Pha 4B: worktree, ownership guard, Merge Detector, Integrator, test tích hợp.
6. Adapter Engine/benchmark cho L4, chạy trong cùng hợp đồng benchmark L1–L3, pilot thật.
