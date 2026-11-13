# software-flow: các bước phát triển phần mềm trước MAS

```text
yêu cầu ─► [1] làm rõ ─► [2] spec FR+NFR ─► [3] kiến trúc ─► [4] môi trường ─► [5] kế hoạch task (DAG) ─► MAS
             ▲   │ answers.md   │ duyệt          │ duyệt        │ dò máy + duyệt     │
             └───┘ (lặp được)   ▼                ▼              ▼                    ▼
                             spec.md      architecture.md  environment.md      tasks.md → export → run_tasks.ps1
```

Mỗi bước gọi model một lần, kiểm tra kết quả bằng schema và luật bằng code (ví dụ NFR phải đo được, DAG không có
chu trình, mọi AC phải có task phụ trách). Kết quả sai thì gửi lại cho model kèm danh sách lỗi, tối đa 3 lần.

## Độc lập với MAS

- Thư mục này **không sửa gì bên ngoài**. Nó chỉ **dùng lại** (đọc) từ RAAC: `.env`, `configs/llm.yaml` và
  `raac.llm.client.LLMClient` (gọi 9router, đếm token, che key, ghi log). Toàn bộ chỗ dùng nằm ở `swflow/llm_raac.py`.
- Các bước chỉ phụ thuộc giao diện `JsonModel` (`swflow/ports.py`), nên test chạy bằng model giả, không tốn token.
- Không import L1/L2/L3. Kết quả cuối (`tasks.json`) là đầu vào cho MAS ở bước tích hợp sau.

## Cài đặt

Cần `raac` đã cài trong Python đang dùng (ở gốc RAAC: `python -m pip install -e .`) và 9router đang chạy.
Chạy mọi lệnh **trong thư mục `software-flow`**.

## Dùng

```powershell
Set-Location C:\Users\Tuan\Desktop\RAAC\RAAC\RAAC-Stage2\software-flow
```

Tạo project từ một yêu cầu (hoặc `--request-file yeucau.md`):
```powershell
python -m swflow new todo --request "Xây ứng dụng web quản lý công việc cho nhóm 5 người"
```

Hoặc từ một project kiểu MAS (đọc `requirement/user_chat.txt`, và lấy `repo/<tên>` làm codebase có sẵn, chỉ đọc):
```powershell
python -m swflow new fix-django --from-project ..\currentProject
```

Các bước:
```powershell
python -m swflow clarify todo
```
Mở `runs/todo/answers.md`, sửa phần sau `A:` (đã điền sẵn câu trả lời mặc định). Câu nào để nguyên được ghi là giả
định. Muốn hỏi thêm vòng nữa dựa trên câu trả lời thì chạy lại `clarify`.

```powershell
python -m swflow spec todo
python -m swflow approve todo spec
python -m swflow architect todo
python -m swflow approve todo architecture
python -m swflow env todo
python -m swflow setup todo
python -m swflow approve todo environment
python -m swflow plan todo
python -m swflow status todo
```

Chạy một mạch với câu trả lời mặc định, không dừng duyệt:
```powershell
python -m swflow auto todo
```

Thêm `--model <id>` (đặt trước tên lệnh) để đổi model, ví dụ `python -m swflow --model cc/claude-opus-5-5 spec todo`.

## Bước môi trường

`env` dò máy trước (code, không dùng model): các bản Python (kể cả mục đăng ký hỏng, ghi `exists: false`), Node,
npm, git, Java, Go, .NET, Docker…, và các trình cài có sẵn (winget, py…). Kết quả lưu ở `machine.json`. Model sau đó
chọn môi trường, **ưu tiên bản đã có trên máy** nếu tương thích với kiến trúc. Chỉ khi không có bản phù hợp mới
ghi `source: install` kèm lệnh cài. Code kiểm tra: runtime ghi "đã có" phải thật sự có trên máy đúng phiên bản,
runtime cần cài phải có lệnh cài. Kết quả: `environment.json`, `environment.md`.

`setup` in các lệnh cài cho runtime còn thiếu. Chỉ khi chạy `setup <tên> --apply` mới thật sự cài; sau đó chạy lại
`env <tên> --force` để ghi lại đường dẫn mới.

Khi `export`, môi trường đã chốt được đưa vào mỗi `T*.user_chat.txt` (mục "Môi trường đã chốt"): runtime, phiên bản,
đường dẫn, cách cài thư viện, lệnh test, lệnh kiểm tra, kèm dặn Environment của MAS chỉ xác nhận các runtime đó, không
dựng thử project ngoài repo, không tải thêm công cụ.

## Chạy MAS cho từng task

Sau khi có `tasks.md` hợp lệ, xuất mỗi task thành một yêu cầu cho MAS cùng một script chạy theo thứ tự phụ thuộc:
```powershell
python -m swflow export ban-ao-quan --mas-project C:\Users\Tuan\Desktop\RAAC\RAAC\RAAC-Stage2\shopProject --repo-name shop
```

Lệnh tạo `runs/ban-ao-quan/mas/`:
- `T1.user_chat.txt`, `T2.user_chat.txt`…: mỗi file là yêu cầu của một task, gồm tóm tắt sản phẩm, công nghệ đã chốt,
  việc cần làm, file dự kiến, điều kiện trước, **nội dung đầy đủ các AC** của task, yêu cầu phi chức năng liên quan,
  task trước đã làm gì, và việc nào để cho task sau.
- `run_tasks.ps1`: tạo `shopProject\repo\shop` (git init, `.gitignore` cho `.venv`) nếu chưa có, rồi với từng task
  theo thứ tự: chép yêu cầu vào `shopProject\requirement\user_chat.txt`, chạy
  `python -m raac.mas_cli run --level L3 --project-dir shopProject --observe none`, nếu PASS thì commit
  `"T1: ..."` để task sau bắt đầu từ code đã có, nếu không PASS thì dừng.

Chạy (nên thử một task trước):
```powershell
powershell -ExecutionPolicy Bypass -File runs\ban-ao-quan\mas\run_tasks.ps1 -Only T1
```
```powershell
powershell -ExecutionPolicy Bypass -File runs\ban-ao-quan\mas\run_tasks.ps1 -From T2
```
Bỏ `-Only`/`-From` để chạy hết. Log từng run vẫn nằm ở `results\chain-*` của RAAC; xem bằng
`python -m raac.mas_cli show-log --run-id <run_id> --web` ở gốc RAAC.

Thêm `--topology star` (hoặc `chain_review_loop`) cho `export` để mọi task chạy topology đó.

## Kết quả (`runs/<project>/`)

| File | Nội dung |
|---|---|
| `request.md` | yêu cầu gốc |
| `clarify.json`, `answers.md` | câu hỏi, lý do, câu trả lời (người dùng hoặc mặc định) |
| `spec.json`, `spec.md` | `FR-n` / `NFR-n`, mỗi AC có mã `FR-1.AC2`; NFR có `metric` + `target` |
| `architecture.json`, `architecture.md` | `ADR-n`: lựa chọn, lý do, phương án đã cân nhắc, yêu cầu liên quan |
| `machine.json` | những gì đã cài trên máy lúc chạy `env` |
| `environment.json`, `environment.md` | runtime + phiên bản + đã có/cần cài, lệnh cài thư viện, lệnh test |
| `tasks.json`, `tasks.md` | task `T1..`: `depends_on`, `preconditions`, AC phụ trách, ưu tiên, cỡ; thứ tự chạy |
| `state.json` | bước hiện tại, đã duyệt gì, trạng thái task |
| `llm.jsonl` | log mọi lần gọi model (đọc bằng viewer của RAAC nếu cần) |

## Cấu trúc code

| File | Việc |
|---|---|
| `swflow/models.py` | schema các artifact (pydantic) |
| `swflow/task_graph.py` | kiểm tra DAG, thứ tự chạy, task sẵn sàng, task bị chặn khi một task hỏng |
| `swflow/phases.py` | 4 bước: dựng yêu cầu gửi model, kiểm tra, thử lại, lưu |
| `swflow/store.py` | thư mục project, render Markdown, đọc `answers.md` |
| `swflow/ports.py` | giao diện `JsonModel` |
| `swflow/llm_raac.py` | adapter dùng LLM client của RAAC |
| `swflow/machine.py` | dò runtime và công cụ đã cài trên máy (chỉ đọc) |
| `swflow/handoff.py` | ghép yêu cầu MAS cho một task, kèm môi trường đã chốt |
| `swflow/cli.py` | lệnh `new / clarify / spec / architect / env / setup / plan / approve / status / auto / export` |
| `prompts/*.md` | prompt từng bước |

## Test

```powershell
python -m pytest tests -q
```

## Chưa làm

- `run_tasks.ps1` chạy tuần tự theo thứ tự topo và dừng ở task đầu tiên không PASS, kể cả khi các nhánh khác độc lập.
  Chưa cập nhật trạng thái task trong `state.json` từ kết quả MAS.
- Kiểm thử tích hợp và review toàn dự án sau khi chạy hết task.
