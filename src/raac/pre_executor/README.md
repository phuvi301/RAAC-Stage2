# raac.pre_executor — các flow trước khi chạy MAS

> Trạng thái: **đã chạy được `clarify` và `spec` (Business Analyst)** (code + test; clarify và phần Analyst/Profiler cũ đã chạy thật với model) —
> toàn bộ phần phân tích. Các flow thiết kế (tech_select ...) vẫn là khung.
> Role và prompt nằm trong `registry/roles/pre_executor/` và `registry/prompts/pre_executor/`.

## Phạm vi

Từ yêu cầu của người dùng tới **danh sách task đã duyệt** (`tasks.json`) cho bước chạy MAS:

```
yêu cầu ─► clarify ─► spec ─► tech_select ─► architecture ─► environment ─► task_decomposer ─► (tasks.json)
            ▲  │answers     │approve      │approve    │approve        │approve        │approve        │approve
            └──┘ (lặp)
           Clarification  Requirement Spec (Business Analyst)
```

| Phần | Flow | Người làm |
|---|---|---|
| Làm rõ và yêu cầu | clarify, spec | **Tuấn** |
| Thiết kế và chia task | tech_select, architecture, environment, task_decomposer | thành viên khác |
| Chạy task qua MAS / chọn level | (ngoài package này, dùng `tasks.json`) | thành viên khác |


## Cách chạy

Chạy ở gốc RAAC. Cần 9router đang chạy; TUI cần `textual` (`python -m pip install -e ".[tui]"`).
`--workspace` là thư mục dự án đích: thư mục trống = dự án mới, có code = repo có sẵn (agent đọc code bằng tool).

### Chạy cả chuỗi (TUI)

```bash
python -m raac.pre_executor tui --workspace C:/work/shop
```

```bash
python -m raac.pre_executor tui --workspace C:/work/shop --prompt-file C:/work/request.md --model cc/claude-opus-5-5
```

1. **Yêu cầu:** gõ trong TUI nếu workspace chưa có (hoặc truyền `--prompt` / `--prompt-file`). Có thể là một câu
   yêu cầu thô hoặc cả một bản spec: agent đọc hết và chỉ hỏi những gì spec chưa nói rõ.
2. **clarify:** mỗi câu hỏi một cửa sổ. Câu trắc nghiệm: chọn trong danh sách (phương án khuyến nghị chọn sẵn) hoặc
   tự gõ ở ô "Khác"; câu trả lời ngắn: sửa gợi ý rồi Enter. Agent tự hỏi vòng tiếp tới khi đủ (hoặc hết
   `max_clarify_rounds`) rồi tự chốt. Không có góp ý/approve ở bước này.
3. **spec:** Business Analyst viết cả Requirement Spec; màn hình hiện bản nháp; gõ góp ý rồi Enter để agent sửa tiếp
   bấm **Approve**, rồi "Tiếp" sang bước sau.

Ctrl+Q để dừng bất cứ lúc nào; chạy lại đúng lệnh đó sẽ **tiếp tục từ bước đang dở** (không cần `--prompt`).

### Chạy từng flow (dòng lệnh)

| Bước | Lệnh |
|---|---|
| clarify | `python -m raac.pre_executor clarify --workspace W --prompt "..."` — sửa phần `A:` trong `answers.md`, chạy lại `clarify --workspace W` cho vòng tiếp, tới khi in "xong" |
| spec | `python -m raac.pre_executor spec --workspace W` |
| góp ý | `python -m raac.pre_executor revise <flow> --workspace W --note "thêm mã giảm giá"` |
| duyệt | `python -m raac.pre_executor approve <flow> --workspace W` |
| hỏi đáp trong terminal | `python -m raac.pre_executor review <flow> --workspace W` |
| trạng thái | `python -m raac.pre_executor status --workspace W` |

`revise`, `approve`, `review` dùng cho spec (clarify chỉ hỏi-đáp). Mỗi flow chỉ chạy khi flow
trước đã chốt/duyệt. Sau mỗi lần chạy, lệnh in **token đã dùng** (lần này và cộng dồn workspace, như MAS: số do 9router
báo) và `run_id`; xem log agent bằng `python -m raac.mas_cli show-log --run-id <run_id>`. TUI hiện token của từng lần
chạy trong log và tổng trên thanh tiêu đề; `status` hiện token theo từng flow.

### Theo dõi agent và log

- **Một run cho cả workspace:** mọi flow và mọi lần góp ý dùng chung một `run_id` (lưu ở `.raac/pre_executor/run.json`),
  log nối tiếp vào cùng `results/<run_id>/llm.jsonl`: `python -m raac.mas_cli show-log --run-id <run_id>` xem được cả
  chuỗi clarify → spec. Mỗi lần chạy flow có `flow_started` / `flow_completed` trong log. Nếu lần
  trước bị dừng giữa chừng (Ctrl+Q khi agent đang chạy), lần sau tự mở run mới và ghi lý do trong `run.json`.
- **Đang chạy thì thấy agent làm gì** (như MAS):
  - TUI: log hiện từng bước — agent bắt đầu, mỗi lượt gọi model, tóm tắt suy nghĩ công khai của model, tool agent
    dùng (đọc file, tìm kiếm), token từng lượt, và khi đầu ra vi phạm luật phải gửi lại cho agent sửa.
  - Dòng lệnh: `--observe terminal` (mặc định) in sự kiện trực tiếp; `--observe web` mở viewer web của MAS (viewer
    vẫn mở sau khi chạy, Ctrl+C để đóng); `--observe none` để tắt.
- **Mỗi lần góp ý, agent làm gì:** agent nhận bản nháp hiện tại + toàn bộ góp ý của flow đó (cùng lượt trước của chính
  nó), rồi trả về **toàn bộ bản nháp mới** theo schema (không sửa file bằng tool); prompt yêu cầu giữ nguyên mọi phần
  người dùng không yêu cầu đổi. Code ghi đè `requirement_spec.json` và in **danh sách thay đổi
  so với bản trước** (`+` thêm, `~` sửa, `-` bớt theo id: chức năng, tiêu chí thành công, NFR, user flow, quy tắc, yêu
  cầu, trường hợp biên; và các mục tổng quan/ràng buộc/giả định nếu đổi) để kiểm agent có đổi ngoài ý muốn không.

### Tham số chung

- `--workspace` (bắt buộc): thư mục dự án đích.
- `--prompt "..."` hoặc `--prompt-file request.md`: yêu cầu, chỉ cần ở lần đầu (lưu ở `.raac/pre_executor/request.md`).
- `--model`: model cho lần chạy này. Đặt cố định theo từng role ở `models:` trong `configs/pre_executor.yaml` (khóa là id role: `clarifier`, `business_analyst`, `tech_selector`, `system_architect`, `environment_planner`, `task_decomposer`)
  (để trống = model mặc định của `configs/llm.yaml`).
  Không thêm profile vào `llm.yaml` cho các role này: AgentRunner kiểm model của mọi role trong registry, nên đổi
  `ref` của role sẽ làm hỏng các chỗ dùng cấu hình LLM khác (test, benchmark).

### Kết quả và chạy lại

Mọi thứ nằm trong `<workspace>/.raac/pre_executor/` (có `.gitignore` riêng, không lẫn vào git của dự án):

| File | Nội dung |
|---|---|
| `request.md` | yêu cầu gốc |
| `answers.md`, `clarification.json` | câu hỏi/trả lời của clarify (kèm bảng độ phủ từng vòng), bản đã chốt |
| `coverage.json` | độ phủ cuối của 10 nhóm: rõ / đã rõ sau khi hỏi / để bước sau / không áp dụng / còn thiếu |
| `requirement_spec.md/.json` | Requirement Spec: tổng quan, chức năng (lý do ưu tiên, kiểm độc lập), tiêu chí thành công, thực thể chính, NFR, user flow, quy tắc nghiệp vụ, FR + AC Given/When/Then, trường hợp biên — đầu vào cho các flow thiết kế |
| `usage.json` | token đã dùng: từng lần chạy flow và cộng dồn theo flow |
| `run.json` | run_id chung của workspace (và run cũ bị thay nếu lần trước dừng giữa chừng) |
| `approvals.json`, `feedback/` | bước nào đã duyệt (kèm hash), các góp ý |

- Sửa tay file đã duyệt → trạng thái `stale`, phải duyệt lại. Revise một bước → các bước sau mất duyệt và chạy lại.
- Mỗi yêu cầu dùng một workspace riêng; muốn làm lại từ đầu trên cùng thư mục thì xoá `.raac/pre_executor/`.

## Nguồn tham khảo (đã đọc trực tiếp; học cấu trúc, prompt tự viết)

| Flow | Nguồn | Áp dụng |
|---|---|---|
| clarify | GitHub Spec Kit `templates/commands/clarify.md` (`github/spec-kit`, MIT) | 10 nhóm độ phủ, trạng thái clear/partial/missing (+ not_applicable, deferred), tối đa 5 câu tác động lớn nhất, trắc nghiệm 2–5 lựa chọn có khuyến nghị, báo cáo độ phủ cuối (resolved/deferred/outstanding); không hỏi điều có mặc định thông thường |
| spec (Business Analyst) | Spec Kit `templates/spec-template.md`, `templates/commands/specify.md`; ISO/IEC 25010; Gherkin; EARS (Mavin); MetaGPT (PRD của ProductManager) | chức năng như user story có lý do ưu tiên + cách kiểm độc lập; success criteria đo được, không nói công nghệ; key entities; NFR theo ISO 25010; AC Given/When/Then; trường hợp biên; FR câu chỉ thị; tối đa 3 câu hỏi mở; mặc định hợp lý ghi vào giả định |

Đọc thêm: ISO/IEC/IEEE 29148 (đặc điểm của yêu cầu tốt: cần thiết, không mơ hồ, kiểm được), Volere Requirements
Specification Template (Robertson), Karl Wiegers *Software Requirements*, User Story Mapping (Jeff Patton), MoSCoW.

## Ý tưởng

- Mỗi bước là **một flow riêng, giống một role**: một role + một prompt trong registry, chạy bằng harness của MAS
  (`AgentRunner`: tool đọc repo, memory, checkpoint, giới hạn token, log xem bằng `show-log`/TUI).
  Không gộp các flow thành một topology.
- Người dùng **duyệt** đầu ra của một flow thì flow sau mới chạy; **đầu ra đã duyệt của flow trước là đầu vào của
  flow sau**.

| Flow (vai trong sơ đồ) | Role | Đầu vào (đã chốt/duyệt) | Đầu ra | Duyệt | Luật kiểm bằng code |
|---|---|---|---|---|---|
| clarify (Clarification Manager) | `clarifier` | yêu cầu thô hoặc spec (+ câu trả lời vòng trước) | **Clarification** `answers.md` → `clarification.json` + `coverage.json` | tự chốt, chỉ hỏi-đáp | đủ 10 nhóm độ phủ; không complete khi còn missing; chỉ hỏi nhóm partial/missing; trắc nghiệm 2–5 lựa chọn, khuyến nghị nằm trong lựa chọn; không hỏi lại |
| spec (Business Analyst) | `business_analyst` | request + Clarification + coverage | **Requirement Spec** `requirement_spec.json/.md`: tổng quan, chức năng F-n, SC-n, thực thể, NFR-n, UF-n, BR-n, FR-n + AC, EC-n | góp ý + approve | id/tên không trùng; chức năng có lý do ưu tiên + kiểm độc lập; có SC không nhắc công nghệ; ≤ 3 câu hỏi mở; căn cứ là request/Qn có thật; câu giữ khuyến nghị vào giả định; FR/BR/UF/EC trỏ chức năng có thật; mỗi chức năng có FR; AC đặt tên theo FR; chức năng ưu tiên cao có user flow và trường hợp biên |
| tech_select (Technology Selector) | `tech_selector` | Requirement Spec (+ đọc repo) | `tech_stack.json/.md` | góp ý + approve | mỗi lựa chọn có lý do; repo có sẵn không đổi stack vô cớ |
| architecture (System Architect) | `system_architect` | Requirement Spec + tech_stack | `architecture.json/.md` | góp ý + approve | chỉ dùng công nghệ đã chốt; thư mục là đường dẫn tương đối |
| environment | `environment_planner` | tech_stack + architecture + NFR + `machine.json` | `environment.json/.md` | góp ý + approve | runtime "đã có" phải có thật trên máy; "cần cài" phải có lệnh cài; có lệnh test |
| task_decomposer (Task Decomposer) | `task_decomposer` | Requirement Spec + tech_stack + architecture + environment | `tasks.json/.md` | góp ý + approve | id duy nhất, phụ thuộc hợp lệ, không vòng, mọi AC có task, có task dựng khung |

**Requirement Spec** (một role Business Analyst sinh trong một lần) là "hợp đồng" cho các flow thiết kế phía sau.
Schema chi tiết xem `contracts.py`.


## Cấu trúc

```
registry/roles/pre_executor/      clarifier, business_analyst, tech_selector, system_architect,
                             environment_planner, task_decomposer (.yaml)
registry/prompts/pre_executor/    prompt tương ứng (.md)
src/raac/pre_executor/
  cli.py          lệnh: clarify | spec | tech_select | architecture | environment | task_decomposer | review | revise | approve | status | tui
  runtime.py      dựng registry, LLMClient, AgentRunner, ToolOrchestrator, trace (dùng lại MAS)
  contracts.py    đầu vào/đầu ra các flow + đăng ký schema với harness
  artifacts.py    đọc/ghi kết quả; nối đầu ra flow trước thành đầu vào flow sau
  gate.py         duyệt, hash chống sửa sau duyệt, flow sau mất duyệt khi flow trước đổi
  render.py       answers.md: in câu hỏi, đọc câu trả lời người dùng sửa
  config.py       đọc configs/pre_executor.yaml (số lần thử, số vòng hỏi, model theo flow)
  tui.py          giao diện Textual: mỗi câu hỏi một cửa sổ
  machine.py      dò máy cho flow environment (code thuần, chỉ lệnh --version)
  flows/          base.py + clarify.py, spec.py (Tuấn)
                  + tech_select.py, architecture.py, environment.py, task_decomposer.py (thành viên khác)
configs/pre_executor.yaml   max_flow_retries, max_clarify_rounds
```

## Một flow chạy thế nào (`flows/base.py`)

1. `gate`: flow trước đã duyệt và chưa bị sửa sau khi duyệt.
2. `artifacts.inputs_for`: lấy đầu ra đã duyệt của các flow trước làm đầu vào.
3. `runtime.run(role, request)`: chạy role bằng `AgentRunner`.
4. Kiểm schema (`contracts.py`) và luật của flow; sai thì chạy lại kèm danh sách lỗi (tối đa `max_flow_retries`).
5. Ghi bản nháp `json` + `md`; các flow sau mất duyệt.

### Góp ý, chỉnh, rồi mới duyệt

Mỗi flow có vòng duyệt riêng; flow sau chỉ chạy khi flow trước đã được approve:

```
tech_select ─► bản nháp ─► "dùng PostgreSQL thay MySQL" ─► agent chỉnh ─► "bỏ Redis" ─► agent chỉnh ─► approve
                                                                                                         │
architecture ◄───────────────────────────────────────────────────────────────────────────────────────────┘
```

- `revise <flow> --note "..."`: agent sửa bản nháp theo góp ý. Agent nhận **toàn bộ góp ý** của flow đó và bản nháp
  hiện tại (sửa tiếp, không làm lại từ đầu), cùng lượt trước của chính nó qua `previous_attempt` của harness.
- `review <flow>`: chế độ hỏi đáp: in bản nháp, nhập góp ý để agent chỉnh, `a` để approve, `q` để thoát.
- `approve <flow>`: vừa ý thì chốt. Góp ý lưu ở `feedback/<flow>.md`.
- Revise một flow đã duyệt thì flow đó về nháp và các flow sau mất duyệt.

## Sửa ngoài package (đã làm)

1. `src/raac/registry/models.py`: `RoleGroup.PRE_EXECUTOR = "pre_executor"`, loader nạp `roles/pre_executor/`.
2. `registry/VERSION`: 0.21.0.
3. `src/raac/common/agent_contracts.py`: `register_output_model(role, model)` (không ghi đè role lõi);
   `contracts.register()` đăng ký schema role của pre_executor.

## Thứ tự triển khai

| Bước | Làm |
|---|---|
| 1 | Sửa ngoài package, `runtime.py`, `contracts.py`, `artifacts.py`, `gate.py`, `flows/base.py`, `cli.py` (làm chung) |
| 2 | `clarify` + `render.py` + `tui.py` — **xong** |
| 3 | `spec` (role `business_analyst`, gộp Requirement Analyst + Requirement Profiler cũ) — **xong** |


## Cần thống nhất / quyết định

- Định dạng Requirement Spec (`requirement_spec.json`) với người làm tech_select và task_decomposer,
  nhất là id feature `F-n` và id AC `FR-n.ACm`.
- Định dạng `tasks.json` (đầu ra) với người làm phần chạy MAS, nhất là `risk`, `depends_on` và môi trường đã chốt.
- Chính sách duyệt lệnh khi `environment_planner` chạy lệnh `--version`.

## Kiểm thử

Test với `AgentRunner`/LLM giả cho từng flow (schema, luật, chạy lại khi sai), gate (duyệt, hash, mất duyệt),
artifacts (nối đầu vào), machine (lệnh giả). Chỉ coi là xong khi đã chạy với model thật trên một yêu cầu mẫu.
