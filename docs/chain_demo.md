# Chạy MAS Chain trên repo thật với 9router

Phạm vi hiện tại: đọc repo và user chat → MAS thực hiện. Code, build, test và
kiểm tra môi trường chạy trực tiếp trên laptop. Không có sandbox, evaluator
hoặc pipeline benchmark trong luồng này.


MAS/L3 dùng Registry chung: role ở `registry/roles/core/`, prompt ở
`registry/prompts/`, schema/loader ở `raac.registry.models` và
`raac.registry.loader`. Cấu hình dùng `configs/llm.yaml`,
`configs/harness.yaml`, `configs/levels/l3_collaboration.yaml`.
Lệnh `raac-mas` tương đương `python -m raac.mas_cli` sau khi cài editable.
Registry VERSION hiện 0.17.1, prompt chain-v4. Bộ dữ liệu/model/loader
MAS trùng và prompt v1 cũ đã được gỡ. CLI sandbox hiện có giữ nguyên.
Tự cấu hình model/key ở `.env`; `--project-dir` chỉ định workspace.

## Đầu vào và workspace

```text
PROJECT/
├── repo/                    # Cây thư mục thật; file tools sửa trực tiếp ở đây
│   └── ...
└── requirement/
    └── user_chat.txt        # Yêu cầu hiện tại của người dùng

results/chain-<uuid>/
├── result.json
└── llm.jsonl
```

Không bắt buộc tên file, ngôn ngữ hoặc test framework. Repo có thể rỗng hoặc đã
có phần mềm. Mỗi run đọc lại user_chat.txt và repo hiện tại; không nạp task,
hội thoại hoặc kết quả của run trước. Code và test đã lưu trong repo vẫn
thuộc codebase đầu vào của run mới.

Chỉnh sửa nằm trực tiếp trong repo, kể cả khi run hết ngân sách hoặc thất bại.
File build/cache do lệnh local tạo cũng nằm trên laptop. Không copy workspace
vào results; mỗi run chỉ lưu hai file nêu trên.

## Cấu hình và chạy

1. Bật 9router, kết nối provider.
2. Điền LLM_API_KEY, LLM_BASE_URL, LLM_MODEL trong .env ở gốc framework.
   Key không nằm trong source/YAML/log. Base URL mặc định:
   http://127.0.0.1:20128/v1.
3. Chuẩn bị repo và requirement/user_chat.txt.
4. Runtime/dependency của phần mềm cần có trên laptop. Không cần Docker.

Cài dependencies framework nếu chưa có:

```powershell
python -m pip install -e .
```

Trên máy hiện tại, chạy từ gốc framework:

```powershell
Set-Location 'C:/Users/Tuan/Desktop/RAAC/RAAC/RAAC-Stage2'
$env:PYTHONPATH = 'src'
& 'C:/Users/Tuan/AppData/Local/Python/bin/python.exe' -m raac.mas_cli run-chain --project-dir 'C:/Users/Tuan/Desktop/RAAC/RAAC/RAAC-Stage2/currentProject'
```

--project-dir bắt buộc, trỏ tới thư mục cha chứa cả repo/ và requirement/.
--task-dir là alias. Đường dẫn tương đối tính từ thư mục terminal hiện tại;
đường dẫn có dấu cách cần đặt trong dấu nháy.

Các lựa chọn:

```powershell
python -m raac.mas_cli models
python -m raac.mas_cli run-chain --project-dir 'D:/myProject' --model MODEL_ID --max-rounds 3
```

models chỉ đọc danh sách model, không gửi task để completion.

### Timeout LLM và chính sách retry/usage

`configs/llm.yaml` (llm-v3) có `timeout_seconds` cho mỗi request LLM (hiện 400s)
và `max_retries` (hiện 2). `configs/harness.yaml` có `agent_timeout_seconds`
cho toàn bộ lượt role gồm các request/tool/backoff (hiện 400s), còn
`timeout_seconds` ở cấp gốc là trần toàn run (900s). Timeout command nằm riêng
trong `execution.timeout_seconds`, không phải thời gian chờ model.

Muốn cho model thêm thời gian, chỉnh request timeout và role timeout phù hợp
với nhau; ví dụ request 120s, role 300s, giữ trần run đủ lớn. Các con số này
chỉ là ví dụ, không phải cấu hình đã tự tăng hoặc bảo đảm chạy được mọi task.

Client và adapter dùng cùng quy tắc: không gửi thêm completion khi usage của
run không đầy đủ. Lỗi mạng/ReadTimeout không nhận được HTTP response đánh dấu
usage chưa biết và dừng ngay `usage_unavailable`, không retry một request mới
rồi mới bị guard chặn. Không coi request timeout là 0 token; usage thành công
của request khác không bù được lượt đã thiếu. Run khác có bộ đếm riêng.

Retry/backoff cho HTTP 408/429/5xx có response vẫn giữ hành vi cũ, chỉ tiếp tục
khi tracker chưa có usage không xác định. Response thành công thiếu usage cũng
làm adapter dừng; cancellation và response lỗi định dạng không tự retry.
Log `llm_call` có `retry_scheduled`/`retry_blocked_reason`; terminal/web giải
thích “Không gọi lại” khi usage chưa xác định. Không tăng token_limit/call cap.

configs/harness.yaml version harness-demo-v9 có execution.mode=local,
timeout cấu hình 200 giây/lệnh và output tối đa 131072 byte. Không có cấu hình
sandbox. Tiến trình dùng quyền tài khoản đang chạy MAS; giới hạn cwd của tool
không phải cơ chế cách ly filesystem cho code được thực thi.

### Cache token trong log và kết quả

Client đọc `usage.prompt_tokens_details.cached_tokens` theo
[OpenAI Docs — Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
hoặc `usage.cached_tokens` nếu router đã chuẩn hóa trường này. Chỉ nhận số nguyên
không âm và không lớn hơn prompt_tokens. Log JSONL lưu trường chuẩn hóa
`usage.cached_tokens`; không lưu các trường usage tùy ý từ provider.

UsageTracker cộng theo run/agent/level; result.json và sự kiện run_completed có
`usage.cached_tokens` và `usage.cache_usage_complete`. Chỉ trả tổng cache khi mọi
HTTP attempt trong phạm vi đó đều có số liệu hợp lệ; thiếu hoặc sai số liệu sẽ là
`cached_tokens: null`, `cache_usage_complete: false`. Không coi dữ liệu thiếu là 0.
Thiếu cache không chặn run khi prompt/completion usage vẫn đầy đủ.

Cache là một phần của input: `total_tokens = prompt_tokens + completion_tokens`
và ngân sách token vẫn giữ cách tính cũ, không cộng cache lần nữa hoặc trừ cache.
Web hiển thị cache theo lượt LLM/role và tổng run; terminal cũng có chỉ số cache.
Log cũ không ghi cache sẽ hiển thị chưa có số liệu. Thay đổi chỉ đo cache từ
response cho các lượt gọi mới, không thể khôi phục cache của các run cũ và không
bảo đảm provider/router có trả trường đó. Đây chưa phải phép đo giá tiền hoặc
phần trăm quota tài khoản.

### Coder đã lưu file nhưng diff cuối sai

Nếu Coder đã sửa file thành công qua tool trong lượt hiện tại, workspace là nguồn
patch chính thức. Adapter sinh cumulative diff từ baseline, tự áp lại trong RAM
để kiểm chứng nội dung khớp file đã lưu và kiểm tra cùng schema CoderOutput trước
khi bàn giao. Diff cuối do model viết sai hunk không làm mất các sửa đổi này;
log/web/terminal có sự kiện `coder_patch_rebuilt` nêu nguồn workspace và hash patch.

Nếu chưa có sửa file thành công trong lượt Coder, diff model vẫn phải hợp lệ và
áp được vào baseline, nếu không dừng `invalid_coder_patch`. Lỗi sinh/kiểm chứng
patch workspace vẫn dừng `patch_generation_failed`. Schema/flow Chain không đổi:
Tester và Reviewer vẫn phải kiểm tra đúng revision và đầy đủ AC/test; sinh lại
patch không tự biến run thành PASS. Run cũ đã aborted giữ nguyên lịch sử.

## Chain và role

Coordinator hiện cố định chọn chain, không gọi LLM để chọn topology.
Graph chạy Analyst → Planner → Coder ⇄ Tester → Reviewer, cùng năm role.
Mặc định tối đa ba lượt Coder–Tester; --max-rounds phải từ 1 đến hard cap 4.
Đây chưa phải coordinator thích ứng nhiều topology.

| Role | Tool | Vai trò |
| --- | --- | --- |
| Analyst | list_files, read_file, read_files, search_repo | Đọc yêu cầu/repo, phân tích ràng buộc và AC |
| Planner | list_files, read_file, read_files, search_repo | Lập phương án theo cấu trúc phần mềm thực tế |
| Coder | Các tool đọc, edit_file, replace_text, delete_file, apply_patch, run_command, run_commands, run_tests | Hiện thực và gom smoke check trước bàn giao |
| Tester | Các tool đọc, edit_file, replace_text, delete_file, run_command, run_commands, run_tests | Sửa từng vùng test, chạy đầy đủ và báo kết quả thật |
| Reviewer | list_files, read_file, read_files, search_repo | Review patch, AC, smoke evidence và kết quả test |

Registry VERSION là 0.17.1. YAML role dùng cấu trúc như sau, ví dụ Planner:

```yaml
id: planner
role: Planner
group: core
prompt: prompts/planner.md
prompt_version: chain-v4
tools:
  - list_files
  - read_file
  - read_files
  - search_repo
model:
  ref: planner
cost_weight: 1.0
max_llm_calls: 6
```

`prompt` vẫn trỏ trực tiếp đến file đang có. Nội dung hướng dẫn tool đã được
đồng bộ với quyền YAML, giữ nhiệm vụ và hợp đồng bàn giao của từng role.
Cả năm role dùng prompt_version=chain-v4; output theo commit 39d39e7 và mở rộng L3.
`group` là cách viết thống nhất; schema cũng
nhận `Group` như mẫu người dùng. `model.ref` chọn model theo cấu hình bên dưới.
`cost_weight` là metadata chờ hiệu chỉnh pilot, chưa thay đổi tính giá/ngân sách.
Không thêm description/output_format vào YAML.

### Chọn model cho từng role

Cấu hình hiện tại có năm profile riêng: analyst, planner, coder, tester, reviewer;
mỗi role dùng model.ref trùng ID của mình. Tất cả profile đang dùng
`cx/gpt-5.6-sol`, kế thừa temperature/max_tokens cấp gốc. Đổi model trong từng
profile ở configs/llm.yaml để chọn riêng; --model chỉ tác động ref default.

`ref: default` dùng `model`, `temperature`, `max_tokens` ở cấp gốc của
`configs/llm.yaml`. Riêng model mặc định có thứ tự ưu tiên:
`--model` → `LLM_MODEL` trong môi trường/`.env` → `model` trong YAML.

Muốn role dùng model riêng, thêm profile vào cùng `configs/llm.yaml`:

```yaml
# Các cấu hình version/base_url/model/timeout/retry ở trên vẫn giữ nguyên.
profiles:
  analysis:
    model: MODEL_ID_FOR_ANALYSIS
  coding:
    model: MODEL_ID_FOR_CODING
    temperature: 0.0
    max_tokens: 8192
```

Thay model ID bằng ID từ `python -m raac.mas_cli models`. Trong role YAML,
đặt `model: {ref: analysis}` hoặc `model: {ref: coding}`. Profile bắt buộc có
model ID; temperature/max_tokens không khai báo sẽ kế thừa cấp gốc.
`default` là ref dành riêng cho cấp gốc, không khai báo lại trong profiles.
Tên ref/profile dùng chữ thường, số và dấu gạch dưới, bắt đầu bằng chữ cái.

`--model`/`LLM_MODEL` chỉ đổi model của ref default; model trong profile có tên
được giữ theo YAML. Tất cả profile dùng chung base_url, API key, timeout và retry
của client 9router. Key chỉ nằm ở môi trường/`.env`, không khai báo trong profile.
Chưa có profile định tuyến tới endpoint/auth riêng.

CLI kiểm tra tất cả model.ref trong Registry trước khi tạo workspace hoặc gọi
model; ref không tồn tại hay default chưa có model sẽ báo lỗi và không gửi task.
Nếu mọi role dùng profile có tên thì model mặc định có thể để trống.
Mỗi lần gọi LLM ghi model_ref, model_settings và requested_model/served_model;
result provenance lưu effective_config.role_models cùng snapshot/hash cấu hình.
Các role vẫn dùng chung bộ đếm token và budget guard của run, kể cả khi khác model.
Flow Chain, output contract và max_llm_calls không đổi.
Output format đổi theo commit; schema arguments tool và flow Chain giữ nguyên.

Prompt hoàn chỉnh thêm danh sách `Role tool allowlist from Registry` lấy trực
tiếp từ YAML. Hướng dẫn read_files/run_commands chỉ được thêm khi role có tool
tương ứng; Analyst/Planner/Reviewer không nhận gợi ý gọi command. Danh sách
function declarations trong request vẫn quyết định tool được gọi ở lượt đó,
kể cả lượt cuối chỉ bàn giao, không có tool.

Prompt Tester nêu đủ tool đọc/sửa/xóa test, giới hạn không sửa file Coder đã
chạm và yêu cầu chọn lại cấu hình test đầy đủ để tránh kế thừa smoke command.
Prompt Coder phân biệt run_command/run_commands với run_tests cho smoke có
báo cáo cấu trúc. Unit test đối chiếu tool được nhắc trong system prompt với
function declarations cho đủ năm role, cả bật/tắt compaction.

max_llm_calls trong từng role YAML giới hạn số lượt LLM bên trong role, khác
max_rounds của vòng Coder–Tester. Coder/Tester mặc định có 10 lượt; lượt cuối
không khai báo tool; Coder trả diff, các role khác trả JSON đúng hợp đồng.
Nếu model vẫn đòi tool ở lượt cuối, adapter dừng agent_call_limit trước khi
thực hiện tool đó. Đây không phải cơ chế tăng giới hạn hoặc tự báo PASS.

File tools chỉ nhận đường dẫn tương đối dưới repo, loại credential, VCS và
dependency cache; chặn symlink/junction/path escape. Snapshot theo dõi baseline
tối đa 2000 file, 2 MiB/file và tổng 16 MiB. Binary được giữ trong baseline,
còn tool sửa file chỉ nhận văn bản UTF-8.

Quyền file tools: Analyst/Planner/Reviewer chỉ đọc. Tester được thêm file test
và sửa test có sẵn theo quy ước đường dẫn, không ghi implementation đầu run
hoặc file Coder đã sửa. Lệnh local chạy với quyền tài khoản hiện tại; các
quy tắc file tools không cách ly được tác động của tiến trình.

## Registry dùng chung

`models.py` giữ cấu trúc YAML role của MAS: id/role/group/prompt,
prompt_version/tools/model.ref/cost_weight/max_llm_calls. `Group` là alias
của `group`. Model profile hiện chỉ hỗ trợ default; không khai thêm
description/output_format trong YAML. Output format được suy ra từ role
và kiểm tra bằng hợp đồng Chain dùng chung.

`loader.py` gom lỗi khi kiểm tra version, tool catalog, tool trùng/lạ,
tên file/ID, group, tên role và đường dẫn prompt. `registry/tools.yaml`
khai đủ tool hiện có. Core và extended đều nạp được; việc nạp extended
không chứng minh L4 đã được hiện thực.

`Registry.get_role(id)` trả RoleSpec; `Registry.get(id hoặc tên)` trả
LoadedRole gồm spec/system_prompt/prompt_hash cho adapter. Snapshot và
stamp_record dùng cùng registry/VERSION. Chỉ còn một nguồn role/prompt.

## Chạy lệnh và test local

run_command nhận argv, cwd tương đối với repo, env bổ sung và timeout_seconds.
Nó trả stdout, stderr, exit_code, thời gian và lỗi timeout/output nếu có.
Không tự bọc chuỗi thành lệnh shell; muốn dùng shell thì argv phải gọi shell.

Schema tool công bố timeout tối đa đúng với execution.timeout_seconds hiện tại.
Yêu cầu vượt mức được trả TimeoutPolicyExceeded kèm mức hợp lệ để agent sửa;
không đổi cấu hình timeout của máy hay tự kéo dài lệnh.

Ví dụ tham số tool kiểm tra runtime:

```json
{"command": ["python", "--version"], "cwd": "."}
```

Với python/python3, executor ưu tiên .venv hoặc venv trong repo; nếu không có
thì dùng Python đang chạy MAS để tránh Windows Store alias. Có thể chỉ định
đường dẫn interpreter khác. Runtime khác lấy từ đường dẫn đã chỉ định/PATH.

Coder dùng file tools để sửa source nhằm ghi nhận đầy đủ cumulative patch.
run_command phục vụ chạy code/build/kiểm tra môi trường. Output được log qua
tool orchestrator như các lời gọi tool khác.

### Đọc nhiều file/vùng và chạy nhiều command trong một lượt

`read_files` nhận tối đa 8 mục, có thể là nhiều file hoặc nhiều vùng của cùng
file. Ví dụ dưới đây chỉ minh họa tham số; agent chọn path từ repo thực tế:

```json
{"files":[
  {"path":"src/service.py","start_line":1,"end_line":80},
  {"path":"src/service.py","start_line":150,"end_line":210},
  {"path":"tests/test_service.py"}
]}
```

Mỗi mục có observation và tool_call_id riêng, liên kết parent_tool_call_id.
Mục lỗi không làm mất nội dung những mục đọc thành công. Context manager nhận
diện source đầy đủ trong lô để cache một bản, không lặp lại toàn bộ trong các
observation/checkpoint. Vùng đọc một phần không được coi là toàn file.

`run_commands` nhận tối đa 4 command argv, chạy tuần tự trong một tool call,
không mở shell để ghép chuỗi lệnh. Mỗi command chọn cwd/env/timeout riêng:

```json
{"commands":[
  {"command":["python","--version"]},
  {"command":["python","scripts/smoke.py"],"cwd":".","timeout_seconds":15.0}
],"stop_on_error":true}
```

Chỉ dùng command/path tồn tại trong project. Cả lô dùng chung giới hạn thời
gian execution.timeout_seconds (mặc định 60s), không phải 60s nhân số mục.
Timeout mỗi command được hạ theo thời gian còn lại. Timeout vượt policy bị
từ chối trước khi bắt đầu lô. Mặc định dừng khi một mục lỗi/exit khác 0;
`stop_on_error=false` tiếp tục các mục khác khi còn thời gian. Kết quả liệt kê
results theo index, stdout/stderr/exit code từng mục, failed và skipped.
Không tự retry command có thể có side effect. run_tests vẫn dùng cho report
test đầy đủ và số liệu quan sát thật.

### Sửa vùng nhỏ và bàn giao bằng chứng smoke check

`replace_text` sửa file đang có bằng đoạn old_text khớp chính xác. Mặc định
phải có đúng một lần xuất hiện; có thể chỉ định expected_occurrences khi muốn
thay nhiều lần. Nếu thiếu đoạn cũ hoặc khớp nhiều hơn dự kiến, tool trả
EditConflict và không ghi file; agent đọc lại vùng hiện tại trước khi sửa.

```json
{"path":"tests/test_service.py",
 "old_text":"self.assertEqual(add(2, 3), 4)",
 "new_text":"self.assertEqual(add(2, 3), 5)"}
```

Tester chỉ sửa test hoặc file chính Tester đã tạo; implementation và file
Coder đã chạm vào vẫn bị chặn. apply_patch tiếp tục chỉ dành cho Coder.
Context manager bỏ payload sửa khỏi các receipt và cập nhật source cache từ
before/after hash khi có đủ bản cũ, tránh đọc lại cả file chỉ vì một sửa nhỏ.

Prompt Coder hướng dẫn mặc định một lô với 1–2 smoke check có mục tiêu sau
triển khai, rồi bàn giao. Khi có lỗi/rủi ro cụ thể có thể kiểm tra thêm; đây là
hướng dẫn cho model, không phải hard cap 2 command. Không thay max_llm_calls
hoặc bỏ role. Tester giữ kiểm thử độc lập, đầy đủ theo AC.

Framework gắn execution_evidence vào metadata bàn giao Coder từ quan sát run_command/
run_tests thật, ghi đè mọi evidence do model tự khai. Tối đa 8 receipt gần nhất
của đúng lượt Coder: tool_call_id, command preview/hash, cwd, status, exit code,
stdout/stderr/summary rút gọn, số test nếu có và patch_hash. matches_final_patch
cho biết receipt còn cùng revision khi bàn giao; nó không tự chứng minh AC.
Command/output đầy đủ vẫn ở log audit. Tester và Reviewer nhận coder_summary,
coder_evidence trong artifacts; result.json giữ cùng dữ liệu, không tạo file thứ ba.

Tester chọn lệnh run_tests theo project, ví dụ:

```json
{
  "command": ["python", "-m", "unittest", "discover", "-s", "checks"],
  "cwd": ".",
  "env": {"PYTHONPATH": "src"}
}
```

run_tests chạy trên repo thật trong tiến trình local riêng. Hỗ trợ báo cáo
unittest, pytest qua JUnit, Node TAP hoặc lệnh tùy chọn xuất JUnit tại report_path.
Runtime/package phải có trong môi trường local đã chọn. Không có test hoặc
không đọc được số liệu đáng tin cậy thì báo lỗi, không coi exit code 0 là PASS.

Gọi run_tests không có tham số lặp lại lựa chọn trong cùng run. Nếu chưa có
lựa chọn, chỉ fallback unittest khi thấy file test Python theo quy ước. Adapter
chạy lại test sau output cuối của Tester và thay số liệu model khai bằng kết
quả thực thi. Thiếu report hoặc test fail là blocker.

Với unittest, tests_run và failed cùng đếm theo test method. Một method có
nhiều subTest lỗi vẫn là một method thất bại; subtest_failures giữ số subtest
lỗi riêng. Summary nguyên gốc vẫn có traceback và tổng lỗi chi tiết.

Coder trả unified diff theo prompt; framework vẫn tạo diff nhiều file từ
workspace so với baseline đầu run để đối chiếu sản phẩm thực. apply_patch mặc định dựa trên code hiện tại,
base=baseline chọn bản đầu run. Report/review phải gắn đúng patch hash.

## Context trong mỗi role

Mỗi lượt chạy role bắt đầu với context mới. Raw user chat trong requirement chỉ
gửi cho Analyst và Reviewer. Planner, Coder và Tester nhận bàn giao thay cho raw
chat; mỗi request tiếp theo của ba role này cũng chỉ lặp lại bàn giao cần dùng.

| Role | Nội dung nhiệm vụ nhận |
| --- | --- |
| Analyst | User chat gốc, repo và AC/ràng buộc kế thừa nếu có |
| Planner | WorkSpec gồm summary, requirements, AC dạng chuỗi, constraints, open_questions, interface_contracts; kèm ac_catalog |
| Coder | WorkSpec + Plan đầy đủ; retry thêm report lỗi và code đã sửa |
| Tester | WorkSpec + contract mới từ Planner + code hiện tại/patch hash + Coder summary/evidence |
| Reviewer | User chat gốc + Spec + Plan quyết định/phụ thuộc + full diff + test report/evidence |

Analyst phải chuyển đủ hành vi, Business Rules, giới hạn, ví dụ cần thiết và
interface vào WorkSpec; interface_contracts chứa chữ ký, định dạng vào/ra và
yêu cầu tương thích. Planner/Coder/Tester báo blocker khi Spec thiếu hoặc mơ hồ.
Reviewer đối chiếu cả Spec lẫn code/test với user chat để phát hiện bỏ sót yêu cầu.
Không cắt chuỗi AC/contract. Việc model chuyển đủ nội dung vẫn cần kiểm tra trong
run thật; test framework chỉ chứng minh đường truyền và schema.

Plan được chọn theo nhu cầu role; các bản sao giống hệt trong
incoming_message được thay bằng tham chiếu tới field đã có trước khi lọc Plan,
tránh message bàn giao đưa lại bản đầy đủ vào context. Schema bỏ title mô tả,
giữ type/required/ràng buộc.

Từ Registry 0.17.1, TechnicalPlan theo format của commit 39d39e7: steps gồm
id số nguyên liên tiếp, description, files, depends_on và phần mở rộng ac_ids.
Planner không chép lại đề, ví dụ hoặc chữ ký đã có trong requirement/spec.
interface_contracts chỉ chứa contract mới phải quyết định và test command/cwd/env
nếu đã chọn. covered_ac_ids và hợp ac_ids của các bước phải khớp toàn bộ AC;
graph dừng incomplete_plan nếu bước bỏ sót hoặc tham chiếu AC lạ.

| Role nhận | Nội dung Plan gửi LLM khi context.enabled=true |
| --- | --- |
| Coder | Đầy đủ các bước, contract và ánh xạ AC; retry vẫn nhận report lỗi |
| Tester | Chỉ interface_contracts; tự xây oracle từ WorkSpec/AC/contract Analyst và repo |
| Reviewer | decisions gồm id, description, files, depends_on, ac_ids và contract; giữ ID để đọc phụ thuộc |

Tester vẫn nhận file đã đổi, coder_summary/evidence và patch hash. Reviewer vẫn
nhận full diff, test report, blocker và evidence. Chỉ lọc bản sao gửi model:
state, result.json và agent_started trong audit giữ toàn bộ Plan; llm_request
cho biết chính xác payload đã lọc. agent_started cũng bỏ requirement ở Planner,
Coder và Tester; raw chat đầy đủ vẫn có trong state và audit Analyst/Reviewer.
Các bản sao raw chat và tham chiếu requirement trong incoming_message được bỏ
hoặc trỏ về artifacts.spec, tránh tham chiếu treo hay gửi lại raw chat.
context.enabled=false gửi toàn bộ Plan nhưng không thay chính sách raw chat chỉ
cho Analyst/Reviewer. Log lịch sử vẫn đọc được; schema
đầu ra Planner của run mới yêu cầu steps có cấu trúc, không dùng steps dạng chuỗi.

Để giảm input gửi lặp qua mỗi lần gọi tool, context manager rút gọn bằng code,
không gọi thêm LLM:

- Payload edit_file/apply_patch đã thực thi được thay bằng biên nhận có đường
  dẫn, độ dài/hash và kết quả thành công/lỗi. Full code/patch vẫn nằm trong log.
- role_file_context giữ một phiên bản đầy đủ gần nhất của file đã đọc/ghi trong
  role, tối đa 12000 ký tự tổng nội dung, ưu tiên file role vừa sửa. Các bản đọc
  trùng trỏ về bản này; adapter kiểm tra hash với file thật trước mỗi lượt LLM.
  File bị đổi/xóa hoặc quá giới hạn không được ghim vào cache. Cache reset mỗi
  lượt role, không dùng lại qua task/run. Metadata cache có thể tăng nhẹ payload
  của lượt đầu để tránh agent phải đọc lại source ở các lượt sau.
- Giữ một lượt tool gần nhất; các lượt cũ thành checkpoint chứa tên tool,
  tham số gọn và kết quả. Output dài giữ đầu/cuối, đánh dấu rút gọn để agent
  đọc lại khoảng dòng cần thiết. Số liệu test và lỗi không bị đổi thành PASS.
- Checkpoint cũ được cắt bằng `trim_messages` của LangChain Core, dùng
  `count_tokens_approximately` để chọn các biên nhận gần nhất vừa ngân sách.
  Mỗi biên nhận được giữ/bỏ nguyên khối; các cặp tool call/result gần nhất
  được xử lý riêng để bảo toàn giao thức. Kết quả run_tests gần nhất trong
  checkpoint luôn được giữ, kể cả khi riêng nó vượt ngân sách; log có cờ báo.
- Bỏ bản sao report nằm trong execution.stdout của run_tests khi summary và
  số liệu đã có; stdout/stderr đầy đủ vẫn được ghi ở sự kiện tool gốc.
- Với diff trên 2000 ký tự, Tester và Coder retry nhận tham chiếu tới file đã
  thay đổi rồi đọc code hiện tại. Reviewer tiếp tục nhận full diff để review.
- Agent dùng danh sách repository_files đã có, tránh list_files lặp lại nếu
  danh sách đầy đủ, và có thể gộp các tool call độc lập trong một response.

Cấu hình trong context: enabled=true, recent_tool_turns=1,
max_tool_output_chars=8000, max_old_tool_output_chars=1200 và
max_file_context_chars=12000, max_checkpoint_tokens=2000. Có thể đặt
enabled=false để giữ cách truyền đầy đủ; không tự thay đổi token_limit.
Ngân sách 2000 token ước lượng chỉ áp dụng cho biên nhận cũ, không bao gồm
requirement/AC, source cache hoặc lượt tool gần nhất. Đây chưa phải giới hạn
context window toàn request theo tokenizer của model. Usage API vẫn là số
token thực tế dùng để chặn ngân sách run. Không gọi LLM để tóm tắt lịch sử.
Khi cần phần source đã rút gọn, agent phải dùng read_file với khoảng dòng.

LangChain Core là phụ thuộc dùng chung với LangGraph, được khai báo trực tiếp
trong pyproject.toml. Tài liệu API: [trim_messages](https://reference.langchain.com/python/langchain-core/messages/utils/trim_messages)
và [count_tokens_approximately](https://reference.langchain.com/python/langchain-core/messages/utils/count_tokens_approximately).
Client 9router tiếp tục gửi Chat Completions như trước.

Event context_compacted cho biết số ký tự lịch sử trước/sau, history_backend,
checkpoint_estimated_tokens, checkpoint_receipts_dropped và
checkpoint_protected_over_budget khi áp dụng bộ cắt lịch sử. Final run_completed
ghi usage tổng của run để show-log vẫn hiện đúng khi chỉ đọc phần cuối log.

## Kết quả và log

result.json chứa trạng thái, lý do dừng, patch, report, verdict, quyết định
Chain, usage và execution.mode=local. Không có kết quả đánh giá sandbox.
llm.jsonl lưu trao đổi role/LLM, tool arguments/results, stdout/stderr,
retry và lỗi khi trace_content=true; secret được che trước khi ghi.
Trace không ghi reasoning nội bộ của provider.

Đọc log:

```powershell
python -m raac.mas_cli show-log --run-id RUN_ID
python -m raac.mas_cli show-log --run-id RUN_ID --agent tester --details
python -m raac.mas_cli show-log --run-id RUN_ID --follow
python -m raac.mas_cli show-log --log 'duong/dan/Pasted text.txt'
python -m raac.mas_cli show-log --run-id RUN_ID --web
```

show-log chỉ đọc dữ liệu, không khởi chạy MAS. Mặc định hiện timeline UTC+7;
--details mở nội dung, --agent lọc role, --follow theo dõi tới khi run kết thúc.
Log chứa user chat/code sau redaction; nội dung thật của workspace không bị
redaction thay đổi. Usage thiếu thì run dừng thay vì coi token bằng 0.

### Xem luồng trao đổi trên trình duyệt

`show-log --run-id RUN_ID --web` mở trang local, mặc định tại
`http://127.0.0.1:8765/`. Mỗi trang đọc đúng một file `llm.jsonl`, không gọi
LLM hoặc khởi chạy lại MAS. Không tạo thêm artifact trong results.

- Mỗi card ghi bên gửi → bên nhận, role, vòng, lượt LLM và giờ UTC+7.
  `Nhận nhiệm vụ` thể hiện nguồn bàn giao thật trong incoming_message;
  `Bàn giao kết quả` mở được spec/plan/patch/report/verdict đã ghi.
- Token vào/ra hiện ngay trên card, ghép từ usage API theo llm_call_id.
  Request và response cùng lượt hiển thị cùng chi phí, không cộng hai lần.
  Card tool ghi `LLM liên quan`, không gán token riêng cho tool. Card bắt đầu/
  bàn giao role ghi tổng đúng agent_call_id, không trộn các vòng cùng role.
  Thiếu usage hiện `—`; nếu chỉ biết một phần qua retry thì hiện `≥`.
- `Gọi tool` hiện tham số; `Kết quả` hiện observation, code, stdout/stderr,
  exit code và report test. Bấm mã tool để chuyển tới yêu cầu/phản hồi cùng ID.
  Tool chạy xong nhưng test fail hoặc exit code khác 0 vẫn hiện đỏ.
  Các lượt adapter tự kiểm chứng được gắn nhãn riêng.
- Chọn role bên trái, tìm theo tên file/tool/nội dung hoặc bật `Chỉ lỗi`.
  `Context & HTTP` mở thêm request LLM thực tế (system prompt, messages,
  tool schema), response, retry, usage và các bước rút gọn context.
  Lời nhắn ngôn ngữ tự nhiên của agent được hiện ngay trong luồng chính.
- Bấm `Mở nội dung` để đọc từng trao đổi. Nội dung log được render như văn
  bản, kể cả code/HTML. Secret được che lại trước khi gửi tới trình xem.
  Các trường lồng nhau đặt nhãn phía trên và nội dung dùng hết chiều ngang;
  đoạn dài xuống dòng, không có ô cuộn nhỏ lồng bên trong cắt văn bản.
- Các số tổng luôn tính toàn run, không đổi theo bộ lọc. Khi có run_completed,
  ưu tiên usage cuối run để đọc log bị cắt đầu vẫn đúng tổng token/số lần gọi.
  Nếu chưa có usage đầy đủ, tổng hiển thị `≥` thay vì suy ra usage bằng 0.
- Trang tự đọc phần log mới; không cần `--follow`. `Tạm dừng cập nhật` dừng
  việc đọc của trình xem, không dừng MAS. Ctrl+C ở terminal dừng server xem log.

Đổi cổng hoặc chỉ in URL:

```powershell
python -m raac.mas_cli show-log --run-id RUN_ID --web --port 8766
python -m raac.mas_cli show-log --log 'duong/dan/Pasted text.txt' --web --port 0 --no-open
```

Server chỉ lắng nghe 127.0.0.1; assets nằm trong package, không tải CDN hoặc
gửi log tới dịch vụ ngoài. Muốn xem nội dung trao đổi, run phải được ghi với
`trace_content: true`. Không thể dựng lại message đã bị tắt ghi ở run cũ.
Sau khi cập nhật mã server hoặc thêm asset, dừng trình xem cũ bằng Ctrl+C,
chạy lại lệnh show-log --web rồi tải lại trang.

## Output contract của Chain

Các role theo format prompt tại commit 39d39e7: Analyst có requirements và AC
dạng chuỗi; Planner có bước số nguyên/files/depends_on; Tester có
tests_to_run/results/verdict; Reviewer có decision/comments. Coder trả
unified diff, không trả JSON. Schema ở chain_state.py; adapter và node dùng
chung parse_role_output/role_output_model.

Các field mở rộng L3 giữ contract công khai và AC coverage. ac_catalog ánh
xạ ID → mô tả AC; RoleHandoff tách output của model khỏi metadata runtime,
revision và evidence của framework. CodeOutput/TestReport/ReviewReport là
artifact điều phối, không phải wire schema để model tự khai thực thi.
Flow/giới hạn Coder–Tester và điều kiện PASS giữ nguyên. Registry MAS 0.17.1,
prompt chain-v4; chưa nghiệm thu resume checkpoint thuộc contract cũ.

Xem [cấu trúc request, output, metadata và từng chặng bàn giao](../src/raac/collaboration/levels/l3_collaboration/l3_topologies.md#cấu-trúc-hợp-đồng-chuyển-giao-giữa-các-role).

## Kiểm tra framework

```powershell
Set-Location 'C:/Users/Tuan/Desktop/RAAC/RAAC/RAAC-Stage2'
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests/mas/unit -p 'test_*.py' -q
python -m unittest discover -s tests/mas/integration -p 'test_demo_local.py' -v
node --test tests/mas/web/test_trace_usage.mjs
```

Các kiểm tra dùng project tạm, HTTP mock và tiến trình local thật, không gọi
completion thật hoặc sửa currentProject của người dùng. Có kiểm tra cwd/env,
stdout/stderr/exit code, test fail/zero test, JUnit, timeout tiến trình con,
output limit và hai luồng CLI gồm repo src/app với thư mục checks riêng.
