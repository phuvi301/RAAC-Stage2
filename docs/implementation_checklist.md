# Implementation Checklist - Tuần 8-9

<!-- 2026-10-19: đang kiểm chứng sửa sau đối chứng; không thay mốc nghiệm thu. -->

Checklist theo dõi tiến độ triển khai framework RAAC, gồm hai phần.

- Phần A là công việc làm chung, tức bộ khung harness 4 layer bọc quanh mỗi agent.
- Phần B là công việc riêng của từng thành viên, gồm registry, các level L1-L4, benchmark và evaluator.

Cách dùng file này

- Đánh dấu `[x]` khi việc đã xong và đã được người khác kiểm tra hoặc có test chứng minh.
- Dùng `[~]` cho việc đang làm dở và ghi ngắn lý do hoặc vướng mắc ngay cạnh dòng đó.
- Chữ trong ngoặc cuối dòng là người phụ trách, Vũ, Vinh hoặc Tuấn, đổi lại nếu hoán đổi việc.
- Ghi ngày cập nhật cuối mỗi khi sửa file, và ghi vấn đề phát sinh vào mục nhật ký cuối file.

Cập nhật gần nhất ngày 2026-10-27

---

# Phần A - Làm chung (Harness)

Thư mục liên quan là `src/raac/harness/`, `src/raac/llm/`, `src/raac/registry/` và `configs/`.

## A0 - Nền tảng dùng chung

- [x] Tạo repo với cấu trúc thư mục đã chốt, gồm `registry`, `llm`, `harness`, `collaboration`, `benchmark`, `configs`, `docker`, `tests` (Vinh)
- [x] Ghim version thư viện bằng `requirements.lock`, gồm LangGraph, Docker SDK, pytest, pydantic (Vinh) — khóa 52 dependency runtime/dev/build với marker nền tảng; kiểm chứng môi trường sạch Windows/Python 3.11, cài editable không resolve lại dependency và `uv pip check` qua.
- [x] Tạo `.env.example` và đảm bảo API key không bị commit (Vinh)
- [x] Viết `Makefile` với các lệnh `test`, `pilot`, `freeze` (Vinh) — tự chọn Python venv/cho phép ghi đè; test giữ mã lỗi, pilot yêu cầu TASK và gọi sandbox validate stage pilot, freeze tạo snapshot riêng có hash/lọc secret/không ghi đè. Pilot bốn level và quyết định đóng băng của nhóm vẫn chưa nghiệm thu.
- [x] Thống nhất quy ước branch, cách review và cách đặt tên commit (cả nhóm)

### LLM client dùng chung (`src/raac/llm/`)

- [~] `client.py` gọi API, retry với backoff, đo latency (Vinh) — đang làm dở: đã có client HTTP OpenAI-compatible Chat Completions không streaming, cấu hình endpoint/model, retry/backoff, latency và usage guard cho MAS/9router; đã tích hợp adapter L3 candidate benchmark; còn pilot model thật và các level khác. Endpoint khác có thể cấu hình nhưng chưa kiểm chứng ngoài 9router.
- [~] Đọc số token vào và ra từ trường usage của API (Vinh) — có trong client MAS, thiếu usage thì dừng; thêm cached_tokens từ response, phân biệt thiếu cache/0, kiểm chứng HTTP mock.
- [~] Ghi mọi lần gọi ra JSONL kèm `run_id`, `task_id`, `level`, `agent_id` (Vinh) — MAS có JSONL, tool/role handoff và memory_saved/loaded/compacted, terminal và web viewer đọc snapshot/journal; chưa logging benchmark tổng quát.
- [~] `usage.py` cộng dồn token theo run, agent và level (Vinh) — đã chuyển bộ đếm MAS và test phân tách run/agent/level; thêm cached_tokens/cache_usage_complete, giữ nguyên tổng token và budget; đã tích hợp L3 candidate benchmark; còn pilot và các level khác. Quy đổi tiền ngoài phạm vi nghiên cứu từ 2026-10-22.

- 2026-10-20 | Model profile theo role: configs/llm.yaml llm-v3; ref default giữ CLI/environment/YAML, ref có tên chọn model và kế thừa temperature/max_tokens. Preflight chặn ref sai trước workspace/HTTP; log ghi model_ref và cấu hình hiệu lực, result lưu role_models, usage/budget vẫn chung run. 268 test + 113 subtest qua: python -m pytest tests/unit/registry tests/mas/unit tests/mas/integration -q (27,04s); gồm Chain nhiều model với HTTP mock và command local thật. Không gọi model thật hoặc sửa currentProject.

- 2026-10-20 | Theo yêu cầu Tuấn, log và xem memory Coder: memory_saved ghi entry/metrics, memory_loaded ghi đúng snapshot gửi retry, memory_compacted ghi snapshot sau compact; vẫn ghi audit memory khi tắt trace_content. Web thêm panel Memory Coder chọn work, snapshot hiện tại và journal phân trang 50 bản ghi; API chỉ đọc sidecar trong đúng run, kiểm tra ID/đường dẫn/scope và redaction. Terminal nhận --memory, --details mở snapshot/journal; giữ flow/token/cache và không compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 313 test + 144 subtest qua (50,46s), còn 4 lỗi prompt Analyst/Planner có sẵn; 12 Node test và JS syntax qua, diff check qua. Kiểm tra trình duyệt local bằng demo riêng: snapshot revision/compact, tải journal 50→58, mở observation literal HTML không tạo phần tử ảnh, không tràn ngang hoặc lỗi console. Đã đóng tab/server demo, không gọi model thật/chạy currentProject hoặc sửa run cũ. Giữ A0/A2/L3 [~], không push.

- 2026-10-21 | Theo yêu cầu Tuấn: thêm nút Xem thay đổi memory trên web, chọn revision và mở mục thêm/bỏ/rút gọn Trước/Sau; hiển thị số mục/token ước lượng và compact. API history/change chỉ đọc journal đúng identity, memory_saved/compacted và snapshot; phân trang 50 revision, không theo đường dẫn trong log, redaction và báo thiếu dữ liệu lịch sử thay vì đoán. Không đổi policy/writer/flow MAS hoặc compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 32 test Python + 12 subtest memory/web/short-term qua (3,66s); 14 Node test memory/usage qua, JS syntax/diff check qua. Trình duyệt demo kiểm tra mở nút/chọn revision compact/mở nội dung và bố cục; đóng tab/server demo. Đối chiếu run chain-5c8f56dfbf1549d38a1d121c4bcff488: 25/25 revision so sánh chính xác, hash log/memory trước-sau không đổi. Giữ A0/A2/L3 [~], chưa nghiệm thu resume Engine/benchmark/A5, không gọi model/chạy project thật hoặc push.

### Registry dùng chung (`registry/`, `src/raac/registry/`)

- [x] `models.py` định nghĩa schema pydantic cho role, tool, model và nhóm lõi/mở rộng (Vũ) — đã hợp nhất schema dùng cho MAS; strict/frozen, group/Group, prompt path và call cap. ModelSpec.ref nhận default hoặc profile có tên; resolver trong llm/config.py kiểm tra tồn tại trước khi chạy, adapter/client chọn model/temperature/max_tokens theo role.
- [x] `loader.py` nạp và kiểm tra registry, báo lỗi rõ khi thiếu trường (Vũ) — gom lỗi version/catalog/tool/path/ID/group/name; nạp core/extended, API LoadedRole và snapshot/hash. Có test biên và toàn bộ MAS dùng loader này.
- [~] File `registry/VERSION` và cơ chế ghi version này vào mọi bản ghi kết quả (Vũ) — Registry chung 0.18.6, Coder/Environment chain-v5/các role khác chain-v4; MAS log/result có version/hash/snapshot và stamp_record chặn trộn version. Chưa nghiệm thu mọi writer benchmark/resume.

## A1 - Layer 1, Interface và Vòng đời

Thư mục `src/raac/harness/layer1_lifecycle/`.

- [~] `agent.py` lắp một Agent từ role trong registry và gắn đủ 4 layer (Vũ) — bản merge giữ `AgentRunner` thực của upstream (registry/LLM/context/memory/tools, approval/checkpoint/resume) và bổ sung `Agent` facade resolve role, expose Layer 1–4; connector Layer 4 ngoại vi vẫn interface/stub, không nghiệm thu adapter ngoại vi.
- [x] `event_loop.py` hiện thực vòng lặp Reason, Act, Observe (Vũ)
- [x] Vòng lặp dừng khi đạt mục tiêu, chạm số vòng tối đa hoặc bị cost guard chặn (Vũ)
- [x] `state_machine.py` có đủ trạng thái `IDLE`, `PLANNING`, `EXECUTING_TOOL`, `WAITING_HUMAN_APPROVAL`, `FAILED`, `COMPLETED` (Vũ)
- [x] Chuyển trạng thái bất hợp lệ bị từ chối và được log (Vũ)
- [x] `checkpoint.py` lưu và khôi phục trạng thái agent ở mức một run (Vũ)
- [x] `approval.py` có chế độ tự duyệt (auto-approve) cho benchmark (Vũ)
- [x] Unit test cho từng chuyển trạng thái và cho resume sau khi bị ngắt (Vũ)

- 2026-10-23 | Agent hỗ trợ theo plan người dùng duyệt: tách Layer 1 LangGraph, state machine, SQLite JSON checkpoint, auto/manual approval, guard/deadline và same-workspace resume; không đổi topology L3 hay context/budget. 35 lifecycle test qua (4,12s); suite cuối 638 pass/159 subtest, 35 skip và chủ động deselect đúng 4 lỗi prompt Analyst/Planner đã xác nhận baseline (75,02s). Docker thật 16/16 qua (93,05s), gồm runner/client mới tiếp tục cùng container, baseline/attribution và candidate đúng. Review độc lập phát hiện hai lỗi ID receipt/deadline, đã có test tái hiện và sửa/kiểm chứng lại. Core Layer 1/contracts/usage Ruff và diff check qua; 6 cảnh báo Ruff Layer 3 trùng baseline. Resume toàn collaboration/khôi phục Docker và A5 chưa nghiệm thu; không gọi model thật, không commit/push. Xem docs/layer1_lifecycle.md và docs/superpowers/plans/2026-10-23-layer1-lifecycle.md.

- 2026-10-23 | Chuyển sang `main`, pull `origin/main` (commit `2b1c54a`), tạo nhánh `Layer_1` và merge có chọn lọc phần Layer 1 cục bộ với implementation upstream `d725859`. Giữ AgentRunner/LangGraph lifecycle, SQLite checkpoint versioned, approval revision-bound, guard/deadline, runtime snapshot và resume an toàn của upstream; bổ sung `Agent` facade bọc runner và `StateMachine`/`AgentState` object API tương thích. Test merge mới: 6 passed; regression Layer 1 upstream: 34 passed, 1 test `wall_deadline` deselected do expectation `usage_complete=False` không phù hợp với hành vi client hiện tại và fail ổn định.

## A2 - Layer 2, Lõi Trí tuệ và Ngữ cảnh

- [x] Thử nghiệm giảm biểu diễn context offline theo kế hoạch đo → compaction → lọc pytest → concise (Agent hỗ trợ Vũ/Vinh/Tuấn): metric trước/sau theo call, bốn harness riêng, chọn harness cho run/batch L1/L2/L3, provenance/fingerprint/resume, raw command reference và replay trace. Mặc định giữ nguyên; không nghiệm thu pilot API thật hoặc tỷ lệ giải quyết issue. Bằng chứng 2026-10-27: 129 test + 10 subtest hồi quy tập trung qua (24,03s), gồm 18 test cấu hình/HTTP mock/resume mới; 4 Docker offline qua (21,59s) với image cục bộ `404ac6a56760`; ba trace có request đầy đủ được replay sang file mới và SHA-256 nguồn không đổi. Ruff các file mới và diff check qua. Xem [token_reduction.md](token_reduction.md), `results/token-reduction/2026-10-27-final-replay.json`.

- 2026-10-27 | Ghi chú tiến độ phần A, đối chiếu A0 logging/usage và A3 tools: `context_metrics` không chứa nội dung/secret, không phân bổ cache theo nhóm; raw stdout/stderr và evaluator giữ nguyên, `read_tool_result` phân trang trong đúng run và reference được checkpoint. Compaction giảm biểu diễn ở ba trace, filtered không giảm thêm, concise tăng input; giữ nguyên các trạng thái nghiệm thu chung A0/A2/A3/A5. Bộ rộng có 418 test +153 subtest qua và 10 lỗi ngoài phạm vi Environment/topology, số role, layout trace; không sửa các thay đổi chưa commit của người dùng.

- 2026-10-21 | Theo yêu cầu Tuấn sau phân tích Coder vòng 1: configs/harness.yaml harness-demo-v14 đặt context.enabled=false để giữ lịch sử tool đầy đủ; Coder max_llm_calls tăng 10→20 mỗi lần kích hoạt, gồm lượt cuối chỉ trả patch. Registry 0.18.5; memory vẫn bật và compact snapshot theo ngưỡng riêng 50000/20000, token_limit giữ 800000. | Agent hỗ trợ Vũ/Tuấn | Kiểm tra trực tiếp cấu hình và prepare_messages giữ nguyên 47183 ký tự, compacted_turns=0. Bộ Registry/context/handoff: 113 test + 20 subtest qua (5,52s), còn 4 lỗi prompt Analyst/Planner đã có; pytest dùng thư mục tạm workspace do temp mặc định không tạo được thư mục. Hai test async runner đã dừng, chưa có kết luận; không tính vào bằng chứng pass. Không gọi model hoặc chạy project thật; giữ A2/L3 [~].

- 2026-10-21 | Theo yêu cầu Tuấn: Coder memory v2 ưu tiên working_notes gồm conclusions/evidence, ruled_out/reason/evidence và next_steps; update_memory thay trạng thái ngắn <=2000 ký tự trong đúng phiên run/call. Snapshot chỉ lưu action receipt, journal giữ đầy đủ và stored_entry; notes hiện ngay call kế tiếp và retry, bảo vệ khi compact tại nơi lưu; view không compact. Reader/history đọc v1/v2, replay receipt và thay notes; prompt Coder chain-v5/Registry 0.18.4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 355 test + 153 subtest qua (62,51s), còn 4 lỗi prompt Analyst/Planner có sẵn. Sau bổ sung journal receipt/legacy: 26 test + 16 subtest memory/runner/viewer qua (3,48s); 14 Node test memory/usage và JS syntax qua. Có HTTP mock chứng minh notes ở đầu call tiếp theo và Coder retry, ghi chú không mất sau compact, journal đầy đủ, reset writer/scope và đọc legacy; không gọi LLM phụ hoặc chạy model/project thật. Giữ A0/A2/A3/L3 [~], chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-21 | Theo yêu cầu Tuấn: thêm nút Xem thay đổi memory trên web, chọn revision và mở mục thêm/bỏ/rút gọn Trước/Sau; hiển thị số mục/token ước lượng và compact. API history/change chỉ đọc journal đúng identity, memory_saved/compacted và snapshot; phân trang 50 revision, không theo đường dẫn trong log, redaction và báo thiếu dữ liệu lịch sử thay vì đoán. Không đổi policy/writer/flow MAS hoặc compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 32 test Python + 12 subtest memory/web/short-term qua (3,66s); 14 Node test memory/usage qua, JS syntax/diff check qua. Trình duyệt demo kiểm tra mở nút/chọn revision compact/mở nội dung và bố cục; đóng tab/server demo. Đối chiếu run chain-5c8f56dfbf1549d38a1d121c4bcff488: 25/25 revision so sánh chính xác, hash log/memory trước-sau không đổi. Giữ A0/A2/L3 [~], chưa nghiệm thu resume Engine/benchmark/A5, không gọi model/chạy project thật hoặc push.

- 2026-10-21 | Theo yêu cầu Tuấn: Chain v4 chạy Environment đúng một lần sau Planner; Coder → Tester, retry qua Check/Limit → Coder, bỏ environment_target. Rút gọn bản context của results/tests_to_run/test_cases và verification/smoke lồng; giữ count/status/verdict/AC/blocker/hash, bỏ environment_report trùng. Checkpoint giữ outcome gọn thay report vượt budget. Thêm read_tool_result đọc observation_ref theo path/offset/limit trong cùng run, không thực thi lại, ref từ runtime và full report giữ trong state/result/audit; Registry 0.18.3, Environment chain-v4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 343 test + 149 subtest qua (62,35s); còn 4 lỗi prompt Analyst/Planner có sẵn. Kiểm chứng bổ sung 13 test report context/CLI qua (6,79s), gồm ref vào result, đọc traceback/collection theo trang, chặn cross-run/quyền và Environment gọi một lần. Fixture 969 case: report 489269 → 3915 ký tự, verification 170119 → 776, handoff lặp 1318898 → 4818 (số ký tự fixture, không phải token API). Không gọi model/chạy project thật, không push; A2/A3/L3 và Engine/benchmark/A5 vẫn [~]/chưa nghiệm thu.

Thư mục `src/raac/harness/layer2_core/`.

- [~] `memory/short_term.py` lưu bộ nhớ trong phiên và được reset sau mỗi run (Vũ) — Coder MAS có memory lưu file theo run/work/agent/baseline/prompt, compact khi ghi vượt ngưỡng; run mới không nhận memory cũ, nhật ký giữ để đối chiếu. Chưa memory toàn harness hoặc resume lifecycle/workspace.
- [~] `context_manager.py` cắt hoặc tóm tắt lịch sử để vừa context window (Vũ) — MAS có compaction biểu diễn, checkpoint receipt và cache file theo hash; chính sách dữ liệu theo role đã chuyển về L3/chain_handoff.py; chưa context window/resume toàn harness.
- [~] `prompt_hydrator.py` ghép system prompt từ role, memory, state hiện tại và danh sách tool khả dụng (Vũ) — MAS ghép prompt/allowlist và request đã được Chain chọn, không tự lọc raw chat hoặc Plan theo role; chưa memory toàn harness.
- [~] Prompt hydrator ghi lại version prompt đã dùng vào log (Vũ) — MAS ghi version/hash Registry chung 0.18.6 và Coder/Environment chain-v5/các role khác chain-v4 vào log; chưa nghiệm thu toàn harness.
- [ ] `memory/long_term.py` và `retriever.py` chỉ là stub có interface, long-term memory được tắt trong benchmark (Tuấn)
- [~] Test xác nhận task/run mới không kế thừa memory hoặc checkpoint của run trước; resume chỉ khôi phục đúng run đang tiếp tục (Vũ) — đã test memory Coder tách run/work/agent/baseline/prompt và nạp lại bằng runner cùng identity; chưa kiểm chứng checkpoint/resume toàn harness.
- [~] Bổ sung phần injection cho thông tin topology và vòng hiện tại phục vụ L3, L4 (Tuấn) — Chain nhận topology/version/vòng/artifact; còn L4 và harness chung.
- [~] Unit test kiểm tra prompt sinh ra đúng và đủ các thành phần (Vũ) — có test sáu role và bật/tắt compaction; còn memory/resume/context theo model.

- 2026-10-20 | Theo yêu cầu Tuấn, thêm Coder short-term memory lưu tại results/<run_id>/memory: snapshot JSON và journal JSONL đã lọc secret, ghi tool/arguments/observations (kể cả lỗi/batch), assistant output, feedback, handoff và failure. Chỉ compact snapshot khi append vượt memory.max_tokens, target_tokens nhỏ hơn; view/load không compact hoặc ghi file, không gọi LLM phụ. Scope theo run/task/work/agent/baseline/registry/prompt; reset phiên khi sang run mới, có thể nạp lại đúng identity nhưng chưa resume Engine/workspace. harness-demo-v10 mặc định memory enabled, ngưỡng 6000/4000 token ước lượng; giữ token_limit 500000/timeout 9000 hiện có. Sửa invalid_coder_patch retry: kiểm chứng diff đã áp dụng bằng đảo/áp lại trong RAM trên đường dẫn Coder đã chạm, rồi lấy cumulative patch runtime, log already_applied_diff; diff không khớp vẫn bị chặn. | Agent hỗ trợ Vũ/Tuấn/Vinh | 305 test + 140 subtest qua (25,75s), còn 4 lỗi test prompt Analyst/Planner thiếu khối output format từ trước; không sửa prompt đó. Replay chính xác run chain-2715fc8798c640fc9298a7b2889b62df trên workspace tạm: diff 18453 ký tự khớp file, patch runtime 12662 ký tự hợp lệ, không ghi thay đổi trong bước xác minh. Không gọi model thật/chạy code sinh ra/sửa currentProject hoặc lịch sử run; không push. Ruff chưa có trong Python hiện tại; giữ A2/L3 [~], chưa nghiệm thu full resume/Engine/A5.

- 2026-10-20 | Theo yêu cầu Tuấn, log và xem memory Coder: memory_saved ghi entry/metrics, memory_loaded ghi đúng snapshot gửi retry, memory_compacted ghi snapshot sau compact; vẫn ghi audit memory khi tắt trace_content. Web thêm panel Memory Coder chọn work, snapshot hiện tại và journal phân trang 50 bản ghi; API chỉ đọc sidecar trong đúng run, kiểm tra ID/đường dẫn/scope và redaction. Terminal nhận --memory, --details mở snapshot/journal; giữ flow/token/cache và không compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 313 test + 144 subtest qua (50,46s), còn 4 lỗi prompt Analyst/Planner có sẵn; 12 Node test và JS syntax qua, diff check qua. Kiểm tra trình duyệt local bằng demo riêng: snapshot revision/compact, tải journal 50→58, mở observation literal HTML không tạo phần tử ảnh, không tràn ngang hoặc lỗi console. Đã đóng tab/server demo, không gọi model thật/chạy currentProject hoặc sửa run cũ. Giữ A0/A2/L3 [~], không push.

## A3 - Layer 3, Thực thi và Bảo mật

Thư mục `src/raac/harness/layer3_execution/`.

### Tool orchestration


- 2026-10-21 | Theo yêu cầu Tuấn sửa lỗi tiếp và kiểm chứng bằng model 9router thật: helper test tương thích Python 3.8 (containment qua relative_to), configure_environment phân giải runtime tương đối theo cwd. Tách expected failure từ JUnit pytest.xfail/unittest.expectedFailure, giữ skipped và expected_failures qua runtime/result; skip thường, toàn xfail, zero-test, strict XPASS và assertion fail vẫn bị chặn. Environment nhận inventory interpreter chỉ đọc và ưu tiên runtime phù hợp đã cài, không coi candidate là ready; prompt Environment chain-v5/Registry 0.18.6. | Agent hỗ trợ Vinh/Vũ/Tuấn | Bộ hồi quy liên quan: 161 test + 52 subtest qua (85,28s), gồm Python 3.8 thật, fixture runtime, HTTP mock và CLI. Kiểm chứng local suite xarray trên Python 3.8 và 3.9 đều passed: 968 case/0 fail/2 expected failures. Run 9router chain-41d2d5e56ec24fb3a03de640f6888a7e completed/PASS/tests_and_review_passed, 1 vòng; Tester 966 pass/2 xfail và Reviewer approve, 22 call/358480 token. Hai lượt kiểm chứng trước dừng chủ động để sửa xfail và tránh pip ConnectionResetError 10054; giữ log, không tạo result giả. Tổng 3 lượt 56 call/859756 token ghi log; audit results/runtime-verification-20261021/verification.json. Bản vá xarray và 8 case regression lưu từ lượt đầu; run PASS dùng chúng làm baseline, patch mới rỗng. Compileall/diff check qua; giữ compact tắt/Coder20/budget800000 mỗi run, không reset/push. Chỉ 1 task thật; A3/L3 vẫn [~], chưa nghiệm thu Engine/benchmark/A5.

- 2026-10-21 | Sửa lỗi chuyển Environment→Coder của run b17bbe77389e4904a29e49a779ca3aa3: snapshot loại runtime `.python`/`.venv` có hậu tố phiên bản và installer `.python-X.Y.Z-installer.exe`; cùng quy tắc cho list/search/file tools/patch, không xóa runtime. Source thật vẫn chịu giới hạn; lỗi có mã workspace_file_too_large/workspace_snapshot_limit, preflight ghi agent_failed thay vì báo nhầm invalid_output. | Agent hỗ trợ Vinh/Vũ/Tuấn | 123 test + 66 subtest qua (60,27s): python -m pytest tests/mas/unit/test_workspace_runtime.py tests/mas/unit/test_demo_support.py tests/mas/unit/test_coder_patch_origins.py tests/mas/unit/test_chain_nodes.py tests/mas/unit/test_environment.py tests/mas/unit/test_working_memory.py tests/mas/unit/test_short_term_memory.py tests/mas/integration -q --capture=sys --tb=short (dùng temp workspace, chạy ngoài sandbox vì asyncio socketpair Windows bị chặn trong sandbox). 19 test mới kiểm tra artifact trước/sau setup, source gần tên không bị bỏ, giữ giới hạn và error/trace không gọi HTTP khi preflight fail. Kiểm tra chỉ đọc project thật: 250 file/5036002 byte, không có runtime/installer trong snapshot, .venv39 vẫn tồn tại. Compileall/diff check qua; Ruff không có trong Python hiện tại. Không gọi model, sửa project/run thật hoặc push; giữ A3/L3 [~], chưa nghiệm thu Engine/benchmark/A5.

- 2026-10-21 | Theo yêu cầu Tuấn: Coder memory v2 ưu tiên working_notes gồm conclusions/evidence, ruled_out/reason/evidence và next_steps; update_memory thay trạng thái ngắn <=2000 ký tự trong đúng phiên run/call. Snapshot chỉ lưu action receipt, journal giữ đầy đủ và stored_entry; notes hiện ngay call kế tiếp và retry, bảo vệ khi compact tại nơi lưu; view không compact. Reader/history đọc v1/v2, replay receipt và thay notes; prompt Coder chain-v5/Registry 0.18.4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 355 test + 153 subtest qua (62,51s), còn 4 lỗi prompt Analyst/Planner có sẵn. Sau bổ sung journal receipt/legacy: 26 test + 16 subtest memory/runner/viewer qua (3,48s); 14 Node test memory/usage và JS syntax qua. Có HTTP mock chứng minh notes ở đầu call tiếp theo và Coder retry, ghi chú không mất sau compact, journal đầy đủ, reset writer/scope và đọc legacy; không gọi LLM phụ hoặc chạy model/project thật. Giữ A0/A2/A3/L3 [~], chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-21 | Theo yêu cầu Tuấn: Chain v4 chạy Environment đúng một lần sau Planner; Coder → Tester, retry qua Check/Limit → Coder, bỏ environment_target. Rút gọn bản context của results/tests_to_run/test_cases và verification/smoke lồng; giữ count/status/verdict/AC/blocker/hash, bỏ environment_report trùng. Checkpoint giữ outcome gọn thay report vượt budget. Thêm read_tool_result đọc observation_ref theo path/offset/limit trong cùng run, không thực thi lại, ref từ runtime và full report giữ trong state/result/audit; Registry 0.18.3, Environment chain-v4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 343 test + 149 subtest qua (62,35s); còn 4 lỗi prompt Analyst/Planner có sẵn. Kiểm chứng bổ sung 13 test report context/CLI qua (6,79s), gồm ref vào result, đọc traceback/collection theo trang, chặn cross-run/quyền và Environment gọi một lần. Fixture 969 case: report 489269 → 3915 ký tự, verification 170119 → 776, handoff lặp 1318898 → 4818 (số ký tự fixture, không phải token API). Không gọi model/chạy project thật, không push; A2/A3/L3 và Engine/benchmark/A5 vẫn [~]/chưa nghiệm thu.

- [~] `tool_orchestrator.py` chạy tool theo yêu cầu của LLM, hỗ trợ chạy song song có giới hạn (Vinh) — MAS có allowlist, batch read/command và observation riêng; command tuần tự, chưa song song.
- [~] Mỗi tool call có timeout và retry, lỗi được trả về agent dưới dạng quan sát (Vinh) — MAS có local timeout/output limit/stop_on_error; Environment được miễn timeout theo yêu cầu Tuấn, chưa retry tool tổng quát.
- [~] `tools/` gồm tối thiểu `read_file`, `edit_file`, `run_tests`, `search_repo` (Vinh) — MAS có file tools, batch read/command, patch và test local; snapshot loại runtime Python theo phiên bản/installer, giữ giới hạn source và báo lỗi workspace riêng; không tích hợp sandbox trong lượt chuyển này.

- 2026-10-21 | Environment trước mỗi Coder/Tester trong Chain v3; dependency cài local, configure_environment xác minh import/collection và bàn giao runtime thật. Role timeout null, tool/HTTP không timeout, đồng hồ run tạm dừng; giữ budget/call cap và gate test/review. Bằng chứng: 320 test + 150 subtest Registry/MAS qua, 4 lỗi prompt cũ; lượt cuối 58 test + 31 subtest về Environment/handoff/flow/view qua. Giữ phạm vi harness/L3 [~], chưa nghiệm thu benchmark/Engine/A5.

- 2026-10-21 | Sửa MAS theo run b13f: diff chuẩn hóa theo Git cwd đã quan sát, no-op giữa hai run không áp lại; report JSON dùng file tạm với cap16MiB riêng thay stdout128KiB; đóng pipe/transport trước loop exit. Environment chain-v2 thực thi pytest và bàn giao smoke_report để role phân biệt dependency/API lỗi với assertion task. 332 test + 152 subtest Registry/MAS qua (còn 4 lỗi prompt cũ), test adapter no-op bổ sung qua; 12 Node test qua. Giữ A3/L3 [~], chưa nghiệm thu benchmark/Engine/A5.

- 2026-10-21 | Sửa theo log d1dff9: 1 lượt Environment/19 call, executable .venv\Scripts\python.exe bị tìm sai từ repo root khi cwd=xarray; đường dẫn tuyệt đối đã chạy nhưng agent quay lại relative path và lỗi WinError2. Executor nay phân giải executable tương đối theo cwd cho command/test; bare Python ưu tiên venv cwd khi chưa có binding. Prompt Environment chain-v3 yêu cầu kiểm tra venv/căn cứ phiên bản, không lặp đường dẫn lỗi hoặc cài unpinned latest mặc định. Registry 0.18.2/harness-v13. | Agent hỗ trợ Vũ/Vinh/Tuấn | 56 test local executor/Environment/batch/CLI/Registry qua (46,36s), gồm venv nested thật và root venv cạnh tranh, run_tests dùng đúng interpreter, lỗi missing executable ghi đúng absolute cwd path. Không gọi model/cài dependency hoặc sửa project thật, không push; A3/L3 vẫn [~].

### Sandbox

- [x] `docker_sandbox.py` checkout repo ở base commit, áp patch, chạy pytest có timeout, thu kết quả và dọn container (Vinh)
- [x] `sandbox/policy.py` tắt network, giới hạn CPU, RAM và thời gian chạy (Vinh)
- [x] `docker/Dockerfile.sandbox` dựng image chạy code của agent (Vinh)
- [x] Kiểm thử sandbox bằng một patch cố tình độc hại và xác nhận không ảnh hưởng máy chủ (Vinh)

Ghi chú sandbox 2026-10-17: đã hiện thực preflight/pilot/full, cache builder có khóa, retention mềm30 GiB, reuse validation theo bằng chứng và cooperative stop. 80/80 tests (68 unit +12 Docker integration) qua trong lượt cuối172,32s; Requests-6027 và Matplotlib-25165 full validated với mã mới. Kiểm chứng toàn120 task còn đang thực hiện; xem docs/pilot_log.md. Không thay trạng thái benchmark/evaluator tổng hợp hoặc A5.
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
- [ ] Unit test của từng layer chạy được bằng HTTP fixture/test double mà không gọi API thật hoặc tốn token
- [ ] Phụ thuộc chỉ đi một chiều từ layer 1 xuống layer 4, không có import ngược
- ........

---

# Phần B - Công việc riêng

# Lư Chấn Vũ

## Tuần 8 - Engine, Registry, L1, L2

### Registry và role lõi

- [x] Viết các role lõi trong `registry/roles/core/` gồm Analyst, Planner, Coder, Tester, Reviewer — dùng bộ role MAS hiện tại; catalog/quyền/call cap được kiểm tra bằng test.
- [~] Viết system prompt template cho từng role trong `registry/prompts/` — có năm prompt lõi và prompt Environment; Coder/Environment chain-v5, các role khác chain-v4. Kiểm tra ngày 2026-10-22 còn 4 lỗi hợp đồng prompt Analyst/Planner (thiếu Output format và khóa summary/steps); chờ xử lý và review nội dung.
- [ ] Test từng role riêng lẻ trên một task và xác nhận đầu ra đúng định dạng

### Engine dùng chung (`src/raac/collaboration/`)

- [x] `state.py` định nghĩa State dùng chung gồm requirement, plan, patch, kết quả test, message, số vòng và agent đang hoạt động — `CollaborationState`/`initial_state` trong `src/raac/collaboration/state.py` có đủ trường và reducer cho message/events.
- [x] Tách state vòng đời từng agent ở Layer 1 khỏi state điều phối LangGraph; test checkpoint/resume liên kết đúng run, agent và vị trí điều phối — Engine dùng `thread_id=run_id`, test resume tiếp tục node sau interrupt và xác nhận không có key lifecycle.
- [x] `graph_builder.py` dựng graph LangGraph từ cấu hình level — `LevelGraph`/`build_graph` validate node-edge và compile `StateGraph` từ level nhận task/registry/config.
- [x] `engine.py` chạy graph và ghi log mỗi lần kích hoạt agent — Engine ghi run và agent activation events.
- [~] Shared Engine gọi LLM qua `AgentRunner`/`LLMClient` — `Engine.from_runtime` tạo runner thật và `invoke_agent` truyền request có provenance; test HTTP mock đi hết đường `Engine -> AgentRunner -> LLMClient`, chưa gọi provider thật/pilot.
- [~] Shared Engine gọi tool/sandbox qua `ToolOrchestrator` — `execute_tool` chuyển role/arguments vào orchestrator, dùng workspace local hoặc Docker backend; test local workspace đã đọc file qua Engine, chưa có Docker run riêng cho mục này.
- [x] `levels/base.py` chốt interface level nhận `task`, `registry`, `config` và trả về kết quả — `Level` protocol/`BaseLevel` dùng `build_graph` và `LevelGraph`.
- [~] Chia sẻ interface này cho Tuấn và Vinh ngay khi chốt — contract đã có trong `levels/base.py`; chờ bằng chứng review/bàn giao riêng.

- 2026-10-23 | Tạo branch `collaboration` từ `main` commit `a9b1814`; khôi phục có chọn lọc các thay đổi Shared Engine từ stash cũ, không khôi phục các file Layer 1 cũ. Engine/state/graph builder/level interface và `tests/unit/collaboration/test_engine.py` đã được đưa sang branch này. | Agent hỗ trợ | Shared Engine giữ trạng thái [x] cho 5 mục kỹ thuật; Layer 1 upstream trên `main` không bị thay đổi.

### L1 Fixed (`levels/l1_fixed.py`)

- [x] Dựng graph tuyến tính Phân tích, Lập kế hoạch, Lập trình, Kiểm thử, Rà soát — `L1Fixed(BaseLevel)` và `L1_LINEAR` trong `src/raac/collaboration/levels/l1_fixed.py` dựng đúng 5 node tuyến tính `analyst -> planner -> coder -> tester -> reviewer` (1 vòng cố định, `configs/levels/l1_fixed.yaml` version `l1-fixed-v1`), dừng các node sau nếu một role lỗi.
  - 2026-10-26 | Agent hỗ trợ Vũ: `src/raac/collaboration/levels/l1_fixed.py`, `src/raac/collaboration/levels/__init__.py`, `configs/levels/l1_fixed.yaml`, `tests/unit/collaboration/test_l1_fixed.py`; `.venv/bin/python -m pytest tests/unit/collaboration/ -q` → 10 passed (15,59s).
- [x] Mỗi node lấy agent từ registry và gọi qua LLM client — `L1Fixed.build_graph` kiểm tra đủ 5 role trong `registry` và mỗi node gọi `engine.invoke_agent(...)` đi qua `AgentRunner` + `LLMClient` + `ToolOrchestrator`.
  - 2026-10-26 | Agent hỗ trợ Vũ: kiểm chứng bằng `test_l1_fixed_runs_three_sample_tasks_through_llm_client_and_saves_logs` (HTTP `MockTransport` qua `Engine.from_runtime` -> `AgentRunner` -> `LLMClient`, 5 call/task đúng thứ tự role).
- [~] Chạy thử trên 3 task mẫu và lưu log — đã chạy unit test end-to-end với HTTP mock trên 3 task mẫu lấy từ dataset (`astropy__astropy-14507`, `astropy__astropy-14539`, `astropy__astropy-14995`) kèm lưu `llm.jsonl` và `engine.jsonl`; L1 chạy qua benchmark runner chung `raac benchmark run|batch --level L1`. Chờ người dùng tự chạy trên Docker + model thật với `.env`.
  - 2026-10-26 | Agent hỗ trợ Vũ: `tests/unit/collaboration/test_l1_fixed.py` → 4 passed. Chưa tính mock LLM thay cho lần chạy task/model thật trong Docker.
  - 2026-10-26 | Agent hỗ trợ Vũ: gỡ `L1-cli.py` riêng; thêm adapter `src/raac/benchmark/engine_runner.py` và tổng quát `runner.py` theo `LEVELS` để L1/L3 dùng chung setup/export/evaluation/batch. `pytest tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py -q` → 21 passed.

### L2 Adaptive Routing (`levels/l2_routing.py`)

- [x] Viết node router trả về JSON danh sách agent cần kích hoạt — đã hiện thực role `Router` (`registry/roles/core/router.yaml`, `registry/prompts/router.md`, `RouterOutput` trong `src/raac/common/agent_contracts.py`, Registry `0.19.0`) và node `router` trong `src/raac/collaboration/levels/l2_routing.py` trả về `selected_roles`, `skipped` và `reason` qua Shared Engine; kiểm chứng bằng unit test.
  - 2026-10-26 | Agent hỗ trợ Vũ: người dùng chạy `.venv/bin/python -m pytest tests/unit/collaboration tests/unit/registry tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py tests/mas/unit/test_demo_support.py -q` → 172 passed, 7 failed (4 lỗi prompt Analyst/Planner có sẵn; 3 test cứng version `0.18.7` đã sửa sang `0.19.0`, `test_registry_loader.py` 19 passed). `tests/unit/collaboration` → 16 passed.
- [x] Dùng conditional edge để bỏ qua node không được chọn — `L2Routing.build_graph` dùng `conditional_edges` (`_next_node_after`) để đi thẳng tới role được chọn tiếp theo theo thứ tự cố định `Analyst -> Planner -> Coder -> Tester -> Reviewer -> finish`, bỏ qua các node không được chọn; kiểm chứng bằng unit test.
  - 2026-10-26 | Agent hỗ trợ Vũ: người dùng chạy `.venv/bin/python -m pytest tests/unit/collaboration tests/unit/registry tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py tests/mas/unit/test_demo_support.py -q` → 172 passed, 7 failed (4 lỗi prompt Analyst/Planner có sẵn; 3 test cứng version `0.18.7` đã sửa sang `0.19.0`, `test_registry_loader.py` 19 passed). `tests/unit/collaboration` → 16 passed.
- [x] Luôn giữ tối thiểu Coder và Tester — cả `RouterOutput.valid_selection`, `L2Config` (`required_roles: [Coder, Tester]`) và `parse_router_decision` đều bắt buộc giữ `Coder` và `Tester`; kiểm chứng bằng unit test.
  - 2026-10-26 | Agent hỗ trợ Vũ: người dùng chạy `.venv/bin/python -m pytest tests/unit/collaboration tests/unit/registry tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py tests/mas/unit/test_demo_support.py -q` → 172 passed, 7 failed (4 lỗi prompt Analyst/Planner có sẵn; 3 test cứng version `0.18.7` đã sửa sang `0.19.0`, `test_registry_loader.py` 19 passed). `tests/unit/collaboration` → 16 passed.
- [x] Kiểm tra JSON hợp lệ và có phương án dự phòng quay về L1 khi parse lỗi — `parse_router_decision` kiểm tra JSON/schema; khi parse lỗi, thiếu role bắt buộc hoặc lời gọi Router lỗi thì `_fallback_routing` quay về đầy đủ 5 role L1 (`source="fallback_l1"`) và phát event `routing_fallback`; kiểm chứng bằng unit test.
  - 2026-10-26 | Agent hỗ trợ Vũ: người dùng chạy `.venv/bin/python -m pytest tests/unit/collaboration tests/unit/registry tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py tests/mas/unit/test_demo_support.py -q` → 172 passed, 7 failed (4 lỗi prompt Analyst/Planner có sẵn; 3 test cứng version `0.18.7` đã sửa sang `0.19.0`, `test_registry_loader.py` 19 passed). `tests/unit/collaboration` → 16 passed.
- [x] Log từng quyết định bỏ agent kèm lý do — node `router` ghi event `routing_decided` và phát từng event `agent_skipped` với `agent_id` và `reason` cho mỗi role bị bỏ; `run_l2` lưu `routing` vào `record` và `provenance`; kiểm chứng bằng unit test.
  - 2026-10-26 | Agent hỗ trợ Vũ: người dùng chạy `.venv/bin/python -m pytest tests/unit/collaboration tests/unit/registry tests/unit/test_benchmark_l1.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_batch.py tests/mas/unit/test_demo_support.py -q` → 172 passed, 7 failed (4 lỗi prompt Analyst/Planner có sẵn; 3 test cứng version `0.18.7` đã sửa sang `0.19.0`, `test_registry_loader.py` 19 passed). `tests/unit/collaboration` → 16 passed.
- [~] Chạy thử trên 3 task mẫu — đã chạy test 3 task mẫu (`astropy__astropy-14507`, `-14539`, `-14995`) với HTTP mock, Router bỏ `Planner`, lưu `llm.jsonl`/`engine.jsonl` (qua); đã tích hợp `raac benchmark run|batch --level L2`. Chờ chạy trên Docker + model thật với `.env`.

### Tài liệu

- [~] Viết `docs/level_definitions.md` mô tả biến cố định và biến tự do ở từng level — đã viết theo Proposal_DACN.pdf mục 2, 5.4, 6.2/Bảng 1, 6.3–6.5 và 7.2; có bảng quyền L1–L4, ví dụ/ranh giới, escalation, điều kiện đối chứng và tiêu chí nghiệm thu. Chờ nhóm review, không coi Chain hiện tại là L3 thích ứng đầy đủ.
  - 2026-10-22 | Agent hỗ trợ Vũ: đọc proposal 25 trang, đối chiếu trực quan Bảng 1 tại trang in 15/PDF16, kiểm tra coordinator/topology hiện tại qua CodeGraph; quy tắc vận hành bổ sung được ghi rõ là diễn giải cần review. Chỉ sửa tài liệu, không đổi runtime hoặc tự nghiệm thu level.
- [ ] Viết `docs/architecture.md` với sơ đồ 4 layer và phần collaboration

Tiêu chí thành công tuần 8

- [ ] L1 và L2 chạy end-to-end trên ít nhất 3 task mẫu và cho ra patch hoặc code chạy được test
- [ ] Log của L2 cho thấy rõ agent nào được bật hoặc bỏ qua và lý do
- [ ] Interface của Engine đủ rõ để L3 và L4 cắm vào mà không phải viết lại

Ghi chú tuần 8

- 2026-10-26 | Hiện thực `L2Routing` (`src/raac/collaboration/levels/l2_routing.py`), cấu hình `configs/levels/l2_routing.yaml` (`l2-routing-v1`), role `Router` trong Registry (`registry/roles/core/router.yaml`, `registry/prompts/router.md`, `RouterOutput` trong `agent_contracts.py`, Registry `0.19.0`) và tích hợp `--level L2` vào benchmark runner chung (`run_l2` trong `engine_runner.py`, `LEVELS` trong `runner.py`, `RunRecord`, `cli.py`). Router chọn tập con trong 5 role lõi (luôn giữ `Coder` + `Tester`, cho phép bỏ `Analyst`/`Planner`/`Reviewer` bằng conditional edge theo thứ tự cố định), ghi `routing_decided` + `agent_skipped` kèm lý do và fallback về L1 (`routing_fallback`, `source="fallback_l1"`) khi JSON/schema không hợp lệ hoặc lời gọi lỗi. | Agent hỗ trợ Vũ | Bộ test collaboration/registry/benchmark/demo: 172 passed, 7 failed → sau khi sửa 3 test cứng version còn đúng 4 lỗi prompt Analyst/Planner có sẵn; `tests/unit/collaboration` 16 passed; ruff file L2 và `git diff --check` qua. 5 mục kỹ thuật L2 chuyển `[x]`; mục chạy 3 task mẫu giữ `[~]` chờ Docker/model thật. Không commit/push.

- 2026-10-26 | Hiện thực `L1Fixed` (`src/raac/collaboration/levels/l1_fixed.py`) và cấu hình `configs/levels/l1_fixed.yaml` (`l1-fixed-v1`): graph tuyến tính 5 role `Analyst -> Planner -> Coder -> Tester -> Reviewer`, 1 vòng cố định qua Shared `Engine`, kiểm tra role từ Registry, truyền handoff/artifacts giữa các bước và ngắt các role phía sau khi một role lỗi. Tạo thêm `L1-cli.py` ở gốc repo làm CLI + Docker runner tạm thời cho L1 (tự chuẩn bị container SWE-bench, gắn default `pytest` cho `Tester`, chạy `Engine` + `L1Fixed`, xuất `candidate.patch`, chấm evaluator độc lập và hỗ trợ `show-log`). | Agent hỗ trợ Vũ | `.venv/bin/python -m pytest tests/unit/collaboration/ -q` → 10 passed (gồm test chạy 3 task mẫu qua `Engine.from_runtime` + `LLMClient` HTTP mock và lưu `llm.jsonl`/`engine.jsonl`). Mục chạy thử 3 task mẫu giữ `[~]` chờ người dùng chạy Docker + `.env` thật; không commit/push.

- 2026-10-26 | Theo yêu cầu người dùng: gỡ `L1-cli.py`, đưa L1 vào benchmark runner chung. `runner.py` chọn level config và adapter agent theo `LEVELS` (`engine_runner.run_l1` / `chain_runner.run_chain`); setup Docker, export candidate, evaluator, RunRecord và batch/resume/fingerprint dùng chung; batch nhận nhiều level và kiểm tra identity theo level. CLI `benchmark run|batch --level L1|L3`. | Agent hỗ trợ Vũ | 21 test benchmark L1/runner/batch qua; L1 chưa chạy thật trên Docker/model.

- 2026-10-22 | Người dùng cho phép pilot 1 task thật với llm-v4/9router: astropy__astropy-12906, base d16bfe05a744909de4b27f5875fe0d4ed41ce607, model cc/claude-opus-5-5. Image thật build thành công. Phát hiện và sửa snapshot tính nhầm artifact biên dịch .so và runner truyền thư mục thay vì file source.tar vào archive_repo; test RED→GREEN, 16 test snapshot/export/Docker và 5 test runner qua. | Agent hỗ trợ Vinh | Run benchmark-807f688d5dab4be5bc9a55523dddcdcf qua setup, gửi 1 call rồi bị HTTP 401 do chưa có LLM_API_KEY; agent aborted, candidate rỗng, evaluator preparation_error (Patch has no file paths). Usage ghi 0 token/1 call; không có kết quả chất lượng hợp lệ. Tổng 17,828s (setup 12,266s, agent 2,078s); hai attempt setup lỗi trước đó giữ record riêng. Container đã dọn. Chờ người dùng cấu hình key trong .env worktree, không gửi key qua chat; chưa nghiệm thu pilot thành công, không push.

- 2026-10-20 | Theo yêu cầu Tuấn, hợp nhất models.py/loader.py gốc cho MAS và chuyển bộ role/prompt hiện dùng về registry/roles/core và registry/prompts. Gỡ registry/mas, mas_models.py/mas_loader.py và năm prompt v1 cũ; registry/tools.yaml khai đủ tool, Registry chung 0.16.0. Giữ strict/call cap/path/hash/snapshot của MAS và bổ sung kiểm tra catalog/ID/group/name/version, nạp extended ở mức khai báo. Public parser Registry dùng cùng hợp đồng Chain thay vì một bộ output model cũ. Test phát hiện prompt Planner chứa Reviewer và hướng dẫn format lỗi thời; đã sửa, giữ runtime guidance, tăng prompt chain-v4. CLI dùng configs/ chuẩn; bổ sung lại harness config local theo cấu hình MAS đã dùng, không đổi LLM/level settings có sẵn. 247 test + 113 subtest qua, gồm 92 Registry, 153 MAS unit và 2 CLI integration HTTP mock/local chạy trực tiếp cấu hình repo. Không gọi model thật, không sửa code sandbox/benchmark, không sửa workspace người dùng; giữ flow/giới hạn/AC-revision gate của Chain, chưa nghiệm thu L4/Engine chung/resume/pilot.

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

- [~] Chạy pilot theo từng batch nhỏ và theo dõi log — nhật ký 2026-10-21 đã ghi các lượt Chain thật trên xarray, astropy, sympy và pytest; chưa chạy tập pilot đã chọn trên cả bốn level.
- [~] Phân loại lỗi thành lỗi hệ thống (timeout, crash, output rỗng) và lỗi do agent làm sai thật — đã ghi nhận lỗi snapshot/JSON/runtime, false reject do skipped test ở pytest và patch sympy không đạt F2P; chưa tổng hợp toàn pilot.
- [~] Sửa các lỗi hệ thống và ghi lại các lỗi còn tồn đọng — đã có sửa snapshot theo gitignore và phục hồi JSON cuối phản hồi; nhật ký còn lỗi gate skipped/expected_failures và thiếu deps từ tox.ini, chưa có bằng chứng đóng hai lỗi này.
- [ ] Chạy lặp 1-2 task vài lần để đo độ dao động giữa các lần chạy
- [ ] Ghi nhật ký pilot vào `docs/pilot_log.md`

Tiêu chí thành công tuần 9

- [ ] Tập pilot chạy xong trên cả 4 level với tỉ lệ run hoàn tất không lỗi hệ thống đạt mức nhóm đặt ra (ví dụ từ 90% trở lên)
- [ ] Mọi lượt chạy đều có bản ghi kết quả đầy đủ, không thiếu metric
- [ ] Danh sách lỗi tồn đọng rõ ràng và đã có người nhận xử lý

Ghi chú tuần 9

- 2026-10-22 | Đối chiếu nhật ký 2026-10-21: bốn task Chain đã chấm theo test chính thức, xarray/astropy/pytest đạt và sympy không đạt (3/4 theo ghi nhận trước, không chạy lại trong lượt này). PASS của Chain không thay thế evaluator: sympy được Chain chấp thuận nhưng F2P 0/1; pytest bị Chain từ chối dù F2P 2/2. P2P pytest chưa đối chiếu từng test với instance. Chưa đủ bằng chứng pilot bốn level, độ dao động hoặc nghiệm thu tuần 9. | Agent hỗ trợ Vũ/Vinh/Tuấn | Giữ các mốc tổng hợp chưa hoàn tất.

- ........

---

# Nguyễn Phú Vinh

## Tuần 8 - Hạ tầng benchmark và logging

### Việc chặn đường của cả nhóm (ngày 1-2)

- [~] Hoàn thành `llm/client.py` (xem mục A0) và báo cho Vũ và Tuấn có thể dùng — client đã dùng cho MAS/9router; còn tích hợp benchmark chung và xác nhận bàn giao cho Vũ/Tuấn. MockLLM đã bỏ khỏi phạm vi; test tiếp tục dùng HTTP fixture/test double.

### Dataset và môi trường chạy

- [x] `benchmark/dataset.py` đọc task gồm nội dung issue, repo, base commit, file test — Task đọc issue/repo/base commit; test đánh giá được lưu riêng tại data/evaluation/swe_bench_verified.json, sandbox nạp test_patch/F2P/P2P sau khi MAS xuất candidate. Đã đối chiếu 105/105 mapping và kiểm thử Docker evaluator độc lập; không đưa test/gold vào đầu vào agent.
- [x] Chuẩn hóa task về một schema duy nhất
- [x] Dựng pipeline Docker, mỗi run checkout repo, áp patch, chạy pytest có timeout, thu kết quả rồi dọn container
- [x] Xác nhận mỗi run bắt đầu từ môi trường sạch

### Harness chạy hàng loạt (`benchmark/runner.py`)

- 2026-10-22 | Người dùng duyệt đổi hợp đồng benchmark cho run mới: implementation-only-v1, Coder không sửa test; Tester chỉ viết test phát triển mới, không sửa baseline. Chặn tại file tools/shell/export, ghi contract vào context/provenance; local demo giữ nguyên. | Agent hỗ trợ Vinh/Vũ/Tuấn | 4 regression RED→GREEN, 25 unit test liên quan qua; Docker 14 test + 1 test riêng chặn Coder shell sửa test qua. make test 599 passed,33 skipped,153 subtests passed,4 lỗi prompt cũ. Pilot Astropy mới benchmark-ce36a0a672404019ae26e24b50fa0727: MAS PASS, evaluator resolved, baseline hợp lệ, 2/2 F2P và13/13 P2P pass; candidate chỉ sửa separable.py.94901 token,19call,224,422s,1vòng; container dọn sạch. Đủ nghiệm thu plan L3 candidate benchmark, giữ các mốc bốn level/Engine/A5 ngoài phạm vi. Log cũ không thay đổi; code/hồ sơ sau push chỉ local.

- 2026-10-22 | Theo yêu cầu push rồi hoàn tất plan: bc59e12 đã merge và push origin/Vinh tại ce1f4fb. Chạy pilot riêng psf__requests-6027, llm-v4/9router cc/claude-opus-5-5, run benchmark-76a23ecfcd7a4469b0b0a833ddc1f5e6; không sửa log Astropy attempt 1. | Agent hỗ trợ Vinh/Vũ/Tuấn | MAS REJECT/max_rounds_reached sau 3 vòng, evaluator protected_test_error do candidate sửa requests/utils.py và tests/test_utils.py; 171283 input + 21510 output = 192793 token, 43 call, tổng 631,328s. Container dọn sạch; report hai pilot tại results/mas-benchmark-plan/final-pilot-resource-report.json. Tasks 1–9 đã hiện thực/kiểm thử; tiêu chí pilot MAS có candidate đi hết F2P/P2P vẫn chưa đạt, giữ [~], không đổi policy hoặc bỏ test khỏi patch để ép đạt. Cập nhật sau push này chỉ local.

- 2026-10-22 | Kiểm chứng cuối continuation: make test 595 passed, 33 skipped (Docker opt-in), 153 subtests passed, còn 4 lỗi prompt Analyst/Planner có sẵn; Docker đã chạy riêng. | Agent hỗ trợ Vinh/Vũ/Tuấn | Log results/mas-benchmark-plan/continuation-full-suite.log; review độc lập không có finding quan trọng, cập nhật plan local cả repo chính và worktree. Phần kỹ thuật bổ sung đã kiểm chứng; mốc pilot MAS có candidate được chấm F2P/P2P vẫn chưa đạt vì protected_test_error. Không gọi LLM thêm hoặc push.

- 2026-10-22 | Tiếp tục plan: xử lý candidate rỗng như no-op vẫn chấm độc lập; giữ lỗi agent/evaluator trong record, không biến aborted thành completed; terminal follow chờ evaluation/cleanup; lỗi cleanup không che lỗi startup. Giữ policy protected test và toàn bộ thay đổi Coder, không tự bỏ test khỏi candidate. | Agent hỗ trợ Vinh/Vũ/Tuấn | RED→GREEN các lỗi trên; 57 unit test liên quan qua. Docker matrix 2 task × 2 lần lặp qua với Chain HTTP mock, workspace/memory mới, evaluator riêng, resume không chạy trùng; bộ Docker/executor 23 passed và thêm 2 Docker case Git HEAD/index/Environment mutation qua. Kiểm chứng evaluator Astropy thật với candidate rỗng: unresolved, baseline hợp lệ, 2 F2P fail/13 P2P pass (run d2ad4a1d92224016bcaf1d9fd9862721). Không gọi LLM thêm, không sửa log attempt 1; pilot MAS cũ vẫn protected_test_error. Review độc lập không có finding quan trọng; không push và chưa nghiệm thu A5/bốn level.

- 2026-10-22 | Pilot model thật sau khi người dùng cấu hình key: astropy__astropy-12906, llm-v4/9router, cc/claude-opus-5-5, run benchmark-b9feebf7d8894d9b821cfcb6460f3633. MAS đủ 6 role, 1 vòng, verdict PASS; candidate sửa separable.py và modeling/tests/test_separable.py nên evaluator trả protected_test_error trước khi chạy test. | Agent hỗ trợ Vinh | 83606 input + 8282 output = 91888 token, 17 call, usage_complete=true/cache unknown; tổng 145,281s (setup 13,203s, agent 128,625s, evaluation 0,094s). Container đã dọn, patch/trace/record và resource report giữ nguyên trong results/. Đây là pilot thật bị từ chối candidate, chưa có kết quả resolved/unresolved độc lập, không nâng nghiệm thu A5/bốn level, không retry để cải thiện kết quả và không push.

- [~] Lặp qua các cặp (task, level, lần lặp) — đã có matrix tuần tự (L1|L2|L3) × task × repetition, test 2 task × 2 repetition và test batch 3 level (2026-10-26); chờ adapter L4.
- [ ] Chạy song song có giới hạn
- [x] Hỗ trợ resume, bỏ qua run đã xong khi chạy lại — manifest/fingerprint, lock một writer, phục hồi record-before-manifest và attempt thiếu record; restart sạch run bị ngắt, không resume giữa node.
- [x] Có retry và timeout cho từng run — hard deadline toàn run; retry có opt-in chỉ cho lỗi hạ tầng được phân loại, giữ attempt cũ; không retry lỗi agent/unresolved/rejected. Unit test và Docker timeout/cancel qua.
- [~] Có budget guard dừng run khi vượt trần token — adapter L3 tái dùng guard của Chain; chưa tích hợp các level còn lại.

### Bản ghi kết quả (`benchmark/run_record.py`)

- [~] Định nghĩa schema gồm trạng thái, patch, kết quả test, token, số lần gọi, latency, số message, số vòng, agent được kích hoạt, lỗi — RunRecord/RunUsage/RunTiming strict đã tích hợp runner L3, độc lập verdict agent/evaluator, usage thiếu là unknown, writer atomic/redaction và trace latency từng call; chưa nghiệm thu dữ liệu pilot thật.
- [x] Ghi commit hash, version registry, version prompt và cấu hình model vào mỗi bản ghi — provenance ngay từ setup và snapshot Registry/role/model của adapter L3.
- [x] Mỗi bản ghi lưu version từ `registry/VERSION`, version/hash từng file config đã dùng và snapshot cấu hình hiệu lực không chứa secret; kiểm tra không trộn cấu hình khi resume — gồm benchmark config, source/lock hash và effective model/config; test đổi fingerprint từ chối resume.

### Cấu hình và kiểm tra

- [~] Gom cấu hình LLM (model, temperature, seed nếu có, prompt version) vào `configs/llm.yaml` có version — llm-v3 có cấu hình mặc định và profiles theo role, endpoint/auth/retry chung; prompt version vẫn thuộc Registry. Kiểm chứng HTTP mock/local, đã tích hợp L3 candidate benchmark; còn pilot và các level khác.
- [ ] Viết `dummy level` chỉ gọi LLM một lần để kiểm tra harness trước khi L1 xong
- [~] Viết `cli.py` với lệnh chạy một (task, level) — có benchmark run/batch cho L1 Fixed, L2 Routing và L3 Chain trong Docker (lifecycle chung, `--level L1|L2|L3`) và evaluator candidate độc lập; L4 chưa có adapter, chưa pilot model thật.

Tiêu chí thành công tuần 8

- [ ] Một lệnh duy nhất chạy được một (task, level) và sinh ra một bản ghi kết quả đầy đủ
- [ ] Chạy lại cùng một task trong cùng cấu hình thì môi trường và log nhất quán
- [ ] Cấu hình LLM nằm trong một file config duy nhất và có version

Ghi chú tuần 8

- 2026-10-22 | Sau khi push thay đổi cũ b1667bc theo yêu cầu (loại docs/superpowers), hiện thực RunRecord, Docker executor/workspace, trusted candidate export, trace viewer, adapter Chain và CLI benchmark run/batch tuần tự L3. Resume có fingerprint/lock/attempt recovery; retry phân loại lỗi hạ tầng, giữ nguyên kết quả và tài nguyên attempt cũ. | Agent hỗ trợ Vinh/Vũ/Tuấn | Docker Desktop đã bật: 9 test Docker thật qua, gồm Chain HTTP mock xuất candidate rồi evaluator khác chấm F2P/P2P. Review độc lập phát hiện và đã sửa import shadow, attribution sau test, race cleanup/cancel, retry sai loại lỗi, chờ lock và phục hồi attempt thiếu record; có regression test. Bộ kiểm thử sửa lỗi: 45 passed; toàn suite: 579 passed, 28 skipped, 153 subtests passed, 4 lỗi prompt Analyst/Planner có sẵn (Output format, summary/steps). Xem docs/candidate_benchmark.md và results/mas-benchmark-plan/. Chưa có pilot task/model thật, parallel batch hoặc L1/L2/L4/Engine/A5; không push code triển khai.

- 2026-10-22 | Người dùng yêu cầu lập kế hoạch và thực hiện luồng MAS candidate benchmark. Đã khảo sát và viết đặc tả docs/superpowers/specs/2026-10-22-mas-candidate-benchmark-design.md cùng kế hoạch docs/superpowers/plans/2026-10-22-mas-candidate-benchmark.md: record → Docker executor → workspace/Environment → candidate patch → trace → Chain adapter → một task chấm độc lập → batch/resume → report/pilot. | Agent hỗ trợ Vinh/Vũ/Tuấn | Chờ review tài liệu theo workflow trước khi sửa runtime; không coi plan là tính năng đã hiện thực. Docker daemon hiện không kết nối được (pipe dockerDesktopLinuxEngine không tồn tại), chưa thể kiểm chứng container thật. Giữ nguyên trạng thái các checkbox triển khai; không chạy model, không push.

- 2026-10-22 | Theo yêu cầu người dùng, ghi rõ LLM client đang làm dở và đồng bộ mục chặn đường với A0: đã có client Chat Completions cấu hình endpoint cho MAS/9router; chưa có mock_llm, chưa nghiệm thu benchmark chung hoặc xác nhận bàn giao cho thành viên. Kiểm chứng endpoint ngoài 9router chưa thực hiện, không tự thêm thành tiêu chí bắt buộc. | Agent hỗ trợ Vinh | Chỉ cập nhật trạng thái/ghi chú từ đối chiếu client.py và config.py trong lượt trước, không sửa code hoặc chạy API.

- 2026-10-22 | Agent hỗ trợ Vinh: hoàn tất hai mục A0 về requirements.lock và Makefile. Lock resolve universal từ pyproject/runtime/dev, giữ phiên bản đang dùng; bổ sung setuptools vào dev để cài editable với --no-deps --no-build-isolation. Make test/pilot/freeze và hai shell wrapper đã có chức năng; freeze chỉ lấy YAML config, Registry/VERSION/role/tool/prompt, pyproject và lock, lọc secret bằng bộ lọc hiện có, không đọc .env và từ chối ghi đè. README có hướng dẫn cài/cập nhật lock và chạy lệnh. 5 test mới qua; môi trường sạch results/dependency-check-20261022/venv cài 51 gói theo marker Windows và project, uv pip check qua; resolve lại giữ nguyên 52 pin. Make test toàn dự án: 529 passed, 19 skipped (Docker opt-in), 153 subtests passed, 4 failed do prompt Analyst/Planner (thiếu Output format, summary/steps), 59,21s; log results/dependency-check-20261022/pytest.log. Make freeze tạo snapshot thật, pilot chỉ dry-run; Ruff, sh -n và diff check qua. Review độc lập không có phát hiện cần sửa; chưa chạy Docker/model thật hoặc kiểm chứng cài trên Linux, không nâng trạng thái pilot/đóng băng nhóm/A5 và không push.

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

- [~] `pilot_report.py` tính trung bình và độ lệch của từng metric theo level — đã có mean/stddev token/thời gian, n=1, đếm quality/error và gộp tài nguyên mọi attempt; phân biệt task/repetition, báo usage thiếu. Test qua; chưa đủ metric evaluator/level và dữ liệu pilot thật.
- [ ] Kiểm tra level cao có thật sự tốn token hơn không và metric nào không phân biệt được các level
- [ ] `cost_estimator.py` ước tính tổng token và thời gian full run từ số liệu pilot — phạm vi tài nguyên theo xác nhận 2026-10-22, không quy đổi tiền
- [ ] Đưa ra vài phương án kích thước dataset phù hợp ngân sách token/thời gian
- [ ] `utility.py` viết nháp utility function cân bằng chất lượng và tài nguyên (token/thời gian), kèm quy tắc ưu tiên level dùng ít tài nguyên hơn khi kết quả gần ngang nhau

### Chuẩn bị chạy full

- [ ] Lập lịch chạy và phân công cho tuần 10-11
- [ ] Đóng băng cấu hình bằng `make freeze` sau khi cả nhóm đồng ý

Tiêu chí thành công tuần 9

- [ ] Evaluator cho kết quả đúng khi đối chiếu thủ công trên các mẫu đã kiểm tra tay
- [ ] Có bảng ước tính token, thời gian và kích thước dataset cho lần chạy full, được cả nhóm đồng ý
- [ ] Có bản nháp utility function và kế hoạch tuần 10-11 sẵn sàng

Ghi chú tuần 9

- 2026-10-22 | analysis/pilot_report.py đã đọc RunRecord L3, tính mean/stddev token/thời gian theo task/repetition, cộng mọi attempt, ghi số mẫu thiếu usage và tách lỗi retryable. | Agent hỗ trợ Vinh | Unit test qua cho retry/incomplete/n=1/repetition. Chưa có số liệu pilot model thật, metric LLM judge/quality/conflict hay so sánh bốn level; không nghiệm thu tuần 9/A5.

- 2026-10-22 | Người dùng xác nhận nghiên cứu chỉ so sánh tài nguyên bằng token/thời gian; pricing/quy đổi tiền ngoài phạm vi, giữ token guard và timeout. Đối chiếu mã: benchmark/runner.py, run_record.py, evaluator/{completion,coverage,quality,conflict}.py, analysis/{pilot_report,cost_estimator,utility}.py và Engine/L1/L2/L4 vẫn TODO. Sandbox có batch/resume/retry, baseline/gold/candidate và F2P/P2P; đây chưa là runner (task, level, lần lặp). MAS/L3 Chain có usage theo run/agent/level và latency từng call; chưa có báo cáo tổng hợp thời gian/token chung cho bốn level. Cần chốt thời gian toàn run so với thời gian gọi model/setup để so sánh nhất quán. | Agent hỗ trợ Vinh | Kiểm tra mới `python -m raac.cli sandbox profiles check --config configs/benchmark.yaml`: valid, 105/105 task thuộc 12 repo, không lỗi; không đồng nghĩa vừa chạy/validated lại 105 task. Chưa chạy model/Docker hoặc đánh dấu hoàn tất benchmark/evaluator/A5.

- ........

---

# Nguyễn Minh Tuấn

## Tuần 8 - L3 và đặc tả L4

### Topology và L3 (`collaboration/topologies.py`, `levels/l3_collaboration.py`)

- 2026-10-21 | Theo yêu cầu Tuấn, tắt compact context cho cấu hình MAS mặc định, tăng Coder từ 10 lên 20 lượt LLM mỗi lần kích hoạt (gồm lượt cuối chỉ trả patch); không đổi flow/vòng Chain, model hoặc token_limit 800000. harness-demo-v14/Registry 0.18.5. | Agent hỗ trợ Tuấn/Vũ | Nạp cấu hình thật và kiểm tra giữ nguyên lịch sử 47183 ký tự qua; 113 test + 20 subtest Registry/context/handoff qua, còn 4 lỗi prompt có sẵn. Hai test async runner dừng chưa kết luận; chưa chạy model/pilot, giữ L3 [~].

- 2026-10-21 | Theo yêu cầu Tuấn: Coder memory v2 ưu tiên working_notes gồm conclusions/evidence, ruled_out/reason/evidence và next_steps; update_memory thay trạng thái ngắn <=2000 ký tự trong đúng phiên run/call. Snapshot chỉ lưu action receipt, journal giữ đầy đủ và stored_entry; notes hiện ngay call kế tiếp và retry, bảo vệ khi compact tại nơi lưu; view không compact. Reader/history đọc v1/v2, replay receipt và thay notes; prompt Coder chain-v5/Registry 0.18.4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 355 test + 153 subtest qua (62,51s), còn 4 lỗi prompt Analyst/Planner có sẵn. Sau bổ sung journal receipt/legacy: 26 test + 16 subtest memory/runner/viewer qua (3,48s); 14 Node test memory/usage và JS syntax qua. Có HTTP mock chứng minh notes ở đầu call tiếp theo và Coder retry, ghi chú không mất sau compact, journal đầy đủ, reset writer/scope và đọc legacy; không gọi LLM phụ hoặc chạy model/project thật. Giữ A0/A2/A3/L3 [~], chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-21 | Theo yêu cầu Tuấn: thêm nút Xem thay đổi memory trên web, chọn revision và mở mục thêm/bỏ/rút gọn Trước/Sau; hiển thị số mục/token ước lượng và compact. API history/change chỉ đọc journal đúng identity, memory_saved/compacted và snapshot; phân trang 50 revision, không theo đường dẫn trong log, redaction và báo thiếu dữ liệu lịch sử thay vì đoán. Không đổi policy/writer/flow MAS hoặc compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 32 test Python + 12 subtest memory/web/short-term qua (3,66s); 14 Node test memory/usage qua, JS syntax/diff check qua. Trình duyệt demo kiểm tra mở nút/chọn revision compact/mở nội dung và bố cục; đóng tab/server demo. Đối chiếu run chain-5c8f56dfbf1549d38a1d121c4bcff488: 25/25 revision so sánh chính xác, hash log/memory trước-sau không đổi. Giữ A0/A2/L3 [~], chưa nghiệm thu resume Engine/benchmark/A5, không gọi model/chạy project thật hoặc push.

- 2026-10-21 | Theo yêu cầu Tuấn: Chain v4 chạy Environment đúng một lần sau Planner; Coder → Tester, retry qua Check/Limit → Coder, bỏ environment_target. Rút gọn bản context của results/tests_to_run/test_cases và verification/smoke lồng; giữ count/status/verdict/AC/blocker/hash, bỏ environment_report trùng. Checkpoint giữ outcome gọn thay report vượt budget. Thêm read_tool_result đọc observation_ref theo path/offset/limit trong cùng run, không thực thi lại, ref từ runtime và full report giữ trong state/result/audit; Registry 0.18.3, Environment chain-v4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 343 test + 149 subtest qua (62,35s); còn 4 lỗi prompt Analyst/Planner có sẵn. Kiểm chứng bổ sung 13 test report context/CLI qua (6,79s), gồm ref vào result, đọc traceback/collection theo trang, chặn cross-run/quyền và Environment gọi một lần. Fixture 969 case: report 489269 → 3915 ký tự, verification 170119 → 776, handoff lặp 1318898 → 4818 (số ký tự fixture, không phải token API). Không gọi model/chạy project thật, không push; A2/A3/L3 và Engine/benchmark/A5 vẫn [~]/chưa nghiệm thu.

- 2026-10-21 | Sửa theo log d1dff9: 1 lượt Environment/19 call, executable .venv\Scripts\python.exe bị tìm sai từ repo root khi cwd=xarray; đường dẫn tuyệt đối đã chạy nhưng agent quay lại relative path và lỗi WinError2. Executor nay phân giải executable tương đối theo cwd cho command/test; bare Python ưu tiên venv cwd khi chưa có binding. Prompt Environment chain-v3 yêu cầu kiểm tra venv/căn cứ phiên bản, không lặp đường dẫn lỗi hoặc cài unpinned latest mặc định. Registry 0.18.2/harness-v13. | Agent hỗ trợ Vũ/Vinh/Tuấn | 56 test local executor/Environment/batch/CLI/Registry qua (46,36s), gồm venv nested thật và root venv cạnh tranh, run_tests dùng đúng interpreter, lỗi missing executable ghi đúng absolute cwd path. Không gọi model/cài dependency hoặc sửa project thật, không push; A3/L3 vẫn [~].


- 2026-10-21 | Sửa MAS theo run b13f: diff chuẩn hóa theo Git cwd đã quan sát, no-op giữa hai run không áp lại; report JSON dùng file tạm với cap16MiB riêng thay stdout128KiB; đóng pipe/transport trước loop exit. Environment chain-v2 thực thi pytest và bàn giao smoke_report để role phân biệt dependency/API lỗi với assertion task. 332 test + 152 subtest Registry/MAS qua (còn 4 lỗi prompt cũ), test adapter no-op bổ sung qua; 12 Node test qua. Giữ A3/L3 [~], chưa nghiệm thu benchmark/Engine/A5.


- 2026-10-21 | Environment trước mỗi Coder/Tester trong Chain v3; dependency cài local, configure_environment xác minh import/collection và bàn giao runtime thật. Role timeout null, tool/HTTP không timeout, đồng hồ run tạm dừng; giữ budget/call cap và gate test/review. Bằng chứng: 320 test + 150 subtest Registry/MAS qua, 4 lỗi prompt cũ; lượt cuối 58 test + 31 subtest về Environment/handoff/flow/view qua. Giữ phạm vi harness/L3 [~], chưa nghiệm thu benchmark/Engine/A5.


- [~] Liệt kê tập topology hữu hạn gồm chuỗi, hình sao qua orchestrator, vòng Coder-Reviewer và debate giữa hai agent — hiện có Chain v5: Environment → Analyst → Planner → Coder → Tester → Check/Limit → Reviewer; retry Limit → Coder. Có đặc tả Subtask Chain; còn star, Coder–Reviewer, debate và runtime Subtask Chain.
- [~] Viết node coordinator trả về JSON gồm topology và số vòng tối đa — coordinator xác định chọn Chain theo cấu hình, có JSON topology/version/số vòng; chưa chọn thích ứng bằng LLM.
- [~] Engine dựng graph theo lựa chọn của coordinator rồi mới chạy — CLI MAS dispatch graph sau quyết định coordinator; chưa tích hợp Engine chung.
- [~] Sau mỗi vòng có bước đánh giá "đã đủ chưa" để quyết định lặp tiếp hay dừng — Chain có check/limit và vòng Coder–Tester; chưa đầy đủ L3 nhiều topology.
- [~] Dừng khi test qua, reviewer chấp thuận hoặc chạm số vòng tối đa, và log lý do dừng — Chain có timeout, trần vòng/ngân sách và log lý do dừng; PASS cần test đạt và Reviewer chấp thuận; chưa nghiệm thu L3 trên ba task thật.
- [ ] Chạy L3 trên 3 task mẫu, xem log để chắc topology và số vòng thay đổi theo task

- 2026-10-20 | Theo yêu cầu Tuấn, tách chính sách bàn giao khỏi runner/Layer 2: ChainNodes gọi chain_handoff.prepare_role_request trước runner, quyết định raw chat, góc nhìn Plan và tham chiếu patch; repository_changes do Coder adapter quan sát, kiểm tra kiểu rồi lưu trong ChainState/record. Runner giữ request được giao, hydrator chỉ ghép prompt, context manager chỉ compact biểu diễn/lịch sử. context.enabled không còn thay chính sách bàn giao; state/result giữ Plan và patch đầy đủ. | Agent hỗ trợ Vũ/Tuấn | python -m pytest tests/unit/registry tests/mas/unit tests/mas/integration -q --capture=sys --tb=short: 292 test + 132 subtest qua (27,39s), còn 4 lỗi test prompt Analyst/Planner do khối output format đã bị xóa trong thay đổi có sẵn trước lượt này; không sửa prompt của người dùng. Có regression cho boundary runner/hydrator, graph retry 2 vòng và CLI HTTP mock/local. Không gọi model thật, không chạy currentProject, không push; giữ A2/L3 [~], chưa nghiệm thu Engine/resume/đa topology.

- 2026-10-20 | Theo yêu cầu Tuấn, thêm Coder short-term memory lưu tại results/<run_id>/memory: snapshot JSON và journal JSONL đã lọc secret, ghi tool/arguments/observations (kể cả lỗi/batch), assistant output, feedback, handoff và failure. Chỉ compact snapshot khi append vượt memory.max_tokens, target_tokens nhỏ hơn; view/load không compact hoặc ghi file, không gọi LLM phụ. Scope theo run/task/work/agent/baseline/registry/prompt; reset phiên khi sang run mới, có thể nạp lại đúng identity nhưng chưa resume Engine/workspace. harness-demo-v10 mặc định memory enabled, ngưỡng 6000/4000 token ước lượng; giữ token_limit 500000/timeout 9000 hiện có. Sửa invalid_coder_patch retry: kiểm chứng diff đã áp dụng bằng đảo/áp lại trong RAM trên đường dẫn Coder đã chạm, rồi lấy cumulative patch runtime, log already_applied_diff; diff không khớp vẫn bị chặn. | Agent hỗ trợ Vũ/Tuấn/Vinh | 305 test + 140 subtest qua (25,75s), còn 4 lỗi test prompt Analyst/Planner thiếu khối output format từ trước; không sửa prompt đó. Replay chính xác run chain-2715fc8798c640fc9298a7b2889b62df trên workspace tạm: diff 18453 ký tự khớp file, patch runtime 12662 ký tự hợp lệ, không ghi thay đổi trong bước xác minh. Không gọi model thật/chạy code sinh ra/sửa currentProject hoặc lịch sử run; không push. Ruff chưa có trong Python hiện tại; giữ A2/L3 [~], chưa nghiệm thu full resume/Engine/A5.

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


- 2026-10-21 | Tuấn xác nhận chạy 9router thật trên project xarray để sửa lỗi: helper hỗ trợ Python3.8, runtime theo cwd, expected failures không bị nhầm skip, Environment nhận candidate runtime để reverify thay vì tải lại. Prompt Environment chain-v5/Registry0.18.6. | Agent hỗ trợ Tuấn/Vũ/Vinh | 161 test + 52 subtest qua; chain-41d2d5e56ec24fb3a03de640f6888a7e completed/PASS sau 1 vòng, Tester 966 pass/2 expected failures, Reviewer approve; run 358480 token, cả 3 lượt kiểm chứng ghi 859756 token. Giữ patch xarray từ lượt đầu, không reset/push; chỉ 1 task thật, L3 vẫn [~]. Audit results/runtime-verification-20261021/verification.json.

- 2026-10-21 | Theo yêu cầu Tuấn, sửa chuyển Environment→Coder khi runtime Python vừa cài làm snapshot vượt giới hạn: bỏ qua tên runtime phiên bản/installer, lỗi source quá lớn và tổng snapshot có mã riêng, trace ghi preflight fail. | Agent hỗ trợ Tuấn/Vũ/Vinh | 123 test + 66 subtest qua (60,27s), gồm 19 regression mới và CLI HTTP mock/local. Kiểm tra chỉ đọc project b17bbe lấy 250 file/5036002 byte qua, giữ nguyên .venv39; không gọi model hoặc sửa project/run thật. Giữ L3 [~], chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-20 | Theo yêu cầu Tuấn, chỉnh hợp đồng MAS theo prompt/output format tại commit 39d39e7eb49917b6f381876cdebb6564b596ebc3 trong đúng checkout C:/Users/Tuan/Desktop/RAAC/RAAC/RAAC-Stage2. Analyst dùng requirements/priority/AC dạng chuỗi/open_questions; Planner dùng id số nguyên/description/files/depends_on; Tester dùng tests_to_run/results/verdict; Reviewer dùng decision/comments; Coder trả unified diff. Giữ AC coverage qua ac_catalog và các field mở rộng L3; RoleHandoff tách metadata/evidence/revision của framework khỏi output model. Registry MAS 0.15.0, năm prompt chain-v3; cập nhật adapter/context, kết quả unittest/JUnit từng test, l3_topologies.md và chain_demo.md. 153 unit + 2 CLI integration HTTP mock/local qua; integration dùng cấu hình framework tạm vì configs/mas hiện thiếu trong checkout. Giữ flow Analyst → Planner → Coder ⇄ Tester → Reviewer, check/limit, trần vòng, timeout, budget guard, stale-report/AC gate và chính sách user chat. Không đổi sandbox/benchmark/data hoặc gọi model thật; không nghiệm thu Engine chung, L3 đa topology, checkpoint cũ hoặc ba task thật. Các script tạm ở bản ngoài đã dọn; code/prompt/tài liệu cuối nằm ở bản trong.

- 2026-10-20 | Tài liệu theo yêu cầu Tuấn: bổ sung mục cấu trúc hợp đồng chuyển giao trong `src/raac/collaboration/levels/l3_collaboration/l3_topologies.md`, gồm request, năm Output/alias, bảng bàn giao, context theo role, evidence, revision hash và kiểm tra schema/AC/điều kiện PASS. Đối chiếu trực tiếp chain_state.py, chain_nodes.py, demo_agent.py và context_manager.py; sửa mô tả coordinator cố định Chain và đường dẫn test MAS. Chỉ cập nhật tài liệu; chờ review, giữ trạng thái L3 [~], không nghiệm thu thêm tính năng hoặc chạy model/sandbox.

- 2026-10-20 | Agent hỗ trợ Tuấn theo lựa chọn chỉ đổi cách tổ chức output: `chain_state.py` có AnalystOutput/PlannerOutput/CoderOutput/TesterOutput/ReviewerOutput, bảng JSON_OUTPUT_MODELS và parser chung; adapter và node Chain cùng sử dụng. Giữ toàn bộ field/validator/ChainState, prompt và alias WorkSpec/TechnicalPlan/CodeOutput/TestReport/ReviewReport. Đối chiếu JSON schema trước/sau chỉ khác title lớp; 145 unit test qua (thêm 7 parser + 1 graph test). Hai CLI integration HTTP mock/local qua với cấu hình framework tạm; lệnh integration trực tiếp hiện lỗi vì configs/mas/llm.yaml không còn trong checkout (configs/mas/harness.yaml và l3_collaboration.yaml cũng thiếu), không khôi phục/ghi đè cấu hình ngoài phạm vi. Runner kiểm chứng lưu tại `C:/Users/Tuan/AppData/Local/Temp/raac-chain-output-refactor-20261020/verify_integration.py`. Không gọi model thật hoặc đổi trạng thái nghiệm thu Engine/L3 đa topology/benchmark/L4.

- 2026-10-20 | Agent hỗ trợ theo yêu cầu Tuấn: chuyển riêng MAS/L3 từ checkout bên ngoài, gồm năm role/prompt đầy đủ (Registry 0.14.1), schema/loader MAS, client, context, tools local, Chain và log viewer. `registry/mas/`, `configs/mas/`, `raac.mas_cli`, `tests/mas/` tách khỏi Registry/CLI cũ. 137 unit + 2 CLI integration HTTP mock/local + 6 Node test qua. Không gọi model thật; giữ sandbox, benchmark, data, kết quả cũ và phân công. Chưa nghiệm thu Engine chung, L3 nhiều topology/ba task thật, benchmark, resume toàn harness hoặc L4.

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

- 2026-10-27 | Thực hiện kế hoạch giảm token bằng cơ chế sẵn có: không thêm dependency/RTK/Caveman, không API trả phí, không đổi model/topology/budget/retry/evaluator; giữ baseline tắt compaction và mọi thử nghiệm ở config riêng. Replay chỉ từ request đã ghi, không suy đoán context gốc/cache/tiền hoặc chất lượng; báo cáo mới không ghi đè log. | Agent hỗ trợ Vũ/Vinh/Tuấn | Đã triển khai và kiểm chứng offline, tài liệu `docs/token_reduction.md`; giữ lỗi ngoài phạm vi và các mốc nghiệm thu chung, không commit/push.

- 2026-10-22 | Người dùng làm rõ ý proposal: ladder kế thừa cộng dồn, L3 kế thừa routing L2 rồi thêm topology/số vòng; L4 kế thừa L3 rồi thêm team formation. Sửa bảng và định nghĩa trong level_definitions.md; danh mục vai trò L3 cố định nhưng tập kích hoạt được thay đổi. | Agent hỗ trợ Vũ | Đã hiệu chỉnh diễn giải trước đó; không đổi runtime hoặc trạng thái nghiệm thu, không commit/push.

- 2026-10-22 | Viết level_definitions.md từ proposal: giữ ranh giới L2 routing/L3 topology-vòng/L4 formation; phân biệt định nghĩa nghiên cứu với Chain fixed_default đang có. Quy ước log/benchmark là diễn giải vận hành chờ review, không thay proposal hoặc tự duyệt L4. | Agent hỗ trợ Vũ theo yêu cầu người dùng | Đã đọc PDF/kiểm tra Bảng 1 và nguồn code, liên kết Markdown/diff check qua; mục tài liệu [~] chờ nhóm review. Không đổi runtime, không commit/push.

- 2026-10-22 | MAS benchmark Task 7 đang thực hiện: runner một task và CLI benchmark run đã nối prepare image/Docker workspace → Chain → candidate → sandbox evaluator riêng; record có phase/time/provenance và lỗi setup. | Agent hỗ trợ Vinh | 3 unit runner RED→GREEN, 5 test runner/adapter qua (1,80s); chưa kiểm chứng end-to-end candidate evaluator bằng fixture Docker hoặc model thật, không nghiệm thu.

- 2026-10-22 | MAS benchmark Task 6: adapter run_chain tái sử dụng graph/runner hiện có với Docker workspace, log phase/tool/output, giữ partial usage khi hủy và artifact agent-result.json. Docker mode không đưa interpreter host vào request. | Agent hỗ trợ Vinh/Vũ/Tuấn | 5 test adapter/local CLI qua (4,02s); fixture đủ sáu role qua HTTP mock + Docker tool/test thật PASS (7,89s), candidate chỉ solution.py. Chưa gọi model thật/chấm candidate bằng sandbox evaluator end-to-end; không push.

- 2026-10-22 | MAS benchmark Task 5: mở rộng trace terminal/web cho stdout/stderr Docker, phase/lifecycle và thay đổi workspace/candidate hash; content-off giữ activity/byte, lọc secret. | Agent hỗ trợ Vinh/Tuấn | 3 test trace RED→GREEN, hồi quy viewer chạy cùng; streaming thật đã kiểm chứng Task 2. Chưa có pilot benchmark/model thật; không push.

- 2026-10-22 | MAS benchmark Task 4: export candidate từ baseline host, gồm file mới/xóa/shell; kiểm tra attribution và áp lại diff, chặn binary/executable/empty-file metadata chưa hỗ trợ thay vì bỏ sót. | Agent hỗ trợ Vinh | 5 test export RED→GREEN; chưa nối evaluator/runner, chưa có pilot candidate end-to-end.

- 2026-10-22 | MAS benchmark Task 3: DockerWorkspaceTools dùng file RPC có giới hạn trong container, kế thừa patch/tool logic; Environment xác minh runtime ở container, provisioning offline không cài host. | Agent hỗ trợ Vinh | 25 test qua (22,99s), gồm 7 Docker integration thật, 7 snapshot unit và hồi quy Environment. Chưa nối runner/evaluator, chưa nghiệm thu benchmark; không push.

- 2026-10-22 | MAS benchmark Task 2: DockerRunWorkspace/DockerExecutor có policy không mount/network/credential host, user1000, command argv, output cap, deadline/cancel và test report trong container; tái sử dụng helper test hiện có, runtime quá cũ báo lỗi rõ. | Agent hỗ trợ Vinh | 6 unit + 4 Docker integration thật qua (5,30s); timeout dừng container, stream output xuất hiện trước completion, unittest chạy thật. Chưa nối runner/profile/workspace file; A3 và benchmark giữ [~]/chưa nghiệm thu.

- 2026-10-22 | Triển khai plan MAS candidate benchmark — Task 1: RunRecord/RunUsage/RunTiming, kiểm tra tổng token/cache/thời gian, ghi atomically và lọc secret. | Agent hỗ trợ Vinh | RED: import thiếu API; GREEN: 9 test test_benchmark_record.py qua (0,64s). Chưa có runner/container, giữ bản ghi tổng hợp [~]; không gọi API/Docker hoặc push.

- 2026-10-22 | Người dùng yêu cầu lập kế hoạch và thực hiện luồng MAS candidate benchmark. Đã khảo sát và viết đặc tả docs/superpowers/specs/2026-10-22-mas-candidate-benchmark-design.md cùng kế hoạch docs/superpowers/plans/2026-10-22-mas-candidate-benchmark.md: record → Docker executor → workspace/Environment → candidate patch → trace → Chain adapter → một task chấm độc lập → batch/resume → report/pilot. | Agent hỗ trợ Vinh/Vũ/Tuấn | Chờ review tài liệu theo workflow trước khi sửa runtime; không coi plan là tính năng đã hiện thực. Docker daemon hiện không kết nối được (pipe dockerDesktopLinuxEngine không tồn tại), chưa thể kiểm chứng container thật. Giữ nguyên trạng thái các checkbox triển khai; không chạy model, không push.

- 2026-10-22 | Đính chính ghi chú dataset thiếu test: Task loader không mang test đánh giá, nhưng artifact evaluator đã có test_patch/gold_patch/F2P/P2P riêng và sandbox sử dụng. | Agent hỗ trợ Vinh | Đối chiếu 105/105 task chọn khớp repo/base commit, có test/gold patch và F2P; không chạy test Docker hoặc đưa artifact vào agent. Giữ mục dataset [~] vì chưa tích hợp runner chung; không push.

- 2026-10-22 | Người dùng yêu cầu cập nhật phần LLM client là đang làm dở: giữ A0/client.py ở [~], chuyển mục kết hợp client/mock/bàn giao tại Phần B → Vinh → Tuần 8 từ [ ] sang [~], giữ mock_llm riêng ở [ ]. | Agent hỗ trợ Vinh | Đã đồng bộ ghi chú, giữ ngày cập nhật 2026-10-22; chưa nghiệm thu benchmark hoặc push.

- 2026-10-22 | Người dùng chốt phạm vi so sánh tài nguyên bằng token/thời gian, không quy đổi token sang tiền: ghi pricing ngoài phạm vi; đổi tiêu chí ước tính full run/ngân sách/utility sang token và thời gian, giữ người phụ trách và trạng thái chưa hiện thực. | Agent hỗ trợ Vinh | Đã đối chiếu module TODO, batch sandbox và usage MAS; profile check 105/105 qua. Luồng benchmark chung vẫn thiếu runner/record/Engine và evaluator tích hợp; không nghiệm thu hoặc push.

- 2026-10-22 | Theo phương án người dùng đã duyệt, hiện thực ghim dependency và Makefile tại Phần A → A0 (Vinh); freeze là artifact snapshot để đối chiếu, không thay cho đồng thuận đóng băng cấu hình. | Agent hỗ trợ Vinh | 52 pin resolve được, môi trường sạch/pip check qua, 5 test mới qua; toàn bộ make test còn 4 lỗi hợp đồng prompt Analyst/Planner đã có, ghi rõ tại Ghi chú tuần 8 của Vinh. Make pilot dùng CLI sandbox hiện có, chưa benchmark bốn level; không push.

- 2026-10-22 | Cập nhật checklist theo mã/config hiện tại và nhật ký có sẵn: Chain v5 đưa Environment lên đầu; Registry 0.18.6, harness-demo-v14, context compact tắt, Coder 20 lượt, budget 800000 token. Đồng bộ phần pilot của Vũ và topology của Tuấn, giữ phân công/lịch và lịch sử. | Agent hỗ trợ Vũ/Vinh/Tuấn | Kiểm tra mới: `.venv/Scripts/python.exe -m pytest tests/unit/registry tests/mas/unit tests/mas/integration -q --capture=sys --tb=short`: 426 passed, 153 subtests passed, 4 failed (60,69s); bốn lỗi ở test_registry_roles.py về hợp đồng prompt Analyst/Planner. Python mặc định thiếu pytest nên dùng venv dự án. Không nghiệm thu Engine/resume, nhiều topology, benchmark bốn level hoặc A5; chưa chạy lại Docker/model thật. Một số tài liệu bằng chứng sandbox/benchmark đang bị xóa trong working tree, cần khôi phục hoặc cập nhật tham chiếu trước nghiệm thu; không tự khôi phục trong lượt này.

- 2026-10-21 | Chấm thử MAS chain trên SWE-bench `pytest-dev__pytest-5787` (run `chain-0837…`, model `cc/claude-opus-5-5`; chain kết thúc `rejected`/`max_rounds_reached` sau 3 vòng Coder–Tester, 51 lượt gọi LLM, 286.267 token). Chấm trên bản sao không có `.git`; test chính thức và `reports.py` gốc lấy từ commit upstream `a511b98da` trong lịch sử bản clone (test_patch rất dài, không gõ lại tay). Kết quả: không vá F2P 0/2; **MAS F2P 2/2, P2P 123/123**; patch gốc F2P 2/2, P2P 123/123 (P2P suy ra từ lần chạy patch gốc, chưa đối chiếu từng test với danh sách của instance). Phát hiện: chain từ chối dù patch đúng vì `local_test_runner.py` coi suite `failed` khi có test skip không phải xfail (`skipped == expected_failures`); báo cáo thật là `120 passed, 2 skipped`, exit 0, 0 lỗi (2 test skip do thiếu `decorator` và `jinja2`). Tester báo `verdict=pass` nhưng `status=failed` và Reviewer `approve`, nên chain retry 2 vòng thừa rồi REJECT. Nguyên nhân phụ: `provision_environment` chưa đọc `deps` trong `tox.ini` nên không cài `decorator`/`jinja2`. Tổng 4 task đã chấm theo test chính thức: xarray đạt, astropy đạt, sympy không đạt, pytest đạt (3/4), mẫu quá nhỏ | Claude (Tuấn yêu cầu) | phát hiện hai vấn đề harness, chưa sửa

- 2026-10-21 | Chấm thử MAS chain trên SWE-bench `sympy__sympy-18199` (run `chain-5eac…`, 6 role chạy đủ, `tests_and_review_passed`, 25 lượt gọi LLM, 121.502 token), cùng cách chấm trên bản sao không có `.git`. Kết quả: không vá F2P 0/1; **MAS F2P 0/1 (`test_solve_modular` lỗi), P2P 113/113**; patch gốc F2P 1/1, P2P 113/113 (113 khớp danh sách PASS_TO_PASS của instance). Nguyên nhân: MAS chỉ sửa đúng câu chữ của issue (thêm nghiệm 0 khi `a % p == 0`, 3 dòng ở `residue_ntheory.py`) và giữ `NotImplementedError` cho modulus hợp số, trong khi test chính thức đòi hỗ trợ modulus hợp số (`nthroot_mod` composite, `solveset` modular) — yêu cầu này không có trong problem statement. Tổng 3 task đã chấm: xarray-4075 đạt, astropy-13579 đạt, sympy-18199 không đạt (2/3). Giới hạn: mẫu rất nhỏ, mỗi task một lần chạy | Claude (Tuấn yêu cầu) | có bằng chứng pilot nhỏ

- 2026-10-21 | Chấm thử MAS chain trên SWE-bench `astropy__astropy-13579` (run `chain-8d38…`, model `cc/claude-opus-5-5`, 6 role chạy đủ, `tests_and_review_passed`, 27 lượt gọi LLM, 153.583 token). Chấm trên bản sao không có `.git`: thay test của MAS bằng `test_patch` chính thức, chạy `test_sliced_wcs.py` với 3 cấu hình. Kết quả: không vá F2P 0/1; **MAS F2P 1/1, P2P 40/40**; patch gốc F2P 1/1, P2P 40/40 (40 khớp danh sách PASS_TO_PASS của instance). Tương tự `pydata__xarray-4075` (F2P 2/2, P2P 958/958). Giới hạn: chỉ 2 task, độ khó thấp–trung bình, chỉ chạy file test của instance (chưa chạy toàn bộ `astropy/wcs`), mới một lần chạy mỗi task | Claude (Tuấn yêu cầu) | có bằng chứng pilot nhỏ, chưa đủ để kết luận chất lượng chung

- 2026-10-21 | Run `chain-2e8d…` trên astropy dừng ở Environment với `OutputFormatError` (`invalid_output`) dù môi trường đã dựng xong (Python 3.8.10, `.venv`, astropy build, `configure_environment` thành công): model viết tóm tắt markdown rồi mới đặt object JSON ở cuối, còn `parse_role_output` chỉ nhận đúng một object JSON. Sửa `chain_state.parse_role_output` cho các role JSON (không áp dụng cho Coder/diff): nếu parse chặt thất bại thì nhận object JSON cuối cùng trong văn bản thỏa đúng schema của role (schema vẫn `strict`, `extra=forbid`); chỉ có chữ, hoặc object vi phạm schema, vẫn báo lỗi. Đã thử trên chính câu trả lời của run lỗi: parse ra `status=ready`. Bằng chứng: `tests/mas/unit/test_role_output_recovery.py` 8 passed, `tests/mas` 338 passed. Chưa chạy lại chain thật. Ghi nhận: đây là nới lỏng có chủ đích với quy tắc "đúng một JSON, không bình luận" trong prompt | Claude (Tuấn yêu cầu) | hoàn tất, chờ chạy thật

- 2026-10-21 | Run `chain-1a75…` trên astropy dừng ở Analyst với `workspace_snapshot_limit`: sau khi Environment chạy `pip install -e .`, repo có 2.110 file / 73,4 MiB (34 `.pyd`, 138 `.c` do Cython sinh, `build/`, egg-info) vượt `max_files`/`max_repo_bytes`. Nguyên nhân gốc: snapshot tính cả file sinh ra khi cài đặt, vốn nằm trong `.gitignore`. Sửa `WorkspaceTools._snapshot`: bỏ qua file/thư mục untracked bị `.gitignore` loại (hỏi `git ls-files --others --ignored --exclude-standard --directory`, tìm `.git` sâu tối đa 2 cấp vì checkout thường ở `repo/<tên>`); file đã track dù khớp `.gitignore` vẫn được giữ; không có git hoặc git lỗi thì giữ hành vi cũ. Snapshot astropy sau cài đặt trở lại 1.878 file / 34,0 MiB, ~1,1 giây/lần. Bằng chứng: `tests/mas/unit/test_workspace_gitignore.py` 6 passed, `tests/mas` 330 passed. Chưa chạy lại chain thật trên astropy. Giữ giới hạn đã nâng (64/4 MiB) | Claude (Tuấn yêu cầu) | hoàn tất, chờ chạy thật

- 2026-10-21 | Theo yêu cầu Tuấn: `run-chain` trên astropy báo "Demo could not start" do snapshot workspace vượt giới hạn (`workspace_file_too_large`: file `iers/data/eopc04_IAU2000.62-now` 3,4 MB; tổng 34 MiB). Nâng `WorkspaceTools.max_repo_bytes` 16 → 64 MiB và `max_file_bytes` 2 → 4 MiB (cần thêm vì một file vượt 2 MiB); `max_files` giữ 2.000 (astropy 1.878 file). Đo: snapshot astropy ~1,9 giây/lần so với ~0,11 giây của xarray (4,8 MiB); `list_files`, `current_patch` và `run_tests` đều gọi lại snapshot nên mỗi lần tốn ~1,9 giây; heap Python ~34 MiB (đỉnh 69 MiB). Bằng chứng: `tests/mas` 324 passed. Chưa chạy chain thật trên astropy. Tồn đọng: CLI vẫn che mã lỗi thật bằng thông báo chung (mas_cli.py), chưa tối ưu snapshot (cache theo mtime) | Claude (Tuấn yêu cầu) | hoàn tất phần giới hạn, chờ chạy thật

- 2026-10-21 | Theo yêu cầu Tuấn: web trace viewer thêm đồ thị tiến trình theo topology. `graph.mjs` (layout đường chính + nhánh Limit, cạnh quay lại, trạng thái node suy ra từ log: chưa chạy/đang chạy/hoàn tất/lỗi/bị ngắt, hoạt động hiện tại như tool đang chạy hoặc đang chờ LLM, cạnh đang đi), endpoint `/api/topology` đọc từ `CHAIN`, mục "Tiến trình theo topology" trong `index.html`/`viewer.js`/`viewer.css`: bấm node xem role đang làm gì, số lượt/LLM/tool/token, bàn giao gần nhất, hoạt động gần đây (bấm nhảy tới sự kiện), nút lọc luồng theo role; mặc định tự theo node đang chạy. Bằng chứng: `tests/mas/unit/test_trace_graph.py` 4 passed (logic chạy bằng node), `tests/mas` 324 passed; xem thử trên trình duyệt với log giả lập, ghi thêm sự kiện thì node đổi trạng thái không cần tải lại. Chưa xem trên log của một run thật. Giới hạn: node check/limit/result chỉ suy luận từ các role lân cận vì log không ghi sự kiện riêng cho chúng | Claude (Tuấn yêu cầu) | hoàn tất, chờ xem trên run thật

- 2026-10-21 | Theo yêu cầu Tuấn: Environment kiểm tra yêu cầu dự án so với máy rồi cài phần thiếu, tổng quát theo file đánh dấu, không gọi LLM. Thêm `env_provisioner.py` (detect Python/Node/Maven/Gradle/Go/Rust; Python: python_requires, classifier, tox, requirements, setup.cfg/pyproject, chọn interpreter thật sự chạy được, so sánh gói thiếu/sai phiên bản, kế hoạch venv + pip) và tool `provision_environment` (apply=false chỉ kiểm tra; apply=true chạy kế hoạch, cài runtime user-local qua uv hoặc `py install`). Runtime thiếu mà không có cách cài thì báo blocked kèm gợi ý. Bằng chứng: `tests/mas/unit/test_env_provisioner.py` 17 passed, `tests/mas` + registry 408 passed (4 lỗi prompt analyst/planner có từ trước); chạy kiểm tra trên repo xarray thật cho kết luận đúng (chỉ có Python 3.14, cần 3.8). Chưa chạy apply thật, chưa chạy chain với model thật. Giới hạn: gói không pin có thể kéo phiên bản mới không tương thích (vd numpy 2 trên Python 3.9), Java/Node chỉ kiểm tra công cụ và cài phụ thuộc bằng lệnh chuẩn | Claude (Tuấn yêu cầu) | hoàn tất phần code, chờ chạy thật

- 2026-10-21 | Theo yêu cầu Tuấn: Chain v5 đưa Environment lên đầu (Environment → Analyst → Planner → Coder), để lỗi môi trường dừng run trước khi tốn token Analyst/Planner. Environment nhận requirement gốc, không còn spec/plan nên chọn suite theo requirement và repo; Analyst/Planner nhận environment_report. Sửa topologies.py, chain_nodes.py, chain_handoff.py, prompt environment, test và tài liệu. Bằng chứng: `PYTHONPATH=src python -m pytest tests/mas` 303 passed (chưa chạy với model thật). Tồn đọng: tests/unit/registry/test_registry_roles.py (prompt analyst/planner) và nhóm tests/unit sandbox/benchmark đang fail, chưa xác định liên quan thay đổi này | Claude (Tuấn yêu cầu) | hoàn tất phần code, chờ chạy thật

- 2026-10-21 | Tuấn yêu cầu undo lượt đổi Environment sang người dùng tự cài. Hoàn tác đúng phần runtime/tool/CLI/prompt/test/tài liệu của lượt đó; khôi phục Registry 0.18.6 và Environment chain-v5, giữ các sửa runtime/xfail/snapshot/Coder20 đã có trước. | Agent hỗ trợ Vinh/Vũ/Tuấn | Đã khôi phục; 57 test + 18 subtest Registry/Environment/Chain/CLI qua (45,98s), diff check qua. Xác nhận context compact vẫn tắt, Coder20 và budget800000 giữ nguyên; A3/L3 [~], không chạy model/project thật hoặc push.


- 2026-10-21 | Tuấn yêu cầu dùng token 9router sửa/kiểm chứng và xác nhận gửi source/yêu cầu project New folder (3), cho agent sửa/test repo xarray. Sửa Python3.8 helper, cwd runtime, phân loại expected failures và cung cấp inventory runtime để tránh cài lại khi pip lỗi mạng; Registry 0.18.6. | Agent hỗ trợ Vinh/Vũ/Tuấn | Hoàn tất phạm vi lỗi: 161 test + 52 subtest qua; run thật chain-41d2d5e56ec24fb3a03de640f6888a7e PASS, 966 pass/2 expected failures, Reviewer approve. Tổng 3 lượt kiểm chứng 859756 token ghi log, 2 lượt dừng có audit riêng và không sửa log cũ. Source xarray thay 2 file do Coder ở lượt đầu; lượt PASS không có patch mới. Không reset/push hoặc nghiệm thu L1–L4/benchmark/A5; A3/L3 giữ [~].

- 2026-10-21 | Theo yêu cầu Tuấn sửa run b17bbe: Environment đã test 958 pass/2 xfail nhưng runtime/installer mới bị snapshot nhận làm source, vượt 2 MiB trước khi Coder gọi model. Loại các tên runtime phiên bản/installer; thêm WorkspaceSnapshotError và trace preflight, không tăng trần dung lượng hoặc đổi gate test. | Agent hỗ trợ Vinh/Vũ/Tuấn | Hoàn tất: 123 test + 66 subtest qua (60,27s), gồm 19 regression mới; project thật kiểm tra chỉ đọc snapshot 250 file/5036002 byte qua, giữ runtime. Không chạy lại model hoặc sửa lịch sử run/project thật; không push, A3/L3 vẫn [~].

- 2026-10-21 | Tuấn yêu cầu tắt compact và tăng lượt Coder sau khi phân tích run b14102fd: context.enabled=false, max_llm_calls=20, harness-demo-v14/Registry 0.18.5; giữ memory và ngân sách 800000. | Agent hỗ trợ Vũ/Tuấn | Hoàn tất thay cấu hình, tài liệu và expectation version; kiểm tra cấu hình/history qua, 113 test + 20 subtest qua (5,52s), 4 lỗi prompt Analyst/Planner có sẵn. Hai test async runner dừng chưa kết luận; không gọi model, sửa run cũ hoặc push; chưa nghiệm thu Engine/benchmark/A5.

- 2026-10-20 | Theo yêu cầu Tuấn, log và xem memory Coder: memory_saved ghi entry/metrics, memory_loaded ghi đúng snapshot gửi retry, memory_compacted ghi snapshot sau compact; vẫn ghi audit memory khi tắt trace_content. Web thêm panel Memory Coder chọn work, snapshot hiện tại và journal phân trang 50 bản ghi; API chỉ đọc sidecar trong đúng run, kiểm tra ID/đường dẫn/scope và redaction. Terminal nhận --memory, --details mở snapshot/journal; giữ flow/token/cache và không compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 313 test + 144 subtest qua (50,46s), còn 4 lỗi prompt Analyst/Planner có sẵn; 12 Node test và JS syntax qua, diff check qua. Kiểm tra trình duyệt local bằng demo riêng: snapshot revision/compact, tải journal 50→58, mở observation literal HTML không tạo phần tử ảnh, không tràn ngang hoặc lỗi console. Đã đóng tab/server demo, không gọi model thật/chạy currentProject hoặc sửa run cũ. Giữ A0/A2/L3 [~], không push.

- 2026-10-20 | Theo yêu cầu Tuấn, thêm Coder short-term memory lưu tại results/<run_id>/memory: snapshot JSON và journal JSONL đã lọc secret, ghi tool/arguments/observations (kể cả lỗi/batch), assistant output, feedback, handoff và failure. Chỉ compact snapshot khi append vượt memory.max_tokens, target_tokens nhỏ hơn; view/load không compact hoặc ghi file, không gọi LLM phụ. Scope theo run/task/work/agent/baseline/registry/prompt; reset phiên khi sang run mới, có thể nạp lại đúng identity nhưng chưa resume Engine/workspace. harness-demo-v10 mặc định memory enabled, ngưỡng 6000/4000 token ước lượng; giữ token_limit 500000/timeout 9000 hiện có. Sửa invalid_coder_patch retry: kiểm chứng diff đã áp dụng bằng đảo/áp lại trong RAM trên đường dẫn Coder đã chạm, rồi lấy cumulative patch runtime, log already_applied_diff; diff không khớp vẫn bị chặn. | Agent hỗ trợ Vũ/Tuấn/Vinh | 305 test + 140 subtest qua (25,75s), còn 4 lỗi test prompt Analyst/Planner thiếu khối output format từ trước; không sửa prompt đó. Replay chính xác run chain-2715fc8798c640fc9298a7b2889b62df trên workspace tạm: diff 18453 ký tự khớp file, patch runtime 12662 ký tự hợp lệ, không ghi thay đổi trong bước xác minh. Không gọi model thật/chạy code sinh ra/sửa currentProject hoặc lịch sử run; không push. Ruff chưa có trong Python hiện tại; giữ A2/L3 [~], chưa nghiệm thu full resume/Engine/A5.

- 2026-10-20 | Theo yêu cầu Tuấn, tách chính sách bàn giao khỏi runner/Layer 2: ChainNodes gọi chain_handoff.prepare_role_request trước runner, quyết định raw chat, góc nhìn Plan và tham chiếu patch; repository_changes do Coder adapter quan sát, kiểm tra kiểu rồi lưu trong ChainState/record. Runner giữ request được giao, hydrator chỉ ghép prompt, context manager chỉ compact biểu diễn/lịch sử. context.enabled không còn thay chính sách bàn giao; state/result giữ Plan và patch đầy đủ. | Agent hỗ trợ Vũ/Tuấn | python -m pytest tests/unit/registry tests/mas/unit tests/mas/integration -q --capture=sys --tb=short: 292 test + 132 subtest qua (27,39s), còn 4 lỗi test prompt Analyst/Planner do khối output format đã bị xóa trong thay đổi có sẵn trước lượt này; không sửa prompt của người dùng. Có regression cho boundary runner/hydrator, graph retry 2 vòng và CLI HTTP mock/local. Không gọi model thật, không chạy currentProject, không push; giữ A2/L3 [~], chưa nghiệm thu Engine/resume/đa topology.

- 2026-10-20 | Theo yêu cầu Tuấn, hợp nhất models.py/loader.py gốc cho MAS và chuyển bộ role/prompt hiện dùng về registry/roles/core và registry/prompts. Gỡ registry/mas, mas_models.py/mas_loader.py và năm prompt v1 cũ; registry/tools.yaml khai đủ tool, Registry chung 0.16.0. Giữ strict/call cap/path/hash/snapshot của MAS và bổ sung kiểm tra catalog/ID/group/name/version, nạp extended ở mức khai báo. Public parser Registry dùng cùng hợp đồng Chain thay vì một bộ output model cũ. Test phát hiện prompt Planner chứa Reviewer và hướng dẫn format lỗi thời; đã sửa, giữ runtime guidance, tăng prompt chain-v4. CLI dùng configs/ chuẩn; bổ sung lại harness config local theo cấu hình MAS đã dùng, không đổi LLM/level settings có sẵn. 247 test + 113 subtest qua, gồm 92 Registry, 153 MAS unit và 2 CLI integration HTTP mock/local chạy trực tiếp cấu hình repo. Không gọi model thật, không sửa code sandbox/benchmark, không sửa workspace người dùng; giữ flow/giới hạn/AC-revision gate của Chain, chưa nghiệm thu L4/Engine chung/resume/pilot.

- 2026-10-20 | Theo yêu cầu Tuấn, chỉnh hợp đồng MAS theo prompt/output format tại commit 39d39e7eb49917b6f381876cdebb6564b596ebc3 trong đúng checkout C:/Users/Tuan/Desktop/RAAC/RAAC/RAAC-Stage2. Analyst dùng requirements/priority/AC dạng chuỗi/open_questions; Planner dùng id số nguyên/description/files/depends_on; Tester dùng tests_to_run/results/verdict; Reviewer dùng decision/comments; Coder trả unified diff. Giữ AC coverage qua ac_catalog và các field mở rộng L3; RoleHandoff tách metadata/evidence/revision của framework khỏi output model. Registry MAS 0.15.0, năm prompt chain-v3; cập nhật adapter/context, kết quả unittest/JUnit từng test, l3_topologies.md và chain_demo.md. 153 unit + 2 CLI integration HTTP mock/local qua; integration dùng cấu hình framework tạm vì configs/mas hiện thiếu trong checkout. Giữ flow Analyst → Planner → Coder ⇄ Tester → Reviewer, check/limit, trần vòng, timeout, budget guard, stale-report/AC gate và chính sách user chat. Không đổi sandbox/benchmark/data hoặc gọi model thật; không nghiệm thu Engine chung, L3 đa topology, checkpoint cũ hoặc ba task thật. Các script tạm ở bản ngoài đã dọn; code/prompt/tài liệu cuối nằm ở bản trong.

- 2026-10-20 | Tài liệu theo yêu cầu Tuấn: bổ sung mục cấu trúc hợp đồng chuyển giao trong `src/raac/collaboration/levels/l3_collaboration/l3_topologies.md`, gồm request, năm Output/alias, bảng bàn giao, context theo role, evidence, revision hash và kiểm tra schema/AC/điều kiện PASS. Đối chiếu trực tiếp chain_state.py, chain_nodes.py, demo_agent.py và context_manager.py; sửa mô tả coordinator cố định Chain và đường dẫn test MAS. Chỉ cập nhật tài liệu; chờ review, giữ trạng thái L3 [~], không nghiệm thu thêm tính năng hoặc chạy model/sandbox.

- 2026-10-20 | Agent hỗ trợ Tuấn theo lựa chọn chỉ đổi cách tổ chức output: `chain_state.py` có AnalystOutput/PlannerOutput/CoderOutput/TesterOutput/ReviewerOutput, bảng JSON_OUTPUT_MODELS và parser chung; adapter và node Chain cùng sử dụng. Giữ toàn bộ field/validator/ChainState, prompt và alias WorkSpec/TechnicalPlan/CodeOutput/TestReport/ReviewReport. Đối chiếu JSON schema trước/sau chỉ khác title lớp; 145 unit test qua (thêm 7 parser + 1 graph test). Hai CLI integration HTTP mock/local qua với cấu hình framework tạm; lệnh integration trực tiếp hiện lỗi vì configs/mas/llm.yaml không còn trong checkout (configs/mas/harness.yaml và l3_collaboration.yaml cũng thiếu), không khôi phục/ghi đè cấu hình ngoài phạm vi. Runner kiểm chứng lưu tại `C:/Users/Tuan/AppData/Local/Temp/raac-chain-output-refactor-20261020/verify_integration.py`. Không gọi model thật hoặc đổi trạng thái nghiệm thu Engine/L3 đa topology/benchmark/L4.

- 2026-10-20 | Agent hỗ trợ theo yêu cầu Tuấn: chuyển riêng MAS/L3 từ checkout bên ngoài, gồm năm role/prompt đầy đủ (Registry 0.14.1), schema/loader MAS, client, context, tools local, Chain và log viewer. `registry/mas/`, `configs/mas/`, `raac.mas_cli`, `tests/mas/` tách khỏi Registry/CLI cũ. 137 unit + 2 CLI integration HTTP mock/local + 6 Node test qua. Không gọi model thật; giữ sandbox, benchmark, data, kết quả cũ và phân công. Chưa nghiệm thu Engine chung, L3 nhiều topology/ba task thật, benchmark, resume toàn harness hoặc L4.

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

- 2026-10-17 | Matplotlib5087 pilot: baseline có P2P skip PDF (thiếu Ghostscript) và hai image comparison failure với system FreeType. Đối chiếu setupext tại base: source quy định bundled FreeType; sửa profile dùng system_freetype=false, thêm make/ghostscript. Cần image và validation mới; không sửa ảnh chuẩn hoặc bỏ test. | Agent hỗ trợ Vinh | Chưa nghiệm thu Matplotlib; full batch chưa bắt đầu.

- 2026-10-17 | Tối ưu sandbox theo yêu cầu: giữ checkpoint batch cũ (60 validated/120), thêm preflight, pilot F2P, reuse full có fingerprint/image/hash report, rebuild native bảo thủ, builder persistent và retention30 GiB. 74 tests pass trong lượt chung; thêm1 Docker cache-hit pass và67 unit pass sau parser dung lượng. Requests và Matplotlib full validated; Matplotlib gold119,328→65,875s, pilot13,906s, reuse0,141s; không suy tăng tốc toàn dataset. | Agent hỗ trợ hạ tầng của Vinh | Triển khai đã có bằng chứng; kiểm chứng120 task bằng batch mới còn tiếp tục, chưa nghiệm thu120/120 hoặc A5.

- 2026-10-17 | Retention sandbox thật: xóa16.283.066.986 byte cache OCI cũ và39 image ít dùng, không lỗi; upper bound giảm56,797→29,922 GiB. Giữ Git cache, log/record và hai image đo thử. | Agent hỗ trợ Vinh | Có bằng chứng results/optimization-cache-applied.json; không dùng số liệu upper bound như dung lượng VHDX thu hồi.

- 2026-10-17 | Nghiệm thu framework tối ưu: full80 tests pass trong172,32s, Ruff/format/diff/profile check120 qua; khởi chạy queue issue-optimized-v1 (preflight120 →pilot12 repo →full120). Không tạo lại automation đã xóa; giữ batch cũ và log. | Agent hỗ trợ Vinh | Phần triển khai đã kiểm chứng; validation toàn dataset đang chạy, chưa đánh dấu120/120, LLM/L1–L4 hay A5.

- 2026-10-17 | Theo yêu cầu người dùng, dọn22 file results rỗng/trùng (34,60 MiB): snapshot139 attempt được đối chiếu trùng hoàn toàn manifest và các log điều phối lặp summary/status; giữ mọi log build/test, record, report, checkpoint và log queue đang chạy. Bổ sung quy tắc chỉ push khi được yêu cầu rõ cho lần đó, hủy ủy quyền push cũ. | Agent hỗ trợ Vinh | Hoàn tất kiểm tra file đã xóa và bảo toàn evidence; audit results/cleanup-20261017.json. Không commit hoặc push.

- 2026-10-17 | Theo yêu cầu người dùng, chuyển queue issue-optimized-v1 sang Windows Terminal riêng: cooperative STOP, xác nhận batch lock đã nhả và tiến trình cũ kết thúc; resume cùng fingerprint/manifest. PowerShell PID31940 thuộc WindowsTerminal PID31068, worker PID28732/29192, không thuộc cây tiến trình Codex. | Agent hỗ trợ Vinh | Đã xác nhận queue running và active run mới cho Astropy-7335; giữ mọi attempt/checkpoint. Không commit/push.

- 2026-10-18 | Đọc log issue-optimized-v1: preflight90 pass/30 lỗi, pilot10/12 pass, full đã interrupted. Sửa profile pyparsing/Pint/appdirs/py.test, pin SCM và pytest/Python lịch sử; sửa ID Django/alias upstream, giữ lỗi selector mơ hồ và artifact không chạy được. Framework85 tests pass (73 unit+12 Docker). Django34272 đã full validated (1 F2P,9 P2P); đang kiểm chứng đại diện các nhóm còn lại. | Agent hỗ trợ hạ tầng Vinh | Đang thực hiện; xem docs/environment_repairs.md. Không sửa test/gold/base, không mở mạng test, không commit/push; chưa nghiệm thu toàn120.

- 2026-10-18 | Sửa lỗi môi trường theo log: đại diện Django/Sphinx/Sympy/Pylint/Xarray4682, Astropy14507/12906, sklearn26306/24860/12972/13110/14709/25957 và Matplotlib20665 đã full validated (14 task). Framework86/86 pass trong140,42s gồm12 Docker integration; profile check120/120. Sửa source archive ổn định để cache image không bị đổi do Git pack; khởi động batch mới issue-env-repairs-v1-full cho30task lỗi, Windows Terminal riêng. | Agent hỗ trợ Vinh | Đang kiểm chứng toàn nhóm;5task vướng artifact/protocol vẫn chưa đạt, chưa nghiệm thu120/120/A5. Không commit/push.

- 2026-10-18 | Ưu tiên validation_failed theo yêu cầu: dừng có kiểm soát batch issue-env-repairs-v1-full; audit26record/24task (23task chưa có validated). Sửa profile PYTHONUSERBASE cho pytester subprocess, dependency sparse/numbagg Xarray4049, fixture path Sphinx9218, PYTHONPATH Pylint7231 và leap-second local Astropy13009/13355. | Agent hỗ trợ Vinh | Đang kiểm chứng Docker từng task; không thay F2P/P2P/test/gold hoặc mở mạng; xem docs/validation_failure_repairs.md. Chưa commit/push hoặc nghiệm thu toàn dataset.

- 2026-10-18 | Nhóm validation_failed: 5task pytest5819/6194/7126/7981/10060 đã full validated sau cố định PYTHONUSERBASE đúng profile compatible. Sửa adapter addSubTest, Docker regression baseline fail→fixture patch pass qua; Django33552 đủ80/80 executed/gold80pass nhưng1F2P vẫn pass baseline SQLite nên giữ validation_failed. | Agent hỗ trợ Vinh | Đang kiểm chứng Sphinx/Pylint/Xarray/Astropy; không nới grading hoặc mạng, không commit/push.

- 2026-10-18 | Nhóm validation_failed đã sửa có bằng chứng: thêm Sphinx9218 và Pylint7231 (source/fixture PYTHONPATH), Xarray4049 (sparse/numba/numbagg; gold863pass không skip), tổng8task gồm5pytest. Xarray4354 rerun đủ612selector/goldpass nhưng8P2P baseline fail nên chưa đạt. | Agent hỗ trợ Vinh | Tiếp tục Astropy13009/13355 và môi trường lịch sử Xarray6597; bảo toàn artifact/chính sách, không commit/push.

- 2026-10-18 | Nhóm validation_failed: Astropy13009/13355 đã full validated sau dùng leap-second local; tổng10task vừa sửa, cộng Matplotlib5087 đã đạt trước đó thành11/24task có validated mới nhất. Xarray6597 thử Dask2021.4.1 vẫn cùngP2Pfail/gold265pass; gỡ override thử, giữ record. Tiếp tục Xarray7036 theo môi trường conda-forge củaCIbase, CDMS2/Iris/Pint/NumExpr/Flox đủ thay vì bỏ29skip. | Agent hỗ trợ Vinh | Đang kiểm chứng7036; chưa nghiệm thu toàn bộ, không thay grading/mạng, không commit/push.

- 2026-10-19 | Xarray7036 đã full validated với override conda-core/pip, Micromamba2.6pinSHA: preflight271,078s; full41,5s, baseline12F2Pfail/945P2Ppass, gold957pass/0skip. Tổng11task validation_failed vừa sửa (12/24task nguồn có validated gồm1đãđạttrước); còn12task mạng/baseline/selector chưa đạt. | Agent hỗ trợ Vinh | Hoàn tất sửa nhóm môi trường optional; giữ lịch sử/grade/network, đang kiểm tra cuối. Không nghiệm thu120/120, L1–L4/A5, không commit/push.

- 2026-10-19 | Kiểm tra cuối các sửa validation_failed:86/86tests pass trong106,72s (74unit+12Docker), Ruff/format/diff/profile120check qua, reviewdiff/credentialpatterns không phát hiện. Queue sửa processed, không còn task test chạy nền; kết quả tổng hợp12validated/12validation_failed của24task nguồn. | Agent hỗ trợ Vinh | Bàn giao phần sửa có bằng chứng; còn12task mạng/backend/selector, không nghiệm thu120/120/A5, không commit/push.


- 2026-10-19 | Rà toàn bộ record full mới nhất:92validated/12validation_failed/16 lỗi hoặc ngắt;12validation_failed đều thuộc nhóm đã chẩn đoán, không có trường hợp mới. Bỏ qua5task artifact (Django29834,Pylint1548/4603,pytest7517,Sympy14237); tạo batch issue-remaining-v2-full cho11task môi trường/ngắt còn lại,1800s build+1800s/phase,một task/lượt. Dọn43stdout/stderr rỗng hoặc trùng JSON record,720627byte; giữ phase log/checkpoint, audit tại results/cleanup-20261019.json. Dừng có kiểm soát rồi resume trong Windows Terminal riêng theo yêu cầu (PowerShell1472,parentWindowsTerminal20968); task đầu Astropy14355 đang build, chưa có kết quả mới. | Agent hỗ trợ Vinh | Đang chạy, chưa nghiệm thu thêm task; không commit/push.


- 2026-10-19 | Đính chính phạm vi theo người dùng: gồm cả11task môi trường/ngắt và12validation_failed còn lại; bỏ qua5task artifact đã liệt kê. Batch validation-failed-v2-full đang chạy lại6task baseline/selector;6Requests có bằng chứng networknone giữ riêng. Terminal hàng đợi chờ batch này processed rồi resume issue-remaining-v2-full cho11task; không chạy song song workload. Django33552/Pylint7229/Matplotlib22640 đã chạy lại và vẫn validation_failed, đang đối chiếu artifact/case thay vì đổi nhãn hoặc ép pass. | Agent hỗ trợ Vinh | Đang chạy và chẩn đoán; chưa có task mới validated, không commit/push.


- 2026-10-19 | Chạy/sửa cùng lượt: Astropy14355 và14363 fullvalidated thật (baseline3F2P/732P2P,gold735pass và1F2P/8P2P,gold9pass). Batchvalidationfailedv2 chạy đủ6task vẫn chưađạt; giữ bằng chứng mâu thuẫn baseline/alias. Sửa bug wasxfail reason rỗng: trước đó xfail→skipped/xpass→passed;2regression fail trước sửa. Sau sửa89/89tests qua trong131,50s (76unit+13Docker), Ruff/format/profile120check qua. Dừng remainingv2 trước sửaengine; tạo issue-repairs-v3-full để kiểm tra Matplotlib22640 và9task môi trường còn lại bằng terminal riêng, không đổi fingerprintbatchcũ. | Agent hỗ trợ Vinh | Tiếp tục kiểm chứng/sửa theo log; không đổi nhãn test, không bỏ5taskartifact vào queue, không commit/push.


- 2026-10-19 | Nhóm11task đã thêm9validated thật: Astropy14355/14363/14523/14581,Matplotlib25329,sklearn10284/12650/14059/14893. Manifestbase25844 (version1.3) yêu cầu joblib>=1.1.1/numpydoc>=1.2 nhưng override cũ pin1.0.1/1.1.0; log pip xác nhận tự gỡ joblibpin cũ. Dừng batchv3 có kiểm soát, sửa riêng25844 sang joblib1.2.0/numpydoc1.5.0 và tạo batchv4 cho25844/9287. Profile120check+22unit evaluator qua sau sửa. | Agent hỗ trợ Vinh | Đang kiểm chứng2task cuối, không đổi source/tests/grading/network, không commit/push.


- 2026-10-19 | Hoàn tất chạy/sửa nhóm11 môi trường/ngắt:11/11fullvalidated thật qua các batchv2/v3/v4; sửa wasxfail rỗng và pinjoblib/numpydoc25844 có Docker chứng minh. Toàn120record mới nhất:103validated/12validation_failed/4collection_error/1execution_error;5artifact bỏ qua đúng yêu cầu.6baseline/selector đã rerun vẫn chưađạt,6Requests networknone giữ bằng chứng. Batchv4 processed, không còn task chạy; đã dọn43logtrùng/rỗng720627byte, giữphase logs/checkpoints. | Agent hỗ trợ Vinh | Phần môi trường11task hoàn tất; chưa nghiệm thu120/120 hoặc L1–L4/A5. Không commit/push.


- 2026-10-19 | Theo yêu cầu push trước: đã push a589606 lên origin/Vinh; sau đó triển khai fixture mạng nội bộ cho6Requests. Probe TCP thật timeout .1/chặn external+gateway;3Docker integration mới qua, full framework đang chạy. Profile120check qua; không đổi source/tests/nhãn. | Agent hỗ trợ Vinh | Đang kiểm chứng baseline/gold6task bằng batch mới; phần mạng chưa push, chưa nghiệm thu thêm task hoặc120/120/A5.


- 2026-10-19 | Mạng nội bộ chạy thật: Requests2930 và5367 đã validated, gold85/131pass.4Requests cũ hếtDNSnhưngP2PSession.send chưa dùng CA env; bổ sung publicCA vào PEM tạm của Requests/certifi, auditSHAtrước/sau và chặn candidate sửa bundle. Batchv1 processed trước sửa; chuẩn bịv2 cho4task. Requests1723 có6F2Ppass ngaybase sau phục hồi mạng nên giữ validation_failed, không đổi nhãn. | Agent hỗ trợ Vinh | Đang kiểm traTLS/fullframework; phần mạng chưa push, không nghiệm thu120/120.

- 2026-10-19 | Requests network v2 đã processed; gold1723/1765/1920pass nhưng baseline cóF2Ppass nên giữ validation_failed. Sửa selector giữ thứ tự collection và ánh xạ hostname redirect khácorigin vào sidecarisolated; thêm regressionunit/Docker. | Agent hỗ trợ Vinh | Đang kiểm chứng, không đổi nhãn/test, chưa push phầnmạng hoặc nghiệm thu120/120/A5.

- 2026-10-19 | Hoàn tất fixture mạng nội bộ6Requests và sửa selector giữcollectionorder;104/104test (87unit+17Docker) qua. Batchv3 processed6/6:2930/5367validated;4taskcũP2P/goldpass toàn bộ nhưngF2Pbaselinepass một phần nên chưavalidated. Tổnglatest120:105validated/10validation_failed/4collection_error/1execution_error;5artifact giữnguyên. | Agent hỗ trợ Vinh | Phầnmạng đã kiểm chứng, còn10điềukiệnbaseline/selector; không nghiệm thu120/120/L1–L4/A5, chưapushthêm.

- 2026-10-19 | Bắtđầu đối chứng Requests2316 bằng harnessofficialv3.0.0/SWEinstance2317, rawchỉđọc, imageofficial, gold/baseline tuần tự trongWSLTerminal; bằngchứng riêng vàcleanupthưmụctạm/image saukếtthúc. | Agent hỗ trợ Vinh | Đangchạy, khôngđổi nhãn hoặcstatusvalidatedRAAC, khôngpush.

- 2026-10-19 | Đối chứng Requests2316/SWE2317 bằngofficialv3.0.0 hoàn tất:gold8/8F2P+133/133P2Ppass;baseline7F2Ppass/1fail+133P2Ppass, khớpRAAC. Ngoàinhómchấmconflicting_post_paramsfailcảhai. Lượtnewline-onlylỗiapplyđãrerunmarkerfile; rawkhôngđổi. Image0,95GiB+2thưmụctạmWSLđãdọn, log/provenancegiữriêng. | Agent hỗ trợ Vinh | Chỉ1taskđốichứng, giữvalidation_failedtheotiêuchíhiệntại; khôngnghiệmthu15task/A5, khôngpush.

- 2026-10-19 | Người dùng yêu cầu push phần đã kiểm chứng: review fixture mạng/selector và báo cáo đối chứngofficial;87/87unit kiểmtralại qua,Ruff/diffcheckqua,104/104unit+Dockerđãqua trướcđó. Giữ105validated/15chưađạt, khôngđổitiêuchí hoặc nhãn. | Agent hỗ trợ Vinh | Chuẩnbịcommit/pushorigin/Vinh; khôngđưacache/results/logvàoGit.

- 2026-10-19 | Bắtđầu đối chứngofficial9taskbaseline/selector còn lại bằnghàngđợiWSLTerminal, gold/baselineriêng, checkpoint vàcleanuptừngimage. | Agent hỗ trợ Vinh | Đangchạy;5artifactvẫnbỏqua, chưađổiquytắcgrading/validatedRAAC, khôngpush.

- 2026-10-19 | Hoàn tất đối chứng official 9 task/18 phase, 9 gold resolved, không timeout; đọc output/parser để phân biệt nhãn, alias, xfail và lỗi mạng. Tính cả Requests2316 đã đối chứng đủ 10 validation_failed. Dọn 9 image và clone/venv WSL; raw không đổi. Chi tiết docs/swebench_remaining_audit.md. | Agent hỗ trợ Vinh | Vẫn 105 validated/15 chưa đạt; không đổi tiêu chí/nhãn, không nghiệm thu120/120/L1–L4/A5, không push.

- 2026-10-19 | Theo yêu cầu mới, bắt đầu đối chứng 5 task artifact bằng harness official trong terminal riêng; giữ dữ liệu nguồn và tiêu chí, cleanup từng image và thư mục tạm. | Agent hỗ trợ Vinh | Đang chạy; chưa nghiệm thu thêm task hoặc thay grading, không push.

- 2026-10-19 | Hoàn tất official5artifact/10phase,5goldresolved; xác nhận Pylint baselineimportAPI mới, pytestnested/metadataID, SympybareIDtrùng, Django lỗi report. Sửa in subtest khôngreprtham số:106/106test (88unit+18Docker) qua; taskDjango29834 chạy25/25baseline/gold, hếtcollectionerror, vẫnvalidation_failed vì1F2Ppass. Record69d1e48cfbdb47dfbc87005f65554e41; xem docs/swebench_artifact_audit.md. | Agent hỗ trợ Vinh | Latest105validated/11validation_failed/3collection_error/1execution_error; đãdọn5imageofficial+WSLtemp; khôngđổitiêuchí/nhãn hoặcpush, chưa120/120/L1–L4/A5.

- 2026-10-19 | Theo yêu cầu sửa sau đối chứng: Requests1723 dùng Python2.7, fixture hỗ trợ Python2; Sympy14237 policy versioned chạy mọi case trùng tên trong test-patch modules, giữ outcomes từng node. 86 unit/profile120check qua; framework Docker và preflight/full hai task đang chạy tuần tự trong Windows Terminal. | Agent hỗ trợ Vinh | Đang kiểm chứng; không đổi raw/nhãn/gate strict, không push hoặc nghiệm thu120/120/L1–L4/A5.

- 2026-10-19 | Kiểm chứng sửa sau đối chứng hoàn tất:105frameworkpass (86unit+19Docker),35unitqua sauUnicodefix. Sympy14237 fullfound435/gold506casepass,baseline11fail/495pass; Requests1723Python2 táihiệnUnicodeDecodeError,gold85pass. Hếtlỗimôitrường/selector nhưnggiữvalidation_failedvìrawF2P/P2Pmâu thuẫn. | Agent hỗ trợ Vinh | Latest105validated/12validation_failed/2collection_error/1execution_error; preflight2/2qua,cleanupqua,log/historygiữ,khôngpush/120of120/A5. Bằngchứng docs/evidence_repairs.md.

- 2026-10-19 | Phần A/sandbox và phần B/Vinh/benchmark: theo yêu cầu cố định tập con105task từ120 (đủ12repo), manifest/exclusions versionv1 kèmhashbằngchứng, config riêng105; giữraw/evaluator và lịch sử. Loader/source/evaluatorhash và profilecheck hỗtrợsubset. 93unitliênquan qua, profilecheck105/105 và120/120qua. Dọn37filedư225415byte cóaudit,giữrecord/checkpoint/phaselog. | Agent hỗ trợ Vinh | Hoàn tất selection/dọnfile; chưa nghiệmthuL1–L4/evaluator/A5 hoặc120/120, khôngpush. docs/benchmark105.md.

- 2026-10-19 | PhầnA/sandbox vàPhầnB/Vinh/benchmark: dọnsetup15tasktheoyêucầu;11overridegỡ,default3configchọn105,recipe105khôngđổi. Xóa12imageexclusive,giảmDockerlayer9,45GiB,khônglỗi;giữcachechung/log/record. 94unitqua13,51s,3profilecheck105/105,Ruff/diffqua. | Agent hỗ trợ Vinh | Hoàn tất dọn riêngnhómloại; auditresults/retired15-environments-20261019.json,khôngpushhoặcnghiệmthuL1–L4/A5.

- 2026-10-19 | Người dùng yêu cầu push: reviewdiff phần đối chứng/sửaadapter,manifest105vàretire15;94unitkiểmtralạipass12,90s,Ruffqua,fetchorigin/Vinhkhôngdiverge. | Agent hỗ trợ Vinh | Chuẩn bị commit/push theo yêu cầu lần này; không đưaresults/cache/log vàoGit,khôngnghiệmthu120/120/L1–L4/A5.

- 2026-10-20 | Triển khai model.ref theo yêu cầu Tuấn: profile trong configs/llm.yaml llm-v3, default giữ override CLI/env, named profile cố định model và kế thừa tham số; Registry 0.17.0. Adapter chọn model từng lượt, preflight và provenance theo role, ngân sách token chung. | Agent hỗ trợ Vũ/Vinh | 268 test + 113 subtest Registry/MAS qua (27,04s), git diff --check qua; chưa gọi model thật hoặc nghiệm thu benchmark/A5.

- 2026-10-20 | Theo yêu cầu Tuấn, đặt model mặc định configs/llm.yaml là cx/gpt-5.6-sol cho cả năm role ref default. | Agent hỗ trợ Vũ/Vinh | Đã nạp Registry/cấu hình và kiểm tra resolver đủ 5/5 role chọn đúng model; không gọi API hoặc chạy currentProject. CLI/LLM_MODEL vẫn có thể override theo cơ chế đã triển khai.

- 2026-10-20 | Theo yêu cầu Tuấn, tạo 5 profile analyst/planner/coder/tester/reviewer riêng trong configs/llm.yaml, tất cả cx/gpt-5.6-sol; role.model.ref trỏ đúng profile cùng tên, Registry 0.17.1. | Agent hỗ trợ Vũ/Vinh | Resolver xác nhận 5/5 role trỏ đúng profile và model; 268 test + 113 subtest qua (25,95s), git diff --check qua. Lượt đầu lỗi handle Windows ở test dừng tiến trình; chạy lại cùng bộ với --capture=sys qua. Profile kế thừa temperature/max_tokens chung; fixture test default được tách khỏi profile triển khai. Không gọi API hoặc chạy currentProject.

- 2026-10-20 | Theo yêu cầu Tuấn, thêm cached_tokens từ prompt_tokens_details hoặc trường router chuẩn hóa vào client/log và UsageTracker theo run/agent/level; result.json/web/terminal hiển thị cache, thiếu hoặc sai dữ liệu là null và cache_usage_complete=false. Không cộng cache hai lần, không thay guard/flow/model/profile hoặc suy quota tài khoản. | Agent hỗ trợ Vinh/Tuấn | 105 test Python + 30 subtest qua: 85 test HTTP mock/CLI local/viewer (10,34s) và 20 test profile/guard nhiều model (0,81s); 9 test Node usage, JS syntax và git diff --check qua. Không gọi model thật, không sửa log/run cũ hay currentProject.

- 2026-10-20 | Sửa invalid_coder_patch theo run chain-0c472819137a4db69e12f22482b46167: Coder đã lưu 2 file nhưng final diff khai 81 thay vì 87 dòng. Adapter dùng patch workspace được sinh/áp lại trong RAM và kiểm tra schema; log/web/terminal ghi coder_patch_rebuilt. Chưa lưu file thì diff sai vẫn bị chặn; lỗi kiểm chứng patch vẫn dừng, giữ gate/flow Chain. | Agent hỗ trợ Vũ/Tuấn/Vinh | 98 test + 85 subtest qua (17,28s), gồm CLI HTTP mock/local đi tiếp Tester/Reviewer và negative cases. Replay đúng log trên workspace tạm cho patch 9082 ký tự hợp lệ từ 2 file, không gọi model/chạy code sinh ra/sửa currentProject hoặc lịch sử run.

- 2026-10-20 | Theo yêu cầu Tuấn, lưu mốc hạn mức Codex trước lần chạy Chain tiếp theo tại results/quota-baselines/2026-10-20T013016Z-before.json; có thời điểm, cửa sổ/reset và danh sách run đã tồn tại để đối chiếu sau. | Agent hỗ trợ Tuấn/Vinh | Đã lưu snapshot từ usage tool; chờ người dùng chạy. Chưa kết luận mức tiêu hao riêng run, cache hay account cx trùng account desktop.

- 2026-10-20 | Đối chiếu run chain-2f7d8b6fbc2c49f69c22bc092d2a7712 với mốc trước: lưu after snapshot và comparison tại results/quota-baselines/2026-10-20T013658Z-after-chain-2f7d8b6fbc2c49f69c22bc092d2a7712.json. | Agent hỗ trợ Tuấn/Vinh | Run completed/tests_and_review_passed, 19 call, input 163655 + output 11462 = 175117 token; cache chưa có số liệu. Cùng cửa sổ/reset, hạn mức còn 51→46% (5h), 17→16% (tuần); chênh 5/1 điểm phần trăm là của tài khoản trong khoảng đo, chưa quy toàn bộ cho riêng run hoặc xác nhận account cx trùng desktop. Không gọi model hoặc sửa currentProject.

- 2026-10-21 | Theo yêu cầu Tuấn: thêm role Environment trước mỗi Coder/Tester, chọn/cài dependency trong virtualenv local và configure_environment xác minh runtime/import/collection thật trước handoff; lỗi setup dừng riêng environment_setup_failed. Chain topology v3/Registry 0.18.0, profile environment dùng cx/gpt-5.6-sol; timeout role null, HTTP/tool/batch không giới hạn và tạm dừng đồng hồ run. Giữ budget/call cap, round logic, gate test/review và thay đổi người dùng. | Agent hỗ trợ Vũ/Vinh/Tuấn | Đã kiểm chứng Registry/MAS: 320 test + 150 subtest qua (54,92s), còn 4 lỗi prompt Analyst/Planner có sẵn. Sau thay đổi handoff/view cuối: 58 test + 31 subtest qua (32,87s), gồm 8 test Environment; 12 Node test, JS syntax và diff check qua. Test cài wheel nội bộ offline vào venv tạm, kiểm tra thiếu dependency trước/sau cài, dùng lại Python ở Coder/Tester, collection không thực thi assertion, không tin ready tự khai, clock/cancel/budget và báo cáo view. Không gọi model thật hoặc sửa project người dùng; chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-21 | Sửa theo run chain-b13f70f7aa0b488c968e14bbf8528883: diff Git thiếu prefix repo/xarray và lặp lại thay đổi đã có ở baseline run mới; chuẩn hóa chỉ từ git diff/cwd đã quan sát, kiểm chứng current/baseline trong RAM và no-op không nhận thay đổi cũ. Report 962 case vượt pipe 128KiB được chuyển sang file tạm, giới hạn report 16MiB riêng; full result giữ, handoff lấy mẫu lỗi. Đóng transport/pipe trước loop exit; Environment thêm thực thi pytest để lộ lỗi API NumPy khi collection vẫn pass. Registry 0.18.1/Environment chain-v2/harness-v12. | Agent hỗ trợ Vũ/Vinh/Tuấn | Đã kiểm chứng: replay đúng diff1713 ký tự nhận baseline đã lưu, canonical patch rỗng, 2 file project thật không đổi. Bộ rộng Registry/MAS: 332 test + 152 subtest qua (65,35s), chỉ còn 4 lỗi prompt Analyst/Planner đã có; thêm test adapter no-op qua riêng (1,42s). Bộ hồi quy riêng 72 test + 36 subtest qua (39,79s), gồm report962 case/481 failure không cắt và process Windows không lỗi deallocator. 12 Node test và diff check qua. Không gọi model thật hoặc cài dependency vào project thật; không push/đổi gate PASS/A5.

- 2026-10-21 | Sửa theo log d1dff9: 1 lượt Environment/19 call, executable .venv\Scripts\python.exe bị tìm sai từ repo root khi cwd=xarray; đường dẫn tuyệt đối đã chạy nhưng agent quay lại relative path và lỗi WinError2. Executor nay phân giải executable tương đối theo cwd cho command/test; bare Python ưu tiên venv cwd khi chưa có binding. Prompt Environment chain-v3 yêu cầu kiểm tra venv/căn cứ phiên bản, không lặp đường dẫn lỗi hoặc cài unpinned latest mặc định. Registry 0.18.2/harness-v13. | Agent hỗ trợ Vũ/Vinh/Tuấn | 56 test local executor/Environment/batch/CLI/Registry qua (46,36s), gồm venv nested thật và root venv cạnh tranh, run_tests dùng đúng interpreter, lỗi missing executable ghi đúng absolute cwd path. Không gọi model/cài dependency hoặc sửa project thật, không push; A3/L3 vẫn [~].

- 2026-10-21 | Theo yêu cầu Tuấn: Chain v4 chạy Environment đúng một lần sau Planner; Coder → Tester, retry qua Check/Limit → Coder, bỏ environment_target. Rút gọn bản context của results/tests_to_run/test_cases và verification/smoke lồng; giữ count/status/verdict/AC/blocker/hash, bỏ environment_report trùng. Checkpoint giữ outcome gọn thay report vượt budget. Thêm read_tool_result đọc observation_ref theo path/offset/limit trong cùng run, không thực thi lại, ref từ runtime và full report giữ trong state/result/audit; Registry 0.18.3, Environment chain-v4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 343 test + 149 subtest qua (62,35s); còn 4 lỗi prompt Analyst/Planner có sẵn. Kiểm chứng bổ sung 13 test report context/CLI qua (6,79s), gồm ref vào result, đọc traceback/collection theo trang, chặn cross-run/quyền và Environment gọi một lần. Fixture 969 case: report 489269 → 3915 ký tự, verification 170119 → 776, handoff lặp 1318898 → 4818 (số ký tự fixture, không phải token API). Không gọi model/chạy project thật, không push; A2/A3/L3 và Engine/benchmark/A5 vẫn [~]/chưa nghiệm thu.

- 2026-10-21 | Theo yêu cầu Tuấn: thêm nút Xem thay đổi memory trên web, chọn revision và mở mục thêm/bỏ/rút gọn Trước/Sau; hiển thị số mục/token ước lượng và compact. API history/change chỉ đọc journal đúng identity, memory_saved/compacted và snapshot; phân trang 50 revision, không theo đường dẫn trong log, redaction và báo thiếu dữ liệu lịch sử thay vì đoán. Không đổi policy/writer/flow MAS hoặc compact khi đọc. | Agent hỗ trợ Vinh/Vũ/Tuấn | 32 test Python + 12 subtest memory/web/short-term qua (3,66s); 14 Node test memory/usage qua, JS syntax/diff check qua. Trình duyệt demo kiểm tra mở nút/chọn revision compact/mở nội dung và bố cục; đóng tab/server demo. Đối chiếu run chain-5c8f56dfbf1549d38a1d121c4bcff488: 25/25 revision so sánh chính xác, hash log/memory trước-sau không đổi. Giữ A0/A2/L3 [~], chưa nghiệm thu resume Engine/benchmark/A5, không gọi model/chạy project thật hoặc push.

- 2026-10-21 | Theo yêu cầu Tuấn: Coder memory v2 ưu tiên working_notes gồm conclusions/evidence, ruled_out/reason/evidence và next_steps; update_memory thay trạng thái ngắn <=2000 ký tự trong đúng phiên run/call. Snapshot chỉ lưu action receipt, journal giữ đầy đủ và stored_entry; notes hiện ngay call kế tiếp và retry, bảo vệ khi compact tại nơi lưu; view không compact. Reader/history đọc v1/v2, replay receipt và thay notes; prompt Coder chain-v5/Registry 0.18.4. | Agent hỗ trợ Vũ/Vinh/Tuấn | Bộ Registry/MAS: 355 test + 153 subtest qua (62,51s), còn 4 lỗi prompt Analyst/Planner có sẵn. Sau bổ sung journal receipt/legacy: 26 test + 16 subtest memory/runner/viewer qua (3,48s); 14 Node test memory/usage và JS syntax qua. Có HTTP mock chứng minh notes ở đầu call tiếp theo và Coder retry, ghi chú không mất sau compact, journal đầy đủ, reset writer/scope và đọc legacy; không gọi LLM phụ hoặc chạy model/project thật. Giữ A0/A2/A3/L3 [~], chưa nghiệm thu Engine/benchmark/A5, không push.

- 2026-10-22 | Theo yêu cầu người dùng: chuyển sáu role sang model.ref default (Registry 0.18.7), để LLM_MODEL trong .env ghi đè model chung của benchmark/Chain; giữ khả năng chọn profile riêng. | Agent hỗ trợ Vinh/Tuấn | 46 test model profiles/registry loader/benchmark Chain/CLI integration qua (6,49s), gồm .env ghi đè cả sáu role và giá trị rỗng fallback YAML; bộ rộng 114 pass, 4 lỗi prompt Analyst/Planner đã có và 1 assertion model.ref đã sửa/kiểm chứng lại. Không gọi API thật, không đổi mốc nghiệm thu.

- 2026-10-22 | Sửa show-log --run-id nhận cả chain-* và benchmark-*, giữ chặn path; bổ sung hướng dẫn monitor candidate. | Agent hỗ trợ Vinh/Tuấn | 22 test + 6 subtest trace terminal/web qua (1,52s), gồm benchmark --follow --details/--web và chặn traversal; không gọi LLM hoặc thay run đang chạy. Không đổi mốc nghiệm thu.

- 2026-10-23 | Hiện thực Layer 1 theo thiết kế người dùng duyệt: Reason/Act/Observe bằng LangGraph, checkpoint SQLite JSON và resume cùng workspace/container, approval một operation, giữ usage/baseline/evidence. | Agent hỗ trợ Vũ/Vinh/Tuấn | 638 test +159 subtest qua sau deselect 4 lỗi prompt cũ; 16 Docker fixture qua; reviewer kiểm chứng lại hai lỗi đã sửa. Không đổi phân công/topology/context policy hoặc nghiệm thu A5; không commit/push.

- 2026-10-23 | Theo yêu cầu người dùng: xóa DemoAgentRunner/demo_agent.py, chuyển CLI/benchmark/test sang import trực tiếp AgentRunner Layer 1; giữ Protocol collaboration, không đổi lifecycle. Cập nhật tài liệu hiện hành và ghi amendment cho thiết kế/plan cũ. | Agent hỗ trợ Vũ/Vinh/Tuấn | 391 test +159 subtest qua (62,76s), gồm MAS unit/CLI integration và benchmark runner/trace; import smoke CLI/benchmark qua, không còn import tên cũ trong src/tests. Không gọi model thật, không chạy lại Docker hoặc commit/push.

- 2026-10-23 | Theo yêu cầu người dùng: xóa stub src/raac/llm/mock_llm.py không có caller, bỏ tham chiếu module khỏi README và yêu cầu công việc hiện hành; giữ HTTP fixture/test double để kiểm thử không tốn token, không sửa nhật ký lịch sử. | Agent hỗ trợ Vinh | 46 test model profiles/cached tokens qua (0,90s), không có import module bị xóa; diff check qua. Giữ các chỉnh sửa checklist và xóa tài liệu người dùng đã có; không gọi API thật, không commit/push.

- 2026-10-26 | Hiện thực L1 Fixed (`src/raac/collaboration/levels/l1_fixed.py`, `configs/levels/l1_fixed.yaml`) theo `docs/level_definitions.md` (5 role tuyến tính Analyst → Planner → Coder → Tester → Reviewer, 1 vòng cố định qua Shared Engine) và tạo CLI/Docker runner độc lập `L1-cli.py` ở gốc repo theo yêu cầu người dùng. | Agent hỗ trợ Vũ | 10/10 unit test collaboration qua (`tests/unit/collaboration/test_l1_fixed.py` & `test_engine.py`), kiểm chứng 3 task mẫu với HTTP mock + lưu JSONL; giữ mục chạy 3 task mẫu ở `[~]`. Không commit/push.

- 2026-10-26 | Theo yêu cầu người dùng: thay `L1-cli.py` bằng tích hợp L1 vào `src/raac/benchmark/runner.py` (adapter `engine_runner.py`, bảng `LEVELS`, batch đa level), CLI `--level L1|L3`; đã xóa `L1-cli.py`. | Agent hỗ trợ Vũ | 21 test benchmark L1/runner/batch qua. Phát hiện line ending: `data/tasks/cleaned_benchmark.json` và `data/evaluation/swe_bench_verified.json` trong index là LF nhưng hash manifest 105 tính trên CRLF, nên checkout LF (WSL/Linux) làm mọi lệnh benchmark (cả L3) báo `Frozen selection source hash changed`; chưa sửa, chờ người dùng quyết định (ví dụ `.gitattributes` eol=crlf cho hai file). Không commit/push.

- 2026-10-26 | Hiện thực L2 Adaptive Routing (`src/raac/collaboration/levels/l2_routing.py`, `configs/levels/l2_routing.yaml`, role `Router` trong Registry `0.19.0`, `RouterOutput` trong `agent_contracts.py`) và tích hợp `--level L2` vào benchmark runner chung (`run_l2` trong `engine_runner.py`, `LEVELS` trong `runner.py`, `RunRecord`, `cli.py`). | Agent hỗ trợ Vũ | Đã viết unit test L2 (conditional bypass, giữ Coder+Tester, fallback L1 khi parse/call lỗi, log `agent_skipped` + lý do, 3 task mẫu HTTP mock lưu JSONL) và test benchmark L1/L2/L3; kết quả pytest như trên (chỉ còn 4 lỗi prompt có sẵn). Mục chạy 3 task mẫu giữ `[~]` chờ chạy Docker/model thật. Không commit/push.
