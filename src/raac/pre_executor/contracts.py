"""Đầu vào/đầu ra của các flow (pydantic, extra="forbid") và đăng ký với harness.

Chuỗi:  User request -> Clarification -> BA spec (ba_spec.json: F/SC/ENT/NFR/INT/UF/BR/FR/AC/EC/OQ)
        -> Solution Architecture (SA) -> Task Plan (PM: DAG T1..Tn, tasks.json)
"Requirement Profile" chỉ dùng cho bộ 4 điểm (Complexity, Ambiguity, Architecture Impact, Security) của Requirement
Analyzer ở tầng Micro (Proposal Mục 6.3), không dùng cho đặc tả BA.

Đã triển khai:
- ClarifyOutput (flow clarify, role Clarifier), theo cách của GitHub Spec Kit /clarify: mỗi vòng quét đầu vào (yêu
  cầu thô hoặc một bản spec) theo 10 nhóm độ phủ của Spec Kit (COVERAGE_CATEGORIES), mỗi nhóm một trạng thái (COVERAGE_STATUSES);
  hỏi tối đa 5 câu về nhóm partial/missing, mỗi câu là trắc nghiệm 2-5 lựa chọn có phương án khuyến nghị
  (`default_answer` là một trong `options`) hoặc trả lời ngắn có gợi ý (`options` rỗng).
  Bản chốt: Clarification = [{id, category, question, answer, assumed}] + coverage.json (độ phủ cuối).
- BusinessAnalystOutput (flow business_analyst, role BusinessAnalyst; gộp requirements + profile cũ), theo Spec Kit
  spec-template + ISO/IEC 25010 + EARS + Gherkin: overview; features (F-n có lý do ưu tiên, cách kiểm độc lập, căn
  cứ); success_criteria (SC-n đo được, không nêu tên công nghệ); key_entities (ENT-n); non_functional_requirements
  (NFR-n theo ISO/IEC 25010, có metric, gắn feature_ids); external_integrations (INT-n, gắn feature_ids);
  user_flows (UF-n); business_rules (BR-n); functional_requirements (FR-n câu chỉ thị) + acceptance_criteria
  (FR-n.ACm Given/When/Then); edge_cases (EC-n); constraints; assumptions (gắn feature_ids); out_of_scope;
  open_questions (OQ-n, tối đa 5, gắn feature_ids). feature_ids rỗng = áp dụng toàn hệ thống; PM dùng nó để cắt
  context_slice đúng phạm vi từng task.
- SolutionArchitectureOutput (flow solution_architect, role SolutionArchitect): tầng Macro chốt tech stack, thư mục,
  module MOD-n, data model DM-n, hợp đồng API-n, kiến trúc bảo mật, runtime/test contract, ràng buộc toàn cục CON-n
  (chỉ-đọc với tầng Micro L1–L3) và quyết định ADR-n.
- ProjectManagerOutput (flow project_manager, role ProjectManager): DAG các task T1..Tn chỉ mang metadata khách quan
  và id tham chiếu về BA/SA. KHÔNG có level, risk hay 4 điểm của Requirement Analyzer (extra="forbid" chặn).
  execution_order và context_slice của tasks.json do code tạo (flows/project_manager.py), không qua model.

register(): đăng ký schema theo tên role vào agent_contracts, vì AgentRunner/parse_role_output tra schema theo tên
role. Gọi trước khi chạy role (runtime.open_runtime đã gọi).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from raac.common.agent_contracts import register_output_model

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MAX_QUESTIONS_PER_ROUND = 5


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Nhóm độ phủ khi làm rõ: theo bảng phân loại của GitHub Spec Kit /clarify (templates/commands/clarify.md).
COVERAGE_CATEGORIES = {
    "functional_scope": "Phạm vi chức năng: mục tiêu người dùng, tiêu chí thành công, vai trò/persona, ngoài phạm vi",
    "domain_data": "Miền và dữ liệu: thực thể, thuộc tính, quan hệ, định danh/duy nhất, vòng đời và trạng thái, khối lượng",
    "ux_flow": "Luồng tương tác: hành trình chính, trạng thái lỗi/rỗng/đang tải, nền tảng, trợ năng, ngôn ngữ",
    "quality_attributes": "Chất lượng (NFR): hiệu năng, mở rộng, tin cậy/khả dụng, quan sát, bảo mật/riêng tư, tuân thủ",
    "integrations": "Tích hợp và phụ thuộc ngoài: dịch vụ ngoài và khi chúng lỗi, nhập/xuất dữ liệu, giao thức",
    "edge_cases": "Trường hợp biên và lỗi: tình huống tiêu cực, giới hạn tần suất, xung đột dữ liệu",
    "constraints": "Ràng buộc và đánh đổi: ngân sách, thời hạn, hosting, ràng buộc kỹ thuật, phương án bị loại",
    "terminology": "Thuật ngữ: tên gọi thống nhất cho các khái niệm chính, từ tránh dùng",
    "completion_signals": "Tín hiệu hoàn thành: tiêu chí nghiệm thu kiểm được, định nghĩa xong đo được",
    "open_items": "Còn bỏ ngỏ: TODO, quyết định chưa chốt, tính từ mơ hồ không có con số",
}
# clear: đủ; partial: có nhưng mơ hồ/mâu thuẫn; missing: chưa có; not_applicable: không áp dụng (ghi lý do);
# deferred: thuộc tầng kế hoạch (chọn công nghệ, cách triển khai, chia task) hoặc chi tiết để business_analyst.
COVERAGE_STATUSES = ("clear", "partial", "missing", "not_applicable", "deferred")


class CoverageItem(Strict):
    category: Text
    status: Literal[COVERAGE_STATUSES]
    note: str = ""


class ClarifyQuestion(Strict):
    id: Text
    category: str = ""  # một khóa của COVERAGE_CATEGORIES đang partial/missing
    text: Text
    options: list[Text] = Field(default_factory=list)  # 2-5 lựa chọn; rỗng = trả lời ngắn
    default_answer: str = ""  # phương án khuyến nghị (một trong options) hoặc gợi ý trả lời ngắn
    why: str = ""


class ClarifyOutput(Strict):
    """Một vòng làm rõ: độ phủ hiện tại, đã đủ chưa, nếu chưa thì cần hỏi gì."""
    complete: bool
    summary: str = ""
    coverage: list[CoverageItem] = Field(default_factory=list)
    questions: list[ClarifyQuestion] = Field(default_factory=list, max_length=MAX_QUESTIONS_PER_ROUND)

    @model_validator(mode="after")
    def consistent(self):
        if not self.complete and not self.questions:
            raise ValueError("an incomplete clarification must ask at least one question")
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique within a round")
        return self


class Overview(Strict):
    summary: Text  # vài câu tổng quan sản phẩm
    product_goal: Text
    target_users: list[Text] = Field(min_length=1)
    platforms: list[str] = Field(default_factory=list)  # web, mobile web, admin ...
    scope: str = ""  # phạm vi bản đầu tiên


class Feature(Strict):
    """Một chức năng cốt lõi; như user story của Spec Kit: tự nó mang lại giá trị và kiểm được độc lập."""
    id: str = Field(pattern=r"^F-\d+$")
    name: Text
    description: Text  # người dùng làm được gì, bằng ngôn ngữ nghiệp vụ
    priority: Literal["high", "medium", "low"]
    why_priority: str = ""  # vì sao mức ưu tiên này
    independent_test: str = ""  # kiểm riêng chức năng này thế nào, nó mang lại giá trị gì
    sources: list[str] = Field(default_factory=list)  # căn cứ: "request" hoặc id câu hỏi clarify (Q3)


class SuccessCriterion(Strict):
    """Kết quả đo được từ góc nhìn người dùng/kinh doanh, không nói tới công nghệ (Spec Kit success criteria)."""
    id: str = Field(pattern=r"^SC-\d+$")
    outcome: Text
    metric: Text


class KeyEntity(Strict):
    id: str = Field(pattern=r"^ENT-\d+$")
    name: Text
    description: Text
    attributes: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)


# Đặc tính chất lượng theo ISO/IEC 25010 (+ safety, compliance pháp lý, other).
NFR_CATEGORIES = ("performance_efficiency", "compatibility", "usability", "reliability", "security",
                  "maintainability", "portability", "safety", "compliance", "other")
MAX_OPEN_QUESTIONS = 5  # gộp requirements (3) + profile: giữ trung thực các điểm còn mở


class NonFunctionalRequirement(Strict):
    id: str = Field(pattern=r"^NFR-\d+$")
    category: Literal[NFR_CATEGORIES]
    requirement: Text
    metric: Text  # cách đo / ngưỡng cụ thể
    feature_ids: list[str] = Field(default_factory=list)  # F-n; rỗng = áp dụng toàn hệ thống


class ExternalIntegration(Strict):
    """Dịch vụ/hệ thống bên ngoài cần tích hợp (nối nhóm `integrations` của Clarifier sang SA & PM)."""
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
    """Điểm chưa chốt, giữ có cấu trúc để PM cắt lát vào task (tín hiệu Ambiguity cho Requirement Analyzer)."""
    id: str = Field(pattern=r"^OQ-\d+$")
    text: Text
    feature_ids: list[str] = Field(default_factory=list)  # F-n liên quan; rỗng = cấp toàn cục


class UserFlow(Strict):
    id: str = Field(pattern=r"^UF-\d+$")
    name: Text
    actor: Text
    feature_ids: list[str] = Field(min_length=1)
    steps: list[Text] = Field(min_length=1)


class BusinessRule(Strict):
    id: str = Field(pattern=r"^BR-\d+$")
    feature_id: Text
    rule: Text


class AcceptanceCriterion(Strict):
    """Kịch bản chấp nhận Given/When/Then (Gherkin, acceptance scenarios của Spec Kit)."""
    id: str = Field(pattern=r"^FR-\d+\.AC\d+$")
    given: Text  # bối cảnh/dữ liệu ban đầu cụ thể
    when: Text  # hành động
    then: Text  # kết quả quan sát được


class EdgeCase(Strict):
    """Trường hợp biên hoặc lỗi và cách hệ thống phải xử lý (edge cases của Spec Kit)."""
    id: str = Field(pattern=r"^EC-\d+$")
    feature_id: Text
    situation: Text
    expected: Text


class FunctionalRequirement(Strict):
    id: str = Field(pattern=r"^FR-\d+$")
    feature_id: Text
    description: Text
    priority: Literal["high", "medium", "low"]
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)


class BusinessAnalystOutput(Strict):
    """Business Analyst (gộp requirements + profile): đặc tả nghiệp vụ đầy đủ `ba_spec.json`."""
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
    feature_ids: list[str] = Field(default_factory=list)  # F-n; rỗng được với module nền tảng (shared/core)
    depends_on: list[str] = Field(default_factory=list)  # MOD-n
    owns_entities: list[str] = Field(default_factory=list)


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
    entity_ids: list[str] = Field(default_factory=list)  # ENT-n của BA mà data model này hiện thực


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
    integration_ids: list[str] = Field(default_factory=list)  # INT-n của BA mà hợp đồng này phục vụ


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
    build_command: str = ""  # rỗng nếu không cần bước build
    run_command: str = ""  # lệnh khởi chạy app/CLI (nếu có)
    test_framework: Text  # vd. "pytest", "vitest"
    test_command: Text  # lệnh test chuẩn cho tầng MAS và regression gate
    env_vars: dict[str, str] = Field(default_factory=dict)  # tên biến -> mô tả/mặc định PHI bí mật


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
    """Solution Architect: kiến trúc cấp hệ thống (Macro) cho toàn bộ sản phẩm."""
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


TaskKind = Literal["bootstrap", "feature", "integration", "hardening"]


class Milestone(Strict):
    id: str = Field(pattern=r"^M\d+$")
    name: Text
    goal: Text
    task_ids: list[str] = Field(min_length=1)


class TaskSpec(Strict):
    """Một task nhỏ (T1, T2, ...) do PM phân rã.
    TUYỆT ĐỐI không chứa điểm Requirement Profile (4 trục), risk hay suggested_level: những thứ đó do Requirement
    Analyzer (just-in-time) và Controller quyết định.
    """
    id: str = Field(pattern=r"^T\d+$")
    title: Text
    kind: TaskKind
    goal: Text  # mục tiêu nghiệp vụ/kỹ thuật, không viết diff từng dòng code
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
    open_assumptions: list[str] = Field(default_factory=list)


class ProjectManagerOutput(Strict):
    """Project Manager: DAG các task; execution_order và context_slice do code tạo sau khi kiểm."""
    summary: Text
    milestones: list[Milestone] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)


ROLE_OUTPUTS = {"Clarifier": ClarifyOutput, "BusinessAnalyst": BusinessAnalystOutput,
                "SolutionArchitect": SolutionArchitectureOutput, "ProjectManager": ProjectManagerOutput}


def register() -> None:
    for role, model in ROLE_OUTPUTS.items():
        register_output_model(role, model)
