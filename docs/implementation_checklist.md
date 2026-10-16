# Implementation Checklist - Tuần 8-9

Checklist theo dõi tiến độ triển khai framework RAAC, gồm hai phần.

- Phần A là công việc làm chung, tức bộ khung harness 4 layer bọc quanh mỗi agent.
- Phần B là công việc riêng của từng thành viên, gồm registry, các level L1-L4, benchmark và evaluator.

Cách dùng file này

- Đánh dấu `[x]` khi việc đã xong và đã được người khác kiểm tra hoặc có test chứng minh.
- Dùng `[~]` cho việc đang làm dở và ghi ngắn lý do hoặc vướng mắc ngay cạnh dòng đó.
- Chữ trong ngoặc cuối dòng là người phụ trách, Vũ, Vinh hoặc Tuấn, đổi lại nếu hoán đổi việc.
- Ghi ngày cập nhật cuối mỗi khi sửa file, và ghi vấn đề phát sinh vào mục nhật ký cuối file.

Cập nhật gần nhất ngày 2026-10-16

---

# Phần A - Làm chung (Harness)

Thư mục liên quan là `src/raac/harness/`, `src/raac/llm/`, `src/raac/registry/` và `configs/`.

## A0 - Nền tảng dùng chung

- [x] Tạo repo với cấu trúc thư mục đã chốt, gồm `registry`, `llm`, `harness`, `collaboration`, `benchmark`, `configs`, `docker`, `tests` (Vinh)
- [ ] Ghim version thư viện bằng `requirements.lock`, gồm LangGraph, Docker SDK, pytest, pydantic (Vinh)
- [x] Tạo `.env.example` và đảm bảo API key không bị commit (Vinh)
- [ ] Viết `Makefile` với các lệnh `test`, `pilot`, `freeze` (Vinh)
- [ ] Thống nhất quy ước branch, cách review và cách đặt tên commit (cả nhóm)

### LLM client dùng chung (`src/raac/llm/`)

- [ ] `client.py` gọi API, retry với backoff, đo latency (Vinh)
- [ ] Đọc số token vào và ra từ trường usage của API (Vinh)
- [ ] Ghi mọi lần gọi ra JSONL kèm `run_id`, `task_id`, `level`, `agent_id` (Vinh)
- [ ] `usage.py` cộng dồn token theo run, agent và level (Vinh)
- [ ] `pricing.py` quy đổi token ra chi phí theo model (Vinh)
- [ ] `mock_llm.py` trả về phản hồi giả có thể cấu hình, dùng cho unit test và 1-2 ngày đầu (Vinh)

### Registry dùng chung (`registry/`, `src/raac/registry/`)

- [ ] `models.py` định nghĩa schema pydantic cho role, tool, model và nhóm lõi/mở rộng (Vũ)
- [ ] `loader.py` nạp và kiểm tra registry, báo lỗi rõ khi thiếu trường (Vũ)
- [ ] File `registry/VERSION` và cơ chế ghi version này vào mọi bản ghi kết quả (Vũ)

## A1 - Layer 1, Interface và Vòng đời

Thư mục `src/raac/harness/layer1_lifecycle/`.

- [ ] `agent.py` lắp một Agent từ role trong registry và gắn đủ 4 layer (Vũ)
- [ ] `event_loop.py` hiện thực vòng lặp Reason, Act, Observe (Vũ)
- [ ] Vòng lặp dừng khi đạt mục tiêu, chạm số vòng tối đa hoặc bị cost guard chặn (Vũ)
- [ ] `state_machine.py` có đủ trạng thái `IDLE`, `PLANNING`, `EXECUTING_TOOL`, `WAITING_HUMAN_APPROVAL`, `FAILED`, `COMPLETED` (Vũ)
- [ ] Chuyển trạng thái bất hợp lệ bị từ chối và được log (Vũ)
- [ ] `checkpoint.py` lưu và khôi phục trạng thái agent ở mức một run (Vũ)
- [ ] `approval.py` có chế độ tự duyệt (auto-approve) cho benchmark (Vũ)
- [ ] Unit test cho từng chuyển trạng thái và cho resume sau khi bị ngắt (Vũ)

## A2 - Layer 2, Lõi Trí tuệ và Ngữ cảnh

Thư mục `src/raac/harness/layer2_core/`.

- [ ] `memory/short_term.py` lưu bộ nhớ trong phiên và được reset sau mỗi run (Vũ)
- [ ] `context_manager.py` cắt hoặc tóm tắt lịch sử để vừa context window (Vũ)
- [ ] `prompt_hydrator.py` ghép system prompt từ role, memory, state hiện tại và danh sách tool khả dụng (Vũ)
- [ ] Prompt hydrator ghi lại version prompt đã dùng vào log (Vũ)
- [ ] `memory/long_term.py` và `retriever.py` chỉ là stub có interface, long-term memory được tắt trong benchmark (Tuấn)
- [ ] Bổ sung phần injection cho thông tin topology và vòng hiện tại phục vụ L3, L4 (Tuấn)
- [ ] Unit test kiểm tra prompt sinh ra đúng và đủ các thành phần (Vũ)

## A3 - Layer 3, Thực thi và Bảo mật

Thư mục `src/raac/harness/layer3_execution/`.

### Tool orchestration

- [ ] `tool_orchestrator.py` chạy tool theo yêu cầu của LLM, hỗ trợ chạy song song có giới hạn (Vinh)
- [ ] Mỗi tool call có timeout và retry, lỗi được trả về agent dưới dạng quan sát (Vinh)
- [ ] `tools/` gồm tối thiểu `read_file`, `edit_file`, `run_tests`, `search_repo` (Vinh)

### Sandbox

- [ ] `docker_sandbox.py` checkout repo ở base commit, áp patch, chạy pytest có timeout, thu kết quả và dọn container (Vinh)
- [ ] `sandbox/policy.py` tắt network, giới hạn CPU, RAM và thời gian chạy (Vinh)
- [ ] `docker/Dockerfile.sandbox` dựng image chạy code của agent (Vinh)
- [ ] Kiểm thử sandbox bằng một patch cố tình độc hại và xác nhận không ảnh hưởng máy chủ (Vinh)
- [ ] `wasm_sandbox.py` chỉ là stub có interface (Vinh)

### Guardrails và kiểm soát chi phí

- [ ] `guardrails/input_filter.py` có quy tắc cơ bản chống prompt injection (Tuấn)
- [ ] `guardrails/output_filter.py` lọc secret và phát hiện lệnh nguy hiểm (Tuấn)
- [ ] `guardrails/validators.py` kiểm tra JSON quyết định của router, coordinator và team manager (Tuấn)
- [ ] `cost_control.py` dừng run khi vượt trần token hoặc số vòng, đọc số liệu từ `llm/usage.py` (Vinh)
- [ ] Test chứng minh một agent cố tình lặp vô hạn bị dừng đúng ở trần (Vinh)

## A4 - Layer 4, Protocol Connectors

Thư mục `src/raac/harness/layer4_connectors/`.

- [ ] `base.py` định nghĩa interface adapter chung (Vinh)
- [ ] `mcp_client.py` có adapter tối thiểu, chỉ làm đầy đủ nếu cần tool ngoài (Vinh)
- [ ] `a2a_connector.py` chỉ là stub có interface và docstring (Vinh)

## A5 - Tiêu chí hoàn thành harness (cả nhóm kiểm tra)

- [ ] Một agent mẫu chạy hết vòng Reason, Act, Observe với ít nhất một tool và một lần chạy trong sandbox
- [ ] Mọi lần gọi LLM đều đi qua `llm/client.py` và có log token
- [ ] Resume từ checkpoint cho kết quả giống như chạy liền mạch
- [ ] Cost guard dừng được run vượt trần và ghi rõ lý do dừng
- [ ] Unit test của từng layer chạy được bằng `mock_llm` mà không tốn token
- [ ] Phụ thuộc chỉ đi một chiều từ layer 1 xuống layer 4, không có import ngược

Ghi chú tiến độ phần A

- ........

---

# Phần B - Công việc riêng

# Lư Chấn Vũ

## Tuần 8 - Engine, Registry, L1, L2

### Registry và role lõi

- [ ] Viết các role lõi trong `registry/roles/core/` gồm Analyst, Planner, Coder, Tester, Reviewer
- [ ] Viết system prompt template cho từng role trong `registry/prompts/`
- [ ] Test từng role riêng lẻ trên một task và xác nhận đầu ra đúng định dạng

### Engine dùng chung (`src/raac/collaboration/`)

- [ ] `state.py` định nghĩa State dùng chung gồm requirement, plan, patch, kết quả test, message, số vòng và agent đang hoạt động
- [ ] `graph_builder.py` dựng graph LangGraph từ cấu hình level
- [ ] `engine.py` chạy graph và ghi log mỗi lần kích hoạt agent
- [ ] `levels/base.py` chốt interface level nhận `task`, `registry`, `config` và trả về kết quả
- [ ] Chia sẻ interface này cho Tuấn và Vinh ngay khi chốt

### L1 Fixed (`levels/l1_fixed.py`)

- [ ] Dựng graph tuyến tính Phân tích, Lập kế hoạch, Lập trình, Kiểm thử, Rà soát
- [ ] Mỗi node lấy agent từ registry và gọi qua LLM client
- [ ] Chạy thử trên 3 task mẫu và lưu log

### L2 Adaptive Routing (`levels/l2_routing.py`)

- [ ] Viết node router trả về JSON danh sách agent cần kích hoạt
- [ ] Dùng conditional edge để bỏ qua node không được chọn
- [ ] Luôn giữ tối thiểu Coder và Tester
- [ ] Kiểm tra JSON hợp lệ và có phương án dự phòng quay về L1 khi parse lỗi
- [ ] Log từng quyết định bỏ agent kèm lý do
- [ ] Chạy thử trên 3 task mẫu

### Tài liệu

- [ ] Viết `docs/level_definitions.md` mô tả biến cố định và biến tự do ở từng level
- [ ] Viết `docs/architecture.md` với sơ đồ 4 layer và phần collaboration

Tiêu chí thành công tuần 8

- [ ] L1 và L2 chạy end-to-end trên ít nhất 3 task mẫu và cho ra patch hoặc code chạy được test
- [ ] Log của L2 cho thấy rõ agent nào được bật hoặc bỏ qua và lý do
- [ ] Interface của Engine đủ rõ để L3 và L4 cắm vào mà không phải viết lại

Ghi chú tuần 8

- ........

## Tuần 9 - Tích hợp và chạy pilot

### Tích hợp

- [ ] Đăng ký cả bốn level vào harness của Vinh
- [ ] Smoke test 3 task trên mỗi level và xác nhận đường chạy thông suốt
- [ ] Rà lại L1 và L2 sau khi chạy trên task thật và sửa các lỗi lộ ra

### Chọn tập pilot (`benchmark/pilot_subset.py`)

- [ ] Chọn khoảng 10-15 task dựa trên human labels của giai đoạn 1
- [ ] Đảm bảo có đủ task dễ, mơ hồ, nhạy cảm bảo mật và ảnh hưởng kiến trúc
- [ ] Chạy test ở trạng thái gốc để xác nhận test fail đúng chỗ và ổn định

### Chạy pilot

- [ ] Chạy pilot theo từng batch nhỏ và theo dõi log
- [ ] Phân loại lỗi thành lỗi hệ thống (timeout, crash, output rỗng) và lỗi do agent làm sai thật
- [ ] Sửa các lỗi hệ thống và ghi lại các lỗi còn tồn đọng
- [ ] Chạy lặp 1-2 task vài lần để đo độ dao động giữa các lần chạy
- [ ] Ghi nhật ký pilot vào `docs/pilot_log.md`

Tiêu chí thành công tuần 9

- [ ] Tập pilot chạy xong trên cả 4 level với tỉ lệ run hoàn tất không lỗi hệ thống đạt mức nhóm đặt ra (ví dụ từ 90% trở lên)
- [ ] Mọi lượt chạy đều có bản ghi kết quả đầy đủ, không thiếu metric
- [ ] Danh sách lỗi tồn đọng rõ ràng và đã có người nhận xử lý

Ghi chú tuần 9

- ........

---

# Nguyễn Phú Vinh

## Tuần 8 - Hạ tầng benchmark và logging

### Việc chặn đường của cả nhóm (ngày 1-2)

- [ ] Hoàn thành `llm/client.py` và `mock_llm.py` (xem mục A0) và báo cho Vũ và Tuấn có thể dùng

### Dataset và môi trường chạy

- [ ] `benchmark/dataset.py` đọc task gồm nội dung issue, repo, base commit, file test
- [ ] Chuẩn hóa task về một schema duy nhất
- [ ] Dựng pipeline Docker, mỗi run checkout repo, áp patch, chạy pytest có timeout, thu kết quả rồi dọn container
- [ ] Xác nhận mỗi run bắt đầu từ môi trường sạch

### Harness chạy hàng loạt (`benchmark/runner.py`)

- [ ] Lặp qua các cặp (task, level, lần lặp)
- [ ] Chạy song song có giới hạn
- [ ] Hỗ trợ resume, bỏ qua run đã xong khi chạy lại
- [ ] Có retry và timeout cho từng run
- [ ] Có budget guard dừng run khi vượt trần token

### Bản ghi kết quả (`benchmark/run_record.py`)

- [ ] Định nghĩa schema gồm trạng thái, patch, kết quả test, token, số lần gọi, latency, số message, số vòng, agent được kích hoạt, lỗi
- [ ] Ghi commit hash, version registry, version prompt và cấu hình model vào mỗi bản ghi

### Cấu hình và kiểm tra

- [ ] Gom cấu hình LLM (model, temperature, seed nếu có, prompt version) vào `configs/llm.yaml` có version
- [ ] Viết `dummy level` chỉ gọi LLM một lần để kiểm tra harness trước khi L1 xong
- [ ] Viết `cli.py` với lệnh chạy một (task, level)

Tiêu chí thành công tuần 8

- [ ] Một lệnh duy nhất chạy được một (task, level) và sinh ra một bản ghi kết quả đầy đủ
- [ ] Chạy lại cùng một task trong cùng cấu hình thì môi trường và log nhất quán
- [ ] Cấu hình LLM nằm trong một file config duy nhất và có version

Ghi chú tuần 8

- ........

## Tuần 9 - Evaluator, chi phí và chuẩn bị chạy full

### Evaluator (`benchmark/evaluator/`)

- [ ] `completion.py` tính task completion và test pass rate, chạy cả test mục tiêu lẫn test có sẵn để phát hiện hồi quy
- [ ] `coverage.py` tách issue thành các yêu cầu con, dùng LLM giám khảo với rubric kiểm tra patch đáp ứng từng yêu cầu
- [ ] Cố định model và temperature của giám khảo
- [ ] `quality.py` đo số cảnh báo linter tăng thêm, độ phức tạp bằng radon và kích thước patch
- [ ] `conflict.py` định nghĩa rõ conflict rate và đo giống nhau ở mọi level
- [ ] Kiểm tra evaluator thủ công trên khoảng 10 mẫu, so với kết quả tự đọc

### Phân tích pilot và chi phí (`benchmark/analysis/`)

- [ ] `pilot_report.py` tính trung bình và độ lệch của từng metric theo level
- [ ] Kiểm tra level cao có thật sự tốn token hơn không và metric nào không phân biệt được các level
- [ ] `cost_estimator.py` ước tính chi phí và thời gian full run từ số liệu pilot
- [ ] Đưa ra vài phương án kích thước dataset phù hợp ngân sách
- [ ] `utility.py` viết nháp utility function cân bằng chất lượng và chi phí, kèm quy tắc ưu tiên level rẻ hơn khi kết quả gần ngang nhau

### Chuẩn bị chạy full

- [ ] Lập lịch chạy và phân công cho tuần 10-11
- [ ] Đóng băng cấu hình bằng `make freeze` sau khi cả nhóm đồng ý

Tiêu chí thành công tuần 9

- [ ] Evaluator cho kết quả đúng khi đối chiếu thủ công trên các mẫu đã kiểm tra tay
- [ ] Có bảng ước tính chi phí, thời gian và kích thước dataset cho lần chạy full, được cả nhóm đồng ý
- [ ] Có bản nháp utility function và kế hoạch tuần 10-11 sẵn sàng

Ghi chú tuần 9

- ........

---

# Nguyễn Minh Tuấn

## Tuần 8 - L3 và đặc tả L4

### Topology và L3 (`collaboration/topologies.py`, `levels/l3_collaboration.py`)

- [ ] Liệt kê tập topology hữu hạn gồm chuỗi, hình sao qua orchestrator, vòng Coder-Reviewer và debate giữa hai agent
- [ ] Viết node coordinator trả về JSON gồm topology và số vòng tối đa
- [ ] Engine dựng graph theo lựa chọn của coordinator rồi mới chạy
- [ ] Sau mỗi vòng có bước đánh giá "đã đủ chưa" để quyết định lặp tiếp hay dừng
- [ ] Dừng khi test qua, reviewer chấp thuận hoặc chạm số vòng tối đa, và log lý do dừng
- [ ] Chạy L3 trên 3 task mẫu, xem log để chắc topology và số vòng thay đổi theo task

### Guardrails cho các quyết định của LLM

- [ ] Viết `validators.py` kiểm tra JSON của coordinator
- [ ] Viết `input_filter.py` và `output_filter.py` bản cơ bản (xem mục A3)

### Đặc tả L4 (`docs/l4_spec.md`)

- [ ] Liệt kê danh sách role mở rộng dưới dạng template trong `registry/roles/extended/`
- [ ] Định nghĩa điều kiện kích hoạt thêm role
- [ ] Mô tả ba thao tác thêm, bớt và thay role
- [ ] Đặt giới hạn số agent và số lần thay đổi
- [ ] Viết JSON schema cho quyết định về đội
- [ ] Thêm ít nhất hai kịch bản ví dụ, chẳng hạn task phát hiện vấn đề bảo mật giữa chừng
- [ ] Cả nhóm review và duyệt đặc tả trước khi sang tuần 9

Tiêu chí thành công tuần 8

- [ ] L3 chạy end-to-end trên ít nhất 3 task mẫu và log được topology cùng số vòng
- [ ] Có điều kiện dừng rõ ràng, không có run nào bị treo hoặc lặp vô hạn
- [ ] Đặc tả L4 đủ chi tiết để người khác hiện thực và cả nhóm đã review

Ghi chú tuần 8

- ........

## Tuần 9 - Hiện thực L4

### Team Manager (`levels/l4_team_formation.py`)

- [ ] Viết node Team Manager đề xuất đội ban đầu từ pool role
- [ ] Sau mỗi vòng, đọc tín hiệu như test fail, ghi chú của reviewer và bất đồng để cập nhật đội
- [ ] Engine tạo agent từ template trong registry
- [ ] Kiểm tra quyết định của LLM trước khi áp dụng, quyết định không hợp lệ thì bỏ qua và ghi log
- [ ] Đặt ràng buộc chi phí gồm số agent tối đa và số vòng tối đa
- [ ] Log thành phần đội ở từng vòng

### Kiểm thử

- [ ] Viết unit test cho logic thêm, bớt và thay role bằng LLM giả
- [ ] Chạy L4 trên 3 task thật và so sánh log với L3
- [ ] Sửa các lỗi của L3 phát sinh khi tích hợp vào harness

Tiêu chí thành công tuần 9

- [ ] L4 chạy end-to-end trên ít nhất 3 task mẫu và log thành phần đội ở từng bước
- [ ] Mọi run của L4 đều dừng trong giới hạn đã đặt
- [ ] Hành vi của L4 khác L3 một cách quan sát được

Ghi chú tuần 9

- ........

---

# Mốc kiểm tra chung

## Cuối tuần 8

- [ ] Hạ tầng benchmark hoạt động, L1, L2 và L3 chạy được, đặc tả L4 đã được duyệt
- [ ] Phần A0, A1 và A3 (sandbox, cost control) đã có bản dùng được
- [ ] Họp ngắn chốt giao diện giữa Engine, harness và các level

## Cuối tuần 9

- [ ] Bốn level chạy ổn định, kết quả pilot đã được đánh giá
- [ ] Phần A5 đã đạt toàn bộ
- [ ] Cấu hình LLM, prompt, registry và level đã đóng băng
- [ ] Có tài liệu tóm tắt pilot, vấn đề đã gặp và cách xử lý

---

# Nhật ký vấn đề và quyết định

Ghi mỗi dòng theo dạng ngày, vấn đề hoặc quyết định, người xử lý và trạng thái.

- ........
- ........
- ........

- 2026-10-16 | Tài liệu: thêm cây thư mục và chú thích theo mẫu vào README, bổ sung .agents/rules và checklist; giải thích vai trò architecture.md | Agent | Hoàn tất phần cập nhật README; đã kiểm tra 7 liên kết Markdown đều tồn tại, tài liệu kiến trúc chi tiết vẫn còn TODO.

- 2026-10-16 | Tài liệu harness: ghi mô tả 4 layer vào docs/architecture.md, ánh xạ module và luồng xử lý, cập nhật mô tả trong README | Agent | Hoàn tất tài liệu 4 layer; kiểm tra đủ 4 mục Layer và liên kết checklist tồn tại; chưa đánh dấu hoàn tất phần code.

- 2026-10-16 | Điều chỉnh .agents/rules/progress.md theo Phần A/B, phân công thành viên và lịch tuần 8–9; dùng [~] cho việc dở dang, bỏ quy tắc ID D01–D14 và tổng tiến độ 42 mục | Agent | Đã cập nhật rule; đối chiếu với cách dùng checklist, không thay đổi checkbox công việc.
