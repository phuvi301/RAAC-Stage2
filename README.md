# RAAC Agent

Khung dự án Python >= 3.11 / LangGraph để xây dựng và so sánh bốn cấp điều phối agent.

- Kế hoạch và tiến độ: [checklist](docs/implementation_checklist.md).
- Quy tắc agent: [.agents/rules/progress.md](.agents/rules/progress.md).
- Kiến trúc: harness 4 layer bao quanh mỗi agent, collaboration engine dùng LangGraph.
- CLI sandbox Docker đã hiện thực; agent/LLM/collaboration còn scaffold. Xem [hướng dẫn sandbox](docs/sandbox.md).
- requirements.lock chưa chứa dependency đã khóa; cần resolve và kiểm tra ở D01.
- WASM, A2A và long-term memory được đặc tả là stub/tuỳ chọn trong kế hoạch.

## Khởi tạo môi trường

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install -e ".[dev]"
    python -m pytest

Không commit API key hoặc dữ liệu nhạy cảm. CLI sandbox đã có; CLI benchmark và các level chưa hiện thực.

## Cấu trúc thư mục

Thư mục hiện tại là gốc dự án `raac-agent`.
Cây dưới đây lược bỏ các file `__init__.py` và `.gitkeep` để dễ đọc.

~~~text
./
├── README.md
├── pyproject.toml
├── requirements.lock                 # ghim version (LangGraph, docker SDK, pytest...)
├── Makefile                          # make pilot, make test, make freeze
├── .env.example                      # API key, không commit key thật
│
├── configs/                          # toàn bộ tham số, đóng băng trước pilot ở tuần 2
│   ├── llm.yaml                      # model, temperature, seed, prompt version
│   ├── harness.yaml                  # trần token, số vòng tối đa, timeout, retry
│   ├── levels/
│   │   ├── l1_fixed.yaml
│   │   ├── l2_routing.yaml
│   │   ├── l3_collaboration.yaml
│   │   └── l4_team_formation.yaml
│   └── benchmark.yaml                # kích thước pilot/full, số lần lặp, song song
│
├── registry/                         # dữ liệu Agent Registry (cố định cho 4 level)
│   ├── VERSION                       # ghi vào mọi bản ghi kết quả
│   ├── roles/
│   │   ├── core/                     # Analyst, Planner, Coder, Tester, Reviewer
│   │   └── extended/                 # Security, Architect, DB/Migration (chỉ L4 dùng)
│   └── prompts/                      # system prompt template theo role
│
├── src/raac/
│   ├── common/                       # types, errors, ids, logging chung
│   │
│   ├── llm/                          # client duy nhất gọi LLM (đo token tại đây)
│   │   ├── client.py                 # retry/backoff, đọc usage, ghi JSONL
│   │   ├── usage.py                  # cộng dồn token theo run/agent/level
│   │   ├── pricing.py
│   │   └── mock_llm.py               # LLM giả cho unit test và 1-2 ngày đầu
│   │
│   ├── registry/                     # code nạp và kiểm tra registry
│   │   ├── models.py                 # pydantic schema (role, tool, model, nhóm)
│   │   └── loader.py
│   │
│   ├── harness/                      # bộ khung 4 layer bọc quanh MỖI agent
│   │   ├── agent.py                  # lắp một Agent từ role trong registry
│   │   │
│   │   ├── layer1_lifecycle/         # Layer 1 - Interface & Lifecycle
│   │   │   ├── event_loop.py         # vòng lặp Reason - Act - Observe
│   │   │   ├── state_machine.py      # IDLE, PLANNING, EXECUTING_TOOL,
│   │   │   │                         # WAITING_HUMAN_APPROVAL, FAILED, COMPLETED
│   │   │   ├── checkpoint.py         # lưu/khôi phục trạng thái để resume
│   │   │   └── approval.py           # chờ người duyệt (benchmark dùng auto-approve)
│   │   │
│   │   ├── layer2_core/              # Layer 2 - Intelligence Core
│   │   │   ├── context_manager.py    # cắt/tóm tắt để vừa context window
│   │   │   ├── prompt_hydrator.py    # ghép memory + state + tool list thành prompt
│   │   │   └── memory/
│   │   │       ├── short_term.py     # bộ nhớ trong phiên
│   │   │       ├── long_term.py      # RAG (Vector/Graph DB), có thể tắt
│   │   │       └── retriever.py
│   │   │
│   │   ├── layer3_execution/         # Layer 3 - Execution & Security
│   │   │   ├── tool_orchestrator.py  # chạy song song, retry, timeout
│   │   │   ├── tools/                # read_file, edit_file, run_tests, search_repo
│   │   │   ├── sandbox/
│   │   │   │   ├── docker_sandbox.py # checkout, áp patch, chạy pytest, dọn container
│   │   │   │   ├── wasm_sandbox.py   # để trống (stub)
│   │   │   │   └── policy.py         # tắt network, giới hạn CPU/RAM/thời gian
│   │   │   ├── guardrails/
│   │   │   │   ├── input_filter.py   # chống prompt injection
│   │   │   │   ├── output_filter.py  # rò rỉ dữ liệu nhạy cảm
│   │   │   │   └── validators.py     # kiểm tra JSON quyết định của LLM
│   │   │   └── cost_control.py       # trần token, số vòng tối đa (budget guard)
│   │   │
│   │   └── layer4_connectors/        # Layer 4 - Protocol Connectors
│   │       ├── base.py               # interface adapter chung
│   │       ├── mcp_client.py         # gọi MCP Server bên ngoài
│   │       └── a2a_connector.py      # ủy quyền cho agent khác (stub)
│   │
│   ├── collaboration/                # Engine điều phối nhiều agent (LangGraph)
│   │   ├── state.py                  # State dùng chung (plan, patch, message, vòng)
│   │   ├── engine.py                 # chạy graph, ghi log kích hoạt agent
│   │   ├── graph_builder.py
│   │   ├── topologies.py             # chuỗi, hình sao, Coder-Reviewer, debate
│   │   └── levels/
│   │       ├── base.py               # interface level: (task, registry, config) -> result
│   │       ├── l1_fixed.py
│   │       ├── l2_routing.py         # node router + conditional edge
│   │       ├── l3_collaboration.py   # coordinator + điều kiện dừng
│   │       └── l4_team_formation.py  # team manager thêm/bớt/thay role
│   │
│   ├── benchmark/                    # bộ chạy thực nghiệm
│   │   ├── dataset.py                # loader, chuẩn hóa schema task
│   │   ├── runner.py                 # lặp (task, level, lần lặp), song song, resume
│   │   ├── run_record.py             # schema bản ghi kết quả
│   │   ├── evaluator/
│   │   │   ├── completion.py         # task completion, test pass rate
│   │   │   ├── coverage.py           # requirement coverage (LLM giám khảo + rubric)
│   │   │   ├── quality.py            # linter, radon, kích thước patch
│   │   │   └── conflict.py
│   │   ├── analysis/
│   │   │   ├── pilot_report.py       # trung bình, độ lệch theo level
│   │   │   ├── cost_estimator.py     # ước tính chi phí full run
│   │   │   └── utility.py            # utility function chất lượng - chi phí
│   │   └── pilot_subset.py
│   │
│   └── cli.py                        # raac run / raac pilot / raac eval
│
├── docker/
│   ├── Dockerfile.sandbox            # image chạy code/test của agent
│   └── Dockerfile.bench
│
├── data/                             # task, repo cache (gitignore phần lớn)
├── results/                          # runs/*.jsonl, báo cáo pilot (gitignore)
├── tests/
│   ├── unit/                         # theo từng layer, dùng mock_llm
│   ├── integration/                  # mỗi level chạy trên 3 task mẫu
│   └── fixtures/
├── scripts/                          # freeze_config.sh, run_pilot.sh
├── docs/
│   ├── architecture.md               # sơ đồ 4 layer và collaboration
│   ├── level_definitions.md          # biến cố định/tự do ở từng level
│   ├── l4_spec.md                    # đặc tả L4 của Tuấn
│   ├── pilot_log.md                  # nhật ký lỗi pilot cho báo cáo
│   └── implementation_checklist.md   # kế hoạch 14 ngày, tiến độ và bằng chứng
├── AGENTS.md                         # hướng dẫn agent đọc rule và checklist
├── .gitignore                        # bỏ qua secret, cache và kết quả chạy
└── .agents/
    └── rules/
        └── progress.md               # cập nhật checklist khi hoàn tất công việc
~~~

README mô tả cách bắt đầu và vị trí các thành phần. [docs/architecture.md](docs/architecture.md) dành cho thiết kế kỹ thuật: trách nhiệm từng layer, luồng dữ liệu, LangGraph state/nodes/edges, checkpoint và cách các agent phối hợp. File kiến trúc đã mô tả thiết kế bốn layer; cây thư mục và tài liệu thiết kế không xác nhận các tính năng đã được hiện thực.

## Pipeline repo và test

```powershell
.venv\Scripts\Activate.ps1
raac sandbox check
raac sandbox prepare --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027 --patch candidate.patch
```

Cần Docker Desktop ở chế độ Linux containers. Cache Git nằm ở `data/repos/<owner>/<repo>.git`, kết quả ở `results/<run_id>/record.json`. Có profile cho 12 repo trong dataset; mức kiểm chứng thực tế được ghi trong báo cáo batch. Các kiểm thử cách ly đã chạy qua Docker thật. Xem [docs/sandbox.md](docs/sandbox.md) để cấu hình và kiểm thử.

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


Sandbox có quy trình chuẩn bị nhanh: `raac sandbox preflight` → `raac sandbox validate --stage pilot` → `raac sandbox validate --stage full --reuse-validated`. Chỉ full đạt đủ F2P/P2P mới có trạng thái `validated`. Build dùng cache nội bộ của builder sandbox; batch tự retention theo ngân sách mềm 30 GiB. Xem [hướng dẫn CLI, stop/resume và cache](docs/sandbox.md#chuẩn-bị-nhanh-và-retention-2026-10-17) và [bằng chứng kiểm chứng](docs/pilot_log.md).
