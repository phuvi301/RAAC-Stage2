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

- `--model`: đổi model cho lần chạy này. Mặc định lấy `model` trong `configs/ra.yaml`; để trống thì dùng model của
  role trong registry.
- `--observe terminal|web|none`: xem agent gọi tool gì, giống bên MAS.

Data mẫu để chạy tay nằm ở `data/ra/` (xem `data/ra/README.md`).

## Đầu vào

Đọc từ `W/.raac/pre_executor/`, đầu ra của pre_executor:

- `tasks.json`: chọn task theo `--task`. Báo lỗi và liệt kê các task đang có nếu không tìm thấy id.
- `requirement_spec.json`: phần liên quan được gom bằng code, không gửi cả file.
  - Từ id AC của task (`FR-n.ACm`), tra ra FR và chỉ giữ các AC của task.
  - Kèm theo: các feature của task, BR, EC, user flow của các feature đó, toàn bộ NFR, thực thể và ràng buộc.
  - Báo lỗi nếu task trỏ tới AC không có trong spec.
- `architecture.md`: không gửi nội dung. Agent chỉ nhận đường dẫn và outline (tiêu đề + khoảng dòng), rồi tự đọc đúng
  mục bằng tool harness `read_file` / `read_files`. Các tiêu đề đã đọc ghi vào `architecture_sections_read`.
- Rubric: các mục 2 đến 7 của `registry/prompts/requirement_analyzer/RAAC_Official_4Axis_Rubric_v1.0.md`, kèm danh
  sách khóa factor của từng trục.

## Đầu ra

Ghi vào `W/.raac/ra/`:

- `requirement_profile.json`: dạng `{"profiles": {task_id: ...}}`. Mỗi lần chạy chỉ thêm hoặc thay mục của task đó.
  Mỗi mục gồm:
  - bốn trục, mỗi trục có `score`, `factors`, `evidence`, `confidence`;
  - `profile` [C, T, S, A], `architecture_sections_read`, `summary`;
  - `checks` (cảnh báo mềm), `rubric_version`, `run_id`;
  - `inputs` (hash của task, spec_context, architecture), `analyzed_at`;
  - `human` (để trống, dành cho người chấm lại).
- `requirement_profile.md`: bảng tổng hợp và chi tiết từng task.

`show` đánh dấu `(cũ)` cho task có đầu vào đã đổi sau lần chấm.

## Luật

Vi phạm luật thì agent được chạy lại kèm danh sách lỗi, tối đa `max_flow_retries` lần:

- `task_id` phải đúng task được chọn.
- Factor phải thuộc đúng trục.
- Nếu có `architecture.md` thì phải đọc ít nhất một mục, và tên mục phải đúng tiêu đề trong outline.

Cảnh báo mềm (chỉ ghi chú, không chặn):

- `testability` thấp trong khi mô tả có từ mơ hồ hoặc task không có AC.
- `security` = 1 trong khi có từ khóa bảo mật.
- `architecture_impact` = 1 trong khi có dấu hiệu đổi kiến trúc.

## Hạ tầng

Dùng lại runtime của pre_executor: AgentRunner, LLMClient, trace, token, và cùng `run_id` với workspace. Role
`requirement_analyzer` (group `ra`) nằm ở `registry/roles/ra/`, prompt ở `registry/prompts/ra/requirement_analyzer.md`.

## Chưa làm

- Ánh xạ profile sang L1/L2/L3.
- Chấm nhiều lần để lấy đồng thuận.
- Nhãn người chấm và đo độ đồng thuận (kappa).
- TUI.
