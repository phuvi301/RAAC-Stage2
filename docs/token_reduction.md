# Thử nghiệm giảm biểu diễn context

Mặc định trong `configs/harness.yaml` và `configs/benchmark.yaml` giữ nguyên. Không đổi model, topology, budget, retry hoặc evaluator; không thêm dependency hay gọi API thật.

Chọn một benchmark YAML trong `configs/experiments/token_reduction/`: `benchmark-baseline.yaml`, `benchmark-compact.yaml`, `benchmark-filtered.yaml`, `benchmark-concise.yaml`. Dùng với CLI run/batch hiện có cho L1/L2/L3. Trường tùy chọn `harness_config` nhận đường dẫn tính từ framework root, hoặc đường dẫn tuyệt đối; bỏ trống dùng `configs/harness.yaml`. Harness được kiểm tra trước Docker/model. Provenance ghi file thật và effective config; fingerprint có cả harness ngoài repo, đổi chính sách sẽ chặn batch/lifecycle resume.

| Chính sách | Compaction hiện có | Lọc stdout pytest | Hướng dẫn ngắn |
|---|---|---|---|
| baseline | Tắt | Tắt | Tắt |
| compact | Bật, giữ ngưỡng hiện tại | Tắt | Tắt |
| filtered | Bật | Bật | Tắt |
| concise | Bật | Bật | `concise-v1` |

Layer 1 giữ task và observations gốc; Layer 2 chỉ chuẩn bị bản gửi model. Bộ lọc nhận argv `pytest` hoặc `python[version] -m pytest`, chỉ với status completed, exit code 0 và stderr rỗng. Nó bỏ ANSI CSI, gom dòng trống/progress giống nhau liên tiếp, ghi số lần lặp và chỉ nhận bản ngắn hơn. Lệnh lạ, lỗi, traceback, diff và các dấu hiệu source code được giữ nguyên; không đoán nội dung quan trọng. Compaction cũ vẫn chạy độc lập sau bước lọc. Hướng dẫn concise chỉ thêm vào system message của bản gửi model, giữ Registry và hợp đồng output.

`run_command` và lệnh con trong `run_commands` có `observation_ref`. Dùng `read_tool_result` với `path: ["stdout"]` hoặc `["stderr"]`, `offset`, `limit` để đọc bản gốc; không chạy lại lệnh. Reference chỉ hợp lệ trong run và quyền tool hiện tại, được lưu trong runtime checkpoint. Parser/evaluator không nhận output đã lọc.

Mỗi call có event `context_metrics`, kể cả khi `trace_content=false`, cùng run/agent/turn và `llm_call_id` của provider call. Event chỉ chứa số liệu và chính sách:

- Trước/sau: system, task/handoff, assistant history, tool results, context tổng hợp, tool schemas và tổng request; số ký tự, byte UTF-8 và token ước lượng.
- Estimator `json-chars-div4-v1` lấy ceil(ký tự JSON / 4). Đây là ước lượng biểu diễn, không phải tokenizer của model. Tổng chỉ gồm messages/tools, không gồm model settings hay toàn bộ payload HTTP. Mỗi nhóm có overhead JSON riêng nên không cộng nhóm để thay tổng request.
- `preparation_ms` gồm chuẩn bị context/schema và tính hai bộ metric; không gồm checkpoint, HTTP hoặc bước đếm lặp ở client.
- Byte lặp là message/schema giống hoàn toàn đã có trong request trước của cùng run/client session. Không phân bổ provider cache cho từng nhóm, không coi phần lặp là lãng phí. Sau resume bằng client mới, cửa sổ đếm lặp trực tiếp bắt đầu lại; replay trace đếm xuyên các call đã ghi.

Usage input/output/cache và các cờ thiếu usage/cache vẫn do provider/UsageTracker hiện có quản lý; không suy ra tiền tiết kiệm từ số ký tự hay token ước lượng.

Replay offline, tạo file mới và từ chối ghi đè:

```powershell
.venv/Scripts/python.exe scripts/analyze_context.py --latest 3 --output results/token-reduction/new-report.json
# Hoặc truyền các đường dẫn trace cụ thể trước --output.
```

`--latest` chọn các run trực tiếp dưới `results/` có request đầy đủ, tránh fixture/profiler lồng bên dưới. Trace cũ thiếu request báo unavailable. Replay bắt đầu từ request đã lưu, không tái tạo được context gốc trước compaction; không replay tool, không gọi model. Báo cáo giữ usage đã ghi, số lần đọc chi tiết thực tế, hash trace nguồn, kích thước/token ước lượng, thời gian và byte lặp cho từng chính sách. Số lần đọc chi tiết của các chính sách giả định chưa có dữ liệu vì không chạy lại agent.

Kiểm chứng ngày 2026-10-27: 129 test +10 subtest hồi quy tập trung qua (24,03s); 4 Docker offline qua (21,59s). HTTP mock qua cả bốn chính sách, metric khớp messages/tools của wire request và cache thiếu vẫn thiếu; Docker offline dùng image có sẵn `404ac6a56760`, network disabled, không build/pull. Cùng fixture pytest chạy thành công, raw stdout đọc theo trang và structured report vẫn 1 test/0 fail. Test kiểm tra immutability, idempotence, ANSI, Unicode, batch, cross-run, checkpoint reference, chọn harness/provenance và batch/lifecycle resume khi đổi chính sách. Ruff các file mới và diff check qua.

Replay ba run gần nhất có request đầy đủ: [báo cáo JSON](../results/token-reduction/2026-10-27-final-replay.json). Không sửa trace nguồn; kiểm tra SHA-256 sau replay.

| Run (đuôi ID) | Calls | Baseline token ước lượng | Compact / filtered | Concise |
|---|---:|---:|---:|---:|
| `6c4b6314` | 32 | 642723 | 467040 | 468456 |
| `748c31e3` | 10 | 115292 | 91485 | 91927 |
| `aba9257b9` | 3 | 25302 | 21698 | 21830 |

Compaction giảm lần lượt khoảng 27,3%, 20,6%, 14,2% token ước lượng trên biểu diễn đã ghi. Bộ lọc không giảm thêm ở ba trace này; concise tăng input do thêm hướng dẫn. Mỗi trace ghi 0 lần đọc chi tiết. Thời gian chuẩn bị và byte lặp có trong JSON, là phép đo một lượt offline, không phải benchmark latency provider. Không tự bật chính sách nào.

Giới hạn: kiểm chứng này chứng minh luồng và mức rút gọn biểu diễn. Chưa kết luận giảm hóa đơn hoặc giữ nguyên tỷ lệ giải quyết issue; cần pilot API thật riêng. Bộ test rộng còn 10 lỗi ngoài phạm vi về Environment/topology, số role và layout trace; không sửa các lỗi đó hoặc thay đổi chưa commit của người dùng.
