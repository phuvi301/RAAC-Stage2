"""answers.md: người dùng đọc câu hỏi của flow clarify và sửa câu trả lời ngay trong file.

Định dạng (mỗi câu một khối, phần trả lời là mọi dòng sau "A:" tới câu kế tiếp):

    ### Q3: Tích hợp thanh toán qua cổng nào?
    _Vì sao hỏi: ..._
    A: VNPay
"""

import re

HEADER = """# Làm rõ yêu cầu

<!-- Sửa câu trả lời sau "A:". Giữ nguyên các dòng "### Qn: ...".
     Câu để nguyên đáp án gợi ý sẽ được ghi là giả định. Trả lời xong thì chạy lại clarify (hoặc dùng tui)
     để agent hỏi tiếp; khi agent thấy đủ, clarify tự chốt. -->
"""
QUESTION = re.compile(r"^### (Q\d+):[ \t]*(.*)$", re.MULTILINE)
CATEGORY_LABELS = {"functional_scope": "Phạm vi chức năng", "domain_data": "Miền & dữ liệu",
                   "ux_flow": "Luồng tương tác & UX", "quality_attributes": "Chất lượng (NFR)",
                   "integrations": "Tích hợp ngoài", "edge_cases": "Trường hợp biên & lỗi",
                   "constraints": "Ràng buộc & đánh đổi", "terminology": "Thuật ngữ",
                   "completion_signals": "Tín hiệu hoàn thành", "open_items": "Còn bỏ ngỏ"}
STATUS_LABELS = {"clear": "rõ", "partial": "mơ hồ", "missing": "thiếu", "not_applicable": "không áp dụng",
                 "deferred": "để bước sau", "resolved": "đã rõ sau khi hỏi", "outstanding": "còn thiếu"}


def coverage_lines(coverage: list[dict]) -> list[str]:
    if not coverage:
        return []
    lines = ["| Nhóm | Trạng thái | Ghi chú |", "|---|---|---|"]
    for item in coverage:
        note = str(item.get("note", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {CATEGORY_LABELS.get(item['category'], item['category'])} | "
                     f"{STATUS_LABELS.get(item['status'], item['status'])} | {note} |")
    return lines


def render_answers(rounds: list[dict], answers: dict[str, str]) -> str:
    """Toàn bộ câu hỏi của các vòng; giữ câu trả lời người dùng đã sửa (`answers`), câu mới điền đáp án khuyến nghị."""
    lines = [HEADER]
    for number, round_ in enumerate(rounds, start=1):
        lines.append(f"## Vòng {number}")
        if round_.get("summary"):
            lines.append(f"> {round_['summary']}")
        if round_.get("coverage"):
            lines += ["", *coverage_lines(round_["coverage"]), ""]
        if not round_["questions"]:
            lines.append("_Không cần hỏi thêm: thông tin đã đủ._")
        for question in round_["questions"]:
            lines.append(f"### {question['id']}: {question['text']}")
            meta = [f"Nhóm: {CATEGORY_LABELS.get(question.get('category'), question.get('category'))}"
                    ] if question.get("category") else []
            if question.get("why"):
                meta.append(f"Vì sao hỏi: {question['why']}")
            if meta:
                lines.append(f"_{' · '.join(meta)}_")
            if question.get("options"):
                lines.append("Lựa chọn: " + " · ".join(
                    f"**{option}** (khuyến nghị)" if option == question.get("default_answer") else option
                    for option in question["options"]))
            lines.append(f"A: {answers.get(question['id'], question.get('default_answer', ''))}".rstrip())
            lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_answers(text: str) -> dict[str, str]:
    """{Qn: câu trả lời} đọc từ answers.md; câu trả lời nhiều dòng được giữ nguyên."""
    result = {}
    matches = list(QUESTION.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        answer = re.search(r"^A:[ \t]?(.*)", block, re.MULTILINE | re.DOTALL)
        if answer:
            body = re.split(r"^## ", answer.group(1), maxsplit=1, flags=re.MULTILINE)[0]
            result[match.group(1)] = body.strip()
    return result


PRIORITY = {"high": "cao", "medium": "trung bình", "low": "thấp"}


def _cell(text: str) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def render_requirements(data: dict) -> str:
    """requirements.json -> requirements.md để người dùng đọc, góp ý, duyệt (nguồn sự thật vẫn là json)."""
    overview = data["overview"]
    lines = ["# Yêu cầu sản phẩm", "", "## Tổng quan", "", overview["summary"], "",
             f"- **Mục tiêu:** {overview['product_goal']}",
             f"- **Người dùng:** {', '.join(overview['target_users'])}"]
    if overview.get("platforms"):
        lines.append(f"- **Nền tảng:** {', '.join(overview['platforms'])}")
    if overview.get("scope"):
        lines.append(f"- **Phạm vi bản đầu:** {overview['scope']}")
    lines += ["", "## Chức năng cốt lõi"]
    for feature in data["features"]:
        lines += ["", f"### {feature['id']} · {feature['name']} — ưu tiên "
                      f"{PRIORITY.get(feature['priority'], feature['priority'])}", "", feature["description"]]
        if feature.get("why_priority"):
            lines.append(f"- **Vì sao ưu tiên này:** {feature['why_priority']}")
        if feature.get("independent_test"):
            lines.append(f"- **Kiểm độc lập:** {feature['independent_test']}")
        if feature.get("sources"):
            lines.append(f"- **Căn cứ:** {', '.join(feature['sources'])}")
    if data.get("success_criteria"):
        lines += ["", "## Tiêu chí thành công", "", "| ID | Kết quả | Đo bằng |", "|---|---|---|"]
        lines += [f"| {sc['id']} | {_cell(sc['outcome'])} | {_cell(sc['metric'])} |" for sc in data["success_criteria"]]
    if data.get("key_entities"):
        lines += ["", "## Thực thể chính", ""]
        for entity in data["key_entities"]:
            detail = "; ".join(part for part in (", ".join(entity.get("attributes", [])),
                                                 ", ".join(entity.get("relations", []))) if part)
            lines.append(f"- **{entity['name']}**: {entity['description']}" + (f" ({detail})" if detail else ""))
    nfrs = data.get("non_functional_requirements", [])
    if nfrs:
        lines += ["", "## Yêu cầu phi chức năng", "", "| ID | Loại | Yêu cầu | Đo bằng |", "|---|---|---|---|"]
        for nfr in nfrs:
            lines.append(f"| {nfr['id']} | {nfr['category']} | {_cell(nfr['requirement'])} | {_cell(nfr['metric'])} |")
    for title, key in (("Ràng buộc", "constraints"), ("Ngoài phạm vi", "out_of_scope"), ("Câu hỏi mở", "open_questions")):
        if data.get(key):
            lines += ["", f"## {title}", "", *[f"- {item}" for item in data[key]]]
    if data.get("assumptions"):
        lines += ["", "## Giả định", ""]
        lines += [f"- {item['text']}" + (f" _(từ {item['clarification_id']})_" if item.get("clarification_id") else "")
                  for item in data["assumptions"]]
    return "\n".join(lines) + "\n"


def render_profile(data: dict) -> str:
    """requirement_profile.json -> .md: từng chức năng với yêu cầu chi tiết + AC, rồi user flow, quy tắc, NFR."""
    overview = data["overview"]
    lines = ["# Hồ sơ yêu cầu (Requirement Profile)", "",
             "> Tổng quan, tên/mô tả chức năng (F-n), tiêu chí thành công, NFR, ràng buộc và giả định được chép nguyên "
             "từ requirements đã duyệt: muốn đổi những phần đó, góp ý ở bước requirements (dòng lệnh: `revise requirements`, rồi "
             "duyệt lại và chạy lại profile). Góp ý ở bước này chỉ đổi user flow, quy tắc nghiệp vụ, "
             "yêu cầu chi tiết, AC và trường hợp biên.", "", overview["summary"], "",
             f"- **Mục tiêu:** {overview['product_goal']}", f"- **Người dùng:** {', '.join(overview['target_users'])}"]
    rules = data.get("business_rules", [])
    for feature in data["features"]:
        lines += ["", f"## {feature['id']} · {feature['name']} ({PRIORITY.get(feature['priority'], feature['priority'])})",
                  "", feature["description"]]
        for fr in [fr for fr in data["functional_requirements"] if fr["feature_id"] == feature["id"]]:
            lines += ["", f"**{fr['id']}** ({PRIORITY.get(fr['priority'], fr['priority'])}): {fr['description']}"]
            lines += [f"- {ac['id']}: **Cho** {ac['given']}, **khi** {ac['when']}, **thì** {ac['then']}"
                      for ac in fr["acceptance_criteria"]]
        feature_rules = [rule for rule in rules if rule["feature_id"] == feature["id"]]
        if feature_rules:
            lines += ["", "_Quy tắc nghiệp vụ:_", *[f"- {rule['id']}: {rule['rule']}" for rule in feature_rules]]
        cases = [case for case in data.get("edge_cases", []) if case["feature_id"] == feature["id"]]
        if cases:
            lines += ["", "_Trường hợp biên:_", *[f"- {case['id']}: {case['situation']} → {case['expected']}"
                                                  for case in cases]]
    lines += ["", "## User flow"]
    for flow in data["user_flows"]:
        lines += ["", f"**{flow['id']} · {flow['name']}** — {flow['actor']} ({', '.join(flow['feature_ids'])})",
                  *[f"{number}. {step}" for number, step in enumerate(flow["steps"], start=1)]]
    nfrs = data.get("non_functional_requirements", [])
    if nfrs:
        lines += ["", "## Yêu cầu phi chức năng", "", "| ID | Loại | Yêu cầu | Đo bằng |", "|---|---|---|---|"]
        lines += [f"| {n['id']} | {n['category']} | {_cell(n['requirement'])} | {_cell(n['metric'])} |" for n in nfrs]
    for title, key in (("Ràng buộc", "constraints"), ("Ngoài phạm vi", "out_of_scope"), ("Câu hỏi mở", "open_questions")):
        if data.get(key):
            lines += ["", f"## {title}", "", *[f"- {item}" for item in data[key]]]
    if data.get("assumptions"):
        lines += ["", "## Giả định", "", *[f"- {item['text']}" for item in data["assumptions"]]]
    return "\n".join(lines) + "\n"


def _choice(choice: dict | None) -> tuple[str, str, str]:
    if not choice:
        return "—", "", ""
    return choice["name"], choice.get("version_constraint", ""), choice.get("reason", "")


def _ids_line(label: str, values: list[str]) -> list[str]:
    return [f"- **{label}:** {', '.join(values)}"] if values else []


def render_solution_architecture(data: dict) -> str:
    """solution_architecture.json -> .md để người dùng đọc, góp ý, duyệt (nguồn sự thật vẫn là json)."""
    stack = data["tech_stack"]
    lines = ["# Kiến trúc giải pháp (Solution Architecture)", "", "## 1. Tổng quan", "", data["summary"], "",
             "## 2. Tech stack", "", "| Thành phần | Công nghệ | Phiên bản | Lý do |", "|---|---|---|---|"]
    rows = [("Ngôn ngữ", stack["language"]), ("Backend", stack.get("backend_framework")),
            ("Frontend", stack.get("frontend_framework")), ("Cơ sở dữ liệu", stack.get("database"))]
    rows += [("Thư viện", library) for library in stack.get("libraries", [])]
    for label, choice in rows:
        if choice or label == "Ngôn ngữ":
            name, version, reason = _choice(choice)
            lines.append(f"| {label} | {_cell(name)} | {_cell(version or '—')} | {_cell(reason)} |")
    lines += ["", "## 3. Cấu trúc thư mục", "", *[f"- `{folder['path']}`: {folder['purpose']}"
                                                  for folder in data["folders"]]]
    lines += ["", "## 4. Module"]
    for module in data["modules"]:
        lines += ["", f"### {module['id']} · {module['name']} (`{module['path']}`)", "", module["responsibility"]]
        lines += _ids_line("Chức năng", module.get("feature_ids", []))
        lines += _ids_line("Phụ thuộc", module.get("depends_on", []))
        lines += _ids_line("Thực thể", module.get("owns_entities", []))
    if data.get("data_models"):
        lines += ["", "## 5. Mô hình dữ liệu"]
        for model in data["data_models"]:
            lines += ["", f"### {model['id']} · {model['name']} ({model['module_id']})", "", model["description"], "",
                      "| Trường | Kiểu | Ràng buộc |", "|---|---|---|"]
            lines += [f"| {_cell(field['name'])} | {_cell(field['field_type'])} | {_cell(field.get('constraints', ''))} |"
                      for field in model["fields"]]
            lines += _ids_line("Quan hệ", model.get("relations", []))
    if data.get("api_contracts"):
        lines += ["", "## 6. Hợp đồng giao tiếp"]
        for contract in data["api_contracts"]:
            lines += ["", f"### {contract['id']} · `{contract['signature']}` ({contract['kind']}, {contract['module_id']})",
                      "", contract["purpose"]]
            if contract.get("request_or_input"):
                lines.append(f"- **Vào:** {contract['request_or_input']}")
            if contract.get("response_or_output"):
                lines.append(f"- **Ra:** {contract['response_or_output']}")
            lines += _ids_line("Lỗi", contract.get("error_cases", []))
            lines += _ids_line("Yêu cầu", contract.get("fr_ids", []))
    security = data.get("security_architecture") or {}
    lines += ["", "## 7. Kiến trúc bảo mật", ""]
    shown = False
    for label, key in (("Xác thực", "auth_strategy"), ("Phân quyền", "authorization_model")):
        if security.get(key):
            lines.append(f"- **{label}:** {security[key]}")
            shown = True
    for label, key in (("Dữ liệu nhạy cảm", "sensitive_data_rules"), ("Ranh giới tin cậy", "trust_boundaries")):
        if security.get(key):
            lines += [f"- **{label}:**", *[f"  - {item}" for item in security[key]]]
            shown = True
    lines += _ids_line("NFR", security.get("nfr_ids", []))
    if not shown and not security.get("nfr_ids"):
        lines.append("_Không có yêu cầu bảo mật riêng._")
    runtime = data["runtime_contract"]
    lines += ["", "## 8. Runtime và kiểm thử", "",
              f"- **Runtime:** {', '.join(f'{name} {version}' for name, version in runtime['runtime_versions'].items())}",
              f"- **Trình quản lý gói:** {runtime['package_manager']}",
              f"- **Cài đặt:** `{runtime['install_command']}`"]
    if runtime.get("build_command"):
        lines.append(f"- **Build:** `{runtime['build_command']}`")
    if runtime.get("run_command"):
        lines.append(f"- **Chạy:** `{runtime['run_command']}`")
    lines += [f"- **Test:** `{runtime['test_command']}` ({runtime['test_framework']})"]
    if runtime.get("env_vars"):
        lines += ["- **Biến môi trường:**", *[f"  - `{name}`: {text}" for name, text in runtime["env_vars"].items()]]
    lines += ["", "## 9. Ràng buộc toàn cục (tầng thực thi không được phá vỡ)", "",
              "| ID | Loại | Ràng buộc |", "|---|---|---|"]
    lines += [f"| {item['id']} | {item['category']} | {_cell(item['rule'])} |" for item in data["global_constraints"]]
    lines += ["", "## 10. Quyết định kiến trúc"]
    for decision in data["decisions"]:
        lines += ["", f"**{decision['id']} · {decision['topic']}**: {decision['decision']}",
                  f"- Lý do: {decision['rationale']}"]
        lines += _ids_line("Phương án loại", decision.get("alternatives_rejected", []))
    if data.get("open_assumptions"):
        lines += ["", "### Giả định kỹ thuật còn mở", "", *[f"- {item}" for item in data["open_assumptions"]]]
    return "\n".join(lines) + "\n"


TASK_KIND_LABELS = {"bootstrap": "dựng khung", "feature": "chức năng", "integration": "tích hợp",
                    "hardening": "gia cố"}


def render_tasks(data: dict) -> str:
    """tasks.json -> tasks.md: thứ tự thực thi, bảng task, chi tiết, ma trận phủ AC, câu hỏi mở."""
    runtime = data.get("global_runtime_contract") or {}
    lines = ["# Kế hoạch công việc (Project Manager)", "", data["summary"], "",
             f"- **Workspace:** {data.get('workspace_mode', '')}",
             f"- **Lệnh test chuẩn:** `{runtime.get('test_command', '')}`", "",
             "## Thứ tự thực thi", "", " → ".join(data["execution_order"]), "",
             "## Tổng quan task", "", "| ID | Loại | Tiêu đề | Phụ thuộc | Module | Số AC |", "|---|---|---|---|---|---|"]
    for task in data["tasks"]:
        lines.append(f"| {task['id']} | {TASK_KIND_LABELS.get(task['kind'], task['kind'])} | {_cell(task['title'])} | "
                     f"{', '.join(task['depends_on']) or '—'} | {', '.join(task['module_ids'])} | "
                     f"{len(task['acceptance_criteria_ids'])} |")
    if data.get("milestones"):
        lines += ["", "## Milestone", ""]
        lines += [f"- **{m['id']} · {m['name']}**: {m['goal']} ({', '.join(m['task_ids'])})" for m in data["milestones"]]
    lines += ["", "## Chi tiết task"]
    for task in data["tasks"]:
        lines += ["", f"### {task['id']} · {task['title']} ({TASK_KIND_LABELS.get(task['kind'], task['kind'])})", "",
                  task["goal"]]
        for label, key in (("Phụ thuộc", "depends_on"), ("Chức năng", "feature_ids"), ("Yêu cầu", "fr_ids"),
                           ("AC", "acceptance_criteria_ids"), ("Trường hợp biên", "edge_case_ids"), ("NFR", "nfr_ids"),
                           ("Module", "module_ids"), ("API", "api_contract_ids"), ("Data model", "data_model_ids"),
                           ("Ràng buộc", "constraint_ids")):
            lines += _ids_line(label, task.get(key, []))
        lines.append(f"- **Vùng đường dẫn:** {', '.join(f'`{path}`' for path in task['target_paths'])}")
        lines.append(f"- **Phạm vi kiểm thử:** {task['verification_scope']}")
        if task.get("integration_notes"):
            lines.append(f"- **Tích hợp:** {task['integration_notes']}")
        if task.get("open_assumptions"):
            lines += ["- **Còn mở:**", *[f"  - {item}" for item in task["open_assumptions"]]]
    coverage: dict[str, list[str]] = {}
    for task in data["tasks"]:
        for ac in task["acceptance_criteria_ids"]:
            coverage.setdefault(ac, []).append(task["id"])
    if coverage:
        def ac_key(value: str):
            fr, _, ac = value.partition(".AC")
            return (int(fr.split("-")[-1]) if fr.split("-")[-1].isdigit() else 0, int(ac) if ac.isdigit() else 0, value)
        lines += ["", "## Ma trận phủ AC", "", "| AC | Task |", "|---|---|"]
        lines += [f"| {ac} | {', '.join(coverage[ac])} |" for ac in sorted(coverage, key=ac_key)]
    if data.get("open_questions"):
        lines += ["", "## Câu hỏi mở", "", *[f"- {item}" for item in data["open_questions"]]]
    return "\n".join(lines) + "\n"
