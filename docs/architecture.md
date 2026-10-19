# Kiến trúc Harness

Harness bao quanh mỗi agent, gồm bốn layer dưới đây. Đây là thiết kế mục tiêu; các module hiện vẫn là scaffold và chưa hiện thực đầy đủ chức năng.

## Layer 1 – Interface & Lifecycle (Lớp Giao tiếp & Vòng đời)

Vị trí: `src/raac/harness/layer1_lifecycle/`.

### Agentic Event Loop (Reason – Act – Observe)

Vòng lặp cốt lõi của agent: nhận yêu cầu → suy luận (Reason) → quyết định hành động (Act) → quan sát kết quả trả về (Observe) → lặp lại cho đến khi hoàn thành mục tiêu hoặc đạt điều kiện dừng.

Module `event_loop.py` tổ chức vòng lặp bằng LangGraph, phối hợp với các layer còn lại để chuẩn bị ngữ cảnh, gọi LLM và thực thi công cụ.

### State Machine (Máy trạng thái)

Module `state_machine.py` quản lý các trạng thái:

| Trạng thái | Ý nghĩa |
| --- | --- |
| `IDLE` | Chờ yêu cầu hoặc chưa bắt đầu xử lý. |
| `PLANNING` | Suy luận và lựa chọn hành động tiếp theo. |
| `EXECUTING_TOOL` | Đang thực thi công cụ. |
| `WAITING_HUMAN_APPROVAL` | Tạm dừng để chờ người dùng phê duyệt. |
| `FAILED` | Kết thúc do lỗi không thể tiếp tục trong lượt chạy hiện tại. |
| `COMPLETED` | Hoàn thành mục tiêu. |

Máy trạng thái kết hợp `checkpoint.py` để lưu và khôi phục tiến trình, và `approval.py` để xử lý phê duyệt. Thiết kế này cho phép tạm dừng, resume khi có thể phục hồi từ lỗi hoặc chờ phản hồi bất đồng bộ mà không mất dấu tiến trình. State machine cần checkpoint và chính sách phục hồi đi kèm để thực hiện được khả năng này.

## Layer 2 – Intelligence Core (Lõi Trí tuệ & Ngữ cảnh)

Vị trí: `src/raac/harness/layer2_core/`.

### Context & Memory (Short/Long-term – RAG)

Context window của LLM có giới hạn và việc gửi nhiều ngữ cảnh làm tăng chi phí. Layer này quyết định thông tin cần giữ lại trong phiên và thông tin cần truy xuất khi cần:

- `context_manager.py`: chọn, cắt hoặc tóm tắt thông tin để phù hợp ngân sách context.
- `memory/short_term.py`: giữ lịch sử và thông tin làm việc trong phiên.
- `memory/long_term.py`: giao diện bộ nhớ dài hạn, có thể sử dụng Vector DB hoặc Graph DB.
- `memory/retriever.py`: truy xuất thông tin liên quan để bổ sung ngữ cảnh theo cơ chế RAG.

Trong benchmark, bộ nhớ dài hạn (RAG) phải tắt và short-term memory phải reset sau mỗi run. Backend RAG thực tế là phần mở rộng ngoài phạm vi thực nghiệm hai tuần.

### Prompt Hydrator

Module `prompt_hydrator.py` lắp ghép động thông tin từ memory, trạng thái hiện tại và danh sách công cụ khả dụng vào system prompt hoàn chỉnh trước khi gửi cho LLM suy luận. Nội dung truy xuất và dữ liệu bên ngoài phải được phân biệt với chỉ dẫn hệ thống đáng tin cậy.

## Layer 3 – Execution & Security (Thực thi & Bảo mật)

Vị trí: `src/raac/harness/layer3_execution/`.

### Tool Call Orchestration

Module `tool_orchestrator.py` điều phối các hàm/công cụ mà LLM yêu cầu, bao gồm thực thi song song khi phù hợp, retry có giới hạn và timeout. Các công cụ nằm trong `tools/`, dự kiến gồm đọc file, sửa file, chạy test và tìm kiếm repository.

### Sandbox (Docker/Wasm)

Khi agent thực thi code tự sinh hoặc chạy lệnh shell, tác vụ bắt buộc chạy trong môi trường cô lập như Docker hoặc WebAssembly để giảm rủi ro xóa nhầm dữ liệu hệ thống thật hoặc thực thi mã từ xa (RCE).

- `sandbox/docker_sandbox.py`: chuẩn bị checkout, áp patch, chạy test và dọn container.
- `sandbox/policy.py`: áp chính sách mạng, quyền truy cập và giới hạn CPU/RAM/thời gian.
- `sandbox/wasm_sandbox.py`: điểm mở rộng WebAssembly; hiện được xác định là stub.

Docker là backend mục tiêu cho MVP. Sandbox cần giới hạn quyền, mount và tài nguyên phù hợp; chỉ chạy trong container chưa đủ để bảo đảm cách ly.

### Guardrails & Cost Control

Các module trong `guardrails/` kiểm tra đầu vào/đầu ra nhằm giảm nguy cơ prompt injection, rò rỉ dữ liệu nhạy cảm và quyết định không đúng schema:

- `input_filter.py`: kiểm tra đầu vào và nội dung không đáng tin cậy.
- `output_filter.py`: kiểm tra đầu ra, phát hiện hoặc che dữ liệu nhạy cảm.
- `validators.py`: kiểm tra cấu trúc quyết định và tham số do LLM sinh ra.

Module `cost_control.py` giới hạn ngân sách token và số vòng lặp tối đa, phối hợp với timeout để ngắt các lượt chạy kéo dài. Usage được thu thập tại LLM client chung trong `src/raac/llm/`, giúp kiểm soát tổng chi phí theo run và agent.

## Layer 4 – Protocol Connectors (Cổng giao tiếp ngoại vi)

Vị trí: `src/raac/harness/layer4_connectors/`.

Đây là tầng adapter ở đáy kiến trúc, dịch yêu cầu thực thi từ Layer 3 thành thông điệp theo giao thức tương ứng và chuyển kết quả về định dạng nội bộ:

- `base.py`: giao diện adapter chung.
- `mcp_client.py`: MCP Client kết nối tới MCP Server bên ngoài để lấy dữ liệu hoặc chạy công cụ.
- `a2a_connector.py`: ủy quyền tác vụ cho agent khác trong mạng lưới qua giao thức A2A; hiện được xác định là stub.

Không phải công cụ nào cũng cần Layer 4: công cụ cục bộ được Layer 3 thực thi trực tiếp, còn yêu cầu giao tiếp ngoại vi đi qua connector. Lời gọi connector vẫn phải tuân thủ quyền công cụ, timeout và ngân sách do harness đặt ra.

### Cách ly bộ nhớ giữa các lần chạy

Tắt long-term memory (RAG) trong benchmark để tránh agent giữ thông tin từ task trước, gây rò rỉ dữ liệu giữa các lần chạy và phá tính tái lập. Short-term memory được reset sau mỗi run; run mới không dùng lại lịch sử hoặc checkpoint của task khác. Resume chỉ khôi phục bộ nhớ và checkpoint thuộc chính run đang tiếp tục.

### Giới hạn phạm vi connectors

MCP Client và A2A Connector không cần thiết để trả lời câu hỏi nghiên cứu về mức độ thích ứng. Trong hai tuần, chỉ xây interface connector; thêm adapter MCP tối thiểu nếu thật sự cần công cụ bên ngoài, còn A2A giữ ở mức stub. Không triển khai đầy đủ các giao thức này nếu không phục vụ dữ liệu thực nghiệm.

### Phân biệt state của agent và state điều phối

State machine ở Layer 1 quản lý vòng đời của **từng agent**, gồm trạng thái thực thi, chờ phê duyệt, lỗi và hoàn thành. State của LangGraph trong `collaboration/state.py` quản lý **luồng giữa các agent**, gồm kế hoạch chung, message, patch, topology, đội hiện tại và số vòng.

Hai loại state có trách nhiệm riêng, không dùng một loại thay cho loại còn lại. Khi checkpoint/resume, cần liên kết rõ `run_id`, `agent_id` và checkpoint/thread tương ứng để khôi phục đúng cả tiến trình từng agent lẫn vị trí điều phối. LangGraph có thể được dùng bên trong lifecycle nhưng state nội bộ đó vẫn phải tách biệt về schema và phạm vi với state collaboration.

### Truy vết version cho từng bản ghi kết quả

Mỗi bản ghi kết quả phải lưu version đọc từ `registry/VERSION` và version của các file trong `configs/` thực sự được dùng cho run, gồm cấu hình LLM, harness, level và benchmark. Dùng version định danh kèm hash nội dung từng file và lưu snapshot cấu hình hiệu lực để truy lại chính xác tham số đã chạy, kể cả khi file bị sửa mà chưa tăng version.

Snapshot cần phản ánh giá trị override đã áp dụng nhưng không chứa API key hoặc secret. Khi resume, đối chiếu registry và cấu hình với bản ghi ban đầu; nếu khác phải báo rõ, không âm thầm trộn hai cấu hình vào cùng run.

## Luồng xử lý tổng quát

1. Layer 1 nhận task, tạo hoặc khôi phục state và bắt đầu vòng lặp.
2. Layer 2 chuẩn bị context, memory và prompt; LLM client chung gửi yêu cầu suy luận.
3. Layer 1 xử lý quyết định: hoàn thành, chờ phê duyệt hoặc chuyển sang thực thi công cụ.
4. Layer 3 kiểm tra quyết định, quyền và ngân sách, rồi thực thi trong sandbox khi cần; lời gọi ngoại vi đi qua Layer 4.
5. Kết quả trở thành observation, được ghi vào state/memory và checkpoint; vòng lặp tiếp tục hoặc kết thúc theo điều kiện dừng.

`src/raac/harness/agent.py` lắp một agent từ role trong registry và các thành phần harness. Engine phối hợp nhiều agent ở `src/raac/collaboration/` dùng LangGraph để điều phối L1–L4; bốn level này là các cấp điều phối thực nghiệm, khác với bốn layer chức năng của harness.

Tiến độ hiện thực được theo dõi trong [checklist](implementation_checklist.md).

## Pipeline sandbox đã hiện thực

CLI raac sandbox gọi DockerSandbox độc lập với LLM/collaboration: dataset → profile → mirror cache → archive base commit → build/reuse image → baseline/candidate với volume riêng → log/XML/record → cleanup. Thiếu profile được báo profile_missing, không chạy test trên host. Xem [sandbox.md](sandbox.md).

Dockerfile.sandbox hiện là template build theo repo. Kiểm thử Docker thật đã xác nhận patch fail→pass, timeout, cách ly mạng/workspace, patch thử ghi file host và cleanup; các layer/level khác vẫn còn scaffold.

Sandbox baseline có runner batch riêng ở layer3_execution/sandbox/batch.py; benchmark/runner.py dành cho L1–L4 vẫn chưa hiện thực. Manifest/checkpoint của batch baseline tách biệt với checkpoint agent và collaboration; không dùng chúng thay cho nhau. Budget batch là 30 phút build + 30 phút test/task, một task tại một thời điểm. Profile hợp lệ và batch xử lý hết không đồng nghĩa toàn dataset đã chạy test thành công.


Evaluator SWE-bench được tách khỏi Task cung cấp cho agent. Dataset cleaned giữ issue ID;
artifact evaluator ánh xạ sang instance ID/PR tại cùng repo + base commit, chứa test patch,
gold patch và F2P/P2P. Image build chỉ chứa source base và dependency, không chứa gold.
Layer 3 nạp test patch/selector vào container phase; gold chỉ vào container validation
riêng. Chấm theo trạng thái từng test, không suy từ exit code hay tổng số pass. Batch
issue có fingerprint riêng với suite. Đây là evaluator test chuyển trạng thái, chưa
hoàn tất evaluator tổng hợp của benchmark L1–L4 hoặc A5.


Sandbox tách định danh build (source + recipe đã render) khỏi định danh đánh giá từng task (profile, artifact/selector, adapter, policy, registry và template). Cache layer dependency nằm trong builder sandbox có khóa độc quyền; builder bị xóa khi lỗi/timeout/hủy để dừng build phía daemon. Retention giữ ngân sách mềm 30 GiB và không xóa tài nguyên đang dùng. Source, môi trường hoặc bằng chứng không khớp sẽ không tái sử dụng validation.

Luồng chẩn đoán là preflight collect/import → pilot F2P → full F2P/P2P. Hai bước đầu có trạng thái riêng, không thay điều kiện nghiệm thu full. CLI hỗ trợ dừng có checkpoint; hàng đợi ngoài Codex dùng các batch riêng cho từng bước. Đây là tối ưu sandbox, không thay runner benchmark hoặc checkpoint của agent/LangGraph.

Adapter unittest ghi nhận cả failure từ `addSubTest`; subtest thành công không ghi đè failure trước đó. Profile có thể tạo môi trường dependency riêng ngoài checkout (ví dụ prefix conda của Xarray7036); source base, test/gold patch và volume theo phase vẫn tách như cũ. Một selector upstream rút gọn khớp nhiều case phải có trạng thái thống nhất; kết quả mixed không được tính là pass. Xem [báo cáo sửa validation](validation_failure_repairs.md).


Sandbox hỗ trợ profile `network_fixture` dạng enum (mặc định none): requests_httpbin hoặc requests_tarpit. Fixture dùng bridge internal+isolated riêng cho từng phase, không có Internet/gateway host; httpbin chạy trong sidecar từ image base, không nhận gold/candidate patch. Policy hiệu lực, hash network.py và cấu hình fixture tham gia fingerprint đánh giá; build identity giữ theo source/recipe. Cleanup thu hồi cả sidecar/network. Xem network_fixture_repairs.md.

Adapter unittest giữ ID parent khi in lỗi subtest và không đánh giá giá trị
tham số chỉ để định dạng báo cáo. Điều này tránh `repr(QuerySet)` chạy lại SQL
đang lỗi, giữ nguyên kết quả/traceback gốc và không thay đổi test hoặc tiêu chí
chấm. Xem [đối chứng artifact và sửa reporting](swebench_artifact_audit.md).
# Sửa sau đối chứng official

Profile có `selector_policy` versioned, mặc định `unique-v1`. Override riêng
Sympy-14237 dùng `sympy-all-matches-v1`: chạy mọi case tên bare khớp trong
test-patch modules, giữ đầy đủ node IDs và outcomes; tất cả phải pass để alias
pass. Policy thuộc fingerprint đánh giá, không recipe build. Không đổi nhãn
raw hoặc gate strict. Chi tiết và bằng chứng: [evidence_repairs.md](evidence_repairs.md).

## Tập benchmark cố định105task

`data/tasks/benchmark105-v1.json` là manifest chọn task từ dataset gốc120task;
không sao chép gold/test patch vào agentTask. Loader kiểm tra sourceSHA,
membership vàcount; profilecheck xét override theo nguồn120task nhưng chỉ
báo coverage của tập105. Batch fingerprint bao gồm manifest và nguồn; issue
runner kiểm tra evaluatorSHA theo manifest. Record ghi benchmarkID và hash
selection. Bằng chứng validation lịch sử có nhiều fingerprint, nên cần đóng
băng môi trường thực thi chung trước so sánhL1–L4. Xem [quy tắc105task](benchmark105.md).

Sau yêu cầu dọn môi trường15task, config mặc định và compatibility cũng chọn
manifest105. Các override dành riêng cho nhóm loại được gỡ; profile repo,
Gitcache và buildlayer chung vẫn giữ. RecipeDocker105task đã so sánh không đổi;
image riêng của task bị loại đã xóa, record/log không đổi. Muốn dựng lại task
bị loại phải chủ động khôi phục setup lịch sử; không nằm trong batch mặc định.
