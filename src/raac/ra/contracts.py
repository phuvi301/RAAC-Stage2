"""Đầu vào và đầu ra của Requirement Analyzer (rubric 4 trục C/T/S/A, thang 1-5).

Đầu vào (đọc từ workspace, không qua model):
- TaskInput: một task trong tasks.json (do task_decomposer tạo): id, title, feature_id, description,
  acceptance_criteria (id FR-n.ACm trong requirement_spec.json), depends_on, files, risk.

Đầu ra (role RequirementAnalyzer, mỗi task một mục trong requirement_profile.json):
- RequirementProfileOutput: task_id; complexity, testability, security, architecture_impact (mỗi trục AxisScore:
  score 1-5, factors lấy từ bảng "Các yếu tố đánh giá" của trục đó trong rubric, evidence, confidence);
  architecture_sections_read (các mục của architecture.md agent đã đọc để lấy bằng chứng); summary.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from raac.common.agent_contracts import register_output_model

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RUBRIC_FILE = "registry/prompts/requirement_analyzer/RAAC_Official_4Axis_Rubric_v1.0.md"
RUBRIC_VERSION = "RAAC-4Axis-v1.0"

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
    """Một task trong tasks.json. Cho phép trường thêm (task_decomposer có thể ghi nhiều hơn)."""
    model_config = ConfigDict(extra="allow")

    id: Text
    title: Text
    feature_id: str = ""
    description: Text
    acceptance_criteria: list[str] = Field(default_factory=list)  # id FR-n.ACm
    depends_on: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    risk: str = ""


class AxisScore(Strict):
    score: int = Field(ge=1, le=5)
    factors: list[str] = Field(min_length=1)  # khóa trong AXIS_FACTORS của đúng trục này
    evidence: Text  # vì sao điểm này: chi tiết cụ thể của task / spec / kiến trúc
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class RequirementProfileOutput(Strict):
    task_id: Text
    complexity: AxisScore
    testability: AxisScore
    security: AxisScore
    architecture_impact: AxisScore
    architecture_sections_read: list[str] = Field(default_factory=list)  # tiêu đề mục của architecture.md đã đọc
    summary: str = ""


def register() -> None:
    register_output_model("RequirementAnalyzer", RequirementProfileOutput)
