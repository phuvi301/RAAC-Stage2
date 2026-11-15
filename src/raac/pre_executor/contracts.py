"""Đầu vào/đầu ra của các flow (pydantic, extra="forbid") và đăng ký với harness.

Chuỗi:  User request -> Clarification -> Requirements (tổng quan, chức năng cốt lõi, NFR)
        -> Requirement Profile (user flow, quy tắc nghiệp vụ, yêu cầu chi tiết, AC)
        -> Tech Stack -> Architecture -> Environment Plan -> Task Plan

Đã triển khai:
- ClarifyOutput (flow clarify, role Clarifier), theo cách của GitHub Spec Kit /clarify: mỗi vòng quét đầu vào (yêu
  cầu thô hoặc một bản spec) theo 10 nhóm độ phủ của Spec Kit (COVERAGE_CATEGORIES), mỗi nhóm một trạng thái (COVERAGE_STATUSES);
  hỏi tối đa 5 câu về nhóm partial/missing, mỗi câu là trắc nghiệm 2-5 lựa chọn có phương án khuyến nghị
  (`default_answer` là một trong `options`) hoặc trả lời ngắn có gợi ý (`options` rỗng).
  Bản chốt: Clarification = [{id, category, question, answer, assumed}] + coverage.json (độ phủ cuối).
- RequirementsOutput (flow requirements, role RequirementAnalyst), theo Spec Kit spec-template + ISO/IEC 25010:
  overview; features (F-n, mỗi chức năng có lý do ưu tiên và cách kiểm độc lập như một lát cắt MVP); success_criteria
  (SC-n đo được, không phụ thuộc công nghệ); key_entities; non_functional_requirements (NFR-n theo đặc tính chất lượng
  ISO/IEC 25010, có metric); constraints; assumptions; out_of_scope; open_questions (tối đa 3).

- ProfileOutput (flow profile, role RequirementProfiler): với mỗi feature F-n đã duyệt: user_flows (UF-n),
  business_rules (BR-n), functional_requirements (FR-n, câu chỉ thị kiểu "Hệ thống phải ..." như EARS/Spec Kit) kèm
  acceptance_criteria (FR-n.ACm dạng Given/When/Then như Gherkin/Spec Kit) và edge_cases (EC-n).
  requirement_profile.json = phần này + tổng quan/features/NFR/ràng buộc/giả định chép nguyên từ requirements.

Chưa triển khai (xem docstring các flow): TechStack, Architecture, EnvironmentPlan, TaskPlan.

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
# deferred: thuộc tầng kế hoạch (chọn công nghệ, cách triển khai, chia task) hoặc chi tiết để requirements/profile.
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
    name: Text
    description: Text
    attributes: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)


# Đặc tính chất lượng theo ISO/IEC 25010 (+ safety, compliance pháp lý, other).
NFR_CATEGORIES = ("performance_efficiency", "compatibility", "usability", "reliability", "security",
                  "maintainability", "portability", "safety", "compliance", "other")
MAX_OPEN_QUESTIONS = 3  # như giới hạn [NEEDS CLARIFICATION] của Spec Kit


class NonFunctionalRequirement(Strict):
    id: str = Field(pattern=r"^NFR-\d+$")
    category: Literal[NFR_CATEGORIES]
    requirement: Text
    metric: Text  # cách đo / ngưỡng cụ thể


class Assumption(Strict):
    text: Text
    clarification_id: str | None = None  # câu hỏi clarify mà người dùng để nguyên gợi ý


class RequirementsOutput(Strict):
    """Product Requirement Analyst: tổng quan, chức năng cốt lõi, yêu cầu phi chức năng."""
    overview: Overview
    features: list[Feature] = Field(min_length=1)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    key_entities: list[KeyEntity] = Field(default_factory=list)
    non_functional_requirements: list[NonFunctionalRequirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


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


class ProfileOutput(Strict):
    """Requirement Profiler: chi tiết hoá từng chức năng cốt lõi đã duyệt."""
    user_flows: list[UserFlow] = Field(min_length=1)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(min_length=1)
    edge_cases: list[EdgeCase] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


ROLE_OUTPUTS = {"Clarifier": ClarifyOutput, "RequirementAnalyst": RequirementsOutput,
                "RequirementProfiler": ProfileOutput}


def register() -> None:
    for role, model in ROLE_OUTPUTS.items():
        register_output_model(role, model)
