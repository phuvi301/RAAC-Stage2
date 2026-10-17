# Implementation Checklist - Tuần 8-9

Checklist theo dõi tiến độ triển khai framework RAAC, gồm hai phần.

- Phần A là công việc làm chung, tức bộ khung harness 4 layer bọc quanh mỗi agent.
- Phần B là công việc riêng của từng thành viên, gồm registry, các level L1-L4, benchmark và evaluator.

Cách dùng file này

- Đánh dấu `[x]` khi việc đã xong và đã được người khác kiểm tra hoặc có test chứng minh.
- Dùng `[~]` cho việc đang làm dở và ghi ngắn lý do hoặc vướng mắc ngay cạnh dòng đó.
- Chữ trong ngoặc cuối dòng là người phụ trách, Vũ, Vinh hoặc Tuấn, đổi lại nếu hoán đổi việc.
- Ghi ngày cập nhật cuối mỗi khi sửa file, và ghi vấn đề phát sinh vào mục nhật ký cuối file.

Cập nhật gần nhất ngày 2026-10-17

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
- [ ] Test xác nhận task/run mới không kế thừa memory hoặc checkpoint của run trước; resume chỉ khôi phục đúng run đang tiếp tục (Vũ)
- [ ] Bổ sung phần injection cho thông tin topology và vòng hiện tại phục vụ L3, L4 (Tuấn)
- [ ] Unit test kiểm tra prompt sinh ra đúng và đủ các thành phần (Vũ)

## A3 - Layer 3, Thực thi và Bảo mật

Thư mục `src/raac/harness/layer3_execution/`.

### Tool orchestration

- [ ] `tool_orchestrator.py` chạy tool theo yêu cầu của LLM, hỗ trợ chạy song song có giới hạn (Vinh)
- [ ] Mỗi tool call có timeout và retry, lỗi được trả về agent dưới dạng quan sát (Vinh)
- [ ] `tools/` gồm tối thiểu `read_file`, `edit_file`, `run_tests`, `search_repo` (Vinh)

### Sandbox

- [x] `docker_sandbox.py` checkout repo ở base commit, áp patch, chạy pytest có timeout, thu kết quả và dọn container (Vinh)
- [x] `sandbox/policy.py` tắt network, giới hạn CPU, RAM và thời gian chạy (Vinh)
- [x] `docker/Dockerfile.sandbox` dựng image chạy code của agent (Vinh)
- [x] Kiểm thử sandbox bằng một patch cố tình độc hại và xác nhận không ảnh hưởng máy chủ (Vinh)
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
- [ ] `mcp_client.py` có interface; chỉ thêm adapter MCP tối thiểu nếu thật sự cần tool ngoài, không triển khai đầy đủ trong phạm vi hai tuần (Vinh)
- [ ] `a2a_connector.py` chỉ là stub có interface và docstring (Vinh)

## A5 - Tiêu chí hoàn thành harness (cả nhóm kiểm tra)

- [ ] Một agent mẫu chạy hết vòng Reason, Act, Observe với ít nhất một tool và một lần chạy trong sandbox
- [ ] Mọi lần gọi LLM đều đi qua `llm/client.py` và có log token
- [ ] Resume từ checkpoint cho kết quả giống như chạy liền mạch
- [ ] Cost guard dừng được run vượt trần và ghi rõ lý do dừng
- [ ] Unit test của từng layer chạy được bằng `mock_llm` mà không tốn token
- [ ] Phụ thuộc chỉ đi một chiều từ layer 1 xuống layer 4, không có import ngược

Ghi chú tiến độ phần A

- 2026-10-17 | Agent triển khai sandbox độc lập: mirror cache có khóa, archive commit LF, Dockerfile theo profile, baseline/candidate riêng volume, timeout và cleanup. Kiểm chứng `RAAC_DOCKER_TESTS=1 python -m pytest -q`: 21 passed, gồm 4 integration Docker thật trên Linux Engine 29.1.2; patch thử ghi file host không đổi sentinel. Các agent/layer khác chưa được nghiệm thu.
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
- [ ] Tách state vòng đời từng agent ở Layer 1 khỏi state điều phối LangGraph; test checkpoint/resume liên kết đúng run, agent và vị trí điều phối
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

- [~] `benchmark/dataset.py` đọc task gồm nội dung issue, repo, base commit, file test — đã đọc schema Stage1; dataset hiện không có file test, dùng lệnh suite trong profile, chưa có regression test theo issue.
- [x] Chuẩn hóa task về một schema duy nhất
- [x] Dựng pipeline Docker, mỗi run checkout repo, áp patch, chạy pytest có timeout, thu kết quả rồi dọn container
- [x] Xác nhận mỗi run bắt đầu từ môi trường sạch

### Harness chạy hàng loạt (`benchmark/runner.py`)

- [ ] Lặp qua các cặp (task, level, lần lặp)
- [ ] Chạy song song có giới hạn
- [ ] Hỗ trợ resume, bỏ qua run đã xong khi chạy lại
- [ ] Có retry và timeout cho từng run
- [ ] Có budget guard dừng run khi vượt trần token

### Bản ghi kết quả (`benchmark/run_record.py`)

- [ ] Định nghĩa schema gồm trạng thái, patch, kết quả test, token, số lần gọi, latency, số message, số vòng, agent được kích hoạt, lỗi
- [ ] Ghi commit hash, version registry, version prompt và cấu hình model vào mỗi bản ghi
- [ ] Mỗi bản ghi lưu version từ `registry/VERSION`, version/hash từng file config đã dùng và snapshot cấu hình hiệu lực không chứa secret; kiểm tra không trộn cấu hình khi resume

### Cấu hình và kiểm tra

- [ ] Gom cấu hình LLM (model, temperature, seed nếu có, prompt version) vào `configs/llm.yaml` có version
- [ ] Viết `dummy level` chỉ gọi LLM một lần để kiểm tra harness trước khi L1 xong
- [~] Viết `cli.py` với lệnh chạy một (task, level) — đã có sandbox check/prepare/run theo task; chưa tích hợp level hoặc LLM.

Tiêu chí thành công tuần 8

- [ ] Một lệnh duy nhất chạy được một (task, level) và sinh ra một bản ghi kết quả đầy đủ
- [ ] Chạy lại cùng một task trong cùng cấu hình thì môi trường và log nhất quán
- [ ] Cấu hình LLM nằm trong một file config duy nhất và có version

Ghi chú tuần 8

- 2026-10-17 | Agent: loader Stage1 (120 issue/12 repo), profile Requests và CLI sandbox đã có; 17 unit/Git tests và 4 Docker integration tests passed. Pilot psf__requests-6027 đã build image và chạy suite, lưu results/; xem docs/pilot_log.md. Thiếu test mục tiêu theo issue, chưa có batch/resume hoặc tích hợp level.
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

- 2026-10-16 | Chốt lưu ý thiết kế benchmark: cách ly memory, giới hạn connectors, tách state agent/collaboration và version hóa registry/config; đồng bộ architecture, checklist và rule | Agent | Đã ghi nhận thiết kế và bổ sung tiêu chí kiểm tra; các mục hiện thực vẫn chưa hoàn tất.

- 2026-10-17 | Hiện thực pipeline sandbox Docker độc lập theo yêu cầu người dùng; cache data/repos, volume riêng từng phase, profile theo repo/task, provenance và CLI. 21 test passed gồm Docker thật; không đánh dấu runner/LLM/A5. Dataset thiếu file test nên loader giữ [~]. Sửa export LF trên Windows, profile Debian Bookworm và network none để giữ localhost. | Agent hỗ trợ hạ tầng của Vinh | Hoàn tất phạm vi pipeline; theo dõi baseline Requests và giới hạn dataset tại docs/pilot_log.md.

- 2026-10-17 | Đưa bản sao dataset Stage1 vào data/tasks/cleaned_benchmark.json, chuyển cấu hình mặc định sang đường dẫn tương đối và cho phép Git theo dõi riêng file dataset; giữ nguyên nguồn Stage1, cache/results vẫn gitignore. | Agent | Đã so sánh SHA-256 nguồn/bản sao trùng nhau và nạp thành công 120 task thuộc 12 repo; không đổi trạng thái loader còn thiếu file test theo issue.

- 2026-10-17 | Mở rộng sandbox baseline: 12 profile ánh xạ đủ 120 task; kiểm tra manifest đúng commit, thêm batch tuần tự, checkpoint atomic, resume có fingerprint, retry lỗi hạ tầng và JSON/CSV. 32 tests passed gồm 6 Docker integration (hủy build, batch ngắt/resume và cách ly); smoke 12 repo đang chạy, chưa nghiệm thu 120/120. Không thay runner L1–L4 hoặc đánh dấu A5/evaluator. | Agent hỗ trợ Vinh | Đang kiểm chứng Docker thực tế; xem results/batches và docs/sandbox_profiles.md.

- 2026-10-17 | Kiểm chứng mở rộng sandbox: 41 test framework passed gồm 7 Docker integration (thêm rebuild extension native sau patch); 35 unit passed sau kiểm tra tổng deadline source + build. Profile check covered=120/120, errors=0. Chẩn đoán đại diện đã phát hiện/sửa thiếu pybind11, Git metadata Versioneer, Matplotlib lịch sử và Werkzeug; các profile đổi cần kiểm chứng lại bằng batch chuẩn. | Agent hỗ trợ Vinh | Đang chạy Docker thực tế, chưa nghiệm thu 120 môi trường; không đổi checkbox benchmark theo level, evaluator hoặc A5.

- 2026-10-17 | Kiểm tra cuối sau bổ sung tổng deadline: `RAAC_DOCKER_TESTS=1 python -m pytest -q --tb=short` đạt **42 passed** trong 121,44 giây (35 unit + 7 integration Docker thật); Ruff, format và `git diff --check` qua.

- 2026-10-17 | Khởi chạy batch chuẩn baseline-all-v1 đủ 120 task, tuần tự, ngân sách 1800/1800 giây; manifest, summary JSON/CSV và CSV theo repo đã tạo, fingerprint đóng băng. | Agent hỗ trợ Vinh | Đang chạy, chưa nghiệm thu hỗ trợ toàn dataset; theo dõi results/batches/baseline-all-v1 và docs/pilot_log.md. Không đánh dấu hoàn tất A5/evaluator/benchmark L1–L4.

- 2026-10-17 | Tiếp tục baseline-all-v1: 8/120 task Requests đã chạy suite (test_failed, không collection/timeout). Requests-1051 có lỗi tương thích HTTPConnection strict trên Python 3.9; chuẩn bị configs/repos/requests-legacy.yaml dùng Python 2.7, chưa nghiệm thu profile này. Tiến trình cũ mất khi build Flask (BuildKit context canceled); lưu interrupted record và dọn builder tồn dư, resume giữ 8 kết quả cũ. Flask dựng lại thành công, đang chạy tiếp. | Agent hỗ trợ Vinh | Chưa nghiệm thu toàn dataset.

- 2026-10-17 | Resume baseline-all-v1: Flask 5010 pass 481 test, 3 skip; tổng 9/120 suite đã chạy (8 test_failed Requests, 1 passed), Seaborn đang chạy. Đã khởi động driver nền PID 16080 để chờ khóa/resume batch nếu bị ngắt và chạy validation requests-1051-legacy-v1 sau batch chính. Profile legacy Python 2.7 và config riêng chưa được kiểm chứng Docker; không thay fingerprint batch đang chạy. Kết quả driver ở results/baseline-driver.json và *.log. | Agent hỗ trợ Vinh | Đang chạy, chưa nghiệm thu 120/120.

- 2026-10-17 | Heartbeat: batch baseline-all-v1 đã chạy suite 26/120 task, 5 passed và 21 test_failed; không collection/preparation/timeout ở các task đã hoàn tất. Pytest có subprocess ModuleNotFoundError pytest, setuptools deprecation và attrs không tương thích; chuẩn bị profile pytest-compatible cùng config compatibility riêng, chưa kiểm chứng Docker. Không đổi fingerprint batch đang chạy, chưa push. | Agent hỗ trợ Vinh | Đang chạy, cần kiểm chứng lại môi trường pytest và Requests-1051.

- 2026-10-17 | Pylint-4901 phát hiện collection_error do thiếu package py._path. Chuẩn bị pylint-compatible.yaml thêm py==1.11.0 và config compatibility; các profile sửa cần kiểm chứng bằng batch riêng sau batch chính/legacy. Tổng hiện tại 26 suite đã chạy, 1 collection_error, 93 chưa chạy. | Agent hỗ trợ Vinh | Đang xử lý, chưa nghiệm thu/push.

- 2026-10-17 | Heartbeat 18:22: baseline-all-v1 đã chạy suite 40/120 (5 passed, 35 test_failed), 6 Pylint collection_error thiếu py, 1 Sphinx preparation_error thiếu flit_core, 73 chưa chạy. Đối chiếu pyproject.toml Sphinx-10678 yêu cầu flit_core>=3.7; thêm flit_core==3.9.0 vào sphinx-compatible.yaml/config compatibility. Queue kiểm chứng sau batch chính và legacy gồm pytest/Pylint/Sphinx; chưa coi profile sửa là đã nghiệm thu. | Agent hỗ trợ Vinh | Đang chạy, chưa push.

- 2026-10-17 | Người dùng yêu cầu push ngay bản triển khai hiện có. Snapshot trước push: baseline-all-v1 đang chạy, 41 suite đã chạy (5 passed, 36 test_failed), 6 collection_error Pylint, 1 preparation_error Sphinx, 72 chưa chạy. Profile compatibility/legacy còn chờ Docker kiểm chứng; chưa nghiệm thu hỗ trợ 120/120. Chỉ commit source/config/test/tài liệu và dataset; cache/results/log không đưa lên Git. | Agent hỗ trợ Vinh | Push bản triển khai đang kiểm chứng theo yêu cầu; tiếp tục theo dõi batch.

- 2026-10-17 | Chuyển sang issue tests theo kế hoạch mới: dừng batch suite/driver và giữ lịch sử, nhập 120/120 evaluator từ raw Verified bằng repo+base_commit+PR URL, tách gold/test patch khỏi Task. Thêm run/batch issue mặc định, validate baseline+gold, selector manifest, trạng thái từng test, bảo vệ file test và fingerprint evaluator. 49 unit passed; 8 Docker integration passed trước bổ sung hai fixture native issue/unittest. Requests-6027 validation thật đạt 2 F2P fail +185 P2P pass ở baseline, 187/187 pass sau gold; Flask/Seaborn pilot cũng validated. Các repo còn lại đang kiểm chứng, chưa nghiệm thu 120/120 hoặc evaluator L1–L4/A5. | Agent hỗ trợ Vinh | Đang triển khai và kiểm chứng; chưa push lượt mới.

- 2026-10-17 | Issue evaluation: nhập120 mappings, default issue mode, baseline/gold/candidate riêng, per-test adapter và bảo vệ test.49 unit passed;58 tests passed trong full suite,1 build-timeout integration gặp lock do pilot build đồng thời, đang chạy lại tách biệt.5 repo pilot validated (Requests/Flask/Seaborn/Sphinx/pytest), Pylint1548 baseline collection_error do test import API mới dù gold pass; giữ chưa đạt. | Agent hỗ trợ Vinh | Chưa nghiệm thu120/120, không đánh dấu A5/evaluator tổng hợp.

- 2026-10-17 | Kiểm chứng issue runner: 49 unit passed; Docker integration có 9 test pass trong lượt chung và test build timeout chạy lại tách biệt pass (tổng 10 integration). Lỗi trước do pilot giữ cache lock đã được phân lập. Pilot Requests/Flask/Seaborn/Sphinx/pytest validated; Pylint1548 gold pass nhưng baseline collection_error nên chưa đạt. Tiếp tục pilot các repo còn lại bằng driver nền; suite cũ không resume. | Agent hỗ trợ Vinh | Chưa nghiệm thu 120/120; xem issue-pilot manifest.

- 2026-10-17 | Pilot issue tests: Sympy và Django-v2 validated, tổng8 repo đạt; Django126 selector được ghi bằng runner gốc và docstring alias. Pylint baseline collection còn bị chặn;3repo native đang kiểm chứng. Driver đã xếp issue-all-v1 đủ120 sau pilot; chưa coi xếp hàng là đã chạy. | Agent hỗ trợ Vinh | Đang kiểm chứng, chưa nghiệm thu120/120.

- 2026-10-17 | Kiểm tra adapter Django sau sửa docstring alias: Docker native unittest fixture +14 unit issue evaluation đều pass (15 tests); toàn bộ49 unit cũng pass. Source/profile mặc định đã chuyển issue; suite cần chọn rõ. Triển khai được đưa lên Git theo ủy quyền, nhưng8 repo pilot mới validated và3repo native còn chạy; Pylint1548 chưa đạt. Giữ yêu cầu validation120 thực tế ở trạng thái đang thực hiện. | Agent hỗ trợ Vinh | Chưa nghiệm thu120/120.
