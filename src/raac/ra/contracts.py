"""Đầu vào và đầu ra của Requirement Analyzer (rubric 4 trục C/T/S/A, thang 1-5).

Đầu vào (đọc từ workspace, không qua model):
- TaskInput: một task trong tasks.json (do project_manager tạo, schema TaskSpec): id, title, kind, goal, depends_on,
  feature_ids, fr_ids, acceptance_criteria_ids (FR-n.ACm trong ba_spec.json), edge_case_ids, nfr_ids, module_ids,
  api_contract_ids, data_model_ids, constraint_ids, target_paths, verification_scope, ...

Đầu ra (role RequirementAnalyzer, mỗi task một mục trong requirement_profile.json):
- RequirementProfileOutput: complexity, testability, security, architecture_impact (mỗi trục AxisScore:
  score 1-5, factors lấy từ bảng "Các yếu tố đánh giá" của trục đó trong rubric, evidence, confidence);
  architecture_sections_read (các mục của solution_architecture.md agent đã đọc để lấy bằng chứng); summary.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from raac.common.agent_contracts import register_output_model

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RUBRIC_FILE = "registry/prompts/requirement_analyzer/RAAC_Official_4Axis_Rubric_v1.0.md"
RUBRIC_VERSION = "RAAC-4Axis-v1.0"
# Prompt của role: phần hướng dẫn viết tay + phần rubric (mục 2-7) chép từ RUBRIC_FILE sau RUBRIC_MARKER.
# Rubric nằm trong system prompt (giống hệt nhau giữa các task) thay vì artifacts, để provider cache được prefix.
PROMPT_FILE = "registry/prompts/ra/requirement_analyzer.md"
RUBRIC_MARKER = ("<!-- RUBRIC: sinh từ registry/prompts/requirement_analyzer/RAAC_Official_4Axis_Rubric_v1.0.md "
                 "bằng `python -m raac.ra sync-rubric`; không sửa tay phần dưới -->")

# Yếu tố đánh giá của từng trục, theo bảng "Các yếu tố đánh giá" (mục 3.2, 4.2, 5.2, 6.2) của rubric.
AXIS_FACTORS = {
    "complexity": (
        "scope_of_impact", "io_complexity", "business_logic", "dependencies", "actors_flows", "integration",
        "concurrency", "data_entities", "nfr_interaction", "technical_uncertainty", "domain_knowledge",
        "compliance"),
    "testability": (
        "measurability", "weak_phrases", "acceptance_criteria", "observability", "reproducibility",
        "test_dependencies", "determinism", "test_space", "subjectivity", "test_oracle", "test_tooling"),
    "security": (
        "data_sensitivity", "attack_surface", "authentication", "authorization", "data_integrity", "privacy",
        "threat_exposure", "auditability", "trust_boundaries", "compliance", "cia_impact", "security_control"),
    "architecture_impact": (
        "system_structure", "system_boundary", "data_architecture", "integration_middleware", "deployment_infra",
        "quality_attributes", "crosscutting", "architectural_decision", "business_value_risk", "stakeholder_impact",
        "reversibility"),
}
AXES = tuple(AXIS_FACTORS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskInput(BaseModel):
    """Một task trong tasks.json của project_manager (TaskSpec). Bỏ qua context_slice và trường khác: phần spec do
    Requirement Analyzer tự gom, phần kiến trúc agent tự đọc bằng tool."""
    model_config = ConfigDict(extra="ignore")

    id: Text
    title: Text
    kind: str = ""
    goal: Text
    depends_on: list[str] = Field(default_factory=list)  # Tn
    feature_ids: list[str] = Field(default_factory=list)  # F-n
    fr_ids: list[str] = Field(default_factory=list)  # FR-n
    acceptance_criteria_ids: list[str] = Field(default_factory=list)  # FR-n.ACm
    edge_case_ids: list[str] = Field(default_factory=list)  # EC-n
    nfr_ids: list[str] = Field(default_factory=list)  # NFR-n
    module_ids: list[str] = Field(default_factory=list)  # MOD-n (solution_architecture)
    api_contract_ids: list[str] = Field(default_factory=list)  # API-n
    data_model_ids: list[str] = Field(default_factory=list)  # DM-n
    constraint_ids: list[str] = Field(default_factory=list)  # CON-n
    target_paths: list[str] = Field(default_factory=list)
    verification_scope: str = ""
    integration_notes: str = ""
    open_assumptions: list[str] = Field(default_factory=list)


class AxisScore(Strict):
    score: int = Field(ge=1, le=5)
    factors: list[str] = Field(min_length=1)  # khóa trong AXIS_FACTORS của đúng trục này
    evidence: Text  # vì sao điểm này: chi tiết cụ thể của task / spec / kiến trúc
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class RequirementProfileOutput(Strict):
    # task_id không do model ghi: code gán khi lưu (model dễ chép nhầm task_id của harness).
    complexity: AxisScore
    testability: AxisScore
    security: AxisScore
    architecture_impact: AxisScore
    architecture_sections_read: list[str] = Field(default_factory=list)  # tiêu đề mục của solution_architecture.md đã đọc
    summary: str = ""


def register() -> None:
    register_output_model("RequirementAnalyzer", RequirementProfileOutput)
