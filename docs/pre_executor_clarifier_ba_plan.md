# Kế hoạch chỉnh sửa & hợp nhất `Clarifier` và `Business Analyst (BA)` trong `raac.pre_executor`

Ngày lập: 2026-11-23  
Phụ thuộc: Tiếp nối [pre_executor_sa_pm_plan.md](pre_executor_sa_pm_plan.md) (SA và PM đã được triển khai trong `src/raac/pre_executor/`).  
Mục đích: Đặc tả chi tiết để nâng cấp `clarify`, hợp nhất `requirements` + `profile` thành một flow `business_analyst` duy nhất xuất `ba_spec.json/.md`, xóa triệt để sự trùng tên với **Requirement Profile 4 điểm** của [Proposal_DACN.pdf](../Proposal_DACN.pdf), và tối ưu hóa dữ liệu đầu vào cho `solution_architect` (SA), `project_manager` (PM) và tầng Micro (`Requirement Analyzer → Controller → L1/L2/L3`).

---

## 1. Bối cảnh & Mục tiêu thay đổi

### 1.1. Hiện trạng `raac.pre_executor` trước kế hoạch này

Hiện tại `FLOWS` trong `src/raac/pre_executor/artifacts.py` đang có 5 bước:

```text
clarify ─► requirements ─► profile ─► solution_architect ─► project_manager
```

Trong đó có 5 điểm nghẽn khi toàn bộ Tầng Macro do cùng một quy trình kiểm soát:

1. **Trùng tên trực diện với Proposal:** Flow `profile` xuất `requirement_profile.json` (chứa User Flow, Business Rules, FR, AC, Edge Cases) — trùng tên với **Requirement Profile 4 điểm** `(Complexity, Testability, Architecture Impact, Security)` *(đã thay trục `Ambiguity` gốc của [Proposal_DACN.pdf](../Proposal_DACN.pdf) bằng `Testability` do `Clarifier` đã xử lý độ mơ hồ đầu vào)* do `Requirement Analyzer` sinh ra ở Tầng Micro.
2. **Dư 1 vòng agent & 1 lần người dùng approve:** Tách BA thành `requirements` + `profile` buộc người dùng duyệt 4 lần (requirements → profile → SA → PM) và phải gửi lặp toàn bộ `request + clarification + requirements` cho flow `profile`.
3. **Thiếu phạm vi gắn kết theo `feature_id` ở BA:** `NonFunctionalRequirement`, `Assumption`, và `open_questions` đang nằm phẳng ở cấp toàn cục. Vì vậy `project_manager.py` (`build_context_slice`) hiện buộc phải chép **toàn bộ** `ba_spec["assumptions"]` vào mọi task $T_i$, làm loãng ngữ cảnh và làm mờ tín hiệu `Testability`/`Security` của từng task khi đưa sang `Requirement Analyzer`.
4. **Thiếu định danh chuẩn cho Thực thể và Tích hợp ngoài:** `KeyEntity` chưa có ID (`ENT-n`), và nhóm `integrations` mà `Clarifier` đã hỏi lại chưa có trường riêng ở đầu ra BA (`INT-n`), khiến SA không kiểm soát được độ phủ thực thể/tích hợp bằng luật code.
5. **Thiếu chế độ chốt tự động ở `Clarifier` & lọc từ quá gắt ở `requirements`:**
   - `Clarifier` chưa có cờ `--auto-assume` để chạy tự động trên tập dự án mẫu khi làm thực nghiệm đối chứng, và chưa nhận `workspace_mode`.
   - `IMPLEMENTATION_WORDS` trong `requirements.py` hiện chặn cả từ chung như `api`, `endpoint`, `server`, gây báo lỗi oan khi yêu cầu sản phẩm chính là xây REST API hoặc CLI/Server.

### 1.2. Chuỗi Tầng Macro mục tiêu sau khi triển khai

```text
User Requirement
  ─► clarify            (Clarifier → clarification.json + coverage.json)
  ─► business_analyst   (BusinessAnalyst → ba_spec.json + ba_spec.md)
  ─► solution_architect (SolutionArchitect → solution_architecture.json + .md)
  ─► project_manager    (ProjectManager → tasks.json + .md)
```

---

## 2. Bản đồ file cần tạo mới, chỉnh sửa và xóa

| Hành động | Đường dẫn | Nội dung chính |
| --- | --- | --- |
| **Tạo mới** | `registry/roles/pre_executor/business_analyst.yaml` | Role `BusinessAnalyst` (`id: business_analyst`, `group: pre_executor`, tools đọc repo, `model.ref: default`, `cost_weight: 1.0`, `max_llm_calls: 10`) |
| **Tạo mới** | `registry/prompts/pre_executor/business_analyst.md` | Prompt hợp nhất BA (xem Mục 4.4) |
| **Tạo mới** | `src/raac/pre_executor/flows/business_analyst.py` | Flow `business_analyst`: schema, deterministic checks, ghi `ba_spec.json/.md`, `invalidate_from`, `summarize_changes` |
| **Tạo mới** | `tests/pre_executor/test_business_analyst.py` | Bộ test đầy đủ cho `business_analyst` (thay thế `test_requirements.py` và `test_profile.py`) |
| **Chỉnh sửa** | `src/raac/pre_executor/flows/clarify.py` | Truyền `workspace_mode`, thêm tham số `auto_assume: bool = False` để tự chốt bằng `default_answer` trong chế độ tự động |
| **Chỉnh sửa** | `registry/prompts/pre_executor/clarifier.md` | Cập nhật tên chuỗi vai trò mới; gỡ mâu thuẫn về ràng buộc nền tảng/môi trường cứng; thêm hướng dẫn `workspace_mode` |
| **Chỉnh sửa** | `registry/roles/pre_executor/clarifier.yaml` | Tăng `prompt_version: pre-executor-v4` |
| **Chỉnh sửa** | `src/raac/pre_executor/contracts.py` | Thay `RequirementsOutput` + `ProfileOutput` bằng `BusinessAnalystOutput` (thêm `ENT-n`, `INT-n`, `OQ-n`, `NFR.feature_ids`, `Assumption.feature_ids`); thêm field optional nhẹ ở SA (`DataModel.entity_ids`, `ApiContract.integration_ids`); cập nhật `ROLE_OUTPUTS` |
| **Chỉnh sửa** | `src/raac/pre_executor/artifacts.py` | Cập nhật `FLOWS` (4 bước) và `APPROVED_FILE` (`business_analyst: ba_spec.json`); giữ `BA_SPEC_CANDIDATES = ("ba_spec.json", "requirement_profile.json")` |
| **Chỉnh sửa** | `src/raac/pre_executor/flows/solution_architect.py` | Khớp nối kiểm tra `ENT-n` và `INT-n` khi `ba_spec` có khai báo; giữ tương thích ngược khi `key_entities` thiếu `id` |
| **Chỉnh sửa** | `registry/prompts/pre_executor/solution_architect.md` | Nhắc SA map `ENT-n` vào `DataModel.entity_ids` / `Module.owns_entities` và `INT-n` vào `ApiContract.integration_ids` |
| **Chỉnh sửa** | `src/raac/pre_executor/flows/project_manager.py` | Nâng cấp `build_context_slice()` lọc `assumptions`, `open_questions` (`OQ-n`) và `external_integrations` (`INT-n`) **theo `feature_ids`**; kiểm tra NFR security có `feature_ids` được gắn đúng task liên quan |
| **Chỉnh sửa** | `src/raac/pre_executor/render.py` | Thay `render_requirements` + `render_profile` bằng `render_ba_spec(data)` (hiển thị đầy đủ `ENT-n`, `INT-n`, `F-n`, `FR-n/AC`, `UF-n`, `BR-n`, `EC-n`, `NFR-n`, `Assumptions`, `OQ-n`) |
| **Chỉnh sửa** | `src/raac/pre_executor/events.py` | Thay entry `"requirements"` & `"profile"` trong `CHANGE_FIELDS` bằng `"business_analyst"` |
| **Chỉnh sửa** | `src/raac/pre_executor/cli.py` | Cập nhật `IMPLEMENTED` (4 flow); thêm cờ `--auto-assume` cho lệnh `clarify` |
| **Chỉnh sửa** | `src/raac/pre_executor/tui.py` | Cập nhật `DRAFT_FLOWS` (3 bước duyệt: `business_analyst → solution_architect → project_manager`), `DRAFT_TITLES`, `STATE_ATTR`, `PreExecutorApp` (`self.ba_spec`) |
| **Chỉnh sửa** | `configs/pre_executor.yaml` | Cập nhật `version: pre-executor-v3` và 4 khóa dưới `models:` (`clarify`, `business_analyst`, `solution_architect`, `project_manager`) |
| **Chỉnh sửa** | `src/raac/pre_executor/__init__.py`, `flows/__init__.py`, `README.md` | Cập nhật mô tả chuỗi 4 flow chuẩn mới |
| **Chỉnh sửa** | `tests/pre_executor/pe_helpers.py`, `test_clarify.py`, `test_events.py`, `test_tui.py`, `test_solution_architect.py`, `test_project_manager.py` | Đồng bộ fixture `BA_SPEC` mới (`ENT-n`, `INT-n`, `OQ-n`, `feature_ids`), đơn giản hóa `approve_ba_spec`, bổ sung test `--auto-assume` và context slicing theo `feature_ids` |
| **Xóa** | `src/raac/pre_executor/flows/requirements.py`, `src/raac/pre_executor/flows/profile.py` | Thay bằng `flows/business_analyst.py` |
| **Xóa** | `registry/roles/pre_executor/{requirement_analyst,requirement_profiler}.yaml` và `registry/prompts/pre_executor/{requirement_analyst,requirement_profiler}.md` | Thay bằng `business_analyst.yaml/.md` |
| **Xóa** | `tests/pre_executor/test_requirements.py`, `tests/pre_executor/test_profile.py` | Thay bằng `tests/pre_executor/test_business_analyst.py` |

> **Cảnh báo registry bắt buộc:** Không thêm model profile vào `configs/llm.yaml`; mọi file role mới dưới `registry/roles/pre_executor/` phải giữ `model.ref: default`, `cost_weight: 1.0`, chỉ dùng tool có trong `registry/tools.yaml` (`list_files`, `read_file`, `read_files`, `search_repo`), và không tự ý hạ `registry/VERSION`.

---

## 3. Thiết kế nâng cấp Flow `clarify` (Clarifier)

### 3.1. Bổ sung `workspace_mode` trong `flows/clarify.py`

Import `workspace_mode` từ `._checks` và truyền `"workspace_mode": workspace_mode(artifacts.workspace)` vào payload gọi `run_role`. Điều này đồng bộ payload giữa cả 4 flow Macro (`clarify`, `business_analyst`, `solution_architect`, `project_manager`).

### 3.2. Thêm chế độ chốt tự động `--auto-assume` cho thực nghiệm tự động

**Mục đích:** Khi chạy benchmark/case study hoặc demo batch không muốn trả lời từng câu bằng tay, lệnh CLI cho phép tự chấp nhận `default_answer` (đánh dấu `assumed: True` trung thực) và chạy qua các vòng hỏi cho đến khi `finished(artifacts, config)` (tối đa `max_clarify_rounds` vòng) rồi tự gọi `_done()`.

- **Trong `flows/clarify.py`:**
  - Chữ ký hàm `run`:
    ```python
    async def run(artifacts: Artifacts, runtime, config, *, note: str | None = None,
                  auto_assume: bool = False) -> dict:
    ```
  - Khi `auto_assume=False` (mặc định — giữ nguyên 100% hành vi hiện có cho CLI và TUI): chạy đúng 1 vòng và trả `status="questions"` hoặc `status="done"`.
  - Khi `auto_assume=True`:
    - Lặp trong khi chưa `finished(artifacts, config)`:
      - Gọi `run_role` sinh vòng câu hỏi, ghi `clarify.json` và `answers.md` (vốn đã điền sẵn `default_answer` cho mọi câu mới, nên khi đọc lại `clarification()` mọi câu chưa sửa đều có `assumed=True`).
      - Nếu vòng đó không có câu hỏi hoặc `output.complete` là `True` hoặc đã chạm `config.max_clarify_rounds` → gọi `_done(artifacts, config)` và trả về `status="done"` kèm `auto_assumed: True`.
- **Trong `src/raac/pre_executor/cli.py`:**
  - Trên subparser `clarify`, thêm cờ `--auto-assume` (`action="store_true"`, `help="tự động dùng phương án khuyến nghị qua mọi vòng và chốt clarify (dùng cho chạy tự động/benchmark)"`).
  - Trong `_run_flow`: truyền `auto_assume=auto_assume` chỉ khi `flow == "clarify"` (hoặc truyền qua `**flow_kwargs`).

### 3.3. Chỉnh sửa Prompt `registry/prompts/pre_executor/clarifier.md`

Giữ nguyên 10 nhóm độ phủ Spec Kit và toàn bộ luật hiện có, cập nhật 3 điểm cụ thể:

1. **Dòng 2–3 và dòng 36–37:** Đổi `"The Requirement Analyst and the Requirement Profiler will later write..."` thành `"The Business Analyst will write the detailed specification (features, user flows, functional requirements and acceptance criteria), the Solution Architect will design the tech stack and architecture, and the Project Manager will split the work into tasks; you make sure the product facts they need are known."`
2. **Thêm mô tả `workspace_mode` trong mục `artifacts`:**
   - Khi `workspace_mode` là `"brownfield"`, đọc codebase hiện có trước bằng tool để không hỏi lại điều mã nguồn đã trả lời.
3. **Gỡ mâu thuẫn dòng 25 vs dòng 43:**
   - Thay câu *"Do not ask about technology choices or implementation: later steps decide those"* bằng:
     *"Do not ask about libraries, frameworks, databases, or internal implementation details (the Solution Architect decides those). However, you MAY ask about hard user-mandated constraints that affect product boundaries — such as target platform (web, CLI, API, desktop), offline/local execution mandates, mandatory external services, or strict security/compliance requirements."*

---

## 4. Thiết kế Flow hợp nhất `business_analyst` (BA)

### 4.1. Role YAML (`registry/roles/pre_executor/business_analyst.yaml`)

```yaml
id: business_analyst
role: BusinessAnalyst
group: pre_executor
prompt: prompts/pre_executor/business_analyst.md
prompt_version: pre-executor-v1
tools:
  - list_files
  - read_file
  - read_files
  - search_repo
model:
  ref: default
cost_weight: 1.0
max_llm_calls: 10
```

> Role name `"BusinessAnalyst"` (lowercase `"businessanalyst"`) không đụng bất kỳ role lõi nào trong `CORE_ROLE_OUTPUT_MODELS` (`analyst`, `architect`, `planner`, `coder`, `tester`, `reviewer`, `environment`, `hub`, `router`).

### 4.2. Schema Pydantic (`src/raac/pre_executor/contracts.py`)

Xóa `RequirementsOutput` và `ProfileOutput`, thay bằng `BusinessAnalystOutput` (tái sử dụng `Overview`, `Feature`, `SuccessCriterion`, `UserFlow`, `BusinessRule`, `AcceptanceCriterion`, `EdgeCase`, `FunctionalRequirement`, và nâng cấp 4 model liên kết):

```python
MAX_OPEN_QUESTIONS = 5  # gộp từ requirements (3) + profile: giữ trung thực các điểm còn bỏ ngỏ thay vì tự bịa


class KeyEntity(Strict):
    id: str = Field(pattern=r"^ENT-\d+$")
    name: Text
    description: Text
    attributes: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)


class NonFunctionalRequirement(Strict):
    id: str = Field(pattern=r"^NFR-\d+$")
    category: Literal[NFR_CATEGORIES]
    requirement: Text
    metric: Text  # cách đo / ngưỡng cụ thể
    feature_ids: list[str] = Field(default_factory=list)  # F-n; rỗng = áp dụng toàn hệ thống


class ExternalIntegration(Strict):
    """Dịch vụ/hệ thống bên ngoài cần tích hợp (cầu nối từ nhóm `integrations` của Clarifier sang SA & PM)."""
    id: str = Field(pattern=r"^INT-\d+$")
    name: Text
    purpose: Text
    feature_ids: list[str] = Field(default_factory=list)  # F-n; rỗng = dùng chung toàn hệ thống
    failure_behavior: str = ""  # hệ thống xử lý thế nào khi dịch vụ ngoài lỗi/timeout


class Assumption(Strict):
    text: Text
    clarification_id: str | None = None  # câu hỏi clarify người dùng giữ nguyên gợi ý (assumed=True)
    feature_ids: list[str] = Field(default_factory=list)  # F-n liên quan; rỗng = giả định toàn cục


class OpenQuestion(Strict):
    """Điểm còn bỏ ngỏ được lưu có cấu trúc gắn feature_ids để PM cắt lát gọn vào từng task."""
    id: str = Field(pattern=r"^OQ-\d+$")
    text: Text
    feature_ids: list[str] = Field(default_factory=list)  # F-n liên quan; rỗng = cấp toàn cục


class BusinessAnalystOutput(Strict):
    """Business Analyst (hợp nhất requirements + profile): đặc tả nghiệp vụ đầy đủ `ba_spec.json`."""
    overview: Overview
    features: list[Feature] = Field(min_length=1)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    key_entities: list[KeyEntity] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    external_integrations: list[ExternalIntegration] = Field(default_factory=list)
    user_flows: list[UserFlow] = Field(min_length=1)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(min_length=1)
    edge_cases: list[EdgeCase] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
```

Cập nhật `ROLE_OUTPUTS`:

```python
ROLE_OUTPUTS = {
    "Clarifier": ClarifyOutput,
    "BusinessAnalyst": BusinessAnalystOutput,
    "SolutionArchitect": SolutionArchitectureOutput,
    "ProjectManager": ProjectManagerOutput,
}
```

### 4.3. Luật kiểm bằng code (`check_against(clarification)` trong `flows/business_analyst.py`)

Hợp nhất toàn bộ luật đã chứng minh hiệu quả từ `requirements.py` + `profile.py` và bổ sung luật cho các trường mới:

1. **ID & Tên duy nhất (dùng `duplicates` từ `._checks`):**
   - Duy nhất ID cho: `features` (`F-n`), `success_criteria` (`SC-n`), `key_entities` (`ENT-n`), `non_functional_requirements` (`NFR-n`), `external_integrations` (`INT-n`), `user_flows` (`UF-n`), `business_rules` (`BR-n`), `functional_requirements` (`FR-n`), `acceptance_criteria` (`FR-n.ACm`), `edge_cases` (`EC-n`), `open_questions` (`OQ-n`).
   - Duy nhất tên (chuẩn hóa lowercase, bỏ khoảng trắng thừa) cho `features` và `key_entities`.
2. **Chất lượng Feature & Success Criteria:**
   - Mọi `F-n` đều phải có `why_priority` và `independent_test` không rỗng.
   - Phải có ít nhất 1 `success_criteria` (`SC-1 ...`).
   - **Tinh chỉnh `TECH_VENDOR_WORDS` (thay cho `IMPLEMENTATION_WORDS` cũ):**
     ```python
     TECH_VENDOR_WORDS = re.compile(
         r"\b(react|vue|angular|svelte|nextjs|django|fastapi|flask|laravel|spring|express|nestjs|"
         r"postgres|postgresql|mysql|sqlite|mongodb|redis|kafka|rabbitmq|elasticsearch|"
         r"docker|kubernetes|k8s|aws|gcp|azure|graphql|grpc)\b",
         re.IGNORECASE,
     )
     ```
     *Lý do thay đổi:* Chỉ chặn tên công nghệ/framework/vendor cụ thể trong `success_criteria`, không chặn oan các từ mô tả bản chất sản phẩm như `api`, `endpoint`, `cli`, `server` khi yêu cầu người dùng chính là xây REST API hoặc công cụ dòng lệnh.
3. **Truy vết `Clarifier` → `BA`:**
   - `feature.sources` chỉ được chứa `"request"` hoặc ID câu hỏi (`Q1`, `Q2`, ...) có thật trong `clarification.json`.
   - `assumption.clarification_id` (nếu có) phải trỏ tới ID câu hỏi có thật trong `clarification.json`.
   - Mọi câu hỏi trong `clarification.json` có `assumed == True` bắt buộc phải xuất hiện trong `assumptions` (với `clarification_id` tương ứng).
   - Số lượng `open_questions` tối đa `MAX_OPEN_QUESTIONS` (5).
4. **Truy vết nội bộ về `F-n`:**
   - Mọi `feature_id` / `feature_ids` trong `user_flows`, `business_rules`, `functional_requirements`, `edge_cases`, `non_functional_requirements`, `external_integrations`, `assumptions`, `open_questions` chỉ được trỏ tới `F-n` có thật.
   - Quy ước đặt tên AC: mọi AC trong `FR-k` bắt buộc có tiền tố `FR-k.` (tức `FR-k.AC1`, `FR-k.AC2`, ...).
5. **Độ phủ bắt buộc cho Feature:**
   - Mọi `F-n` phải có ít nhất 1 `FR-n`.
   - Mọi `F-n` có `priority == "high"` phải xuất hiện trong ít nhất 1 `user_flows` và có ít nhất 1 `edge_cases`.

### 4.4. Prompt thiết kế (`registry/prompts/pre_executor/business_analyst.md`)

Gộp các hướng dẫn cốt lõi của `requirement_analyst.md` và `requirement_profiler.md`, đồng thời bổ sung 4 hướng dẫn gắn kết xuống SA/PM/Requirement Analyzer:

- **Vai trò & phạm vi:** Bạn là *Business Analyst*, chuyển `request` + `clarification` + `coverage` thành một bản đặc tả nghiệp vụ đầy đủ duy nhất (`ba_spec.json`). Mô tả *WHAT* (hành vi, quy tắc, tiêu chí đo lường), không chọn công nghệ hay viết cấu trúc code (việc của Solution Architect).
- **Gắn phạm vi `feature_ids`:**
  - Với `non_functional_requirements`, `external_integrations`, `assumptions`, `open_questions`: nếu mục đó chỉ liên quan một số chức năng cụ thể thì điền rõ `feature_ids` (vd. `["F-2", "F-3"]`); chỉ để `[]` khi thực sự áp dụng cho toàn bộ hệ thống. Điều này giúp Project Manager cắt lát ngữ cảnh chính xác cho từng task.
- **Giữ trung thực các điểm còn mở (không bịa chi tiết kỹ thuật):**
  - Với các thông lệ nghiệp vụ phổ biến từ nhóm `deferred` → chọn mặc định hợp lý và ghi vào `assumptions` (gắn `clarification_id` nếu đến từ câu `assumed: true` của Clarifier).
  - Với các điểm thực sự còn mở hoặc thuộc nhóm `outstanding` có ảnh hưởng tới cách triển khai/tích hợp → giữ nguyên dưới dạng `OpenQuestion` (`OQ-1`, ...) gắn `feature_ids` (tối đa 5 câu), **không tự bịa chi tiết kỹ thuật không có căn cứ**.
- **Quy chuẩn viết yêu cầu:**
  - `features` (`F-n`): mỗi chức năng là một lát cắt có giá trị độc lập, có `why_priority`, `independent_test` (nêu rõ điều kiện cần để kiểm thử: dữ liệu thuần, cần DB, cần mock dịch vụ ngoài hay kiểm giao diện — giúp cung cấp tín hiệu khách quan cho trục `Testability`), và `sources`.
  - `key_entities` (`ENT-n`): định danh chuẩn các thực thể nghiệp vụ chính.
  - `external_integrations` (`INT-n`): khai báo rõ dịch vụ ngoài và `failure_behavior` (để trống `[]` nếu không tích hợp ngoài).
  - `functional_requirements` (`FR-n`): câu chỉ thị một hành vi theo phong cách EARS (`"Hệ thống phải..."`, `"Khi ..., hệ thống phải..."`) kèm `acceptance_criteria` (`FR-n.ACm`) dạng Gherkin Given/When/Then với dữ liệu cụ thể, phủ cả luồng chính và luồng lỗi quan trọng.
  - `success_criteria` (`SC-n`): đo được từ góc nhìn người dùng/nghiệp vụ, không nhắc tên công nghệ/framework/vendor cụ thể.
- **Chế độ `revise` / `retry` / `workspace_mode`:** sửa tiếp trên `current_draft` theo `user_feedback` mà giữ nguyên các phần người dùng không yêu cầu đổi; sửa đúng `rule_violations` trên `previous_output`; đọc repo bằng tool nếu `workspace_mode == "brownfield"`. Ngôn ngữ theo `request`, trả duy nhất JSON đúng `output_schema`.

### 4.5. Payload, Lưu trữ, Render & Diff

- **Payload của `flows/business_analyst.py`:**
  ```python
  payload = {
      "request": artifacts.request(),
      "clarification": clarification,
      "coverage": artifacts.read_json("coverage.json") or [],
      "workspace_mode": mode,
      "user_feedback": artifacts.feedback_history(FLOW),
  }
  if note and current:
      payload["current_draft"] = current
  ```
- **Lưu file:** ghi `ba_spec.json` và `ba_spec.md` (render bằng `render_ba_spec(data)` trong `render.py`).
- **Hủy duyệt downstream:** `gate.invalidate_from(artifacts, "business_analyst")` làm mất duyệt cả `solution_architect` và `project_manager`.
- **`events.py` (`CHANGE_FIELDS["business_analyst"]`):**
  ```python
  "business_analyst": {
      "features": ("chức năng", "name"),
      "success_criteria": ("tiêu chí thành công", "outcome"),
      "key_entities": ("thực thể", "name"),
      "non_functional_requirements": ("NFR", "requirement"),
      "external_integrations": ("tích hợp ngoài", "name"),
      "user_flows": ("user flow", "name"),
      "business_rules": ("quy tắc", "rule"),
      "functional_requirements": ("yêu cầu", "description"),
      "edge_cases": ("trường hợp biên", "situation"),
      "open_questions": ("câu hỏi mở", "text"),
  }
  ```
  và `TOP_LEVEL_FIELDS["business_analyst"] = ("overview", "constraints", "assumptions", "out_of_scope")`.

---

## 5. Điểm khớp nối tinh gọn sang `SA`, `PM`, `CLI`, `TUI` đã hiện thực

Vì `solution_architect.py` và `project_manager.py` đã có sẵn trong repo, chỉ cần các nâng cấp nhỏ, có tính tương thích cao để tận dụng triệt để `ba_spec.json` mới:

### 5.1. `artifacts.py` & `configs/pre_executor.yaml`

```python
FLOWS = ("clarify", "business_analyst", "solution_architect", "project_manager")
APPROVED_FILE = {
    "clarify": "answers.md",
    "business_analyst": "ba_spec.json",
    "solution_architect": "solution_architecture.json",
    "project_manager": "tasks.json",
}
BA_SPEC_CANDIDATES = ("ba_spec.json", "requirement_profile.json")  # giữ fallback đọc workspace cũ
```

```yaml
version: pre-executor-v3
max_flow_retries: 2
max_clarify_rounds: 5
models:
  clarify: ""
  business_analyst: ""
  solution_architect: ""
  project_manager: ""
```

### 5.2. Khớp nối ở `solution_architect` (`contracts.py`, `flows/solution_architect.py`, `solution_architect.md`)

1. Trong `contracts.py`:
   - Thêm trường tùy chọn vào `DataModel`: `entity_ids: list[str] = Field(default_factory=list)` (trỏ `ENT-n` từ BA).
   - Thêm trường tùy chọn vào `ApiContract`: `integration_ids: list[str] = Field(default_factory=list)` (trỏ `INT-n` từ BA).
2. Trong `flows/solution_architect.py` (`check_against`):
   - Lấy `entities = {e["id"] for e in ba_spec.get("key_entities", []) if isinstance(e, dict) and "id" in e}` và `integrations = {i["id"] for i in ba_spec.get("external_integrations", []) if isinstance(i, dict) and "id" in i}`.
   - Nếu `entities` khác rỗng:
     - Kiểm tra mọi `eid` trong `data_model.entity_ids` phải thuộc `entities`.
     - Kiểm tra mọi `ENT-n` đều xuất hiện trong `data_model.entity_ids` (hoặc `module.owns_entities`) của ít nhất một mục — đảm bảo SA không bỏ sót thực thể nghiệp vụ nào.
   - Nếu `integrations` khác rỗng:
     - Kiểm tra mọi `iid` trong `api_contract.integration_ids` phải thuộc `integrations`.
     - Kiểm tra mọi `INT-n` đều được phủ bởi ít nhất một `api_contract.integration_ids` — đảm bảo tích hợp ngoài có hợp đồng rõ ràng.
   - *(Vì chỉ kiểm khi `entities`/`integrations` có `id`, các fixture cũ chưa có `ENT-n`/`INT-n` vẫn không bị gãy đột ngột.)*
3. Trong `registry/prompts/pre_executor/solution_architect.md`: bổ sung hướng dẫn map `ENT-n` vào `data_models[*].entity_ids` và `INT-n` vào `api_contracts[*].integration_ids`; đồng thời nhắc ghi rõ chiến lược kiểm thử ở mức hệ thống cho DB/dịch vụ ngoài (vd. in-memory DB, mock/stub) vào `global_constraints` (`category: "testing"`).

### 5.3. Khớp nối ở `project_manager` (`flows/project_manager.py`, `contracts.py`, `project_manager.md`)

Đây là điểm nâng cấp giá trị nhất cho độ sắc nét của `Requirement Analyzer` (đặc biệt trên hai trục `Testability` và `Security`):

1. **Nâng cấp `build_context_slice(task, ba_spec, sa_data)` (thay dòng 189–202 hiện tại):**
   - Viết helper nhỏ `_scoped(items, task_features)`: giữ các mục có `not item.get("feature_ids")` (toàn cục) **hoặc** `bool(set(item["feature_ids"]) & task_features)` (gắn với feature của task này).
   - **Lọc `relevant_assumptions`** bằng `_scoped(ba_spec.get("assumptions", []), features)` + `task["open_assumptions"]` (thay vì đổ toàn bộ `assumptions` toàn dự án vào mọi task như trước!).
   - **Bổ sung `relevant_open_questions`** vào `context_slice`: lọc `_scoped(ba_spec.get("open_questions", []), features)` (hỗ trợ cả `OpenQuestion` dict lẫn legacy `str`).
   - **Bổ sung `external_integrations`** vào `context_slice`: lọc `_scoped(ba_spec.get("external_integrations", []), features)`.
   - **Bổ sung `touches_security` xét thêm `external_integrations`** nếu có NFR security hoặc integration liên quan đến auth/payment (giữ gọn điều kiện deterministic hiện có).
2. **Nâng cấp `check_against` trong `flows/project_manager.py`:**
   - Nếu một `NFR-n` thuộc `category == "security"` có `feature_ids` không rỗng trong `ba_spec`, kiểm tra ngoài việc `NFR-n` được phủ bởi ít nhất 1 task, thì ít nhất 1 task phủ `NFR-n` đó phải có `feature_ids` giao với `nfr["feature_ids"]` (tránh PM gắn NFR bảo mật của tính năng admin nhầm sang task dựng khung không liên quan).
3. **Đồng bộ thuật ngữ 4 trục & tinh chỉnh prompt PM (`contracts.py`, `flows/project_manager.py`, `project_manager.md`):**
   - Cập nhật docstring chỗ nhắc 4 trục từ `(Complexity, Ambiguity, Architecture Impact, Security)` sang `(Complexity, Testability, Architecture Impact, Security)`.
   - Trong `registry/prompts/pre_executor/project_manager.md`, hướng dẫn PM viết `verification_scope` theo hướng **khách quan về loại kiểm thử cần thiết** (unit test thuần logic, fixture DB/file, mock API ngoài, tích hợp nhiều module, test UI, regression với các task `depends_on`), đồng thời giữ nguyên quy tắc tuyệt đối **không** chấm điểm `Testability` hay chọn Level.

### 5.4. Cập nhật `cli.py` & `tui.py`

- **`cli.py`:**
  - `IMPLEMENTED = {"clarify": clarify, "business_analyst": business_analyst, "solution_architect": solution_architect, "project_manager": project_manager}`.
  - Thêm cờ `--auto-assume` cho subparser `clarify` và truyền vào `clarify.run(..., auto_assume=auto_assume)`.
- **`tui.py`:**
  - `DRAFT_FLOWS`:
    ```python
    DRAFT_FLOWS = (
        ("business_analyst", "Tiếp: Solution Architect"),
        ("solution_architect", "Tiếp: Project Manager"),
        ("project_manager", None),
    )
    ```
  - `STATE_ATTR = {"business_analyst": "ba_spec", "solution_architect": "solution_architecture"}`.
  - `PreExecutorApp`: thay `self.requirements` & `self.profile` bằng `self.ba_spec: dict | None = None`; `last_result()` trả `self.project_manager or self.solution_architecture or self.ba_spec`.
  - `DRAFT_TITLES["business_analyst"]`: hiển thị số chức năng `F-n`, số `FR-n`, số `AC`, số `NFR-n`, số `EC-n`.
  - Nút sau `clarify_stage`: `("next", "Tiếp: Business Analyst")`.

---

## 6. Thứ tự triển khai đề xuất (cho Claude)

1. **Bước 1 — Contracts & Shared Schema:** Cập nhật `contracts.py` (`BusinessAnalystOutput`, nâng cấp `KeyEntity`/`NFR`/`Assumption`/`OpenQuestion`/`ExternalIntegration`, thêm trường optional `entity_ids`/`integration_ids` ở SA, cập nhật `ROLE_OUTPUTS`).
2. **Bước 2 — Clarifier:** Cập nhật `flows/clarify.py` (`workspace_mode`, `auto_assume`), `clarifier.md`, `clarifier.yaml`.
3. **Bước 3 — Business Analyst:** Tạo `business_analyst.yaml`, `business_analyst.md`, `flows/business_analyst.py`, `render_ba_spec()` trong `render.py`, `CHANGE_FIELDS["business_analyst"]` trong `events.py`; xóa `requirements.py`, `profile.py` và 2 cặp role/prompt cũ.
4. **Bước 4 — Khớp nối SA, PM, Artifacts, Config, CLI, TUI:** Cập nhật `artifacts.py`, `configs/pre_executor.yaml`, `flows/solution_architect.py`, `solution_architect.md`, `flows/project_manager.py`, `cli.py`, `tui.py`, `__init__.py`, `flows/__init__.py`, `README.md`.
5. **Bước 5 — Cập nhật & bổ sung Test:**
   - Cập nhật `tests/pre_executor/pe_helpers.py` (`BA_SPEC` có `ENT-n`, `INT-n`, `OQ-n`, `feature_ids`; `approve_ba_spec` chỉ approve `clarify` + `business_analyst`).
   - Xóa `test_requirements.py`, `test_profile.py`; tạo `tests/pre_executor/test_business_analyst.py`.
   - Cập nhật `test_clarify.py` (test `--auto-assume`, `workspace_mode`, `status` 4 flow), `test_events.py`, `test_tui.py`, `test_solution_architect.py` (test `ENT-n` & `INT-n`), `test_project_manager.py` (test lọc `relevant_assumptions`/`relevant_open_questions`/`external_integrations` theo `feature_ids` và kiểm tra scoped security NFR).
6. **Bước 6 — Kiểm chứng cuối.**

---

## 7. Kế hoạch kiểm thử & Tiêu chí nghiệm thu

### 7.1. Các ca kiểm thử bắt buộc bổ sung

- **`test_clarify.py`:**
  - `clarify.run(..., auto_assume=True)` chạy tự động nhiều vòng tới khi `status == "done"`, mọi câu đều ghi `assumed == True`, tự approve gate `clarify`.
  - CLI `clarify --workspace W --prompt "..." --auto-assume` trả exit code 0 và chốt ngay `clarification.json` + `coverage.json`.
  - Payload gửi agent có `"workspace_mode"` đúng (`greenfield` với thư mục trống, `brownfield` khi có file nguồn).
- **`test_business_analyst.py`:**
  - Gate chặn khi `clarify` chưa xong; mở khi `clarify` đã duyệt; sửa `ba_spec.json` bằng tay → `stale`; `revise business_analyst` làm mất duyệt `solution_architect` và `project_manager`.
  - Toàn bộ luật gộp (parametrize): trùng ID (`F/SC/ENT/NFR/INT/UF/BR/FR/AC/EC/OQ`), trùng tên feature/entity, `feature.sources` sai, thiếu `why_priority`/`independent_test`, thiếu `success_criteria`, `TECH_VENDOR_WORDS` chặn `React`/`PostgreSQL`/`Docker` nhưng **cho phép** `"API trả về kết quả trong < 200 ms"`, thiếu assumption cho câu `assumed=True`, vượt `MAX_OPEN_QUESTIONS`, `feature_ids` sai trên NFR/INT/Assumption/OQ/UF/BR/FR/EC, AC sai prefix `FR-k.`, feature chưa có FR, high-priority feature thiếu UF hoặc EC.
  - CLI (`business_analyst`, `revise`, `approve`) + TUI đi trọn 4 bước `clarify → business_analyst → solution_architect → project_manager` + chạy qua `AgentRunner` thật bằng HTTP mock.
- **`test_solution_architect.py` & `test_project_manager.py`:**
  - SA bắt lỗi khi `ba_spec` có `ENT-1` hoặc `INT-1` nhưng SA không ánh xạ hoặc trỏ sai ID.
  - PM `context_slice` của task `T2` (`feature_ids=["F-1"]`) **chỉ** nhận `assumptions`, `open_questions` (`OQ-n`) và `external_integrations` (`INT-n`) toàn cục hoặc gắn `F-1`, **không** bị lẫn mục gắn riêng `F-3`.
  - PM bắt lỗi khi `NFR` security gắn `feature_ids=["F-3"]` chỉ được đặt vào task không chạm `F-3`.

### 7.2. Lệnh kiểm chứng sau khi hoàn tất

```bash
python -m pytest tests/pre_executor/ -q
python -m pytest tests/unit/registry/test_registry_loader.py -k "not test_version_file_exists_and_is_semver and not test_stamp_record_adds_version_without_mutating_input and not test_stamp_record_rejects_mixed_versions" -q
```

Mọi test trong `tests/pre_executor/` và toàn bộ test kiểm tra cấu trúc/role/tool/prompt trong `test_registry_loader.py` bắt buộc phải pass 100%.
