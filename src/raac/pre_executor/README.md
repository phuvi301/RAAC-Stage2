# raac.pre_executor — các flow trước khi chạy MAS

> Trạng thái: **đã chạy được `clarify`, `requirements`, `profile`** (code + test + chạy thử thật với model) —
> toàn bộ phần phân tích. **`solution_architect` và `project_manager` đã có code + test** (FakeRuntime, AgentRunner
> thật với HTTP mock), **chưa chạy thật với model**. Đặc tả: `docs/pre_executor_sa_pm_plan.md`.
> Role và prompt nằm trong `registry/roles/pre_executor/` và `registry/prompts/pre_executor/`.

## Phạm vi

Từ yêu cầu của người dùng tới **danh sách task đã duyệt** (`tasks.json`) cho bước chạy MAS:

```
yêu cầu ─► clarify ─► requirements ─► profile ─► solution_architect ─► project_manager ─► (tasks.json)
            ▲  │answers     │approve      │approve    │approve               │approve
            └──┘ (lặp)
           Clarification  Requirement   Requirement  Solution              Task DAG T1..Tn
                          Document      Profile      Architecture          + execution_order + context_slice
```

Hai tầng (theo Proposal): các flow trên là tầng **Macro**, chạy một lần cho cả dự án. Tầng **Micro** nằm ngoài
package. Nó duyệt `tasks.json["execution_order"]`. Với mỗi task T_i, `Requirement Analyzer` chấm 4 điểm
(Complexity, Ambiguity, Architecture Impact, Security) vào lúc đó, Controller chọn L1/L2/L3, rồi L1/L2/L3 cài đặt
trên `repo_{i-1}`. PM **không** chấm điểm, không gán level hay risk.

Lưu ý thuật ngữ: `requirement_profile.json` của package này là **hồ sơ đặc tả BA** (FR/AC/UF/BR/EC). Nó khác
"Requirement Profile 4 điểm" của Proposal, vốn do Requirement Analyzer sinh ra sau PM.
SA/PM đọc đặc tả BA qua `Artifacts.read_ba_spec()`: ưu tiên `ba_spec.json` nếu có, mặc định
`requirement_profile.json`.

| Phần | Flow | Người làm |
|---|---|---|
| Làm rõ và yêu cầu | clarify, requirements, profile | **Tuấn** |
| Thiết kế và chia task | solution_architect, project_manager | thành viên khác |
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
3. **requirements → profile → solution_architect → project_manager:** mỗi bước hiện bản nháp; gõ góp ý rồi Enter
   để agent sửa tiếp trên bản nháp, vừa ý bấm **Approve**, rồi "Tiếp" sang bước sau. Duyệt `project_manager` xong
   thì `tasks.json` sẵn sàng cho vòng lặp MAS.

Ctrl+Q để dừng bất cứ lúc nào; chạy lại đúng lệnh đó sẽ **tiếp tục từ bước đang dở** (không cần `--prompt`).

### Chạy từng flow (dòng lệnh)

| Bước | Lệnh |
|---|---|
| clarify | `python -m raac.pre_executor clarify --workspace W --prompt "..."` — sửa phần `A:` trong `answers.md`, chạy lại `clarify --workspace W` cho vòng tiếp, tới khi in "xong" |
| requirements | `python -m raac.pre_executor requirements --workspace W` |
| profile | `python -m raac.pre_executor profile --workspace W` |
| solution_architect | `python -m raac.pre_executor solution_architect --workspace W` |
| project_manager | `python -m raac.pre_executor project_manager --workspace W` |
| góp ý | `python -m raac.pre_executor revise <flow> --workspace W --note "thêm mã giảm giá"` |
| duyệt | `python -m raac.pre_executor approve <flow> --workspace W` |
| hỏi đáp trong terminal | `python -m raac.pre_executor review <flow> --workspace W` |
| trạng thái | `python -m raac.pre_executor status --workspace W` |

`revise`, `approve`, `review` dùng cho mọi flow trừ clarify (clarify chỉ hỏi-đáp). Mỗi flow chỉ chạy khi flow
trước đã chốt/duyệt. Sau mỗi lần chạy, lệnh in **token đã dùng** (lần này và cộng dồn workspace, như MAS: số do 9router
báo) và `run_id`; xem log agent bằng `python -m raac.mas_cli show-log --run-id <run_id>`. TUI hiện token của từng lần
chạy trong log và tổng trên thanh tiêu đề; `status` hiện token theo từng flow.

### Theo dõi agent và log

- **Một run cho cả workspace:** mọi flow và mọi lần góp ý dùng chung một `run_id` (lưu ở `.raac/pre_executor/run.json`),
  log nối tiếp vào cùng `results/<run_id>/llm.jsonl`: `python -m raac.mas_cli show-log --run-id <run_id>` xem được cả
  chuỗi clarify → requirements → profile. Mỗi lần chạy flow có `flow_started` / `flow_completed` trong log. Nếu lần
  trước bị dừng giữa chừng (Ctrl+Q khi agent đang chạy), lần sau tự mở run mới và ghi lý do trong `run.json`.
- **Đang chạy thì thấy agent làm gì** (như MAS):
  - TUI: log hiện từng bước — agent bắt đầu, mỗi lượt gọi model, tóm tắt suy nghĩ công khai của model, tool agent
    dùng (đọc file, tìm kiếm), token từng lượt, và khi đầu ra vi phạm luật phải gửi lại cho agent sửa.
  - Dòng lệnh: `--observe terminal` (mặc định) in sự kiện trực tiếp; `--observe web` mở viewer web của MAS (viewer
    vẫn mở sau khi chạy, Ctrl+C để đóng); `--observe none` để tắt.
- **Mỗi lần góp ý, agent làm gì:** agent nhận bản nháp hiện tại + toàn bộ góp ý của flow đó (cùng lượt trước của chính
  nó), rồi trả về **toàn bộ bản nháp mới** theo schema (không sửa file bằng tool); prompt yêu cầu giữ nguyên mọi phần
  người dùng không yêu cầu đổi. Code ghi đè `requirements.json` / `requirement_profile.json` và in **danh sách thay đổi
  so với bản trước** (`+` thêm, `~` sửa, `-` bớt theo id: chức năng, tiêu chí thành công, NFR, user flow, quy tắc, yêu
  cầu, trường hợp biên; và các mục tổng quan/ràng buộc/giả định nếu đổi) để kiểm agent có đổi ngoài ý muốn không.

### Tham số chung

- `--workspace` (bắt buộc): thư mục dự án đích.
- `--prompt "..."` hoặc `--prompt-file request.md`: yêu cầu, chỉ cần ở lần đầu (lưu ở `.raac/pre_executor/request.md`).
- `--model`: model cho lần chạy này. Đặt cố định từng flow ở `models:` trong `configs/pre_executor.yaml`
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
| `requirements.md/.json` | tổng quan, chức năng cốt lõi (lý do ưu tiên, cách kiểm độc lập), tiêu chí thành công, thực thể chính, NFR |
| `requirement_profile.md/.json` | user flow, quy tắc nghiệp vụ, FR + AC Given/When/Then, trường hợp biên — đầu vào cho các flow thiết kế |
| `usage.json` | token đã dùng: từng lần chạy flow và cộng dồn theo flow |
| `run.json` | run_id chung của workspace (và run cũ bị thay nếu lần trước dừng giữa chừng) |
| `approvals.json`, `feedback/` | bước nào đã duyệt (kèm hash), các góp ý |

- Sửa tay file đã duyệt → trạng thái `stale`, phải duyệt lại. Revise một bước → các bước sau mất duyệt và chạy lại.
- Mỗi yêu cầu dùng một workspace riêng; muốn làm lại từ đầu trên cùng thư mục thì xoá `.raac/pre_executor/`.

## Nguồn tham khảo (đã đọc trực tiếp; học cấu trúc, prompt tự viết)

| Flow | Nguồn | Áp dụng |
|---|---|---|
| clarify | GitHub Spec Kit `templates/commands/clarify.md` (`github/spec-kit`, MIT) | 10 nhóm độ phủ, trạng thái clear/partial/missing (+ not_applicable, deferred), tối đa 5 câu tác động lớn nhất, trắc nghiệm 2–5 lựa chọn có khuyến nghị, báo cáo độ phủ cuối (resolved/deferred/outstanding); không hỏi điều có mặc định thông thường |
| requirements | Spec Kit `templates/spec-template.md`, `templates/commands/specify.md`; ISO/IEC 25010 | chức năng như user story có lý do ưu tiên + cách kiểm độc lập (lát cắt MVP); success criteria đo được, không nói công nghệ; key entities; tối đa 3 câu hỏi mở; mặc định hợp lý ghi vào giả định; NFR theo đặc tính chất lượng ISO/IEC 25010 |
| profile | Spec Kit `spec-template.md` (acceptance scenarios, edge cases, "System MUST"); Gherkin; EARS (Mavin); Kiro | AC dạng Given/When/Then; trường hợp biên cho từng chức năng; FR viết thành câu chỉ thị một hành vi |

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
| requirements (Product Requirement Analyst) | `requirement_analyst` | request + Clarification + coverage | **Tổng quan, chức năng cốt lõi (F-n), tiêu chí thành công (SC-n), thực thể chính, NFR (NFR-n, ISO 25010)** `requirements.json/.md` | góp ý + approve | id/tên không trùng; mỗi chức năng có lý do ưu tiên + cách kiểm độc lập; có SC và SC không nhắc công nghệ; ≤ 3 câu hỏi mở; căn cứ là request/Qn có thật; câu giữ khuyến nghị phải vào giả định; NFR có metric |
| profile (Requirement Profile) | `requirement_profiler` | requirements (+ request, Clarification) | **User flow, quy tắc nghiệp vụ, FR-n (câu chỉ thị) + AC Given/When/Then, trường hợp biên EC-n** `requirement_profile.json/.md` | góp ý + approve | mỗi feature có FR; FR/BR/UF/EC trỏ feature có thật; AC đặt tên theo FR; feature ưu tiên cao có user flow và trường hợp biên |
| solution_architect (Solution Architect) | `solution_architect` | đặc tả BA (`read_ba_spec`) + request + Clarification + `workspace_mode` (+ đọc repo nếu brownfield) | **Tech stack, thư mục, module MOD-n, data model DM-n, hợp đồng API-n, kiến trúc bảo mật, runtime/test contract, ràng buộc toàn cục CON-n, quyết định ADR-n** `solution_architecture.json/.md` | góp ý + approve | id/tên/thư mục không trùng; đường dẫn tương đối POSIX; module chỉ phụ thuộc module có thật, không vòng; mọi F-n có module; tham chiếu F/FR/NFR/MOD có thật; NFR security phải được phủ trong `security_architecture`; `env_vars` không chứa giá trị giống secret |
| project_manager (Project Manager) | `project_manager` | đặc tả BA + solution_architecture + `workspace_mode` | **DAG task T1..Tn** (metadata khách quan + id tham chiếu) → code tạo `execution_order` (Kahn, tái lập) + `context_slice` từng task, chép nguyên `global_tech_stack`/`global_runtime_contract` → `tasks.json/.md` | góp ý + approve | id/tiêu đề không trùng; DAG hợp lệ, có task gốc; greenfield có bootstrap gốc và mọi task phụ thuộc vào nó; đường dẫn tương đối; tham chiếu BA/SA có thật; AC kéo theo FR; milestone hợp lệ; phủ 100% AC, MOD, NFR security, EC của feature high; không gộp dự án lớn vào 1 task; schema cấm `level`/`risk`/4 điểm |

**Requirements** chốt *làm cái gì* (tổng quan, chức năng cốt lõi, NFR); **Requirement Profile** chi tiết hoá *từng
chức năng* (user flow, quy tắc nghiệp vụ, yêu cầu chi tiết, AC) và là "hợp đồng" cho các flow thiết kế phía sau.
SA chốt *dựng bằng gì, chia khối ra sao, hợp đồng toàn cục nào*; PM chốt *làm theo thứ tự nào, mỗi task cần biết
gì*. Schema chi tiết xem `contracts.py`.


## Cấu trúc

```
registry/roles/pre_executor/      clarifier, requirement_analyst, requirement_profiler, solution_architect,
                             project_manager (.yaml)
registry/prompts/pre_executor/    prompt tương ứng (.md)
src/raac/pre_executor/
  cli.py          lệnh: clarify | requirements | profile | solution_architect | project_manager | review | revise | approve | status | tui
  runtime.py      dựng registry, LLMClient, AgentRunner, ToolOrchestrator, trace (dùng lại MAS)
  contracts.py    đầu vào/đầu ra các flow + đăng ký schema với harness
  artifacts.py    đọc/ghi kết quả; nối đầu ra flow trước thành đầu vào flow sau
  gate.py         duyệt, hash chống sửa sau duyệt, flow sau mất duyệt khi flow trước đổi
  render.py       answers.md (đọc/ghi câu trả lời) và bản .md để đọc của từng flow
  config.py       đọc configs/pre_executor.yaml (số lần thử, số vòng hỏi, model theo flow)
  tui.py          giao diện Textual: mỗi câu hỏi một cửa sổ, bản nháp + góp ý + Approve cho 4 flow sau
  flows/          base.py + clarify.py, requirements.py, profile.py (Tuấn)
                  + solution_architect.py, project_manager.py, _checks.py (luật dùng chung: path, vòng, workspace_mode)
configs/pre_executor.yaml   max_flow_retries, max_clarify_rounds, models theo flow
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
solution_architect ─► bản nháp ─► "dùng PostgreSQL thay SQLite" ─► agent chỉnh ─► "bỏ Redis" ─► agent chỉnh ─► approve
                                                                                                                │
project_manager ◄───────────────────────────────────────────────────────────────────────────────────────────────┘
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
| 3 | `requirements` — **xong** |
| 4 | `profile` (role `requirement_profiler`: user flow, quy tắc nghiệp vụ, FR + AC) — **xong** |
| 5 | `solution_architect` + `project_manager` (thay 4 stub tech_select/architecture/environment/task_decomposer) — **code + test xong, chưa chạy thật** |


## Cần thống nhất / quyết định

- Nếu phần BA đổi tên đầu ra thành `ba_spec.json`, chỉ cần cập nhật `FLOWS`/`APPROVED_FILE`; SA/PM đã đọc qua
  `read_ba_spec()`.
- `tasks.json` (`schema_version: tasks-v2`) với người làm vòng lặp MAS: chỉ đọc khi `project_manager` là
  `approved`, duyệt theo `execution_order`. `context_slice` (ràng buộc toàn cục, hợp đồng API, data model) và
  `global_runtime_contract` là ràng buộc chỉ-đọc, và `test_command` là regression gate sau mỗi task.
- `context_slice.security_architecture` chỉ có khi task tham chiếu NFR security hoặc ràng buộc CON loại `security`.
  Đặc tả còn nhắc "chạm module auth", nhưng SA không có liên kết xác định giữa module và auth nên chưa dùng tiêu chí
  này.
- Dò và cài môi trường thật trên máy (flow `environment` cũ đã gỡ) giờ là việc của tầng thực thi MAS.

## Kiểm thử

Test với `AgentRunner`/LLM giả cho từng flow (schema, luật, chạy lại khi sai), gate (duyệt, hash, mất duyệt),
artifacts (nối đầu vào), DAG/phủ/hydration của PM. Chỉ coi là xong khi đã chạy với model thật trên một yêu cầu mẫu.
