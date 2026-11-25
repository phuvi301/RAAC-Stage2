# Multi Agent: ý tưởng điều phối và chạy song song

Đây là tài liệu ý tưởng. Phần 1 là khái niệm, phần 2 là các mẫu workflow, phần 3 áp dụng vào RAAC, phần 4 là lộ trình. Thuật ngữ L1–L4 là level điều phối (xem [level_definitions.md](level_definitions.md)), khác bốn layer của harness ([architecture.md](architecture.md)).

## 1. Các khái niệm nền

### 1.1 Orchestration: trong và ngoài tiến trình

- **In-process:** các agent chạy trong cùng một chương trình và tiến trình, điều phối bằng code (LangGraph, asyncio). Đây là phạm vi của RAAC.
- **Out-process:** các agent là chương trình riêng, giao tiếp qua giao thức (A2A, RPC, HTTP). Dự án chỉ giữ interface/stub (Layer 4).

### 1.2 Hai nghĩa của "orchestrator"

Từ "orchestrator" hay bị dùng lẫn lộn giữa hai thứ:

| Nghĩa | Bản chất | Có LLM không |
| --- | --- | --- |
| **Orchestrator runtime** | Bộ chạy graph: gọi agent theo cạnh, chờ, gom kết quả, đếm vòng, kiểm budget/timeout, ghi checkpoint | Không, là code |
| **Orchestrator quyết định (Còn gọi là Coordinator giống trong swarm của Antigravity)** | Phần chọn ai làm, theo topology nào, mấy vòng, có thêm agent không | Thường là LLM, nhưng được code kiểm duyệt |

Mọi level đều cần orchestrator runtime (kể cả L1). Chỉ từ L2 trở lên mới có orchestrator quyết định. Xem bảng ở mục 3.2.

### 1.3 Shared State và Shared Board

Cả hai đều là "sổ tay chung trong RAM/đĩa" để agent không phải nói chuyện bằng văn bản tự do (tốn token, dễ hiểu sai). Nhưng vai trò khác nhau:

| | Shared State | Shared Board (blackboard) |
| --- | --- | --- |
| Trả lời câu hỏi | "Run đang ở đâu?" | "Các agent đang nói/làm gì với nhau?" |
| Nội dung | Dữ liệu điều khiển có kiểu: task, plan, patch, patch_hash, round, topology, verdict, stop_reason | Ledger phân việc (ai giữ subtask/đường dẫn nào, trạng thái), ghi chú tín hiệu ngắn (`interface_published`, `dependency_blocked`, `security_finding`, `conflict_flagged`) |
| Ai ghi | Chỉ orchestrator runtime, qua reducer | Agent đề xuất; orchestrator xác nhận rồi mới ghi |
| Vòng đời | Checkpoint, resume được, là nguồn sự thật | Chỉ gắn với một lần fan-out/team, có thể xóa sau khi gom |
| Ví dụ trong repo | `CollaborationState` (`collaboration/state.py`) | Chưa có |

Có thể dùng cả hai cùng lúc, không sao, với điều kiện giữ một nguồn sự thật duy nhất: board là một vùng (namespace) bên trong state hoặc một store phụ chỉ ghi qua orchestrator, và kết quả cuối của board luôn được gom ngược vào state. Không để hai nơi cùng giữ một sự thật (ví dụ `patch_hash` nằm cả ở state lẫn board). Với L1–L3 tuần tự, state là đủ; board chỉ thật sự cần khi có song song.

Cần phân biệt thêm state thứ ba: state vòng đời từng agent ở Layer 1 (`IDLE/PLANNING/...`), không dùng thay cho hai loại trên.

### 1.4 Cô lập ngữ cảnh

Nếu mọi agent ghi suy nghĩ, tool call, log vào cùng một chỗ thì context bị nhiễu và tràn. Có hai chế độ:

- **Sub-agent (context cô lập):** agent chính giao việc, sub-agent có context window riêng, thử sai thoải mái, chỉ trả kết luận ngắn có schema. Token nháp bị bỏ.
- **Handoff filtering:** agent này bàn giao cho agent kế, orchestrator lọc bỏ tool call/system prompt cũ, chỉ chuyển artifact theo schema. Đây là cách `chain_handoff.py` đang làm.

### 1.5 Cô lập workspace (khác cô lập ngữ cảnh)

Cô lập ngữ cảnh chỉ giải quyết token. Khi nhiều agent **sửa file** song song còn cần cô lập **workspace**: mỗi agent ghi vào bản sao riêng xuất phát từ cùng `baseline_ref`, rồi gộp sau. Agent chỉ đọc thì không cần bản sao riêng.

### 1.6 Đồng bộ khi chạy song song: Fan-out / Barrier / Fan-in

1. **Fan-out:** orchestrator khởi chạy nhiều agent cùng lúc (coroutine). Mỗi agent đọc snapshot chỉ-đọc của state và chỉ ghi vào phần của mình.
2. **Barrier:** orchestrator chờ đến khi mọi agent trả đúng schema, hoặc có agent lỗi/timeout.
3. **Fan-in:** orchestrator gộp kết quả vào state theo thứ tự xác định (deterministic), rồi phát hiện xung đột trước khi sang bước sau.

## 2. Các mẫu workflow chính

Các mẫu độc lập nhau và có thể kết hợp.

### 2.1 Handoff (luồng hiện tại của MAS)

1. Orchestrator gọi agent đầu, kết quả ghi vào state.
2. Agent xong thì bàn giao cho agent kế theo schema chuẩn.
3. Orchestrator lọc context, bỏ log tool nháp, giữ artifact cần thiết và gắn system prompt mới.
4. Agent cuối xong thì orchestrator ghi kết quả và kết thúc.

Đặc điểm: tuần tự, không ghi đè nhau, không cần cô lập workspace.

### 2.2 Sub-agents song song

1. Orchestrator khởi tạo phiên, state ghi `status: init`; Coordinator phân tích requirement thành các task nhỏ.
2. Fan-out các sub-agent, mỗi cái một task, context riêng (và workspace riêng nếu sửa code).
3. Barrier, sau đó fan-in: orchestrator gom kết quả JSON gọn vào state, phát hiện xung đột.
4. Coordinator đọc kết quả sạch từ state để chốt, context rất gọn.

## 3. Kết hợp vào dự án hiện tại (RAAC)

### 3.1 Hiện trạng liên quan

- Tầng Macro (`raac.pre_executor`) chạy một lần, sinh `tasks.json` (DAG `T1..Tn`, `execution_order`, `context_slice`).
- Tầng Micro chạy từng task `T_i` qua `raac.ra` (Requirement Profile `[C, T, S, A]`, trục Testability thay Ambiguity), Controller chọn level, rồi L1/L2/L3 thực thi. Controller và vòng lặp Micro chưa có.
- `Engine` + `CollaborationState` + `chain_handoff` là mẫu **Handoff** tuần tự (2.1). Chưa có fan-out, board, hay workspace cô lập (`WorkspaceTools` ghi thẳng vào một thư mục).
- L4 chưa hiện thực ([l4_spec.md](l4_spec.md) còn TODO); nhóm chưa duyệt đặc tả.

### 3.2 Orchestrator ở từng level

| Level | Orchestrator runtime (code) | Orchestrator quyết định | Quyết định cái gì |
| --- | --- | --- | --- |
| L1 | Có: graph cố định | Không có | Không, graph khai báo trước |
| L2 | Có | Router (LLM, đã có) | Bật/bỏ role trong danh mục |
| L3 | Có | Topology/rounds selector hoặc Hub (chọn tay/rules hiện nay) | Chọn topology, số vòng |
| L4 | Có, thêm fan-out/barrier/merge | Team Lead (LLM) + Formation Policy (code) | Thêm/bớt agent, giao subtask |

Nguyên tắc: LLM chỉ **đề xuất**, code **kiểm duyệt** (role hợp lệ, trần agent, trần budget, đường dẫn không chồng lấn). Quyết định luôn ghi log kèm lý do.

### 3.3 Chạy song song nên đặt ở đâu

| Loại song song | Level | Cần cô lập workspace | Rủi ro |
| --- | --- | --- | --- |
| Task `T_i` với `T_j` ở Macro | Không làm | - | `repo_snapshot_{i-1}` của Analyzer bị nhiễu; giữ tuần tự theo `execution_order` |
| Role chỉ-đọc cùng lúc (Tester ∥ Reviewer ∥ Security Reviewer trên cùng `patch_hash`) | L3 (fan-out nhẹ) hoặc L4 nếu thêm chuyên gia | Không (đọc cùng một snapshot) | Thấp |
| Nhiều Coder sửa subtask độc lập + Integrator | L4 | **Có** | Cao: xung đột, kiểm tích hợp |

Fan-out chỉ-đọc ở L3 là mở rộng topology nhưng không thêm role, nên vẫn nằm trong định nghĩa L3. Thêm agent instance hay chuyên gia mới là L4.

### 3.4 State và board trong RAAC

- Giữ `CollaborationState` làm nguồn sự thật. Thêm vùng `board` (ledger + signals) chỉ khi chạy song song (L4); board ghi qua reducer, kết quả gom lại vào state sau barrier.
- Vùng bất biến trong state: Global Contract (`CON/API/DM`, `global_runtime_contract`), WorkSpec, `ac_catalog`, `baseline_ref`. Agent chỉ đọc.
- Mỗi sub-agent có `agent_instance_id`, `work_id = <task_id>:sub:<subtask_id>`, memory và checkpoint Layer 1 riêng (đã scope theo run/work/agent). Budget token dùng chung toàn run, cộng dồn an toàn từ mọi nhánh; lỗi hoặc hết ngân sách thì hủy các nhánh anh em.
- Log thêm `subagent_id`, `wave_id`, `parent_work_id`; mỗi approval gắn `subagent_id` và chỉ chặn đúng nhánh đó.

### 3.5 Cô lập workspace và merge: trả lời "có phải tạo worktree rồi merge không?"

Đúng với các agent **ghi** chạy song song, với lưu ý:

1. Mỗi writer có workspace riêng từ cùng `baseline_ref`: `git worktree` nếu workspace là git repo; nếu không thì copy thư mục tạm; trên Docker thì volume riêng. Reader không cần.
2. Giao trước `owned_paths` cho mỗi writer và chặn ghi ngoài vùng (mở rộng edit guard Layer 3). Đây là phòng ngừa, không thay thế merge.
3. Barrier: mỗi writer nộp patch tích lũy so với baseline.
4. Merge xác định trên workspace tích hợp sạch (`git apply --3way` hoặc tương đương). Xung đột văn bản thì phát `conflict_detected`.
5. Dù merge sạch vẫn phải chạy test tích hợp: xung đột ngữ nghĩa (đổi chữ ký hàm, import) không hiện ra ở bước merge. Lỗi thì Integrator xử lý theo Global Contract.
6. Chỉ khi đó mới qua Tester/Reviewer; sau merge, test/review cũ không còn hiệu lực (như `patch_hash` hiện nay).
7. Dọn worktree cả khi lỗi hoặc hủy. Lưu ý Windows: đường dẫn dài và file bị khóa khi xóa worktree.

Số writer song song nên giới hạn nhỏ (2–3) vì chi phí merge/xung đột tăng nhanh; `conflict_rate` cũng là metric của Proposal.

## 4. Lộ trình hiện thực tiếp theo

**Bước 1. Hoàn thiện trục dọc Macro → RA (nền cho mọi thứ sau)**
1. Chạy thật với model cho `business_analyst → solution_architect → project_manager` (hiện mới `clarify` chạy thật) trên 1–2 yêu cầu mẫu.
2. Hoàn thiện `raac.ra`: chấm nhiều lần lấy đồng thuận, nhãn người chấm, đo độ tin cậy (kappa); rà lại rubric Testability (chiều ngược, độc lập với Complexity).

**Bước 2. Chốt L1–L3 trước khi so sánh**
1. Dọn tham chiếu role `Architect` còn sót trong code/L1 (role này đã bỏ khỏi registry) và cập nhật test liên quan.
2. Thống nhất L2 (hiện khóa `max_rounds=1`) với L1 mới (cyclic, `max_rounds=3`) theo tính kế thừa trong `level_definitions.md`.
3. Hoàn thiện L3: selector chọn topology (`chain`/`chain_review_loop`/`star`) và số vòng, thay cho chọn tay; Engine chung chạy L1–L3.

**Bước 3. Vòng lặp Micro và Controller (RAAC v1)**
1. Runner duyệt `execution_order`; mỗi `T_i` chụp `repo_snapshot_{i-1}` và `handoff_notes`, gọi `raac.ra`.
2. Controller rule-based ánh xạ `[C, T, S, A]` → L1/L2/L3, ghi lý do; chừa interface cho L4.
3. Task Gate sau mỗi `T_i`: test của task, regression theo `test_command` toàn cục, sinh `handoff_notes`; chừa hook escalate khi L1/L2 fail.

**Bước 4. Benchmark và Oracle (giai đoạn 2)**
1. Hoàn thiện benchmark runner và evaluator cho L1–L3 (và L4 khi có), cùng snapshot gốc, ghi token/thời gian, version/hash cấu hình.
2. Chạy bốn level độc lập (không controller, không escalation) để gán oracle bằng Pareto/utility; tách Macro cost và Per-task cost.

**Bước 5. Đặc tả L4 và song song (chỉ sau khi nhóm duyệt)**
1. Viết `l4_spec.md`: schema Team Plan, Formation Policy, board, `owned_paths`, giao thức merge/conflict. Nhóm review trước khi code.
2. Thêm role mở rộng: `security_reviewer`, `integrator`, tùy chọn `test_specialist`.
3. Pha 4A: fan-out chỉ-đọc (chuyên gia chạy song song với Tester/Reviewer), cần fan-out/barrier/fan-in và board tối thiểu, chưa cần worktree.
4. Pha 4B: nhiều Coder song song với workspace cô lập, merge, phát hiện xung đột, Integrator, test tích hợp.
5. Test bắt buộc: không ghi ngoài `owned_paths`, hủy nhánh khi hết budget, resume giữa wave, xung đột ngữ nghĩa bị bắt, dọn worktree.

**Bước 6. Đánh giá end-to-end**
1. Dual protocol: oracle benchmark độc lập (đường 1) và case study dự án đóng băng `tasks.json` so sánh `all-L1/L2/L3/L4` với RAAC (đường 2).
2. Sau đó mới thêm Runtime Monitor và escalation (giai đoạn 4 của Proposal).