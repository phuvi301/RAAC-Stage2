"""Hàm dùng chung cho test pre_executor."""

from copy import deepcopy

from raac.pre_executor.contracts import COVERAGE_CATEGORIES


def with_coverage(output):
    """Câu trả lời giả của Clarifier chưa có độ phủ thì điền độ phủ hợp lệ: nhóm được hỏi là partial, còn lại clear.
    Test về luật độ phủ tự ghi `coverage` nên không bị đụng tới."""
    if not isinstance(output, dict) or "complete" not in output or "coverage" in output:
        return output
    output = deepcopy(output)
    for question in output["questions"]:
        question.setdefault("category", "functional_scope")
    asked = {question["category"] for question in output["questions"]}
    output["coverage"] = [{"category": name, "status": "partial" if name in asked else "clear", "note": ""}
                          for name in COVERAGE_CATEGORIES]
    return output


# ---------------------------------------------------------------- SA / PM: đặc tả BA + kiến trúc mẫu đã duyệt

def _ac(frid, number):
    return {"id": f"{frid}.AC{number}", "given": f"bối cảnh {frid}.AC{number}", "when": "thao tác",
            "then": f"kết quả {frid}.AC{number}"}


BA_SPEC = {
    "overview": {"summary": "Website bán quần áo", "product_goal": "Bán online", "target_users": ["khách lẻ"]},
    "features": [
        {"id": "F-1", "name": "Giỏ hàng", "description": "thêm, sửa, xoá", "priority": "high", "why_priority": "lõi",
         "independent_test": "thêm 1 món", "sources": ["request"]},
        {"id": "F-2", "name": "Đặt hàng", "description": "đặt và thanh toán COD", "priority": "medium",
         "why_priority": "cần", "independent_test": "đặt 1 đơn", "sources": ["request"]},
        {"id": "F-3", "name": "Quản lý đơn", "description": "admin xử lý đơn", "priority": "low",
         "why_priority": "vận hành", "independent_test": "đổi trạng thái", "sources": ["request"]},
    ],
    "success_criteria": [{"id": "SC-1", "outcome": "Khách đặt hàng nhanh", "metric": "dưới 3 phút"}],
    "key_entities": [{"id": "ENT-1", "name": "Giỏ", "description": "giỏ của khách", "attributes": [], "relations": []},
                     {"id": "ENT-2", "name": "Đơn hàng", "description": "đơn của khách", "attributes": [],
                      "relations": []}],
    "non_functional_requirements": [
        {"id": "NFR-1", "category": "performance_efficiency", "requirement": "nhanh", "metric": "p95 < 2 s",
         "feature_ids": []},
        {"id": "NFR-2", "category": "security", "requirement": "chỉ admin xử lý đơn", "metric": "100% route admin",
         "feature_ids": ["F-3"]},
    ],
    "external_integrations": [{"id": "INT-1", "name": "Dịch vụ giao hàng", "purpose": "tính phí ship",
                               "feature_ids": ["F-2"], "failure_behavior": "dùng phí cố định"}],
    "constraints": ["chỉ COD"],
    "assumptions": [{"text": "giao hàng toàn quốc", "clarification_id": None, "feature_ids": []},
                    {"text": "admin là nhân viên nội bộ", "clarification_id": None, "feature_ids": ["F-3"]}],
    "out_of_scope": ["app mobile"],
    "user_flows": [{"id": "UF-1", "name": "Mua hàng", "actor": "khách", "feature_ids": ["F-1", "F-2"],
                    "steps": ["chọn", "đặt"]}],
    "business_rules": [{"id": "BR-1", "feature_id": "F-1", "rule": "tối đa 10 món"},
                       {"id": "BR-2", "feature_id": "F-3", "rule": "chỉ admin đổi trạng thái"}],
    "functional_requirements": [
        {"id": "FR-1", "feature_id": "F-1", "description": "thêm món", "priority": "high",
         "acceptance_criteria": [_ac("FR-1", 1), _ac("FR-1", 2)]},
        {"id": "FR-2", "feature_id": "F-1", "description": "xoá món", "priority": "high",
         "acceptance_criteria": [_ac("FR-2", 1)]},
        {"id": "FR-3", "feature_id": "F-2", "description": "đặt hàng", "priority": "medium",
         "acceptance_criteria": [_ac("FR-3", 1)]},
        {"id": "FR-4", "feature_id": "F-3", "description": "đổi trạng thái đơn", "priority": "low",
         "acceptance_criteria": [_ac("FR-4", 1)]},
    ],
    "edge_cases": [{"id": "EC-1", "feature_id": "F-1", "situation": "hết hàng", "expected": "báo lỗi"},
                   {"id": "EC-2", "feature_id": "F-3", "situation": "đơn đã huỷ", "expected": "không đổi"}],
    "open_questions": [{"id": "OQ-1", "text": "admin có cần xuất báo cáo?", "feature_ids": ["F-3"]},
                       {"id": "OQ-2", "text": "có voucher?", "feature_ids": []}],
}


def sa_output(**changes):
    data = {
        "summary": "Web app Python một khối",
        "tech_stack": {"language": {"name": "Python", "version_constraint": ">=3.11", "reason": "đội quen"},
                       "backend_framework": {"name": "FastAPI", "version_constraint": "", "reason": "nhẹ"},
                       "frontend_framework": None, "database": {"name": "SQLite", "reason": "đủ cho bản đầu"},
                       "libraries": [{"name": "pydantic", "reason": "kiểm dữ liệu"}]},
        "folders": [{"path": "src/shop", "purpose": "mã nguồn"}, {"path": "tests", "purpose": "kiểm thử"}],
        "modules": [
            {"id": "MOD-1", "name": "core", "path": "src/shop/core", "responsibility": "cấu hình, db"},
            {"id": "MOD-2", "name": "cart", "path": "src/shop/cart", "responsibility": "giỏ hàng",
             "feature_ids": ["F-1"], "depends_on": ["MOD-1"], "owns_entities": ["ENT-1"]},
            {"id": "MOD-3", "name": "orders", "path": "src/shop/orders", "responsibility": "đơn hàng",
             "feature_ids": ["F-2", "F-3"], "depends_on": ["MOD-1", "MOD-2"], "owns_entities": ["Đơn hàng"]},
        ],
        "data_models": [{"id": "DM-1", "name": "Order", "module_id": "MOD-3", "description": "đơn",
                         "fields": [{"name": "id", "field_type": "int", "constraints": "primary key"}],
                         "entity_ids": ["ENT-2"]}],
        "api_contracts": [{"id": "API-1", "module_id": "MOD-2", "kind": "http", "signature": "POST /cart/items",
                           "purpose": "thêm món", "fr_ids": ["FR-1"]},
                          {"id": "API-2", "module_id": "MOD-3", "kind": "http", "signature": "POST /orders",
                           "purpose": "đặt hàng", "fr_ids": ["FR-3"], "integration_ids": ["INT-1"]}],
        "security_architecture": {"auth_strategy": "session cookie cho admin", "authorization_model": "role admin",
                                  "nfr_ids": ["NFR-2"]},
        "runtime_contract": {"runtime_versions": {"python": ">=3.11"}, "package_manager": "pip",
                             "install_command": "pip install -e .[dev]", "test_framework": "pytest",
                             "test_command": "python -m pytest -q", "env_vars": {"SHOP_DB": "đường dẫn file SQLite"}},
        "global_constraints": [{"id": "CON-1", "category": "architecture", "rule": "module chỉ gọi nhau qua API công khai"},
                               {"id": "CON-2", "category": "security", "rule": "route admin phải kiểm quyền"},
                               {"id": "CON-3", "category": "testing", "rule": "mỗi FR có test"}],
        "decisions": [{"id": "ADR-1", "topic": "Kiểu kiến trúc", "decision": "modular monolith",
                       "rationale": "đơn giản"}],
        "open_assumptions": ["chưa chốt cổng thanh toán online"],
    }
    data.update(changes)
    return data


def task(tid, kind="feature", deps=(), **fields):
    data = {"id": tid, "title": f"Task {tid}", "kind": kind, "goal": f"mục tiêu {tid}", "depends_on": list(deps),
            "module_ids": ["MOD-1"], "target_paths": ["src/shop"], "verification_scope": "pytest cho task"}
    data.update(fields)
    return data


def pm_tasks():
    return [
        task("T1", "bootstrap", module_ids=["MOD-1"], target_paths=["src/shop/core", "tests"], constraint_ids=["CON-3"]),
        task("T2", deps=["T1"], feature_ids=["F-1"], fr_ids=["FR-1", "FR-2"],
             acceptance_criteria_ids=["FR-1.AC1", "FR-1.AC2", "FR-2.AC1"], edge_case_ids=["EC-1"],
             module_ids=["MOD-2"], api_contract_ids=["API-1"], target_paths=["src/shop/cart"],
             open_assumptions=["giới hạn 10 món áp dụng mỗi giỏ"]),
        task("T3", deps=["T2"], feature_ids=["F-2"], fr_ids=["FR-3"], acceptance_criteria_ids=["FR-3.AC1"],
             nfr_ids=["NFR-1"], module_ids=["MOD-3"], api_contract_ids=["API-2"], data_model_ids=["DM-1"],
             target_paths=["src/shop/orders"]),
        task("T4", deps=["T3"], feature_ids=["F-3"], fr_ids=["FR-4"], acceptance_criteria_ids=["FR-4.AC1"],
             nfr_ids=["NFR-2"], module_ids=["MOD-3"], constraint_ids=["CON-2"], target_paths=["src/shop/orders"]),
    ]


def pm_output(tasks=None, **changes):
    data = {"summary": "Bốn task theo lát cắt dọc", "milestones": [
        {"id": "M1", "name": "MVP", "goal": "mua được hàng", "task_ids": ["T1", "T2", "T3"]}],
        "tasks": tasks if tasks is not None else pm_tasks(), "open_questions": []}
    data.update(changes)
    return data


def approve_ba_spec(artifacts, data=None):
    """Ghi thẳng các file đã duyệt của clarify + business_analyst rồi approve gate (không chạy agent)."""
    import hashlib
    from datetime import UTC, datetime

    artifacts.save_request("Làm website bán quần áo")
    artifacts.write_text("answers.md", "# Làm rõ yêu cầu\n")
    artifacts.write_json("clarification.json", [])
    artifacts.write_json("ba_spec.json", data or BA_SPEC)
    approvals = {}
    for flow, filename in (("clarify", "answers.md"), ("business_analyst", "ba_spec.json")):
        approvals[flow] = {"sha256": hashlib.sha256(artifacts.path(filename).read_bytes()).hexdigest(),
                           "approved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    artifacts.write_json("approvals.json", approvals)
    return artifacts
