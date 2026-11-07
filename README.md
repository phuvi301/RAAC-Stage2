# RAAC Agent

TUI local: cài `python -m pip install -e ".[tui]"`, rồi chạy
`raac-mas tui --workspace "C:/work/my-project"` và bấm Ctrl+Enter.
TUI chọn được L1 Fixed, L2 Routing, L3 Collaboration (mặc định) và L4 Mock;
L4 chỉ xem thử, không gọi model hoặc sửa workspace.
Xem [hướng dẫn TUI](docs/tui.md) về approval, history và phím tắt.

Khung dự án Python >= 3.11 / LangGraph để xây dựng và so sánh bốn cấp điều phối agent.

- Kế hoạch và tiến độ: [checklist](docs/implementation_checklist.md).
- Quy tắc agent: [.agents/rules/progress.md](.agents/rules/progress.md).
- Kiến trúc: harness 4 layer bao quanh mỗi agent, collaboration engine dùng LangGraph.
- `requirements.lock` ghim dependency runtime, dev/test và setuptools; cài file này trước khi cài project.
- WASM, A2A và long-term memory được đặc tả là stub/tuỳ chọn trong kế hoạch.

## Khởi tạo môi trường

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install -r requirements.lock
    python -m pip install --no-deps --no-build-isolation -e .
    make test

GNU Make tự chọn Python trong `.venv` (Windows/Linux); có thể ghi đè bằng
`make test PYTHON=/path/to/python TEST_ARGS="-q"`. Nếu chưa có Make, chạy
`python -m pytest` trực tiếp. Windows có thể dùng GNU Make trong MSYS2.

    make pilot TASK=psf__requests-6027
    make freeze

`pilot` chạy kiểm chứng môi trường sandbox cho một task bằng CLI hiện có, cần Docker
và dataset/evaluator đã chuẩn bị; đây chưa phải pilot agent trên bốn level.
Chọn cấu hình khác bằng `CONFIG=configs/benchmark.yaml`.

`freeze` tạo file JSON riêng trong `results/freezes/`, lưu snapshot và SHA-256 của
các YAML cấu hình, Registry (role/tool/prompt/VERSION), `pyproject.toml` và lock.
Không đọc `.env`; lọc secret theo bộ lọc hiện có. Hash tính trên file gốc trước khi
lọc, nên nội dung đã lọc có thể khác hash. Có thể chỉ định
`FREEZE_OUTPUT="results/freezes/my-freeze.json"`; file đã tồn tại sẽ không bị ghi đè.
Lệnh không thay cấu hình đang dùng và không thay thế quyết định đóng băng của nhóm.

Khi chủ động cập nhật dependency, dùng uv và chạy lại kiểm thử:

    uv pip compile pyproject.toml --extra dev --extra tui --universal --python-version 3.11 -o requirements.lock --no-annotate

Lệnh giữ các pin hiện có khi còn hợp lệ; chỉ thêm `--upgrade` khi muốn nâng phiên bản.
Các bản lock được resolve cho Python >= 3.11 và có marker theo nền tảng;
kiểm chứng cài đặt hiện tại dùng Windows/Python 3.11.

CLI local, sandbox và benchmark L1–L3 đã có; L4 orchestration và nghiệm thu toàn bộ thực nghiệm chưa hoàn tất.

## Cấu trúc codebase

Cây rút gọn theo mã hiện tại; bỏ các file `__init__.py`, cache và artifact build.
**Bốn layer harness** bao quanh từng agent, còn **L1–L4** là các mức điều phối
giữa agent. L4 hiện chỉ có stub và lựa chọn mock trong TUI.

```text
./
├── src/raac/
│   ├── mas_cli.py                    # raac-mas: run-local, tui, run-chain, show-log
│   ├── cli.py                        # raac: sandbox và benchmark L1–L3
│   ├── tui/                          # Textual UI, extra tùy chọn [tui]
│   │   ├── app.py                    # chọn L1–L4/topology, panel, approval, history
│   │   ├── bridge.py                 # worker asyncio, queue thông báo, hủy run
│   │   └── state.py                  # replay JSONL, gộp delta, state có giới hạn
│   ├── llm/                          # client 9router / Chat Completions dùng chung
│   │   ├── config.py                 # endpoint, model/profile và secret từ môi trường
│   │   ├── client.py                 # HTTPX, retry, usage guard và trace
│   │   ├── streaming.py              # SSE UTF-8, ghép delta/tool-call, redaction
│   │   ├── protocol.py               # chuẩn hóa native tool-call và DSML
│   │   ├── usage.py                  # token thực nhận theo run/agent/level
│   │   └── pricing.py                # scaffold; không dùng quy đổi tiền khi so sánh
│   ├── registry/
│   │   ├── loader.py                 # nạp Registry, prompt và version/hash
│   │   ├── models.py                 # schema role/tool/model
│   │   └── output_formats.py          # kiểm tra output và unified diff
│   ├── harness/
│   │   ├── agent.py                  # lắp một agent từ Registry và runner
│   │   ├── layer1_lifecycle/          # Reason–Act–Observe, approval và checkpoint
│   │   │   ├── agent.py              # AgentRunner, guard, tool receipt, finalize
│   │   │   ├── event_loop.py
│   │   │   ├── state_machine.py
│   │   │   ├── approval.py
│   │   │   └── checkpoint.py
│   │   ├── layer2_core/              # context, prompt, memory, schema/message filter
│   │   │   └── memory/               # short-term; long-term/retriever còn stub
│   │   ├── layer3_execution/          # tool orchestration, quyền và giới hạn thực thi
│   │   │   ├── local_executor.py     # subprocess local, timeout, stdout/stderr
│   │   │   ├── local_test_runner.py  # báo cáo test quan sát được
│   │   │   ├── docker_execution.py   # adapter thực thi trong Docker
│   │   │   ├── tool_orchestrator.py
│   │   │   ├── tools/                # workspace, file, patch, search, test, môi trường
│   │   │   ├── sandbox/              # Docker, batch/resume, cache, network, policy
│   │   │   ├── guardrails/
│   │   │   └── cost_control.py       # token budget và deadline
│   │   └── layer4_connectors/        # interface connector; A2A còn stub
│   ├── collaboration/
│   │   ├── engine.py                 # Engine dùng chung; L1/L2 local đi qua đây
│   │   ├── graph_builder.py          # dựng LangGraph từ hợp đồng level
│   │   ├── state.py                  # state điều phối, tách lifecycle từng agent
│   │   ├── topologies.py             # khai báo chain, chain_review_loop, star
│   │   └── levels/
│   │       ├── base.py               # hợp đồng Level/LevelGraph
│   │       ├── l1_fixed.py           # luồng role cố định
│   │       ├── l2_routing.py         # Router chọn role và ghi lý do bỏ qua
│   │       ├── l3_collaboration/     # coordinator, Hub, graph, handoff, limits
│   │       └── l4_team_formation.py  # stub; chưa có orchestration L4 thật
│   ├── common/                       # hợp đồng output, lỗi, provenance, trace/memory
│   │   ├── tracing.py               # JSONL và subscriber sau redaction
│   │   ├── trace_viewer.py           # viewer terminal
│   │   ├── trace_web.py              # viewer HTTP chỉ đọc
│   │   └── trace_assets/             # JS/CSS/HTML của web viewer
│   └── benchmark/
│       ├── dataset.py                # loader task và chọn tập dữ liệu
│       ├── runner.py                 # batch task/level, record và resume
│       ├── engine_runner.py          # adapter L1/L2 trên workspace benchmark
│       ├── chain_runner.py           # adapter L3 trên workspace benchmark
│       ├── harness_config.py
│       ├── run_record.py             # schema kết quả và usage
│       ├── observe.py
│       ├── evaluator/               # completion; coverage/quality/conflict còn stub
│       └── analysis/                # pilot report; cost_estimator/utility còn stub
├── registry/                         # dữ liệu role/tool/prompt, phiên bản ở VERSION
│   ├── VERSION
│   ├── tools.yaml
│   ├── roles/core/                   # Analyst, Planner, Coder, Tester, Reviewer,
│   │                                 # Router, Hub, Environment
│   ├── roles/extended/               # catalog mở rộng; không chứng minh L4 đã chạy
│   └── prompts/
├── configs/
│   ├── llm.yaml                      # cấu hình LLM và profile theo role
│   ├── harness.yaml                  # budget, timeout, context, memory, execution
│   ├── levels/                       # YAML cho L1, L2, L3, L4
│   ├── repos/                        # profile môi trường/test theo repository
│   ├── experiments/                  # preset thực nghiệm
│   └── benchmark*.yaml               # tập dữ liệu và cấu hình benchmark
├── tests/
│   ├── unit/                         # Registry, Engine, harness và benchmark
│   ├── integration/                  # kiểm thử tích hợp, gồm các bài cần Docker
│   ├── mas/
│   │   ├── unit/                     # lifecycle, local/TUI, SSE và fixture 9router
│   │   ├── integration/              # HTTP mock + command/test thật ở workspace tạm
│   │   └── web/                      # Node tests cho trace viewer
│   └── fixtures/
├── scripts/                          # freeze, phân tích/profile, validation môi trường
├── docker/                           # Dockerfile.sandbox và Dockerfile.bench
├── data/                             # dataset, evaluator, cache repo; phần lớn gitignored
├── results/                          # artifact benchmark/báo cáo; phần lớn gitignored
├── docs/
│   ├── tui.md                        # thao tác TUI, streaming, approval và level mock
│   ├── architecture.md               # thiết kế harness và collaboration
│   ├── level_definitions.md
│   ├── l4_spec.md                     # đặc tả, không phải bằng chứng hiện thực
│   ├── token_reduction.md
│   └── implementation_checklist.md   # tiến độ, bằng chứng và giới hạn nghiệm thu
├── pyproject.toml                    # package, CLI entry point, extra dev/tui
├── requirements.lock                 # dependency được ghim version
├── Makefile                          # test, pilot sandbox, freeze
├── .env.example                      # mẫu cấu hình, không lưu secret thật
├── AGENTS.md
└── .agents/rules/progress.md          # quy tắc cập nhật tiến độ
```

Local TUI/API dùng workspace thật và lưu artifact ngoài workspace tại
`%LOCALAPPDATA%/RAAC/results/` (fallback `~/.local/share/RAAC/results/`).
Benchmark/sandbox dùng các đường dẫn trong config, thường là `data/` và `results/`.
TUI mặc định L3; L1/L2 dùng Engine, L4 mock không tạo client hay executor.
Streaming mặc định chỉ bật trong TUI; CLI/benchmark giữ chế độ non-streaming.

Xem [kiến trúc](docs/architecture.md) về trách nhiệm từng layer và luồng dữ liệu;
[checklist](docs/implementation_checklist.md) ghi mức kiểm chứng thực tế.
Cấu trúc thư mục không đồng nghĩa mọi scaffold/stub hoặc mọi level đã nghiệm thu.


## Pipeline repo và test

```powershell
.venv\Scripts\Activate.ps1
raac sandbox check
raac sandbox prepare --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027 --patch candidate.patch
```

Cần Docker Desktop ở chế độ Linux containers. Cache Git nằm ở `data/repos/<owner>/<repo>.git`, kết quả ở `results/<run_id>/record.json`. Có profile cho 12 repo trong dataset; mức kiểm chứng thực tế được ghi trong báo cáo batch và checklist. Dùng `raac sandbox --help` để xem các lệnh hiện có.

Dataset đi kèm project tại `data/tasks/cleaned_benchmark.json` (120 issue/12 repo); cấu hình mặc định không cần đường dẫn đến Stage1.

Chạy baseline toàn dataset và tiếp tục sau khi ngắt:

```powershell
raac sandbox profiles check
raac sandbox batch --batch-id baseline-all
raac sandbox batch --batch-id baseline-all --resume
```

Xem `results/batches/<batch_id>/summary.json` và `summary.csv` để phân biệt test đã chạy với lỗi môi trường hoặc timeout. Profile hợp lệ cho 120 task chưa chứng minh 120 môi trường đã chạy thành công.


Chế độ mặc định hiện đánh giá test theo issue từ SWE-bench Verified (F2P + P2P).
Dữ liệu evaluator được tách khỏi Task/cleaned dataset; không đưa gold patch cho agent.
Dùng `raac sandbox validate --config configs/benchmark.compatibility.yaml --batch-id issue-all`
để kiểm chứng baseline + gold. Muốn chạy toàn bộ suite cũ, thêm `--test-mode suite`.
Các profile compatibility đang được kiểm chứng; chưa mặc định 120/120 đã đạt.


Sandbox có quy trình chuẩn bị nhanh: `raac sandbox preflight` → `raac sandbox validate --stage pilot` → `raac sandbox validate --stage full --reuse-validated`. Chỉ full đạt đủ F2P/P2P mới có trạng thái `validated`. Build dùng cache nội bộ của builder sandbox; batch tự retention theo ngân sách mềm 30 GiB. Tham số chi tiết có trong `raac sandbox validate --help`; tiến độ kiểm chứng nằm trong checklist.

Record `validation_failed` cần xem kết quả từng F2P/P2P trong artifact của run; gold pass chưa đủ để đánh dấu task validated.

Tập benchmark đã chốt là **105 task/12 repo**, phiên bản `swebench-verified-raac-105-v1`.
Dùng `--config configs/benchmark105.yaml` cho các lệnh sandbox của tập này;
`configs/benchmark.yaml` và `configs/benchmark.compatibility.yaml` cũng mặc định chọn 105 task để tránh build nhầm nhóm bị loại.
Manifest 105 và danh sách 15 task bị loại nằm trong `data/tasks/`; dữ liệu gốc
và evaluator không đổi. Điểm chính là `100 × resolved / 105`, không thu nhỏ mẫu
số khi còn task chưa chạy.

## MAS/L3 Chain

L3 có các topology `chain`, `chain_review_loop` và `star`, bắt đầu từ Hub;
Environment vẫn có trong Registry/API nhưng không được các graph L3 lên lịch.
L1/L2 local dùng Engine chung; L3 dùng các builder trong `levels/l3_collaboration/`.
Đã có đường benchmark L1–L3 tạo candidate trong Docker và chấm độc lập;
kiểm tra cấu hình `configs/benchmark*.yaml`, `raac benchmark --help` và checklist
để phân biệt khả năng hiện có với phạm vi thực nghiệm đã được nghiệm thu.
Cấu hình nằm trong `configs/`, role/prompt trong
`registry/roles/core/` và `registry/prompts/`; CLI sandbox hiện có giữ nguyên.

Chạy `python -m raac.mas_cli run-chain --project-dir PATH` với `PYTHONPATH=src`,
hoặc `raac-mas run-chain --project-dir PATH` sau khi cài editable.
Để chỉnh trực tiếp project hiện có, dùng `raac-mas tui --workspace PATH`
hoặc `raac-mas run-local --workspace PATH --prompt "Yêu cầu"`.
Xem [hướng dẫn TUI/local, streaming và log viewer](docs/tui.md).
