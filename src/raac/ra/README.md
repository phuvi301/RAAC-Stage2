# raac.ra: Requirement Analyzer

Chấm một task (lấy từ `tasks.json`) theo RAAC 4-Axis Rubric v1.0 trước khi đưa vào MAS. Có bốn trục, mỗi trục
từ 1 đến 5: C (complexity), T (testability), S (security), A (architecture_impact).

## Chạy

```bash
python -m raac.ra analyze --workspace W --task T3
```

```bash
python -m raac.ra show --workspace W
```

```bash
python -m raac.ra sync-rubric
```

- `--model`: đổi model cho lần chạy này. Mặc định lấy `model` trong `configs/ra.yaml`; để trống thì dùng model của
  role trong registry.
- `--observe terminal|web|none`: xem agent gọi tool gì, giống bên MAS.

Data mẫu để chạy tay nằm ở `data/ra/` (xem `data/ra/README.md`).

## Đầu vào

Đọc từ `W/.raac/pre_executor/`, đầu ra của pre_executor:

- `tasks.json` (flow project_manager): chọn task theo `--task`. Báo lỗi và liệt kê các task đang có nếu không tìm
  thấy id. Gửi các trường của task (goal, kind, id FR/AC/EC/NFR, id MOD/API/DM/CON, target_paths,
  verification_scope...), bỏ `context_slice`.
- `ba_spec.json` (flow business_analyst): phần liên quan được gom bằng code, không gửi cả file.
  - Từ id AC của task (`FR-n.ACm`) và `fr_ids`, tra ra FR và chỉ giữ các AC của task.
  - Kèm theo: các feature của task, BR, EC, user flow của các feature đó.
  - NFR: chỉ các NFR task trỏ tới (`nfr_ids`) và NFR gắn với feature của task; NFR toàn hệ thống không gửi
    (PM đã chọn vào `nfr_ids` nếu cần).
  - Thực thể: chỉ ENT mà task chạm qua `data_model_ids` (`entity_ids` trong `solution_architecture.json`), không ra
    gì thì qua module (`owns_entities`); không có file json thì gửi hết.
  - Tích hợp ngoài, giả định, câu hỏi mở của các feature đó hoặc toàn hệ thống, cùng ràng buộc.
  - Báo lỗi nếu task trỏ tới AC, FR, feature, EC hoặc NFR không có trong spec.
- `solution_architecture.md` (flow solution_architect): không gửi nội dung. Agent chỉ nhận đường dẫn và outline
  (tiêu đề + khoảng dòng). Tiêu đề module, data model, API bắt đầu bằng id (`MOD-2 · accounts ...`), nên agent dựa vào
  id trong task để tự đọc đúng mục bằng tool harness `read_file` / `read_files` (role chỉ có hai tool này). Prompt
  dặn đọc thêm mục kiến trúc bảo mật khi task đụng xác thực, token, bí mật, dữ liệu cá nhân. Các tiêu đề đã đọc ghi
  vào `architecture_sections_read`.
- Rubric: các mục 2 đến 7 của `registry/prompts/requirement_analyzer/RAAC_Official_4Axis_Rubric_v1.0.md`, nằm ở cuối
  system prompt của role (không gửi trong `artifacts`). System prompt giống hệt nhau giữa các task, nên nếu provider
  có prompt cache thì phần này được cache khi chấm liên tiếp. `artifacts` chỉ còn danh sách khóa factor của từng trục.
  Sửa file rubric xong thì chạy `python -m raac.ra sync-rubric` để chép lại vào prompt; `analyze` báo lỗi nếu hai bên
  lệch nhau.

## Đầu ra

Ghi vào `W/.raac/ra/`:

- `requirement_profile.json`: dạng `{"profiles": {task_id: ...}}`. Mỗi lần chạy chỉ thêm hoặc thay mục của task đó.
  Mỗi mục gồm:
  - `task_id` (code gán, model không ghi để khỏi nhầm với task_id của harness);
  - bốn trục, mỗi trục có `score`, `factors`, `evidence`, `confidence`;
  - `profile` [C, T, S, A], `architecture_sections_read`, `summary`;
  - `checks` (cảnh báo mềm), `rubric_version`, `run_id`;
  - `inputs` (hash của task, spec_context, architecture), `analyzed_at`;
  - `human` (để trống, dành cho người chấm lại).
- `requirement_profile.md`: bảng tổng hợp và chi tiết từng task.

`show` đánh dấu `(cũ)` cho task có đầu vào đã đổi sau lần chấm.

## Luật

Vi phạm luật thì agent được chạy lại kèm danh sách lỗi, tối đa `max_flow_retries` lần:

- Factor phải thuộc đúng trục.
- Nếu có `solution_architecture.md` thì phải đọc ít nhất một mục, và tên mục phải đúng tiêu đề trong outline.

Cảnh báo mềm (chỉ ghi chú, không chặn):

- `testability` thấp trong khi mô tả có từ mơ hồ, hoặc task không có AC (trừ task `bootstrap`/`hardening` hay đã có
  `verification_scope`).
- `security` = 1 trong khi có từ khóa bảo mật.
- `architecture_impact` = 1 trong khi có dấu hiệu đổi kiến trúc.

## Hạ tầng

Dùng lại runtime của pre_executor: AgentRunner, LLMClient, trace, token, và cùng `run_id` với workspace. Role
`requirement_analyzer` (group `ra`) nằm ở `registry/roles/ra/`, prompt ở `registry/prompts/ra/requirement_analyzer.md`.
Mỗi lần `analyze` chạy với `work_id` riêng (`<task>:analyze:<id task>:<mã ngẫu nhiên>`), nên `previous_attempt`
của harness chỉ chứa lượt vừa rồi khi chạy lại vì vi phạm luật, không lẫn lượt chấm task khác hay lần chấm cũ của
chính task đó.

## Chưa làm

- Ánh xạ profile sang L1/L2/L3.
- Chấm nhiều lần để lấy đồng thuận.
- Nhãn người chấm và đo độ đồng thuận (kappa).
- TUI.
- Đoạn system chung của harness (viết cho Coder/Tester) vẫn được gửi kèm, khoảng 3k ký tự.
