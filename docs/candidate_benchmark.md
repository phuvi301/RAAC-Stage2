# Chạy MAS tạo candidate và chấm độc lập

Đường chạy này hiện hỗ trợ **L1 Fixed**, **L2 Adaptive Routing** và **L3 Chain**,
tuần tự, dùng chung setup Docker, export candidate, evaluator và RunRecord; chỉ
phần agent khác nhau (`--level L1` chạy `L1Fixed`, `--level L2` chạy `L2Routing`
qua Shared Engine, `--level L3` chạy Chain). L1 và L2 không có role Environment;
Tester chọn lệnh test phù hợp repository. Host điều phối và gọi LLM;
file tools, shell và test của agent chạy trong container riêng, không mount repo host,
không có Docker socket, API key, gold patch hoặc test đánh giá theo issue.
Image chuẩn bị từ profile phải chứa sẵn dependency: container agent không có network.
Environment chỉ chọn và xác minh runtime trong image.

```powershell
python -m raac.cli benchmark run --task-id psf__requests-6027 --level L3
python -m raac.cli benchmark run --task-id psf__requests-6027 --level L2
python -m raac.cli benchmark run --task-id psf__requests-6027 --level L1
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --level L2 --batch-id l2-pilot
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --level L1 --batch-id l1-pilot
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --repetitions 2 --batch-id candidate-pilot
python -m raac.cli benchmark batch --task-id psf__requests-6027 --task-id psf__requests-1051 --repetitions 2 --batch-id candidate-pilot --resume
```

Chọn task ID tồn tại trong dataset của `--config` (mặc định
`configs/benchmark.yaml`, tập 105 task). Cấu hình model/endpoint ở
`configs/llm.yaml` và biến môi trường host; không chuyển credential vào container.

`benchmark run` và `benchmark batch` tự mở web observe trong cùng lệnh, ngay khi log
setup đã sẵn sàng. Có thể chọn `--observe web|terminal|none` (mặc định `web`):

```powershell
python -m raac.cli benchmark run --task-id astropy__astropy-12906
python -m raac.cli benchmark run --task-id astropy__astropy-12906 --observe terminal
python -m raac.cli benchmark run --task-id astropy__astropy-12906 --observe none
```

Web bind loopback và tự chọn port trống; URL/log được in ra stderr. Batch dùng một
URL và một lần mở browser, tự theo run mới nhất; bộ chọn run cho phép xem lại run
đã đăng ký, gồm attempt đã hoàn tất được bỏ qua khi resume. Kết quả JSON vẫn ở stdout.
Web giữ mở sau evaluator/cleanup; Ctrl+C lúc này chỉ đóng viewer và trả mã kết quả
benchmark. Ctrl+C trong lúc chạy hủy run, cleanup và trả 130. Terminal thoát sau phase
cuối. Dùng `--observe none` trong CI/script cần tự thoát. Viewer lỗi không hủy benchmark;
nếu browser không tự mở, mở URL đã in. `show-log` vẫn dùng để mở lại run cũ.

Lifecycle requests one output-schema repair per activation within the existing call/token/deadline guards. Coder receives bounded progress and revision evidence and hands off after adequate smoke checks.

Từ Registry `0.19.3` (Analyst `chain-v7`), client phân biệt `empty_completion`
(không có content/tool-call), `truncated_completion` (`finish_reason=length`) và
`invalid_response` (envelope không hợp lệ). Log lỗi giữ `finish_reason` và usage
API thực tế, kể cả cache; không tự đánh mất usage chỉ vì nội dung sai. Nội dung
reasoning riêng của provider không được log hoặc chuyển sang lifecycle.
Response rỗng/bị cắt dùng chung quota sửa output một lần, chỉ khi usage đầy đủ,
còn lượt/token/deadline. Tool-call trong response bị cắt không được thực thi;
JSON trông hợp lệ nhưng finish_reason=length cũng không được nhận làm handoff.
Analyst bắt đầu bàn giao không có tools ở lượt áp chót (mặc định lượt 5), giữ lượt
6 để sửa nếu cần. Nếu vẫn lỗi, run dừng với mã rõ; không có lượt thứ 7. Giữ nguyên
model, max_tokens=4096, token budget và số call tối đa. Chạy lệnh benchmark mới để
nạp bản sửa; viewer đang mở của run đã kết thúc chỉ đọc log cũ.
Cả sáu role hiện dùng `ref: default`: `LLM_MODEL` trong `.env` ghi đè model
cấp gốc của YAML; nếu bỏ trống thì giữ model YAML.
Mỗi attempt có run ID, container và memory mới. `agent_run_timeout_seconds` là
thời gian tối đa toàn run, gồm setup, agent và evaluator; token guard dùng harness hiện có.

Host lưu snapshot baseline từ container và đối chiếu source base commit trước khi
agent chạy. Sau đó host tính candidate từ thay đổi thực tế của Coder, kiểm tra
attribution và xuất `results/benchmark-<id>/candidate.patch`. Hợp đồng benchmark
`implementation-only-v1` được người dùng duyệt ngày 2026-10-22: Coder chỉ sửa
implementation; Tester chỉ tạo/sửa test phát triển mới, không sửa test baseline.
Test riêng của Tester không đi vào candidate. File tools, kiểm tra sau shell và
export kiểm tra quyền này; local demo giữ hành vi cũ. Provenance ghi candidate_contract.
Binary, đổi executable bit và một số thay đổi không biểu diễn
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
Ví dụ theo dõi run đang chạy (thay RUN_ID bằng benchmark-...):

```powershell
python -m raac.mas_cli show-log --run-id RUN_ID --follow --details
python -m raac.mas_cli show-log --run-id RUN_ID --web
```

CLI nhận cả ID `chain-*` và `benchmark-*`.
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

Pilot bổ sung theo yêu cầu hoàn tất plan: `psf__requests-6027`, run
`benchmark-76a23ecfcd7a4469b0b0a833ddc1f5e6`, cùng model/cấu hình.
MAS REJECT sau 3 vòng (`max_rounds_reached`); candidate sửa `requests/utils.py`
và protected `tests/test_utils.py` nên evaluator trả `protected_test_error`.
192793 token (171283 input, 21510 output), 43 call, 631,328 giây; container đã dọn.
Report hai pilot ở `results/mas-benchmark-plan/final-pilot-resource-report.json`.
Mốc pilot MAS có candidate được chấm F2P/P2P vẫn chưa đạt; không lọc test khỏi patch
hoặc tự retry để chọn kết quả tốt hơn.

Sau kết quả trên, người dùng duyệt đổi hợp đồng cho các run tương lai sang
`implementation-only-v1`. Pilot mới `benchmark-ce36a0a672404019ae26e24b50fa0727`
(Astropy cùng task/model) đạt MAS PASS và evaluator **resolved**: 2/2 F2P và
13/13 P2P pass, baseline hợp lệ, candidate chỉ sửa `astropy/modeling/separable.py`.
Run dùng 94901 token (87823 input, 7078 output), 19 call, 224,422 giây, 1 vòng;
evaluator riêng `1ba56322af1e499f8c4f4d1cda8f5f13` thực thi đủ 15 selector.
Container đã dọn; report riêng ở `results/mas-benchmark-plan/implementation-only-resource-report.json`.
Các pilot trước giữ nguyên và không gộp thành so sánh cùng hợp đồng với run mới.
Đã kiểm chứng phạm vi plan L3; chưa nghiệm thu A5/bốn level hoặc full dataset.

## Shared Tester fixes and output-budget trial (2026-10-27)

Registry 0.19.4 / Tester chain-v6 applies to future L1/L2/L3 runs across the
105-issue selection. Tool cwd values are repository-relative (`.` for root).
Invalid tool paths are rejected with field-level guidance. A Docker command
raising ValueError leaves attribution usable only after an unchanged content
and mode snapshot is verified; earlier integrity violations remain blocking.

Tester must select the repository's actual command with `run_tests(command=...)`.
Neither Workspace nor L1/L2 silently guesses unittest/pytest. Invalid directory,
report path, argv or environment selections preserve the previous suite.
The isolated unittest helper restores installed external user-site dependencies
for a base interpreter; virtualenv isolation and trusted bootstrap imports stay
in place. Real failed assertions still reach Coder. A passing suite with missing
AC coverage returns feedback to Tester within the same activation, under the
existing call/deadline/token limits; exhaustion reports `incomplete_test_coverage`
or `missing_test_command`. Each new final suite execution gets its own durable
receipt; resuming that execution reuses its receipt.

The default output budget remains 4096. The separate trial config changes only
the output cap to 8192, preserving environment model overrides and recording the
selected LLM file in provenance and batch fingerprint:

```powershell
.venv\Scripts\python.exe -m raac.cli benchmark run --task-id astropy__astropy-12906 --level L3 --config configs/experiments/benchmark-output-8192.yaml
```

This command starts the benchmark and its web observer together (default
`--observe web`). Use a new run/batch identity for the changed configuration.
Compare truncation count, total calls/tokens, latency and independent evaluation
against the 4096 control before changing defaults. No live 8192 trial or full
105-issue run has been performed as part of these fixes; no speedup or resolution
rate is claimed. Existing runs and their integrity errors are preserved.

## Managed pytest reports and error diagnostics (2026-10-28)

For recognized pytest argv, run_tests normalizes report_path and both
--junitxml/--junit-xml forms before selection and validation. Local and Docker
helpers always use a harness-selected temporary JUnit file outside the repo.
Existing report/source files are preserved; no XML files are broadly exempted
from integrity checks. Custom JUnit runners retain their relative report_path
contract. A verified unauthorized mutation returns WorkspaceIntegrityError with
the relative offending path; export remains blocked after a genuine violation.

The web viewer distinguishes transport, truncated/empty output, validation,
test failure and integrity violations. Paired llm_call/llm_response errors merge
into one card with response details, keyed by run/call/attempt; raw JSONL stays
unchanged. Standalone/pasted response errors remain visible. The terminal view
suppresses the second error heading and can show its content with --details.
