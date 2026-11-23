# Kế hoạch thiết kế & triển khai `Solution Architect (SA)` và `Project Manager (PM)` trong `raac.pre_executor`

Ngày lập: 2026-11-23  
Mục đích: Đặc tả chi tiết để hiện thực hai flow thiết kế cấp dự án (`solution_architect` và `project_manager`) thay thế các stub `tech_select`, `architecture`, `environment`, `task_decomposer` trong `src/raac/pre_executor/`, phù hợp với kiến trúc hai tầng bám [Proposal_DACN.pdf](../Proposal_DACN.pdf).

> **Phạm vi bàn giao:** Tài liệu này chỉ định nghĩa phần **SA** và **PM** (cùng cấu hình, schema, luật kiểm bằng code, prompt, render, CLI/TUI và test). Phần **Clarifier** và **BA** (`clarify`, `requirements`, `profile`) do thành viên khác đảm nhiệm và ghép nối; phần **Requirement Analyzer (4 điểm)**, **Controller** và vòng lặp **L1/L2/L3** nằm ở tầng downstream ngoài package `pre_executor`.

---

## 1. Bối cảnh & Kiến trúc hai tầng (Bám Proposal)

### 1.1. Tổng thể pipeline mục tiêu

```text
[Tầng 1 — Macro / Project-level (chạy 1 lần cho toàn dự án trong raac.pre_executor)]
  User Requirement
    ─► clarify ─► requirements ─► profile          (Clarifier + BA — thành viên khác giữ/ghép nối)
    ─► solution_architect                          (SA — thay tech_select + system_architect + runtime của environment)
    ─► project_manager                             (PM — thay task_decomposer, xuất tasks.json dạng DAG + context_slice)

[Tầng 2 — Micro / Per-Task Adaptive Execution Loop (ngoài pre_executor, lặp theo DAG)]
  For each T_i in tasks.json["execution_order"]:
    (T_i, T_i.context_slice, repo_snapshot_{i-1})
      ─► Requirement Analyzer (just-in-time)       → Requirement Profile 4 điểm: (Complexity, Testability, Architecture Impact, Security)
      ─► Adaptivity Controller                     → chọn L1 / L2 / L3
      ─► Collaboration Engine (L1 / L2 / L3)       → cài đặt code-level, chạy test T_i + regression test
```

### 1.2. Các quyết định thiết kế đã chốt

1. **Thay thế gọn các stub cũ trong `pre_executor`:**
   - Flow/role mới **`solution_architect`** (`SolutionArchitect`) thay cho `tech_select` (`TechSelector`) + `architecture` (`SystemArchitect`) + phần khai báo runtime/build/test mục tiêu của `environment` (`EnvironmentPlanner`).
   - Flow/role mới **`project_manager`** (`ProjectManager`) thay cho `task_decomposer` (`TaskDecomposer`).
   - **Gỡ flow `environment` riêng** (`environment_planner` + `machine.probe`) khỏi chuỗi `FLOWS` chính; việc dò/cài đặt môi trường thực tế trên máy để tầng thực thi MAS lo.
   - **Giữ nguyên hoàn toàn** code/logic của `clarify`, `requirements`, `profile` để không gây xung đột merge với thành viên phụ trách Clarifier + BA.
2. **Tương thích đầu vào Upstream (từ Clarifier + BA):**
   - SA bám trực tiếp schema đầu ra hiện có của `pre_executor` là `requirement_profile.json` (`F-n`, `FR-n`, `FR-n.ACm`, `UF-n`, `BR-n`, `EC-n`, `NFR-n`, `SC-n`, `key_entities`, `constraints`, `assumptions`, `out_of_scope`, `open_questions`), thông qua một helper đọc tập trung (`artifacts.read_ba_spec()`) để nếu sau này thành viên làm BA gộp/đổi tên sang `ba_spec.json` thì chỉ đổi một chỗ.
   - **Lưu ý thuật ngữ (tránh nhầm lẫn):** Trong `pre_executor` hiện tại, file `requirement_profile.json` là **hồ sơ đặc tả BA chi tiết** (FR/AC/UF/BR/EC). Khái niệm **Requirement Profile** ở Tầng Micro là **bộ 4 điểm** `(Complexity, Testability, Architecture Impact, Security)` *(thay trục `Ambiguity` gốc của Proposal bằng `Testability` do `Clarifier` đã xử lý mơ hồ ở Tầng Macro)* do `Requirement Analyzer` sinh ra just-in-time sau PM. Hai thứ này khác nhau hoàn toàn.
3. **Phân ranh giới Macro vs Micro (chống lặp việc & chống phá kiến trúc):**
   - **SA (Macro):** Chốt Tech Stack, cấu trúc module cấp hệ thống, Data Models dùng chung, Public/Module API Contracts, Security Architecture, Target Runtime/Test Contract và **Global Constraints bất biến**. Không đi chi tiết hàm helper nội bộ hay viết mã giả từng dòng.
   - **PM (Macro):** Chia DAG các task nhỏ `T1..Tn`, đảm bảo 100% phủ AC/Module/Security NFR, giữ nguyên `open_assumptions`, và **code Python** tự cắt lát `context_slice` trích từ BA + SA cho từng task. Không viết kế hoạch sửa từng dòng code (việc của `Planner` tầng Micro).
   - **Tầng Micro (L1/L2/L3):** Nhận `T_i` + `context_slice` + `repo_{i-1}`; các role `Analyst / Architect / Planner` bên trong L1–L3 làm ở mức **code-level** trên `repo_{i-1}`, tuân thủ Global Constraints của SA như ràng buộc chỉ-đọc, và L2 vẫn giữ đủ role optional để adaptive routing có ý nghĩa.
4. **Ranh giới PM với `Requirement Analyzer` & `Controller`:**
   - PM **tuyệt đối không** chấm 4 điểm `(Complexity, Testability, Architecture Impact, Security)`, **không** gán `suggested_level`, **không** gán `risk: low|medium|high` mang tính quyết định thay Analyzer.
   - PM chỉ xuất **metadata cấu trúc khách quan** (`kind`, `depends_on`, tham chiếu ID về BA/SA, `target_paths`, `verification_scope`, `integration_notes`, `open_assumptions`) và `context_slice` được hydrate bằng code. Việc chấm 4 điểm thuộc `Requirement Analyzer` chạy just-in-time ở từng vòng $T_i$.

---

## 2. Thay đổi tổng quan trong `raac.pre_executor` & `registry`

### 2.1. Chuỗi `FLOWS` và `APPROVED_FILE` (`src/raac/pre_executor/artifacts.py`)

```python
FLOWS = ("clarify", "requirements", "profile", "solution_architect", "project_manager")

APPROVED_FILE = {
    "clarify": "answers.md",
    "requirements": "requirements.json",
    "profile": "requirement_profile.json",
    "solution_architect": "solution_architecture.json",
    "project_manager": "tasks.json",
}
```

### 2.2. Adapter đọc đầu vào BA (`Artifacts.read_ba_spec()` trong `artifacts.py`)

Thêm hằng số và method trên `Artifacts` để tách lớp giao tiếp với phần Clarifier + BA:

```python
BA_SPEC_CANDIDATES = ("ba_spec.json", "requirement_profile.json")
```

```python
def read_ba_spec(self) -> dict | None:
    """Đọc đầu ra đã duyệt của tầng BA (ưu tiên ba_spec.json nếu thành viên BA đổi tên, mặc định requirement_profile.json)."""
    for filename in BA_SPEC_CANDIDATES:
        data = self.read_json(filename)
        if data is not None:
            return data
    return None
```

> Khi thành viên phụ trách Clarifier + BA giữ nguyên `requirements` + `profile`, `read_ba_spec()` đọc `requirement_profile.json`. Nếu sau này họ gộp thành 1 flow `ba` xuất `ba_spec.json`, chỉ cần cập nhật `FLOWS`/`APPROVED_FILE` là SA và PM chạy tiếp mà không phải sửa logic bên trong.

### 2.3. Cấu hình (`configs/pre_executor.yaml`)

```yaml
version: pre-executor-v2
# Số lần cho role chạy lại khi đầu ra sai schema hoặc luật kiểm bằng code.
max_flow_retries: 2
# Số vòng hỏi lại tối đa của flow clarify.
max_clarify_rounds: 5
# Model của từng flow; để trống = model mặc định trong configs/llm.yaml. Cờ --model ghi đè tất cả.
models:
  clarify: ""
  requirements: ""
  profile: ""
  solution_architect: ""
  project_manager: ""
```

> **Cảnh báo bắt buộc:** Không thêm model profile mới vào `configs/llm.yaml` cho các role `pre_executor`, và giữ `model.ref: default`, `cost_weight: 1.0` trong mọi file YAML thuộc `registry/roles/pre_executor/` để pass `tests/unit/registry/test_registry_loader.py` (`test_every_role_uses_only_catalog_tools_and_has_prompt`).

### 2.4. Bảng ánh xạ file tạo mới / sửa / xóa

| Hành động | Đường dẫn | Nội dung chính |
| --- | --- | --- |
| **Tạo mới** | `registry/roles/pre_executor/solution_architect.yaml` | Role `SolutionArchitect` (`id: solution_architect`, `group: pre_executor`, tools đọc repo, `max_llm_calls: 8`) |
| **Tạo mới** | `registry/prompts/pre_executor/solution_architect.md` | System prompt cho SA (xem Mục 3.4) |
| **Tạo mới** | `registry/roles/pre_executor/project_manager.yaml` | Role `ProjectManager` (`id: project_manager`, `group: pre_executor`, tools đọc repo, `max_llm_calls: 8`) |
| **Tạo mới** | `registry/prompts/pre_executor/project_manager.md` | System prompt cho PM (xem Mục 4.5) |
| **Tạo mới** | `src/raac/pre_executor/flows/solution_architect.py` | Flow SA: payload, deterministic checks, ghi `solution_architecture.json/.md`, `invalidate_from`, `summarize_changes` |
| **Tạo mới** | `src/raac/pre_executor/flows/project_manager.py` | Flow PM: payload, deterministic checks (DAG/coverage/granularity), hydrate `execution_order` + `context_slice` bằng code, ghi `tasks.json/.md` |
| **Tạo mới** | `tests/pre_executor/test_solution_architect.py` | Unit + CLI + TUI + `AgentRunner` HTTP mock test cho SA |
| **Tạo mới** | `tests/pre_executor/test_project_manager.py` | Unit + CLI + TUI + `AgentRunner` HTTP mock test cho PM |
| **Chỉnh sửa** | `src/raac/pre_executor/contracts.py` | Thêm Pydantic models cho SA và PM; đăng ký `"SolutionArchitect"` & `"ProjectManager"` vào `ROLE_OUTPUTS` (không chạm `ClarifyOutput`, `RequirementsOutput`, `ProfileOutput`) |
| **Chỉnh sửa** | `src/raac/pre_executor/artifacts.py` | Cập nhật `FLOWS`, `APPROVED_FILE`, thêm `read_ba_spec()` |
| **Chỉnh sửa** | `src/raac/pre_executor/render.py` | Thêm `render_solution_architecture(data)` và `render_tasks(data)` |
| **Chỉnh sửa** | `src/raac/pre_executor/events.py` | Thêm `CHANGE_FIELDS` và top-level diff keys cho `solution_architect` và `project_manager` |
| **Chỉnh sửa** | `src/raac/pre_executor/cli.py` | Thêm `solution_architect` và `project_manager` vào `IMPLEMENTED` |
| **Chỉnh sửa** | `src/raac/pre_executor/tui.py` | Nối `DRAFT_FLOWS`, `DRAFT_TITLES`, lưu `self.solution_architecture` và `self.project_manager` trong `PreExecutorApp` |
| **Chỉnh sửa** | `configs/pre_executor.yaml` | Cập nhật `version` và khóa `models` theo 5 flow |
| **Chỉnh sửa** | `src/raac/pre_executor/__init__.py`, `flows/__init__.py`, `README.md` | Cập nhật mô tả chuỗi 5 flow |
| **Chỉnh sửa** | `tests/pre_executor/test_clarify.py`, `tests/pre_executor/test_profile.py` | Cập nhật 2 assertion cũ trỏ tên `tech_select` (`test_clarify.py:189`, `test_profile.py:140`) |
| **Xóa** | `src/raac/pre_executor/flows/{tech_select,architecture,environment,task_decomposer}.py` và `src/raac/pre_executor/machine.py` | Xóa các stub đã bị thay thế |
| **Xóa** | `registry/roles/pre_executor/{tech_selector,system_architect,environment_planner,task_decomposer}.yaml` và các `.md` tương ứng trong `registry/prompts/pre_executor/` | Xóa role/prompt stub cũ để tránh rác registry |

> **Tuyệt đối không sửa:** `flows/clarify.py`, `flows/requirements.py`, `flows/profile.py`, `registry/{roles,prompts}/pre_executor/{clarifier,requirement_analyst,requirement_profiler}.*`, và `registry/VERSION` (giữ nguyên `0.21.0` hiện tại trừ khi nhóm thống nhất bump).

---

## 3. Thiết kế chi tiết Flow `solution_architect` (SA)

### 3.1. Thông tin định danh & Role YAML

File `registry/roles/pre_executor/solution_architect.yaml`:

```yaml
id: solution_architect
role: SolutionArchitect
group: pre_executor
prompt: prompts/pre_executor/solution_architect.md
prompt_version: pre-executor-v2
tools:
  - list_files
  - read_file
  - read_files
  - search_repo
model:
  ref: default
cost_weight: 1.0
max_llm_calls: 8
```

> Lưu ý: Role name là `SolutionArchitect` (lowercase `solutionarchitect`), hoàn toàn không đụng `CORE_ROLE_OUTPUT_MODELS` (`architect`) trong `src/raac/common/agent_contracts.py`. Không cấp tool chạy shell hay ghi file cho SA.

### 3.2. Đầu vào (`payload` truyền cho `run_role`)

- `request`: nội dung `request.md`
- `clarification`: nội dung `clarification.json` (hoặc `[]`)
- `ba_spec`: toàn bộ nội dung `artifacts.read_ba_spec()` đã duyệt (tức `requirement_profile.json`)
- `workspace_mode`: `"brownfield"` nếu workspace có file thật ngoài `.raac/` (và `.git/`), ngược lại `"greenfield"` — tính bằng code Python để prompt biết có cần đọc repo trước khi chốt stack hay không
- `user_feedback`: `artifacts.feedback_history("solution_architect")`
- `current_draft`: chỉ truyền khi `note` có giá trị và đã có `solution_architecture.json` trước đó (sửa tiếp trên bản nháp, giữ nguyên phần người dùng không yêu cầu đổi)

### 3.3. Output Contract (`contracts.py` — Pydantic `Strict`, `extra="forbid"`)

Đặt các model sau trong `src/raac/pre_executor/contracts.py` (kế thừa `Strict` với `ConfigDict(extra="forbid")`, dùng `Text`):

```python
class TechChoice(Strict):
    name: Text
    version_constraint: str = ""  # vd. ">=3.11", "^0.115", "" nếu không khóa phiên bản
    reason: Text


class TechStack(Strict):
    language: TechChoice
    backend_framework: TechChoice | None = None
    frontend_framework: TechChoice | None = None
    database: TechChoice | None = None
    libraries: list[TechChoice] = Field(default_factory=list)


class FolderSpec(Strict):
    path: Text  # đường dẫn tương đối kiểu POSIX, vd. "src/orders"
    purpose: Text


class ModuleSpec(Strict):
    id: str = Field(pattern=r"^MOD-\d+$")
    name: Text
    path: Text  # thư mục hoặc đường dẫn tương đối đại diện cho module
    responsibility: Text
    feature_ids: list[str] = Field(default_factory=list)  # F-n; cho phép rỗng với module nền tảng (vd. shared/core)
    depends_on: list[str] = Field(default_factory=list)  # MOD-n
    owns_entities: list[str] = Field(default_factory=list)  # tên thực thể chính thuộc module


class DataModelField(Strict):
    name: Text
    field_type: Text
    constraints: str = ""  # vd. "primary key", "unique, not null"


class DataModel(Strict):
    id: str = Field(pattern=r"^DM-\d+$")
    name: Text
    module_id: str = Field(pattern=r"^MOD-\d+$")
    description: Text
    fields: list[DataModelField] = Field(min_length=1)
    relations: list[str] = Field(default_factory=list)


class ApiContract(Strict):
    id: str = Field(pattern=r"^API-\d+$")
    module_id: str = Field(pattern=r"^MOD-\d+$")
    kind: Literal["http", "cli", "library", "event", "other"]
    signature: Text  # vd. "POST /api/v1/orders" hoặc "OrderService.create_order(cart_id) -> Order"
    purpose: Text
    request_or_input: str = ""
    response_or_output: str = ""
    error_cases: list[str] = Field(default_factory=list)
    fr_ids: list[str] = Field(default_factory=list)  # FR-n


class SecurityArchitecture(Strict):
    auth_strategy: str = ""  # để trống nếu sản phẩm không yêu cầu xác thực
    authorization_model: str = ""
    sensitive_data_rules: list[str] = Field(default_factory=list)
    trust_boundaries: list[str] = Field(default_factory=list)
    nfr_ids: list[str] = Field(default_factory=list)  # NFR-n liên quan bảo mật


class RuntimeContract(Strict):
    runtime_versions: dict[str, Text] = Field(min_length=1)  # vd. {"python": ">=3.11"}
    package_manager: Text  # vd. "uv", "pip", "npm", "pnpm"
    install_command: Text  # vd. "pip install -e .[dev]"
    build_command: str = ""  # rỗng nếu ngôn ngữ thông dịch không cần bước build
    run_command: str = ""  # lệnh khởi chạy app/CLI (nếu là sản phẩm có entrypoint chạy)
    test_framework: Text  # vd. "pytest", "vitest", "unittest"
    test_command: Text  # BẮT BUỘC: lệnh chạy test chuẩn cho tầng MAS & regression gate
    env_vars: dict[str, str] = Field(default_factory=dict)  # tên biến -> mô tả/mặc định phi-bí-mật


class GlobalConstraint(Strict):
    """Ràng buộc bất biến (read-only) mà tầng Micro L1–L3 không được tự ý phá vỡ."""
    id: str = Field(pattern=r"^CON-\d+$")
    category: Literal["architecture", "api", "data", "security", "testing", "convention"]
    rule: Text


class ArchitectureDecision(Strict):
    id: str = Field(pattern=r"^ADR-\d+$")
    topic: Text
    decision: Text
    alternatives_rejected: list[str] = Field(default_factory=list)
    rationale: Text


class SolutionArchitectureOutput(Strict):
    summary: Text
    tech_stack: TechStack
    folders: list[FolderSpec] = Field(min_length=1)
    modules: list[ModuleSpec] = Field(min_length=1)
    data_models: list[DataModel] = Field(default_factory=list)
    api_contracts: list[ApiContract] = Field(default_factory=list)
    security_architecture: SecurityArchitecture = Field(default_factory=SecurityArchitecture)
    runtime_contract: RuntimeContract
    global_constraints: list[GlobalConstraint] = Field(min_length=1)
    decisions: list[ArchitectureDecision] = Field(min_length=1)
    open_assumptions: list[str] = Field(default_factory=list)
```

Đăng ký trong `ROLE_OUTPUTS`:
```python
ROLE_OUTPUTS = {
    "Clarifier": ClarifyOutput,
    "RequirementAnalyst": RequirementsOutput,
    "RequirementProfiler": ProfileOutput,
    "SolutionArchitect": SolutionArchitectureOutput,
    "ProjectManager": ProjectManagerOutput,
}
```

### 3.4. Luật kiểm bằng code (`check_against(ba_spec, workspace)` trong `flows/solution_architect.py`)

Mọi vi phạm trả về `list[str]` tiếng Anh ngắn gọn, rõ ràng (để `run_role` gửi lại vào `rule_violations` cho agent tự sửa, tối đa `config.max_flow_retries` lần):

1. **ID & Tên không trùng:**
   - Không trùng ID trong `modules` (`MOD-n`), `data_models` (`DM-n`), `api_contracts` (`API-n`), `global_constraints` (`CON-n`), `decisions` (`ADR-n`).
   - Không trùng tên (chuẩn hóa lowercase) trong `modules`, `tech_stack.libraries`, `data_models`.
   - Không trùng `path` trong `folders`.
2. **Đường dẫn an toàn & tương đối (POSIX):**
   - Viết helper `_validate_relative_path(path: str) -> str | None`: từ chối chuỗi chứa `\`, bắt đầu bằng `/` hoặc `~`, chứa ổ đĩa Windows (`^[A-Za-z]:`), hoặc có phân đoạn `..` (như `../x`, `a/../b`).
   - Áp dụng cho toàn bộ `folder.path` và `module.path`.
3. **Đồ thị phụ thuộc module (`MOD-n`) hợp lệ & không vòng:**
   - Mọi `dep` trong `module.depends_on` phải là `MOD-n` tồn tại và không phải chính `module.id`.
   - Kiểm tra không có chu trình giữa các `MOD-n` (viết helper `_find_cycle(graph)` dùng chung cho cả SA và PM; báo cụ thể vòng `MOD-1 -> MOD-2 -> MOD-1`).
4. **Truy vết & độ phủ về BA spec:**
   - Mọi `fid` trong `module.feature_ids` phải tồn tại trong `ba_spec["features"]`.
   - **Mọi feature `F-n` trong `ba_spec["features"]` phải được ít nhất một `MOD-n` phụ trách.**
   - Mọi `data_model.module_id` và `api_contract.module_id` phải là `MOD-n` có thật.
   - Mọi `fr_id` trong `api_contract.fr_ids` phải tồn tại trong `ba_spec["functional_requirements"]`.
   - Mọi `nfr_id` trong `security_architecture.nfr_ids` phải tồn tại trong `ba_spec["non_functional_requirements"]`.
   - Nếu `ba_spec["non_functional_requirements"]` có bất kỳ NFR nào với `category == "security"`, thì:
     - Mọi ID NFR security đó phải xuất hiện trong `security_architecture.nfr_ids`.
     - Ít nhất một trong các trường `auth_strategy`, `authorization_model`, `sensitive_data_rules`, `trust_boundaries` phải có nội dung.
5. **Ràng buộc brownfield (nhẹ, không gây false positive):**
   - Nếu `workspace_mode == "brownfield"` (workspace có sẵn file nguồn/manifest như `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`): kiểm tra `decisions` có ít nhất 1 quyết định nhắc tới stack/cấu trúc hiện có hoặc lý do giữ/thay đổi stack không — hoặc giữ ở prompt + chỉ bắt lỗi cứng nếu SA tạo `folders` mâu thuẫn hoàn toàn đường dẫn không hợp lệ. *(Ưu tiên kiểm tra mềm ở prompt và chỉ giữ các deterministic rules chắc chắn 100% không false positive trong unit test).*
6. **Không chứa bí mật thực trong `runtime_contract.env_vars`:**
   - Cảnh báo/chặn nếu giá trị mặc định trong `env_vars` trông như API key thật (ví dụ prefix `sk-`, `ghp_`, `AIza`).

### 3.5. Prompt thiết kế (`registry/prompts/pre_executor/solution_architect.md`)

Prompt cần tuân thủ đúng văn phong và cấu trúc của `requirement_profiler.md`:
- Khai báo vai trò: *Solution Architect* chịu trách nhiệm chốt **Tech Stack**, **System Architecture (Global Contract)** và **Target Runtime/Test Contract** ở mức hệ thống (Macro) cho toàn bộ sản phẩm.
- Giải thích rõ các khóa trong `artifacts`: `request`, `clarification`, `ba_spec`, `workspace_mode`, `user_feedback`, `current_draft` (khi revise), `previous_output` + `rule_violations` (khi retry).
- Nguyên tắc chọn Tech Stack:
  - Chọn stack tối giản, phổ biến, dễ bảo trì, vừa đủ đáp ứng `ba_spec` (không chọn database/framework/library mà yêu cầu không cần).
  - Nếu `workspace_mode == "brownfield"`, dùng tools (`list_files`, `read_file`, `search_repo`) đọc codebase hiện có trước, giữ nguyên stack và layout hiện tại trừ khi yêu cầu bắt buộc phải đổi (nếu đổi phải giải thích trong `ADR-n`).
- Nguyên tắc thiết kế kiến trúc Macro:
  - Thiết kế ở mức **module boundaries, data models dùng chung, public/inter-module API contracts, security boundaries và global constraints bất biến** để các task nhỏ sau này triển khai độc lập mà không đá nhau.
  - **Không** viết chi tiết thuật toán nội bộ, tên hàm helper cục bộ hay từng bước sửa file (để tầng Micro giải quyết trên codebase thật).
  - Mọi `F-n` đều phải có `MOD-n` phụ trách; không tạo vòng phụ thuộc giữa các `MOD-n`.
  - Nếu có `NFR` thuộc `security`, phải điền đầy đủ `security_architecture` và tham chiếu đúng `NFR-n`.
  - `runtime_contract` chỉ khai báo phiên bản/lệnh chuẩn mực (install/build/run/test), không giả định đã cài trên máy; luôn có `test_command` chạy được tự động. Ghi rõ chiến lược kiểm thử ở mức hệ thống đối với DB/dịch vụ ngoài (vd. in-memory DB, mock/stub) trong `global_constraints` (`category: "testing"`).
  - Những chi tiết kỹ thuật chưa cần chốt cứng ở mức hệ thống thì ghi vào `open_assumptions` thay vì bịa đặt quá chi tiết.
- Yêu cầu ngôn ngữ: dùng ngôn ngữ của `request`, chỉ trả JSON đúng `output_schema`.

### 3.6. Lưu trữ, Render & Diff Summary

- Ghi `solution_architecture.json` (JSON trực tiếp từ `output.model_dump()`) và `solution_architecture.md` (render bằng `render_solution_architecture(data)` trong `render.py`).
- `render_solution_architecture(data)` trình bày rõ:
  1. Tổng quan kiến trúc (`summary`)
  2. Bảng Tech Stack (Thành phần | Công nghệ | Phiên bản | Lý do)
  3. Cấu trúc thư mục (`folders`)
  4. Các module (`MOD-n`: path, trách nhiệm, features, phụ thuộc, thực thể)
  5. Mô hình dữ liệu (`DM-n`)
  6. Hợp đồng giao tiếp (`API-n`)
  7. Kiến trúc bảo mật (`security_architecture`)
  8. Hợp đồng Runtime & Kiểm thử (`runtime_contract`)
  9. Ràng buộc toàn cục bất biến (`CON-n`)
  10. Quyết định kiến trúc (`ADR-n`) & Giả định kỹ thuật mở (`open_assumptions`)
- `events.py` (`CHANGE_FIELDS["solution_architect"]`):
  - Theo dõi `+ / ~ / -` theo `id` cho: `modules` (`name`), `data_models` (`name`), `api_contracts` (`signature`), `global_constraints` (`rule`), `decisions` (`topic`).
  - Theo dõi `~` ở top-level cho: `summary`, `tech_stack`, `folders`, `security_architecture`, `runtime_contract`, `open_assumptions`.
- Sau khi ghi bản nháp, gọi `gate.invalidate_from(artifacts, "solution_architect")` để `project_manager` mất duyệt nếu đã duyệt trước đó.

---

## 4. Thiết kế chi tiết Flow `project_manager` (PM)

### 4.1. Thông tin định danh & Role YAML

File `registry/roles/pre_executor/project_manager.yaml`:

```yaml
id: project_manager
role: ProjectManager
group: pre_executor
prompt: prompts/pre_executor/project_manager.md
prompt_version: pre-executor-v2
tools:
  - list_files
  - read_file
  - read_files
  - search_repo
model:
  ref: default
cost_weight: 1.0
max_llm_calls: 8
```

### 4.2. Đầu vào (`payload` truyền cho `run_role`)

- `request`: nội dung `request.md`
- `ba_spec`: nội dung `artifacts.read_ba_spec()` đã duyệt
- `solution_architecture`: nội dung `solution_architecture.json` đã duyệt
- `workspace_mode`: `"brownfield"` hoặc `"greenfield"`
- `user_feedback`: `artifacts.feedback_history("project_manager")`
- `current_draft`: chỉ truyền phần schema của `ProjectManagerOutput` (tức bỏ phần `context_slice` / `execution_order` / `global_*` do code hydrate — tương tự cách `flows/profile.py` dòng 80 lọc theo `ProfileOutput.model_fields`!) khi `note` có giá trị và đã có `tasks.json` trước đó.

> **Chi tiết quan trọng:** Lọc `current_draft` theo `ProjectManagerOutput.model_fields` (và trong mỗi task chỉ giữ các field của `TaskSpec`, bỏ `context_slice`) giúp giảm mạnh token khi `revise` và tránh việc LLM copy lại `context_slice` làm sai `extra="forbid"`.

### 4.3. Output Contract (`contracts.py` — Schema LLM trả về)

```python
TaskKind = Literal["bootstrap", "feature", "integration", "hardening"]


class Milestone(Strict):
    id: str = Field(pattern=r"^M\d+$")
    name: Text
    goal: Text
    task_ids: list[str] = Field(min_length=1)


class TaskSpec(Strict):
    """Một task nhỏ (T1, T2, ...) do PM phân rã.
    TUYỆT ĐỐI không chứa điểm Requirement Profile (4 trục), risk hay suggested_level:
    những thứ đó do Requirement Analyzer (just-in-time) và Controller quyết định.
    """
    id: str = Field(pattern=r"^T\d+$")
    title: Text
    kind: TaskKind
    goal: Text  # mục tiêu nghiệp vụ/kỹ thuật của task, không viết diff từng dòng code
    depends_on: list[str] = Field(default_factory=list)  # Tn
    feature_ids: list[str] = Field(default_factory=list)  # F-n
    fr_ids: list[str] = Field(default_factory=list)  # FR-n
    acceptance_criteria_ids: list[str] = Field(default_factory=list)  # FR-n.ACm
    edge_case_ids: list[str] = Field(default_factory=list)  # EC-n
    nfr_ids: list[str] = Field(default_factory=list)  # NFR-n
    module_ids: list[str] = Field(min_length=1)  # MOD-n
    api_contract_ids: list[str] = Field(default_factory=list)  # API-n
    data_model_ids: list[str] = Field(default_factory=list)  # DM-n
    constraint_ids: list[str] = Field(default_factory=list)  # CON-n
    target_paths: list[Text] = Field(min_length=1)  # vùng thư mục/module dự kiến chạm (tương đối)
    verification_scope: Text  # phạm vi kiểm thử cần có cho task này (kèm regression nếu có)
    integration_notes: str = ""  # cách nối với đầu ra của các task trong depends_on
    open_assumptions: list[str] = Field(default_factory=list)  # điểm kỹ thuật còn mở riêng cho task này


class ProjectManagerOutput(Strict):
    summary: Text
    milestones: list[Milestone] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)
```

> Vì `TaskSpec` kế thừa `Strict` (`extra="forbid"`), nếu model tự thêm các trường cấm như `level`, `suggested_level`, `risk`, `complexity`, `testability`, `ambiguity`, `architecture_impact`, `security`, Pydantic sẽ chặn ngay lập tức.

### 4.4. Luật kiểm bằng code (`check_against(ba_spec, sa_data, workspace_mode)` trong `flows/project_manager.py`)

1. **ID duy nhất:**
   - Không trùng `task.id` (`T1`, `T2`, ...), không trùng `milestone.id` (`M1`, ...).
   - Không trùng `task.title` (chuẩn hóa lowercase).
2. **Đồ thị phụ thuộc DAG hợp lệ:**
   - Mọi `dep` trong `task.depends_on` phải là `task.id` có thật và `dep != task.id`.
   - **Không có chu trình:** dùng `_find_cycle()` báo chính xác chu trình (vd. `T2 -> T3 -> T2`).
   - Có ít nhất 1 task gốc (`depends_on == []`).
3. **Task khởi tạo (Bootstrap) cho Greenfield:**
   - Nếu `workspace_mode == "greenfield"`:
     - Bắt buộc có ít nhất 1 task `kind == "bootstrap"` có `depends_on == []`.
     - Nếu tổng số task > 1, mọi task không phải `bootstrap` (`feature`, `integration`, `hardening`) phải phụ thuộc trực tiếp hoặc gián tiếp (reachable trong DAG) vào ít nhất một task `bootstrap` (tránh task chức năng chạy trước khi khung dự án tồn tại).
4. **Đường dẫn an toàn:**
   - Mọi đường dẫn trong `task.target_paths` phải qua `_validate_relative_path()` (tương đối, chuẩn POSIX, không `..`, không `/`, không `C:`).
5. **Tham chiếu ID hợp lệ về BA & SA:**
   - `feature_ids` ⊆ `ba_spec.features`
   - `fr_ids` ⊆ `ba_spec.functional_requirements`
   - `acceptance_criteria_ids` ⊆ tập tất cả `FR-n.ACm` trong `ba_spec`
   - `edge_case_ids` ⊆ `ba_spec.edge_cases`
   - `nfr_ids` ⊆ `ba_spec.non_functional_requirements`
   - `module_ids` ⊆ `sa_data.modules`
   - `api_contract_ids` ⊆ `sa_data.api_contracts`
   - `data_model_ids` ⊆ `sa_data.data_models`
   - `constraint_ids` ⊆ `sa_data.global_constraints`
   - **Nhất quán chéo AC ↔ FR:** nếu task liệt kê `FR-k.ACm` trong `acceptance_criteria_ids`, thì `FR-k` phải có trong `task.fr_ids` (tránh lệch giữa FR và AC).
   - **Nhất quán Milestone (nếu có):** mọi `task_id` trong `milestone.task_ids` phải tồn tại trong `tasks`, và mỗi task không xuất hiện ở quá 1 milestone.
6. **Độ phủ bắt buộc (100% Traceability):**
   - **100% `FR-n.ACm`** của `ba_spec` phải xuất hiện trong `acceptance_criteria_ids` của ít nhất 1 task.
   - **100% `MOD-n`** của `sa_data` phải xuất hiện trong `module_ids` của ít nhất 1 task.
   - **100% `NFR-n` thuộc `category == "security"`** phải xuất hiện trong `nfr_ids` của ít nhất 1 task.
   - **100% `edge_cases` của các feature `priority == "high"`** phải xuất hiện trong `edge_case_ids` của ít nhất 1 task.
7. **Kiểm tra độ mịn (Anti-Monolith Granularity Guard):**
   - Nếu `len(ba_spec["features"]) >= 3` và `len(ba_spec["functional_requirements"]) >= 4`: yêu cầu `len(output.tasks) >= 2` (không được gộp toàn bộ sản phẩm thành 1 task duy nhất).
   - Mỗi task có `kind == "feature"` phải có ít nhất 1 `fr_ids` hoặc 1 `acceptance_criteria_ids`.

### 4.5. Hydration bằng code Python (Tạo `tasks.json` hoàn chỉnh)

Sau khi `run_role` trả về `output: ProjectManagerOutput` đã pass toàn bộ kiểm tra, code trong `flows/project_manager.py` **tự động thực hiện bằng Python thuần** (không tốn token LLM, không sai lệch dữ liệu):

1. **Tính `execution_order` (Topological Sort deterministic):**
   - Dùng thuật toán Kahn kết hợp `heapq` theo cặp `(số thứ tự trong T<n>, task_id)` để luôn cho thứ tự thực thi tái lập 100% giữa các lần chạy.
2. **Cắt lát ngữ cảnh (`build_context_slice(task, ba_spec, sa_data)`):**
   Với từng `task` $T_i$, trích xuất chính xác các mục mà $T_i$ tham chiếu:
   ```json
   {
     "features": ["... các object F-n trong task.feature_ids ..."],
     "functional_requirements": ["... các object FR-n trong task.fr_ids (lọc/giữ đầy đủ AC liên quan) ..."],
     "business_rules": ["... các BR-n có feature_id nằm trong task.feature_ids ..."],
     "edge_cases": ["... các EC-n trong task.edge_case_ids ..."],
     "non_functional_requirements": ["... các NFR-n trong task.nfr_ids ..."],
     "modules": ["... các MOD-n trong task.module_ids ..."],
     "api_contracts": ["... các API-n trong task.api_contract_ids ..."],
     "data_models": ["... các DM-n trong task.data_model_ids ..."],
     "global_constraints": [
       "... các CON-n trong task.constraint_ids + mọi CON-n thuộc category 'architecture' hoặc 'convention' áp dụng chung ..."
     ],
     "security_architecture": "... object security_architecture của SA nếu task có NFR security hoặc chạm module auth, ngược lại null ...",
     "relevant_assumptions": ["... các assumptions từ BA + task.open_assumptions ..."]
   }
   ```
3. **Cấu trúc cuối cùng của file `.raac/pre_executor/tasks.json`:**
   ```json
   {
     "schema_version": "tasks-v2",
     "summary": "...",
     "workspace_mode": "greenfield",
     "execution_order": ["T1", "T2", "T3"],
     "global_tech_stack": { "... chép nguyên từ solution_architecture.json ..." },
     "global_runtime_contract": { "... chép nguyên từ solution_architecture.json ..." },
     "milestones": [ "..." ],
     "tasks": [
       {
         "id": "T1",
         "title": "...",
         "kind": "bootstrap",
         "goal": "...",
         "depends_on": [],
         "feature_ids": [],
         "fr_ids": [],
         "acceptance_criteria_ids": [],
         "edge_case_ids": [],
         "nfr_ids": [],
         "module_ids": ["MOD-1"],
         "api_contract_ids": [],
         "data_model_ids": [],
         "constraint_ids": ["CON-1"],
         "target_paths": ["src", "tests"],
         "verification_scope": "...",
         "integration_notes": "...",
         "open_assumptions": [],
         "context_slice": { "..." }
       }
     ],
     "open_questions": ["..."]
   }
   ```

### 4.6. Prompt thiết kế (`registry/prompts/pre_executor/project_manager.md`)

Prompt cần nhấn mạnh các nguyên tắc cốt lõi sau:
- Khai báo vai trò: *Project Manager* chia dự án đã chốt kiến trúc (`solution_architecture`) và đặc tả (`ba_spec`) thành một **DAG các task nhỏ (`T1`, `T2`, ...)** để đội kỹ thuật thực thi lần lượt từng task.
- Giải thích rõ các khóa trong `artifacts`: `request`, `ba_spec`, `solution_architecture`, `workspace_mode`, `user_feedback`, `current_draft`, `previous_output` + `rule_violations`.
- Quy tắc phân rã task:
  - Mỗi task là một lát cắt dọc/đơn vị công việc **đủ nhỏ, tự nhất quán và kiểm thử được độc lập** trong 1 phiên chạy agent. Ưu tiên nhiều task nhỏ rõ ranh giới thay vì ít task khổng lồ.
  - Với `greenfield`, luôn có task `bootstrap` (`depends_on: []`) đầu tiên để dựng khung thư mục, dependency và test runner; các task sau phụ thuộc vào nó.
  - `depends_on` chỉ khai báo phụ thuộc kỹ thuật thực sự (dùng model/API/module của task trước); **không tạo chu trình**.
  - Phủ **100%** `FR-n.ACm`, **100%** `MOD-n`, **100%** `NFR-n` bảo mật và `edge_cases` của feature ưu tiên cao. Nếu đưa `FR-k.ACm` vào `acceptance_criteria_ids` thì phải đưa `FR-k` vào `fr_ids`.
  - **Ranh giới Macro vs Micro:** chỉ mô tả *mục tiêu, hợp đồng cần tuân thủ, vùng thư mục (`target_paths`) và phạm vi kiểm thử (`verification_scope` — nêu rõ loại kiểm thử cần thiết như unit test thuần logic, fixture DB/file, mock API ngoài, tích hợp nhiều module, test UI và regression với `depends_on`)*. Không kê chi tiết từng hàm nội bộ hay diff dòng code.
  - **Không tự đánh giá level hay chấm điểm:** không tự xếp loại L1/L2/L3, không tự chấm 4 trục (Complexity/Testability/Architecture Impact/Security), không tự thêm trường `risk` hay `suggested_level`.
  - **Bảo tồn `open_assumptions`:** nếu một task còn điểm kỹ thuật chưa chốt (ví dụ từ `ba_spec.open_questions` hoặc `solution_architecture.open_assumptions`), ghi trung thực vào `task.open_assumptions` thay vì tự bịa giải pháp thay developer.
- Ngôn ngữ theo `request`, chỉ trả về JSON đúng `output_schema`.

### 4.7. Render & Diff Summary

- `render_tasks(data)` trong `render.py` in ra `tasks.md` gồm:
  1. Tóm tắt kế hoạch (`summary`), chế độ workspace, lệnh test chuẩn (`global_runtime_contract.test_command`)
  2. Thứ tự thực thi topo (`execution_order`: `T1 → T2 → ...`)
  3. Bảng tổng quan Task (ID | Loại | Tiêu đề | Phụ thuộc | Modules | Số AC)
  4. Chi tiết từng task (mục tiêu, truy vết `F/FR/AC/EC/NFR/MOD/API/DM/CON`, vùng path, phạm vi test, ghi chú tích hợp, `open_assumptions`)
  5. Ma trận truy vết phủ AC (`FR-n.ACm → [Tn]`) để người dùng dễ kiểm soát trước khi bấm Approve
  6. Câu hỏi mở (`open_questions`)
- `events.py` (`CHANGE_FIELDS["project_manager"]`):
  - Theo dõi `+ / ~ / -` theo `id` cho: `tasks` (`title`), `milestones` (`name`).
  - Theo dõi `~` ở top-level cho: `summary`, `execution_order`, `open_questions`.
  - Lưu ý khi so diff trong `flows/project_manager.py`: so `tasks` sau khi bỏ `context_slice` (hoặc so trên `TaskSpec`) để thay đổi nội dung thực của task hiện rõ, không bị nhiễu.

---

## 5. Hợp đồng bàn giao Downstream (PM → Vòng lặp `Requirement Analyzer → Controller → L1/L2/L3`)

Phần này **không triển khai trong phạm vi `pre_executor`**, nhưng định nghĩa rõ giao diện để thành viên làm vòng lặp MAS / `Requirement Analyzer` ghép trực tiếp vào `tasks.json`:

1. **Cách vòng lặp downstream đọc `tasks.json`:**
   - Chỉ đọc khi `gate.state(artifacts, "project_manager") == "approved"`.
   - Duyệt lần lượt `task_id` theo `tasks_data["execution_order"]`.
2. **Chạy `Requirement Analyzer` just-in-time (tại đầu vòng thứ $i$):**
   - Input cho `Requirement Analyzer` gồm 3 mảnh:
     1. `task_i` (metadata khách quan từ `tasks.json`, gồm `goal`, `kind`, `depends_on`, `target_paths`, `open_assumptions`, các ID tham chiếu).
     2. `task_i["context_slice"]` (ngữ cảnh BA + SA đã cắt lát đúng phạm vi $T_i$).
     3. `repo_snapshot_{i-1}` (cây thư mục hiện tại sau $T_{i-1}$ + tóm tắt public interface/schema của các module trong `depends_on` + ghi chú sai lệch nếu có từ các vòng trước).
   - Output của `Requirement Analyzer`: **Requirement Profile 4 điểm** `(Complexity, Testability, Architecture Impact, Security)` → đưa vào `Adaptivity Controller` chọn `L1 / L2 / L3`.
3. **Ràng buộc cho tầng Micro (L1/L2/L3):**
   - `context_slice["global_constraints"]`, `context_slice["api_contracts"]`, `context_slice["data_models"]` và `global_runtime_contract` là **read-only constraints** — tầng Micro không được tự ý sửa vỡ hợp đồng toàn cục; nếu xung đột với code thực tế phải báo conflict/escalate.
   - Cuối mỗi vòng $T_i$, gate kiểm tra chạy test của $T_i$ + regression test bằng `global_runtime_contract["test_command"]`.
4. **Chuẩn bị cho thực nghiệm đối chứng theo Proposal (Mục 7.2 & 7.4):**
   - Khi so sánh `all-L1`, `all-L2`, `all-L3` và `RAAC (Analyzer + Controller)` trên cùng một case study dự án, **đóng băng toàn bộ thư mục `.raac/pre_executor/` tới `tasks.json` đã duyệt** để cả 4 cấu hình dùng chung đúng một bộ đầu vào, đảm bảo công bằng và tái lập.

---

## 6. Tích hợp CLI & TUI

### 6.1. CLI (`src/raac/pre_executor/cli.py`)

- Import `project_manager`, `solution_architect` từ `.flows`.
- Cập nhật:
  ```python
  IMPLEMENTED = {
      "clarify": clarify,
      "requirements": requirements,
      "profile": profile,
      "solution_architect": solution_architect,
      "project_manager": project_manager,
  }
  ```
- Tự động hỗ trợ đủ các lệnh:
  - `python -m raac.pre_executor solution_architect --workspace W`
  - `python -m raac.pre_executor revise solution_architect --workspace W --note "..."`
  - `python -m raac.pre_executor approve solution_architect --workspace W`
  - `python -m raac.pre_executor review solution_architect --workspace W`
  - Tương tự cho `project_manager` và `status` / `tui`.

### 6.2. TUI (`src/raac/pre_executor/tui.py`)

- Cập nhật `DRAFT_FLOWS`:
  ```python
  DRAFT_FLOWS = (
      ("requirements", "Tiếp: Requirement Profile"),
      ("profile", "Tiếp: Solution Architect"),
      ("solution_architect", "Tiếp: Project Manager"),
      ("project_manager", None),
  )
  ```
- Cập nhật `DRAFT_TITLES`:
  ```python
  DRAFT_TITLES = {
      "requirements": ("Yêu cầu sản phẩm", lambda data: f"{len(data['features'])} chức năng cốt lõi, "
                                                        f"{len(data['non_functional_requirements'])} NFR"),
      "profile": ("Hồ sơ yêu cầu", lambda data: f"{len(data['user_flows'])} user flow, "
                                               f"{len(data['functional_requirements'])} yêu cầu chi tiết, "
                                               f"{sum(len(fr['acceptance_criteria']) for fr in data['functional_requirements'])} AC"),
      "solution_architect": ("Kiến trúc giải pháp", lambda data: f"{data['tech_stack']['language']['name']}, "
                                                                 f"{len(data['modules'])} module, "
                                                                 f"{len(data['api_contracts'])} hợp đồng API, "
                                                                 f"{len(data['global_constraints'])} ràng buộc"),
      "project_manager": ("Kế hoạch công việc (PM)", lambda data: f"{len(data['tasks'])} task, "
                                                                  f"{len(data.get('milestones', []))} milestone"),
  }
  ```
- Trong `PreExecutorApp.__init__`: bổ sung `self.solution_architecture: dict | None = None` và `self.project_manager: dict | None = None`.
- Trong `PreExecutorApp.session()`: khi dừng giữa chừng hoặc hoàn tất, `self.exit(self.project_manager or self.solution_architecture or self.profile or self.requirements)`.

---

## 7. Thứ tự triển khai đề xuất (cho Claude)

1. **Bước 1 — Dọn stub & cập nhật khung chung:**
   - Xóa các stub cũ (`tech_select.py`, `architecture.py`, `environment.py`, `task_decomposer.py`, `machine.py` và 4 cặp role/prompt tương ứng).
   - Cập nhật `artifacts.py` (`FLOWS`, `APPROVED_FILE`, `read_ba_spec()`), `configs/pre_executor.yaml`, `events.py`.
2. **Bước 2 — Contracts & Shared Validators:**
   - Thêm đầy đủ Pydantic models của SA và PM vào `contracts.py`, đăng ký `SolutionArchitect` và `ProjectManager` vào `ROLE_OUTPUTS`.
   - Thêm helper dùng chung (ví dụ đặt trong `flows/base.py` hoặc module helper nội bộ `flows/_checks.py`): `_duplicates()`, `_validate_relative_path()`, `_find_cycle()`, `_workspace_mode(workspace)`.
3. **Bước 3 — Registry Roles & Prompts:**
   - Tạo `solution_architect.yaml/.md` và `project_manager.yaml/.md` đúng chuẩn registry (`model.ref: default`, `cost_weight: 1.0`, tools đọc repo).
4. **Bước 4 — Flow `solution_architect` + Render + Tests:**
   - Hiện thực `flows/solution_architect.py` và `render_solution_architecture()` trong `render.py`.
   - Viết đầy đủ `tests/pre_executor/test_solution_architect.py`.
5. **Bước 5 — Flow `project_manager` + Hydration + Render + Tests:**
   - Hiện thực `flows/project_manager.py` (gồm DAG check, coverage check, Kahn topological sort, `build_context_slice`) và `render_tasks()` trong `render.py`.
   - Viết đầy đủ `tests/pre_executor/test_project_manager.py`.
6. **Bước 6 — Nối CLI, TUI, Docs & Sửa 2 assertion test cũ:**
   - Cập nhật `cli.py`, `tui.py`, `__init__.py`, `flows/__init__.py`, `README.md`.
   - Cập nhật assertion ở `tests/pre_executor/test_clarify.py` (dòng 189) và `tests/pre_executor/test_profile.py` (dòng 140) từ `tech_select` sang `solution_architect` (và điều chỉnh kiểm tra `status` phù hợp khi cả 5 flow đều đã triển khai).
7. **Bước 7 — Kiểm thử & đối chiếu cuối:**
   - Chạy toàn bộ test và kiểm tra `ruff` / `git diff --check`.

---

## 8. Kế hoạch kiểm thử & Tiêu chí nghiệm thu

### 8.1. Test bắt buộc cho `solution_architect` (`tests/pre_executor/test_solution_architect.py`)

- **Gate:** từ chối chạy khi `profile` chưa duyệt (`draft`/`missing`/`stale`); chạy được khi `profile` đã `approved`; đọc được cả `ba_spec.json` lẫn `requirement_profile.json` qua `read_ba_spec()`.
- **Schema (`extra="forbid"`):** từ chối field thừa, từ chối ID sai regex (`MOD-x`, `API-x`), từ chối thiếu `runtime_contract.test_command` hoặc thiếu `tech_stack.language.reason`.
- **Deterministic rules (parametrize — vi phạm → gửi lại qua `rule_violations` → sửa thành công ở lượt kế tiếp):**
  - Trùng ID `MOD-1`, `API-1`, `DM-1`, `CON-1`, `ADR-1`; trùng tên module hoặc thư viện.
  - Đường dẫn không hợp lệ (`/abs/path`, `../escape`, `C:\win`, `a\b`).
  - `module.feature_ids` trỏ tới `F-99` không tồn tại.
  - Feature `F-2` chưa có module nào phụ trách.
  - `module.depends_on` trỏ `MOD-99` không tồn tại, tự phụ thuộc `MOD-1 -> MOD-1`, hoặc có vòng `MOD-1 -> MOD-2 -> MOD-1`.
  - `api_contract.module_id` hoặc `fr_ids` không tồn tại; `data_model.module_id` không tồn tại.
  - Có `NFR` loại `security` trong BA spec nhưng `security_architecture` bỏ sót ID NFR đó hoặc để trống toàn bộ nội dung bảo mật.
  - `env_vars` chứa chuỗi giống secret (`sk-...`).
- **Revise & Invalidation:** góp ý gửi kèm `current_draft` + `user_feedback`, trả `changes` từ `summarize_changes`, ghi đè `.json`/`.md`, làm mất duyệt `project_manager` nếu đã duyệt.
- **CLI & TUI & `AgentRunner` thật (HTTP mock):** chạy `solution_architect` → `revise` → `approve`; kiểm tra chạy qua `open_runtime` + `AgentRunner` thật bằng `httpx.MockTransport` và sinh log JSONL hợp lệ.

### 8.2. Test bắt buộc cho `project_manager` (`tests/pre_executor/test_project_manager.py`)

- **Gate:** từ chối chạy khi `solution_architect` chưa duyệt hoặc bị `stale`; mất duyệt ngay khi `solution_architect` bị `revise`.
- **Schema (`extra="forbid"`):** từ chối mọi trường rò rỉ thẩm quyền Analyzer/Controller (`suggested_level`, `level`, `risk`, `complexity`, `testability`, `ambiguity`, `architecture_impact`, `security`).
- **Deterministic rules (parametrize — vi phạm → retry → pass):**
  - Trùng ID `T1`, trùng tiêu đề task.
  - `depends_on` trỏ task không tồn tại, tự phụ thuộc, **chu trình 2-node và 3-node**.
  - Greenfield thiếu task `kind == "bootstrap"` có `depends_on == []`, hoặc có task `feature` không phụ thuộc (trực tiếp/gián tiếp) vào `bootstrap`.
  - Đường dẫn `target_paths` không hợp lệ (`../x`, `/etc`).
  - Tham chiếu sai `feature_ids`, `fr_ids`, `acceptance_criteria_ids`, `edge_case_ids`, `nfr_ids`, `module_ids`, `api_contract_ids`, `data_model_ids`, `constraint_ids`.
  - Lệch chéo AC ↔ FR (có `FR-2.AC1` nhưng thiếu `FR-2` trong `fr_ids`).
  - Thiếu độ phủ: bỏ sót một `FR-n.ACm`, bỏ sót một `MOD-n`, bỏ sót một `NFR` security, bỏ sót `EC-n` của feature `high`.
  - Anti-monolith: gom toàn bộ dự án có ≥ 3 feature và ≥ 4 FR vào đúng 1 task.
- **Deterministic Hydration (`tasks.json`):**
  - `execution_order` đúng thứ tự topo và tái lập (deterministic) kể cả khi thứ tự mảng `tasks` trong JSON đầu ra LLM bị xáo trộn.
  - `context_slice` của `T_i` chỉ chứa đúng các đối tượng mà `T_i` tham chiếu + `global_constraints` chung, khớp nguyên văn với `ba_spec` và `solution_architecture.json`.
  - `global_tech_stack` và `global_runtime_contract` được chép nguyên từ `solution_architecture.json`.
  - Khi `revise`, `current_draft` gửi lên LLM **không** chứa `context_slice` / `execution_order`.
- **CLI & TUI & `AgentRunner` thật (HTTP mock):** chạy trọn chuỗi TUI `requirements → profile → solution_architect → project_manager` với `FakeRuntime`, kiểm tra `tasks.json` và `tasks.md` đầy đủ; chạy qua `open_runtime` + `AgentRunner` thật với HTTP mock.

### 8.3. Lệnh kiểm chứng sau khi Claude hiện thực xong

```bash
python -m pytest tests/pre_executor/ -q
python -m pytest tests/unit/registry/test_registry_loader.py -k "not test_version_file_exists_and_is_semver and not test_stamp_record_adds_version_without_mutating_input and not test_stamp_record_rejects_mixed_versions" -q
```

> Lưu ý: 3 test kiểm cứng chuỗi `"0.19.6"` trong `tests/unit/registry/test_registry_loader.py` đã lệch sẵn với `registry/VERSION` (`0.21.0`) từ trước khi triển khai kế hoạch này; không tự ý hạ `registry/VERSION` về `0.19.6`. Mọi test còn lại của `test_registry_loader.py` (bao gồm `test_real_registry_loads` và `test_every_role_uses_only_catalog_tools_and_has_prompt`) và 100% `tests/pre_executor/` **bắt buộc phải pass**.
