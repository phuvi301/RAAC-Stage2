# Chạy MAS tạo candidate và chấm độc lập

Đường chạy này hiện hỗ trợ **L3 Chain**, tuần tự. Host điều phối và gọi LLM;
file tools, shell và test của agent chạy trong container riêng, không mount repo host,
không có Docker socket, API key, gold patch hoặc test đánh giá theo issue.
Image chuẩn bị từ profile phải chứa sẵn dependency: container agent không có network.
Environment chỉ chọn và xác minh runtime trong image.

```powershell
python -m raac.cli benchmark run --task-id psf__requests-6027 --level L3
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --repetitions 2 --batch-id candidate-pilot
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --repetitions 2 --batch-id candidate-pilot --resume
```

Chọn task ID tồn tại trong dataset của `--config` (mặc định
`configs/benchmark.yaml`, tập 105 task). Cấu hình model/endpoint ở
`configs/llm.yaml` và biến môi trường host; không chuyển credential vào container.
Mỗi attempt có run ID, container và memory mới. `agent_run_timeout_seconds` là
thời gian tối đa toàn run, gồm setup, agent và evaluator; token guard dùng harness hiện có.

Host lưu snapshot baseline từ container và đối chiếu source base commit trước khi
agent chạy. Sau đó host tính candidate từ thay đổi thực tế của Coder, kiểm tra
attribution và xuất `results/benchmark-<id>/candidate.patch`. Test riêng của Tester
không đi vào candidate. Binary, đổi executable bit và một số thay đổi không biểu diễn
được sẽ báo lỗi rõ. Container evaluator mới áp candidate lên base commit rồi mới
nạp test đánh giá F2P/P2P; verdict của Reviewer được giữ riêng với kết quả evaluator.

Candidate rỗng vẫn được chấm như source không đổi; không gửi patch rỗng vào `git apply`.
Lỗi agent được giữ trong record ngay cả khi evaluator trả resolved/unresolved.
Candidate sửa file test được evaluator bảo vệ vẫn bị từ chối với `protected_test_error`;
harness không tự bỏ phần test khỏi patch và không retry lỗi này.

Snapshot bỏ cache/build và artifact biên dịch `.so`, `.pyd`, `.o`, `.a` do cài dependency
tạo ra. File nguồn tại base commit vẫn phải khớp baseline; không cho profile sửa source.

Mỗi thư mục run có `record.json`, `agent-result.json`, `llm.jsonl` và memory.
Record chứa usage, thời gian setup/agent/evaluation/total, identity, lỗi,
registry/prompt/config snapshot và hash source/lock. Usage thiếu được ghi unknown;
cache token thuộc input, không cộng thêm vào total. `trace_content=false` giữ
trạng thái và usage nhưng bỏ nội dung command/output.
Dùng terminal/web trace viewer hiện có với `llm.jsonl` của run để theo dõi
command output, file thay đổi và candidate đã xuất; xem lệnh viewer trong
[hướng dẫn Chain](chain_demo.md).
Terminal `show-log --follow` chờ phase cuối của benchmark, gồm evaluator và cleanup;
event MAS `run_completed` chưa kết thúc toàn benchmark.

Manifest batch nằm ở `results/benchmark-batches/<batch-id>/manifest.json`.
Resume bỏ attempt đã kết thúc, phục hồi run bị ngắt rồi chạy lại từ môi trường sạch;
không tiếp tục giữa node LangGraph. Cấu hình/source/model đổi thì phải tạo batch mới.
`--resume --retry-errors` chỉ retry lỗi hạ tầng được phân loại retryable;
không retry unresolved, verdict rejected hoặc lỗi patch/Environment/attribution của agent.
Mọi attempt vẫn được giữ để tính tài nguyên.

```powershell
python -m raac.benchmark.analysis.pilot_report results/benchmark-<id>/record.json
```

Report nhận danh sách record rõ ràng, tính mean/stddev theo level, gộp tài nguyên
các attempt của cùng task/repetition. Mẫu thiếu usage không được biến thành zero;
report ghi số mẫu thiếu và token đã quan sát. Chưa có L1/L2/L4, parallel batch,
LLM judge, utility hoặc quy đổi tiền. `make pilot` vẫn là sandbox baseline/gold
validation; dùng `benchmark run/batch` để chạy MAS candidate.

Bằng chứng fixture gồm unit test, HTTP mock và Docker thật (Chain xuất candidate,
container evaluator khác chấm F2P/P2P).

Pilot model thật ngày 2026-10-22: task `astropy__astropy-12906`, model
`cc/claude-opus-5-5` qua 9router, run `benchmark-b9feebf7d8894d9b821cfcb6460f3633`.
MAS trả PASS sau một vòng, nhưng candidate sửa cả `astropy/modeling/tests/test_separable.py`
thuộc test được bảo vệ nên evaluator trả `protected_test_error` trước khi chạy test.
Run dùng 91888 token (83606 input, 8282 output), 17 call, 145,281 giây;
usage token đầy đủ, cache usage chưa biết. Record/trace/patch giữ nguyên trong thư mục run,
resource report ở `results/mas-benchmark-plan/live-pilot-resource-report.json`.
Không có kết quả resolved/unresolved độc lập từ pilot này; chưa nghiệm thu bốn level/A5.

Kiểm chứng tiếp theo ngày 2026-10-22 (không gọi LLM, không thay log pilot):
evaluator thật của cùng task chấm candidate rỗng, run `d2ad4a1d92224016bcaf1d9fd9862721`,
kết quả `unresolved`. Baseline hợp lệ, 2/2 F2P vẫn fail và 13/13 P2P pass;
evidence ở `results/mas-benchmark-plan/empty-candidate-astropy-evaluation.json`.
Đây là kiểm tra evaluator trên task thật, không phải kết quả chấm lại candidate của pilot MAS.
