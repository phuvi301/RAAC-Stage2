# Chạy MAS Chain trên repo thật với 9router

Phạm vi hiện tại: đọc repo và user chat → MAS thực hiện. Code, build, test và
kiểm tra môi trường chạy trực tiếp trên laptop. Không có sandbox, evaluator
hoặc pipeline benchmark trong luồng này.


MAS/L3 dùng Registry chung: role ở `registry/roles/core/`, prompt ở
`registry/prompts/`, schema/loader ở `raac.registry.models` và
`raac.registry.loader`. Cấu hình dùng `configs/llm.yaml`,
`configs/harness.yaml`, `configs/levels/l3_collaboration.yaml`.
Lệnh `raac-mas` tương đương `python -m raac.mas_cli` sau khi cài editable.
Registry VERSION hiện 0.18.6, Coder và Environment dùng prompt chain-v5; các role còn lại chain-v4. Bộ dữ liệu/model/loader
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
4. Role Environment kiểm tra và cài dependency vào virtualenv local một lần ở bước đầu tiên, trước Analyst và Planner. Không cần Docker.

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

### Role Environment chạy một lần ở bước đầu tiên

Flow Chain topology v5:

`Environment → Analyst → Planner → Coder → Tester → Check`.
Khi cần sửa tiếp: `Check → Limit → Coder → Tester`. Environment không chạy lại;
Coder/Tester dùng cấu hình Python/cwd/env đã xác minh trong cả run.
Environment không tăng số vòng Coder–Tester. Chưa sẵn sàng thì dừng tại
`environment_setup_failed`, giữ báo cáo và patch đã có; không lặp sửa code vì thiếu dependency.

Khai báo ở `registry/roles/core/environment.yaml`, prompt ở
`registry/prompts/environment.md`; Environment dùng model mặc định chung, có thể ghi đè bằng `LLM_MODEL`.
Role đọc manifest/CI/README, chọn Python tương thích, tạo hoặc dùng lại `.venv`,
cài runtime/build/test dependency bằng `run_command` hoặc `run_commands`.
Prompt yêu cầu giữ cài đặt trong virtualenv của project và không sửa source/test để né lỗi.

`execution_environment.python_candidates` liệt kê interpreter đã có trong các
runtime cục bộ (kể cả `.venv38`, `.venv39`, `.python38`). Đây chỉ là đường dẫn
được phát hiện, không phải bằng chứng ready hoặc dữ liệu kế thừa từ run trước.
Environment phải kiểm tra phiên bản/import và gọi `configure_environment` trong
chính run hiện tại; ưu tiên runtime tương thích đã có, tránh xóa môi trường đang
hoạt động hoặc tải lại dependency khi lỗi mạng và còn candidate phù hợp.
Discovery không chạy interpreter, không đi vào site-packages hoặc theo thư mục
symlink/junction; source snapshot vẫn bỏ qua các runtime này.

Snapshot/list/search/patch bỏ qua các thư mục runtime Python cục bộ có tên
`.python`, `.venv` và hậu tố phiên bản như `.python39`, `.venv38`, `.venv-3.12`,
cùng installer dạng `.python-3.9.13-installer.exe`. Quy tắc áp dụng cả trước và
sau Environment, không xóa runtime hoặc đưa dependency vào baseline/patch.
Các tên source như `.python-tools`, `.venv_config` và `venv_helpers.py` vẫn được
giữ. File tools không đọc/ghi các đường dẫn runtime bị loại; command tools vẫn
chạy interpreter đã được Environment xác minh.
Source thật vượt 2 MiB dừng bằng `workspace_file_too_large`; vượt tổng 16 MiB
hoặc 2000 file dừng bằng `workspace_snapshot_limit`, không gán thành
`invalid_output`. Nếu lỗi ở bước chuẩn bị danh sách file trước khi gọi model,
trace ghi `agent_failed` với `stage=workspace_preflight` và mã lỗi tương ứng.

Tool `configure_environment` nhận `python_executable`, `cwd`, `test_command`,
`check_command` và `env`. Runtime chạy Python thật rồi kiểm tra import/discovery;
với `python -m pytest`, thu thập suite bằng `--collect-only` rồi thực thi suite và trả `smoke_report` cùng mẫu lỗi khác nhau để role kiểm tra tương thích dependency/API.
Không yêu cầu assertion của task phải pass ở bước chuẩn bị môi trường.
Chỉ khi startup/collection và report thực thi đáng tin cậy trong chính lượt Environment hiện tại mới mở cấu hình runtime. Role đọc smoke_report để phân biệt lỗi task với lỗi môi trường; assertion fail do task vẫn có thể bàn giao cho Coder, lỗi dependency/API chưa xử lý phải báo blocked.
Một báo cáo LLM tự khai thành công không mở được gate.

Helper test chạy bằng Python của project và tương thích Python 3.8; Python chạy
framework vẫn theo yêu cầu `>=3.11`. Đường dẫn `python_executable` tương đối trong
`configure_environment` được tính từ `cwd`, giống command/test tools.
JUnit phân biệt `<skipped type="pytest.xfail">` với skip thông thường; unittest
ghi nhận `expectedFailure` riêng và `unexpectedSuccess` là lỗi. `skipped` giữ
tổng số case bỏ qua/xfail, `expected_failures` là tập con có bằng chứng từ runner.
Suite chỉ passed khi exit code 0, không lỗi, không skip thông thường và có ít
nhất một case passed thật. Suite toàn xfail/zero test hoặc strict XPASS vẫn bị
chặn. Các case xfail giữ status skipped và lý do, không đổi thành case passed.
Counter expected failure đi qua Environment, Tester runtime và result; AC và
Reviewer vẫn phải đạt đủ điều kiện Chain PASS.

Cấu hình đã xác minh được dùng cho lệnh Python và test sau đó, kể cả khi model
vẫn đưa đường dẫn Python đang chạy MAS. `run_tests()` không tham số dùng suite/cwd/env
đã chọn; Tester vẫn có thể chọn thêm test. Result lưu `environment_report` và
`execution.prepared_environment`; log/web/terminal hiển thị role, tool và kiểm tra thực tế.

`timeout_seconds: null` trong role bỏ deadline riêng của Environment. Lệnh và batch
cài đặt/kiểm tra, cùng HTTP của role này, không có timeout phía RAAC. Đồng hồ run
tạm dừng trong thời gian Environment chạy; Coder/Tester vẫn giữ giới hạn cũ.
Giới hạn token chung, `max_llm_calls: 20`, output limit và hủy bằng Ctrl+C vẫn áp dụng.
Provider/router hoặc hệ điều hành vẫn có thể tự ngắt kết nối/tiến trình.
Không hỗ trợ nạp checkpoint topology v2/v3 vào graph v4.

Executable tương đối của `run_command`/`run_tests` được phân giải theo `cwd`
trước khi spawn, kể cả trên Windows. Ví dụ `cwd=xarray` cùng
`.venv\Scripts\python.exe` chọn `repo/xarray/.venv/Scripts/python.exe`.
Bare `python` ưu tiên cấu hình Environment đã xác minh; khi chưa có cấu hình,
ưu tiên venv ngay trong cwd rồi venv ở gốc repo, cuối cùng Python của MAS.
Lệnh lỗi hiển thị đường dẫn tuyệt đối đã thử. Không cài lại venv chỉ vì lỗi khởi chạy.

### Diff từ Git lồng nhau và báo cáo test lớn

Khi Coder trả diff sau một `git diff` thành công, adapter dùng cwd đã quan sát và
marker Git worktree trong repo để chuẩn hóa header về gốc `repo/`. Không tự dò
prefix chỉ vì tên file giống nhau. Patch chỉ có một base áp dụng hợp lệ mới được
áp dụng; nhiều base hợp lệ hoặc nội dung không khớp vẫn bị từ chối.
Diff của lần chạy trước đã khớp nguyên baseline/current của run mới được nhận là
no-op: patch của run hiện tại rỗng, không ghi lại file hoặc nhận công thay đổi cũ.

Báo cáo test JSON đầy đủ đi qua file tạm do parent chọn, không đi chung pipe stdout.
`execution.max_output_bytes` vẫn giới hạn output lệnh; `execution.max_report_bytes`
mặc định 16 MiB giới hạn report riêng. Report lỗi/quá giới hạn không được coi là pass.
Full case results giữ trong log/state/result; handoff lớn chỉ gửi aggregate counts,
AC/blockers, summary và mẫu lỗi đại diện để không lặp hàng trăm stacktrace vào LLM.
Executor đóng transport/pipe trước khi event loop kết thúc, kể cả output-limit,
timeout và cancellation; không chỉ chờ tiến trình trả exit code trên Windows.

### Timeout LLM và chính sách retry/usage

`configs/llm.yaml` (llm-v4) có `timeout_seconds` cho mỗi request LLM (hiện 400s)
và `max_retries` (hiện 2). `configs/harness.yaml` có `agent_timeout_seconds`
cho toàn bộ lượt role gồm các request/tool/backoff (hiện 400s), còn
`timeout_seconds` ở cấp gốc là trần thời gian làm việc ngoài Environment (theo cấu hình hiện tại). Timeout command nằm riêng
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
Graph chạy Environment → Analyst → Planner → Coder → Tester → Reviewer; retry vẫn qua Limit, có sáu role.
Mặc định tối đa ba lượt Coder–Tester; --max-rounds phải từ 1 đến hard cap 4.

Topology chọn bằng tay: `--topology chain|chain_review_loop|star` (mặc định lấy từ `configs/levels/l3_collaboration.yaml`). `chain_review_loop` thêm `--max-review-rounds N`; `star` thêm `--hub rules|llm` và `--max-dispatches N`. Chi tiết mẫu ở [l3_topologies.md](../src/raac/collaboration/levels/l3_collaboration/l3_topologies.md).
Đây chưa phải coordinator thích ứng nhiều topology.

| Role | Tool | Vai trò |
| --- | --- | --- |
| Analyst | list_files, read_file, read_files, search_repo | Đọc yêu cầu/repo, phân tích ràng buộc và AC |
| Planner | list_files, read_file, read_files, search_repo | Lập phương án theo cấu trúc phần mềm thực tế |
| Coder | Các tool đọc, edit_file, replace_text, delete_file, apply_patch, run_command, run_commands, run_tests | Hiện thực và gom smoke check trước bàn giao |
| Tester | Các tool đọc, edit_file, replace_text, delete_file, run_command, run_commands, run_tests | Sửa từng vùng test, chạy đầy đủ và báo kết quả thật |
| Reviewer | list_files, read_file, read_files, search_repo | Review patch, AC, smoke evidence và kết quả test |

Registry VERSION là 0.18.6. YAML role dùng cấu trúc như sau, ví dụ Planner:

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
Coder/Environment dùng prompt_version=chain-v5; các role còn lại dùng chain-v4; output theo commit 39d39e7 và mở rộng L3.
`group` là cách viết thống nhất; schema cũng
nhận `Group` như mẫu người dùng. `model.ref` chọn model theo cấu hình bên dưới.
`cost_weight` là metadata chờ hiệu chỉnh pilot, chưa thay đổi tính giá/ngân sách.
Không thêm description/output_format vào YAML.

### Chọn model cho từng role

Cấu hình hiện tại dùng `model.ref: default` cho cả sáu role. Đặt `LLM_MODEL`
trong `.env` để đổi model chung; nếu bỏ trống thì dùng model cấp gốc trong
`configs/llm.yaml`. Các profile có tên vẫn có thể được chọn riêng bằng cách
đổi `model.ref` của role tương ứng.

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
function declarations cho đủ sáu role, cả bật/tắt compaction.

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
- Checkpoint cũ được cắt bằng `trim_messages` của LangChain Core và bộ đếm token
  ước lượng. Kết quả test gần nhất được bảo vệ bằng biên nhận gọn khi vượt
  `max_checkpoint_tokens`, giữ status/count/hash/ref và mẫu lỗi khi còn chỗ.
  Không giữ nguyên báo cáo test khổng lồ để vượt ngưỡng checkpoint.
- `run_tests.results`, `test_cases` và `tests_to_run` có mẫu đại diện khi báo cáo
  lớn: ưu tiên lỗi, kèm tổng số kết quả/số lỗi/số tên test và số liệu gốc. AC,
  blocker, verdict và patch hash giữ nguyên; mẫu không thay bằng chứng thực thi.
- `configure_environment.verification` rút gọn từng stdout/stderr lồng bên trong,
  giữ command/exit code/trạng thái; smoke_report cũng được rút gọn. Handoff chỉ
  chứa một bản environment_report ở cấp ngoài.
- Tool `read_tool_result` dành cho Environment/Coder/Tester/Reviewer đọc chi tiết
  observation_ref trong cùng run mà không chạy lại test. `path` là danh sách khóa
  và chỉ số, ví dụ `["results", 0, "details"]` hoặc `["verification", 1, "stdout"]`.
  `offset`/`limit` phân trang tối đa 10 phần tử hoặc 4000 ký tự. Giá trị lồng quá
  lớn trả preview có cờ rõ ràng; đi sâu bằng path để đọc trường chính xác.
  Ref được framework tạo từ quan sát thật; báo cáo lịch sử không chứng minh code
  hiện tại vẫn pass. Báo cáo đầy đủ giữ trong log/result; bộ đọc giữ dữ liệu trong
  phiên run, không phải cơ chế resume Engine sau khi process kết thúc.

Cấu hình hiện tại `harness-demo-v14` đặt `context.enabled=false`: giữ nguyên
lịch sử tool gửi model, không cắt code/output hoặc thay lượt ghi bằng receipt
trong context. Coder có `max_llm_calls=20` cho mỗi lần kích hoạt role, bao gồm
lượt cuối chỉ trả patch (tối đa 19 lượt có tool). Memory vẫn bật với ngưỡng
compact snapshot riêng 50000/20000 token ước lượng; `token_limit` vẫn 800000.
Các tham số chỉ áp dụng khi bật lại context: recent_tool_turns=1,
max_tool_output_chars=8000, max_old_tool_output_chars=1200,
max_file_context_chars=12000, max_checkpoint_tokens=2000.
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
python -m raac.mas_cli show-log --run-id RUN_ID --agent coder --memory --details
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
LLM hoặc khởi chạy lại MAS. Panel memory chỉ đọc snapshot/journal trong thư mục
`memory/` cùng run với log đã chọn; không mở đường dẫn lấy từ nội dung log.
Trình xem không tạo thêm artifact trong results.

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
  `Context, HTTP & memory` mở thêm request LLM thực tế (system prompt, messages,
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

### Xem memory Coder

Mở **Memory Coder** phía trên timeline để xem snapshot hiện tại: phạm vi work,
revision, token ước lượng, số lần compact và các bản ghi đang giữ. Chọn phạm vi
khi một run có nhiều work. **Nhật ký đầy đủ** đọc `.events.jsonl`, tải từng
trang 50 bản ghi; bấm từng mục để mở arguments/observation, feedback, handoff
hoặc lỗi. Nội dung code/HTML được hiển thị như văn bản. Panel tự cập nhật và
không compact, thay đổi hoặc ghi lại memory khi đọc.

Timeline nhận ba sự kiện: `memory_saved` ghi entry và thông số lần ghi;
`memory_loaded` lưu đúng memory đã đưa vào request Coder retry;
`memory_compacted` lưu snapshot sau compact và số token trước/sau. Hai sự kiện
nạp/compact hiện trong luồng chính; các lần ghi nằm trong bộ lọc kỹ thuật.
Sự kiện memory được ghi cả khi `trace_content: false`, độc lập việc ghi toàn
bộ message LLM. Log cũ không có sự kiện vẫn xem được sidecar nếu còn file
memory; không có file thì panel báo chưa có memory.

Memory Coder `short-term-v2` ưu tiên một bản **working_notes** mới nhất ở đầu
snapshot, gồm `conclusions` (kết luận + tham chiếu bằng chứng), `ruled_out`
(giả thuyết đã loại + lý do + bằng chứng) và `next_steps` theo thứ tự. Coder gọi
`update_memory` để thay toàn bộ ba danh sách, giữ mục còn đúng và bỏ mục đã lỗi
thời; danh sách rỗng xóa phần tương ứng. Ghi chú giới hạn tổng 2000 ký tự JSON,
không chứa bản chép suy nghĩ hay dump source/stdout. Bằng chứng trong ghi chú là
tham chiếu do model khai, không thay bằng chứng test do executor quan sát.

Snapshot lưu biên nhận tool ngắn (path/hash/status/count/ref), còn journal giữ
arguments/observation đầy đủ và stored_entry để xem lại đúng revision. Ghi chú
được cập nhật ngay trong task message của call tiếp theo và được nạp trước các
biên nhận khi Coder retry. Chỉ Coder đang có phiên memory đúng run/call được ghi;
tắt memory thì tool báo MemoryUnavailable. Không gọi LLM tóm tắt phụ. Ghi chú mới
thay bản cũ tại nơi lưu; compact chỉ chạy khi snapshot vượt ngưỡng, bảo vệ nguyên
bản ghi chú mới nhất. Đọc/view/hydrate không compact hoặc ghi lại file. Snapshot
v1 vẫn đọc được; run cũ không tự sinh ra kết luận/giả thuyết/bước tiếp theo.

Trên web, mở **Memory Coder → Xem thay đổi memory**. Chọn **Thời điểm lưu**
để so sánh revision N với N−1: mục thêm, mục bị bỏ khỏi snapshot và mục được
rút gọn/thay đổi có phần Trước/Sau. Revision compact được đánh dấu COMPACT,
hiển thị token ước lượng trước/sau compact và số mục của snapshot. Token này
là ước lượng kích thước memory, không phải usage tính phí của API.
Lịch sử tải 50 revision mới nhất trước; nút **Tải revision cũ hơn** mở thêm.
API `/api/memory/history?id=...&cursor=...` trả metadata, còn
`/api/memory/change?id=...&revision=N` trả thay đổi. Dữ liệu dựng từ journal
đúng identity, sự kiện memory_saved/compacted và snapshot hiện tại; không gọi
model, ghi lại memory hoặc compact khi xem. Run cũ thiếu bản chụp compact hay
journal bị thiếu sẽ báo không đủ dữ liệu để so sánh chính xác; không giả dựng
snapshot lịch sử. Khởi động lại server show-log --web sau khi cập nhật.

Terminal: thêm `--memory` để đọc snapshot và nhật ký sau timeline; thêm
`--details` để mở nội dung. `--agent coder` lọc phần Coder. Với `--follow`,
timeline cập nhật sự kiện memory ngay khi run đang chạy; sidecar tổng hợp
được in sau khi run kết thúc.

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
Flow Coder–Tester và điều kiện PASS giữ nguyên. Registry chung 0.18.6,
Coder prompt chain-v5, các role khác chain-v4; chưa nghiệm thu resume checkpoint thuộc contract cũ.

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
