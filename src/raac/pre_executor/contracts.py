"""Đầu vào/đầu ra của các flow (pydantic, extra="forbid") và đăng ký với harness.

Chuỗi:  User request -> Clarification -> Requirements (tổng quan, chức năng cốt lõi, NFR)
        -> Requirement Profile (user flow, quy tắc nghiệp vụ, yêu cầu chi tiết, AC)
        -> Tech Stack -> Architecture -> Environment Plan -> Task Plan

Đã triển khai:
- ClarifyOutput (flow clarify, role Clarifier): complete, summary, questions[{id, text, default_answer, why}].
  Người dùng trả lời trong answers.md; bản chốt là Clarification = [{id, question, answer, assumed}].
- RequirementsOutput (flow requirements, role RequirementAnalyst): overview, features (chức năng cốt lõi F-n),
  non_functional_requirements (NFR-n có metric), constraints, assumptions, out_of_scope, open_questions.

- ProfileOutput (flow profile, role RequirementProfiler): với mỗi feature F-n đã duyệt: user_flows (UF-n),
  business_rules (BR-n), functional_requirements (FR-n) kèm acceptance_criteria (FR-n.ACm).
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


class ClarifyQuestion(Strict):
    id: Text
    text: Text
    default_answer: str = ""
    why: str = ""


class ClarifyOutput(Strict):
    """Một vòng làm rõ: đã đủ thông tin chưa, nếu chưa thì cần hỏi gì."""
    complete: bool
    summary: str = ""
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
    """Một chức năng cốt lõi của phần mềm."""
    id: str = Field(pattern=r"^F-\d+$")
    name: Text
    description: Text
    priority: Literal["high", "medium", "low"]
    sources: list[str] = Field(default_factory=list)  # căn cứ: "request" hoặc id câu hỏi clarify (Q3)


NFR_CATEGORIES = ("security", "performance", "availability", "scalability", "usability", "compatibility",
                  "maintainability", "compliance", "other")


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
    id: str = Field(pattern=r"^FR-\d+\.AC\d+$")
    text: Text  # kiểm được: đầu vào/tình huống cụ thể -> kết quả mong đợi


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
    open_questions: list[str] = Field(default_factory=list)


ROLE_OUTPUTS = {"Clarifier": ClarifyOutput, "RequirementAnalyst": RequirementsOutput,
                "RequirementProfiler": ProfileOutput}


def register() -> None:
    for role, model in ROLE_OUTPUTS.items():
        register_output_model(role, model)
